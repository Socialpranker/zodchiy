#!/usr/bin/env python3
"""Юнит-слой детектора клонов: окна нормализованных токенов tree-sitter.

Фикстура `clones_money`: четыре копии форматтера денег с переименованными
переменными (`.ts` и `.tsx`) и `duration.ts` — тот же размер и стиль, но другая
логика. Детектор, который не отличает их, — шум.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import clones  # noqa: E402
import lint  # noqa: E402
import measure  # noqa: E402

# Фикстура лежит JSON-ом, а файлы `.ts`/`.tsx` создаются на месте: публикуемое
# дерево (`publish.py`, SHIP_EXT) `.ts`/`.tsx` не несёт, и тесты в нём падали бы.
_TMP = tempfile.mkdtemp(prefix="zodchiy-clones-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
FIX = os.path.join(_TMP, "clones_money")
os.makedirs(FIX)
with open(os.path.join(HERE, "fixtures", "clones_money", "files.json"), encoding="utf-8") as _fh:
    for _name, _text in json.load(_fh).items():
        with open(os.path.join(FIX, _name), "w", encoding="utf-8") as _out:
            _out.write(_text)
COPIES = ["MoneyLabel.tsx", "Total.tsx", "money_a.ts", "money_b.ts"]
LOOKALIKE = "duration.ts"
ALL = sorted(COPIES + [LOOKALIKE])

need_ts = unittest.skipUnless(lint.detect_tools()["tree_sitter"]["available"], "tree-sitter не установлен")


@need_ts
class TestClones(unittest.TestCase):
    def test_clones_finds_renamed_copies(self):
        res = clones.find_clones(FIX, ALL)
        self.assertTrue(res["available"])
        self.assertEqual(res["window"], 40)
        biggest = max(res["groups"], key=lambda g: g["size"])
        self.assertEqual(biggest["size"], 4)
        self.assertEqual(sorted(biggest["files"]), COPIES)
        for p in res["pairs"]:
            self.assertGreaterEqual(p["shared_windows"], 2)
            self.assertEqual(len(p["lines_a"]), 2)
            self.assertLessEqual(p["lines_a"][0], p["lines_a"][1])
        self.assertEqual(res["pairs_total"], 6)  # C(4, 2)

    def test_clones_skips_lookalike(self):
        res = clones.find_clones(FIX, ALL)
        in_groups = {f for g in res["groups"] for f in g["files"]}
        self.assertNotIn(LOOKALIKE, in_groups)
        self.assertFalse(any(LOOKALIKE in (p["a"], p["b"]) for p in res["pairs"]))

    def test_clones_truncation_reported(self):
        res = clones.find_clones(FIX, ALL)
        self.assertGreaterEqual(res["pairs_total"], len(res["pairs"]))
        with tempfile.TemporaryDirectory() as d:
            names = []
            for i in range(12):  # 12 копий = 66 пар > лимита 40
                names.append(f"m{i}.ts")
                shutil.copy(os.path.join(FIX, "money_a.ts"), os.path.join(d, names[-1]))
            big = clones.find_clones(d, names)
        self.assertEqual(big["pairs_total"], 66)
        self.assertEqual(len(big["pairs"]), clones.MAX_PAIRS)
        self.assertEqual(big["groups"][0]["size"], 12)

    def test_clones_window_and_min_shared_are_respected(self):
        # окно длиннее любой копии → общих окон нет вовсе
        res = clones.find_clones(FIX, ALL, window=5000)
        self.assertEqual(res["pairs_total"], 0)
        self.assertEqual(res["groups"], [])
        # порог общих окон выше их числа → пар нет
        res = clones.find_clones(FIX, ALL, min_shared=5000)
        self.assertEqual(res["pairs_total"], 0)

    def test_clones_python_and_cyrillic_path(self):
        src = (
            "def total(items, rate):\n"
            "    acc = 0\n"
            "    for item in items:\n"
            "        if item.price > 0 and item.qty > 0:\n"
            "            acc += item.price * item.qty * rate\n"
            "        else:\n"
            "            acc -= 1\n"
            "    return round(acc, 2)\n"
        )
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "тест репо")
            os.makedirs(repo)
            renamed = src.replace("acc", "sum_").replace("item", "row")
            for name, text in (("a.py", src), ("b.py", renamed)):
                with open(os.path.join(repo, name), "w", encoding="utf-8") as fh:
                    fh.write(text)
            res = clones.find_clones(repo, ["a.py", "b.py"], window=20)
        self.assertEqual(res["pairs"][0]["a"], "a.py")
        self.assertEqual(res["groups"][0]["files"], ["a.py", "b.py"])

    def test_clones_ignores_unsupported_extensions(self):
        res = clones.find_clones(FIX, ["duration.ts", "README.md", "x.go"])
        self.assertTrue(res["available"])
        self.assertEqual(res["pairs_total"], 0)


class TestClonesDegraded(unittest.TestCase):
    def test_clones_unavailable_without_tree_sitter(self):
        class NoParsers:
            ok = False

        with mock.patch.object(clones, "Parsers", NoParsers):
            res = clones.find_clones(FIX, ALL)
        self.assertIs(res["available"], False)
        self.assertIn("tree-sitter", res["reason"])
        self.assertEqual(res["pairs"], [])
        self.assertEqual(res["pairs_total"], 0)

    def test_lint_has_clones_even_without_ruff(self):
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict(os.environ, {"PATH": empty}):
                res = lint.run_lint(FIX, ALL, set())
        self.assertIs(res["available"], False)
        self.assertEqual(res["reason"], "ruff not found")
        self.assertIn("clones", res)
        if lint.detect_tools()["tree_sitter"]["available"]:
            self.assertTrue(res["clones"]["available"])
            self.assertEqual(res["clones"]["pairs_total"], 6)


def _behavior():
    return {
        "available": True,
        "commits_with_code": 100,
        "churn_profile": {"code": 90, "test": 40},
        "temporal_coupling_total": 5,
        "containment": {"commits_considered": 100},
    }


def _structure():
    return {"parser": {"backends": {"tree-sitter": 100}}, "files": 100, "edges": 140}


@need_ts
class TestClonesControl(unittest.TestCase):
    def _check(self, calib):
        return next(c for c in calib["checks"] if c["check"] == "детектор клонов различает")

    def test_calibration_control_passes(self):
        calib = measure.calibrate(_behavior(), _structure())
        self.assertTrue(self._check(calib)["passed"], self._check(calib))
        self.assertNotIn("clones", calib["blocked_metrics"])

    def test_calibration_blocks_clones_when_control_copy_missed(self):
        # детектор «ослеп»: не находит даже контрольные копии
        blind = {"available": True, "window": 40, "pairs": [], "pairs_total": 0, "groups": []}
        with mock.patch.object(clones, "find_clones", return_value=blind):
            calib = measure.calibrate(_behavior(), _structure())
        self.assertFalse(self._check(calib)["passed"])
        self.assertIn("clones", calib["blocked_metrics"])

    def test_calibration_blocks_clones_when_lookalike_matched(self):
        # детектор «слипает»: зовёт клоном всё со всем
        def everything(repo, files, **kw):
            pairs = [{"a": files[0], "b": f, "shared_windows": 9, "lines_a": [1, 2], "lines_b": [1, 2]} for f in files[1:]]
            return {"available": True, "window": 40, "pairs": pairs, "pairs_total": len(pairs),
                    "groups": [{"files": sorted(files), "size": len(files)}]}

        with mock.patch.object(clones, "find_clones", side_effect=everything):
            calib = measure.calibrate(_behavior(), _structure())
        self.assertIn("clones", calib["blocked_metrics"])

    def test_calibration_unavailable_detector_does_not_block(self):
        # ни одной грамматики: метрику нечем проверить, но и обвинить не в чем
        off = {"available": False, "reason": "tree-sitter not installed", "window": 40,
               "pairs": [], "pairs_total": 0, "groups": []}
        with mock.patch.object(clones, "find_clones", return_value=off):
            calib = measure.calibrate(_behavior(), _structure())
        self.assertTrue(calib["passed"])
        self.assertNotIn("clones", calib["blocked_metrics"])
        self.assertIn("tree-sitter not installed", self._check(calib)["detail"])
        self.assertEqual(measure.confidence_ceiling(_behavior(), _structure(), calib)["ceiling"], "verdict")

    def test_calibration_ts_grammar_missing_python_available(self):
        real_get = clones.Parsers.get

        def no_ts(self, lang):
            return None if lang in ("typescript", "tsx") else real_get(self, lang)

        with mock.patch.object(clones.Parsers, "get", no_ts):
            calib = measure.calibrate(_behavior(), _structure())
        self.assertTrue(calib["passed"], calib)
        self.assertEqual(calib["blocked_metrics"], [])
        self.assertTrue(self._check(calib)["passed"])
        # причина зафиксирована, а не проглочена
        self.assertIn("typescript", self._check(calib)["detail"])
        self.assertIn("python", self._check(calib)["detail"])
        self.assertEqual(measure.confidence_ceiling(_behavior(), _structure(), calib)["ceiling"], "verdict")

    def test_find_clones_reports_missing_grammars(self):
        with mock.patch.object(clones.Parsers, "get", lambda self, lang: None):
            res = clones.find_clones(FIX, ALL)
        self.assertIs(res["available"], False)
        self.assertIn("typescript", res["reason"])
        self.assertIn("tsx", res["reason"])

    def test_find_clones_partial_grammars_stay_available(self):
        real_get = clones.Parsers.get
        no_tsx = lambda self, lang: None if lang == "tsx" else real_get(self, lang)  # noqa: E731
        with mock.patch.object(clones.Parsers, "get", no_tsx):
            res = clones.find_clones(FIX, ALL)
        self.assertTrue(res["available"])
        self.assertEqual(res["grammars_missing"], ["tsx"])
        self.assertEqual(res["files_skipped"], 2)

    def test_calibration_blind_python_still_blocks_when_ts_unavailable(self):
        # доступный, но слепой детектор блокирует метрику, даже если TS недоступен
        def fake(repo, files, **kw):
            if any(f.endswith(".ts") for f in files):
                return {"available": False, "reason": "tree-sitter grammars missing: typescript",
                        "window": 40, "pairs": [], "pairs_total": 0, "groups": []}
            return {"available": True, "window": 40, "pairs": [], "pairs_total": 0, "groups": []}

        with mock.patch.object(clones, "find_clones", side_effect=fake):
            calib = measure.calibrate(_behavior(), _structure())
        self.assertFalse(calib["passed"])
        self.assertIn("clones", calib["blocked_metrics"])
        self.assertIn("python", self._check(calib)["detail"])

    def test_control_covers_python_and_typescript(self):
        status, detail = clones.control()
        self.assertEqual(status, "ok", detail)
        self.assertIn("python", detail)
        self.assertIn("typescript", detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
