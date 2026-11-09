#!/usr/bin/env python3
"""Срез `measure.json` под одну находку — то, что получает агент режима `refactor`.

    slice.py --id F1 --measure measure.json --findings findings.csv --repo . [--out slice.json]

Целый `measure.json` — 300–500 КБ, агенту на одну находку нужна доля процента.
Срез ≤ 8 КБ: строки по файлам находки, а не весь замер. Всё, что не влезло,
обрезается с числом отброшенного в соседнем поле `<имя>_dropped` — тихого
усечения нет. Пути в срезе — как в замере, относительно корня репозитория.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from behavior import layer_of
from ledger import FILE_IN_SOURCE, read_findings
from payback import PAYBACK_MAX_QUARTERS, pain_per_quarter

BUDGET = 8192
NEIGHBORS_MAX = 20
COUPLING_MAX = 10
LAYER_DEPTHS = (1, 2, 3)  # глубина слоя в measure.json не хранится — сверяем по всем
FINDING_FIELD_CUT = 400

# Порядок отбрасывания при нехватке бюджета: сначала самое периферийное.
SHRINK_ORDER = (
    "neighbors",
    "top_crossings",
    "temporal_coupling",
    "lint_files",
    "hubs",
    "complex_files",
    "unstable_files",
    "hotspots",
    "blocked_metrics",
    "files",
)


def finding_files(finding: dict) -> list[str]:
    """Файлы находки — из `[file=…]` в `source`, в порядке появления, без повторов."""
    out: list[str] = []
    for f in FILE_IN_SOURCE.findall(finding.get("source") or ""):
        if f not in out:
            out.append(f)
    return out


def _rows(items: list, files: list[str]) -> list[dict]:
    by_file = {r["file"]: r for r in items if isinstance(r, dict) and "file" in r}
    return [by_file[f] for f in files if f in by_file]


def _neighbors(adjacency: dict, files: list[str], hot: dict[str, int]) -> list[str]:
    """Соседи на один шаг в обе стороны, самые правимые — первыми."""
    own = set(files)
    found: set[str] = set()
    for f in files:
        found.update(adjacency.get(f, []))
    for src, deps in adjacency.items():
        if own & set(deps):
            found.add(src)
    return sorted(found - own, key=lambda n: (-hot.get(n, 0), n))


def serialize(doc: dict) -> str:
    """Единственная сериализация среза: и файл, и stdout получают её + перевод строки."""
    return json.dumps(doc, ensure_ascii=False) + "\n"


def _size(doc: dict) -> int:
    # Считаем ровно то, что уйдёт в файл, вместе с переводом строки: бюджет — про артефакт.
    # Счётчик `<имя>_dropped` растёт до проверки в цикле `_fit`, так что последний инкремент учтён.
    return len(serialize(doc).encode("utf-8"))


def _fit(doc: dict) -> dict:
    """Ужать до BUDGET. Списки режутся с хвоста; счётчик отброшенного пишется всегда."""
    for name in SHRINK_ORDER:
        while _size(doc) > BUDGET and doc[name]:
            doc[name].pop()
            doc[f"{name}_dropped"] += 1
    if _size(doc) > BUDGET:
        # Списки кончились, а строка находки всё ещё велика — режем её длинные поля;
        # какие и сколько символов отброшено, записываем.
        cut = {}
        for k, v in doc["finding"].items():
            if isinstance(v, str) and len(v) > FINDING_FIELD_CUT:
                doc["finding"][k] = v[:FINDING_FIELD_CUT]
                cut[k] = len(v) - FINDING_FIELD_CUT
        doc["finding_fields_cut"] = cut
    if _size(doc) > BUDGET:
        raise RuntimeError(f"срез {_size(doc)} байт > {BUDGET} после всех усечений")
    return doc


def build_slice(measure: dict, finding: dict, repo: str) -> dict:
    beh = measure.get("behavior", {})
    st = measure.get("structure", {})
    files = finding_files(finding)
    own = set(files)

    hot = {r["file"]: r.get("edits", 0) for r in beh.get("hotspots", [])}
    stab = beh.get("stability", {}).get("unstable_files", [])
    lint_files = measure.get("lint", {}).get("files", [])

    pairs = [p for p in beh.get("temporal_coupling", []) if p["a"] in own or p["b"] in own]

    roots = beh.get("package_roots", [])
    layers = {layer_of(f, roots, d) for f in files for d in LAYER_DEPTHS}
    crossings = [
        c
        for c in beh.get("containment", {}).get("top_crossings", [])
        if layers & set(c["layers"].split(" + "))
    ]

    neighbors = _neighbors(st.get("adjacency_through_barrels", {}), files, hot)

    try:
        pain = pain_per_quarter(repo, files)
    except SystemExit as e:  # read_log выходит через sys.exit — срез из-за этого не роняем
        pain = {"available": False, "reason": str(e)}

    doc = {
        "finding": {k: v for k, v in finding.items() if v not in (None, "")},
        "files": files,
        "hotspots": _rows(beh.get("hotspots", []), files),
        "unstable_files": _rows(stab, files),
        "complex_files": _rows(st.get("complex_files", []), files),
        "hubs": _rows(st.get("hubs", []), files),
        "lint_files": _rows(lint_files, files),
        "temporal_coupling": pairs[:COUPLING_MAX],
        "neighbors": neighbors[:NEIGHBORS_MAX],
        "top_crossings": crossings,
        "thresholds": beh.get("thresholds", {}),
        "blocked_metrics": list(measure.get("calibration", {}).get("blocked_metrics", [])),
        "ceiling": measure.get("confidence", {}).get("ceiling"),
        "pain": pain,
        "payback_max_quarters": PAYBACK_MAX_QUARTERS,
    }
    for name in SHRINK_ORDER:
        doc[f"{name}_dropped"] = 0
    doc["temporal_coupling_dropped"] = len(pairs) - len(doc["temporal_coupling"])
    doc["neighbors_dropped"] = len(neighbors) - len(doc["neighbors"])
    return _fit(doc)


def main():
    ap = argparse.ArgumentParser(description="срез measure.json под находку — zodchiy")
    ap.add_argument("--id", required=True)
    ap.add_argument("--measure", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.measure, encoding="utf-8") as fh:
        measure = json.load(fh)
    row = next((r for r in read_findings(args.findings) if r["id"] == args.id), None)
    if row is None:
        sys.exit(f"находки {args.id} нет в {args.findings}")

    # Без отступов: бюджет считается по этой же сериализации, отступы его бы съели.
    text = serialize(build_slice(measure, row, os.path.abspath(args.repo)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
