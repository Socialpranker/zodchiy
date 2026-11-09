#!/usr/bin/env python3
"""Юнит-слой `slice`: бюджет 8 КБ, нет тихого усечения, пути с кириллицей.

Бюджет проверяется на замере `examples/rich` (F1) и на синтетическом
замере, где файлов у находки намного больше, чем влезает.
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, SKILL)

import ledger  # noqa: E402
import slice as slicer  # noqa: E402
import zodchiy  # noqa: E402
from test_payback import make_repo  # noqa: E402

EXAMPLE = os.path.join(SKILL, "examples", "rich")


def size(doc: dict) -> int:
    return len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))


def lists_have_counters(test: unittest.TestCase, doc: dict):
    for k, v in doc.items():
        if isinstance(v, list) and k not in ("finding_fields_cut",):
            test.assertIn(f"{k}_dropped", doc, k)
            test.assertIsInstance(doc[f"{k}_dropped"], int, k)


def synthetic(n_files: int) -> tuple[dict, dict]:
    files = [f"pkg/m{i:03d}.py" for i in range(n_files)]
    measure = {
        "behavior": {
            "package_roots": [],
            "thresholds": {"coupling_min_shared": 5},
            "hotspots": [{"file": f, "edits": 50 + i, "loc": 900, "score": 1, "fix_share": 0.3,
                          "edits_pct": 90, "loc_pct": 90, "fix_share_pct": 90, "score_pct": 90}
                         for i, f in enumerate(files)],
            "stability": {"unstable_files": [{"file": f, "changes": 40, "rework_rate": 0.5} for f in files]},
            "temporal_coupling": [{"a": files[i], "b": f"pkg/x{i}.py", "shared": 9, "degree": 0.9,
                                   "commits": ["a" * 9] * 5} for i in range(n_files)],
            "containment": {"top_crossings": [{"layers": "pkg + other", "count": 3, "share": 0.1,
                                               "examples": [{"sha": "a" * 9, "subject": "s" * 60}] * 3}] * 12},
        },
        "structure": {
            "complex_files": [{"file": f, "loc": 900, "max_nesting": 9} for f in files],
            "hubs": [{"file": f, "fan_in": 9, "fan_out": 9} for f in files],
            "adjacency_through_barrels": {files[0]: [f"dep/d{i:03d}.py" for i in range(60)]},
        },
        "lint": {"available": True, "files": [{"file": f, "simplicity": 3, "width": 1, "perf": 0,
                                                 "simplicity_pct": 80, "width_pct": 50, "perf_pct": 0,
                                                 "hot": True} for f in files]},
        "calibration": {"blocked_metrics": []},
        "confidence": {"ceiling": "finding"},
    }
    finding = {"id": "F9", "title": "wide", "risk": "R1", "symptom": "s",
               "source": "; ".join(f"behavior.hotspots[file={f}].edits" for f in files)}
    return measure, finding


class TestSlice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = make_repo(cls.tmp.name, [(10, "fix: a", ["rich/console.py"])])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def rich_slice(self):
        with open(os.path.join(EXAMPLE, "measure.json"), encoding="utf-8") as fh:
            measure = json.load(fh)
        row = next(r for r in ledger.read_findings(os.path.join(EXAMPLE, "findings.csv")) if r["id"] == "F1")
        return measure, row, slicer.build_slice(measure, row, self.repo)

    def test_slice_budget(self):
        measure, _, doc = self.rich_slice()
        self.assertLessEqual(size(doc), 8192)
        self.assertEqual(doc["files"], ["rich/console.py"])
        self.assertEqual(doc["hotspots"][0]["edits"], 463)
        self.assertEqual(doc["hubs"][0]["fan_in"], 110)
        self.assertEqual(doc["ceiling"], measure["confidence"]["ceiling"])
        self.assertEqual(doc["payback_max_quarters"], 4)
        self.assertEqual(doc["pain"]["edits_by_quarter"], [0, 0, 0, 1])
        # срез — доля целого, а не его копия
        self.assertLess(size(doc) * 10, os.path.getsize(os.path.join(EXAMPLE, "measure.json")))

    def test_slice_budget_holds_when_content_overflows(self):
        measure, finding = synthetic(40)
        doc = slicer.build_slice(measure, finding, self.repo)
        self.assertLessEqual(size(doc), 8192)

    def run_slice_cli(self, measure, finding, out_dir):
        paths = {"measure": os.path.join(out_dir, "measure.json"), "findings": os.path.join(out_dir, "f.csv"),
                 "out": os.path.join(out_dir, "slice.json")}
        with open(paths["measure"], "w", encoding="utf-8") as fh:
            json.dump(measure, fh, ensure_ascii=False)
        ledger.write_findings(paths["findings"], [finding])
        rc = zodchiy.main(["slice", "--id", finding["id"], "--measure", paths["measure"],
                           "--findings", paths["findings"], "--repo", self.repo, "--out", paths["out"]])
        self.assertEqual(rc, 0)
        return paths["out"]

    def test_written_file_within_budget_at_the_newline_boundary(self):
        # Сериализация без перевода строки ровно 8192 байта: старый код оставлял её как есть
        # и дописывал "\n" → файл 8193 байта, а сценарий отбрасывал находку по `bytes > 8192`.
        measure, finding = synthetic(3)
        finding["symptom"] = "s"
        base = size(slicer.build_slice(copy.deepcopy(measure), finding, self.repo))
        finding["symptom"] = "s" * (slicer.BUDGET - base + 1)
        with mock.patch.object(slicer, "_fit", lambda doc: doc):
            unfit = slicer.build_slice(copy.deepcopy(measure), finding, self.repo)
        self.assertEqual(size(unfit), slicer.BUDGET)  # ровно на границе, без перевода строки
        with tempfile.TemporaryDirectory() as d:
            out = self.run_slice_cli(measure, finding, d)
            self.assertLessEqual(os.path.getsize(out), slicer.BUDGET)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
        lists_have_counters(self, doc)
        self.assertGreater(doc["neighbors_dropped"], 40)  # уступили ради перевода строки, с числом

    def test_written_file_within_budget_one_byte_under_untouched(self):
        measure, finding = synthetic(3)
        finding["symptom"] = "s"
        base = size(slicer.build_slice(copy.deepcopy(measure), finding, self.repo))
        finding["symptom"] = "s" * (slicer.BUDGET - base)
        with tempfile.TemporaryDirectory() as d:
            out = self.run_slice_cli(measure, finding, d)
            self.assertEqual(os.path.getsize(out), slicer.BUDGET)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
        self.assertEqual(doc["neighbors_dropped"], 40)

    def test_dropped_counter_increment_cannot_push_over_budget(self):
        # Счётчик 9 → 10 добавляет байт; пробуем все размеры около границы.
        measure, finding = synthetic(3)
        finding["symptom"] = "s"
        base = size(slicer.build_slice(copy.deepcopy(measure), finding, self.repo))
        for pad in range(slicer.BUDGET - base - 2, slicer.BUDGET - base + 60):
            finding["symptom"] = "s" * pad
            doc = slicer.build_slice(copy.deepcopy(measure), finding, self.repo)
            self.assertLessEqual(len(slicer.serialize(doc).encode("utf-8")), slicer.BUDGET, pad)

    def test_slice_no_silent_cut(self):
        _, _, doc = self.rich_slice()
        lists_have_counters(self, doc)
        measure, finding = synthetic(40)
        doc = slicer.build_slice(measure, finding, self.repo)
        lists_have_counters(self, doc)
        # то, что было в замере, = осталось + отброшено
        self.assertEqual(len(doc["neighbors"]) + doc["neighbors_dropped"], 60)
        self.assertEqual(len(doc["temporal_coupling"]) + doc["temporal_coupling_dropped"], 40)
        self.assertGreater(doc["temporal_coupling_dropped"], 0)
        self.assertEqual(len(doc["top_crossings"]) + doc["top_crossings_dropped"], 12)
        self.assertEqual(len(doc["hotspots"]) + doc["hotspots_dropped"], 40)
        self.assertEqual(len(doc["lint_files"]) + doc["lint_files_dropped"], 40)

    def test_slice_finding_cut_reports_dropped_chars(self):
        measure, finding = synthetic(3)
        finding["symptom"] = "s" * 20000
        finding["remedy"] = "r" * 1000
        doc = slicer.build_slice(measure, finding, self.repo)
        self.assertLessEqual(size(doc), 8192)
        self.assertEqual(doc["finding_fields_cut"], {"symptom": 19600, "remedy": 600})
        self.assertEqual(len(doc["finding"]["symptom"]), 400)

    def test_slice_neighbors_and_coupling_caps(self):
        measure, finding = synthetic(3)
        doc = slicer.build_slice(measure, finding, self.repo)
        self.assertEqual(len(doc["neighbors"]), 20)
        self.assertEqual(doc["neighbors_dropped"], 40)
        measure["behavior"]["temporal_coupling"] *= 6  # 18 пар с файлами находки
        doc = slicer.build_slice(measure, finding, self.repo)
        self.assertEqual(len(doc["temporal_coupling"]), 10)
        self.assertEqual(doc["temporal_coupling_dropped"], 8)

    def test_slice_filters_by_finding_files(self):
        measure, finding = synthetic(5)
        finding["source"] = "behavior.hotspots[file=pkg/m001.py].edits"
        doc = slicer.build_slice(copy.deepcopy(measure), finding, self.repo)
        self.assertEqual(doc["files"], ["pkg/m001.py"])
        self.assertEqual([r["file"] for r in doc["hotspots"]], ["pkg/m001.py"])
        self.assertEqual([p["a"] for p in doc["temporal_coupling"]], ["pkg/m001.py"])

    def test_slice_cyrillic_path(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, [(20, "fix: раз", ["src/mod.py", "src/b.py"]),
                                 (5, "fix: два", ["src/mod.py"])], name="тест репо")
            measure, finding = synthetic(1)
            finding["source"] = "behavior.hotspots[file=src/mod.py].edits"
            out = os.path.join(d, "out.json")
            paths = {"measure": os.path.join(d, "measure.json"), "findings": os.path.join(d, "f.csv")}
            with open(paths["measure"], "w", encoding="utf-8") as fh:
                json.dump(measure, fh, ensure_ascii=False)
            ledger.write_findings(paths["findings"], [{**finding, "source": finding["source"]}])
            rc = zodchiy.main(["slice", "--id", "F9", "--measure", paths["measure"],
                               "--findings", paths["findings"], "--repo", repo, "--out", out])
            self.assertEqual(rc, 0)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
        self.assertEqual(doc["files"], ["src/mod.py"])
        self.assertEqual(doc["pain"]["edits_by_quarter"], [0, 0, 0, 2])
        self.assertEqual(doc["pain"]["fix_episodes_per_quarter"], 0.5)
        self.assertNotIn("тест репо", json.dumps(doc, ensure_ascii=False))
        self.assertNotIn(d, json.dumps(doc, ensure_ascii=False))

    def test_slice_not_a_git_repo_reports_it(self):
        measure, finding = synthetic(1)
        with tempfile.TemporaryDirectory() as d:
            doc = slicer.build_slice(measure, finding, d)
        self.assertIs(doc["pain"]["available"], False)
        self.assertIn("git", doc["pain"]["reason"])

    def test_slice_unknown_id_exits(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit) as cm:
            csv_path = os.path.join(d, "f.csv")
            ledger.write_findings(csv_path, [{"id": "F1"}])
            sys.argv = ["zodchiy slice", "--id", "F7", "--measure", os.path.join(EXAMPLE, "measure.json"),
                        "--findings", csv_path, "--repo", d]
            with redirect_stdout(io.StringIO()):
                slicer.main()
        self.assertIn("F7", str(cm.exception.code))


if __name__ == "__main__":
    unittest.main()
