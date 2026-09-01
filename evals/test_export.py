#!/usr/bin/env python3
"""Машиночитаемый выход: документ по схеме и SARIF.

Markdown отчёта пишет модель — его форма зависит от харнесса и от настроения
прогона. В CI и в сравнении двух прогонов опираться можно только на это,
поэтому здесь проверяется не «сформировалось», а что именно сформировалось:
разрешены ли `source`, доехал ли потолок, краснеет ли схема на дрейфе полей.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, HERE)

import ledger  # noqa: E402

ENTRY = os.path.join(SKILL, "zodchiy.py")

MEASURE = {
    "repo": "/tmp/repo",
    "snapshot": {"head": "abc123"},
    "confidence": {"ceiling": "verdict", "reasons": []},
    "calibration": {"passed": True},
    "structure": {"files": 10, "edges": 12, "cycles": []},
    "behavior": {
        "containment": {"ratio": 0.5},
        "temporal_coupling_total": 1,
        "commits_with_code": 100,
        "hotspots": [{"file": "src/a.py", "edits": 20, "fix_share": 0.4}],
        "stability": {"rework_rate": 0.3},
        "velocity": {},
    },
}


def finding(**kw):
    row = {k: "x" for k in ledger.REQUIRED}
    row.update({
        "id": "F1",
        "risk": "R2",
        "source": "behavior.hotspots[file=src/a.py].fix_share",
        "gain_metric": "containment_ratio",
        "gain_target": "0.7",
        "priority": "8",
        "confidence": "verdict",
    })
    row.update(kw)
    return row


def verdict(**kw):
    rec = {"finding_id": "F1", "lens": "L1", "verdict": "survives",
           "reason": "не barrel", "mode": "parallel"}
    rec.update(kw)
    return rec


class TestDocument(unittest.TestCase):
    def test_source_is_resolved_into_the_document(self):
        """Документ читается без measure.json под рукой — иначе это ссылка на
        файл, которого у читателя нет, а не машиночитаемый выход."""
        doc = ledger.document([finding()], MEASURE, [verdict()])
        src = doc["findings"][0]["source"][0]
        self.assertEqual(src["resolved"], 0.4)
        self.assertNotIn("error", src)

    def test_broken_source_is_recorded_not_swallowed(self):
        doc = ledger.document([finding(source="behavior.нетакого")], MEASURE, [verdict()])
        src = doc["findings"][0]["source"][0]
        self.assertIsNone(src["resolved"])
        self.assertIn("error", src)

    def test_files_extracted_for_locations(self):
        doc = ledger.document([finding()], MEASURE, [verdict()])
        self.assertEqual(doc["findings"][0]["files"], ["src/a.py"])

    def test_sequential_refutation_lowers_the_ceiling(self):
        doc = ledger.document([finding()], MEASURE, [verdict(mode="sequential")])
        self.assertEqual(doc["confidence_ceiling"], "finding")
        self.assertTrue(any("последовательно" in r for r in doc["ceiling_reasons"]))
        self.assertEqual(doc["refutation"]["sequential_findings"], ["F1"])

    def test_ceiling_is_the_lower_of_two(self):
        m = dict(MEASURE, confidence={"ceiling": "finding", "reasons": ["regex"]})
        doc = ledger.document([finding()], m, [verdict()])
        self.assertEqual(doc["confidence_ceiling"], "finding")

    def test_priority_is_a_number_not_a_string(self):
        doc = ledger.document([finding()], MEASURE, [verdict()])
        self.assertEqual(doc["findings"][0]["priority"], 8)


class TestSchema(unittest.TestCase):
    def doc(self, **kw):
        d = ledger.document([finding()], MEASURE, [verdict()])
        d.update(kw)
        return d

    def test_real_document_validates(self):
        self.assertEqual(ledger.validate_document(self.doc()), [])

    def test_missing_required_is_caught(self):
        d = self.doc()
        del d["head"]
        self.assertTrue(any("head" in p for p in ledger.validate_document(d)))

    def test_wrong_enum_inside_ref_is_caught(self):
        d = self.doc()
        d["findings"][0]["confidence"] = "почти уверен"
        problems = ledger.validate_document(d)
        self.assertTrue(any("findings[0].confidence" in p for p in problems), problems)

    def test_type_mismatch_is_caught(self):
        d = self.doc()
        d["findings"][0]["priority"] = "8"
        self.assertTrue(any("integer" in p for p in ledger.validate_document(d)))

    def test_range_is_checked(self):
        d = self.doc()
        d["findings"][0]["priority"] = 42
        self.assertTrue(any("максимума" in p for p in ledger.validate_document(d)))

    def test_every_csv_field_is_described(self):
        """Дрейф: колонка в findings.csv есть, в схеме её нет — значит она
        молча выпадет из машинного выхода и в CI её никто не увидит."""
        with open(ledger.SCHEMA_PATH, encoding="utf-8") as fh:
            schema = json.load(fh)
        described = set(schema["$defs"]["finding"]["properties"])
        self.assertEqual(set(ledger.FIELDS) - described, set())


class TestSarif(unittest.TestCase):
    def sarif_for(self, prio):
        doc = ledger.document([finding(priority=str(prio))], MEASURE, [verdict()])
        return ledger.sarif(doc)

    def test_level_from_priority(self):
        self.assertEqual(self.sarif_for(9)["runs"][0]["results"][0]["level"], "error")
        self.assertEqual(self.sarif_for(6)["runs"][0]["results"][0]["level"], "warning")
        self.assertEqual(self.sarif_for(2)["runs"][0]["results"][0]["level"], "note")

    def test_location_comes_from_source(self):
        loc = self.sarif_for(8)["runs"][0]["results"][0]["locations"][0]
        self.assertEqual(loc["physicalLocation"]["artifactLocation"]["uri"], "src/a.py")

    def test_no_location_when_source_has_no_file(self):
        doc = ledger.document([finding(source="behavior.containment.ratio")], MEASURE, [verdict()])
        self.assertNotIn("locations", ledger.sarif(doc)["runs"][0]["results"][0])

    def test_rules_are_deduped(self):
        rows = [finding(), finding(id="F2"), finding(id="F3", risk="R5")]
        refs = [verdict(), verdict(finding_id="F2"), verdict(finding_id="F3")]
        s = ledger.sarif(ledger.document(rows, MEASURE, refs))
        self.assertEqual([r["id"] for r in s["runs"][0]["tool"]["driver"]["rules"]], ["R2", "R5"])

    def test_degradation_reaches_the_machine_output(self):
        doc = ledger.document([finding()], MEASURE, [verdict(mode="sequential")])
        props = ledger.sarif(doc)["runs"][0]["properties"]
        self.assertEqual(props["refutationMode"], "sequential")
        self.assertIn("потолок", props["disclosure"].lower())
        self.assertEqual(props["confidenceCeiling"], "finding")


class TestExportCli(unittest.TestCase):
    def setUp(self):
        import csv
        import tempfile

        self.dir = tempfile.mkdtemp(prefix="zodchiy-export-")
        self.m = os.path.join(self.dir, "measure.json")
        self.f = os.path.join(self.dir, "findings.csv")
        self.r = os.path.join(self.dir, "refutation.json")
        with open(self.m, "w", encoding="utf-8") as fh:
            json.dump(MEASURE, fh)
        with open(self.r, "w", encoding="utf-8") as fh:
            # две различные линзы: confidence=verdict одной не обходится
            json.dump([verdict(), verdict(lens="L4")], fh)
        with open(self.f, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=ledger.FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerow(finding())

    def run_export(self, *extra):
        return subprocess.run(
            [sys.executable, ENTRY, "export", "--findings", self.f,
             "--measure", self.m, "--refutations", self.r, *extra],
            capture_output=True, text=True, timeout=120,
        )

    def test_json_export_is_valid(self):
        r = self.run_export()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(ledger.validate_document(json.loads(r.stdout)), [])

    def test_sarif_export_parses(self):
        r = self.run_export("--format", "sarif")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["version"], "2.1.0")

    def test_broken_finding_blocks_export(self):
        """Экспорт — вход в CI. Машиночитаемый брак хуже отсутствия выхода:
        его прочитают и поверят."""
        import csv

        with open(self.f, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=ledger.FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerow(finding(cost_pain=""))
        r = self.run_export()
        self.assertEqual(r.returncode, 1)
        self.assertIn("cost_pain", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
