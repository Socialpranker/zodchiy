#!/usr/bin/env python3
"""Юнит-слой `scribe`: файлы прогона пишет код из сохранённого промпта писаря.

Дерево расшифровок субагентов строится во временном каталоге (он же
CLAUDE_CONFIG_DIR): настоящий `~/.claude` тесты не читают. Формат первой
строки расшифровки и отступ харнесса сняты с живого прогона.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, HERE)

import scribe  # noqa: E402
from test_report import make_decisions  # noqa: E402

NONCE = "85f4f99be4e21092-fdb301333be6186c"
REFUTATIONS = [
    {"finding_id": "F1", "lens": "L1 это норма", "verdict": "survives", "reason": "ё «не вошли» 😀 \"q\" \\ \t  конец"},
    {"finding_id": "F1", "lens": "L2", "verdict": "survives", "reason": "r"},
]
CSV_TEXT = "id,title,priority\nF1,первая,8\nF2,вторая,7\nF3,третья,6\n"
HEADER = (
    "[Workflow harness — computed task] The task text below was computed at runtime by a workflow script. "
    "The harness indents every line of the computed text. The computed task text follows:\n"
)


def prompt_text(nonce=NONCE, refutations=REFUTATIONS, decisions=None, ids=("F1", "F3"), indent="  "):
    decisions = make_decisions() if decisions is None else decisions
    body = "\n".join(
        [
            "Выполни ровно одну команду.",
            f"python3 zodchiy.py scribe --nonce \"{nonce}\" --run-dir R",
            f"ZODCHIY-SCRIBE-NONCE: {nonce}",
            "<<<ZODCHIY refutation.json",
            json.dumps(refutations, ensure_ascii=False, separators=(",", ":")),
            ">>>ZODCHIY",
            "<<<ZODCHIY decisions.json",
            json.dumps(decisions, ensure_ascii=False, separators=(",", ":")),
            ">>>ZODCHIY",
            "<<<ZODCHIY findings.run.csv",
            json.dumps(list(ids)),
            ">>>ZODCHIY",
            "Верни только то, что просит schema.",
        ]
    )
    return HEADER + "\n".join(indent + ln for ln in body.split("\n"))


class Tree:
    """Временный CLAUDE_CONFIG_DIR + каталог прогона с findings.csv."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.config = os.path.join(self.root, "cfg")
        self.run_dir = os.path.join(self.root, "repo", ".zodchiy", "run-1")
        os.makedirs(self.run_dir)
        with open(os.path.join(self.run_dir, "findings.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.write(CSV_TEXT)
        self.n = 0

    def add(self, content, mtime=None, wf="wf_1", first_line=None):
        self.n += 1
        d = os.path.join(self.config, "projects", "proj", "sess", "subagents", "workflows", wf)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"agent-a{self.n}.jsonl")
        line = first_line or json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(line + "\n" + json.dumps({"type": "assistant", "message": {"content": "хвост: ZODCHIY-SCRIBE-NONCE: " + NONCE}}) + "\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def run(self, nonce=NONCE):
        return scribe.scribe(nonce, self.run_dir, config=self.config)

    def close(self):
        self._tmp.cleanup()


class ScribeCase(unittest.TestCase):
    def setUp(self):
        self.t = Tree()
        self.addCleanup(self.t.close)

    def read(self, name):
        with open(os.path.join(self.t.run_dir, name), encoding="utf-8", newline="") as fh:
            return fh.read()


class TestFound(ScribeCase):
    def test_files_written_byte_exact_and_digests_match(self):
        decisions = make_decisions()
        self.t.add(prompt_text(decisions=decisions))
        out = self.t.run()
        self.assertEqual(json.loads(self.read("decisions.json")), decisions)
        self.assertEqual(json.loads(self.read("refutation.json")), REFUTATIONS)
        # ровно та строка, что была в промпте, плюс перевод строки
        self.assertEqual(self.read("refutation.json"), json.dumps(REFUTATIONS, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.assertEqual(out["decisions_digest"], scribe.fnv(decisions))
        self.assertEqual(out["refutation_digest"], scribe.fnv(REFUTATIONS))
        self.assertEqual(out["schema_errors"], [])

    def test_csv_limited_to_ids_in_file_order(self):
        self.t.add(prompt_text(ids=("F3", "F1")))
        out = self.t.run()
        self.assertEqual(out["csv_ids"], ["F1", "F3"])
        self.assertEqual(self.read("findings.run.csv"), "id,title,priority\r\nF1,первая,8\r\nF3,третья,6\r\n")

    def test_unknown_id_is_simply_absent(self):
        self.t.add(prompt_text(ids=("F1", "F9")))
        self.assertEqual(self.t.run()["csv_ids"], ["F1"])

    def test_prompt_as_list_of_blocks(self):
        text = prompt_text()
        half = text.index("<<<ZODCHIY decisions.json")
        self.t.add([{"type": "text", "text": text[:half]}, {"type": "image"}, {"type": "text", "text": text[half:]}])
        out = self.t.run()
        self.assertEqual(out["decisions_digest"], scribe.fnv(make_decisions()))

    def test_prompt_without_indent(self):
        self.t.add(prompt_text(indent=""))
        self.assertEqual(self.t.run()["refutation_digest"], scribe.fnv(REFUTATIONS))

    def test_schema_errors_reported_not_raised(self):
        self.t.add(prompt_text(decisions={"run": {}}))
        out = self.t.run()
        self.assertTrue(out["schema_errors"])
        self.assertIn("decisions.json", os.listdir(self.t.run_dir))

    def test_nonce_matched_as_whole_line_not_substring(self):
        self.t.add(prompt_text(nonce=NONCE + "0"))
        with self.assertRaises(scribe.ScribeError):
            self.t.run()

    def test_other_nonce_files_ignored(self):
        self.t.add(prompt_text(nonce="deadbeef-deadbeef", refutations=[]))
        self.t.add(prompt_text())
        self.assertEqual(self.t.run()["refutation_digest"], scribe.fnv(REFUTATIONS))

    def test_only_first_line_counts(self):
        # nonce в хвосте расшифровки (ответ агента, вызов Bash) — не промпт
        self.t.add("нет маркера")
        with self.assertRaises(scribe.ScribeError):
            self.t.run()

    def test_unreadable_junk_first_line_skipped(self):
        self.t.add(None, first_line="{не json " + NONCE)
        self.t.add(prompt_text())
        self.assertEqual(self.t.run()["csv_ids"], ["F1", "F3"])


class TestNotFoundAndMismatch(ScribeCase):
    def test_no_transcripts_at_all(self):
        with self.assertRaises(scribe.ScribeError) as cm:
            self.t.run()
        self.assertIn(NONCE, str(cm.exception))

    def test_two_matches_identical_payload_ok(self):
        self.t.add(prompt_text(), mtime=1000, wf="wf_1")
        self.t.add(prompt_text(), mtime=2000, wf="wf_2")
        self.assertEqual(self.t.run()["csv_ids"], ["F1", "F3"])

    def test_two_matches_different_payload_error(self):
        self.t.add(prompt_text(), mtime=1000, wf="wf_1")
        self.t.add(prompt_text(refutations=REFUTATIONS[:1]), mtime=2000, wf="wf_2")
        with self.assertRaises(scribe.ScribeError) as cm:
            self.t.run()
        self.assertIn("разным содержимым", str(cm.exception))
        self.assertNotIn("decisions.json", os.listdir(self.t.run_dir), "при расхождении ничего не пишем")

    def test_missing_block(self):
        text = prompt_text().replace("<<<ZODCHIY findings.run.csv", "<<<ZODCHIY other")
        self.t.add(text)
        with self.assertRaises(scribe.ScribeError) as cm:
            self.t.run()
        self.assertIn("findings.run.csv", str(cm.exception))

    def test_block_not_json(self):
        self.t.add(prompt_text().replace('"verdict":"survives","reason":"r"', '"verdict":'))
        with self.assertRaises(scribe.ScribeError) as cm:
            self.t.run()
        self.assertIn("refutation.json", str(cm.exception))

    def test_missing_findings_csv(self):
        self.t.add(prompt_text())
        os.remove(os.path.join(self.t.run_dir, "findings.csv"))
        with self.assertRaises(scribe.ScribeError):
            self.t.run()


class TestCli(ScribeCase):
    def cli(self, *args):
        env = {**os.environ, "CLAUDE_CONFIG_DIR": self.t.config}
        return subprocess.run(
            [sys.executable, os.path.join(SKILL, "zodchiy.py"), "scribe", *args],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def test_one_json_line_through_entrypoint(self):
        self.t.add(prompt_text())
        r = self.cli("--nonce", NONCE, "--run-dir", self.t.run_dir)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().split("\n")
        self.assertEqual(len(lines), 1)
        out = json.loads(lines[0])
        self.assertEqual(sorted(out), ["csv_ids", "decisions_digest", "refutation_digest", "schema_errors"])

    def test_not_found_is_error_json_and_exit_1(self):
        r = self.cli("--nonce", "nope", "--run-dir", self.t.run_dir)
        self.assertEqual(r.returncode, 1)
        self.assertIn("error", json.loads(r.stdout))

    def test_config_dir_env_and_default(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/x"}):
            self.assertEqual(scribe.config_dir(), "/x")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(scribe.config_dir(), os.path.expanduser("~/.claude"))

    def test_main_prints_error_via_stdout(self):
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", ["scribe", "--nonce", "nope", "--run-dir", self.t.run_dir]):
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.t.config}), redirect_stdout(buf):
                with self.assertRaises(SystemExit) as cm:
                    scribe.main()
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("error", json.loads(buf.getvalue()))


if __name__ == "__main__":
    unittest.main()
