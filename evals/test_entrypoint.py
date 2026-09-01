#!/usr/bin/env python3
"""Юнит-слой единой точки входа `zodchiy.py`.

Точка входа делегирует в main() скриптов, а не переобъявляет их флаги —
проверяется именно это: что маршрут ведёт куда обещал, что коды выхода
проходят наружу и что таблица маршрутов не отстала от подкоманд ledger.
Дрейф здесь тихий: подкоманда появится, а харнесс о ней не узнает.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)

import zodchiy  # noqa: E402

ENTRY = os.path.join(SKILL, "zodchiy.py")


def run(*args):
    return subprocess.run(
        [sys.executable, ENTRY, *args], capture_output=True, text=True, timeout=120
    )


class TestRouteTable(unittest.TestCase):
    def test_covers_every_ledger_subcommand(self):
        with open(os.path.join(SKILL, "scripts", "ledger.py"), encoding="utf-8") as fh:
            src = fh.read()
        declared = set(re.findall(r'add_parser\(\s*"([\w-]+)"', src))
        routed = {
            prefix[0]
            for module, prefix, _ in zodchiy.ROUTES.values()
            if module == "ledger" and prefix
        }
        self.assertEqual(
            declared - routed, set(), "подкоманда ledger не заведена в zodchiy.py"
        )
        self.assertEqual(
            routed - declared, set(), "маршрут ведёт в несуществующую подкоманду"
        )

    def test_every_route_resolves_to_a_main(self):
        sys.path.insert(0, os.path.join(SKILL, "scripts"))
        import importlib

        for cmd, (module, _, doc) in zodchiy.ROUTES.items():
            with self.subTest(cmd=cmd):
                mod = importlib.import_module(module)
                self.assertTrue(callable(getattr(mod, "main", None)))
                self.assertTrue(doc.strip(), "маршрут без описания не попадёт в usage")


class TestUsage(unittest.TestCase):
    def test_no_args_is_usage_and_exit_2(self):
        r = run()
        self.assertEqual(r.returncode, 2)
        self.assertIn("zodchiy", r.stderr)
        self.assertEqual(r.stdout, "", "usage без команды не должен идти в stdout")

    def test_unknown_command_exit_2(self):
        r = run("bogus")
        self.assertEqual(r.returncode, 2)
        self.assertIn("неизвестная команда", r.stderr)

    def test_help_lists_every_command(self):
        r = run("--help")
        self.assertEqual(r.returncode, 0)
        for cmd in zodchiy.ROUTES:
            self.assertIn(cmd, r.stdout)


class TestDelegation(unittest.TestCase):
    def test_measure_help_comes_from_measure_py(self):
        r = run("measure", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage: zodchiy measure", r.stdout)
        self.assertIn("--layer-depth", r.stdout)

    def test_subcommand_prog_is_two_words(self):
        r = run("gate", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("usage: zodchiy gate", r.stdout)
        self.assertIn("--baseline", r.stdout)

    def test_argparse_error_exit_2_through_route(self):
        r = run("snapshot")  # обязательный позиционный не передан
        self.assertEqual(r.returncode, 2)

    def test_script_exit_1_reaches_shell(self):
        # ledger.refute падает на неполном вердикте; код выхода обязан пройти
        # сквозь делегирование, иначе гейт в CI станет всегда зелёным.
        r = run("refute", "--json", "{}")
        self.assertEqual(r.returncode, 1)
        self.assertIn("вердикт линзы неполон", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
