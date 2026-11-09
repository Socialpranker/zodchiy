#!/usr/bin/env python3
"""Статический слой сценария Workflow `workflows/refactor.js`.

Сам сценарий без модели не запустить, поэтому проверяется текст: фазы `meta`
совпадают с вызовами `phase()`, у каждой роли модель из разрешённого набора и
effort по таблице спеки §1, нет недетерминизма (Workflow бросает на
`Date.now`/`Math.random`), отсечка `top` не молчит. Плюс разбор синтаксиса
самим `node`, если он есть.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPT = os.path.join(SKILL, "workflows", "refactor.js")

ALLOWED_MODELS = {"claude-opus-5-5", "claude-haiku-5-5"}
OPUS, HAIKU = "claude-opus-5-5", "claude-haiku-5-5"

# Спека §1 (+ поправка 10 из §5): роль -> (модель, effort)
EXPECTED = {
    "measurer": (HAIKU, "low"),
    "slicer": (HAIKU, "low"),
    "analyst": (OPUS, "medium"),
    "criticL1": (OPUS, "high"),
    "criticL2": (OPUS, "high"),
    "criticL3": (OPUS, "medium"),
    "designer": (OPUS, "medium"),
    "lead": (OPUS, "high"),
    "trial": (HAIKU, "low"),
    "scribe": (HAIKU, "low"),
    "reporter": (HAIKU, "low"),
}


def source() -> str:
    with open(SCRIPT, encoding="utf-8") as fh:
        return fh.read()


def meta_block(text: str) -> str:
    m = re.search(r"^export const meta = \{.*?^\};", text, re.S | re.M)
    assert m, "нет `export const meta = {...};` в начале сценария"
    return m.group(0)


def roles(text: str) -> dict[str, str]:
    """Строки таблицы `const ROLES = { имя: {…}, … };` — по одной роли на строку."""
    m = re.search(r"^const ROLES = \{\n(.*?)^\};", text, re.S | re.M)
    assert m, "нет таблицы `const ROLES = {…};`"
    out = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        r = re.match(r"(\w+):\s*\{(.*)\},?\s*(?://.*)?$", line)
        assert r, f"строка ROLES не разобрана: {line!r}"
        out[r.group(1)] = r.group(2)
    return out


class WorkflowStatic(unittest.TestCase):
    def setUp(self):
        self.text = source()

    def test_starts_with_meta(self):
        self.assertTrue(self.text.lstrip().startswith("export const meta"))

    def test_meta_is_pure_literal(self):
        block = meta_block(self.text)
        self.assertNotIn("${", block)
        self.assertNotIn("`", block)

    def test_phases_match_meta(self):
        titles = re.findall(r"title:\s*'([^']+)'", meta_block(self.text))
        used = set(re.findall(r"\bphase\('([^']+)'\)", self.text))
        used |= set(re.findall(r"\bphase:\s*'([^']+)'", self.text))
        self.assertTrue(titles)
        self.assertEqual(len(titles), len(set(titles)), "повтор заголовка фазы")
        self.assertEqual(set(titles), used)
        self.assertEqual(
            titles[:2], ["Замер", "Анализ"], "порядок фаз: замер → анализ → …"
        )
        self.assertEqual(titles[-1], "Отчёт")

    def test_agent_called_once_via_role_table(self):
        # единственный вызов `agent(` — в обёртке `ask`, она же считает агентов
        calls = re.findall(r"\bagent\(", self.text)
        self.assertEqual(len(calls), 1, "agent( только в обёртке ask")
        self.assertRegex(self.text, r"agent\(prompt,\s*\{[^}]*\.\.\.ROLES\[role\]")
        self.assertRegex(self.text, r"agents\s*\+=\s*1|agents\+\+")

    def test_every_ask_uses_known_role(self):
        table = roles(self.text)
        asked = set(re.findall(r"\bask\(\s*'(\w+)'", self.text))
        self.assertTrue(asked)
        self.assertLessEqual(asked, set(table))

    def test_role_models_and_efforts(self):
        table = roles(self.text)
        for role, (model, effort) in EXPECTED.items():
            self.assertIn(role, table, role)
            self.assertIn(f"model: '{model}'", table[role], role)
            self.assertIn(f"effort: '{effort}'", table[role], role)
        # исполнитель — свой тип агента, без model
        self.assertIn("executor", table)
        self.assertIn("agentType: 'sonnet-implementer'", table["executor"])
        self.assertNotIn("model:", table["executor"])
        self.assertEqual(set(table), set(EXPECTED) | {"executor"})

    def test_l1_l2_lead_are_high(self):
        table = roles(self.text)
        for role in ("criticL1", "criticL2", "lead"):
            self.assertIn("effort: 'high'", table[role], role)

    def test_only_explicit_models(self):
        models = set(re.findall(r"claude-[A-Za-z0-9.\-]+", self.text))
        self.assertLessEqual(models, ALLOWED_MODELS)
        self.assertNotIn("fable", self.text.lower())
        for alias in ("model: 'opus'", "model: 'sonnet'", "model: 'haiku'"):
            self.assertNotIn(alias, self.text)
        self.assertNotIn("isolation", self.text)

    def test_no_nondeterminism(self):
        self.assertNotRegex(self.text, r"Date\.now\s*\(")
        self.assertNotRegex(self.text, r"Math\.random\s*\(")
        self.assertNotRegex(self.text, r"new Date\s*\(\s*\)")

    def test_top_cut_is_logged_with_dropped_count(self):
        self.assertRegex(self.text, r"log\(`[^`]*отброшен[^`]*`\)")
        self.assertRegex(self.text, r"slice\(0,\s*top\)")

    def test_characters(self):
        for c in (
            "удали, а не абстрагируй",
            "минимальный интерфейс",
            "под реального частого вызывающего",
        ):
            self.assertIn(c, self.text)
        self.assertNotRegex(self.text, r"(?i)максимальн\w+\s+гибкост")

    def test_no_forbidden_writes_to_project_docs(self):
        self.assertNotIn("docs/superpowers", self.text)

    def test_returns_contract(self):
        for key in ("decisions_path", "report_html", "plan_md", "branches", "agents"):
            self.assertRegex(self.text, rf"\b{key}\b")

    @unittest.skipUnless(shutil.which("node"), "нет node")
    def test_node_parses_as_async_body(self):
        js = (
            "const fs=require('fs');"
            "const src=fs.readFileSync(process.argv[1],'utf8')"
            ".replace(/^export const meta/m,'const meta');"
            "new (Object.getPrototypeOf(async function(){}).constructor)(src);"
        )
        r = subprocess.run(
            ["node", "-e", js, SCRIPT], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
