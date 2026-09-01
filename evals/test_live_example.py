"""Регрессия живого прогона — слой, которого не было.

`examples/rich/` — единственный проход всей доктрины по чужому репозиторию:
карта, суждение, опровержение, отчёт. Он лежал в дереве как иллюстрация, то
есть не проверялся ничем. Иллюстрация, которая разошлась с доктриной, хуже
её отсутствия: читатель берёт из неё образец.

Скрипты покрыты юнитами, но здесь проверяется другое — что выдал АГЕНТ,
прочитавший `SKILL.md`. Проверяемо ровно то, что можно проверить механически:
резолвятся ли пути, покрыты ли находки опровержением, объявлена ли деградация
шага 4, дошла ли каждая находка до отчёта. Качество формулировок отсюда
недоступно и не изображается проверенным.
"""

from __future__ import annotations

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
# Каталог примера переопределяется переменной окружения — иначе мутационную
# проверку этого слоя пришлось бы делать правкой самого примера в дереве.
EXAMPLE = os.environ.get("ZODCHIY_EXAMPLE") or os.path.join(SKILL, "examples", "rich")
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import ledger  # noqa: E402


def _paths():
    return {
        "findings": os.path.join(EXAMPLE, "findings.csv"),
        "measure": os.path.join(EXAMPLE, "measure.json"),
        "refutations": os.path.join(EXAMPLE, "refutation.json"),
        "report": os.path.join(EXAMPLE, "report.md"),
        "sarif": os.path.join(EXAMPLE, "findings.sarif"),
        "document": os.path.join(EXAMPLE, "findings.json"),
    }


@unittest.skipUnless(os.path.isdir(EXAMPLE), "нет examples/rich")
class LiveExample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = _paths()
        cls.findings = ledger.read_findings(p["findings"])
        cls.measure = ledger.load(p["measure"])
        with open(p["refutations"], encoding="utf-8") as fh:
            cls.refutations = json.load(fh)
        with open(p["report"], encoding="utf-8") as fh:
            cls.report = fh.read()

    def test_selfcheck_passes(self):
        """Тот же гейт, которым скилл проверяет чужие отчёты, — на своём."""
        res = ledger.selfcheck(self.findings, self.measure, self.refutations)
        self.assertEqual(res["problems"], [], "selfcheck нашёл проблемы в примере")
        self.assertEqual(res["result"], "OK")

    def test_every_source_resolves(self):
        """Каждое число находки достаётся из замера по своему пути.

        Дублирует selfcheck намеренно: selfcheck — проверяемый код, и если
        он однажды начнёт «тихо пропускать», этот тест останется независимым.
        """
        for row in self.findings:
            for path in (row.get("source") or "").split(";"):
                path = path.strip()
                if not path:
                    continue
                with self.subTest(finding=row["id"], source=path):
                    self.assertIsNotNone(
                        ledger.resolve_path(self.measure, path),
                        f"{row['id']}: путь {path} не резолвится",
                    )

    def test_no_finding_without_source(self):
        for row in self.findings:
            with self.subTest(finding=row["id"]):
                self.assertTrue((row.get("source") or "").strip(),
                                f"{row['id']} без источника — доктрина такое запрещает")

    def test_refutation_covers_every_finding(self):
        covered = {r["finding_id"] for r in self.refutations}
        for row in self.findings:
            with self.subTest(finding=row["id"]):
                self.assertIn(row["id"], covered, f"{row['id']} не проходил шаг 4")

    def test_degradation_is_declared(self):
        """Молчаливая деградация — ровно то, что скилл запрещает другим.

        Прогон шёл последовательно, одной моделью. Значит потолок уверенности
        обязан быть опущен и объявлен, а не подразумеваться.
        """
        res = ledger.selfcheck(self.findings, self.measure, self.refutations)
        mode = res["refutation"]
        if mode["mode"] != "sequential":
            self.skipTest("прогон был не последовательным")
        self.assertTrue(mode.get("disclosure"), "деградация не объявлена в отчёте")
        self.assertEqual(mode.get("ceiling_cap"), "finding")
        for row in self.findings:
            with self.subTest(finding=row["id"]):
                self.assertNotEqual(row.get("confidence"), "verdict",
                                    f"{row['id']}: verdict недостижим при последовательном шаге 4")

    def test_every_finding_reaches_the_report(self):
        """Находка, дошедшая до леджера и потерянная в отчёте, — тихая пропажа."""
        for row in self.findings:
            with self.subTest(finding=row["id"]):
                self.assertIn(row["id"], self.report,
                              f"{row['id']} есть в леджере и отсутствует в отчёте")

    def test_exports_match_the_ledger(self):
        """Машиночитаемые выходы — те же находки, а не их прошлая версия."""
        doc = ledger.document(self.findings, self.measure, self.refutations)
        self.assertEqual(ledger.validate_document(doc), [], "документ не по схеме")
        ids = {f["id"] for f in doc["findings"]}
        self.assertEqual(ids, {row["id"] for row in self.findings})

        with open(_paths()["document"], encoding="utf-8") as fh:
            stored = json.load(fh)
        self.assertEqual({f["id"] for f in stored["findings"]}, ids,
                         "findings.json разошёлся с findings.csv — пересоберите export")

        # ruleId в SARIF — код риска (R1…R6), а не идентификатор находки:
        # правило у сканера одно на класс дефекта, находок по нему бывает много.
        with open(_paths()["sarif"], encoding="utf-8") as fh:
            sar = json.load(fh)
        rule_ids = {r["ruleId"] for run in sar["runs"] for r in run["results"]}
        risks = {row["risk"] for row in self.findings}
        self.assertTrue(rule_ids <= risks,
                        f"SARIF ссылается на риски вне леджера: {rule_ids - risks}")
        self.assertEqual(sum(len(run["results"]) for run in sar["runs"]), len(self.findings),
                         "в SARIF не столько находок, сколько в леджере")


if __name__ == "__main__":
    unittest.main()
