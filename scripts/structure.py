#!/usr/bin/env python3
"""Структурная ось: какая архитектура по форме.

Граф импортов через tree-sitter, циклы обходом графа (Tarjan), fan-in/out,
глубина модуля, ветвление. Никакого «модель посмотрит грепом и решит».

    python3 structure.py <repo> [--json out.json] [--roots src,app]

tree-sitter опционален: без него разбор падает на регулярки с честной пометкой
`parser: regex` — числа те же по смыслу, но точность ниже, и это видно в выходе.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

from common import pct_rank

# ── Языки ───────────────────────────────────────────────────────────────────

LANGS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c_sharp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".ex": "elixir",
    ".exs": "elixir",
    ".lua": "lua",
    ".zig": "zig",
}

NOISE_DIRS = (
    "node_modules",
    ".venv",
    "venv",
    "vendor",
    "dist",
    "build",
    "target",
    "__pycache__",
    ".next",
    ".nuxt",
    "coverage",
    ".git",
    "site-packages",
)

# Узлы, ветвящие поток управления. Приближение цикломатической сложности
# по McCabe: число решений + 1.
BRANCH_NODES = {
    "if_statement",
    "elif_clause",
    "else_clause",
    "for_statement",
    "while_statement",
    "case_clause",
    "switch_case",
    "catch_clause",
    "except_clause",
    "conditional_expression",
    "ternary_expression",
    "boolean_operator",
    "binary_expression",
    "match_arm",
    "when_clause",
    "guard_statement",
    "do_statement",
    "for_in_statement",
}

IMPORT_RE = {
    "python": re.compile(r"^\s*(?:from\s+([.\w]+)\s+import|import\s+([.\w]+))", re.M),
    "js": re.compile(
        r"""(?:^\s*import\s[^'"]*from\s*['"]([^'"]+)['"]"""
        r"""|^\s*import\s*['"]([^'"]+)['"]"""
        r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
        r"""|^\s*export\s[^'"]*from\s*['"]([^'"]+)['"])""",
        re.M | re.X,
    ),
    "generic": re.compile(
        r"""^\s*(?:import|use|#include|require)\s+["'<]?([\w./:\-]+)""", re.M
    ),
}


# ── tree-sitter, если есть ──────────────────────────────────────────────────


class Parsers:
    """Ленивая загрузка грамматик. Отсутствующая — не ошибка, а деградация."""

    def __init__(self):
        self.cache: dict[str, object] = {}
        try:
            import tree_sitter  # noqa: F401

            self.ok = True
        except ImportError:
            self.ok = False
        self.missing: set[str] = set()

    def get(self, lang: str):
        if not self.ok or lang in self.missing:
            return None
        if lang in self.cache:
            return self.cache[lang]
        try:
            from tree_sitter import Language, Parser

            mod_name = {
                "tsx": "tree_sitter_typescript",
                "typescript": "tree_sitter_typescript",
            }.get(lang, f"tree_sitter_{lang}")
            mod = __import__(mod_name)
            if lang in ("typescript", "tsx"):
                raw = getattr(
                    mod, "language_tsx" if lang == "tsx" else "language_typescript"
                )()
            else:
                raw = mod.language()
            parser = Parser(Language(raw))
            self.cache[lang] = parser
            return parser
        except Exception:
            self.missing.add(lang)
            return None


def walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.children)


def _txt(src: bytes, n) -> str:
    return src[n.start_byte : n.end_byte].decode("utf8", "ignore").strip("\"'`")


def _is_type_only(src: bytes, node) -> bool:
    """Импорт существует только для тайпчекера и в рантайме не выполняется.

    Такое ребро НЕ создаёт цикла и не стоит ничего: `if TYPE_CHECKING:` в Python
    ровно для того и введён, чтобы разорвать циклическую типизацию. Проверено на
    repo-A: единственный найденный там «цикл» оказался этим. Смешать его с
    настоящим — выдать ложную находку первой же строкой отчёта.
    """
    if node.type == "import_statement":  # TS: import type { X } from "y"
        for c in node.children:
            if c.type == "type":
                return True
    p = node.parent
    while p is not None:
        if p.type in ("if_statement", "if_clause"):
            cond = p.child_by_field_name("condition")
            if cond is not None and "TYPE_CHECKING" in _txt(src, cond):
                return True
        p = p.parent
    return False


def imports_via_ts(parser, src: bytes, lang: str) -> list[tuple[str, str]]:
    """[(модуль, kind)], где kind — runtime | type.

    Берётся именованным полем узла (`module_name` / `source`), а не первым
    подходящим потомком: обход идёт не в порядке исходника, и «первый
    подходящий» у `from pkg.mod import Name` — это `Name`. На repo-A такая
    ошибка давала 5 рёбер вместо ~1400 и выглядела как «зависимостей нет».
    """
    tree = parser.parse(src)
    out: list[tuple[str, str]] = []

    def add(node, text):
        if text:
            out.append((text, "type" if _is_type_only(src, node) else "runtime"))

    for n in walk(tree.root_node):
        t = n.type

        # Python: from X import a, b
        if t == "import_from_statement":
            mod = n.child_by_field_name("module_name")
            if mod is None:
                continue
            base = _txt(src, mod)
            add(n, base)
            # `from pkg import sub` тянет pkg/sub.py, а не только pkg/__init__.py.
            # Без этой ветки цикл через подмодуль не виден вовсе: проверено —
            # гейт пропускал внесённый вручную цикл и отвечал OK.
            for c in n.children:
                if c is mod or c.type in (
                    "from",
                    "import",
                    ",",
                    "(",
                    ")",
                    "wildcard_import",
                ):
                    continue
                if c.type == "aliased_import":
                    c = c.child_by_field_name("name") or c
                if c.type in ("dotted_name", "identifier"):
                    nm = _txt(src, c)
                    if nm:
                        add(
                            n,
                            f"{base}.{nm}" if not base.endswith(".") else f"{base}{nm}",
                        )
            continue

        # JS/TS: import ... from "X" / import "X"   |   Python: import a.b, c
        if t == "import_statement":
            srcf = n.child_by_field_name("source")
            if srcf is not None:
                add(n, _txt(src, srcf))
                continue
            for c in n.children:
                if c.type == "dotted_name":
                    add(n, _txt(src, c))
                elif c.type == "aliased_import":
                    nm = c.child_by_field_name("name")
                    if nm is not None:
                        add(n, _txt(src, nm))
                elif c.type in ("string", "string_literal"):
                    add(n, _txt(src, c))
            continue

        # JS/TS: export { x } from "X"
        if t == "export_statement":
            srcf = n.child_by_field_name("source")
            if srcf is not None:
                add(n, _txt(src, srcf))
            continue

        # Go / Java / Rust / C: одна строка или скоуп-путь внутри объявления
        if t in (
            "import_spec",
            "import_declaration",
            "use_declaration",
            "preproc_include",
            "use_wildcard",
        ):
            for c in walk(n):
                if c.type in (
                    "interpreted_string_literal",
                    "string_literal",
                    "scoped_identifier",
                    "string_fragment",
                    "system_lib_string",
                    "scoped_use_list",
                    "use_as_clause",
                    "crate",
                    "identifier",
                ):
                    txt = _txt(src, c)
                    if txt and txt not in ("import", "use", "include", "pub"):
                        add(n, txt)
                        break
            continue

        # require("X")
        if t == "call_expression":
            fn = n.child_by_field_name("function")
            if fn is None or _txt(src, fn) not in ("require", "import"):
                continue
            args = n.child_by_field_name("arguments")
            if args is None:
                continue
            for c in args.children:
                if c.type in ("string", "string_literal"):
                    add(n, _txt(src, c))
                    break

    return [(s, k) for s, k in out if s]


FUNC_NODES = (
    "function_definition",
    "function_declaration",
    "function_item",
    "method_definition",
    "method_declaration",
    "arrow_function",
    "function_expression",
    "constructor_declaration",
    "func_literal",
)


def _is_func(t: str) -> bool:
    return t in FUNC_NODES or ("function" in t and "type" not in t)


def complexity_via_ts(parser, src: bytes) -> dict:
    """Сложность файла И максимум по функции.

    McCabe (1976) задаёт порог на ФУНКЦИЮ, не на файл: сумма по файлу растёт
    с его длиной и сравнивать её с «>10» бессмысленно. Оба числа отдаются
    отдельно — `per_function_max` сопоставим с порогом, `total` годится только
    для ранжирования файлов между собой.
    """
    tree = parser.parse(src)
    total, funcs, max_depth = 0, 0, 0
    per_func_max, worst_line = 0, 0

    stack = [(tree.root_node, 0)]
    while stack:
        n, d = stack.pop()
        if n.type in BRANCH_NODES:
            total += 1
        nd = d + 1 if n.type in BRANCH_NODES else d
        max_depth = max(max_depth, nd)
        if _is_func(n.type):
            funcs += 1
            inner = sum(1 for c in walk(n) if c.type in BRANCH_NODES) + 1
            if inner > per_func_max:
                per_func_max, worst_line = inner, n.start_point[0] + 1
        stack.extend((c, nd) for c in n.children)

    return {
        "cyclomatic_total": total + 1,
        "cyclomatic_per_function_max": per_func_max or (total + 1),
        "worst_function_line": worst_line,
        "max_nesting": max_depth,
        "functions": funcs,
    }


# ── Обход дерева файлов ─────────────────────────────────────────────────────


def collect_files(repo: str) -> list[str]:
    out = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in NOISE_DIRS and not d.startswith(".")]
        for f in files:
            if os.path.splitext(f)[1] in LANGS:
                out.append(os.path.relpath(os.path.join(root, f), repo))
    return sorted(out)


def extract_imports(repo, rel, lang, parsers) -> tuple[list[tuple[str, str]], str]:
    """[(модуль, runtime|type)], бэкенд разбора.

    На регулярках kind всегда `runtime`: отличить `if TYPE_CHECKING:` без дерева
    нельзя, а гадать хуже, чем честно завысить. Бэкенд виден в выходе, чтобы
    судящая фаза знала цену числам.
    """
    path = os.path.join(repo, rel)
    try:
        raw = open(path, "rb").read()
    except OSError:
        return [], "none"
    parser = parsers.get(lang)
    if parser:
        try:
            return imports_via_ts(parser, raw, lang), "tree-sitter"
        except Exception:
            pass
    text = raw.decode("utf8", "ignore")
    if lang == "python":
        hits = IMPORT_RE["python"].findall(text)
        return [(a or b, "runtime") for a, b in hits], "regex"
    if lang in ("typescript", "tsx", "javascript"):
        hits = IMPORT_RE["js"].findall(text)
        return [(next(filter(None, h), ""), "runtime") for h in hits], "regex"
    return [(s, "runtime") for s in IMPORT_RE["generic"].findall(text)], "regex"


# ── Разрешение импорта в файл репозитория ───────────────────────────────────


def build_index(files: list[str]) -> dict[str, list[str]]:
    """module-path -> файлы. Ключи и в точечной, и в слэшевой форме."""
    idx = collections.defaultdict(list)
    for f in files:
        stem, _ = os.path.splitext(f)
        idx[stem].append(f)
        idx[stem.replace("/", ".")].append(f)
        if stem.endswith("/index") or stem.endswith("/__init__"):
            pkg = stem.rsplit("/", 1)[0]
            idx[pkg].append(f)
            idx[pkg.replace("/", ".")].append(f)
    return idx


def resolve(spec: str, src_file: str, index, roots) -> str | None:
    """Импорт -> файл репозитория, либо None (внешняя зависимость)."""
    if not spec:
        return None
    cand = []
    if spec.startswith("."):
        base = os.path.dirname(src_file)
        if re.match(r"^\.+/", spec) or spec.startswith("./") or spec.startswith("../"):
            cand.append(os.path.normpath(os.path.join(base, spec)))
        else:  # питоновский относительный: ..pkg.mod
            up = len(spec) - len(spec.lstrip("."))
            tail = spec[up:].replace(".", "/")
            b = base
            for _ in range(up - 1):
                b = os.path.dirname(b)
            cand.append(os.path.normpath(os.path.join(b, tail)) if tail else b)
    else:
        s = (
            spec.lstrip("@").replace(".", "/")
            if "." in spec and "/" not in spec
            else spec
        )
        cand.append(s.lstrip("@"))
        cand.append(spec)
        for r in roots:  # алиасы вида "@/shared/api"
            cand.append(f"{r}/{s.lstrip('@/')}")
    for c in cand:
        c = c.strip("/")
        for key in (c, c.replace("/", ".")):
            if key in index:
                return index[key][0]
    return None


# ── Циклы: Тарьян, итеративно ───────────────────────────────────────────────


def tarjan(graph: dict[str, set[str]]) -> list[list[str]]:
    """Сильно связные компоненты. Итеративный — рекурсия на большом репо
    пробивает лимит стека."""
    index, low, on_stack = {}, {}, {}
    stack, result, counter = [], [], [0]

    for root in graph:
        if root in index:
            continue
        work = [(root, iter(graph.get(root, ())))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack[root] = True
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    on_stack[nxt] = True
                    work.append((nxt, iter(graph.get(nxt, ()))))
                    advanced = True
                    break
                if on_stack.get(nxt):
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    result.append(sorted(comp))
    return result


def self_loops(graph):
    return [n for n, deps in graph.items() if n in deps]


# ── Сборка ──────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="Структурная ось zodchiy")
    ap.add_argument("repo")
    ap.add_argument("--roots", default=None, help="корни алиасов через запятую")
    ap.add_argument("--layer-depth", type=int, default=1)
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    files = collect_files(repo)
    if not files:
        sys.exit(
            json.dumps(
                {"error": "не найдено файлов с кодом", "repo": repo}, ensure_ascii=False
            )
        )

    parsers = Parsers()
    index = build_index(files)
    roots = (
        [r.strip() for r in args.roots.split(",")]
        if args.roots
        else sorted({f.split("/")[0] for f in files if "/" in f})[:6]
    )

    # Два графа. `graph` — рантайм, по нему считаются циклы и веса: только он
    # описывает то, что реально исполняется. `graph_all` — с типовыми рёбрами,
    # по нему видно связность на уровне исходника.
    graph: dict[str, set[str]] = {f: set() for f in files}
    graph_all: dict[str, set[str]] = {f: set() for f in files}
    type_edges: list[dict] = []
    external = collections.Counter()
    metrics, backends = {}, collections.Counter()

    for f in files:
        lang = LANGS[os.path.splitext(f)[1]]
        specs, backend = extract_imports(repo, f, lang, parsers)
        backends[backend] += 1
        for s, kind in specs:
            tgt = resolve(s, f, index, roots)
            if tgt and tgt != f:
                graph_all[f].add(tgt)
                if kind == "runtime":
                    graph[f].add(tgt)
                else:
                    type_edges.append({"from": f, "to": tgt})
            elif not tgt and s and not s.startswith("."):
                external[s.split("/")[0].lstrip("@")] += 1

        parser = parsers.get(lang)
        try:
            raw = open(os.path.join(repo, f), "rb").read()
            loc = raw.count(b"\n") + 1
            if parser:
                m = complexity_via_ts(parser, raw)
            else:
                m = {
                    "cyclomatic_total": -1,
                    "cyclomatic_per_function_max": -1,
                    "worst_function_line": 0,
                    "max_nesting": -1,
                    "functions": -1,
                }
            metrics[f] = {"loc": loc, **m}
        except OSError:
            continue

    fan_out = {f: len(d) for f, d in graph.items()}
    fan_in: collections.Counter = collections.Counter()
    for f, deps in graph.items():
        for d in deps:
            fan_in[d] += 1

    # Barrel — файл, который в основном пере-экспортирует чужое. Импорт пакета
    # означает достижимость его содержимого, и для сравнения со сцеплением
    # ребро надо вести до реального модуля, а не до реэкспортёра.
    barrels = {
        f
        for f in files
        if os.path.basename(f)
        in ("__init__.py", "index.ts", "index.js", "index.tsx", "mod.rs")
        and graph_all.get(f)
    }
    through_barrels: dict[str, set[str]] = {}
    for f, deps in graph_all.items():
        reach = set()
        for d in deps:
            reach.add(d)
            if d in barrels:
                reach |= {x for x in graph_all.get(d, ()) if x != f}
        through_barrels[f] = reach

    cycles = tarjan(graph)
    # Цикл, который держится только на типовом ребре, — не находка: он и заведён,
    # чтобы разорвать рантайм-зависимость. Считается отдельно и так и называется.
    cycles_type_only = [
        c for c in tarjan(graph_all) if not any(set(c) == set(r) for r in cycles)
    ]
    layers = collections.Counter(
        "/".join([p for p in f.split("/") if p][: args.layer_depth]) for f in files
    )

    def top(counter, n=20):
        return [{"file": k, "value": v} for k, v in counter.most_common(n) if v]

    # Место числа в распределении этого репо рядом с самим числом: порог «fan-in
    # больше N» неперенос­им между проектами, «верхний процент» — переносим.
    fan_in_rank = pct_rank([fan_in[f] for f in files])
    cc_rank = pct_rank(
        [m["cyclomatic_per_function_max"] for m in metrics.values() if m["cyclomatic_per_function_max"] > 0]
    )
    loc_rank = pct_rank([m["loc"] for m in metrics.values()])

    hubs = sorted(
        (
            {
                "file": f,
                "fan_in": fan_in[f],
                "fan_out": fan_out[f],
                "total": fan_in[f] + fan_out[f],
                "loc": metrics.get(f, {}).get("loc", 0),
                "cyclomatic_per_function_max": metrics.get(f, {}).get(
                    "cyclomatic_per_function_max", -1
                ),
                "fan_in_pct": fan_in_rank(fan_in[f]),
                "loc_pct": loc_rank(metrics.get(f, {}).get("loc", 0)),
            }
            for f in files
        ),
        key=lambda r: -r["total"],
    )[:25]

    complex_files = sorted(
        (
            dict(
                file=f,
                **m,
                cyclomatic_pct=cc_rank(m["cyclomatic_per_function_max"]),
                loc_pct=loc_rank(m["loc"]),
            )
            for f, m in metrics.items()
            if m["cyclomatic_per_function_max"] > 0
        ),
        key=lambda r: -r["cyclomatic_per_function_max"],
    )[:25]

    print(
        json.dumps(
            {
                "axis": "structure",
                "parser": {
                    "tree_sitter": parsers.ok,
                    "backends": dict(backends),
                    "grammars_missing": sorted(parsers.missing),
                },
                "files": len(files),
                "edges": sum(len(d) for d in graph.values()),
                "layers": dict(layers.most_common(20)),
                "package_roots": roots,
                "cycles": [{"size": len(c), "members": c[:12]} for c in cycles],
                "cycles_type_only": [
                    {"size": len(c), "members": c[:12]} for c in cycles_type_only
                ],
                "type_only_edges": len(type_edges),
                # Список смежности целиком. Нужен судящей фазе, чтобы отличить
                # R2 от R3: два файла меняются вместе И связаны импортом — это
                # честная зависимость; меняются вместе БЕЗ ребра — одно решение
                # разложено в двух местах, и это другой диагноз с другим лечением.
                # Без графа в JSON эту проверку пришлось бы делать грепом, то есть
                # на глаз.
                "adjacency": {f: sorted(d) for f, d in graph.items() if d},
                # Тот же граф, но barrel-файлы (`__init__.py`, `index.ts`) пройдены
                # насквозь. Без этого `from pkg import User` даёт ребро в `__init__`,
                # а не в `user.py`, и пара (user.py, user_repository.py) выглядит
                # «сцеплены, но не связаны» — то есть R3 вместо R2. Проверено на
                # repo-A: ровно так флагманская находка получала неверный диагноз.
                "adjacency_through_barrels": {
                    f: sorted(d) for f, d in through_barrels.items() if d
                },
                "barrels": sorted(barrels),
                "self_loops": self_loops(graph),
                "hubs": hubs,
                "fan_in_top": top(fan_in),
                "complex_files": complex_files,
                "external_deps_top": [
                    {"name": k, "imports": v} for k, v in external.most_common(25)
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
