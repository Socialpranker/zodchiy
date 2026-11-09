#!/usr/bin/env python3
"""Юнит-слой `trial`: проба варианта в worktree и приёмка кодом.

Репо строим в каталоге `тест репо` (кириллица + пробел). «Тесты проекта» в
фикстуре — крошечный `python -m unittest`, а не настоящий pytest. Токены ветвей
и worktree создаём только в tmp-репо, поэтому в `~/.claude` остатков нет.
"""

from __future__ import annotations

import atexit
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import common  # noqa: E402
import ledger  # noqa: E402
import lint  # noqa: E402
import trial  # noqa: E402

# Вердикт trial сравнивает метрики замера, а без tree-sitter замер их не даёт.
need_ts = unittest.skipUnless(lint.detect_tools()["tree_sitter"]["available"], "tree-sitter не установлен")

DAY = 86400
PY = sys.executable

# Функция с 8 ветками: цикломатика 9.
A_ORIG = (
    "def f(x):\n"
    + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(8))
    + "    return 0\n"
)
# Без ветвлений: цикломатика 1; поведение то же.
A_GOOD = "def f(x):\n    return x if x in range(8) else 0\n"
# Ветвей стало 5: цикломатика 7, цель (3) не достигнута; поведение то же.
A_MISS = (
    "def f(x):\n"
    + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(5))
    + ("    return x if x in range(8) else 0\n")
)
# Цикломатика 1, но поведение сломано.
A_BROKEN = "def f(x):\n    return -1\n"
# Ссылка на несуществующее имя: ловит только F821.
A_NAMEERR = "def f(x):\n    return helper(x)\n"

TEST_A = (
    "import unittest\n"
    "from src.a import f\n\n\n"
    "class T(unittest.TestCase):\n"
    "    def test_f(self):\n"
    "        self.assertEqual(f(3), 3)\n"
    "        self.assertEqual(f(99), 0)\n\n\n"
    "if __name__ == '__main__':\n"
    "    unittest.main()\n"
)

FINDING = {
    "id": "F1",
    "source": "structure.complex_files[file=src/a.py].cyclomatic_per_function_max",
    "gain_metric": "structure.complex_files[file=src/a.py].cyclomatic_per_function_max",
    "gain_target": "3",
    "gain_direction": "down",
}
MANUAL = {
    "id": "F1",
    "source": "structure.complex_files[file=src/a.py].loc",
    "gain_metric": "manual",
}

UNITTEST = {
    "name": "unittest",
    "cmd": [PY, "-m", "unittest", "-q"],
    "needs_docker": False,
}


def git(repo, *args, **kw):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.test",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.test",
        **kw.pop("env", {}),
    }
    return subprocess.run(
        ["git", "-C", repo, *args], check=True, capture_output=True, text=True, env=env
    ).stdout


def make_repo(root: str, name: str = "тест репо") -> str:
    """Четыре фикс-эпизода по src/a.py за год: боль 1 файл в квартал."""
    repo = os.path.join(root, name)
    os.makedirs(os.path.join(repo, "src"))
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", "-q", repo], check=True
    )
    now = int(time.time())
    for n, days_ago in enumerate((300, 200, 100, 30)):
        path = os.path.join(repo, "src", "a.py")
        if n == 0:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(A_ORIG)
            with open(os.path.join(repo, "test_a.py"), "w", encoding="utf-8") as fh:
                fh.write(TEST_A)
            with open(
                os.path.join(repo, "pyproject.toml"), "w", encoding="utf-8"
            ) as fh:
                fh.write("[project]\nname = 'mini'\n")
        else:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"# правка {n}\n")
        stamp = f"{now - days_ago * DAY} +0000"
        git(repo, "add", "-A")
        git(
            repo,
            "commit",
            "-q",
            "-m",
            f"fix: эпизод {n}",
            env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
        )
    return repo


_TEMPLATE: list[str] = []


def copy_repo(root: str, name: str = "тест репо") -> str:
    """Копия готового репо: историю строим один раз на модуль, а не на каждый тест
    (десятки `git commit` на тест утяжеляли сборку публичного дерева, где этот набор
    гоняется вложенно)."""
    if not _TEMPLATE:
        tpl = tempfile.mkdtemp(prefix="zodchiy-trial-tpl-")
        atexit.register(shutil.rmtree, tpl, ignore_errors=True)
        make_repo(tpl, name)
        _TEMPLATE.append(tpl)
    dst = os.path.join(root, name)
    shutil.copytree(os.path.join(_TEMPLATE[0], name), dst, symlinks=True)
    return dst


def write(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


class TrialBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = copy_repo(self._tmp.name)

    def prep(self, slug="split-f", finding_id="F1"):
        info = trial.prepare(self.repo, finding_id, slug)
        self.addCleanup(trial.cleanup, self.repo, info["worktree"], False)
        return info


class TestDirtyIgnoresRunDir(TrialBase):
    """Каталог прогона `.zodchiy/` не делает чистое дерево грязным, всё прочее — делает."""

    def behavior_worktree(self):
        r = subprocess.run(
            [sys.executable, os.path.join(SKILL, "scripts", "behavior.py"), self.repo],
            capture_output=True, text=True, check=True,
        )
        return json.loads(r.stdout)["snapshot"]["worktree"]

    def test_clean_tree_with_run_dir_is_not_dirty(self):
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "")
        write(os.path.join(self.repo, ".zodchiy", "run-x", "file"), "x\n")
        self.assertIn(".zodchiy", git(self.repo, "status", "--porcelain"))  # git его видит
        self.assertFalse(common.worktree_dirty(self.repo))
        self.assertIs(self.prep()["base_dirty"], False)
        self.assertEqual(self.behavior_worktree(), "clean")

    def test_other_untracked_file_is_dirty_even_next_to_run_dir(self):
        write(os.path.join(self.repo, ".zodchiy", "run-x", "file"), "x\n")
        write(os.path.join(self.repo, "wip.py"), "x = 1\n")
        self.assertTrue(common.worktree_dirty(self.repo))
        self.assertIs(self.prep()["base_dirty"], True)
        self.assertEqual(self.behavior_worktree(), "dirty")

    def test_tracked_change_is_dirty(self):
        write(os.path.join(self.repo, "src", "a.py"), A_ORIG + "# правка\n")
        self.assertTrue(common.worktree_dirty(self.repo))

    def test_name_lookalike_is_not_the_run_dir(self):
        write(os.path.join(self.repo, "not.zodchiy", "f"), "x\n")
        write(os.path.join(self.repo, ".zodchiy-old", "f"), "x\n")
        self.assertTrue(common.worktree_dirty(self.repo))


class TestPrepare(TrialBase):
    def test_trial_branch_collision(self):
        old = git(self.repo, "rev-parse", "HEAD~1").strip()
        git(self.repo, "branch", "zodchiy/F1-split-f", old)
        info = self.prep()
        self.assertEqual(info["branch"], "zodchiy/F1-split-f-2")
        self.assertTrue(info["branch_collision"])
        self.assertEqual(info["branch_requested"], "zodchiy/F1-split-f")
        # чужая ветка не тронута
        self.assertEqual(git(self.repo, "rev-parse", "zodchiy/F1-split-f").strip(), old)
        again = self.prep()
        self.assertEqual(again["branch"], "zodchiy/F1-split-f-3")

    def test_prepare_clean_has_no_collision(self):
        info = self.prep()
        self.assertEqual(info["branch"], "zodchiy/F1-split-f")
        self.assertFalse(info["branch_collision"])
        self.assertFalse(info["base_dirty"])
        self.assertEqual(info["base_head"], git(self.repo, "rev-parse", "HEAD").strip())

    def test_worktree_outside_repo_and_user_tree_untouched(self):
        head = git(self.repo, "rev-parse", "HEAD").strip()
        branch = git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        info = self.prep()
        wt = os.path.realpath(info["worktree"])
        self.assertTrue(
            wt.startswith(
                os.path.realpath(os.path.join(tempfile.gettempdir(), "zodchiy-trials"))
                + os.sep
            )
        )
        self.assertFalse(wt.startswith(os.path.realpath(self.repo) + os.sep))
        self.assertTrue(os.path.isfile(os.path.join(wt, "src", "a.py")))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), head)
        self.assertEqual(
            git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip(), branch
        )
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "")

    def test_trial_dirty_base(self):
        write(os.path.join(self.repo, "wip.py"), "x = 1\n")
        write(os.path.join(self.repo, "src", "a.py"), A_ORIG + "# не закоммичено\n")
        info = self.prep()
        self.assertIs(info["base_dirty"], True)
        self.assertFalse(os.path.exists(os.path.join(info["worktree"], "wip.py")))
        with open(
            os.path.join(info["worktree"], "src", "a.py"), encoding="utf-8"
        ) as fh:
            self.assertNotIn("не закоммичено", fh.read())
        # пользовательские правки на месте
        self.assertTrue(os.path.exists(os.path.join(self.repo, "wip.py")))

    def test_node_modules_is_symlinked(self):
        os.makedirs(os.path.join(self.repo, "node_modules", "pkg"))
        write(os.path.join(self.repo, ".gitignore"), "node_modules/\n")
        git(self.repo, "add", ".gitignore")
        git(self.repo, "commit", "-q", "-m", "chore: gitignore")
        info = self.prep()
        link = os.path.join(info["worktree"], "node_modules")
        self.assertTrue(os.path.islink(link))
        self.assertEqual(
            os.path.realpath(link),
            os.path.realpath(os.path.join(self.repo, "node_modules")),
        )
        trial.cleanup(self.repo, info["worktree"], False)
        # симлинк снят, а не пройден насквозь: содержимое основного checkout цело
        self.assertTrue(os.path.isdir(os.path.join(self.repo, "node_modules", "pkg")))


class TestCleanup(TrialBase):
    def test_cleanup_keeps_branch(self):
        info = trial.prepare(self.repo, "F1", "keep")
        trial.cleanup(self.repo, info["worktree"], keep_branch=True)
        self.assertFalse(os.path.exists(info["worktree"]))
        self.assertIn(
            info["branch"], git(self.repo, "branch", "--list", info["branch"])
        )
        self.assertNotIn(info["worktree"], git(self.repo, "worktree", "list"))

    def test_cleanup_drops_branch(self):
        info = trial.prepare(self.repo, "F1", "drop")
        trial.cleanup(self.repo, info["worktree"], keep_branch=False)
        self.assertFalse(os.path.exists(info["worktree"]))
        self.assertEqual(git(self.repo, "branch", "--list", info["branch"]).strip(), "")

    def test_cleanup_refuses_foreign_dir(self):
        foreign = os.path.join(self._tmp.name, "чужая папка")
        os.makedirs(foreign)
        with self.assertRaises(ValueError):
            trial.cleanup(self.repo, foreign, keep_branch=False)
        self.assertTrue(os.path.isdir(foreign))


class TestJudge(TrialBase):
    def judge(self, info, finding, checks, **kw):
        return trial.judge(
            self.repo,
            info["worktree"],
            finding,
            {"id": "v1"},
            checks,
            base_head=info["base_head"],
            **kw,
        )

    @need_ts
    def test_trial_accepts_hit(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        write(
            os.path.join(info["worktree"], "src", "новый.py"), "X = 1\n"
        )  # кириллица в имени
        res = self.judge(info, FINDING, [UNITTEST])
        self.assertEqual(res["status"], "accepted", res)
        self.assertEqual([c["status"] for c in res["checks"]], ["pass"])
        self.assertEqual(res["gain_before"], 9)
        self.assertLessEqual(res["gain_after"], 3)
        self.assertEqual(res["gain_target"], 3)
        self.assertIs(res["gain_hit"], True)
        self.assertEqual(res["remedy_files"], 2)
        self.assertEqual(res["payback"], 2.0)
        self.assertEqual(res["payback_verdict"], "ok")
        # проба не оставила следов в дереве пользователя
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "")
        # абсолютный путь репо в JSON не течёт
        self.assertNotIn(self.repo, json.dumps(res, ensure_ascii=False))

    @need_ts
    def test_trial_rejects_miss(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_MISS)
        res = self.judge(info, FINDING, [UNITTEST])
        self.assertEqual([c["status"] for c in res["checks"]], ["pass"])
        self.assertIs(res["gain_hit"], False)
        self.assertEqual(res["gain_after"], 7)
        self.assertEqual(res["status"], "rejected")

    @need_ts
    def test_trial_rejects_red_tests(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_BROKEN)
        res = self.judge(info, FINDING, [UNITTEST])
        self.assertEqual(res["checks"][0]["status"], "fail")
        self.assertTrue(res["checks"][0]["tail"])
        self.assertIs(
            res["gain_hit"], True
        )  # цель по метрике достигнута, но тесты красные
        self.assertEqual(res["status"], "rejected")

    def test_trial_checks_run_separately(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        red = {
            "name": "red",
            "cmd": [PY, "-c", "import sys; sys.exit(1)"],
            "needs_docker": False,
        }
        green = {"name": "green", "cmd": [PY, "-c", "pass"], "needs_docker": False}
        res = self.judge(info, MANUAL, [red, green])
        self.assertEqual(
            [(c["name"], c["status"]) for c in res["checks"]],
            [("red", "fail"), ("green", "pass")],
        )
        self.assertEqual(res["status"], "rejected")

    def test_trial_timeout_unverified(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        slow = {"name": "slow", "cmd": ["sleep", "60"], "needs_docker": False}
        t0 = time.time()
        res = self.judge(info, MANUAL, [slow], timeout=1)
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(res["checks"][0]["status"], "unverified")
        self.assertEqual(res["checks"][0]["reason"], "timeout")
        self.assertEqual(res["status"], "unverified")

    def test_timeout_does_not_mask_red_check(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        slow = {"name": "slow", "cmd": ["sleep", "60"], "needs_docker": False}
        red = {
            "name": "red",
            "cmd": [PY, "-c", "import sys; sys.exit(3)"],
            "needs_docker": False,
        }
        res = self.judge(info, MANUAL, [slow, red], timeout=1)
        self.assertEqual([c["status"] for c in res["checks"]], ["unverified", "fail"])
        self.assertEqual(res["status"], "rejected")

    @need_ts
    def test_trial_no_tests_unverified(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        res = self.judge(info, FINDING, [])
        self.assertEqual(res["checks"], [])
        self.assertIs(res["gain_hit"], True)
        self.assertEqual(res["status"], "unverified")

    @need_ts
    def test_only_static_checks_is_unverified_not_accepted(self):
        # ruff F821 не проверяет поведение: gain достигнут, но принимать без тестов нельзя (спека §4)
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        static = {"name": "ruff check --select F821 .", "cmd": [PY, "-c", "pass"], "needs_docker": False}
        res = self.judge(info, FINDING, [static])
        self.assertEqual([c["status"] for c in res["checks"]], ["pass"])
        self.assertIs(res["gain_hit"], True)
        self.assertEqual(res["status"], "unverified")
        self.assertIn("no tests found", " ".join(res["reasons"]))
        res = self.judge(info, FINDING, [static, UNITTEST])
        self.assertEqual(res["status"], "accepted", res)

    def test_test_runners_are_recognised(self):
        for cmd in (["pytest", "-q"], ["uv", "run", "pytest"], ["python3", "-m", "pytest"], [PY, "-m", "unittest"],
                    ["npm", "run", "test"], ["pnpm", "test"], ["yarn", "test"], ["npx", "vitest"], ["go", "test", "./..."]):
            self.assertTrue(trial.is_test_check({"cmd": cmd}), cmd)
        for cmd in (["ruff", "check", "."], ["lint-imports"], ["npm", "run", "lint"], ["npm", "run", "build"]):
            self.assertFalse(trial.is_test_check({"cmd": cmd}), cmd)

    @need_ts
    def test_partial_phase_miss_is_unverified_not_rejected(self):
        # architectural: проба покрывает фазу 1, цель находки — вся находка
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_MISS)
        res = self.judge(info, FINDING, [UNITTEST], phase_of="3")
        self.assertIs(res["gain_hit"], False)
        self.assertEqual(res["status"], "unverified", res)
        self.assertIn("фаза 1 из 3: цель находки не применима к фазе", res["reasons"])
        self.assertNotIn("gain target not reached", res["reasons"])

    @need_ts
    def test_partial_phase_hit_is_accepted(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        self.assertEqual(self.judge(info, FINDING, [UNITTEST], phase_of="3")["status"], "accepted")

    def test_partial_phase_still_rejects_red_tests(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_BROKEN)
        res = self.judge(info, FINDING, [UNITTEST], phase_of="?")
        self.assertEqual(res["status"], "rejected")

    @need_ts
    def test_single_phase_or_no_phase_keeps_the_target(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_MISS)
        for phase_of in (None, "1"):
            res = self.judge(info, FINDING, [UNITTEST], phase_of=phase_of)
            self.assertEqual(res["status"], "rejected", phase_of)

    def test_cli_passes_phase_of(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_MISS)
        argv = ["trial.py", "judge", "--repo", self.repo, "--worktree", info["worktree"],
                "--finding-json", json.dumps(FINDING), "--checks-json", json.dumps([UNITTEST]),
                "--base-head", info["base_head"], "--phase-of", "4"]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), mock.patch("sys.stdout", buf):
            trial.main()
        self.assertEqual(json.loads(buf.getvalue())["status"], "unverified")

    def test_manual_gain_is_unverified_not_accepted(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        res = self.judge(info, MANUAL, [UNITTEST])
        self.assertEqual([c["status"] for c in res["checks"]], ["pass"])
        self.assertIsNone(res["gain_after"])
        self.assertIs(res["gain_hit"], False)
        self.assertEqual(res["status"], "unverified")

    def test_snapshot_key_metric(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        write(os.path.join(info["worktree"], "src", "b.py"), "y = 1\n")
        finding = {
            **FINDING,
            "gain_metric": "files",
            "gain_target": "2",
            "gain_direction": "up",
        }
        res = self.judge(info, finding, [UNITTEST])
        self.assertEqual((res["gain_before"], res["gain_after"]), (2, 3))
        self.assertIs(res["gain_hit"], True)

    def test_cold_file_is_rejected(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        cold = {**FINDING, "source": "structure.complex_files[file=src/cold.py].loc"}
        res = self.judge(info, {**cold, "gain_metric": "manual"}, [UNITTEST])
        self.assertEqual(res["payback_verdict"], "cold")
        self.assertIsNone(res["payback"])
        self.assertEqual(res["status"], "rejected")

    def test_too_costly_is_rejected(self):
        info = self.prep()
        for i in range(
            6
        ):  # 6 файлов лечения на боль в 1 файл/квартал → 6 кварталов > 4
            write(os.path.join(info["worktree"], "src", f"n{i}.py"), f"v = {i}\n")
        res = self.judge(info, {**MANUAL}, [UNITTEST])
        self.assertEqual(res["remedy_files"], 6)
        self.assertEqual(res["payback_verdict"], "too_costly")
        self.assertEqual(res["status"], "rejected")

    def test_remedy_files_counts_uncommitted_and_committed(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        git(info["worktree"], "commit", "-q", "-am", "refactor: a")
        write(os.path.join(info["worktree"], "src", "c.py"), "z = 1\n")
        res = self.judge(info, MANUAL, [UNITTEST])
        self.assertEqual(res["remedy_files"], 2)

    @unittest.skipUnless(shutil.which("ruff"), "ruff не установлен")
    def test_f821_check_catches_name_error(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_NAMEERR)
        f821 = {
            "name": "ruff F821",
            "cmd": ["ruff", "check", "--select", "F821", "."],
            "needs_docker": False,
        }
        res = self.judge(info, MANUAL, [f821])
        self.assertEqual(res["checks"][0]["status"], "fail")
        self.assertIn("helper", res["checks"][0]["tail"])


class TestDocker(TrialBase):
    def _fake_docker(self, code: int) -> str:
        d = tempfile.mkdtemp(dir=self._tmp.name)
        path = os.path.join(d, "docker")
        write(path, f"#!/bin/sh\nexit {code}\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return d

    def _marker_check(self, marker: str, docker: bool):
        return {
            "name": "needs-docker",
            "needs_docker": docker,
            "cmd": [PY, "-c", f"open({marker!r}, 'w').close()"],
        }

    def test_trial_docker_missing(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        marker = os.path.join(self._tmp.name, "ran")
        with mock.patch.object(trial.shutil, "which", return_value=None):
            res = trial.judge(
                self.repo,
                info["worktree"],
                MANUAL,
                {"id": "v1"},
                [self._marker_check(marker, True)],
                base_head=info["base_head"],
            )
        self.assertEqual(res["checks"][0]["status"], "unverified")
        self.assertEqual(res["checks"][0]["reason"], "docker unavailable")
        self.assertFalse(
            os.path.exists(marker), "команда не должна запускаться без Docker"
        )

    def test_docker_info_fails_is_unverified(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        marker = os.path.join(self._tmp.name, "ran")
        env = {"PATH": self._fake_docker(1) + os.pathsep + os.environ["PATH"]}
        with mock.patch.dict(os.environ, env):
            res = trial.judge(
                self.repo,
                info["worktree"],
                MANUAL,
                {"id": "v1"},
                [self._marker_check(marker, True)],
                base_head=info["base_head"],
            )
        self.assertEqual(res["checks"][0]["status"], "unverified")
        self.assertEqual(res["checks"][0]["reason"], "docker unavailable")
        self.assertFalse(os.path.exists(marker))

    def test_docker_available_runs_check(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        marker = os.path.join(self._tmp.name, "ran")
        env = {"PATH": self._fake_docker(0) + os.pathsep + os.environ["PATH"]}
        with mock.patch.dict(os.environ, env):
            res = trial.judge(
                self.repo,
                info["worktree"],
                MANUAL,
                {"id": "v1"},
                [self._marker_check(marker, True)],
                base_head=info["base_head"],
            )
        self.assertEqual(res["checks"][0]["status"], "pass")
        self.assertTrue(os.path.exists(marker))

    def test_non_docker_check_ignores_docker(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        marker = os.path.join(self._tmp.name, "ran")
        with mock.patch.object(trial.shutil, "which", return_value=None):
            res = trial.judge(
                self.repo,
                info["worktree"],
                MANUAL,
                {"id": "v1"},
                [self._marker_check(marker, False)],
                base_head=info["base_head"],
            )
        self.assertEqual(res["checks"][0]["status"], "pass")


class TestDiscover(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "тест репо")
        os.makedirs(self.repo)

    def cmds(self):
        return [c["cmd"] for c in trial.discover_checks(self.repo)]

    def test_claude_md_chain_is_split_into_separate_checks(self):
        write(os.path.join(self.repo, "pyproject.toml"), "[project]\nname='x'\n")
        write(
            os.path.join(self.repo, "CLAUDE.md"),
            "# Команды\n\n```bash\nruff check src && lint-imports && pytest -q\n"
            "npm run lint   # линтер\necho привет\n"
            "pytest tests/integration  # нужен docker\n```\n\n```python\npytest не команда\n```\n",
        )
        checks = trial.discover_checks(self.repo)
        self.assertEqual(
            [c["cmd"] for c in checks],
            [
                ["ruff", "check", "src"],
                ["lint-imports"],
                ["pytest", "-q"],
                ["npm", "run", "lint"],
                ["pytest", "tests/integration"],
                ["ruff", "check", "--select", "F821", "."],
            ],
        )
        self.assertEqual(
            [c["needs_docker"] for c in checks],
            [False, False, False, False, True, False],
        )
        self.assertTrue(all(c["name"] for c in checks))

    def test_f821_not_duplicated(self):
        write(os.path.join(self.repo, "pyproject.toml"), "[project]\nname='x'\n")
        write(
            os.path.join(self.repo, "CLAUDE.md"),
            "```bash\nruff check --select F821 .\n```\n",
        )
        self.assertEqual(
            self.cmds().count(["ruff", "check", "--select", "F821", "."]), 1
        )

    def test_pyproject_fallback(self):
        write(
            os.path.join(self.repo, "pyproject.toml"),
            "[tool.ruff]\nline-length = 100\n[tool.pytest.ini_options]\n",
        )
        self.assertEqual(
            self.cmds(),
            [
                ["ruff", "check", "."],
                ["python3", "-m", "pytest", "-q"],
                ["ruff", "check", "--select", "F821", "."],
            ],
        )

    def test_package_json_fallback_has_no_f821(self):
        write(
            os.path.join(self.repo, "package.json"),
            json.dumps(
                {
                    "scripts": {
                        "lint": "eslint .",
                        "test": "vitest",
                        "build": "tsc",
                        "dev": "vite",
                    }
                }
            ),
        )
        self.assertEqual(
            self.cmds(),
            [["npm", "run", "lint"], ["npm", "run", "test"], ["npm", "run", "build"]],
        )

    def test_placeholder_npm_test_is_not_a_check(self):
        write(
            os.path.join(self.repo, "package.json"),
            json.dumps(
                {"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}
            ),
        )
        self.assertEqual(self.cmds(), [])

    def test_nothing_to_run(self):
        self.assertEqual(self.cmds(), [])


# Копия bash-блоков из CLAUDE.md реального проекта: opt-in строки
# с платными живыми вызовами не должны становиться проверками варианта.
PAID_RUNS_CLAUDE_MD = """# Команды

```bash
uv sync --all-groups          # установить зависимости (первый раз)
uv run ruff check .           # линтер (должен быть "All checks passed!")
uv run lint-imports           # гексагон-гейт (должен быть "Contracts: 4 kept, 0 broken.")
uv run pytest                 # все тесты (см. про Docker ниже)
```

Платные прогоны — только вручную:

```bash
APP_LLM_LIVE=1 uv run pytest -m live      # живые вызовы OpenRouter/OpenAI
APP_GOLDEN=1 uv run pytest -m golden -s -v # золотой набор судьи (tests/golden/judge_cases.yaml)
```
"""


class TestDiscoverOptIn(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "тест репо")
        os.makedirs(self.repo)

    def cmds(self):
        return [c["cmd"] for c in trial.discover_checks(self.repo)]

    def test_real_claude_md_has_no_paid_runs(self):
        write(os.path.join(self.repo, "CLAUDE.md"), PAID_RUNS_CLAUDE_MD)
        write(os.path.join(self.repo, "pyproject.toml"), "[project]\nname='x'\n")
        write(os.path.join(self.repo, "uv.lock"), "")
        checks = trial.discover_checks(self.repo)
        self.assertEqual(
            [c["cmd"] for c in checks],
            [
                ["uv", "run", "ruff", "check", "."],
                ["uv", "run", "lint-imports"],
                ["uv", "run", "pytest"],
                ["uv", "run", "ruff", "check", "--select", "F821", "."],
            ],
        )
        self.assertEqual([c["needs_docker"] for c in checks], [False, False, True, False])
        flat = [t for c in checks for t in c["cmd"]]
        for bad in ("env", "live", "golden", "-m"):
            self.assertNotIn(bad, flat)
        self.assertFalse(any("=" in t for t in flat if t != "--select"))

    def test_env_prefixed_line_is_skipped(self):
        write(os.path.join(self.repo, "CLAUDE.md"), "```bash\nFOO=1 pytest -q\npytest -q\n```\n")
        self.assertEqual(self.cmds(), [["pytest", "-q"]])

    def test_marker_selection_is_skipped_but_exclusion_is_kept(self):
        write(
            self.repo + "/CLAUDE.md",
            "```bash\npytest -m e2e\npytest -m 'not live'\npytest -m 'live or golden'\npytest -m unit\n```\n",
        )
        self.assertEqual(self.cmds(), [["pytest", "-m", "not live"], ["pytest", "-m", "unit"]])


class TestUv(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = os.path.join(self._tmp.name, "тест репо")
        os.makedirs(self.repo)

    def cmds(self):
        return [c["cmd"] for c in trial.discover_checks(self.repo)]

    def test_uv_lock_uses_uv_run(self):
        write(os.path.join(self.repo, "pyproject.toml"), "[tool.ruff]\n[tool.pytest.ini_options]\n")
        write(os.path.join(self.repo, "uv.lock"), "")
        self.assertEqual(
            self.cmds(),
            [
                ["uv", "run", "ruff", "check", "."],
                ["uv", "run", "pytest", "-q"],
                ["uv", "run", "ruff", "check", "--select", "F821", "."],
            ],
        )

    def test_tool_uv_section_uses_uv_run(self):
        write(os.path.join(self.repo, "pyproject.toml"), "[tool.uv]\ndev-dependencies = []\n[tool.pytest.ini_options]\n")
        self.assertEqual(self.cmds()[0], ["uv", "run", "pytest", "-q"])

    def test_no_uv_keeps_plain_commands(self):
        write(os.path.join(self.repo, "pyproject.toml"), "[tool.pytest.ini_options]\n")
        self.assertEqual(self.cmds()[0], ["python3", "-m", "pytest", "-q"])


class TestCleanupCommitsVariant(TrialBase):
    def test_keep_branch_commits_uncommitted_variant(self):
        info = trial.prepare(self.repo, "F1", "keepme")
        wt = info["worktree"]
        write(os.path.join(wt, "src", "a.py"), A_GOOD)
        write(os.path.join(wt, "src", "новый.py"), "X = 1\n")
        write(os.path.join(wt, "src", "__pycache__", "a.cpython-313.pyc"), "junk")
        write(os.path.join(wt, ".pytest_cache", "v", "x"), "junk")
        trial.cleanup(self.repo, wt, keep_branch=True)
        self.assertFalse(os.path.exists(wt))
        diff = git(
            self.repo, "-c", "core.quotepath=off", "diff", "--name-only", info["base_head"], info["branch"]
        ).split()
        self.assertEqual(sorted(diff), ["src/a.py", "src/новый.py"])
        self.assertIn("F1", git(self.repo, "log", "-1", "--format=%s", info["branch"]))
        trial.cleanup(self.repo, wt, keep_branch=False)  # идемпотентно, ветку уже не найти по копии
        git(self.repo, "branch", "-D", info["branch"])

    def test_clean_worktree_makes_no_commit(self):
        info = trial.prepare(self.repo, "F1", "clean")
        trial.cleanup(self.repo, info["worktree"], keep_branch=True)
        self.assertEqual(git(self.repo, "rev-parse", info["branch"]).strip(), info["base_head"])
        git(self.repo, "branch", "-D", info["branch"])

    def test_fallback_identity_when_git_has_none(self):
        info = trial.prepare(self.repo, "F1", "noident")
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        env = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": self._tmp.name,
            "XDG_CONFIG_HOME": self._tmp.name,
        }
        with mock.patch.dict(os.environ, env):
            for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
                os.environ.pop(k, None)
            trial.cleanup(self.repo, info["worktree"], keep_branch=True)
        self.assertEqual(git(self.repo, "log", "-1", "--format=%an", info["branch"]).strip(), "zodchiy")
        git(self.repo, "branch", "-D", info["branch"])


class TestProcessGroup(TrialBase):
    def test_timeout_kills_grandchildren(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        pidfile = os.path.join(self._tmp.name, "grandchild.pid")
        # sh стартует мгновенно, в отличие от python под нагрузкой сборки дерева
        code = f"sleep 30 & echo $! > '{pidfile}'; wait"
        check = {"name": "spawner", "cmd": ["sh", "-c", code], "needs_docker": False}
        t0 = time.time()
        res = trial.judge(
            self.repo, info["worktree"], MANUAL, {"id": "v1"}, [check], timeout=2, base_head=info["base_head"]
        )
        # без убийства группы пайпы держит внук, и judge ждал бы его 30 с
        self.assertLess(time.time() - t0, 20)
        self.assertEqual(res["checks"][0]["reason"], "timeout")
        with open(pidfile, encoding="utf-8") as fh:
            pid = int(fh.read())

        def alive():
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            return True

        def reap():
            if alive():
                os.kill(pid, 9)

        self.addCleanup(reap)
        for _ in range(40):  # SIGKILL доставляется не мгновенно
            if not alive():
                break
            time.sleep(0.1)
        self.assertFalse(alive(), "внук пережил таймаут")

    def test_os_error_is_unverified_not_a_crash(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        script = os.path.join(self._tmp.name, "noexec.sh")
        write(script, "#!/bin/sh\nexit 0\n")
        os.chmod(script, 0o644)
        check = {"name": "noexec", "cmd": [script], "needs_docker": False}
        res = trial.judge(self.repo, info["worktree"], MANUAL, {"id": "v1"}, [check], base_head=info["base_head"])
        self.assertEqual(res["checks"][0]["status"], "unverified")
        self.assertTrue(res["checks"][0]["reason"].startswith("cannot run"))


class TestEmptyAndDirection(TrialBase):
    def test_empty_diff_is_rejected(self):
        info = self.prep()
        res = trial.judge(self.repo, info["worktree"], MANUAL, {"id": "v1"}, [UNITTEST], base_head=info["base_head"])
        self.assertEqual(res["remedy_files"], 0)
        self.assertEqual(res["checks"][0]["status"], "pass")
        self.assertEqual(res["status"], "rejected")
        self.assertIn("empty diff: variant changed nothing", res["reasons"])

    @need_ts
    def test_direction_inferred_down_makes_unchanged_metric_a_miss(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_ORIG + "# косметика\n")
        finding = {k: v for k, v in FINDING.items() if k != "gain_direction"}
        res = trial.judge(self.repo, info["worktree"], finding, {"id": "v1"}, [UNITTEST], base_head=info["base_head"])
        self.assertEqual((res["gain_before"], res["gain_after"], res["gain_target"]), (9, 9, 3))
        self.assertIs(res["gain_hit"], False)
        self.assertEqual(res["status"], "rejected")

    @need_ts
    def test_direction_inferred_down_hit(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        finding = {k: v for k, v in FINDING.items() if k != "gain_direction"}
        res = trial.judge(self.repo, info["worktree"], finding, {"id": "v1"}, [UNITTEST], base_head=info["base_head"])
        self.assertIs(res["gain_hit"], True)
        self.assertEqual(res["status"], "accepted")

    def test_direction_inferred_up_miss(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "b.py"), "y = 1\n")
        finding = {**FINDING, "gain_metric": "files", "gain_target": "9"}
        finding.pop("gain_direction")
        res = trial.judge(self.repo, info["worktree"], finding, {"id": "v1"}, [UNITTEST], base_head=info["base_head"])
        self.assertIs(res["gain_hit"], False)
        self.assertEqual(res["status"], "rejected")


class TestBaseMeasure(TrialBase):
    @need_ts
    def test_gain_before_comes_from_clean_head_not_dirty_tree(self):
        write(os.path.join(self.repo, "src", "a.py"), A_GOOD)  # у пользователя не закоммичено
        info = self.prep()
        self.assertIs(info["base_dirty"], True)
        path = info["measure_before"]
        self.assertTrue(os.path.isfile(path))
        self.assertFalse(os.path.realpath(path).startswith(os.path.realpath(info["worktree"]) + os.sep))
        write(os.path.join(info["worktree"], "src", "a.py"), A_MISS)
        res = trial.judge(self.repo, info["worktree"], FINDING, {"id": "v1"}, [UNITTEST], base_head=info["base_head"])
        self.assertEqual(res["gain_before"], 9)  # HEAD, а не 1 из грязного дерева
        self.assertEqual(res["gain_after"], 7)
        self.assertEqual(res["remedy_files"], 1)  # файл замера в дифф не попал

    def test_cleanup_removes_measure_file(self):
        info = trial.prepare(self.repo, "F1", "mf")
        self.assertTrue(os.path.isfile(info["measure_before"]))
        trial.cleanup(self.repo, info["worktree"], keep_branch=False)
        self.assertFalse(os.path.exists(info["measure_before"]))


class TestUvOwnErrors(unittest.TestCase):
    """`uv run <tool>`: сбой самого uv (инструмента нет, resolve/sync/сеть) — не красный."""

    def test_classifier_recognises_uv_errors_only_for_uv_run(self):
        spawn = b"Using CPython 3.12\nerror: Failed to spawn: `ruff`\n  Caused by: No such file\n"
        self.assertEqual(trial._uv_own_error(["uv", "run", "ruff"], spawn), "error: Failed to spawn: `ruff`")
        self.assertIsNone(trial._uv_own_error(["ruff"], spawn))
        self.assertIsNone(trial._uv_own_error(["uv", "sync"], spawn))
        for text in (
            b"error: No solution found when resolving dependencies:\n",
            b"error: Failed to download `x==1`\n",
            b"error: Request failed after 3 retries\n",
            b"error: No interpreter found for Python >=3.14\n",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(trial._uv_own_error(["uv", "run", "pytest"], text))

    def test_tool_errors_stay_red(self):
        # ruff на битом синтаксисе варианта печатает `error: Failed to parse` — это его вердикт
        parse = b"error: Failed to parse src/a.py:1:5: Expected an expression\n"
        self.assertIsNone(trial._uv_own_error(["uv", "run", "ruff", "check", "."], parse))
        self.assertIsNone(trial._uv_own_error(["uv", "run", "pytest"], b"1 failed, 2 passed\n"))

    def _project(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        write(os.path.join(tmp.name, "pyproject.toml"),
              '[project]\nname = "m"\nversion = "0"\nrequires-python = ">=3.9"\n')
        return tmp.name

    @unittest.skipUnless(shutil.which("uv"), "uv не установлен")
    def test_missing_tool_under_uv_is_unverified(self):
        res = trial._run_check({"cmd": ["uv", "run", "zzz-no-such-tool"]}, self._project(), 60, [])
        self.assertEqual(res["status"], "unverified", res)
        self.assertTrue(res["reason"].startswith("tool unavailable: error: Failed to spawn"), res)

    @unittest.skipUnless(shutil.which("uv"), "uv не установлен")
    def test_real_tool_failure_under_uv_stays_fail(self):
        res = trial._run_check(
            {"cmd": ["uv", "run", "python", "-c", "import sys; sys.exit(1)"]}, self._project(), 60, []
        )
        self.assertEqual(res["status"], "fail", res)
        self.assertEqual(res["reason"], "exit 1")


class TestBeforeSource(TrialBase):
    def judge(self, info, **kw):
        return trial.judge(self.repo, info["worktree"], FINDING, {"id": "v1"}, [UNITTEST],
                           base_head=info["base_head"], **kw)

    def test_default_is_clean_base(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        res = self.judge(info)
        self.assertEqual(res["before_source"], "clean_base")
        self.assertFalse(any("dirty" in r for r in res["reasons"]))

    def test_given_measure(self):
        info = self.prep()
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        res = self.judge(info, measure_before=info["measure_before"])
        self.assertEqual(res["before_source"], "given")

    def test_repo_as_is_on_dirty_repo_is_reported(self):
        write(os.path.join(self.repo, "src", "a.py"), A_ORIG + "# правка пользователя\n")
        with mock.patch.object(trial, "_measure", return_value=None):
            info = trial.prepare(self.repo, "F1", "nobase")
        self.addCleanup(trial.cleanup, self.repo, info["worktree"], False)
        self.assertIsNone(info["measure_before"])
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        res = self.judge(info)
        self.assertEqual(res["before_source"], "repo_as_is")
        self.assertTrue(any("dirty repo" in r for r in res["reasons"]), res["reasons"])

    def test_repo_as_is_on_clean_repo_has_no_dirty_note(self):
        with mock.patch.object(trial, "_measure", return_value=None):
            info = trial.prepare(self.repo, "F1", "nobase2")
        self.addCleanup(trial.cleanup, self.repo, info["worktree"], False)
        write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
        res = self.judge(info)
        self.assertEqual(res["before_source"], "repo_as_is")
        self.assertFalse(any("dirty" in r for r in res["reasons"]))


class TestDecisionsSchema(unittest.TestCase):
    PATH = os.path.join(SKILL, "schema", "decisions.schema.json")

    def load(self):
        with open(self.PATH, encoding="utf-8") as fh:
            return json.load(fh)

    def sample(self):
        return {
            "run": {
                "head": "512c13c",
                "base_dirty": True,
                "mode": "workflow",
                "date": "2026-09-29",
                "checklist": {"passed": 5, "total": 6},
                "cost": {"tokens": 184320, "agents": 9},
            },
            "findings": [
                {
                    "id": "F1",
                    "class": "quick-fix",
                    "mode": "agents",
                    "variants": [{"id": "v1", "summary": "разрезать функцию"}],
                    "lead": {
                        "chosen": "v1",
                        "reserve": "v2",
                        "reason": "мельче диф",
                        "questions": [],
                        "order": 1,
                        "depends_on": [],
                        "phases": [
                            {
                                "title": "первая фаза",
                                "steps": ["перенести хелперы"],
                                "risk": "цикл импортов",
                            }
                        ],
                    },
                    "trial": {
                        "checks": [
                            {
                                "name": "pytest",
                                "status": "pass",
                                "reason": "",
                                "tail": "",
                            }
                        ],
                        "gain_metric": "files",
                        "gain_before": 3,
                        "gain_after": 2,
                        "gain_target": 2,
                        "gain_hit": True,
                        "remedy_files": 2,
                        "payback": 0.5,
                        "payback_verdict": "ok",
                        "status": "accepted",
                        "branch": "zodchiy/F1-x",
                        "branch_collision": False,
                    },
                }
            ],
        }

    def test_sample_validates(self):
        self.assertEqual(ledger.validate(self.sample(), self.load()), [])

    def test_judge_output_validates(self):
        with tempfile.TemporaryDirectory() as d:
            repo = copy_repo(d)
            info = trial.prepare(repo, "F1", "schema")
            try:
                write(os.path.join(info["worktree"], "src", "a.py"), A_GOOD)
                res = trial.judge(
                    repo,
                    info["worktree"],
                    FINDING,
                    {"id": "v1"},
                    [UNITTEST],
                    base_head=info["base_head"],
                )
            finally:
                trial.cleanup(repo, info["worktree"], False)
        doc = self.sample()
        doc["findings"][0]["trial"] = res
        self.assertEqual(ledger.validate(doc, self.load()), [])

    def test_structural_violations_are_caught(self):
        for path, value in [
            (("findings", 0, "class"), "huge"),
            (("findings", 0, "mode"), "auto"),
            (("findings", 0, "lead", "order"), "1"),
            (("findings", 0, "trial", "status"), "ok"),
            (("findings", 0, "trial", "checks", 0, "status"), "green"),
            (("run", "base_dirty"), "yes"),
            (("run", "checklist", "passed"), -1),
            (("run", "cost", "tokens"), "много"),
            (("findings", 0, "lead", "phases", 0, "steps"), "одним куском"),
        ]:
            with self.subTest(path=path):
                doc = self.sample()
                cur = doc
                for k in path[:-1]:
                    cur = cur[k]
                cur[path[-1]] = value
                self.assertTrue(ledger.validate(doc, self.load()))

    def test_required_fields(self):
        for drop in (
            ("findings", 0, "id"),
            ("findings", 0, "mode"),
            ("findings", 0, "lead", "chosen"),
            ("findings", 0, "lead", "phases", 0, "title"),
        ):
            with self.subTest(drop=drop):
                doc = self.sample()
                cur = doc
                for k in drop[:-1]:
                    cur = cur[k]
                del cur[drop[-1]]
                self.assertTrue(ledger.validate(doc, self.load()))


if __name__ == "__main__":
    unittest.main()
