#!/usr/bin/env python3
"""Юнит-слой evals: скрипты зодчего без модели и без git-репозитория.

Приоритет тестов задан историей: 4 из 7 багов v1 сидели в резолвере импортов и
парсере лога, и все пять «в коде чисто» выглядели правдой ровно до контрольной
группы. Поэтому каждый закрытый баг получает здесь именной тест — не для
отчётности, а потому что регрессию в отсечках сейчас не ловит ничто.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, HERE)

import behavior  # noqa: E402
import common  # noqa: E402
import ledger  # noqa: E402
import measure  # noqa: E402
import structure  # noqa: E402

try:
    import tree_sitter  # noqa: F401

    HAS_TS = structure.Parsers().get("python") is not None
except ImportError:
    HAS_TS = False

need_ts = unittest.skipUnless(HAS_TS, "tree-sitter недоступен")


def log_line(sha, author, ts, subject):
    s = behavior.LOG_SEP
    return f"__C__{sha}{s}{author}{s}{ts}{s}{subject}"


# ── Парсер git-лога (баг v1 №1) ─────────────────────────────────────────────


class TestParseLog(unittest.TestCase):
    def test_one_commit(self):
        out = "\n".join([log_line("abc123", "Иван", "1700000000", "fix: тема"), "src/a.py", "src/b.py", ""])
        c = behavior.parse_log(out)
        self.assertEqual(len(c), 1)
        self.assertEqual(c[0]["sha"], "abc123")
        self.assertEqual(c[0]["subject"], "fix: тема")
        self.assertEqual(c[0]["files"], ["src/a.py", "src/b.py"])

    def test_ascii_separators_do_not_split_record(self):
        """\x1c/\x1d/\x1e — переводы строки для str.splitlines(), но не для git.

        Ровно это давало 0 багфиксов при 112 фактических: запись разваливалась,
        и `subject` терялся. Снаружи выглядело как «в репо не чинят баги».
        """
        subj = "fix: тема\x1eхвост\x1cещё\x1dи ещё"
        out = "\n".join([log_line("dead1", "Иван", "1700000000", subj), "src/a.py", ""])
        c = behavior.parse_log(out)
        self.assertEqual(len(c), 1, "запись развалилась на управляющем символе")
        self.assertEqual(c[0]["subject"], subj)
        self.assertEqual(c[0]["files"], ["src/a.py"])
        self.assertTrue(behavior.FIX_RE.match(c[0]["subject"]))

    def test_many_commits(self):
        out = "\n".join(
            [
                log_line("a1", "Иван", "1", "feat: раз"),
                "src/a.py",
                "",
                log_line("b2", "Пётр", "2", "fix: два"),
                "src/b.py",
                "src/c.py",
                "",
            ]
        )
        c = behavior.parse_log(out)
        self.assertEqual([x["sha"] for x in c], ["a1", "b2"])
        self.assertEqual(c[1]["author"], "Пётр")
        self.assertEqual(len(c[1]["files"]), 2)

    def test_crlf_and_empty_ts(self):
        out = log_line("a1", "Иван", "", "тема") + "\r\nsrc/a.py\r\n"
        c = behavior.parse_log(out)
        self.assertEqual(c[0]["ts"], 0)
        self.assertEqual(c[0]["files"], ["src/a.py"])

    def test_commit_without_files(self):
        out = "\n".join([log_line("a1", "И", "1", "chore"), "", log_line("b2", "И", "2", "chore"), "src/a.py"])
        c = behavior.parse_log(out)
        self.assertEqual(c[0]["files"], [])
        self.assertEqual(c[1]["files"], ["src/a.py"])


# ── Классификация путей ─────────────────────────────────────────────────────


class TestClassify(unittest.TestCase):
    def test_code(self):
        for p in ("src/payments.py", "app/main.go", "lib/x.ts"):
            self.assertEqual(behavior.classify(p), "code", p)

    def test_test(self):
        for p in ("tests/test_x.py", "src/x.spec.ts", "e2e/flow.ts", "conftest.py", "src/__tests__/a.tsx"):
            self.assertEqual(behavior.classify(p), "test", p)

    def test_generated(self):
        for p in ("api/schema.sql", "db/migrations/001.sql", "proto/x_pb2.py", "types/api.d.ts"):
            self.assertEqual(behavior.classify(p), "generated", p)

    def test_noise(self):
        for p in ("README.md", "node_modules/lib/x.js", ".venv/lib/y.py", "package.json"):
            self.assertEqual(behavior.classify(p), "noise", p)


class TestLayers(unittest.TestCase):
    def test_layer_skips_package_root(self):
        self.assertEqual(behavior.layer_of("src/domain/user.py", ["src"], 1), "domain")

    def test_layer_depth_two(self):
        self.assertEqual(behavior.layer_of("src/domain/user/x.py", ["src"], 2), "domain/user")

    def test_file_at_package_root_is_its_own_layer(self):
        self.assertEqual(behavior.layer_of("src/main.py", ["src"], 1), "main.py")

    def test_guess_roots(self):
        paths = [f"src/a{i}.py" for i in range(20)] + ["docs/x.py"]
        self.assertEqual(behavior.guess_roots(paths), ["src"])

    def test_guess_roots_no_dominant(self):
        paths = [f"a/x{i}.py" for i in range(5)] + [f"b/y{i}.py" for i in range(5)]
        self.assertEqual(behavior.guess_roots(paths), [])


# ── Багфиксы и hotspots ─────────────────────────────────────────────────────


class TestFixDetection(unittest.TestCase):
    def test_matches(self):
        for s in ("fix: падение", "Fix(auth): 401", "hotfix пуш", "bugfix", "исправлено округление", "починил кэш", "patch: тайминг"):
            self.assertTrue(behavior.FIX_RE.match(s), s)

    def test_does_not_match(self):
        for s in ("feat: новая ось", "refactor: вынес модуль", "docs: правка", "chore: bump"):
            self.assertIsNone(behavior.FIX_RE.match(s), s)

    def test_revert(self):
        self.assertTrue(behavior.REVERT_RE.match("Revert \"feat: x\""))
        self.assertTrue(behavior.REVERT_RE.match("откат миграции"))
        self.assertIsNone(behavior.REVERT_RE.match("feat: x"))

    def test_hotspots_fix_share(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "src"))
            for name in ("a.py", "b.py"):
                with open(os.path.join(d, "src", name), "w") as fh:
                    fh.write("x = 1\n" * 10)
            commits = [{"sha": f"s{i}", "author": "И", "ts": i, "subject": "feat: x", "files": ["src/a.py"]} for i in range(6)]
            commits += [{"sha": f"f{i}", "author": "И", "ts": 100 + i, "subject": "fix: y", "files": ["src/a.py"]} for i in range(4)]
            commits += [{"sha": f"g{i}", "author": "И", "ts": 200 + i, "subject": "feat: z", "files": ["src/b.py"]} for i in range(5)]
            solo = {"src/a.py": 10, "src/b.py": 5}
            rows = {r["file"]: r for r in behavior.hotspots(d, solo, commits)}
            self.assertEqual(rows["src/a.py"]["fix_commits"], 4)
            self.assertAlmostEqual(rows["src/a.py"]["fix_share"], 0.4)
            self.assertEqual(rows["src/b.py"]["fix_share"], 0.0)

    def test_hotspots_skips_deleted_file(self):
        with tempfile.TemporaryDirectory() as d:
            rows = behavior.hotspots(d, {"src/gone.py": 9}, [])
            self.assertEqual(rows, [])


# ── Сцепление и containment ─────────────────────────────────────────────────


def _commits(pairs, subject="feat: x", author="И"):
    return [
        {"sha": f"s{i}", "author": author, "ts": i, "subject": subject, "files": list(fs)}
        for i, fs in enumerate(pairs)
    ]


class TestTemporalCoupling(unittest.TestCase):
    cfg = dict(behavior.DEFAULTS)

    def test_below_min_shared_is_noise(self):
        rows, _ = behavior.temporal_coupling(_commits([["src/a.py", "src/b.py"]] * 4), self.cfg)
        self.assertEqual(rows, [], "4 совместные правки — ещё не сцепление")

    def test_above_threshold(self):
        rows, solo = behavior.temporal_coupling(_commits([["src/a.py", "src/b.py"]] * 6), self.cfg)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["shared"], 6)
        self.assertEqual(rows[0]["degree"], 1.0)
        self.assertEqual(solo["src/a.py"], 6)

    def test_degree_by_weakest_link(self):
        """Односторонняя тяга: A всегда с B, B живёт сам. min() показывает её честно."""
        cs = _commits([["src/a.py", "src/b.py"]] * 6 + [["src/b.py"]] * 30)
        rows, _ = behavior.temporal_coupling(cs, self.cfg)
        self.assertEqual(rows[0]["degree"], 1.0)
        self.assertEqual(rows[0]["edits_b"], 36)

    def test_mega_commit_excluded(self):
        mega = [[f"src/f{i}.py" for i in range(30)]] * 10
        rows, solo = behavior.temporal_coupling(_commits(mega), self.cfg)
        self.assertEqual(rows, [])
        self.assertEqual(solo, {})

    def test_tests_are_not_coupled_to_code(self):
        cs = _commits([["src/a.py", "tests/test_a.py"]] * 10)
        rows, solo = behavior.temporal_coupling(cs, self.cfg)
        self.assertEqual(rows, [])
        self.assertNotIn("tests/test_a.py", solo)


class TestContainment(unittest.TestCase):
    cfg = dict(behavior.DEFAULTS)

    def test_tests_excluded_from_crossings(self):
        """Ключевое утверждение калибровки: с тестами метрика меряет наличие
        тестов, а не архитектуру (83% против 49% на живом репо)."""
        cs = _commits([["src/domain/a.py", "tests/test_a.py"]] * 10)
        res = behavior.containment(cs, self.cfg, ["src"], 1)
        self.assertEqual(res["across_layers"], 0)
        self.assertEqual(res["ratio"], 1.0)

    def test_real_crossing_counted(self):
        cs = _commits([["src/domain/a.py", "src/api/b.py"]] * 4 + [["src/domain/a.py"]] * 6)
        res = behavior.containment(cs, self.cfg, ["src"], 1)
        self.assertEqual(res["across_layers"], 4)
        self.assertEqual(res["inside_one_layer"], 6)
        self.assertEqual(res["ratio"], 0.6)
        self.assertEqual(res["top_crossings"][0]["layers"], "api + domain")

    def test_empty(self):
        self.assertIsNone(behavior.containment([], self.cfg, [], 1)["ratio"])


class TestKnowledgeRisk(unittest.TestCase):
    cfg = dict(behavior.DEFAULTS)

    def test_single_owner_flagged(self):
        cs = _commits([["src/a.py"]] * 10, author="Иван")
        rows = behavior.knowledge_risk(cs, self.cfg)
        self.assertEqual(rows[0]["file"], "src/a.py")
        self.assertEqual(rows[0]["contributors"], 1)

    def test_below_min_edits_ignored(self):
        self.assertEqual(behavior.knowledge_risk(_commits([["src/a.py"]] * 5), self.cfg), [])

    def test_shared_ownership_not_flagged(self):
        cs = _commits([["src/a.py"]] * 6, author="Иван") + _commits([["src/a.py"]] * 6, author="Пётр")
        self.assertEqual(behavior.knowledge_risk(cs, self.cfg), [])


class TestLoadConfig(unittest.TestCase):
    def test_defaults_without_file(self):
        self.assertEqual(behavior.load_config(None), behavior.DEFAULTS)
        self.assertEqual(behavior.load_config("/nope/absent.yaml"), behavior.DEFAULTS)

    def test_overrides(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
            fh.write(
                "# комментарий\n"
                "min_commits: 10\n"
                "coupling_min_degree: 0.7\n"
                "history_window: 6.months.ago\n"
                "unknown_key: 5\n"
                "  nested: 1\n"
            )
            path = fh.name
        try:
            cfg = behavior.load_config(path)
            self.assertEqual(cfg["min_commits"], 10)
            self.assertEqual(cfg["coupling_min_degree"], 0.7)
            self.assertEqual(cfg["history_window"], "6.months.ago")
            self.assertNotIn("unknown_key", cfg)
            self.assertNotIn("nested", cfg)
            self.assertEqual(cfg["mega_commit_files"], behavior.DEFAULTS["mega_commit_files"])
        finally:
            os.unlink(path)


# ── Резолвер импортов (баги v1 №2, №5) ──────────────────────────────────────


class TestIndexAndResolve(unittest.TestCase):
    files = [
        "src/__init__.py",
        "src/core/__init__.py",
        "src/core/db.py",
        "src/user/__init__.py",
        "src/user/model.py",
        "src/user/repository.py",
        "web/shared/api/index.ts",
        "web/pages/home.tsx",
    ]

    def setUp(self):
        self.index = structure.build_index(self.files)
        self.roots = ["src", "web"]

    def test_index_has_both_forms(self):
        self.assertIn("src/user/model", self.index)
        self.assertIn("src.user.model", self.index)

    def test_package_resolves_to_init(self):
        self.assertEqual(structure.resolve("src.user", "src/x.py", self.index, self.roots), "src/user/__init__.py")

    def test_submodule_resolves_to_module_not_package(self):
        """`from pkg import submodule` обязан вести в submodule.py.

        Баг v1 №5: не резолвился — и гейт отвечал OK на внесённый руками цикл.
        """
        self.assertEqual(
            structure.resolve("src.user.model", "src/user/repository.py", self.index, self.roots),
            "src/user/model.py",
        )

    def test_python_relative_one_dot(self):
        self.assertEqual(
            structure.resolve(".model", "src/user/repository.py", self.index, self.roots),
            "src/user/model.py",
        )

    def test_python_relative_two_dots(self):
        self.assertEqual(
            structure.resolve("..core.db", "src/user/repository.py", self.index, self.roots),
            "src/core/db.py",
        )

    def test_js_relative(self):
        self.assertEqual(
            structure.resolve("../shared/api", "web/pages/home.tsx", self.index, self.roots),
            "web/shared/api/index.ts",
        )

    def test_alias_root(self):
        self.assertEqual(
            structure.resolve("@/shared/api", "web/pages/home.tsx", self.index, self.roots),
            "web/shared/api/index.ts",
        )

    def test_external_is_none(self):
        for spec in ("fastapi", "react", "@tanstack/react-query", ""):
            self.assertIsNone(structure.resolve(spec, "src/user/model.py", self.index, self.roots), spec)


@need_ts
class TestImportsViaTreeSitter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsers = structure.Parsers()

    def imports(self, code, lang="python"):
        p = self.parsers.get(lang)
        return structure.imports_via_ts(p, code.encode(), lang)

    def test_from_package_import_submodule_yields_both(self):
        """Баг v1 №2: обход брал импортируемое ИМЯ вместо модуля — 5 рёбер
        на 495 файлов, и это выглядело как «зависимостей нет»."""
        got = dict(self.imports("from src.user import model\n"))
        self.assertIn("src.user", got)
        self.assertIn("src.user.model", got)

    def test_from_module_import_name(self):
        got = dict(self.imports("from src.user.model import User\n"))
        self.assertIn("src.user.model", got)
        self.assertEqual(got["src.user.model"], "runtime")

    def test_aliased_import(self):
        got = dict(self.imports("from src.user import model as m\n"))
        self.assertIn("src.user.model", got)

    def test_plain_import_dotted(self):
        self.assertIn("src.core.db", dict(self.imports("import src.core.db\n")))

    def test_relative_import_kept(self):
        self.assertIn(".model", dict(self.imports("from .model import User\n")))

    def test_type_checking_marked_type(self):
        code = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from src.user.model import User\n"
        )
        got = dict(self.imports(code))
        self.assertEqual(got["src.user.model"], "type")

    def test_runtime_import_not_marked_type(self):
        got = dict(self.imports("from src.user.model import User\n"))
        self.assertEqual(got["src.user.model"], "runtime")

    def test_ts_import_type(self):
        got = dict(self.imports('import type { User } from "./model";\n', "typescript"))
        self.assertEqual(got["./model"], "type")

    def test_ts_value_import(self):
        got = dict(self.imports('import { load } from "./repo";\n', "typescript"))
        self.assertEqual(got["./repo"], "runtime")

    def test_ts_reexport(self):
        self.assertIn("./model", dict(self.imports('export { User } from "./model";\n', "typescript")))

    def test_js_require(self):
        self.assertIn("./repo", dict(self.imports('const r = require("./repo");\n', "javascript")))


@need_ts
class TestComplexityNormalization(unittest.TestCase):
    """Баг v1 №3: McCabe задаёт порог на ФУНКЦИЮ, считалось на файл."""

    @classmethod
    def setUpClass(cls):
        cls.parser = structure.Parsers().get("python")

    def test_many_simple_functions(self):
        code = "".join(
            f"def s{i}(x):\n    if x == {i}:\n        return 1\n    if x > {i}:\n        return 2\n    return 0\n\n\n"
            for i in range(1, 11)
        )
        m = structure.complexity_via_ts(self.parser, code.encode())
        self.assertEqual(m["functions"], 10)
        self.assertGreaterEqual(m["cyclomatic_total"], 20)
        self.assertLessEqual(m["cyclomatic_per_function_max"], 4, "сумма по файлу утекла в метрику на функцию")

    def test_one_complex_function(self):
        code = "def hairy(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(1, 15)) + "    return 0\n"
        m = structure.complexity_via_ts(self.parser, code.encode())
        self.assertEqual(m["functions"], 1)
        self.assertGreaterEqual(m["cyclomatic_per_function_max"], 12)
        self.assertGreater(m["worst_function_line"], 0)


# ── Циклы ───────────────────────────────────────────────────────────────────


class TestTarjan(unittest.TestCase):
    def test_two_node_cycle(self):
        g = {"a": {"b"}, "b": {"a"}, "c": set()}
        self.assertEqual(structure.tarjan(g), [["a", "b"]])

    def test_dag_has_no_cycles(self):
        g = {"a": {"b", "c"}, "b": {"c"}, "c": set()}
        self.assertEqual(structure.tarjan(g), [])

    def test_self_loop_is_not_a_component(self):
        g = {"a": {"a"}}
        self.assertEqual(structure.tarjan(g), [])
        self.assertEqual(structure.self_loops(g), ["a"])

    def test_two_independent_components(self):
        g = {"a": {"b"}, "b": {"a"}, "c": {"d"}, "d": {"c"}}
        self.assertEqual(sorted(structure.tarjan(g)), [["a", "b"], ["c", "d"]])

    def test_long_chain_does_not_blow_stack(self):
        n = 5000
        g = {str(i): {str(i + 1)} for i in range(n)}
        g[str(n)] = {"0"}
        comps = structure.tarjan(g)
        self.assertEqual(len(comps), 1)
        self.assertEqual(len(comps[0]), n + 1)


# ── Калибровка (measure.py) ─────────────────────────────────────────────────


def _behavior(available=True, code=100, test=40, pairs=5, considered=100):
    b = {
        "available": available,
        "commits_with_code": code,
        "churn_profile": {"code": code, "test": test},
    }
    if available:
        b["temporal_coupling_total"] = pairs
        b["containment"] = {"commits_considered": considered, "ratio": 0.8}
    else:
        b["reason"] = "история короче порога"
    return b


def _structure(ts=100, rx=0, files=100, edges=140):
    return {"parser": {"backends": {"tree-sitter": ts, **({"regex": rx} if rx else {})}}, "files": files, "edges": edges}


class TestCalibration(unittest.TestCase):
    def test_all_green(self):
        c = measure.calibrate(_behavior(), _structure())
        self.assertTrue(c["passed"])
        self.assertEqual(c["blocked_metrics"], [])
        self.assertEqual(measure.confidence_ceiling(_behavior(), _structure(), c)["ceiling"], "verdict")

    def test_short_history_blocks_behavioural_metrics(self):
        c = measure.calibrate(_behavior(available=False), _structure())
        self.assertFalse(c["passed"])
        self.assertEqual(
            c["blocked_metrics"], ["containment", "hotspots", "knowledge_risk", "temporal_coupling"]
        )

    def test_no_tests_blocks_containment(self):
        c = measure.calibrate(_behavior(test=0), _structure())
        self.assertIn("containment", c["blocked_metrics"])

    def test_regex_backend_lowers_ceiling(self):
        s = _structure(ts=90, rx=10)
        c = measure.calibrate(_behavior(), s)
        self.assertEqual(measure.confidence_ceiling(_behavior(), s, c)["ceiling"], "finding")

    def test_broken_resolver_blocks_graph_metrics(self):
        c = measure.calibrate(_behavior(), _structure(files=495, edges=5))
        self.assertEqual(sorted(c["blocked_metrics"]), ["cycles", "fan_in", "hubs"])

    def test_noisy_coupling_threshold_blocks_coupling(self):
        c = measure.calibrate(_behavior(pairs=90, considered=100), _structure())
        self.assertIn("temporal_coupling", c["blocked_metrics"])


# ── Леджер и гейт (баг v1 №7 — имя поля) ────────────────────────────────────


def _measure(cycles=(), containment=0.8, hotspots=()):
    return {
        "snapshot": {"head": "abc123"},
        "calibration": {"passed": True},
        "confidence": {"ceiling": "verdict"},
        "structure": {"files": 100, "edges": 140, "cycles": [{"size": len(c), "members": list(c)} for c in cycles]},
        "behavior": {
            "containment": {"ratio": containment},
            "temporal_coupling_total": 3,
            "commits_with_code": 100,
            "hotspots": list(hotspots),
        },
    }


class TestLedgerSnapshot(unittest.TestCase):
    def test_painful_needs_both_churn_and_fixes(self):
        hot = [
            {"file": "src/pain.py", "edits": 20, "fix_share": 0.4},
            {"file": "src/extension.py", "edits": 74, "fix_share": 0.11},
            {"file": "src/rare.py", "edits": 4, "fix_share": 0.75},
        ]
        s = ledger.snapshot(_measure(hotspots=hot))
        self.assertEqual(s["painful_files"], ["src/pain.py"])

    def test_cycle_members_flattened(self):
        s = ledger.snapshot(_measure(cycles=[("b.py", "a.py")]))
        self.assertEqual(s["cycle_members"], ["a.py", "b.py"])
        self.assertEqual(s["runtime_cycles"], 1)


class TestLedgerGate(unittest.TestCase):
    def base(self, **kw):
        return ledger.snapshot(_measure(**kw))

    def test_clean_run_is_ok(self):
        r = ledger.diff(self.base(), self.base())
        self.assertEqual(r["gate_result"], "OK")
        self.assertEqual(r["regressions"], [])

    def test_field_is_gate_result_not_verdict(self):
        """Баг v1 №7: слово `verdict` жило в двух несвязанных смыслах."""
        r = ledger.diff(self.base(), self.base())
        self.assertIn("gate_result", r)
        self.assertNotIn("verdict", r)

    def test_new_cycle_is_regression(self):
        r = ledger.diff(self.base(cycles=[("a.py", "b.py")]), self.base())
        self.assertEqual(r["gate_result"], "REGRESSION")
        self.assertEqual(r["new_runtime_cycles"], ["a.py", "b.py"])

    def test_max_new_cycles_tolerance(self):
        cur, base = self.base(cycles=[("a.py", "b.py")]), self.base()
        self.assertEqual(ledger.diff(cur, base, max_new_cycles=2)["gate_result"], "OK")
        self.assertEqual(ledger.diff(cur, base, max_new_cycles=1)["gate_result"], "REGRESSION")

    def test_containment_drop_over_threshold(self):
        self.assertEqual(ledger.diff(self.base(containment=0.78), self.base(containment=0.80))["gate_result"], "OK")
        self.assertEqual(
            ledger.diff(self.base(containment=0.70), self.base(containment=0.80))["gate_result"], "REGRESSION"
        )

    def test_new_painful_file_is_regression(self):
        hot = [{"file": "src/pain.py", "edits": 20, "fix_share": 0.4}]
        r = ledger.diff(self.base(hotspots=hot), self.base())
        self.assertEqual(r["gate_result"], "REGRESSION")
        self.assertEqual(r["new_painful_files"], ["src/pain.py"])

    def test_healed_file_is_not_regression(self):
        hot = [{"file": "src/pain.py", "edits": 20, "fix_share": 0.4}]
        r = ledger.diff(self.base(), self.base(hotspots=hot))
        self.assertEqual(r["gate_result"], "OK")
        self.assertEqual(r["healed_files"], ["src/pain.py"])


# ── Документация не ссылается на несуществующие скрипты (баг v1 №6) ─────────


class TestDocsReferenceRealScripts(unittest.TestCase):
    """`calibrate.py` упоминался в 7 местах, а файла не было никогда."""

    PATTERNS = (re.compile(r"scripts/([\w.-]+\.py)"), re.compile(r"python3\s+(?:\S*/)?([\w.-]+\.py)"))

    def _docs(self):
        for root, dirs, files in os.walk(SKILL):
            dirs[:] = [d for d in dirs if d not in ("research", ".cache")]
            for f in files:
                if f.endswith(".md"):
                    yield os.path.join(root, f)

    def test_every_mentioned_script_exists(self):
        known = set(os.listdir(os.path.join(SKILL, "scripts")))
        known |= {f for f in os.listdir(HERE) if f.endswith(".py")}
        known |= {f for f in os.listdir(SKILL) if f.endswith(".py")}
        known |= set(os.listdir(os.path.join(HERE, "fixtures")))
        missing = []
        for doc in self._docs():
            text = open(doc, encoding="utf-8").read()
            for pat in self.PATTERNS:
                for name in pat.findall(text):
                    if name not in known:
                        missing.append(f"{os.path.relpath(doc, SKILL)} -> {name}")
        self.assertEqual(missing, [], "документация зовёт скрипт, которого нет")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ── Распределения ───────────────────────────────────────────────────────────


class TestPctRank(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(common.pct_rank([])(5))

    def test_monotone(self):
        r = common.pct_rank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        self.assertEqual(r(10), 100)
        self.assertEqual(r(1), 10)
        self.assertLess(r(3), r(7))

    def test_ties_count_as_le(self):
        r = common.pct_rank([5, 5, 5, 9])
        self.assertEqual(r(5), 75)


class TestWilson(unittest.TestCase):
    def test_small_sample_loses_to_large(self):
        """5 из 5 не должно обгонять 55 из 67: иначе шум идёт первой строкой."""
        self.assertLess(common.wilson_lower(5, 5), common.wilson_lower(55, 67))

    def test_zero_and_bounds(self):
        self.assertEqual(common.wilson_lower(0, 10), 0.0)
        self.assertEqual(common.wilson_lower(0, 0), 0.0)
        self.assertLess(common.wilson_lower(10, 10), 1.0)

    def test_more_evidence_raises_bound(self):
        self.assertLess(common.wilson_lower(8, 10), common.wilson_lower(80, 100))


# ── Скорость ────────────────────────────────────────────────────────────────


def _ts(day):
    return day * 86400


def _c(day, files, subject="feat: x", author="И"):
    return {"sha": f"s{day}", "author": author, "ts": _ts(day), "subject": subject, "files": list(files)}


class TestEpisodes(unittest.TestCase):
    cfg = dict(behavior.DEFAULTS)

    def test_gap_splits_episode(self):
        cs = [_c(0, ["src/a.py"]), _c(2, ["src/a.py"]), _c(20, ["src/a.py"])]
        eps = behavior.episodes_by_file(cs, self.cfg)
        self.assertEqual([len(g) for g in eps["src/a.py"]], [2, 1])

    def test_unsorted_input_is_ordered(self):
        cs = [_c(20, ["src/a.py"]), _c(0, ["src/a.py"]), _c(2, ["src/a.py"])]
        eps = behavior.episodes_by_file(cs, self.cfg)
        self.assertEqual([len(g) for g in eps["src/a.py"]], [2, 1])

    def test_tests_excluded(self):
        self.assertEqual(behavior.episodes_by_file([_c(0, ["tests/test_a.py"])], self.cfg), {})


class TestVelocity(unittest.TestCase):
    cfg = dict(behavior.DEFAULTS)

    def test_touch_cost_by_layer(self):
        cs = [_c(i, [f"src/wide/w{k}.py" for k in range(6)]) for i in range(5)]
        cs += [_c(50 + i, ["src/narrow/one.py"]) for i in range(5)]
        v = behavior.velocity(cs, self.cfg, ["src"], 1)
        self.assertEqual(v["touch_cost"]["by_layer_median"]["wide"], 6)
        self.assertEqual(v["touch_cost"]["by_layer_median"]["narrow"], 1)
        self.assertEqual(v["touch_cost"]["median_files_per_commit"], 3.5)

    def test_mega_commit_not_counted(self):
        cs = [_c(0, [f"src/f{i}.py" for i in range(40)])]
        v = behavior.velocity(cs, self.cfg, ["src"], 1)
        self.assertIsNone(v["touch_cost"]["median_files_per_commit"])

    def test_multi_commit_share(self):
        cs = [_c(0, ["src/a.py"]), _c(1, ["src/a.py"]), _c(40, ["src/b.py"])]
        v = behavior.velocity(cs, self.cfg, ["src"], 1)
        self.assertEqual(v["episodes"]["total"], 2)
        self.assertEqual(v["episodes"]["multi_commit_share"], 0.5)
        self.assertEqual(v["episodes"]["median_commits_multi"], 2.0)


# ── Устойчивость ────────────────────────────────────────────────────────────


class TestStability(unittest.TestCase):
    cfg = dict(behavior.DEFAULTS)

    def _cs(self, gap_days):
        out = []
        for i in range(6):
            out.append(_c(i * 40, ["src/a.py"]))
            out.append(_c(i * 40 + gap_days, ["src/a.py"], subject="fix: y"))
        return out

    def test_fix_inside_window_is_rework(self):
        st = behavior.stability(self._cs(2), self.cfg)
        row = st["unstable_files"][0]
        self.assertEqual(row["file"], "src/a.py")
        self.assertEqual(row["rework_rate"], 1.0)
        self.assertEqual(row["fix_latency_median_days"], 2.0)

    def test_fix_outside_window_is_not_rework(self):
        """Окно — смысловая граница: фикс через 30 дней не доделка этой правки."""
        st = behavior.stability(self._cs(30), self.cfg)
        self.assertEqual(st["unstable_files"][0]["rework_rate"], 0.0)

    def test_fix_before_change_does_not_count(self):
        cs = [_c(0, ["src/a.py"], subject="fix: y")] + [_c(10 + i, ["src/a.py"]) for i in range(6)]
        st = behavior.stability(cs, self.cfg)
        self.assertEqual(st["unstable_files"][0]["rework_rate"], 0.0)

    def test_below_min_changes_not_listed(self):
        st = behavior.stability([_c(0, ["src/a.py"]), _c(1, ["src/a.py"], subject="fix: y")], self.cfg)
        self.assertEqual(st["unstable_files"], [])
        self.assertEqual(st["rework_rate"], 1.0)

    def test_reverts_counted_per_file(self):
        cs = [_c(i, ["src/a.py"], subject="revert: x") for i in range(3)]
        st = behavior.stability(cs, self.cfg)
        self.assertEqual(st["revert_files_top"][0], {"file": "src/a.py", "reverts": 3})

    def test_ranked_by_lower_bound(self):
        cs = [_c(i, ["src/small.py"]) for i in range(5)]
        cs += [_c(i, ["src/small.py"], subject="fix: y") for i in range(100, 105)]
        cs += [_c(i * 2, ["src/big.py"]) for i in range(40)]
        cs += [_c(i * 2 + 1, ["src/big.py"], subject="fix: y") for i in range(34)]
        st = behavior.stability(cs, self.cfg)
        self.assertEqual(st["unstable_files"][0]["file"], "src/big.py")


# ── Путь в measure.json ─────────────────────────────────────────────────────


class TestResolvePath(unittest.TestCase):
    doc = {
        "behavior": {
            "hotspots": [{"file": "src/a.py", "fix_share": 0.4}, {"file": "src/b.py", "fix_share": 0.1}],
            "stability": {"rework_rate": 0.33},
        },
        "structure": {"adjacency": {"src/user/repo.py": ["src/user/model.py"]}},
    }

    def test_plain_and_index(self):
        self.assertEqual(ledger.resolve_path(self.doc, "behavior.stability.rework_rate"), 0.33)
        self.assertEqual(ledger.resolve_path(self.doc, "behavior.hotspots[1].file"), "src/b.py")

    def test_filter_value_containing_dot(self):
        """Наивный split('.') рвёт `[file=src/a.py]` пополам — тот же класс,
        что `splitlines()` по `\\x1e`: делитель встречается внутри значения."""
        self.assertEqual(
            ledger.resolve_path(self.doc, "behavior.hotspots[file=src/a.py].fix_share"), 0.4
        )

    def test_quoted_key_with_slash(self):
        self.assertEqual(
            ledger.resolve_path(self.doc, 'structure.adjacency["src/user/repo.py"]'),
            ["src/user/model.py"],
        )

    def test_missing_raises(self):
        for bad in ("behavior.nope", "behavior.hotspots[file=нет].fix_share"):
            with self.assertRaises(KeyError):
                ledger.resolve_path(self.doc, bad)


# ── selfcheck и verify ──────────────────────────────────────────────────────


def _finding(**kw):
    row = {k: "x" for k in ledger.REQUIRED}
    row.update(
        {
            "id": "F1",
            "source": "behavior.stability.rework_rate",
            "gain_metric": "rework_rate",
            "gain_target": "0.25",
            "priority": "3",
            "confidence": "finding",
            "status": "open",
        }
    )
    row.update(kw)
    return row


class TestSelfcheck(unittest.TestCase):
    m = _measure(hotspots=[{"file": "src/a.py", "edits": 20, "fix_share": 0.4}])

    def setUp(self):
        self.m = dict(TestSelfcheck.m)
        self.m["behavior"] = dict(self.m["behavior"], stability={"rework_rate": 0.33}, velocity={})

    def test_clean(self):
        r = ledger.selfcheck([_finding()], self.m, [])
        self.assertEqual(r["result"], "OK", r["problems"])

    def test_empty_required_field(self):
        r = ledger.selfcheck([_finding(remedy_cost="")], self.m, [])
        self.assertIn("remedy_cost", r["problems"][0]["problem"])

    def test_source_must_resolve(self):
        r = ledger.selfcheck([_finding(source="behavior.выдумка")], self.m, [])
        self.assertEqual(r["result"], "BROKEN")

    def test_unknown_gain_metric(self):
        r = ledger.selfcheck([_finding(gain_metric="счастье")], self.m, [])
        self.assertEqual(r["result"], "BROKEN")

    def test_manual_gain_metric_allowed(self):
        self.assertEqual(ledger.selfcheck([_finding(gain_metric="manual")], self.m, [])["result"], "OK")

    def test_high_priority_needs_refutation(self):
        f = _finding(priority="7")
        self.assertEqual(ledger.selfcheck([f], self.m, [])["result"], "BROKEN")
        refs = [
            {"finding_id": "F1", "lens": "L2", "verdict": "survives", "reason": "...", "mode": "parallel"}
        ]
        self.assertEqual(ledger.selfcheck([f], self.m, refs)["result"], "OK")

    def test_verdict_confidence_needs_refutation(self):
        r = ledger.selfcheck([_finding(confidence="verdict")], self.m, [])
        self.assertEqual(r["result"], "BROKEN")


class TestVerifyGains(unittest.TestCase):
    def setUp(self):
        m = _measure(containment=0.8)
        m["behavior"] = dict(m["behavior"], stability={"rework_rate": 0.33}, velocity={})
        self.m = m

    def test_open_finding_skipped(self):
        _, rep = ledger.verify_gains([_finding(status="open")], self.m)
        self.assertEqual(rep, [])

    def test_target_not_reached(self):
        rows, rep = ledger.verify_gains(
            [_finding(status="done", gain_direction="down", gain_target="0.25")], self.m
        )
        self.assertEqual(rep[0]["verdict"], "не сбылось")
        self.assertEqual(rows[0]["gain_actual"], 0.33)
        self.assertTrue(rows[0]["verified_at"])

    def test_target_reached(self):
        _, rep = ledger.verify_gains(
            [_finding(status="done", gain_direction="down", gain_target="0.40")], self.m
        )
        self.assertEqual(rep[0]["verdict"], "сбылось")

    def test_manual_is_not_measurable(self):
        _, rep = ledger.verify_gains([_finding(status="done", gain_metric="manual")], self.m)
        self.assertEqual(rep[0]["verdict"], "не измеримо")


class TestGateSpeedStability(unittest.TestCase):
    def snap(self, **kw):
        m = _measure()
        m["behavior"] = dict(
            m["behavior"],
            velocity={"touch_cost": {"p90_files_per_commit": kw.get("p90", 5)}, "episodes": {}},
            stability={
                "rework_rate": kw.get("rework", 0.3),
                "unstable_files": kw.get("unstable", []),
            },
        )
        return ledger.snapshot(m)

    def test_new_unstable_file_is_regression(self):
        cur = self.snap(unstable=[{"file": "src/a.py", "rework_rate_lb": 0.7, "changes": 20}])
        r = ledger.diff(cur, self.snap())
        self.assertEqual(r["gate_result"], "REGRESSION")
        self.assertEqual(r["new_unstable_files"], ["src/a.py"])

    def test_weak_evidence_not_promoted_to_gate(self):
        """5 правок с высокой долей в гейт не идут: нижняя граница ниже порога."""
        cur = self.snap(unstable=[{"file": "src/a.py", "rework_rate_lb": 0.56, "changes": 5}])
        self.assertEqual(ledger.diff(cur, self.snap())["gate_result"], "OK")

    def test_rework_growth_is_regression(self):
        self.assertEqual(ledger.diff(self.snap(rework=0.34), self.snap(rework=0.30))["gate_result"], "OK")
        self.assertEqual(
            ledger.diff(self.snap(rework=0.40), self.snap(rework=0.30))["gate_result"], "REGRESSION"
        )

    def test_delta_carries_new_metrics(self):
        d = ledger.diff(self.snap(p90=8), self.snap(p90=5))["delta"]
        self.assertEqual(d["touch_cost_p90"], 3)


# ── Схема measure.json не разъезжается с документацией ──────────────────────


class TestSchemaDocCoversFields(unittest.TestCase):
    """`calibrate.py` жил в семи местах документации, не существуя. Обратный
    случай — поле есть в JSON и его нет в схеме — так же нем."""

    SKIP = {"axis", "snapshot", "thresholds", "package_roots", "note", "checks"}

    def test_every_axis_field_documented(self):
        import run_evals

        m = run_evals.measure_fixture("velocity-stability", rebuild=False)
        doc = open(os.path.join(SKILL, "references", "measure_schema.md"), encoding="utf-8").read()
        # Имя ищется как слово внутри любого кодового спана: в схеме поля
        # записаны с суффиксами (`hotspots[]`, `velocity.touch_cost`).
        spans = " ".join(re.findall(r"`([^`]+)`", doc))
        named = set(re.findall(r"[A-Za-z_][\w]*", spans))
        missing = [
            f"{axis}.{k}"
            for axis in ("behavior", "structure")
            for k in m[axis]
            if k not in self.SKIP and k not in named
        ]
        self.assertEqual(missing, [], "поле есть в measure.json, но не описано в схеме")


# ── Деградация шага 4 без субагентов (U2) ──────────────────────────────────


def _u2_finding(**kw):
    row = {
        "id": "F1",
        "title": "пример",
        "risk": "R2",
        "symptom": "пара правится вместе",
        "source": "behavior.containment.ratio",
        "cost_pain": "12 правок",
        "remedy": "перенести границу",
        "remedy_cost": "2 дня",
        "gain": "containment вырастет",
        "gain_metric": "containment_ratio",
        "gain_target": "0.70",
        "refutation": "перестанут править вместе",
        "confidence": "verdict",
        "priority": "7",
    }
    row.update(kw)
    return row


def _u2_refutation(**kw):
    rec = {
        "finding_id": "F1",
        "lens": "L1 это норма",
        "verdict": "survives",
        "reason": "не barrel и не DI-корень",
        "mode": "parallel",
    }
    rec.update(kw)
    return rec


class TestRefutationMode(unittest.TestCase):
    """Без субагентов шаг 4 вырождается в один проход одной модели. Молчаливая
    деградация превращает «опровержение было» в неправду — режим объявляется."""

    def check(self, findings, refs):
        return ledger.selfcheck(findings, _measure(), refs)

    def test_parallel_two_lenses_is_clean(self):
        res = self.check(
            [_u2_finding()],
            [_u2_refutation(), _u2_refutation(lens="L4 лечение дороже", verdict="demoted")],
        )
        self.assertEqual(res["problems"], [])
        self.assertEqual(res["refutation"]["mode"], "parallel")
        self.assertNotIn("ceiling_cap", res["refutation"])

    def test_sequential_caps_verdict_to_finding(self):
        res = self.check(
            [_u2_finding()],
            [
                _u2_refutation(mode="sequential"),
                _u2_refutation(mode="sequential", lens="L3 цена завышена"),
            ],
        )
        self.assertEqual(res["result"], "BROKEN")
        self.assertTrue(any("последовательно" in p["problem"] for p in res["problems"]))
        self.assertEqual(res["refutation"]["ceiling_cap"], "finding")
        self.assertEqual(res["refutation"]["sequential_findings"], ["F1"])
        self.assertIn("потолок", res["refutation"]["disclosure"].lower())

    def test_sequential_is_fine_below_verdict(self):
        """Деградация режет только верхний статус: находка на finding при
        последовательных линзах — честный результат, а не брак."""
        res = self.check(
            [_u2_finding(confidence="finding", priority="7")],
            [_u2_refutation(mode="sequential")],
        )
        self.assertEqual(res["problems"], [])
        self.assertEqual(res["refutation"]["mode"], "sequential")

    def test_one_lens_four_times_is_not_refutation(self):
        res = self.check([_u2_finding()], [_u2_refutation(), _u2_refutation(reason="то же")])
        self.assertTrue(any("одной линзе" in p["problem"] for p in res["problems"]))

    def test_undeclared_mode_is_a_problem(self):
        res = self.check(
            [_u2_finding()],
            [_u2_refutation(mode=None), _u2_refutation(lens="L2 метрика", mode=None)],
        )
        self.assertTrue(any("не объявлен mode" in p["problem"] for p in res["problems"]))

    def test_mixed_mode_still_discloses(self):
        res = self.check(
            [_u2_finding()],
            [_u2_refutation(), _u2_refutation(lens="L2 метрика", mode="sequential")],
        )
        self.assertEqual(res["refutation"]["mode"], "mixed")
        self.assertIn("disclosure", res["refutation"])
        # у самой находки parallel-линза есть — потолок ей не режется
        self.assertEqual(res["refutation"]["sequential_findings"], [])

    def test_missing_refutation_still_caught(self):
        res = self.check([_u2_finding()], [])
        self.assertTrue(any("опровержения нет" in p["problem"] for p in res["problems"]))
        self.assertEqual(res["refutation"]["mode"], "none")


class TestLensKey(unittest.TestCase):
    def test_label_wins_over_free_text(self):
        self.assertEqual(ledger.lens_key("L2 метрика меряет не то"), "L2")
        self.assertEqual(ledger.lens_key("  L4  лечение дороже"), "L4")

    def test_free_text_normalised(self):
        self.assertEqual(ledger.lens_key(" Своими Словами "), "своими словами")
        self.assertEqual(ledger.lens_key(None), "<без линзы>")

    def test_l5_is_not_a_label(self):
        self.assertEqual(ledger.lens_key("L5 выдуманная"), "l5 выдуманная")
