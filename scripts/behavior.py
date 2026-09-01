#!/usr/bin/env python3
"""Поведенческая ось: чего архитектура стоит на практике.

Читает git-историю и считает то, что остальные инструменты спрашивают у модели
«на глаз»: что ломается вместе, где болит, держатся ли швы.

Выход — JSON на stdout. Модель его интерпретирует, но НЕ пересчитывает.

    python3 behavior.py <repo> [--since 18.months.ago] [--config .zodchiy/config.yaml]

Зависимостей нет: только git и стандартная библиотека.
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import re
import subprocess
import sys

from common import median as _median
from common import pct_rank
from common import quantile as _quantile
from common import wilson_lower

# ── Пороги. Числа эмпирические, калибруются на проекте (см. measure.py, calibrate()). ──

DEFAULTS = {
    # Пара файлов ниже этого числа совместных правок не даёт сцепления,
    # каким бы высоким ни был процент: 2 из 2 — это 100% и это шум.
    "coupling_min_shared": 5,
    "coupling_min_degree": 0.45,
    # Коммит шире этого — рефактор/переформатирование, а не связь. Из сцепления вон.
    "mega_commit_files": 25,
    # Поведенческая ось недоступна ниже этого: три коммита не история.
    "min_commits": 60,
    "knowledge_risk_share": 0.85,
    "knowledge_risk_min_edits": 8,
    "history_window": "18.months.ago",
    # Эпизод правки: подряд идущие коммиты по одному файлу, разрыв между
    # которыми не больше этого. Nagappan-Zeller берут 3 дня для burst-детектора,
    # здесь то же число и та же группировка — считается один раз, служит дважды.
    "episode_gap_days": 3,
    # Окно, в котором фикс после правки считается доделкой ЭТОЙ правки, а не
    # независимым багом. 14 дней — компромисс: короче теряет медленные отказы,
    # длиннее склеивает несвязанное.
    "rework_window_days": 14,
    "rework_min_changes": 5,
    "velocity_min_episodes": 3,
}

CODE_EXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
    ".swift",
    ".scala",
    ".ex",
    ".exs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".m",
    ".zig",
    ".lua",
    ".sql",
}

# Пути, которые считаются отдельно и не подмешиваются в основную метрику.
NOISE_DIRS = (
    "node_modules/",
    ".venv/",
    "venv/",
    "vendor/",
    "dist/",
    "build/",
    "target/",
    "__pycache__/",
    ".next/",
    ".nuxt/",
    "coverage/",
    "site-packages/",
)
GENERATED_HINTS = (
    "_pb2.py",
    ".pb.go",
    "_generated.",
    ".generated.",
    ".g.dart",
    ".d.ts",
    "schema.sql",
    "migrations/",
    "__snapshots__/",
)
TEST_HINTS = ("test", "spec", "__tests__", "e2e/", "conftest.py", "fixtures/")

FIX_RE = re.compile(r"^\s*(fix|bugfix|hotfix|patch|исправ|почин|fix\([^)]*\))", re.I)
REVERT_RE = re.compile(r"^\s*(revert|откат)", re.I)


# ── Классификация путей ─────────────────────────────────────────────────────


def classify(path: str) -> str:
    """code | test | generated | noise — что это за файл."""
    if any(d in path for d in NOISE_DIRS):
        return "noise"
    if os.path.splitext(path)[1] not in CODE_EXT:
        return "noise"
    if any(h in path for h in GENERATED_HINTS):
        return "generated"
    base = os.path.basename(path).lower()
    low = path.lower()
    if any(h in base for h in ("test", "spec")) or any(h in low for h in TEST_HINTS):
        return "test"
    return "code"


def layer_of(path: str, roots: list[str], depth: int) -> str:
    """Слой файла. roots — пакетные корни, которые надо пропустить
    (`src`, `repo-A`, `app`), чтобы слоем стал следующий сегмент."""
    parts = [p for p in path.split("/") if p not in (".", "")]
    while parts and parts[0] in roots:
        parts = parts[1:]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0]  # файл в корне пакета — сам себе слой
    return "/".join(parts[:depth])


def guess_roots(paths: list[str]) -> list[str]:
    """Пакетный корень — единственный первый сегмент, покрывающий почти всё дерево."""
    first = collections.Counter(p.split("/")[0] for p in paths if "/" in p)
    if not first:
        return []
    roots, total = [], sum(first.values())
    for name, n in first.most_common(3):
        if (n / total > 0.55 and name in ("src", "lib", "app", "pkg", "internal")) or (
            n / total > 0.75
        ):
            roots.append(name)
    return roots


# ── Чтение истории ──────────────────────────────────────────────────────────


# \x1f, а не \x1e: str.splitlines() режет по \x1c/\x1d/\x1e как по переводу строки
# и разваливает запись (\x00 в argv не пропускает exec). По той же причине в parse_log
# split("\n"), не splitlines().
LOG_SEP = "\x1f"
LOG_FMT = f"__C__%H{LOG_SEP}%an{LOG_SEP}%ct{LOG_SEP}%s"


def parse_log(out: str, sep: str = LOG_SEP) -> list[dict]:
    """Сырой вывод `git log --name-only --format=LOG_FMT` -> список коммитов.

    Отделено от git-вызова намеренно: 1 из 7 багов v1 сидел ровно здесь
    (`splitlines()` вместо `split("\n")`), и поймать его без прямого теста
    на парсер нечем — снаружи он выглядел как «багфиксов в репо нет».
    """
    commits, cur = [], None
    for line in out.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("__C__"):
            if cur:
                commits.append(cur)
            sha, author, ts, subj = (line[5:].split(sep) + ["", "", "", ""])[:4]
            cur = {
                "sha": sha,
                "author": author,
                "ts": int(ts or 0),
                "subject": subj,
                "files": [],
            }
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    if cur:
        commits.append(cur)
    return commits


def read_log(repo: str, since: str) -> list[dict]:
    """Коммиты с файлами. Один git-вызов, парсим сами."""
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                repo,
                "log",
                f"--since={since}",
                "--name-only",
                f"--format={LOG_FMT}",
                "--no-merges",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as e:
        sys.exit(f"git log не отработал: {e.stderr.strip()[:200]}")
    except FileNotFoundError:
        sys.exit("git не найден в PATH")

    return parse_log(out)


# ── Метрики ─────────────────────────────────────────────────────────────────


def temporal_coupling(commits, cfg):
    """Что меняется вместе. Отвечает на R2 числом, а не рассуждением.

    Сцепление БЕЗ ребра импорта — сигнал R3 (одно решение в двух местах);
    ребро импорта тут не проверяется, это делает structure.py, сводит judge.
    """
    pair, solo = collections.Counter(), collections.Counter()
    pair_shas = collections.defaultdict(list)
    for c in commits:
        fs = sorted({f for f in c["files"] if classify(f) == "code"})
        if not fs or len(fs) > cfg["mega_commit_files"]:
            continue
        for f in fs:
            solo[f] += 1
        for a, b in itertools.combinations(fs, 2):
            pair[(a, b)] += 1
            if len(pair_shas[(a, b)]) < 5:
                pair_shas[(a, b)].append(c["sha"][:9])

    rows = []
    for (a, b), n in pair.items():
        if n < cfg["coupling_min_shared"]:
            continue
        # Степень по слабейшему звену: если A всегда тянет B, но B живёт сам —
        # это односторонняя тяга, и min() её показывает честнее среднего.
        deg = n / min(solo[a], solo[b])
        if deg >= cfg["coupling_min_degree"]:
            rows.append(
                {
                    "a": a,
                    "b": b,
                    "shared": n,
                    "degree": round(deg, 3),
                    "edits_a": solo[a],
                    "edits_b": solo[b],
                    "commits": pair_shas[(a, b)],
                }
            )
    rank = pct_rank([r["degree"] for r in rows])
    for r in rows:
        r["degree_pct"] = rank(r["degree"])
    rows.sort(key=lambda r: (-r["degree"], -r["shared"]))
    return rows, solo


def hotspots(repo, solo, commits):
    """Где болит: churn x размер, плюс доля багфиксов в этом файле.

    Размер — прокси сложности. Точную сложность даёт structure.py;
    здесь она не нужна, важен порядок величин.
    """
    fixes = collections.Counter()
    for c in commits:
        if FIX_RE.match(c["subject"]):
            for f in c["files"]:
                if classify(f) == "code":
                    fixes[f] += 1
    out = []
    for f, n in solo.items():
        p = os.path.join(repo, f)
        try:
            with open(p, encoding="utf-8", errors="ignore") as fh:
                loc = sum(1 for _ in fh)
        except OSError:
            continue  # файл удалён — в текущей форме его нет
        out.append(
            {
                "file": f,
                "edits": n,
                "loc": loc,
                "score": n * loc,
                "fix_commits": fixes[f],
                "fix_share": round(fixes[f] / n, 3) if n else 0.0,
            }
        )
    # Рядом с каждым абсолютным числом — его место в распределении этого репо.
    for key in ("edits", "loc", "fix_share", "score"):
        rank = pct_rank([r[key] for r in out])
        for r in out:
            r[f"{key}_pct"] = rank(r[key])
    out.sort(key=lambda r: -r["score"])
    return out


def containment(commits, cfg, roots, depth):
    """Держатся ли швы: доля правок, уложившихся в один слой.

    Тесты исключены — иначе метрика меряет наличие тестов, а не архитектуру.
    Это не косметика: на живом репо разница вышла 17% против 51%.
    """
    inside, across = 0, 0
    cross = collections.Counter()
    examples = collections.defaultdict(list)
    touched = collections.Counter()

    for c in commits:
        fs = {f for f in c["files"] if classify(f) == "code"}
        if not fs or len(fs) > cfg["mega_commit_files"]:
            continue
        layers = {layer_of(f, roots, depth) for f in fs}
        for l in layers:
            touched[l] += 1
        if len(layers) == 1:
            inside += 1
        else:
            across += 1
            key = " + ".join(sorted(layers))
            cross[key] += 1
            if len(examples[key]) < 3:
                examples[key].append(
                    {"sha": c["sha"][:9], "subject": c["subject"][:90]}
                )

    total = inside + across
    return {
        "commits_considered": total,
        "inside_one_layer": inside,
        "across_layers": across,
        "ratio": round(inside / total, 3) if total else None,
        "layers_by_activity": dict(touched.most_common(15)),
        "top_crossings": [
            {
                "layers": k,
                "count": n,
                "share": round(n / total, 3) if total else 0,
                "examples": examples[k],
            }
            for k, n in cross.most_common(12)
        ],
    }


def knowledge_risk(commits, cfg):
    """Файл, который держит один человек. Не дефект кода — риск проекта."""
    authors = collections.defaultdict(collections.Counter)
    for c in commits:
        for f in c["files"]:
            if classify(f) == "code":
                authors[f][c["author"]] += 1
    out = []
    for f, ac in authors.items():
        tot = sum(ac.values())
        if tot < cfg["knowledge_risk_min_edits"]:
            continue
        top, cnt = ac.most_common(1)[0]
        if cnt / tot > cfg["knowledge_risk_share"]:
            out.append(
                {
                    "file": f,
                    "owner": top,
                    "share": round(cnt / tot, 3),
                    "edits": tot,
                    "contributors": len(ac),
                }
            )
    out.sort(key=lambda r: -r["edits"])
    return out


def episodes_by_file(commits, cfg) -> dict[str, list[list[dict]]]:
    """Эпизод — подряд идущие правки одного файла с разрывом <= порога.

    Одно изменение почти никогда не один коммит; мерить «правок в файле» без
    группировки — считать доделки за отдельные изменения и завышать скорость.
    """
    gap = cfg["episode_gap_days"] * 86400
    by_file: dict[str, list[dict]] = collections.defaultdict(list)
    for c in sorted(commits, key=lambda c: c["ts"]):
        for f in c["files"]:
            if classify(f) == "code":
                by_file[f].append(c)
    out: dict[str, list[list[dict]]] = {}
    for f, cs in by_file.items():
        groups, cur = [], [cs[0]]
        for prev, nxt in zip(cs, cs[1:]):
            if nxt["ts"] - prev["ts"] <= gap:
                cur.append(nxt)
            else:
                groups.append(cur)
                cur = [nxt]
        groups.append(cur)
        out[f] = groups
    return out


def velocity(commits, cfg, roots, depth) -> dict:
    """Во что обходится одно изменение: сколько мест тронуть и сколько тянется.

    Цена находки до сих пор считалась в пространстве (файлы, слои). Это она же
    во времени — то, чем «больно менять» и меряется на практике.
    """
    widths, by_layer = [], collections.defaultdict(list)
    for c in commits:
        fs = [f for f in c["files"] if classify(f) == "code"]
        if not fs or len(fs) > cfg["mega_commit_files"]:
            continue
        widths.append(len(fs))
        layers = {layer_of(f, roots, depth) for f in fs}
        if len(layers) == 1:
            by_layer[layers.pop()].append(len(fs))

    eps = episodes_by_file(commits, cfg)
    spans, sizes = [], []
    per_file = []
    for f, groups in eps.items():
        f_spans = [(g[-1]["ts"] - g[0]["ts"]) / 86400 for g in groups]
        f_sizes = [len(g) for g in groups]
        spans += f_spans
        sizes += f_sizes
        if len(groups) >= cfg["velocity_min_episodes"]:
            per_file.append(
                {
                    "file": f,
                    "episodes": len(groups),
                    "median_span_days": _median(f_spans),
                    "median_commits": _median(f_sizes),
                }
            )
    per_file.sort(key=lambda r: (-(r["median_span_days"] or 0), -r["episodes"]))

    return {
        "touch_cost": {
            "median_files_per_commit": _median(widths),
            "p90_files_per_commit": _quantile(widths, 0.9),
            "by_layer_median": {
                k: _median(v) for k, v in sorted(by_layer.items(), key=lambda kv: -len(kv[1]))[:15]
            },
        },
        "episodes": {
            "total": len(spans),
            "median_commits": _median(sizes),
            "median_span_days": _median(spans),
            "p90_span_days": _quantile(spans, 0.9),
            # Эпизод из одного коммита — изменение, сделанное с первого раза.
            # Медиана по всем эпизодам почти всегда 0 дней и ничего не говорит;
            # говорит доля эпизодов с доделками и то, сколько тянутся они.
            "multi_commit_share": (
                round(sum(1 for x in sizes if x > 1) / len(sizes), 3) if sizes else None
            ),
            "median_span_days_multi": _median([s for s, n in zip(spans, sizes) if n > 1]),
            "median_commits_multi": _median([n for n in sizes if n > 1]),
        },
        "slowest_files": per_file[:20],
    }


def stability(commits, cfg) -> dict:
    """Держится ли изменение: доля правок, за которыми пришёл фикс.

    Это не то же, что `fix_share`. `fix_share` говорит «файл часто чинят»,
    `rework_rate` — «правки ЭТОГО файла не держатся». Диагнозы разные и
    лечение разное: первое про сложность места, второе про отсутствие
    обратной связи (тест не ловит, ловит пользователь).
    """
    window = cfg["rework_window_days"] * 86400
    changes: dict[str, list[int]] = collections.defaultdict(list)
    fixes: dict[str, list[int]] = collections.defaultdict(list)
    reverts: collections.Counter = collections.Counter()

    for c in sorted(commits, key=lambda c: c["ts"]):
        is_fix = bool(FIX_RE.match(c["subject"]))
        is_revert = bool(REVERT_RE.match(c["subject"]))
        for f in c["files"]:
            if classify(f) != "code":
                continue
            if is_revert:
                reverts[f] += 1
            (fixes if is_fix else changes)[f].append(c["ts"])

    rows, latencies = [], []
    tot_changes = tot_reworked = 0
    for f, ts_list in changes.items():
        fx = fixes.get(f, [])
        reworked, f_lat = 0, []
        for t in ts_list:
            nxt = next((x for x in fx if t < x <= t + window), None)
            if nxt is not None:
                reworked += 1
                f_lat.append((nxt - t) / 86400)
        tot_changes += len(ts_list)
        tot_reworked += reworked
        latencies += f_lat
        if len(ts_list) >= cfg["rework_min_changes"]:
            rows.append(
                {
                    "file": f,
                    "changes": len(ts_list),
                    "reworked": reworked,
                    "rework_rate": round(reworked / len(ts_list), 3),
                    "rework_rate_lb": wilson_lower(reworked, len(ts_list)),
                    "fix_latency_median_days": _median(f_lat),
                    "reverts": reverts[f],
                }
            )
    rank = pct_rank([r["rework_rate_lb"] for r in rows])
    for r in rows:
        r["rework_rate_pct"] = rank(r["rework_rate_lb"])
    # Сортировка по нижней границе, а не по сырой доле: иначе «5 из 5» встаёт
    # выше «55 из 67» и первой строкой отчёта идёт шум.
    rows.sort(key=lambda r: (-r["rework_rate_lb"], -r["changes"]))

    return {
        "rework_rate": round(tot_reworked / tot_changes, 3) if tot_changes else None,
        "changes_considered": tot_changes,
        "fix_latency_median_days": _median(latencies),
        "revert_commits": sum(1 for c in commits if REVERT_RE.match(c["subject"])),
        "revert_files_top": [
            {"file": f, "reverts": n} for f, n in reverts.most_common(10)
        ],
        "unstable_files": rows[:20],
    }


def churn_profile(commits):
    """Сводка по типам файлов — вход для калибровки."""
    prof = collections.Counter()
    for c in commits:
        for f in c["files"]:
            prof[classify(f)] += 1
    return dict(prof)


# ── Сборка ──────────────────────────────────────────────────────────────────


def load_config(path):
    """Мини-парсер плоского YAML: только `ключ: значение`. Без зависимостей.
    Вложенность и списки не поддерживаются намеренно — конфиг плоский."""
    cfg = dict(DEFAULTS)
    if not path or not os.path.exists(path):
        return cfg
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line or raw[:1].isspace():
                continue
            k, v = (x.strip() for x in line.split(":", 1))
            if k not in DEFAULTS or not v:
                continue
            try:
                cfg[k] = float(v) if "." in v else int(v)
            except ValueError:
                cfg[k] = v.strip("\"'")
    return cfg


def main():
    ap = argparse.ArgumentParser(description="Поведенческая ось zodchiy")
    ap.add_argument("repo")
    ap.add_argument("--since", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument(
        "--layer-depth",
        type=int,
        default=1,
        help="сколько сегментов пути считать слоем",
    )
    ap.add_argument(
        "--roots",
        default=None,
        help="пакетные корни через запятую; по умолчанию выводятся",
    )
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(os.path.join(repo, ".git")):
        sys.exit(
            json.dumps(
                {"error": "не git-репозиторий", "repo": repo}, ensure_ascii=False
            )
        )

    cfg = load_config(args.config)
    since = args.since or cfg["history_window"]
    commits = read_log(repo, since)
    code_commits = [
        c for c in commits if any(classify(f) == "code" for f in c["files"])
    ]

    head = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "-C", repo, "status", "--porcelain"], capture_output=True, text=True
        ).stdout.strip()
    )

    # Ось доступна не всегда. Молчать об этом нельзя: два десятка коммитов
    # дадут числа, которые выглядят как метрика, но ничего не значат.
    available = len(code_commits) >= cfg["min_commits"]
    result = {
        "axis": "behavior",
        "snapshot": {
            "repo": repo,
            "branch": branch,
            "head": head[:12],
            "worktree": "dirty" if dirty else "clean",
            "since": since,
        },
        "available": available,
        "commits_total": len(commits),
        "commits_with_code": len(code_commits),
        "churn_profile": churn_profile(commits),
        "thresholds": cfg,
    }
    if not available:
        result["reason"] = (
            f"история короче порога: {len(code_commits)} коммитов с кодом при "
            f"минимуме {cfg['min_commits']}. Поведенческая ось недоступна, "
            f"потолок находок — finding, не verdict."
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    all_paths = [f for c in code_commits for f in c["files"] if classify(f) == "code"]
    roots = (
        [r.strip() for r in args.roots.split(",")]
        if args.roots
        else guess_roots(all_paths)
    )

    coupling, solo = temporal_coupling(code_commits, cfg)
    result.update(
        {
            "package_roots": roots,
            "temporal_coupling": coupling[:40],
            "temporal_coupling_total": len(coupling),
            "hotspots": hotspots(repo, solo, code_commits)[:25],
            "containment": containment(code_commits, cfg, roots, args.layer_depth),
            "knowledge_risk": knowledge_risk(code_commits, cfg)[:20],
            "reverts": sum(1 for c in code_commits if REVERT_RE.match(c["subject"])),
            "velocity": velocity(code_commits, cfg, roots, args.layer_depth),
            "stability": stability(code_commits, cfg),
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
