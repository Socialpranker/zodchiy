#!/usr/bin/env python3
"""Единая точка входа зодчего.

    python3 ~/.claude/skills/zodchiy/zodchiy.py <команда> [аргументы]

Одна строка вместо четырёх скриптов с разными флагами: харнессу достаточно
знать её, а не раскладку каталога.

Флаги здесь не переобъявляются. Команда делегируется в `main()` нужного
скрипта с подменённым argv, поэтому у каждого флага один источник и
разъезжаться нечему. Цена решения — usage подкоманд печатает argparse
скрипта, а не этот файл; это и есть желаемое.
"""

from __future__ import annotations

import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "scripts")

# команда → (модуль, префикс argv, описание)
ROUTES: dict[str, tuple[str, list[str], str]] = {
    "measure": ("measure", [], "замер обеих осей и калибровка → measure.json"),
    "snapshot": ("ledger", ["snapshot"], "снимок метрик замера → baseline.json"),
    "diff": ("ledger", ["diff"], "сравнить замер с базой"),
    "gate": ("ledger", ["gate"], "то же + exit 1 при регрессии — для CI"),
    "add": ("ledger", ["add"], "дописать находку в findings.csv"),
    "refute": ("ledger", ["refute"], "записать вердикт линзы опровержения"),
    "selfcheck": ("ledger", ["selfcheck"], "проверить находки перед сдачей отчёта"),
    "verify": ("ledger", ["verify"], "сверить прогноз gain со следующим замером"),
    "export": ("ledger", ["export"], "находки машиночитаемо: json по схеме или sarif"),
    "behavior": ("behavior", [], "только поведенческая ось — отладка"),
    "structure": ("structure", [], "только структурная ось — отладка"),
}


def usage(stream) -> None:
    print("zodchiy — архитектурный аудит по трём осям\n", file=stream)
    print("  python3 zodchiy.py <команда> [аргументы]\n", file=stream)
    width = max(len(c) for c in ROUTES)
    for cmd, (_, _, doc) in ROUTES.items():
        print(f"  {cmd.ljust(width)}  {doc}", file=stream)
    print("\n  <команда> --help — флаги конкретной команды", file=stream)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        usage(sys.stderr)
        return 2

    cmd, rest = argv[0], argv[1:]
    if cmd in ("-h", "--help", "help"):
        usage(sys.stdout)
        return 0
    if cmd not in ROUTES:
        print(f"неизвестная команда: {cmd}", file=sys.stderr)
        usage(sys.stderr)
        return 2

    module, prefix, _ = ROUTES[cmd]
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    mod = importlib.import_module(module)
    # argv[0] задаёт prog в usage скрипта: для подкоманд ledger argparse сам
    # допишет имя подкоманды, поэтому там оставляем голое «zodchiy».
    sys.argv = ["zodchiy" if prefix else f"zodchiy {cmd}", *prefix, *rest]
    mod.main()  # SystemExit изнутри проходит наружу — коды выхода сохраняются
    return 0


if __name__ == "__main__":
    sys.exit(main())
