#!/usr/bin/env python3
"""Детектор клонов: окна нормализованных токенов tree-sitter.

    python3 clones.py <repo> [--window 40] [--min-shared 2]

Идентификаторы → `ID`, литералы → `LIT`, комментарии выброшены: переименованная
копия даёт тот же поток токенов. Скользящее окно из `window` токенов хэшируется;
два файла — пара клонов, если делят не меньше `min_shared` окон. Группы — связные
компоненты пар. Только stdlib и tree-sitter из `structure.py`; без него —
`available: false` с причиной, а не пустой список, похожий на «дублей нет».
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import os
import sys
import tempfile

from structure import LANGS, Parsers, collect_files

CLONE_EXT = (".py", ".ts", ".tsx", ".js")
MAX_PAIRS = 40  # в выход; полное число — `pairs_total`
MAX_GROUPS = 40
# Окно, встреченное в стольких файлах, — шаблон (блок импортов, boilerplate),
# а не копия: ради него C(n,2) пар только заглушили бы настоящие. Число
# отброшенных окон — в `common_windows_skipped`.
MAX_FILES_PER_WINDOW = 50

LIT_TYPES = {
    "string", "string_literal", "template_string", "number", "integer", "float",
    "regex", "concatenated_string", "string_content",
}
SKIP_TYPES = {"comment"}


def _is_id(t: str) -> bool:
    return t == "identifier" or t.endswith("_identifier")


def tokens(root) -> list[tuple[str, int, int]]:
    """[(токен, строка начала, строка конца)] в порядке исходника."""
    out: list[tuple[str, int, int]] = []
    stack = [root]
    while stack:
        n = stack.pop()
        t = n.type
        if t in SKIP_TYPES:
            continue
        if t in LIT_TYPES:
            tok = "LIT"
        elif _is_id(t):
            tok = "ID"
        elif n.child_count == 0:
            tok = t
        else:
            stack.extend(reversed(n.children))
            continue
        out.append((tok, n.start_point[0] + 1, n.end_point[0] + 1))
    return out


def _windows(toks: list[tuple[str, int, int]], window: int) -> dict[bytes, tuple[int, int]]:
    """хэш окна → (первая строка, последняя строка) первого вхождения."""
    found: dict[bytes, tuple[int, int]] = {}
    for i in range(len(toks) - window + 1):
        chunk = toks[i : i + window]
        h = hashlib.blake2b(" ".join(t for t, _, _ in chunk).encode(), digest_size=8).digest()
        found.setdefault(h, (chunk[0][1], chunk[-1][2]))
    return found


def _groups(pairs: list[tuple[str, str]]) -> list[dict]:
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    comps: dict[str, list[str]] = collections.defaultdict(list)
    for f in list(parent):
        comps[find(f)].append(f)
    groups = [{"files": sorted(fs), "size": len(fs)} for fs in comps.values()]
    return sorted(groups, key=lambda g: (-g["size"], g["files"]))


def find_clones(repo: str, files: list[str], window: int = 40, min_shared: int = 2) -> dict:
    out: dict = {
        "available": False,
        "window": window,
        "pairs": [],
        "pairs_total": 0,
        "groups": [],
        "groups_total": 0,
    }
    parsers = Parsers()
    if not parsers.ok:
        out["reason"] = "tree-sitter not installed"
        return out

    candidates = [f for f in files if os.path.splitext(f)[1] in CLONE_EXT]
    per_file: dict[str, dict[bytes, tuple[int, int]]] = {}
    no_grammar: set[str] = set()
    for rel in candidates:
        lang = LANGS[os.path.splitext(rel)[1]]
        parser = parsers.get(lang)
        if parser is None:
            no_grammar.add(lang)
            continue
        try:
            with open(os.path.join(repo, rel), "rb") as fh:
                tree = parser.parse(fh.read())
        except OSError:
            continue
        per_file[rel] = _windows(tokens(tree.root_node), window)
    if candidates and not per_file and no_grammar:
        out["reason"] = "tree-sitter grammars missing: " + ", ".join(sorted(no_grammar))
        return out
    out["available"] = True
    out["files_checked"] = len(per_file)
    out["files_skipped"] = len(candidates) - len(per_file)  # нет грамматики или не читается

    index: dict[bytes, list[str]] = collections.defaultdict(list)
    for rel, wins in per_file.items():
        for h in wins:
            index[h].append(rel)
    shared: dict[tuple[str, str], list[bytes]] = collections.defaultdict(list)
    skipped = 0
    for h, owners in index.items():
        if len(owners) > MAX_FILES_PER_WINDOW:
            skipped += 1
            continue
        for a, b in itertools.combinations(sorted(owners), 2):
            shared[(a, b)].append(h)
    out["common_windows_skipped"] = skipped
    out["grammars_missing"] = sorted(no_grammar)

    rows = []
    for (a, b), hs in shared.items():
        if len(hs) < min_shared:
            continue
        rows.append(
            {
                "a": a,
                "b": b,
                "shared_windows": len(hs),
                "lines_a": [min(per_file[a][h][0] for h in hs), max(per_file[a][h][1] for h in hs)],
                "lines_b": [min(per_file[b][h][0] for h in hs), max(per_file[b][h][1] for h in hs)],
            }
        )
    rows.sort(key=lambda r: (-r["shared_windows"], r["a"], r["b"]))
    groups = _groups([(r["a"], r["b"]) for r in rows])
    out["pairs_total"] = len(rows)
    out["pairs"] = rows[:MAX_PAIRS]
    out["groups_total"] = len(groups)
    out["groups"] = groups[:MAX_GROUPS]
    return out


# ── Контрольная группа для калибровки ───────────────────────────────────────
# Известные копии и известный «похожий, но другой» лежат в самом скрипте, а не
# в репо пользователя: калибровка обязана отвечать «детектору можно верить»
# независимо от того, есть ли в репо дубли.

_CONTROL_COPY = """\
export function {fn}({a}: number, {b}: string): string {{
  const {neg} = {a} < 0;
  const {abs} = Math.abs({a});
  const {whole} = Math.floor({abs});
  const {frac} = Math.round(({abs} - {whole}) * 100);
  const {txt} = String({whole});
  let {out} = "";
  for (let {i} = 0; {i} < {txt}.length; {i}++) {{
    const {left} = {txt}.length - {i};
    {out} += {txt}[{i}];
    if ({left} > 1 && {left} % 3 === 1) {{
      {out} += " ";
    }}
  }}
  return ({neg} ? "-" : "") + {out} + "." + {frac} + " " + {b};
}}
"""
_CONTROL_NAMES = (
    dict(fn="formatMoney", a="amount", b="currency", neg="negative", abs="abs", whole="whole",
         frac="cents", txt="digits", out="grouped", i="i", left="fromEnd"),
    dict(fn="renderPrice", a="value", b="unit", neg="isMinus", abs="absolute", whole="integer",
         frac="fraction", txt="text", out="result", i="idx", left="remaining"),
)
_CONTROL_OTHER = """\
export function formatDuration(totalSeconds: number): string {
  const parts: string[] = [];
  let rest = Math.max(0, Math.floor(totalSeconds));
  const units: Array<[string, number]> = [["d", 86400], ["h", 3600], ["m", 60], ["s", 1]];
  for (const [label, size] of units) {
    const count = Math.floor(rest / size);
    if (count > 0) {
      parts.push(count + label);
      rest -= count * size;
    }
  }
  return parts.length === 0 ? "0s" : parts.slice(0, 2).join(" ");
}
"""


_CONTROL_PY_COPY = """\
def {fn}({a}, {b}):
    {neg} = {a} < 0
    {abs} = abs({a})
    {whole} = int({abs})
    {frac} = round(({abs} - {whole}) * 100)
    {txt} = str({whole})
    {out} = ""
    for {i}, {ch} in enumerate({txt}):
        {left} = len({txt}) - {i}
        {out} += {ch}
        if {left} > 1 and {left} % 3 == 1:
            {out} += " "
    return ("-" if {neg} else "") + {out} + "." + str({frac}) + " " + {b}
"""
_CONTROL_PY_NAMES = (
    dict(fn="format_money", a="amount", b="currency", neg="negative", abs="magnitude",
         whole="whole", frac="cents", txt="digits", out="grouped", i="i", ch="ch", left="from_end"),
    dict(fn="render_price", a="value", b="unit", neg="is_minus", abs="absolute",
         whole="integer", frac="fraction", txt="text", out="result", i="idx", ch="c", left="remaining"),
)
_CONTROL_PY_OTHER = """\
def format_duration(total_seconds):
    parts = []
    rest = max(0, int(total_seconds))
    for label, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        count = rest // size
        if count > 0:
            parts.append(str(count) + label)
            rest -= count * size
    if not parts:
        return "0s"
    return " ".join(parts[:2])
"""

# язык → (расширение, шаблон копии, имена для двух копий, «другой» файл)
_CONTROLS = {
    "typescript": (".ts", _CONTROL_COPY, _CONTROL_NAMES, _CONTROL_OTHER),
    "python": (".py", _CONTROL_PY_COPY, _CONTROL_PY_NAMES, _CONTROL_PY_OTHER),
}


def _control_one(ext: str, tpl: str, names: tuple, other: str) -> tuple[str, str]:
    with tempfile.TemporaryDirectory() as d:
        files = {
            f"copy_a{ext}": tpl.format(**names[0]),
            f"copy_b{ext}": tpl.format(**names[1]),
            f"other{ext}": other,
        }
        for name, text in files.items():
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(text)
        res = find_clones(d, sorted(files))
    if not res["available"]:
        return "unavailable", res.get("reason", "детектор клонов недоступен")
    grouped = {f for g in res["groups"] for f in g["files"]}
    if {f"copy_a{ext}", f"copy_b{ext}"} - grouped:
        return "failed", "контрольные переименованные копии не найдены: метрика слепа"
    if f"other{ext}" in grouped:
        return "failed", "контрольный «похожий, но другой» файл принят за клон: метрика слипает"
    return "ok", "контрольная пара найдена, похожий файл отсеян"


def control() -> tuple[str, str]:
    """(статус, деталь). Контроль гоняется на каждом языке, чья грамматика есть.

    `ok` — все доступные языки прошли; `failed` — детектор доступен, но слеп или
    слипает (это блокирует метрику); `unavailable` — ни один язык не разобрался
    (нет tree-sitter или грамматик): метрику нечем проверить, но и обвинить её
    не в чем — недоступность инструмента потолок прогона не опускает.
    """
    results = {lang: _control_one(ext, *rest) for lang, (ext, *rest) in _CONTROLS.items()}
    failed = {lang: d for lang, (st, d) in results.items() if st == "failed"}
    if failed:
        return "failed", "; ".join(f"{lang}: {d}" for lang, d in failed.items())
    ok = [lang for lang, (st, _) in results.items() if st == "ok"]
    off = {lang: d for lang, (st, d) in results.items() if st == "unavailable"}
    if not ok:
        return "unavailable", "; ".join(f"{lang}: {d}" for lang, d in off.items())
    detail = f"контрольная пара найдена, похожий файл отсеян ({', '.join(ok)})"
    if off:
        detail += "; не проверено: " + "; ".join(f"{lang}: {d}" for lang, d in off.items())
    return "ok", detail


def main():
    ap = argparse.ArgumentParser(description="Детектор клонов — zodchiy (отладка)")
    ap.add_argument("repo")
    ap.add_argument("--window", type=int, default=40)
    ap.add_argument("--min-shared", type=int, default=2)
    args = ap.parse_args()
    repo = os.path.abspath(args.repo)
    res = find_clones(repo, collect_files(repo), args.window, args.min_shared)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["available"] else 1


if __name__ == "__main__":
    sys.exit(main())
