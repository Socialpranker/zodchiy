#!/usr/bin/env python3
"""Режим prune: мёртвый код в две фазы, статика и затем исполнение.

    python3 prune.py scan <repo> [--measure measure.json] [--out .zodchiy/prune.json]
    python3 prune.py instrument <prune.json> --control <file>:<func> [--ids P3,P7]
    python3 prune.py collect <prune.json> --log <файл|-> [...] [--min-control 20]
    python3 prune.py strip <repo>

Статика доказывает только лёгкую половину: на код никто не ссылается. Код,
который достижим, но не выполняется, она не видит, а удалять его по
подозрению никто не решится, и он живёт годами. Поэтому подозрительное не
удаляется и не угадывается: на него ставится маркер, и судит лог.

Лог без контрольной группы ничего не доказывает. Ноль срабатываний маркера
значит «мёртв» только тогда, когда маркер на заведомо живой функции за то же
окно сработал достаточно раз: иначе ноль означает, что окно короткое, логи не
те или маркеры не выкачены.
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import time

from behavior import TEST_HINTS
from common import RUN_DIR
from structure import collect_files

HIT = "ZODCHIY_PRUNE_HIT"
CONTROL = "ZODCHIY_PRUNE_CONTROL"
TAG = "# zodchiy-prune"
CHUNK = 400
DEFAULT_MIN_CONFIDENCE = 60
DEFAULT_MIN_CONTROL = 20

VULTURE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+): "
    r"(?:unused (?P<kind>import|variable|function|class|method|attribute|property)"
    r" '(?P<name>[^']+)'|(?P<other>.+?)) \((?P<conf>\d+)% confidence"
    r"(?:, (?P<size>\d+) lines?)?\)$"
)

# Вне Python имя ищем в шаблонах, конфигах и скриптах запуска: оттуда код
# зовут по строке, и граф импортов такого вызова не видит.
TEXT_EXT = {
    ".html", ".jinja", ".jinja2", ".j2", ".txt", ".md", ".yaml", ".yml",
    ".toml", ".json", ".cfg", ".ini", ".sh", ".service", ".env.example",
}
TEXT_NAMES = {"Dockerfile", "Procfile", "Makefile", "docker-compose.yml", "compose.yaml"}
ENTRY_BASENAMES = {
    "__init__.py", "__main__.py", "conftest.py", "setup.py", "manage.py",
    "wsgi.py", "asgi.py",
}
REFLECTION_RE = re.compile(r"\bgetattr\(|\bimportlib\b|__import__\(|\bglobals\(\)\[")
# Методы, свойства и атрибуты зовёт фреймворк или протокол по имени:
# совпадения имени в коде проекта для них нет и не будет.
HOOK_KINDS = {"method", "property", "attribute"}
INSTRUMENTABLE = {"function", "method", "module"}


def _is_test(path: str) -> bool:
    return any(h in path for h in TEST_HINTS)


def _vulture_version() -> str | None:
    try:
        proc = subprocess.run(["vulture", "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = proc.stdout.split()
    return words[-1] if proc.returncode == 0 and words else None


def run_vulture(repo: str, files: list[str], min_confidence: int) -> list[dict] | str:
    """Строки vulture или строка-причина сбоя."""
    rows: list[dict] = []
    for i in range(0, len(files), CHUNK):
        proc = subprocess.run(
            ["vulture", "--min-confidence", str(min_confidence), *files[i : i + CHUNK]],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=900,
        )
        # 0 — чисто, 3 — найден мёртвый код; остальное — сбой разбора
        if proc.returncode not in (0, 3):
            return f"vulture failed: {(proc.stderr or proc.stdout).strip()[-300:]}"
        for line in proc.stdout.splitlines():
            m = VULTURE_RE.match(line.strip())
            if not m:
                continue
            other = m["other"] or ""
            kind = m["kind"] or ("unreachable" if other.startswith("unreachable") else "other")
            rows.append({
                "file": os.path.relpath(os.path.join(repo, m["file"]), repo),
                "line": int(m["line"]),
                "kind": kind,
                "name": m["name"],
                "detail": other or None,
                "tool_confidence": int(m["conf"]),
                "size_lines": int(m["size"]) if m["size"] else None,
            })
    return rows


def _read(repo: str, rel: str) -> str:
    try:
        with open(os.path.join(repo, rel), encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def collect_text_files(repo: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", ".venv", "venv")]
        for f in files:
            if f in TEXT_NAMES or os.path.splitext(f)[1] in TEXT_EXT:
                out.append(os.path.relpath(os.path.join(root, f), repo))
    return sorted(out)


class Corpus:
    """Тексты репозитория один раз в памяти: проверка слепых зон идёт по
    каждому кандидату, и перечитывать дерево на каждом нельзя."""

    def __init__(self, repo: str, py: list[str]):
        self.py = {f: _read(repo, f) for f in py}
        self.other = {f: _read(repo, f) for f in collect_text_files(repo)}

    def string_mention(self, name: str, skip: str | None = None) -> list[str]:
        rx = re.compile(r"""['"`]""" + re.escape(name) + r"""['"`]""")
        return [f for f, t in self.py.items() if f != skip and rx.search(t)]

    def word_mention_outside_py(self, name: str) -> list[str]:
        rx = re.compile(r"\b" + re.escape(name) + r"\b")
        return [f for f, t in self.other.items() if rx.search(t)]


def blind_spots(cand: dict, corpus: Corpus) -> list[str]:
    """Почему статика могла ошибиться. Пустой список — кандидат доказуемо мёртв."""
    spots: list[str] = []
    text = corpus.py.get(cand["file"], "")
    lines = text.splitlines()
    name = cand["name"]
    kind = cand["kind"]

    if kind in HOOK_KINDS:
        spots.append("framework_hook_possible")
    line_text = lines[cand["line"] - 1].lstrip() if 0 < cand["line"] <= len(lines) else ""
    if line_text.startswith("@"):
        spots.append("decorator_registration")
    if kind == "import" and cand["file"].endswith("__init__.py"):
        spots.append("reexport")
    if REFLECTION_RE.search(text):
        spots.append("reflection_in_module")
    if name:
        if re.search(r"__all__\s*=.*['\"]" + re.escape(name) + r"['\"]", text, re.S):
            spots.append("exported_in_all")
        if corpus.string_mention(name):
            spots.append("string_reference")
        if corpus.word_mention_outside_py(name):
            spots.append("non_python_reference")
    return spots


def _module_names(rel: str) -> set[str]:
    stem = rel[:-3]
    dotted = stem.replace(os.sep, ".")
    names = {dotted, os.path.basename(stem)}
    parts = dotted.split(".")
    names.update(".".join(parts[i:]) for i in range(1, len(parts)))
    return {n for n in names if n}


def _is_script(text: str) -> bool:
    """Код на верхнем уровне: файл запускают руками, а не импортируют.

    Без `if __name__ == "__main__"` такой скрипт тоже без входящих рёбер, и
    первая проверка на живом репо отдала четыре рабочих скрипта как provable.
    """
    if text.startswith("#!"):
        return True
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for stmt in tree.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, (ast.Call, ast.Await)):
            return True
        if isinstance(stmt, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try)):
            return True
        if isinstance(stmt, ast.If) and "TYPE_CHECKING" not in ast.unparse(stmt.test):
            return True
    return False


def _imported_by_text(rel: str, corpus: Corpus) -> bool:
    """Импорт модуля по тексту, мимо графа.

    Граф `structure.py` не разрешает плоские импорты соседей при `sys.path`-хаке
    (`import load` из скрипта рядом): на живом репо пакет симуляции из шести
    файлов вышел целиком «сиротами». Отсутствие ребра в графе — не доказательство.
    """
    stem = re.escape(os.path.basename(rel)[:-3])
    rx = re.compile(
        rf"^\s*(?:from\s+(?:[\w.]*\.)?{stem}\s+import\b"
        rf"|import\s+(?:[\w.]*\.)?{stem}\b"
        rf"|from\s+[\w.]+\s+import\s+[^#\n]*\b{stem}\b)",
        re.M,
    )
    return any(rx.search(t) for f, t in corpus.py.items() if f != rel)


def orphan_modules(measure: dict, corpus: Corpus) -> list[dict]:
    """Модули без входящих рёбер, которые не похожи на точку входа."""
    adjacency = measure.get("structure", {}).get("adjacency", {})
    incoming = {dep for deps in adjacency.values() for dep in deps}
    out = []
    for rel, text in sorted(corpus.py.items()):
        if rel in incoming or _is_test(rel) or os.path.basename(rel) in ENTRY_BASENAMES:
            continue
        if _imported_by_text(rel, corpus):
            continue
        if "migrations" in rel.split(os.sep) or re.search(r"__name__\s*==\s*['\"]__main__['\"]", text):
            continue
        cand = {"file": rel, "line": 1, "kind": "module", "name": None,
                "detail": "no incoming imports", "tool_confidence": None,
                "size_lines": len(text.splitlines())}
        spots = []
        names = _module_names(rel)
        if any(corpus.string_mention(n, skip=rel) for n in names):
            spots.append("string_reference")
        if any(corpus.word_mention_outside_py(n) for n in names):
            spots.append("non_python_reference")
        if _is_script(text):
            # ручной запуск не попадает в логи прода: маркер тут ничего не докажет
            spots.append("standalone_script")
            cand["instrumentable"] = False
        cand["blind_spots"] = spots
        out.append(cand)
    return out


def last_touched_days(repo: str, rel: str, line: int, now: float) -> int | None:
    proc = subprocess.run(
        ["git", "-C", repo, "blame", "--porcelain", "-L", f"{line},{line}", "--", rel],
        capture_output=True,
        text=True,
    )
    m = re.search(r"^committer-time (\d+)$", proc.stdout, re.M)
    return int((now - int(m.group(1))) // 86400) if m else None


def _head(repo: str) -> str | None:
    proc = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True)
    return proc.stdout.strip() or None


def scan(repo: str, measure: dict | None, min_confidence: int) -> dict:
    files = collect_files(repo)
    py = [f for f in files if f.endswith(".py")]
    degraded: list[str] = []
    if any(os.path.splitext(f)[1] in (".ts", ".tsx", ".js", ".jsx") for f in files):
        degraded.append("js_ts: не анализируется, prune пока только для Python")
    out = {
        "repo": repo,
        "head": _head(repo),
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "min_confidence": min_confidence,
        "tools": {"vulture": {"available": shutil.which("vulture") is not None,
                              "version": None}},
        "degraded": degraded,
        "control": None,
        "window": None,
        "candidates": [],
    }
    if not py:
        out["note"] = "нечего проверять: в репозитории нет Python-файлов"
        return out
    if shutil.which("vulture") is None:
        degraded.append("vulture not found: символы не проверены, только модули-сироты")
        rows: list[dict] = []
    else:
        out["tools"]["vulture"]["version"] = _vulture_version()
        rows = run_vulture(repo, py, min_confidence)
        if isinstance(rows, str):
            degraded.append(rows)
            rows = []

    corpus = Corpus(repo, py)
    cands = []
    for r in rows:
        if _is_test(r["file"]):
            continue  # тесты, которые не могут упасть, — отдельный класс, см. prune-mode.md
        r["blind_spots"] = blind_spots(r, corpus)
        cands.append(r)
    if measure is None:
        degraded.append("orphan_modules: нет --measure, модули-сироты не искались")
    else:
        cands.extend(orphan_modules(measure, corpus))

    git_ok = out["head"] is not None
    if not git_ok:
        degraded.append("behavior: нет git, возраст строк неизвестен")
    now = time.time()
    cands.sort(key=lambda c: (c["file"], c["line"]))
    for i, c in enumerate(cands, 1):
        c["id"] = f"P{i}"
        c["instrumentable"] = c.get("instrumentable", c["kind"] in INSTRUMENTABLE)
        if not c["blind_spots"]:
            c["status"] = "provable"
        else:
            c["status"] = "suspected" if c["instrumentable"] else "manual"
        c["last_touched_days"] = last_touched_days(repo, c["file"], c["line"], now) if git_ok else None
        c["hits"] = None
    out["candidates"] = cands
    out["summary"] = summarize(cands)
    return out


def summarize(cands: list[dict]) -> dict:
    s: dict[str, int] = {}
    for c in cands:
        s[c["status"]] = s.get(c["status"], 0) + 1
    return dict(sorted(s.items()))


# ── маркеры ─────────────────────────────────────────────────────────────────


def marker_line(indent: str, label: str) -> str:
    # Одна строка без импорта на уровне модуля: снимается `strip` построчно по
    # тегу, и после снятия в файле не остаётся следов. logging без настройки
    # пишет warning в stderr, а stderr сервиса уже уходит в journald/docker.
    return f'{indent}__import__("logging").getLogger("zodchiy.prune").warning("{label}")  {TAG}\n'


def _func_node(tree: ast.AST, line: int, name: str | None):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.decorator_list[0].lineno if node.decorator_list else node.lineno
            if line in (node.lineno, first) and (name is None or node.name == name):
                return node
    return None


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _start(stmt: ast.stmt) -> int:
    # у вложенной функции или класса lineno указывает на def, а не на декоратор
    decos = getattr(stmt, "decorator_list", None) or []
    return min([stmt.lineno, *(d.lineno for d in decos)])


def insertion_point(src: str, kind: str, line: int, name: str | None) -> tuple[int, str] | None:
    """(индекс строки, отступ) для маркера или None, если ставить некуда."""
    tree = ast.parse(src)
    if kind == "module":
        body = list(tree.body)
        while body and (_is_docstring(body[0]) or (
                isinstance(body[0], ast.ImportFrom) and body[0].module == "__future__")):
            body.pop(0)
        if not body:
            return len(src.splitlines()), ""
        return _start(body[0]) - 1, ""
    node = _func_node(tree, line, name)
    if node is None:
        return None
    body = node.body
    if _is_docstring(body[0]):
        if len(body) > 1:
            target = body[1]
        elif body[0].lineno == node.lineno:
            return None
        else:
            return body[0].end_lineno, " " * body[0].col_offset
    else:
        target = body[0]
    if target.lineno == node.lineno:
        return None  # тело в строку с def: маркеру некуда встать
    return _start(target) - 1, " " * target.col_offset


def _insert(path: str, points: list[tuple[int, str, str]]) -> None:
    with open(path, encoding="utf-8") as fh:
        lines = fh.readlines()
    for idx, indent, label in sorted(points, reverse=True):
        lines.insert(idx, marker_line(indent, label))
    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)


def instrument(state: dict, control: str, ids: set[str] | None) -> tuple[dict, list[str], str | None]:
    """(состояние, предупреждения, фатальная ошибка). При фатальной дерево не тронуто."""
    repo = state["repo"]
    warnings: list[str] = []
    by_file: dict[str, list[tuple[int, str, str]]] = {}

    if state.get("control") is not None:
        return state, [], "маркеры уже стоят: сначала strip, потом scan заново"
    cfile, _, cfunc = control.partition(":")
    if not cfunc:
        return state, [], f"--control ждёт <file>:<func>, получено {control!r}"
    src = _read(repo, cfile)
    tree = ast.parse(src) if src else None
    node = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == cfunc), None) if tree else None
    if node is None:
        return state, [], f"контрольной функции {cfunc} нет в {cfile}"
    if any(c["file"] == cfile and c["name"] == cfunc for c in state["candidates"]):
        return state, [], f"контроль {control} сам в кандидатах: годится только заведомо живая функция"
    point = insertion_point(src, "function", node.lineno, cfunc)
    if point is None:
        return state, [], f"в {control} некуда поставить маркер"
    by_file.setdefault(cfile, []).append((*point, CONTROL))

    for c in state["candidates"]:
        if c["status"] != "suspected" or (ids is not None and c["id"] not in ids):
            continue
        src = _read(repo, c["file"])
        point = insertion_point(src, c["kind"], c["line"], c["name"])
        if point is None:
            c["status"] = "manual"
            warnings.append(f"{c['id']}: некуда поставить маркер, статус manual")
            continue
        by_file.setdefault(c["file"], []).append((*point, f"{HIT} {c['id']}"))
        c["status"] = "instrumented"

    for rel, points in by_file.items():
        _insert(os.path.join(repo, rel), points)
    state["control"] = {"target": control, "hits": None}
    state["summary"] = summarize(state["candidates"])
    return state, warnings, None


def strip(repo: str) -> int:
    removed = 0
    for rel in collect_files(repo):
        if not rel.endswith(".py"):
            continue
        path = os.path.join(repo, rel)
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        kept = [ln for ln in lines if not ln.rstrip().endswith(TAG)]
        if len(kept) != len(lines):
            removed += len(lines) - len(kept)
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(kept)
    return removed


def collect(state: dict, logs: list[str], min_control: int) -> dict:
    hit_re = re.compile(re.escape(HIT) + r" (P\d+)\b")
    counts: dict[str, int] = {}
    control_hits = 0
    for text in logs:
        control_hits += text.count(CONTROL)
        for m in hit_re.finditer(text):
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1

    enough = control_hits >= min_control
    state["control"] = {**(state.get("control") or {}), "hits": control_hits}
    state["window"] = {"min_control": min_control, "control_hits": control_hits, "sufficient": enough}
    for c in state["candidates"]:
        if c["status"] not in ("instrumented", "never_fired", "alive"):
            continue
        c["hits"] = counts.get(c["id"], 0)
        if c["hits"] > 0:
            c["status"] = "alive"
        elif enough:
            c["status"] = "never_fired"
        else:
            # ноль при коротком окне — не доказательство, кандидат ждёт
            c["status"] = "instrumented"
    state["summary"] = summarize(state["candidates"])
    return state


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _save(state: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)


def _brief(state: dict) -> str:
    parts = [f"{k}={v}" for k, v in state.get("summary", {}).items()] or ["кандидатов нет"]
    lines = ["prune: " + ", ".join(parts)]
    if state.get("window"):
        w = state["window"]
        verdict = "достаточно" if w["sufficient"] else "МАЛО: вердикта never_fired нет"
        lines.append(f"контроль: {w['control_hits']} срабатываний при пороге {w['min_control']} ({verdict})")
    lines.extend(f"деградация: {d}" for d in state.get("degraded", []))
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="мёртвый код в две фазы — zodchiy prune")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("scan", help="кандидаты статикой + слепые зоны")
    p.add_argument("repo")
    p.add_argument("--measure", default=None, help="measure.json: граф для модулей-сирот")
    p.add_argument("--out", default=None, help=f"по умолчанию <repo>/{RUN_DIR}/prune.json")
    p.add_argument("--min-confidence", type=int, default=DEFAULT_MIN_CONFIDENCE)

    p = sub.add_parser("instrument", help="маркеры на suspected + контроль")
    p.add_argument("state")
    p.add_argument("--control", required=True, help="<file>:<func> заведомо живой функции")
    p.add_argument("--ids", default=None, help="только эти id, через запятую")

    p = sub.add_parser("collect", help="срабатывания маркеров из логов → вердикт")
    p.add_argument("state")
    p.add_argument("--log", action="append", required=True, help="файл лога или - для stdin")
    p.add_argument("--min-control", type=int, default=DEFAULT_MIN_CONTROL)

    p = sub.add_parser("strip", help="снять все маркеры из дерева")
    p.add_argument("repo")

    args = ap.parse_args()

    if args.cmd == "scan":
        repo = os.path.abspath(args.repo)
        measure = _load(args.measure) if args.measure else None
        state = scan(repo, measure, args.min_confidence)
        out = args.out or os.path.join(repo, RUN_DIR, "prune.json")
        _save(state, out)
        print(_brief(state))
        print(f"→ {out}")
        return 0

    if args.cmd == "instrument":
        state = _load(args.state)
        ids = set(args.ids.split(",")) if args.ids else None
        state, warnings, fatal = instrument(state, args.control, ids)
        for w in warnings:
            print(w, file=sys.stderr)
        if fatal:
            print(fatal, file=sys.stderr)
            return 1
        _save(state, args.state)
        print(_brief(state))
        return 0

    if args.cmd == "collect":
        state = _load(args.state)
        texts = []
        for path in args.log:
            if path == "-":
                texts.append(sys.stdin.read())
            else:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    texts.append(fh.read())
        state = collect(state, texts, args.min_control)
        _save(state, args.state)
        print(_brief(state))
        return 0 if state["window"]["sufficient"] else 1

    removed = strip(os.path.abspath(args.repo))
    print(f"снято маркеров: {removed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
