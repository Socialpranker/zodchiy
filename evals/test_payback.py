#!/usr/bin/env python3
"""Юнит-слой окупаемости: арифметика L4 и боль по истории git.

Историю строим в каталоге `тест репо` (кириллица + пробел — путь, на котором
ломаются скрипты, собранные под ASCII) с датами относительно «сейчас».
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import payback  # noqa: E402

DAY = 86400


def make_repo(root: str, commits: list[tuple[int, str, list[str]]], name: str = "тест репо") -> str:
    """commits: (дней назад, тема, файлы). Каждая правка дописывает строку в файл."""
    repo = os.path.join(root, name)
    os.makedirs(repo)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.test"}
    subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", repo], check=True)
    now = int(time.time())
    for n, (days_ago, subject, files) in enumerate(commits):
        for f in files:
            os.makedirs(os.path.dirname(os.path.join(repo, f)), exist_ok=True)
            with open(os.path.join(repo, f), "a", encoding="utf-8") as fh:
                fh.write(f"x{n} = {n}\n")
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True, env=env)
        stamp = f"{now - days_ago * DAY} +0000"
        subprocess.run(
            ["git", "-C", repo, "commit", "-q", "-m", subject],
            check=True, env={**env, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp},
        )
    return repo


class TestPaybackMath(unittest.TestCase):
    def test_payback_math(self):
        self.assertEqual(payback.payback_quarters(6, 3.0), 2.0)
        self.assertEqual(payback.payback_verdict(2.0), "ok")
        self.assertEqual(payback.payback_verdict(5.0), "too_costly")
        self.assertIsNone(payback.payback_quarters(3, 0))
        self.assertEqual(payback.payback_verdict(None), "cold")

    def test_payback_threshold_is_inclusive(self):
        self.assertEqual(payback.PAYBACK_MAX_QUARTERS, 4)
        self.assertEqual(payback.payback_verdict(4.0), "ok")
        self.assertEqual(payback.payback_verdict(4.01), "too_costly")


class TestPain(unittest.TestCase):
    def test_pain_counts_fix_episodes_and_width(self):
        history = [
            (400, "fix: старая правка вне окна", ["a.py", "b.py"]),
            (300, "fix: раз", ["a.py", "b.py"]),
            (200, "feat: не фикс", ["a.py"]),
            (150, "fix: два", ["a.py", "b.py"]),
            (150, "fix: доделка того же эпизода", ["a.py"]),
            (60, "fix: три", ["a.py", "b.py", "c.py"]),
            (10, "fix: четыре", ["a.py", "b.py"]),
        ]
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, history)
            pain = payback.pain_per_quarter(repo, ["a.py"])
        self.assertEqual(set(pain), {"fix_episodes_per_quarter", "median_files_per_episode",
                                     "pain_files_per_quarter", "edits_by_quarter", "last_edit"})
        # окно 365 дней = 4 квартала: эпизодов фикса 4 (300, 150+150, 60, 10) → 1.0 в квартал
        self.assertEqual(pain["fix_episodes_per_quarter"], 1.0)
        # ширины эпизодов: 2, 2 (a,b), 3, 2 → медиана 2
        self.assertEqual(pain["median_files_per_episode"], 2.0)
        self.assertEqual(pain["pain_files_per_quarter"], 2.0)
        self.assertEqual(len(pain["edits_by_quarter"]), 4)
        self.assertEqual(sum(pain["edits_by_quarter"]), 6)  # 7 коммитов минус вне окна
        self.assertEqual(pain["edits_by_quarter"][3], 2)  # 60 и 10 дней назад
        want = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 10 * DAY))
        self.assertEqual(pain["last_edit"], want)

    def test_pain_episode_counted_once_across_files(self):
        # эпизод {one, follow}: a.py тронут обоими коммитами, b.py только первым
        history = [(30, "fix: one", ["a.py", "b.py"]), (30, "fix: follow", ["a.py"])]
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, history)
            one = payback.pain_per_quarter(repo, ["a.py"])
            two = payback.pain_per_quarter(repo, ["a.py", "b.py"])
        self.assertEqual(one["fix_episodes_per_quarter"], 0.25)
        self.assertEqual(two["fix_episodes_per_quarter"], one["fix_episodes_per_quarter"])
        self.assertEqual(two["pain_files_per_quarter"], one["pain_files_per_quarter"])
        self.assertEqual(two["pain_files_per_quarter"], 0.5)

    def test_pain_cold_file(self):
        history = [
            (500, "fix: давно", ["cold.py"]),
            (20, "fix: другой файл", ["hot.py"]),
        ]
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, history)
            pain = payback.pain_per_quarter(repo, ["cold.py"])
        self.assertEqual(pain["pain_files_per_quarter"], 0)
        self.assertEqual(pain["fix_episodes_per_quarter"], 0)
        self.assertEqual(pain["edits_by_quarter"], [0, 0, 0, 0])
        self.assertIsNone(pain["last_edit"])

    def test_pain_edited_but_no_fix_is_cold(self):
        with tempfile.TemporaryDirectory() as d:
            repo = make_repo(d, [(30, "feat: новая фича", ["a.py"])])
            pain = payback.pain_per_quarter(repo, ["a.py"])
        self.assertEqual(pain["pain_files_per_quarter"], 0)
        self.assertEqual(pain["edits_by_quarter"], [0, 0, 0, 1])
        self.assertIsNotNone(pain["last_edit"])


if __name__ == "__main__":
    unittest.main()
