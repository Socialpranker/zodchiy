#!/usr/bin/env python3
"""Линтер-ось замера: ruff по трём группам и реестр найденных инструментов.

    python3 lint.py <repo> [--measure measure.json] [--semgrep]

Отдельный модуль, а не кусок `measure.py`: тот же счёт нужен `slice` и `trial`,
а замер запускать ради них заново незачем.

Ветвления и вложенность у ruff не берём — их считает tree-sitter в
`structure.py`; двойной счёт дал бы ложную сходимость осей.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys

from clones import find_clones
from common import pct_rank
from structure import collect_files

# группа → префиксы кодов ruff. Порядок и состав — контракт с measure_schema.md.
RULES: dict[str, list[str]] = {
    "simplicity": ["SIM", "C4", "FURB"],
    "width": ["PLR0913", "PLR0915"],
    "perf": ["PERF"],
}
CHUNK = 400  # файлов на один вызов: путь с кириллицей ×1000 упирается в ARG_MAX
SEMGREP_TIMEOUT = 300


def _which(name: str) -> str | None:
    # PATH читается при каждом вызове: тесты подменяют его, чтобы убрать ruff.
    return shutil.which(name)


def _version(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = proc.stdout.split()
    return words[-1] if proc.returncode == 0 and words else None


# semgrep не опрашиваем: `semgrep --version` стоит 35-60 с (проверка обновлений
# по сети), а платить этим на каждом замере ради строки версии нельзя.
NO_VERSION_PROBE = ("semgrep",)


def detect_tools() -> dict[str, dict]:
    """Что из внешнего есть на машине. Отсутствие — слепая зона отчёта."""
    tools: dict[str, dict] = {}
    for name in ("git", "ruff", "semgrep"):
        path = _which(name)
        probe = path is not None and name not in NO_VERSION_PROBE
        tools[name] = {
            "available": path is not None,
            "version": _version([path, "--version"]) if probe else None,
        }
    try:
        ts_version = importlib.metadata.version("tree-sitter")
    except importlib.metadata.PackageNotFoundError:
        ts_version = None
    tools["tree_sitter"] = {"available": ts_version is not None, "version": ts_version}
    return {k: tools[k] for k in ("git", "tree_sitter", "ruff", "semgrep")}


def _group_of(code: str) -> str | None:
    for group, prefixes in RULES.items():
        if code.startswith(tuple(prefixes)):
            return group
    return None


def _ruff_counts(repo: str, files: list[str]) -> dict[str, dict[str, int]] | str:
    """Счётчики по файлам или строка-причина сбоя."""
    select = ",".join(p for prefixes in RULES.values() for p in prefixes)
    real = {os.path.realpath(os.path.join(repo, f)): f for f in files}
    counts = {f: dict.fromkeys(RULES, 0) for f in files}
    for i in range(0, len(files), CHUNK):
        proc = subprocess.run(
            [
                "ruff", "check", "--isolated", "--select", select,
                "--output-format", "json", "--exit-zero", "--no-cache",
                *files[i : i + CHUNK],
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=900,
        )
        try:
            rows = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return f"ruff failed: {(proc.stderr or proc.stdout).strip()[-300:]}"
        if proc.returncode != 0:
            return f"ruff failed: {proc.stderr.strip()[-300:]}"
        for row in rows:
            rel = real.get(os.path.realpath(row["filename"]))
            group = _group_of(row.get("code") or "")
            if rel is not None and group is not None:
                counts[rel][group] += 1
    return counts


def _semgrep_perf(repo: str, files: list[str]) -> dict:
    """Только performance-правила p/python. Сеть нужна для загрузки набора."""
    if _which("semgrep") is None:
        return {"available": False, "reason": "semgrep not found"}
    try:
        proc = subprocess.run(
            [
                "semgrep", "--config", "p/python", "--json", "--quiet",
                "--metrics=off", *files,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=SEMGREP_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": f"semgrep timeout {SEMGREP_TIMEOUT}s"}
    try:
        if proc.returncode not in (0, 1):
            raise ValueError
        results = json.loads(proc.stdout)["results"]
    except (ValueError, KeyError):
        tail = (proc.stderr or proc.stdout).strip()[-300:]
        return {"available": False, "reason": f"semgrep failed: {tail}"}
    per_file: dict[str, int] = {}
    for r in results:
        category = r.get("extra", {}).get("metadata", {}).get("category")
        if category == "performance":
            per_file[r["path"]] = per_file.get(r["path"], 0) + 1
    return {"available": True, "reason": None, "files": per_file}


def run_lint(repo: str, files: list[str], hot: set[str], semgrep: bool = False) -> dict:
    """Счётчики групп ruff по .py-файлам из `files` + перцентили внутри репо.

    Ключ `clones` считается всегда — см. `clones.find_clones`."""
    out: dict = {
        "available": False,
        "tool_version": None,
        "files_checked": 0,
        "rules": {g: list(p) for g, p in RULES.items()},
        "files": [],
        "reason": None,
        # клоны считает tree-sitter, не ruff, и покрывают они .ts/.tsx/.js:
        # отсутствие ruff им не помеха
        "clones": find_clones(repo, files),
    }
    if _which("ruff") is None:
        out["reason"] = "ruff not found"
        return out
    out["tool_version"] = _version(["ruff", "--version"])

    py = sorted(f for f in files if f.endswith(".py"))
    if not py:
        # Ноль файлов — не «чисто»: проверять было нечего.
        out["available"] = True
        out["note"] = "нечего проверять: в репозитории нет Python-файлов"
        return out

    counts = _ruff_counts(repo, py)
    if isinstance(counts, str):
        out["reason"] = counts
        return out

    ranks = {g: pct_rank([c[g] for c in counts.values()]) for g in RULES}
    out["available"] = True
    out["files_checked"] = len(py)
    out["files"] = [
        {
            "file": f,
            **counts[f],
            **{f"{g}_pct": ranks[g](counts[f][g]) for g in RULES},
            "hot": f in hot,
        }
        for f in py
    ]
    if semgrep:
        out["perf_semgrep"] = _semgrep_perf(repo, py)
    return out


def main():
    ap = argparse.ArgumentParser(description="lint-ось — zodchiy (отладка)")
    ap.add_argument("repo")
    ap.add_argument("--measure", default=None, help="measure.json: горячие файлы")
    ap.add_argument("--semgrep", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    hot: set[str] = set()
    if args.measure:
        with open(args.measure, encoding="utf-8") as fh:
            hot = {h["file"] for h in json.load(fh)["behavior"].get("hotspots", [])}
    res = run_lint(repo, collect_files(repo), hot, semgrep=args.semgrep)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["available"] else 1


if __name__ == "__main__":
    sys.exit(main())
