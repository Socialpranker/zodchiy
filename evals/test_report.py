#!/usr/bin/env python3
"""Юнит-слой `report`: HTML, markdown и план фаз из одного набора JSON.

Главный инвариант — число в отчёте пришло из входного JSON, а не из головы
рендерера. `test_no_invented_numbers` проверяет это по-настоящему: вытаскивает
каждое число из текста отчёта и ищет его среди чисел сериализованных входов.
Фикстура нарочно набита разными числами — жёстко зашитая в шаблон цифра
не совпала бы ни с одной.
"""

from __future__ import annotations

import ast
import copy
import glob
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tokenize
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import ledger  # noqa: E402
import report  # noqa: E402

NUM = re.compile(r"\d+(?:[.,]\d+)?")
HEAD = "9d8f9a372cc5"
BASE_SECTIONS = [
    "**Статус:**",
    "**Снимок:**",
    "## Правила для всех фаз",
    "## Что показал замер",
    "## Фаза 1.",
    "## Не входит и почему",
    "## Порядок и цена",
]


def rich(name):
    with open(os.path.join(SKILL, "examples", "rich", name), encoding="utf-8") as fh:
        return json.load(fh)


def make_measure():
    m = rich("measure.json")
    keep = m["behavior"]["hotspots"][:1]
    return {
        "tool": m["tool"],
        "repo": m["repo"],
        "snapshot": m["snapshot"],
        "calibration": m["calibration"],
        "confidence": m["confidence"],
        "behavior": {"hotspots": keep},
        "structure": {
            "files": m["structure"]["files"],
            "layers": m["structure"]["layers"],
            "parser": m["structure"]["parser"],
            "hubs": m["structure"]["hubs"][:1],
            "complex_files": m["structure"]["complex_files"][:1],
            "cycles": m["structure"]["cycles"][:1],
        },
        "tools": {
            "git": {"available": True, "version": "2.51.0"},
            "tree_sitter": {"available": True, "version": "0.25.2"},
            "ruff": {"available": False, "version": None},
            "semgrep": {"available": False, "version": None},
        },
        "lint": {"available": False, "reason": "ruff not found", "files_checked": 0},
    }


def make_findings():
    doc = rich("findings.json")
    doc["findings"] = doc["findings"][:3]
    f4 = copy.deepcopy(doc["findings"][1])
    f4.update(
        {"id": "F4", "title": "duplicate date formatters drift apart", "priority": 4}
    )
    f4["source"] = [{"path": "structure.files", "resolved": 213}]
    f5 = copy.deepcopy(doc["findings"][1])
    f5.update({"id": "F5", "title": "orphan helper nobody calls", "priority": 5})
    doc["findings"] += [f4, f5]
    return doc


def make_decisions():
    def check(name, status, reason=None):
        c = {"name": name, "status": status, "tail": "3 passed in 0.41s"}
        if reason:
            c["reason"] = reason
        return c

    return {
        "run": {
            "head": HEAD,
            "base_dirty": True,
            "mode": "agents",
            "date": "2026-09-29",
            "checklist": {"passed": 5, "total": 6},
            "cost": {"tokens": 184320, "agents": 9},
        },
        "findings": [
            {  # architectural, принят, режется на фазы
                "id": "F1",
                "class": "architectural",
                "mode": "agents",
                "variants": [
                    {"name": "facade", "cost": "high", "files": 9},
                    {"name": "split", "cost": "very high", "files": 31},
                ],
                "lead": {
                    "chosen": "facade",
                    "reserve": "split",
                    "order": 2,
                    "reason": "public API stays; 110 importers see no change",
                    "phases": [
                        {
                            "title": "extract segment assembly",
                            "steps": ["move render helpers", "keep Console delegating"],
                            "risk": "import cycle through the barrel",
                        },
                        {
                            "title": "extract options handling",
                            "steps": ["move options module"],
                        },
                    ],
                },
                "trial": {
                    "status": "accepted",
                    "gain_metric": "behavior.stability.unstable_files[file=rich/console.py].rework_rate_lb",
                    "gain_before": 0.497,
                    "gain_after": 0.38,
                    "gain_target": 0.4,
                    "gain_hit": True,
                    "before_source": "clean_base",
                    "remedy_files": 9,
                    "payback": 1.75,
                    "payback_verdict": "ok",
                    "branch": "zodchiy/F1-render-pipeline-2",
                    "branch_requested": "zodchiy/F1-render-pipeline",
                    "branch_collision": True,
                    "checks": [
                        check("ruff check src", "pass"),
                        check("pytest -q", "pass"),
                    ],
                    "reasons": [],
                },
            },
            {  # scheduled, отклонён пробой
                "id": "F2",
                "class": "scheduled",
                "mode": "agents",
                "variants": [],
                "lead": {
                    "chosen": "split function",
                    "reason": "one branch chain",
                    "order": 3,
                },
                "trial": {
                    "status": "rejected",
                    "gain_metric": "structure.complex_files[file=rich/pretty.py].cyclomatic_per_function_max",
                    "gain_before": 76,
                    "gain_after": 58,
                    "gain_target": 40,
                    "gain_hit": False,
                    "gain_reason": None,
                    "before_source": "clean_base",
                    "remedy_files": 3,
                    "payback": 6.5,
                    "payback_verdict": "too_costly",
                    "branch": "zodchiy/F2-pretty",
                    "checks": [check("pytest -q", "fail", "2 failed")],
                    "reasons": ["gain not reached: 58 > 40", "payback too_costly"],
                },
            },
            {  # вопрос владельцу, проба не подтверждена
                "id": "F3",
                "class": "scheduled",
                "mode": "sequential",
                "variants": [{"name": "cut barrel"}],
                "lead": {
                    "chosen": "cut barrel",
                    "reason": "50-file component",
                    "order": 4,
                    "depends_on": ["F1"],
                    "questions": ["is /world a forgotten screen or a legal address?"],
                },
                "trial": {
                    "status": "unverified",
                    "gain_metric": "cycle_members",
                    "gain_before": 50,
                    "gain_after": None,
                    "gain_target": 20,
                    "gain_hit": False,
                    "gain_reason": "gain not measurable: metric missing in new measure",
                    "before_source": "given",
                    "remedy_files": 12,
                    "checks": [
                        check("docker compose up", "unverified", "docker not found")
                    ],
                    "reasons": ["docker not found"],
                },
            },
            {  # quick-fix с минимальным lead: ни запасного, ни порядка, ни проб
                "id": "F4",
                "class": "quick-fix",
                "mode": "agents",
                "variants": [],
                "lead": {
                    "chosen": "one shared formatter",
                    "reason": "two copies, same output",
                },
            },
        ],
    }


def all_inputs(measure, findings, decisions):
    return json.dumps([measure, findings, decisions], ensure_ascii=False)


# Код правила ruff из «Правил для всех фаз» — единственное число шаблона не из входа.
# Явный список, а не «всё в бэктиках»: код в бэктиках несёт пути и имена метрик.
ALLOWED_RULE_CODES = re.compile(r"\bF821\b")


def numbers_in(text, *, drop_head=True):
    text = ALLOWED_RULE_CODES.sub("", html.unescape(text))
    if drop_head:  # CSS и служебные <meta> — не данные отчёта
        text = re.sub(r"<style\b.*?</style>", "", text, flags=re.S)
        text = re.sub(r"<meta\b[^>]*>", "", text)
    return NUM.findall(text)


def stamp_block(doc):
    m = re.search(r'<aside class="stamp".*?</aside>', doc, flags=re.S)
    assert m, "нет штампа"
    return html.unescape(re.sub(r"<[^>]+>", " ", m.group(0)))


class Base(unittest.TestCase):
    def setUp(self):
        self.measure = make_measure()
        self.findings = make_findings()
        self.decisions = make_decisions()

    def out(self, fmt, lang="ru", measure=None, findings=None, decisions=None):
        return report.render(
            self.measure if measure is None else measure,
            self.findings if findings is None else findings,
            self.decisions if decisions is None else decisions,
            fmt,
            lang,
        )


class TestNoInventedNumbers(Base):
    def test_no_invented_numbers(self):
        universe = set(
            NUM.findall(all_inputs(self.measure, self.findings, self.decisions))
        )
        # фикстура не должна маскировать код правила из шаблона
        self.assertNotIn("821", universe)
        # индексы: номера фаз плана — единственное, что рендерер нумерует сам
        phases = len(re.findall(r"^## Фаза \d+\.", self.out("plan"), flags=re.M))
        self.assertGreaterEqual(phases, 3)
        indices = {str(i) for i in range(1, phases + 1)}
        for fmt in ("html", "md", "plan"):
            for lang in ("ru", "en"):
                nums = numbers_in(self.out(fmt, lang))
                self.assertTrue(nums, f"{fmt}/{lang}: в отчёте нет ни одного числа")
                alien = sorted(
                    {n for n in nums if n not in universe and n not in indices}
                )
                self.assertEqual(alien, [], f"{fmt}/{lang}: числа не из входа: {alien}")

    def test_fixture_would_catch_a_hardcoded_number(self):
        # контроль самого теста: выдуманное число реально ловится
        universe = set(
            NUM.findall(all_inputs(self.measure, self.findings, self.decisions))
        )
        for fake in ("0.83", "4242", "777"):
            self.assertNotIn(fake, universe)
        doc = self.out("html").replace("</body>", "<p>0.83 of 4242</p></body>")
        alien = [n for n in numbers_in(doc) if n not in universe]
        self.assertIn("0.83", alien)

    def test_numbers_carry_a_path(self):
        doc = self.out("html")
        self.assertIn('title="decisions.json:findings[id=F1].trial.gain_after"', doc)
        self.assertRegex(
            doc,
            r'<span class="num" title="measure\.json:structure\.hubs\[file=rich/console\.py\]\.fan_in">110</span>',
        )


    def test_every_html_number_is_wrapped(self):
        doc = self.out("html")
        body = doc.split("<body>")[1]
        for pat in (r"<code>.*?</code>", r"<pre\b.*?</pre>",
                    r'<span class="num"[^>]*>.*?</span>'):
            body = re.sub(pat, "", body, flags=re.S)
        text = html.unescape(re.sub(r"<[^>]+>", " ", body))
        bare = re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?(?!\w)", text)
        self.assertEqual(bare, [], "число без span с путём")


class TestMutation(Base):
    def test_mutation_propagates(self):
        before = self.out("html")
        self.decisions["findings"][0]["trial"]["gain_after"] = 0.311
        after = self.out("html")
        self.assertNotEqual(before, after)
        self.assertIn(">0.311<", after)
        self.assertNotIn(">0.38<", after)
        self.assertIn("0.311", self.out("plan"))
        self.assertIn("0.311", self.out("md"))


class TestPlan(Base):
    def test_plan_sections(self):
        plan = self.out("plan")
        pos = -1
        for marker in BASE_SECTIONS:
            at = plan.find(marker, pos + 1)
            self.assertGreater(at, pos, f"нет или не по порядку: {marker}")
            pos = at

    def test_question_becomes_owner_decision(self):
        plan = self.out("plan")
        phase = re.search(r"## Фаза \d+\. F3.*?(?=\n## )", plan, flags=re.S)
        self.assertIsNotNone(phase)
        self.assertIn("**Решение за Иваном:**", phase.group(0))
        self.assertIn("forgotten screen", phase.group(0))
        self.assertIn("**Условие старта:**", phase.group(0))
        # фаза без вопросов строки не получает
        f4 = re.search(r"## Фаза \d+\. F4.*?(?=\n## )", plan, flags=re.S).group(0)
        self.assertNotIn("**Решение за Иваном:**", f4)

    def test_order_follows_lead_order_and_phases_cut(self):
        plan = self.out("plan")
        heads = re.findall(r"^## Фаза \d+\. (F\d)", plan, flags=re.M)
        # порядок задаёт lead.order; находка без order идёт последней
        self.assertEqual(heads, ["F1", "F1", "F3", "F4"])
        self.assertIn("extract segment assembly", plan)
        self.assertIn("extract options handling", plan)
        # trial шёл по первой фазе architectural; вторая ждёт принятия первой
        second = re.findall(r"## Фаза \d+\. F1.*?(?=\n## )", plan, flags=re.S)[1]
        self.assertIn("**Условие старта:**", second)
        self.assertNotIn("git diff", second)

    def test_rejected_goes_to_excluded_with_reason(self):
        plan = self.out("plan")
        excluded = plan.split("## Не входит и почему")[1].split("## Порядок и цена")[0]
        self.assertIn("F2", excluded)
        self.assertIn("gain not reached", excluded)
        self.assertIn("F5", excluded)  # находка без решения лида
        self.assertNotRegex(plan, r"## Фаза \d+\. F2")

    def test_price_section(self):
        tail = self.out("plan").split("## Порядок и цена")[1]
        self.assertIn("1.75", tail)
        self.assertIn("F4", tail)

    def test_measure_table_uses_paths(self):
        table = self.out("plan").split("## Что показал замер")[1].split("## Фаза")[0]
        self.assertIn("| Место | Факт | Путь в `measure.json` |", table)
        self.assertIn("`structure.hubs[file=rich/console.py].fan_in`", table)
        self.assertIn("| 110 |", table)

    def test_english_plan(self):
        plan = self.out("plan", "en")
        self.assertIn("## Rules for every phase", plan)
        self.assertNotIn("## Правила для всех фаз", plan)


class TestDroppedByCritic(Base):
    """Снятая критиком находка: причина из run.skipped, а не «нет решения лида»."""

    REASON = "снята критиком: L2"

    def excluded_line(self, plan, fid):
        section = plan.split("## Не входит и почему")[1].split("## Порядок и цена")[0]
        return next(x for x in section.splitlines() if f"**{fid}**" in x)

    def test_plan_names_the_critic_not_the_missing_lead(self):
        self.decisions["run"]["skipped"] = [{"id": "F5", "reason": self.REASON}]
        line = self.excluded_line(self.out("plan"), "F5")
        self.assertIn(self.REASON, line)
        self.assertNotIn("нет решения лида", line)

    def test_without_skipped_entry_the_old_text_stays(self):
        self.assertIn("нет решения лида", self.excluded_line(self.out("plan"), "F5"))
        self.decisions["run"]["skipped"] = [{"id": "F9", "reason": self.REASON}]
        self.assertIn("нет решения лида", self.excluded_line(self.out("plan"), "F5"))

    def test_skipped_without_item_still_listed_in_plan_excluded(self):
        # F2-как-в-прогоне-1: срез не собрался, находки нет ни в findings.run.csv, ни в decisions.findings
        self.decisions["run"]["skipped"] = [{"id": "F8", "reason": "сбой этапа: срез 8193 Б"}]
        section = self.out("plan").split("## Не входит и почему")[1].split("## Порядок и цена")[0]
        line = next(x for x in section.splitlines() if "**F8**" in x)
        self.assertIn("сбой этапа: срез 8193 Б", line)
        en = self.out("plan", "en").split("## Out of scope and why")[1].split("## Order and cost")[0]
        self.assertIn("**F8**", en)

    def test_html_and_md_cards_say_the_same(self):
        self.decisions["run"]["skipped"] = [{"id": "F5", "reason": self.REASON}]
        html_card = re.search(r'<article class="card" id="card-F5">.*?</article>', self.out("html"), flags=re.S).group(0)
        self.assertIn(self.REASON, html_card)
        self.assertNotIn("нет решения лида", html_card)
        md_card = self.out("md").split("### F5")[1].split("### ")[0]
        self.assertIn(self.REASON, md_card)
        self.assertNotIn("нет решения лида", md_card)
        todo = self.out("md").split("## Что делать")[1].split("## Карточки")[0]
        self.assertRegex(todo, rf"F5 .*\| {self.REASON} \|")


class TestPaybackForecast(Base):
    """L4 в отчёте: без пробы — прогноз лида с пометкой, с пробой — факт пробы; не-ok — «Не входит»."""

    def f4(self):
        return next(x for x in self.decisions["findings"] if x["id"] == "F4")

    def price_row(self, plan, fid):
        tail = plan.split("## Порядок и цена")[1]
        return next(x for x in tail.splitlines() if x.startswith("|") and f" {fid} " in x)

    def set_lead(self, **kw):
        self.f4()["lead"].update(kw)

    def test_dry_plan_shows_forecast_marked_as_forecast(self):
        self.set_lead(remedy_files=2, payback=0.044, payback_verdict="ok", pain=45.5, payback_max_quarters=4)
        row = self.price_row(self.out("plan"), "F4")
        cells = [c.strip() for c in row.strip("|").split("|")]
        self.assertEqual(cells[4:6], ["2", "0.044"])
        self.assertEqual(cells[6], "ok (прогноз)")

    def test_trial_row_is_marked_as_fact(self):
        row = self.price_row(self.out("plan"), "F1")
        cells = [c.strip() for c in row.strip("|").split("|")]
        self.assertEqual(cells[4:7], ["9", "1.75", "ok (факт пробы)"])

    def test_trial_wins_over_lead_forecast(self):
        f1 = next(x for x in self.decisions["findings"] if x["id"] == "F1")
        f1["lead"].update(remedy_files=99, payback=77.7, payback_verdict="too_costly")
        row = self.price_row(self.out("plan"), "F1")
        self.assertIn("1.75", row)
        self.assertNotIn("77.7", row)

    def test_no_forecast_no_numbers(self):
        cells = [c.strip() for c in self.price_row(self.out("plan"), "F4").strip("|").split("|")]
        self.assertEqual(cells[4:7], ["—", "—", "—"])

    def test_cards_show_forecast_with_mark(self):
        self.set_lead(remedy_files=2, payback=0.044, payback_verdict="ok")
        card = re.search(r'<article class="card" id="card-F4">.*?</article>', self.out("html"), flags=re.S).group(0)
        text = html.unescape(re.sub(r"<[^>]+>", "", card))
        self.assertIn("прогноз", text)
        self.assertIn("файлов лечения: 2", text)
        self.assertIn("окупаемость, кварталов: 0.044", text)
        self.assertRegex(card, r'title="decisions.json:findings\[id=F4\].lead.payback"')
        md = self.out("md").split("### F4")[1].split("\n### ")[0]
        self.assertIn("прогноз", md)
        self.assertIn("0.044", md)

    def test_too_costly_or_cold_forecast_goes_to_excluded(self):
        for verdict, payback in (("too_costly", 5.0), ("cold", None)):
            with self.subTest(verdict):
                d = copy.deepcopy(self.decisions)
                lead = next(x for x in d["findings"] if x["id"] == "F4")["lead"]
                lead.update(remedy_files=5, payback=payback, payback_verdict=verdict, pain=1 if payback else 0, payback_max_quarters=4)
                plan = report.render(self.measure, self.findings, d, "plan", "ru")
                self.assertNotRegex(plan, r"## Фаза \d+\. F4")
                section = plan.split("## Не входит и почему")[1].split("## Порядок и цена")[0]
                line = next(x for x in section.splitlines() if "**F4**" in x)
                self.assertIn(verdict, line)
                self.assertIn("прогноз", line)
                self.assertIn("порог 4", line)
                self.assertNotIn("нет решения лида", line)
                self.assertNotIn(" F4 ", plan.split("## Порядок и цена")[1])
                md = report.render(self.measure, self.findings, d, "md", "ru")
                self.assertIn(verdict, md.split("Снятые находки")[1])
                self.assertIn("отсечена по окупаемости", md.split("## Что делать")[1].split("## Карточки")[0])

    def test_english_labels_exist(self):
        self.set_lead(remedy_files=2, payback=0.044, payback_verdict="ok")
        plan = self.out("plan", "en").replace("## Order and cost", "## Порядок и цена")
        self.assertIn("ok (forecast)", self.price_row(plan, "F4"))
        self.assertIn("(trial fact)", self.price_row(plan, "F1"))

    def test_old_decisions_without_l4_fields_still_valid(self):
        with open(os.path.join(SKILL, "schema", "decisions.schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        self.assertEqual(ledger.validate(self.decisions, schema), [])
        self.set_lead(remedy_files=2, payback=0.044, payback_verdict="ok", pain=45.5, payback_max_quarters=4)
        self.assertEqual(ledger.validate(self.decisions, schema), [])
        self.set_lead(payback=None, payback_verdict="cold", pain=None)
        self.assertEqual(ledger.validate(self.decisions, schema), [])
        self.set_lead(payback_verdict="maybe", remedy_files=-1)
        self.assertEqual(len(ledger.validate(self.decisions, schema)), 2)


class TestPlanFromChosenVariant(Base):
    """Шаги, прогноз и риск плана — из выбранного варианта лида, а не из remedy аналитика."""

    VARIANT = {
        "id": "v1",
        "title": "flat modules without a factory",
        "summary": "Move the notify handlers as plain functions; the factory from the analyst remedy is dropped.",
        "files": ["a.py"],
        "remedy_files": 3,
        "forecast_metric": "behavior.velocity.episodes.multi_commit_share",
        "forecast_after": 0.35,
        "risk": "monkeypatch paths in 7 tests change",
    }

    def phase(self, plan, fid="F4"):
        return re.search(rf"## Фаза \d+\. {fid}.*?(?=\n## )", plan, flags=re.S).group(0)

    def with_variant(self, **kw):
        f4 = next(x for x in self.decisions["findings"] if x["id"] == "F4")
        f4["variants"] = [dict(self.VARIANT, **kw), dict(self.VARIANT, id="v2", title="other", summary="other summary")]
        f4["lead"]["chosen"] = "v1"
        return f4

    def test_steps_forecast_risk_come_from_the_chosen_variant(self):
        self.with_variant()
        ph = self.phase(self.out("plan"))
        self.assertIn("flat modules without a factory", ph)
        self.assertIn("Move the notify handlers as plain functions", ph)
        self.assertNotIn("other summary", ph)
        forecast = re.search(r"\*\*Прогноз:\*\*.*", ph).group(0)
        self.assertIn("behavior.velocity.episodes.multi_commit_share", forecast)
        self.assertIn("0.35", forecast)
        self.assertIn("monkeypatch paths in 7 tests change", re.search(r"\*\*Риск:\*\*.*", ph).group(0))

    def test_analyst_remedy_is_not_used_when_a_variant_exists(self):
        f4 = next(x for x in self.findings["findings"] if x["id"] == "F4")
        f4["remedy"] = "ANALYST-REMEDY with the factory"
        f4["gain"] = "ANALYST-GAIN"
        f4["remedy_cost"] = "ANALYST-COST"
        self.with_variant()
        ph = self.phase(self.out("plan"))
        for needle in ("ANALYST-REMEDY", "ANALYST-GAIN", "ANALYST-COST"):
            self.assertNotIn(needle, ph)

    def test_analyst_remedy_only_when_there_is_no_variant(self):
        f4 = next(x for x in self.findings["findings"] if x["id"] == "F4")
        f4["remedy"] = "ANALYST-REMEDY"
        f4["gain"] = "ANALYST-GAIN"
        f4["remedy_cost"] = "ANALYST-COST"
        ph = self.phase(self.out("plan"))  # variants: [] в фикстуре
        for needle in ("ANALYST-REMEDY", "ANALYST-GAIN", "ANALYST-COST"):
            self.assertIn(needle, ph)
        f4d = self.with_variant()
        f4d["lead"]["chosen"] = "no-such-variant"
        self.assertIn("ANALYST-REMEDY", self.phase(self.out("plan")))

    def test_variant_without_risk_does_not_borrow_the_analyst_cost(self):
        f4 = next(x for x in self.findings["findings"] if x["id"] == "F4")
        f4["remedy_cost"] = "ANALYST-COST"
        v = self.with_variant()["variants"][0]
        del v["risk"]
        self.assertRegex(self.phase(self.out("plan")), r"\*\*Риск:\*\* —")

    def test_trial_forecast_still_wins_for_a_tried_finding(self):
        f1 = next(x for x in self.decisions["findings"] if x["id"] == "F1")
        f1["variants"] = [dict(self.VARIANT, id="facade")]
        f1["lead"]["chosen"] = "facade"
        ph = self.phase(self.out("plan"), "F1")
        self.assertIn("0.497", ph)  # gain_before из пробы
        self.assertNotIn("multi_commit_share", ph)

    def test_architectural_phase_steps_are_kept(self):
        f1 = next(x for x in self.decisions["findings"] if x["id"] == "F1")
        f1["variants"] = [dict(self.VARIANT, id="facade")]
        plan = self.out("plan")
        self.assertIn("move render helpers", plan)
        self.assertNotIn("Move the notify handlers", self.phase(plan, "F1"))


class TestReserveVariantInBranch(Base):
    """Проба шла по запасному: план и карточка описывают вариант из ветки, а не отвергнутый основной."""

    V1 = {"id": "v1", "title": "REJECTED-PRIMARY", "summary": "primary summary", "risk": "primary risk",
          "forecast_metric": "m1", "forecast_after": 11}
    V2 = {"id": "v2", "title": "RESERVE-IN-BRANCH", "summary": "reserve summary", "risk": "reserve risk",
          "forecast_metric": "m2", "forecast_after": 22}

    def setUp(self):
        super().setUp()
        self.f4 = next(x for x in self.decisions["findings"] if x["id"] == "F4")
        self.f4["variants"] = [dict(self.V1), dict(self.V2)]
        self.f4["lead"]["chosen"] = "v1"
        self.f4["lead"]["reserve"] = "v2"

    def phase(self, plan):
        return re.search(r"## Фаза \d+\. F4.*?(?=\n## )", plan, flags=re.S).group(0)

    def test_plan_steps_and_risk_come_from_the_trial_variant(self):
        self.f4["trial"] = {"status": "accepted", "variant": "v2", "checks": [], "branch": "zodchiy/F4-x"}
        ph = self.phase(self.out("plan"))
        self.assertIn("RESERVE-IN-BRANCH", ph)
        self.assertIn("reserve summary", ph)
        self.assertIn("reserve risk", ph)
        for needle in ("REJECTED-PRIMARY", "primary summary", "primary risk"):
            self.assertNotIn(needle, ph)

    def test_trial_on_the_chosen_variant_changes_nothing(self):
        self.f4["trial"] = {"status": "accepted", "variant": "v1", "checks": [], "branch": "zodchiy/F4-x"}
        ph = self.phase(self.out("plan"))
        self.assertIn("REJECTED-PRIMARY", ph)
        self.assertNotIn("RESERVE-IN-BRANCH", ph)
        for fmt in ("html", "md"):
            self.assertNotIn("проба шла по запасному", self.out(fmt), fmt)

    def test_card_says_which_variant_the_trial_ran_on(self):
        self.f4["trial"] = {"status": "accepted", "variant": "v2", "checks": [], "branch": "zodchiy/F4-x"}
        for fmt in ("html", "md"):
            doc = self.out(fmt)
            card = re.search(r'<article class="card" id="card-F4">.*?</article>', doc, flags=re.S).group(0) if fmt == "html" else doc.split("### F4")[1].split("### ")[0]
            self.assertIn("проба шла по запасному", card, fmt)
            self.assertIn("v2", card.split("проба шла по запасному")[1][:200], fmt)
        self.assertIn("trial ran on the reserve", self.out("md", "en"))


class TestNothingCutSilently(Base):
    """Отсечка аналитика и *_dropped среза видны в отчёте; числа — из decisions.json."""

    def setUp(self):
        super().setUp()
        self.decisions["run"]["candidates"] = {"total": 14, "taken": 3, "dropped": 11}
        f1 = next(x for x in self.decisions["findings"] if x["id"] == "F1")
        f1["slice_dropped"] = {"neighbors_dropped": 345, "temporal_coupling_dropped": 10}

    def test_candidates_line_in_every_format(self):
        for fmt, needle in (("md", "кандидатов 14, взято 3, отброшено 11"), ("plan", "кандидатов 14, взято 3, отброшено 11")):
            self.assertIn(needle, self.out(fmt), fmt)
        h = html.unescape(re.sub(r"<[^>]+>", "", self.out("html")))
        self.assertIn("кандидатов 14, взято 3, отброшено 11", h)
        self.assertIn("candidates 14, taken 3, dropped 11", re.sub(r"<[^>]+>", "", self.out("html", "en")))
        self.assertRegex(self.out("html"), r'<span class="num" title="decisions.json:run.candidates.total">14</span>')

    def test_slice_dropped_per_finding(self):
        md = self.out("md").split("### F1")[1].split("\n### ")[0]
        self.assertIn("neighbors_dropped: 345", md)
        self.assertIn("temporal_coupling_dropped: 10", md)
        card = re.search(r'<article class="card" id="card-F1">.*?</article>', self.out("html"), flags=re.S).group(0)
        self.assertRegex(card, r'title="decisions.json:findings\[id=F1\].slice_dropped.neighbors_dropped">345<')
        plan = self.out("plan")
        self.assertRegex(plan, r"F1.*neighbors_dropped: 345")

    def test_absent_or_zero_prints_nothing(self):
        del self.decisions["run"]["candidates"]
        next(x for x in self.decisions["findings"] if x["id"] == "F1")["slice_dropped"] = {"neighbors_dropped": 0}
        for fmt in ("html", "md", "plan"):
            doc = self.out(fmt)
            self.assertNotIn("кандидатов", doc, fmt)
            self.assertNotIn("neighbors_dropped", doc, fmt)

    def test_schema_accepts_the_new_fields(self):
        with open(os.path.join(SKILL, "schema", "decisions.schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        self.assertEqual(ledger.validate(self.decisions, schema), [])
        self.assertIn("candidates", schema["properties"]["run"]["properties"])
        self.assertIn("slice_dropped", schema["$defs"]["finding"]["properties"])
        self.decisions["run"]["candidates"]["taken"] = "три"
        self.assertEqual(len(ledger.validate(self.decisions, schema)), 1)


class TestStamp(Base):
    def test_stamp_fields(self):
        stamp = stamp_block(self.out("html"))
        for needle in (
            self.findings["repo"],
            HEAD,
            "2026-09-29",
            "калибровка",
            "пройдена",
            "потолок",
            "finding",
            "agents",
            "sequential",
        ):
            self.assertIn(needle, stamp)

    def test_stamp_numbers_carry_paths(self):
        stamp = re.search(r'<aside class="stamp".*?</aside>', self.out("html"), flags=re.S).group(0)
        for path, text in (
            ("decisions.json:run.head", HEAD),
            ("decisions.json:run.date", "2026-09-29"),
            ("findings.json:confidence_ceiling", "finding"),
            ("decisions.json:run.checklist.passed", "5"),
            ("decisions.json:run.checklist.total", "6"),
            ("decisions.json:run.cost.tokens", "184320"),
            ("decisions.json:run.cost.agents", "9"),
        ):
            self.assertIn(f'<span class="num" title="{path}">{text}</span>', stamp)
        self.assertIn("Чеклист", stamp)
        self.assertIn("цена прогона", stamp)

    def test_stamp_ceiling_falls_back_to_measure(self):
        del self.findings["confidence_ceiling"]
        stamp = re.search(r'<aside class="stamp".*?</aside>', self.out("html"), flags=re.S).group(0)
        self.assertIn('<span class="num" title="measure.json:confidence.ceiling">verdict</span>', stamp)

    def test_stamp_checklist_and_cost_absent_render_dash(self):
        del self.decisions["run"]["checklist"]
        del self.decisions["run"]["cost"]
        stamp = stamp_block(self.out("html"))
        self.assertRegex(stamp, r"Чеклист\s+—")
        self.assertRegex(stamp, r"цена прогона\s+—")
        self.assertNotIn("184320", self.out("html"))

    def test_checklist_and_cost_in_md(self):
        md = self.out("md")
        self.assertIn("- Чеклист: 5/6", md)
        self.assertIn("- цена прогона: 184320 токенов · 9 агентов", md)

    def test_cost_shows_agents_when_tokens_are_absent(self):
        # сценарий Workflow пишет только run.cost.agents: токенов он не видит
        self.decisions["run"]["cost"] = {"agents": 28}
        md = self.out("md")
        self.assertIn("- цена прогона: 28 агентов", md)
        self.assertNotIn("токенов", md)
        stamp = re.search(r'<aside class="stamp".*?</aside>', self.out("html"), flags=re.S).group(0)
        self.assertIn('<span class="num" title="decisions.json:run.cost.agents">28</span>', stamp)
        self.assertNotIn("токенов", stamp)

    def test_stamp_labels_never_break_mid_word(self):
        css = self.out("html").split("<style>")[1].split("</style>")[0]
        self.assertRegex(css, r"\.stamp dt\{[^}]*white-space:nowrap")
        self.assertRegex(css, r"\.stamp dt\{[^}]*overflow-wrap:normal")

    def test_calibration_failure_and_ceiling_shown(self):
        self.measure["calibration"] = {
            "passed": False,
            "checks": [],
            "blocked_metrics": ["temporal_coupling"],
        }
        stamp = stamp_block(self.out("html"))
        self.assertIn("не пройдена", stamp)
        self.assertIn("temporal_coupling", stamp)

    def test_base_dirty_line(self):
        line = "пробы не включают незакоммиченное"
        for fmt in ("html", "md", "plan"):
            self.assertIn(line, self.out(fmt), fmt)
        self.decisions["run"]["base_dirty"] = False
        for fmt in ("html", "md", "plan"):
            self.assertNotIn(line, self.out(fmt), fmt)

    def test_skipped_findings_shown(self):
        self.decisions["run"]["skipped"] = [
            {"id": "F7", "reason": "сбой этапа: срез не собрался"},
            {"id": "F9", "reason": "нет вердиктов критиков"},
        ]
        html_out = self.out("html")
        self.assertRegex(html_out, r'пропущено находок: <span class="num"[^>]*>2</span>')
        for fmt in ("html", "md"):
            doc = self.out(fmt)
            for needle in ("F7", "сбой этапа: срез не собрался", "F9", "нет вердиктов критиков"):
                self.assertIn(needle, doc, f"{fmt}: {needle}")
        self.assertIn("> пропущено находок: 2 — F7: сбой этапа: срез не собрался; F9: нет вердиктов критиков", self.out("md"))
        self.assertIn("findings skipped: 2", self.out("md", lang="en"))

    def test_no_skipped_line_when_empty_or_absent(self):
        for value in (None, []):
            if value is None:
                self.decisions["run"].pop("skipped", None)
            else:
                self.decisions["run"]["skipped"] = value
            for fmt in ("html", "md"):
                self.assertNotIn("пропущено находок", self.out(fmt), (fmt, value))


class TestHtml(Base):
    def test_offline(self):
        doc = self.out("html")
        self.assertNotIn("<script", doc)
        self.assertNotIn("<link", doc)
        self.assertNotIn("@import", doc)
        self.assertNotRegex(doc, r"url\(")
        self.assertNotIn("http", re.sub(r'<a\b[^>]*href="[^"]*"', "", doc))

    def test_design_tokens_and_fallbacks(self):
        doc = self.out("html")
        for token in (
            "--paper:#FDFBF7",
            "--ink:#1A1A1A",
            "--pencil:#4A4A4A",
            "--grid:#E0E0E0",
            "--redline:#B3261E",
            '"Source Serif 4",Georgia',
            '"IBM Plex Mono",ui-monospace',
            "@media print",
        ):
            self.assertIn(token, doc)

    def test_readable_on_phone(self):
        doc = self.out("html")
        self.assertIn('name="viewport"', doc)
        self.assertIn("overflow-x:auto", doc)
        self.assertIn('<div class="scroll"><table', doc)
        self.assertNotIn(
            "<table",
            re.sub(r'<div class="scroll"><table.*?</table></div>', "", doc, flags=re.S),
        )

    def test_report_order(self):
        doc = self.out("html")
        marks = [
            'class="stamp"',
            'id="todo"',
            'id="card-F1"',
            'id="blind"',
            'id="appendix"',
        ]
        at = [doc.find(m) for m in marks]
        self.assertNotIn(-1, at)
        self.assertEqual(at, sorted(at))

    def test_todo_row_has_diff_command(self):
        doc = self.out("html")
        self.assertIn(f"git diff {HEAD}...zodchiy/F1-render-pipeline-2", doc)

    def test_diff_base_is_run_head_not_main(self):
        doc = self.out("html")
        self.assertNotIn("git diff main", doc)
        # нет run.head → берётся head находок; нет и его → команды нет вовсе
        del self.decisions["run"]["head"]
        self.assertIn(f"git diff {HEAD}...zodchiy/F1", self.out("html"))
        del self.findings["head"]
        self.measure["snapshot"] = {}
        self.assertNotIn("git diff", self.out("html"))

    def test_miss_is_red_only_when_forecast_failed(self):
        doc = self.out("html")
        # F2 не дотянул до цели → красная правка; F1 попал → нет
        self.assertEqual(len(re.findall(r'class="[^"]*\bmiss\b', doc)) >= 1, True)
        row_f1 = re.search(r'<tr id="todo-F1".*?</tr>', doc, flags=re.S).group(0)
        self.assertNotIn("miss", row_f1)
        row_f2 = re.search(r'<tr id="todo-F2".*?</tr>', doc, flags=re.S).group(0)
        self.assertIn("miss", row_f2)

    def test_blind_spots_mechanical(self):
        blind = re.search(
            r'id="blind".*?id="appendix"', self.out("html"), flags=re.S
        ).group(0)
        for needle in ("ruff", "semgrep", "docker not found"):
            self.assertIn(needle, blind)

    def test_text_is_escaped(self):
        self.findings["findings"][0]["title"] = "<b>x</b> & y"
        doc = self.out("html")
        self.assertNotIn("<b>x</b>", doc)
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; y", doc)

    def test_english_labels(self):
        doc = self.out("html", "en")
        self.assertIn('<html lang="en">', doc)
        self.assertIn("What to do", doc)
        self.assertNotIn("Что делать", doc)


class TestMarkdown(Base):
    def test_md_order(self):
        md = self.out("md")
        marks = [
            "## Что делать",
            "## Карточки решений",
            "## Слепые зоны",
            "## Приложение",
        ]
        at = [md.find(m) for m in marks]
        self.assertNotIn(-1, at)
        self.assertEqual(at, sorted(at))
        self.assertIn(f"git diff {HEAD}...zodchiy/F1-render-pipeline-2", md)


class TestDegradation(Base):
    def test_empty_inputs_do_not_crash(self):
        for fmt in ("html", "md", "plan"):
            for lang in ("ru", "en"):
                text = report.render({}, [], {}, fmt, lang)
                self.assertIn("—", text)

    def test_missing_optional_fields_render_dash(self):
        findings = [{"id": "F9", "title": "bare"}]
        decisions = {
            "findings": [
                {"id": "F9", "lead": {"chosen": "x"}, "trial": {"status": "unverified"}}
            ]
        }
        doc = report.render({}, findings, decisions, "html", "ru")
        # строка «что делать»: прогноз и команда — прочерки; запасной вариант — прочерк
        row = re.search(r'<tr id="todo-F9".*?</tr>', doc, flags=re.S).group(0)
        self.assertTrue(row.endswith("<td>—</td><td>—</td></tr>"), row)
        self.assertIn("запасной: —</p>", doc)
        stamp = stamp_block(doc)
        for label in ("репозиторий", "HEAD", "дата", "Чеклист", "цена прогона"):
            self.assertRegex(stamp, rf"{label}\s+—", label)
        md = report.render({}, findings, decisions, "md", "ru")
        self.assertRegex(md, r"(?m)^\| F9 bare \| не проверено \| — \| — \|$")
        self.assertIn("запасной: —", md)
        plan = report.render({}, findings, decisions, "plan", "ru")
        self.assertIn("**Прогноз:** —", plan)
        self.assertIn("**Риск:** —", plan)
        self.assertNotIn("git diff", doc + md + plan)

    def test_unknown_format_and_lang_raise(self):
        with self.assertRaises(ValueError):
            report.render({}, [], {}, "pdf", "ru")
        with self.assertRaises(ValueError):
            report.render({}, [], {}, "md", "de")


def used_label_keys(body):
    """Ключи LABELS, которые код берёт по-настоящему: L["k"], плейсхолдер {k}
    (его подставляет sub_labels) и кортежи, по которым метки берутся в цикле
    `L[k]`. Голая строка "k" — например ключ словаря данных — не считается."""
    keys = set(re.findall(r"""\bL\[["'](\w+)["']\]""", body))
    # плейсхолдер стоит в начале строки, которую позже разворачивает sub_labels
    keys |= set(re.findall(r"""f?["']\{\{?(\w+)\}\}?:""", body))
    for group in re.findall(r"for k in \(([^)]*)\)", body):
        keys |= set(re.findall(r'"(\w+)"', group))
    tags = re.search(r"tags = \[(.*?)\n    \]", body, flags=re.S)
    if tags:
        keys |= set(re.findall(r'\(\s*"(\w+)"\s*,', tags.group(1)))
    return keys


class TestContract(Base):
    def test_fixture_decisions_validate_against_schema(self):
        with open(os.path.join(SKILL, "schema", "decisions.schema.json"), encoding="utf-8") as fh:
            schema = json.load(fh)
        self.assertEqual(ledger.validate(self.decisions, schema), [])
        # lead.phases — часть контракта, а не догадка рендерера
        self.assertIn("phases", schema["$defs"]["lead"]["properties"])
        self.assertIn("checklist", schema["properties"]["run"]["properties"])
        self.assertIn("cost", schema["properties"]["run"]["properties"])

    def test_every_label_is_used_and_languages_agree(self):
        self.assertEqual(set(report.LABELS["ru"]), set(report.LABELS["en"]))
        with open(os.path.join(SKILL, "scripts", "report.py"), encoding="utf-8") as fh:
            src = fh.read()
        body = re.sub(r"\nLABELS = \{.*?\nCSS = ", "\nCSS = ", src, flags=re.S)
        for key in sorted(used_label_keys(body)):
            self.assertIn(key, report.LABELS["ru"], f"использована метка {key}, которой нет в словаре")
        unused = set(report.LABELS["ru"]) - used_label_keys(body)
        self.assertEqual(unused, set(), "метки без единого использования")

    def test_label_guard_catches_a_dummy_key(self):
        # контроль самого теста: лишний ключ (и «голое» упоминание строкой) не считается использованием
        with open(os.path.join(SKILL, "scripts", "report.py"), encoding="utf-8") as fh:
            body = fh.read() + '\nx = {"dummy_label": 1}\ny = d.get("dummy_label")\n'
        self.assertNotIn("dummy_label", used_label_keys(body))

    def test_no_digits_in_labels(self):
        for lang, labels in report.LABELS.items():
            for key, text in labels.items():
                for t in text if isinstance(text, list) else [text]:
                    self.assertFalse(re.search(r"\d", t.replace("F821", "").replace("F<n>", "")), f"{lang}.{key}: {t}")


def nested_same_quote_lines(src):
    """Строки, где выражение внутри f-строки повторяет её кавычку.

    Это SyntaxError до Python 3.12 (PEP 701 снял запрет), а на 3.12+ такой код
    молча компилируется — поэтому `ast.parse(feature_version=(3, 9))` тут слеп:
    он не проверяет вложенные кавычки. Ловим токенами; на старом Python f-строка
    приходит одним токеном и ошибку даёт сам разбор файла.
    """
    if sys.version_info < (3, 12):
        return []
    bad, stack = [], []  # элемент стека: [кавычка внешней f-строки, глубина {}]
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.FSTRING_START:
            quote = tok.string.lstrip("fFrRbBuU")
            if stack and stack[-1][1] > 0 and len(stack[-1][0]) == 1 and quote[0] == stack[-1][0]:
                bad.append(tok.start[0])
            stack.append([quote, 0])
        elif tok.type == tokenize.FSTRING_END:
            stack.pop()
        elif stack and tok.type == tokenize.OP and tok.string == "{":
            stack[-1][1] += 1
        elif stack and tok.type == tokenize.OP and tok.string == "}":
            stack[-1][1] -= 1
        elif stack and stack[-1][1] > 0 and tok.type == tokenize.STRING:
            quote = tok.string.lstrip("rRbBuU")
            if len(stack[-1][0]) == 1 and quote[0] == stack[-1][0]:
                bad.append(tok.start[0])
    return bad


class TestPython39(unittest.TestCase):
    """Системный python3 на macOS — 3.9: скрипты скилла обязаны на нём разбираться."""

    FILES = sorted(
        glob.glob(os.path.join(SKILL, "scripts", "*.py")) + [os.path.join(SKILL, "zodchiy.py")]
    )

    def test_guard_catches_nested_same_quote(self):
        if sys.version_info < (3, 12):
            self.skipTest("на Python < 3.12 такой код не разбирается вовсе")
        self.assertEqual(nested_same_quote_lines('x = f"{d["k"]}"\n'), [1])
        self.assertEqual(nested_same_quote_lines("x = f\"{d['k']}\"\n"), [])
        self.assertEqual(nested_same_quote_lines('x = f"""{d["k"]}"""\n'), [])

    def test_no_nested_same_quote_fstrings_and_parse_as_39(self):
        for path in self.FILES:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            with self.subTest(file=os.path.basename(path)):
                ast.parse(src, feature_version=(3, 9))
                self.assertEqual(nested_same_quote_lines(src), [])

    @unittest.skipUnless(
        os.path.exists("/usr/bin/python3")
        and subprocess.run(
            ["/usr/bin/python3", "-c", "import sys;sys.exit(sys.version_info >= (3, 12))"]
        ).returncode
        == 0,
        "нет системного Python < 3.12",
    )
    def test_system_python_renders_every_format(self):
        code = (
            "import sys;sys.path.insert(0, sys.argv[1]);import report;"
            "[report.render({}, [], {}, f, l) for f in ('html','md','plan') for l in ('ru','en')]"
        )
        res = subprocess.run(
            ["/usr/bin/python3", "-c", code, os.path.join(SKILL, "scripts")],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(res.returncode, 0, res.stderr)


class TestCli(Base):
    def test_cli_writes_all_three_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = {}
            for name, obj in (
                ("measure", self.measure),
                ("findings", self.findings),
                ("decisions", self.decisions),
            ):
                paths[name] = os.path.join(tmp, f"{name}.json")
                with open(paths[name], "w", encoding="utf-8") as fh:
                    json.dump(obj, fh, ensure_ascii=False)
            for fmt, marker in (
                ("html", "<!doctype html>"),
                ("md", "## Что делать"),
                ("plan", "**Статус:**"),
            ):
                out = os.path.join(tmp, "вывод", f"r.{fmt}")
                res = subprocess.run(
                    [
                        sys.executable,
                        os.path.join(SKILL, "zodchiy.py"),
                        "report",
                        "--measure",
                        paths["measure"],
                        "--findings",
                        paths["findings"],
                        "--decisions",
                        paths["decisions"],
                        "--format",
                        fmt,
                        "--out",
                        out,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(res.returncode, 0, res.stderr)
                with open(out, encoding="utf-8") as fh:
                    self.assertIn(marker, fh.read())


if __name__ == "__main__":
    unittest.main()
