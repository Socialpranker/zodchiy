#!/usr/bin/env python3
"""Писарь режима `refactor`: файлы прогона пишет код, а не модель.

    scribe.py --nonce N --run-dir R

Сценарий отдаёт агенту-писарю содержимое файлов в его промпте и просит одну
команду — эту. Перепечатывать 60 КБ JSON модели нельзя: haiku пересказывает
вместо копирования. Поэтому команда сама достаёт промпт вызвавшего агента из
сохранённой расшифровки субагента и разбирает блоки оттуда:

    ZODCHIY-SCRIBE-NONCE: <N>
    <<<ZODCHIY decisions.json
    {...}
    >>>ZODCHIY

Харнесс отступом смещает каждую строку вычисленного промпта и дописывает
заголовок, поэтому маркеры ищутся по строке без отступа, а не по началу строки.
Печатает одну JSON-строку {schema_errors, decisions_digest, refutation_digest,
csv_ids}; не нашла или не разобрала — {"error": ...} и код выхода 1.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys

import ledger

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(SKILL, "schema", "decisions.schema.json")

NONCE_PREFIX = "ZODCHIY-SCRIBE-NONCE: "
BLOCK_OPEN = "<<<ZODCHIY "
BLOCK_CLOSE = ">>>ZODCHIY"
# refutation.json и decisions.json — JSON дословно; в findings.run.csv — список id строк findings.csv
BLOCKS = ("refutation.json", "decisions.json", "findings.run.csv")


class ScribeError(Exception):
    pass


def config_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def fnv(obj) -> str:
    """FNV-1a 64 по UTF-8 канонического JSON: ключи по алфавиту, без пробелов, не-ASCII как есть.
    Ту же функцию считает сценарий на JS, поэтому подмену значения не пропустит."""
    h = 0xCBF29CE484222325
    for b in json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8"):
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def _prompt_of(first_line: str) -> str | None:
    try:
        content = json.loads(first_line)["message"]["content"]
    except (ValueError, KeyError, TypeError):
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b["text"]
            for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )
    return None


def _blocks(prompt: str, nonce: str) -> dict[str, str] | None:
    """Блоки промпта с нужным nonce, иначе None. Строки режем только по \\n:
    U+2028 и подобные — обычные символы внутри JSON-строки, не перевод строки."""
    lines = [ln.strip(" \t\r") for ln in prompt.split("\n")]
    if NONCE_PREFIX + nonce not in lines:
        return None
    out: dict[str, str] = {}
    name, buf = None, []
    for ln in lines:
        if name is None:
            if ln.startswith(BLOCK_OPEN):
                name, buf = ln[len(BLOCK_OPEN) :], []
        elif ln == BLOCK_CLOSE:
            out[name] = "\n".join(buf)
            name = None
        else:
            buf.append(ln)
    return out


def find_payload(nonce: str, config: str) -> dict[str, str]:
    pattern = os.path.join(
        config, "projects", "*", "*", "subagents", "workflows", "*", "agent-*.jsonl"
    )
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    found: list[tuple[str, dict[str, str]]] = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as fh:
                first = fh.readline()
        except (OSError, UnicodeDecodeError):
            continue
        if nonce not in first:  # дёшево отсеять, не разбирая JSON
            continue
        prompt = _prompt_of(first)
        blocks = _blocks(prompt, nonce) if prompt is not None else None
        if blocks is not None:
            found.append((path, blocks))
    if not found:
        raise ScribeError(f"промпт с nonce {nonce} не найден в {pattern}")
    # повторное выполнение (resume) оставляет несколько расшифровок: расходиться им нельзя
    for path, blocks in found[1:]:
        if blocks != found[0][1]:
            raise ScribeError(
                f"два промпта с nonce {nonce} с разным содержимым: {found[0][0]} и {path}"
            )
    return found[0][1]


def _json_block(blocks: dict[str, str], name: str):
    if name not in blocks:
        raise ScribeError(f"в промпте нет блока {name}")
    try:
        return json.loads(blocks[name])
    except ValueError as e:
        raise ScribeError(f"блок {name} не разбирается как JSON: {e}") from e


def scribe(nonce: str, run_dir: str, config: str | None = None) -> dict:
    blocks = find_payload(nonce, config or config_dir())
    refutation = _json_block(blocks, "refutation.json")
    decisions = _json_block(blocks, "decisions.json")
    ids = _json_block(blocks, "findings.run.csv")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ScribeError("блок findings.run.csv — не список строк-id")

    findings = os.path.join(run_dir, "findings.csv")
    try:
        with open(findings, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            names = reader.fieldnames
            rows = [r for r in reader if r["id"] in ids]
    except (OSError, KeyError) as e:
        raise ScribeError(f"не прочитать {findings}: {e}") from e

    with open(SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    schema_errors = ledger.validate(decisions, schema, schema)

    os.makedirs(run_dir, exist_ok=True)
    for name, text in (
        ("refutation.json", blocks["refutation.json"]),
        ("decisions.json", blocks["decisions.json"]),
    ):
        with open(os.path.join(run_dir, name), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    with open(
        os.path.join(run_dir, "findings.run.csv"), "w", newline="", encoding="utf-8"
    ) as fh:
        w = csv.DictWriter(fh, fieldnames=names)
        w.writeheader()
        w.writerows(rows)

    return {
        "schema_errors": [str(e) for e in schema_errors],
        "decisions_digest": fnv(decisions),
        "refutation_digest": fnv(refutation),
        "csv_ids": [r["id"] for r in rows],
    }


def main():
    ap = argparse.ArgumentParser(
        description="писарь refactor: файлы прогона из сохранённого промпта — zodchiy"
    )
    ap.add_argument("--nonce", required=True)
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    try:
        result = scribe(args.nonce, args.run_dir)
    except ScribeError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    main()
