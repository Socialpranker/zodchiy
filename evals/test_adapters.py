#!/usr/bin/env python3
"""Слой сборки адаптеров: `dist/` не должен жить своей жизнью.

Пять копий доктрины расходятся молча — это и есть проверяемое утверждение.
Поэтому `--check` пересобирает из `SKILL.md` и сверяет байт в байт, а тесты
ниже держат саму сборку: пропавшая секция валит её громко, относительные пути
не утекают в чужой харнесс, TOML остаётся TOML.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import build_adapters as ba  # noqa: E402

BUILDER = os.path.join(SKILL, "scripts", "build_adapters.py")


class TestDistIsInSync(unittest.TestCase):
    def test_check_is_green(self):
        r = subprocess.run(
            [sys.executable, BUILDER, "--check"], capture_output=True, text=True, timeout=120
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

    def test_check_catches_a_hand_edit(self):
        """Правка прямо в dist/ обязана краснеть, иначе адаптеры разъедутся."""
        path = os.path.join(SKILL, "dist", "AGENTS.md")
        with open(path, encoding="utf-8") as fh:
            keep = fh.read()
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("\nправка руками\n")
            r = subprocess.run(
                [sys.executable, BUILDER, "--check"], capture_output=True, text=True, timeout=120
            )
            self.assertEqual(r.returncode, 1)
            self.assertIn("разошёлся", r.stderr)
        finally:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(keep)


class TestSectionExtraction(unittest.TestCase):
    def test_missing_section_fails_loudly(self):
        with self.assertRaises(SystemExit):
            ba.section("# Заголовок\n\nтекст\n", "## Команда")

    def test_section_stops_at_next_same_level(self):
        md = "## A\n\nтело A\n\n### A1\n\nвложено\n\n## B\n\nтело B\n"
        got = ba.section(md, "## A")
        self.assertIn("вложено", got)
        self.assertNotIn("тело B", got)

    def test_steps_are_numbered_from_headings(self):
        md = "### 1. Считать\n\nx\n\n### 2. Понять\n\ny\n"
        self.assertEqual(ba.steps(md), "1. Считать\n2. Понять")


class TestPaths(unittest.TestCase):
    def test_relative_doctrine_paths_become_absolute(self):
        self.assertEqual(
            ba.absolutise("см. `references/risks.md`", "/opt/z"),
            "см. `/opt/z/references/risks.md`",
        )

    def test_url_is_not_rewritten(self):
        url = "<https://example.com/docs/references/risks.md>"
        self.assertEqual(ba.absolutise(url, "/opt/z"), url)

    def test_no_relative_path_survives_in_adapter(self):
        files = ba.build(ba.DEFAULT_HOME)
        leaks = []
        # README.md — про сам репозиторий скилла и читается из него: там
        # относительный путь как раз верен. Проверяются адаптеры, которые
        # уедут в чужой харнесс и будут читаться откуда угодно.
        for name, text in files.items():
            if name == "README.md":
                continue
            for m in re.finditer(r"(?<![\w/~.])(references|scripts|evals)/[\w.-]+", text):
                leaks.append(f"{name}: {m.group(0)}")
        self.assertEqual(leaks, [], "относительный путь в адаптере не разрешится")


class TestTargets(unittest.TestCase):
    def setUp(self):
        self.files = ba.build(ba.DEFAULT_HOME)

    def test_three_names_one_text(self):
        """Qwen Code и iFlow — форки Gemini CLI: различие только в имени."""
        bodies = {self.files[n] for n in ("GEMINI.md", "QWEN.md", "IFLOW.md", "AGENTS.md")}
        self.assertEqual(len(bodies), 1)

    def test_router_fits_codex_budget(self):
        # project_doc_max_bytes = 32 KiB на ВЕСЬ набор проектных документов,
        # поэтому адаптер обязан оставить место правилам самого проекта.
        size = len(self.files["AGENTS.md"].encode("utf-8"))
        self.assertLess(size, 12 * 1024, f"маршрутизатор раздулся до {size} байт")

    def test_commands_are_valid_toml(self):
        for rel in ba.COMMAND_TARGETS:
            with self.subTest(rel=rel):
                d = tomllib.loads(self.files[rel])
                self.assertTrue(d["description"].strip())
                self.assertIn("{{args}}", d["prompt"])
                self.assertIn("mode=sequential", d["prompt"])

    def test_readme_lists_every_target(self):
        readme = self.files["README.md"]
        for name in list(ba.DOCTRINE_TARGETS) + list(ba.COMMAND_TARGETS):
            self.assertIn(name, readme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
