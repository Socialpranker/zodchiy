#!/usr/bin/env python3
"""Юнит-слой `prune`: статусы кандидатов, маркеры, контрольная группа, снятие.

Главная пара «нарушение / похожий невиновный»: `dead_plain` и `by_name` оба
без вызовов в коде, но `by_name` зовут по строке из реестра. Режим, который
отдаёт их одним статусом, удалит живой код.
"""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import prune  # noqa: E402

need_vulture = unittest.skipUnless(shutil.which("vulture"), "vulture не установлен")

APP = textwrap.dedent('''\
    """Сервис."""

    import os


    def live():
        """Зовётся из main."""
        return 1


    def dead_plain():
        return 2


    def by_name():
        return 3


    def early():
        return 4
        print("never")


    class _Router:
        def get(self, path):
            return lambda f: f


    router = _Router()


    @router.get("/x")
    def handler():
        return 5


    class Box:
        def hook(self):
            return 6


    def main():
        live()
        early()
        return Box


    main()
''')

REGISTRY = 'HANDLERS = {"cmd": "by_name"}\n'


def make_repo(d: str) -> str:
    repo = os.path.join(d, "repo")
    os.makedirs(repo)
    with open(os.path.join(repo, "app.py"), "w") as fh:
        fh.write(APP)
    with open(os.path.join(repo, "registry.py"), "w") as fh:
        fh.write(REGISTRY)
    with open(os.path.join(repo, "orphan.py"), "w") as fh:
        fh.write('"""Никто не импортирует."""\nfrom __future__ import annotations\n\nX = 1\n')
    # скрипт без __main__ — тоже без входящих рёбер, но его запускают руками
    with open(os.path.join(repo, "shot.py"), "w") as fh:
        fh.write("import sys\n\nwith open(sys.argv[1]) as fh:\n    print(fh.read())\n")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}
    for cmd in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
        subprocess.run(["git", "-C", repo, *cmd], check=True, env=env, capture_output=True)
    return repo


MEASURE = {"structure": {"adjacency": {"app.py": [], "registry.py": []}}}


def by_name(state: dict) -> dict:
    return {(c["name"] or c["kind"], c["file"]): c for c in state["candidates"]}


@need_vulture
class TestScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)
        self.state = prune.scan(self.repo, MEASURE, 0)
        self.c = by_name(self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def test_plain_dead_is_provable(self):
        self.assertEqual(self.c[("dead_plain", "app.py")]["status"], "provable")
        self.assertEqual(self.c[("dead_plain", "app.py")]["blind_spots"], [])

    def test_string_reference_is_not_provable(self):
        row = self.c[("by_name", "app.py")]
        self.assertEqual(row["status"], "suspected")
        self.assertIn("string_reference", row["blind_spots"])

    def test_decorator_and_hook_are_suspected(self):
        self.assertIn("decorator_registration", self.c[("handler", "app.py")]["blind_spots"])
        self.assertIn("framework_hook_possible", self.c[("hook", "app.py")]["blind_spots"])

    def test_unreachable_is_provable_but_not_instrumentable(self):
        row = self.c[("unreachable", "app.py")]
        self.assertEqual(row["status"], "provable")
        self.assertFalse(row["instrumentable"])

    def test_orphan_module_found_entry_points_skipped(self):
        self.assertEqual(self.c[("module", "orphan.py")]["status"], "provable")
        self.assertNotIn(("live", "app.py"), self.c)

    def test_flat_import_beats_empty_graph(self):
        # в MEASURE рёбер нет, как у structure.py на плоских импортах соседей
        with open(os.path.join(self.repo, "helper.py"), "w") as fh:
            fh.write("def h():\n    return 1\n")
        with open(os.path.join(self.repo, "runner.py"), "w") as fh:
            fh.write("import helper\n\nhelper.h()\n")
        c = by_name(prune.scan(self.repo, MEASURE, 0))
        self.assertNotIn(("module", "helper.py"), c)
        self.assertIn(("module", "runner.py"), c)  # контроль: сам runner никто не импортирует

    def test_standalone_script_is_manual(self):
        row = self.c[("module", "shot.py")]
        self.assertIn("standalone_script", row["blind_spots"])
        self.assertEqual(row["status"], "manual")

    def test_age_from_git(self):
        self.assertEqual(self.c[("dead_plain", "app.py")]["last_touched_days"], 0)

    def test_without_measure_orphans_declared(self):
        state = prune.scan(self.repo, None, 0)
        self.assertFalse(any(c["kind"] == "module" for c in state["candidates"]))
        self.assertTrue(any("orphan_modules" in d for d in state["degraded"]))


class TestScanDegraded(unittest.TestCase):
    def test_no_vulture_is_declared(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d)
            with mock.patch.object(prune.shutil, "which", return_value=None):
                state = prune.scan(repo, MEASURE, 0)
        self.assertTrue(any("vulture not found" in x for x in state["degraded"]))
        self.assertTrue(all(c["kind"] == "module" for c in state["candidates"]))


class TestInsertionPoint(unittest.TestCase):
    def test_after_docstring(self):
        src = 'def f():\n    """Док."""\n    return 1\n'
        self.assertEqual(prune.insertion_point(src, "function", 1, "f"), (2, "    "))

    def test_docstring_only_body(self):
        src = 'def f():\n    """Док."""\n'
        self.assertEqual(prune.insertion_point(src, "function", 1, "f"), (2, "    "))

    def test_one_liner_refused(self):
        self.assertIsNone(prune.insertion_point("def f(): return 1\n", "function", 1, "f"))

    def test_decorated_line_and_nested_decorator(self):
        src = "@d\ndef f():\n    @g\n    def inner():\n        pass\n    return inner\n"
        # vulture называет строку декоратора; маркер встаёт над декоратором inner
        self.assertEqual(prune.insertion_point(src, "function", 1, "f"), (2, "    "))

    def test_module_after_future(self):
        src = '"""Док."""\nfrom __future__ import annotations\n\nX = 1\n'
        self.assertEqual(prune.insertion_point(src, "module", 1, None), (3, ""))


@need_vulture
class TestInstrumentCollectStrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(self.tmp.name)
        self.state = prune.scan(self.repo, MEASURE, 0)
        with open(os.path.join(self.repo, "app.py")) as fh:
            self.original = fh.read()

    def tearDown(self):
        self.tmp.cleanup()

    def instrumented(self):
        state, warnings, fatal = prune.instrument(self.state, "app.py:live", None)
        self.assertIsNone(fatal)
        return state

    def test_markers_inserted_and_code_parses(self):
        state = self.instrumented()
        with open(os.path.join(self.repo, "app.py")) as fh:
            src = fh.read()
        ast.parse(src)
        self.assertEqual(src.count(prune.CONTROL), 1)
        ids = {c["id"] for c in state["candidates"] if c["status"] == "instrumented"}
        self.assertTrue(ids)
        for i in ids:
            self.assertIn(f"{prune.HIT} {i}\"", src + _read(self.repo, "orphan.py"))
        # provable не метятся: их удаляет проба, а не лог
        dead = next(c for c in state["candidates"] if c["name"] == "dead_plain")
        self.assertEqual(dead["status"], "provable")

    def test_marker_actually_logs(self):
        self.instrumented()
        proc = subprocess.run([sys.executable, "-c", "import app"], cwd=self.repo,
                              capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": self.repo})
        # main() зовёт live — контрольный маркер обязан сработать
        self.assertIn(prune.CONTROL, proc.stderr)

    def test_control_must_exist_and_be_live(self):
        _, _, fatal = prune.instrument(self.state, "app.py:nope", None)
        self.assertIn("нет в", fatal)
        _, _, fatal = prune.instrument(self.state, "app.py:dead_plain", None)
        self.assertIn("сам в кандидатах", fatal)
        self.assertEqual(_read(self.repo, "app.py"), self.original)

    def test_second_instrument_refused(self):
        state = self.instrumented()
        _, _, fatal = prune.instrument(state, "app.py:live", None)
        self.assertIn("уже стоят", fatal)

    def test_short_window_gives_no_verdict(self):
        state = self.instrumented()
        log = f"{prune.CONTROL}\n" * 3
        state = prune.collect(state, [log], min_control=20)
        self.assertFalse(state["window"]["sufficient"])
        self.assertNotIn("never_fired", state["summary"])

    def test_enough_control_splits_alive_and_never_fired(self):
        state = self.instrumented()
        marked = [c for c in state["candidates"] if c["status"] == "instrumented"]
        alive_id = marked[0]["id"]
        log = f"{prune.CONTROL}\n" * 25 + f"WARNING {prune.HIT} {alive_id}\n"
        state = prune.collect(state, [log], min_control=20)
        rows = {c["id"]: c for c in state["candidates"]}
        self.assertEqual(rows[alive_id]["status"], "alive")
        self.assertEqual(rows[alive_id]["hits"], 1)
        for c in marked[1:]:
            self.assertEqual(rows[c["id"]]["status"], "never_fired")

    def test_hit_ids_do_not_bleed(self):
        # срабатывание P10 не засчитывается за P1
        state = self.instrumented()
        target = next(c for c in state["candidates"] if c["status"] == "instrumented")
        log = f"{prune.CONTROL}\n" * 25 + f"{prune.HIT} {target['id']}0\n"
        state = prune.collect(state, [log], min_control=20)
        row = next(c for c in state["candidates"] if c["id"] == target["id"])
        self.assertEqual((row["hits"], row["status"]), (0, "never_fired"))

    def test_strip_restores_tree(self):
        self.instrumented()
        removed = prune.strip(self.repo)
        self.assertGreater(removed, 1)
        self.assertEqual(_read(self.repo, "app.py"), self.original)
        diff = subprocess.run(["git", "-C", self.repo, "status", "--porcelain"],
                              capture_output=True, text=True).stdout
        self.assertEqual(diff, "")


def _read(repo: str, rel: str) -> str:
    with open(os.path.join(repo, rel)) as fh:
        return fh.read()


class TestEntrypoint(unittest.TestCase):
    def test_route_registered(self):
        sys.path.insert(0, SKILL)
        import zodchiy
        self.assertIn("prune", zodchiy.ROUTES)


if __name__ == "__main__":
    unittest.main()
