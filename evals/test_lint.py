#!/usr/bin/env python3
"""Юнит-слой `lint` и `tools`: счётчики групп ruff, деградация, поле в замере.

Пары «нарушение / похожий невиновный» лежат в `fixtures/lint_py`: `a.py` и
`b.py` делают одно и то же, но `b.py` написан чисто. Метрика, которая не
отличает их, — шум.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, os.path.join(HERE, "fixtures"))

import build as fixtures_build  # noqa: E402
import lint  # noqa: E402

FIX = os.path.join(HERE, "fixtures")
need_ruff = unittest.skipUnless(shutil.which("ruff"), "ruff не установлен")


def copy_fixture(name: str, into: str) -> str:
    dst = os.path.join(into, name)
    shutil.copytree(os.path.join(FIX, name), dst)
    return dst


def by_file(res: dict) -> dict:
    return {r["file"]: r for r in res["files"]}


@need_ruff
class TestLintCounts(unittest.TestCase):
    def test_lint_counts_groups(self):
        with tempfile.TemporaryDirectory() as d:
            repo = copy_fixture("lint_py", d)
            res = lint.run_lint(repo, ["a.py", "b.py", "p.py"], set())
        self.assertTrue(res["available"])
        self.assertEqual(res["files_checked"], 3)
        rows = by_file(res)
        self.assertGreaterEqual(rows["a.py"]["simplicity"], 1)
        self.assertGreaterEqual(rows["a.py"]["width"], 1)
        for g in ("simplicity", "width", "perf"):
            self.assertEqual(rows["b.py"][g], 0, g)
        self.assertGreaterEqual(rows["p.py"]["perf"], 1)
        self.assertEqual(rows["p.py"]["simplicity"], 0)
        self.assertEqual(set(res["rules"]), {"simplicity", "width", "perf"})
        self.assertTrue(res["tool_version"])

    def test_lint_percentiles_and_hot(self):
        with tempfile.TemporaryDirectory() as d:
            repo = copy_fixture("lint_py", d)
            res = lint.run_lint(repo, ["a.py", "b.py", "p.py"], {"a.py"})
        rows = by_file(res)
        self.assertTrue(rows["a.py"]["hot"])
        self.assertFalse(rows["b.py"]["hot"])
        self.assertGreater(rows["a.py"]["simplicity_pct"], rows["b.py"]["simplicity_pct"])

    def test_lint_no_python(self):
        with tempfile.TemporaryDirectory() as d:
            # .ts не входит в публикуемые расширения — файл создаём на месте
            with open(os.path.join(d, "x.ts"), "w") as fh:
                fh.write("export const isReady = (x: boolean): boolean => x;\n")
            res = lint.run_lint(d, ["x.ts"], set())
        self.assertTrue(res["available"])
        self.assertEqual(res["files_checked"], 0)
        self.assertEqual(res["files"], [])
        # «нечего проверять» ≠ «чисто»: пометка обязана быть явной
        self.assertIn("нечего проверять", res["note"])

    def test_lint_cyrillic_path_with_space(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "тест репо")
            shutil.copytree(os.path.join(FIX, "lint_py"), repo)
            res = lint.run_lint(repo, ["a.py", "b.py"], set())
        self.assertGreaterEqual(by_file(res)["a.py"]["simplicity"], 1)

    def test_lint_symlinked_repo(self):
        with tempfile.TemporaryDirectory() as d:
            real = copy_fixture("lint_py", d)
            link = os.path.join(d, "link")
            os.symlink(real, link)
            res = lint.run_lint(link, ["a.py"], set())
        self.assertGreaterEqual(by_file(res)["a.py"]["simplicity"], 1)


class TestLintDegraded(unittest.TestCase):
    def test_lint_ruff_missing(self):
        with tempfile.TemporaryDirectory() as empty, tempfile.TemporaryDirectory() as d:
            repo = copy_fixture("lint_py", d)
            with mock.patch.dict(os.environ, {"PATH": empty}):
                res = lint.run_lint(repo, ["a.py"], set())
        self.assertIs(res["available"], False)
        self.assertEqual(res["reason"], "ruff not found")

    def test_detect_tools_shape(self):
        tools = lint.detect_tools()
        self.assertEqual(set(tools), {"git", "tree_sitter", "ruff", "semgrep"})
        for name, t in tools.items():
            self.assertIn("available", t, name)
            self.assertIn("version", t, name)
        self.assertIs(tools["git"]["available"], True)

    def test_detect_tools_missing(self):
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict(os.environ, {"PATH": empty}):
                tools = lint.detect_tools()
        self.assertEqual(tools["ruff"], {"available": False, "version": None})
        self.assertEqual(tools["semgrep"], {"available": False, "version": None})


@need_ruff
class TestSemgrep(unittest.TestCase):
    def _run(self, repo, path_dir):
        env = {"PATH": path_dir + os.pathsep + os.path.dirname(shutil.which("ruff"))}
        with mock.patch.dict(os.environ, env):
            return lint.run_lint(repo, ["p.py"], set(), semgrep=True)

    def _fake(self, bindir, script):
        fake = os.path.join(bindir, "semgrep")
        with open(fake, "w") as fh:
            fh.write(script)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)

    def test_semgrep_off_has_no_key(self):
        with tempfile.TemporaryDirectory() as d:
            repo = copy_fixture("lint_py", d)
            res = lint.run_lint(repo, ["p.py"], set())
        self.assertNotIn("perf_semgrep", res)

    def test_semgrep_missing_is_reported(self):
        with tempfile.TemporaryDirectory() as empty, tempfile.TemporaryDirectory() as d:
            repo = copy_fixture("lint_py", d)
            res = self._run(repo, empty)
        self.assertIs(res["perf_semgrep"]["available"], False)
        self.assertEqual(res["perf_semgrep"]["reason"], "semgrep not found")
        self.assertTrue(res["available"], "ruff-часть не должна страдать от semgrep")

    def test_semgrep_failure_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as bindir, tempfile.TemporaryDirectory() as d:
            repo = copy_fixture("lint_py", d)
            self._fake(bindir, "#!/bin/sh\necho 'network unreachable' >&2\nexit 2\n")
            res = self._run(repo, bindir)
        self.assertIs(res["perf_semgrep"]["available"], False)
        self.assertIn("network unreachable", res["perf_semgrep"]["reason"])

    def test_semgrep_counts_only_performance_rules(self):
        payload = {
            "results": [
                {"path": "p.py", "extra": {"metadata": {"category": "performance"}}},
                {"path": "p.py", "extra": {"metadata": {"category": "security"}}},
            ]
        }
        with tempfile.TemporaryDirectory() as bindir, tempfile.TemporaryDirectory() as d:
            repo = copy_fixture("lint_py", d)
            self._fake(bindir, "#!/bin/sh\necho '%s'\n" % json.dumps(payload))
            res = self._run(repo, bindir)
        self.assertTrue(res["perf_semgrep"]["available"])
        self.assertEqual(res["perf_semgrep"]["files"], {"p.py": 1})


class TestMeasureHasTools(unittest.TestCase):
    def test_measure_has_tools(self):
        repo = fixtures_build.build("cycle-runtime")
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "measure.json")
            proc = subprocess.run(
                [sys.executable, os.path.join(SKILL, "scripts", "measure.py"), repo, "--out", out],
                capture_output=True,
                text=True,
                timeout=900,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[-400:])
            with open(out, encoding="utf-8") as fh:
                m = json.load(fh)
        self.assertIs(m["tools"]["git"]["available"], True)
        self.assertIn("lint", m)
        self.assertIn("available", m["lint"])
        # lint — слепая зона отчёта, а не причина опускать потолок всего прогона
        if not m["lint"]["available"]:
            self.assertTrue(m["lint"].get("reason"), "lint недоступен без причины")
        self.assertFalse(
            any("lint" in r for r in m["confidence"]["reasons"]),
            "отсутствие ruff не должно опускать потолок находок по другим осям",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
