#!/usr/bin/env python3
"""Проверка делом: вариант лечения пробуется в `git worktree`, приёмка — кодом.

    trial.py discover --repo .
    trial.py prepare  --repo . --id F1 --slug split-handlers
    trial.py judge    --repo . --worktree W --findings findings.csv --id F1 --variant v1
                      [--checks-json '[...]'] [--measure-before measure.json] [--timeout 900]
    trial.py cleanup  --repo . --worktree W [--keep-branch]

`trial` никогда не мержит и не трогает рабочее дерево и текущую ветку
пользователя: копия живёт в `<tmp>/zodchiy-trials/`, ветка — `zodchiy/F<n>-<slug>`.
Решение (`accepted` / `rejected` / `unverified`) принимает `judge` по кодам
выхода и числам замера, а не модель.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile

from common import worktree_dirty
from ledger import _reached, read_findings, resolve_path, snapshot
from payback import pain_per_quarter, payback_quarters, payback_verdict
from slice import finding_files

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.join(HERE, "measure.py")

TAIL_LINES = 20
DOCKER_INFO_TIMEOUT = 15
MEASURE_TIMEOUT = 900
KILL_GRACE = 5
F821 = ["ruff", "check", "--select", "F821", "."]

FENCE_RE = re.compile(r"^```(?:bash|sh|shell)[^\n]*\n(.*?)^```", re.M | re.S)
CHECK_RE = re.compile(
    r"^(?:(?:uv|poetry|pipenv) run |python3? -m |npx )?(?:ruff|pytest|lint-imports)\b"
    r"|^(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:lint|test|build)\b"
)
# прогон тестов; ruff, lint-imports и сборка поведения не проверяют
TEST_CMD_RE = re.compile(
    r"\b(?:pytest|unittest|vitest|jest)\b|^(?:npm|pnpm|yarn)\s+(?:run\s+)?test\b|^(?:go|cargo)\s+test\b"
)
SEPARATORS = ("&&", "||", ";", "|")
ENV_ASSIGN_RE = re.compile(r"[A-Za-z_]\w*=.*")
# pytest -m live|golden|e2e — платные или внешние прогоны, не проверка варианта
OPT_IN_MARKERS = re.compile(r"\b(?:live|golden|e2e)\b")


# ── git ─────────────────────────────────────────────────────────────────────


def _git(repo: str, *args: str, check: bool = True) -> str:
    r = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True, check=check
    )
    return r.stdout


def _trials_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "zodchiy-trials")


def _safe(text: str) -> str:
    return re.sub(r"[^\w-]+", "-", text, flags=re.U).strip("-") or "trial"


def _branch_exists(repo: str, branch: str) -> bool:
    r = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
    )
    return r.returncode == 0


# ── discover_checks ─────────────────────────────────────────────────────────


def _check(cmd: list[str], needs_docker: bool = False) -> dict:
    return {"name": " ".join(cmd), "cmd": cmd, "needs_docker": needs_docker}


def _claude_md_checks(repo: str) -> list[dict]:
    path = os.path.join(repo, "CLAUDE.md")
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    out: list[dict] = []
    for block in FENCE_RE.findall(text):
        for line in block.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            docker = bool(re.search(r"docker|testcontainers", line, re.I))
            try:
                tokens = shlex.split(line, comments=True)
            except ValueError:
                continue
            group: list[str] = []
            for tok in [*tokens, ";"]:
                if tok not in SEPARATORS:
                    group.append(tok)
                    continue
                cmd, group = group, []
                # `VAR=1 pytest …` по соглашению — opt-in (платные живые вызовы,
                # золотые наборы): проверкой варианта не является.
                if not cmd or ENV_ASSIGN_RE.fullmatch(cmd[0]):
                    continue
                if CHECK_RE.match(" ".join(cmd)) and not _opt_in_marker(cmd):
                    out.append(_check(cmd, docker))
    return out


def _opt_in_marker(cmd: list[str]) -> bool:
    """`pytest -m live` выбирает платные/внешние тесты; `-m "not live"` — исключает."""
    for i, tok in enumerate(cmd[:-1]):
        if tok == "-m":
            expr = cmd[i + 1].strip()
            if not expr.startswith("not ") and OPT_IN_MARKERS.search(expr):
                return True
    return False


def is_test_check(check: dict) -> bool:
    return bool(TEST_CMD_RE.search(" ".join(check.get("cmd") or [])))


def _is_uv(repo: str) -> bool:
    if os.path.isfile(os.path.join(repo, "uv.lock")):
        return True
    pyproject = os.path.join(repo, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return False
    with open(pyproject, encoding="utf-8") as fh:
        return "[tool.uv" in fh.read()


def _config_checks(repo: str) -> list[dict]:
    out: list[dict] = []
    uv = ["uv", "run"] if _is_uv(repo) else []
    pyproject = os.path.join(repo, "pyproject.toml")
    if os.path.isfile(pyproject):
        with open(pyproject, encoding="utf-8") as fh:
            text = fh.read()
        if "[tool.ruff" in text:
            out.append(_check([*uv, "ruff", "check", "."]))
        if "pytest" in text or os.path.isdir(os.path.join(repo, "tests")):
            out.append(_check([*uv, "pytest", "-q"] if uv else ["python3", "-m", "pytest", "-q"]))
    package = os.path.join(repo, "package.json")
    if os.path.isfile(package):
        with open(package, encoding="utf-8") as fh:
            scripts = json.load(fh).get("scripts") or {}
        for name in ("lint", "test", "build"):
            body = scripts.get(name)
            if body and "no test specified" not in body:
                out.append(_check(["npm", "run", name]))
    return out


def _is_python(repo: str) -> bool:
    if os.path.isfile(os.path.join(repo, "pyproject.toml")):
        return True
    r = subprocess.run(
        ["git", "-C", repo, "ls-files", "--", "*.py"], capture_output=True, text=True
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def discover_checks(repo: str) -> list[dict]:
    """Проверки проекта списком: `CLAUDE.md` → иначе `pyproject.toml`/`package.json`.

    Цепочка `a && b && c` разбирается на отдельные команды: вердикт по коду
    выхода всей цепочки ничего не говорит о звеньях. Для Python всегда добавляется
    `ruff check --select F821 .` — перенос кода ловит `NameError` только так."""
    checks = _claude_md_checks(repo) or _config_checks(repo)
    f821 = [*(["uv", "run"] if _is_uv(repo) else []), *F821]
    if _is_python(repo) and not any(c["cmd"][-len(F821) :] == F821 for c in checks):
        checks.append(_check(f821))
    seen, out = set(), []
    for c in checks:
        key = tuple(c["cmd"])
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


# ── prepare / cleanup ───────────────────────────────────────────────────────


def prepare(repo: str, finding_id: str, slug: str) -> dict:
    """Worktree от `HEAD` в `<tmp>/zodchiy-trials/` и новая ветка. Существующую
    ветку не перезаписывает: берёт `-2`, `-3`… и сообщает об этом в результате —
    вызывающий пишет факт в `decisions.json`."""
    repo = os.path.abspath(repo)
    base_head = _git(repo, "rev-parse", "HEAD").strip()
    base_dirty = worktree_dirty(repo)

    requested = f"zodchiy/{_safe(finding_id)}-{_safe(slug)}"
    branch, n = requested, 1
    while _branch_exists(repo, branch):
        n += 1
        branch = f"{requested}-{n}"

    os.makedirs(_trials_dir(), exist_ok=True)
    worktree = tempfile.mkdtemp(
        prefix=f"{_safe(os.path.basename(repo))}-", dir=_trials_dir()
    )
    try:
        _git(repo, "worktree", "add", "-q", "-b", branch, worktree, base_head)
    except Exception:
        shutil.rmtree(worktree, ignore_errors=True)
        raise

    # node_modules не копируем: симлинк на основной checkout (спека §5.6)
    modules = os.path.join(repo, "node_modules")
    link = os.path.join(worktree, "node_modules")
    if os.path.isdir(modules) and not os.path.lexists(link):
        os.symlink(modules, link)

    # Замер «до» — на чистой копии от HEAD, до любых правок: замер грязного
    # дерева пользователя включил бы незакоммиченное. Файл лежит рядом с копией,
    # а не в ней — иначе попал бы в дифф варианта.
    before = _measure(worktree, MEASURE_TIMEOUT)
    measure_before = None
    if before is not None:
        measure_before = _before_path(worktree)
        with open(measure_before, "w", encoding="utf-8") as fh:
            json.dump(before, fh, ensure_ascii=False)

    return {
        "worktree": worktree,
        "branch": branch,
        "branch_requested": requested,
        "branch_collision": branch != requested,
        "base_head": base_head,
        "base_dirty": base_dirty,
        "measure_before": measure_before,
    }


def _before_path(worktree: str) -> str:
    return worktree.rstrip(os.sep) + ".measure-before.json"


CACHE_EXCLUDES = (
    ":(exclude,glob)**/__pycache__/**",
    ":(exclude,glob)**/.pytest_cache/**",
    ":(exclude,glob)**/.ruff_cache/**",
    ":(exclude,glob)**/.mypy_cache/**",
    ":(exclude,glob)**/*.pyc",
)


def _commit_variant(worktree: str, branch: str) -> None:
    """Незакоммиченный вариант — в коммит ветки, иначе `worktree remove --force`
    унесёт его вместе с копией. Не удалось закоммитить (хук, подпись) — копия
    остаётся на месте, наружу уходит ошибка: потерять принятое хуже, чем оставить мусор."""
    _git(worktree, "add", "-A", "--", ".", *CACHE_EXCLUDES)
    if subprocess.run(["git", "-C", worktree, "diff", "--cached", "--quiet"]).returncode == 0:
        return
    ident = []
    for key, fallback in (("user.name", "zodchiy"), ("user.email", "zodchiy@localhost")):
        if not _git(worktree, "config", "--get", key, check=False).strip():
            ident += ["-c", f"{key}={fallback}"]
    finding = branch.split("/", 1)[-1].split("-", 1)[0]
    r = subprocess.run(
        ["git", "-C", worktree, *ident, "-c", "commit.gpgsign=false",
         "commit", "-q", "-m", f"zodchiy: проба {finding}"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"не удалось закоммитить вариант в {branch}: {r.stderr.strip()}")


def cleanup(repo: str, worktree: str, keep_branch: bool) -> None:
    """Снять копию. Ветка принятой пробы остаётся (`keep_branch`) вместе с
    незакоммиченными правками варианта — они коммитятся перед снятием. Остальные
    ветки удаляются. Трогает только каталоги внутри `zodchiy-trials/`: чужой путь —
    ошибка, не `rm`."""
    repo = os.path.abspath(repo)
    root = os.path.realpath(_trials_dir()) + os.sep
    if not (os.path.realpath(worktree) + os.sep).startswith(root):
        raise ValueError(f"{worktree} вне {_trials_dir()} — не трогаю")

    branch = None
    if os.path.isdir(worktree):
        r = subprocess.run(
            ["git", "-C", worktree, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
        )
        branch = r.stdout.strip() if r.returncode == 0 else None
        link = os.path.join(worktree, "node_modules")
        if os.path.islink(link):
            os.unlink(link)
        if keep_branch and branch and branch.startswith("zodchiy/"):
            _commit_variant(worktree, branch)
        _git(repo, "worktree", "remove", "--force", worktree, check=False)
        shutil.rmtree(worktree, ignore_errors=True)
    if os.path.isfile(_before_path(worktree)):
        os.unlink(_before_path(worktree))
    _git(repo, "worktree", "prune", check=False)
    if branch and branch.startswith("zodchiy/") and not keep_branch:
        _git(repo, "branch", "-D", branch, check=False)


# ── judge ───────────────────────────────────────────────────────────────────


def _tail(*chunks) -> str:
    text = "".join(
        c.decode("utf-8", "replace") if isinstance(c, bytes) else (c or "")
        for c in chunks
    )
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def _docker_ok() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=DOCKER_INFO_TIMEOUT
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return r.returncode == 0


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


# Собственные ошибки uv: инструмент не запускался. Список закрытый и узкий —
# `error: Failed to parse …` печатает сам ruff на битом синтаксисе варианта, это
# честный `fail`.
UV_OWN_ERROR_RE = re.compile(
    r"^error: (?:Failed to (?:spawn|download|fetch|install|build|resolve|sync|lock|inspect"
    r"|create|initialize|retrieve|prepare)\b|No solution found|No interpreter found"
    r"|Request failed|Distribution .* not found)"
)


def _uv_own_error(cmd: list[str], stderr: bytes) -> str | None:
    """Первая строка ошибки самого uv (`uv run` не смог запустить инструмент), иначе `None`."""
    if cmd[:2] != ["uv", "run"]:
        return None
    for line in stderr.decode("utf-8", "replace").splitlines():
        if UV_OWN_ERROR_RE.match(line.strip()):
            return line.strip()
    return None


def _run_check(check: dict, worktree: str, timeout: int, docker: list) -> dict:
    """Одна команда — один процесс, вердикт по её коду выхода. Процесс запускается
    в своей группе: по таймауту убивается вся группа, а не только прямой потомок
    (`sh -c`, `uv run pytest` порождают внуков, которые иначе живут дальше)."""
    name = check.get("name") or " ".join(check["cmd"])

    def res(status, reason="", tail=""):
        return {"name": name, "status": status, "reason": reason, "tail": tail}

    if check.get("needs_docker"):
        if not docker:
            docker.append(_docker_ok())
        if not docker[0]:
            return res("unverified", "docker unavailable")
    try:
        proc = subprocess.Popen(
            check["cmd"],
            cwd=worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except FileNotFoundError:
        return res("unverified", "command not found")
    except OSError as e:
        return res("unverified", f"cannot run: {e}")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            out, err = proc.communicate(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:  # внук ушёл в свою сессию и держит пайпы
            out, err = b"", b""
        return res("unverified", "timeout", _tail(out, err))
    if proc.returncode == 0:
        return res("pass", tail=_tail(out, err))
    own = _uv_own_error(check["cmd"], err)
    if own:
        return res("unverified", f"tool unavailable: {own}", _tail(out, err))
    return res("fail", f"exit {proc.returncode}", _tail(out, err))


def _remedy_files(worktree: str, base_head: str) -> int:
    """Файлы в диффе варианта: закоммиченное и нет, плюс новые. Считается ДО запуска
    проверок — те оставляют `__pycache__` и кэши."""
    names = set(_git(worktree, "diff", "--name-only", "-z", base_head).split("\0"))
    names |= set(
        _git(worktree, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
    )
    names.discard("")
    names.discard("node_modules")
    return len(names)


def _measure(path: str, timeout: int) -> dict | None:
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "measure.json")
        try:
            r = subprocess.run(
                [sys.executable, MEASURE, path, "--out", out],
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return None
        if r.returncode != 0 or not os.path.isfile(out):
            return None
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)


def _metric(m: dict | None, metric: str):
    if m is None:
        return None
    try:
        v = (
            resolve_path(m, metric)
            if ("." in metric or "[" in metric)
            else snapshot(m)[metric]
        )
        return None if isinstance(v, bool) else float(v)
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _num(v):
    return None if v is None else (int(v) if float(v).is_integer() else v)


def _gain(
    repo: str, worktree: str, finding: dict, timeout: int, measure_before: str | None
) -> dict:
    """gain по `gain_metric` находки на старом и новом замере. `gain_hit` — по
    `ledger._reached`, той же семантике, что у `verify`."""
    metric = (finding.get("gain_metric") or "").strip()
    out = {
        "gain_metric": metric or None,
        "gain_before": None,
        "gain_after": None,
        "gain_target": None,
        "gain_hit": False,
        "gain_reason": None,
        "before_source": None,
    }
    if not metric or metric.lower() == "manual":
        out["gain_reason"] = "gain not measurable: gain_metric missing or manual"
        return out
    try:
        target = float(str(finding.get("gain_target")).replace(",", "."))
    except ValueError:
        out["gain_reason"] = "gain not measurable: gain_target is not a number"
        return out
    out["gain_target"] = _num(target)

    if measure_before:
        out["before_source"] = "given"
    elif os.path.isfile(_before_path(worktree)):
        measure_before = _before_path(worktree)
        out["before_source"] = "clean_base"
    else:
        out["before_source"] = "repo_as_is"
    if measure_before:
        with open(measure_before, encoding="utf-8") as fh:
            before = json.load(fh)
    else:
        before = _measure(repo, timeout)
    after = _measure(worktree, timeout)
    b, a = _metric(before, metric), _metric(after, metric)
    out["gain_before"], out["gain_after"] = _num(b), _num(a)
    if a is None:
        out["gain_reason"] = "gain not measurable: metric missing in new measure"
        return out
    row = finding
    if not (finding.get("gain_direction") or "").strip() and b is not None and target != b:
        # без направления `_reached` зачёл бы «9 при цели 3» как попадание
        row = {**finding, "gain_direction": "down" if target < b else "up"}
    out["gain_hit"] = bool(_reached(a, target, row))
    return out


def _base_head(repo: str, worktree: str) -> str:
    r = subprocess.run(
        [
            "git",
            "-C",
            worktree,
            "merge-base",
            "HEAD",
            _git(repo, "rev-parse", "HEAD").strip(),
        ],
        capture_output=True,
        text=True,
    )
    return (
        r.stdout.strip()
        if r.returncode == 0 and r.stdout.strip()
        else _git(repo, "rev-parse", "HEAD").strip()
    )


def judge(
    repo: str,
    worktree: str,
    finding: dict,
    variant: dict,
    checks: list[dict],
    timeout: int = 900,
    *,
    measure_before: str | None = None,
    base_head: str | None = None,
    phase_of: str | None = None,
) -> dict:
    """Решение по пробе, кодом.

    `rejected` — любая проверка `fail`, gain не достигнут, окупаемость `too_costly`
    или `cold`. Иначе `unverified` — если есть непроверенное (таймаут, нет Docker,
    gain не измерим) или среди проверок нет прогона тестов. Иначе `accepted`.
    `measure_before` — замер «до»; по умолчанию тот, что `prepare` снял с чистой
    копии (`<worktree>.measure-before.json`), иначе меряется `repo` как есть.
    Пустой дифф (`remedy_files == 0`) — `rejected`. `base_head` — без него берётся merge-base ветки пробы и `HEAD` репо.
    `phase_of` — число фаз находки (или «?»), если проба покрывает только первую: цель `gain`
    задана на всю находку, промах по ней после одной фазы — не отказ, а `unverified`.
    """
    repo = os.path.abspath(repo)
    base = base_head or _base_head(repo, worktree)
    remedy_files = _remedy_files(worktree, base)

    docker: list[bool] = []
    results = [_run_check(c, worktree, timeout, docker) for c in checks]

    gain = _gain(repo, worktree, finding, timeout, measure_before)

    pain = pain_per_quarter(repo, finding_files(finding))["pain_files_per_quarter"]
    payback = payback_quarters(remedy_files, pain)
    verdict = payback_verdict(payback)

    gain_measured = gain["gain_reason"] is None
    rejected, unverified = [], []
    for c in results:
        if c["status"] == "fail":
            rejected.append(f"check failed: {c['name']}")
        elif c["status"] == "unverified":
            unverified.append(f"check unverified: {c['name']} ({c['reason']})")
    if remedy_files == 0:
        rejected.append("empty diff: variant changed nothing")
    partial = phase_of not in (None, "", "1")
    if gain_measured and not gain["gain_hit"]:
        if partial:
            unverified.append(f"фаза 1 из {phase_of}: цель находки не применима к фазе")
        else:
            rejected.append("gain target not reached")
    if not gain_measured:
        unverified.append(gain["gain_reason"])
    if verdict != "ok":
        rejected.append(f"payback {verdict}")
    if not any(is_test_check(c) for c in checks):
        # линтер и F821 не проверяют поведение (спека §4: тестов нет → unverified)
        unverified.append("no tests found")
    notes = []
    if gain["before_source"] == "repo_as_is" and worktree_dirty(repo):
        # замер «до» снят с грязного дерева: включает незакоммиченное пользователя
        notes.append("gain_before measured on a dirty repo (clean base measure missing)")
    status = "rejected" if rejected else "unverified" if unverified else "accepted"

    return {
        "variant": variant.get("id"),
        "checks": results,
        **gain,
        "remedy_files": remedy_files,
        "payback": None if payback is None else round(payback, 3),
        "payback_verdict": verdict,
        "status": status,
        "reasons": rejected + unverified + notes,
    }


# ── CLI ─────────────────────────────────────────────────────────────────────


def _load_finding(args) -> dict:
    if args.finding_json:
        return json.loads(args.finding_json)
    rows = [r for r in read_findings(args.findings) if r.get("id") == args.id]
    if not rows:
        sys.exit(f"находка {args.id} не найдена в {args.findings}")
    return rows[0]


def main():
    ap = argparse.ArgumentParser(description="Проверка делом — zodchiy")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("discover", help="проверки проекта списком")
    p.add_argument("--repo", default=".")

    p = sub.add_parser("prepare", help="worktree + ветка zodchiy/F<n>-<slug>")
    p.add_argument("--repo", default=".")
    p.add_argument("--id", required=True)
    p.add_argument("--slug", required=True)

    p = sub.add_parser("judge", help="прогнать проверки и принять решение")
    p.add_argument("--repo", default=".")
    p.add_argument("--worktree", required=True)
    p.add_argument("--findings")
    p.add_argument("--id")
    p.add_argument("--finding-json")
    p.add_argument("--variant", default="")
    p.add_argument("--checks-json", help="список проверок; без него — discover")
    p.add_argument("--measure-before")
    p.add_argument("--base-head")
    p.add_argument("--phase-of", help="число фаз находки (или ?), если проба — только первая фаза")
    p.add_argument("--timeout", type=int, default=900)

    p = sub.add_parser("cleanup", help="снять worktree")
    p.add_argument("--repo", default=".")
    p.add_argument("--worktree", required=True)
    p.add_argument("--keep-branch", action="store_true")

    args = ap.parse_args()
    if args.cmd == "discover":
        result = discover_checks(args.repo)
    elif args.cmd == "prepare":
        result = prepare(args.repo, args.id, args.slug)
    elif args.cmd == "judge":
        if not args.finding_json and not (args.findings and args.id):
            ap.error("нужен --finding-json или --findings и --id")
        checks = (
            json.loads(args.checks_json)
            if args.checks_json
            else discover_checks(args.worktree)
        )
        result = judge(
            args.repo,
            args.worktree,
            _load_finding(args),
            {"id": args.variant},
            checks,
            args.timeout,
            measure_before=args.measure_before,
            base_head=args.base_head,
            phase_of=args.phase_of,
        )
    else:
        cleanup(args.repo, args.worktree, args.keep_branch)
        result = {"cleaned": args.worktree, "branch_kept": args.keep_branch}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
