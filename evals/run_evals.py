#!/usr/bin/env python3
"""Прогон evals зодчего.

    python3 run_evals.py                # unit + frozen
    python3 run_evals.py --unit         # только юнит-тесты скриптов
    python3 run_evals.py --frozen       # только сверка measure.py с ground truth
    python3 run_evals.py --robust       # вырожденные репозитории: пустой, битый UTF-8, detached
    python3 run_evals.py --perf         # бюджет времени замера
    python3 run_evals.py --trigger      # печать материала для trigger-eval (модель нужна)
    python3 run_evals.py --rebuild      # пересобрать фикстуры принудительно

Exit 1 при любом расхождении. Frozen-слой не зовёт модель вовсе: он ловит
регрессию СКРИПТОВ (порог поехал, поле переименовали, нормализация сломалась).

Неизвестный ключ ожидания — ошибка, а не пропуск. Защита, которая при
несовпадении тихо проходит мимо, — ровно тот дефект, который скилл ищет
у других.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "fixtures"))
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import build as fixtures_build  # noqa: E402
import ledger  # noqa: E402

CORPUS = os.path.join(HERE, "benchmark_corpus.json")
TRIGGER = os.path.join(HERE, "trigger_cases.json")


# ── Доступ к measure.json ───────────────────────────────────────────────────


def _hotspot(m, path):
    for h in m["behavior"].get("hotspots", []):
        if h["file"] == path:
            return h
    return None


def _file_metrics(m, path):
    for f in m["structure"].get("complex_files", []):
        if f["file"] == path:
            return f
    for h in m["structure"].get("hubs", []):
        if h["file"] == path:
            return h
    return None


def _fan_in(m, path):
    for r in m["structure"].get("fan_in_top", []):
        if r["file"] == path:
            return r["value"]
    for h in m["structure"].get("hubs", []):
        if h["file"] == path:
            return h.get("fan_in", 0)
    return 0


def _unstable(m, path):
    for r in m["behavior"].get("stability", {}).get("unstable_files", []):
        if r["file"] == path:
            return r
    return None


def _slow(m, path):
    for r in m["behavior"].get("velocity", {}).get("slowest_files", []):
        if r["file"] == path:
            return r
    return None


def _layer_touch(m, layer):
    return m["behavior"].get("velocity", {}).get("touch_cost", {}).get("by_layer_median", {}).get(layer)


def _has_edge(adj, a, b):
    return b in adj.get(a, [])


def _coupled(m, a, b):
    for r in m["behavior"].get("temporal_coupling", []):
        if {r["a"], r["b"]} == {a, b}:
            return r
    return None


# ── Словарь ожиданий ────────────────────────────────────────────────────────
# Каждая функция возвращает список строк-расхождений (пустой = сошлось).


def _eq(name, got, want):
    return [] if got == want else [f"{name}: получено {got!r}, ожидалось {want!r}"]


CHECKS = {
    "calibration_passed": lambda m, s, v: _eq(
        "calibration.passed", m["calibration"]["passed"], v
    ),
    "confidence_ceiling": lambda m, s, v: _eq(
        "confidence.ceiling", m["confidence"]["ceiling"], v
    ),
    "behavior_available": lambda m, s, v: _eq(
        "behavior.available", m["behavior"].get("available"), v
    ),
    "runtime_cycles": lambda m, s, v: _eq(
        "runtime-циклов", len(m["structure"].get("cycles", [])), v
    ),
    "type_only_cycles": lambda m, s, v: _eq(
        "type-only циклов", len(m["structure"].get("cycles_type_only", [])), v
    ),
    "type_only_cycles_min": lambda m, s, v: (
        []
        if len(m["structure"].get("cycles_type_only", [])) >= v
        else [f"type-only циклов {len(m['structure'].get('cycles_type_only', []))} < {v}"]
    ),
    "type_only_edges_min": lambda m, s, v: (
        []
        if m["structure"].get("type_only_edges", 0) >= v
        else [f"type_only_edges {m['structure'].get('type_only_edges', 0)} < {v}"]
    ),
    "cycle_members_include": lambda m, s, v: [
        f"{f} не входит ни в один рантайм-цикл"
        for f in v
        if f not in {x for c in m["structure"].get("cycles", []) for x in c["members"]}
    ],
    "edge_present": lambda m, s, v: [
        f"нет ребра {a} -> {b}"
        for a, b in v
        if not _has_edge(m["structure"].get("adjacency", {}), a, b)
    ],
    "edge_absent": lambda m, s, v: [
        f"лишнее ребро {a} -> {b}"
        for a, b in v
        if _has_edge(m["structure"].get("adjacency", {}), a, b)
    ],
    "edge_through_barrel_present": lambda m, s, v: [
        f"нет ребра сквозь barrel {a} -> {b}"
        for a, b in v
        if not _has_edge(m["structure"].get("adjacency_through_barrels", {}), a, b)
    ],
    "edge_through_barrel_absent": lambda m, s, v: [
        f"лишнее ребро сквозь barrel {a} -> {b}"
        for a, b in v
        if _has_edge(m["structure"].get("adjacency_through_barrels", {}), a, b)
    ],
    "barrels_include": lambda m, s, v: [
        f"{f} не опознан как barrel" for f in v if f not in m["structure"].get("barrels", [])
    ],
    "barrels_exclude": lambda m, s, v: [
        f"{f} ошибочно опознан как barrel" for f in v if f in m["structure"].get("barrels", [])
    ],
    "fan_in_at_least": lambda m, s, v: [
        f"fan_in {f} = {_fan_in(m, f)} < {n}" for f, n in v.items() if _fan_in(m, f) < n
    ],
    "coupled_pairs_include": lambda m, s, v: [
        f"пара {a} + {b} не попала в temporal_coupling"
        for a, b in v
        if _coupled(m, a, b) is None
    ],
    "coupling_degree_at_least": lambda m, s, v: [
        msg
        for key, n in v.items()
        for msg in _degree_msg(m, key, n)
    ],
    "edits_at_least": lambda m, s, v: [
        f"{f}: правок {(_hotspot(m, f) or {}).get('edits')} < {n}"
        for f, n in v.items()
        if (_hotspot(m, f) or {}).get("edits", 0) < n
    ],
    "fix_share_at_least": lambda m, s, v: [
        f"{f}: fix_share {(_hotspot(m, f) or {}).get('fix_share')} < {n}"
        for f, n in v.items()
        if (_hotspot(m, f) or {}).get("fix_share", 0) < n
    ],
    "fix_share_at_most": lambda m, s, v: [
        f"{f}: fix_share {(_hotspot(m, f) or {}).get('fix_share')} > {n}"
        for f, n in v.items()
        if (_hotspot(m, f) or {}).get("fix_share", 1) > n
    ],
    "painful_files_include": lambda m, s, v: [
        f"{f} не попал в painful_files (по нему считает гейт)"
        for f in v
        if f not in s["painful_files"]
    ],
    "painful_files_exclude": lambda m, s, v: [
        f"{f} ошибочно в painful_files" for f in v if f in s["painful_files"]
    ],
    "per_function_max_at_least": lambda m, s, v: [
        f"{f}: cc-на-функцию {(_file_metrics(m, f) or {}).get('cyclomatic_per_function_max')} < {n}"
        for f, n in v.items()
        if (_file_metrics(m, f) or {}).get("cyclomatic_per_function_max", -1) < n
    ],
    "per_function_max_at_most": lambda m, s, v: [
        f"{f}: cc-на-функцию {(_file_metrics(m, f) or {}).get('cyclomatic_per_function_max')} > {n}"
        for f, n in v.items()
        if (_file_metrics(m, f) or {}).get("cyclomatic_per_function_max", 10**9) > n
    ],
    "cyclomatic_total_at_least": lambda m, s, v: [
        f"{f}: cc-по-файлу {(_file_metrics(m, f) or {}).get('cyclomatic_total')} < {n}"
        for f, n in v.items()
        if (_file_metrics(m, f) or {}).get("cyclomatic_total", -1) < n
    ],
    "functions_eq": lambda m, s, v: [
        f"{f}: функций {(_file_metrics(m, f) or {}).get('functions')}, ожидалось {n}"
        for f, n in v.items()
        if (_file_metrics(m, f) or {}).get("functions") != n
    ],
    "rework_rate_at_least": lambda m, s, v: [
        f"{f}: rework_rate {(_unstable(m, f) or {}).get('rework_rate')} < {n}"
        for f, n in v.items()
        if (_unstable(m, f) or {}).get("rework_rate", -1) < n
    ],
    "rework_rate_at_most": lambda m, s, v: [
        f"{f}: rework_rate {(_unstable(m, f) or {}).get('rework_rate')} > {n}"
        for f, n in v.items()
        if (_unstable(m, f) or {}).get("rework_rate", 10**9) > n
    ],
    "rework_lb_at_least": lambda m, s, v: [
        f"{f}: rework_rate_lb {(_unstable(m, f) or {}).get('rework_rate_lb')} < {n}"
        for f, n in v.items()
        if (_unstable(m, f) or {}).get("rework_rate_lb", -1) < n
    ],
    "unstable_include": lambda m, s, v: [
        f"{f} не попал в stability.unstable_files" for f in v if _unstable(m, f) is None
    ],
    "ledger_unstable_include": lambda m, s, v: [
        f"{f} не попал в снимок unstable_files (по нему считает гейт)"
        for f in v
        if f not in s.get("unstable_files", [])
    ],
    "ledger_unstable_exclude": lambda m, s, v: [
        f"{f} ошибочно в снимке unstable_files" for f in v if f in s.get("unstable_files", [])
    ],
    "touch_cost_layer_at_least": lambda m, s, v: [
        f"слой {k}: touch_cost {_layer_touch(m, k)} < {n}"
        for k, n in v.items()
        if (_layer_touch(m, k) or -1) < n
    ],
    "touch_cost_layer_at_most": lambda m, s, v: [
        f"слой {k}: touch_cost {_layer_touch(m, k)} > {n}"
        for k, n in v.items()
        if (_layer_touch(m, k) if _layer_touch(m, k) is not None else 10**9) > n
    ],
    "episode_commits_at_least": lambda m, s, v: [
        f"{f}: median_commits {(_slow(m, f) or {}).get('median_commits')} < {n}"
        for f, n in v.items()
        if (_slow(m, f) or {}).get("median_commits", -1) < n
    ],
}


def _degree_msg(m, key, n):
    a, b = key.split("|")
    r = _coupled(m, a, b)
    if r is None:
        return [f"пара {a} + {b} не найдена, степень не проверить"]
    return [] if r["degree"] >= n else [f"степень пары {a} + {b} = {r['degree']} < {n}"]


# ── Frozen-слой ─────────────────────────────────────────────────────────────


def measure_fixture(name: str, rebuild: bool) -> dict:
    repo = fixtures_build.build(name, rebuild=rebuild)
    out = os.path.join(repo, ".zodchiy", "measure.json")
    # measure.json НЕ кэшируется: он и есть выход того кода, который проверяем.
    # Кэшируется только сам репозиторий-фикстура — его сборка дорога, а содержимое
    # от правки скриптов не зависит.
    proc = subprocess.run(
        [sys.executable, os.path.join(SKILL, "scripts", "measure.py"), repo, "--out", out],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"measure.py упал на {name}: {proc.stderr[-400:]}")
    with open(out, encoding="utf-8") as fh:
        return json.load(fh)


def run_frozen(rebuild: bool = False) -> int:
    # Ground truth корпуса снят с разбором деревом. Без грамматик разбор
    # уходит в регулярки, часть кейсов расходится — и красный выглядит как
    # дефект метрики, хотя это отсутствующая зависимость. Говорим прямо.
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_python  # noqa: F401
    except ImportError:
        print(
            "frozen: нужен tree-sitter — ground truth снят с разбором деревом.\n"
            "        pip install tree-sitter tree-sitter-python tree-sitter-typescript\n"
            "        без него гоняй --unit --robust: они от грамматик не зависят",
            file=sys.stderr,
        )
        return 1
    corpus = json.load(open(CORPUS, encoding="utf-8"))
    cases = corpus["cases"]

    fails: list[str] = []
    ids = {c["id"] for c in cases}
    for c in cases:  # правило один-к-одному: у грязного кейса обязана быть пара
        if c["pair"] not in ids:
            fails.append(f"[{c['id']}] пара {c['pair']} не существует")
        elif next(x for x in cases if x["id"] == c["pair"])["polarity"] == c["polarity"]:
            fails.append(f"[{c['id']}] пара {c['pair']} той же полярности — контроля нет")

    cache: dict[str, tuple] = {}
    ok = 0
    for c in cases:
        fx = c["fixture"]
        if fx not in cache:
            m = measure_fixture(fx, rebuild)
            cache[fx] = (m, ledger.snapshot(m))
        m, snap = cache[fx]
        errs: list[str] = []
        for key, want in c["expect"].items():
            fn = CHECKS.get(key)
            if fn is None:
                errs.append(f"неизвестное ожидание {key!r} — опечатка в корпусе")
                continue
            errs.extend(fn(m, snap, want))
        if errs:
            fails.extend(f"[{c['id']}] {e}" for e in errs)
            print(f"FAIL {c['id']} ({c['risk']}, {c['polarity']})")
            for e in errs:
                print(f"      {e}")
        else:
            ok += 1
            print(f"ok   {c['id']} ({c['risk']}, {c['polarity']})")

    print(f"\nfrozen: {ok}/{len(cases)} сошлось")
    return 1 if fails else 0


# ── Юнит-слой ───────────────────────────────────────────────────────────────


def run_unit() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(HERE, pattern="test_*.py", top_level_dir=HERE)
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if res.wasSuccessful() else 1



# ── Устойчивость самого замера ──────────────────────────────────────────────


def _degenerate_repos(root: str) -> list[tuple[str, str]]:
    """Репозитории-уроды. На них скрипты обязаны отвечать явным полем, а не
    трассировкой: «не смог» — это тоже результат, и он должен быть читаемым."""
    import subprocess as sp

    def git(path, *a, **kw):
        return sp.run(["git", "-C", path, *a], capture_output=True, text=True, **kw)

    made = []

    def mk(name):
        d = os.path.join(root, name)
        os.makedirs(d)
        sp.run(["git", "-c", "init.defaultBranch=main", "init", "-q", d], capture_output=True)
        git(d, "config", "user.email", "t@example.test")
        git(d, "config", "user.name", "T")
        return d

    d = mk("empty-repo")
    made.append(("репозиторий без коммитов", d))

    d = mk("single-commit")
    open(os.path.join(d, "a.py"), "w").write("def a():\n    return 1\n")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "feat: a")
    made.append(("один коммит", d))

    d = mk("broken-utf8")
    open(os.path.join(d, "bad.py"), "wb").write(b"x = '\xff\xfe not utf8'\n")
    open(os.path.join(d, "ok.py"), "w").write("def ok():\n    return 1\n")
    git(d, "add", "-A")
    git(d, "commit", "-q", "-m", "feat: bad bytes")
    made.append(("файл не в UTF-8", d))

    d = mk("detached")
    for i in range(2):
        open(os.path.join(d, f"m{i}.py"), "w").write("x = 1\n")
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", f"feat: {i}")
    sha = git(d, "rev-parse", "HEAD~1").stdout.strip()
    git(d, "checkout", "-q", sha)
    made.append(("detached HEAD", d))

    d = os.path.join(root, "not-a-repo")
    os.makedirs(d)
    open(os.path.join(d, "a.py"), "w").write("x = 1\n")
    made.append(("не git-репозиторий", d))

    return made


def run_robust() -> int:
    import tempfile

    fails = []
    with tempfile.TemporaryDirectory() as root:
        cases = _degenerate_repos(root)
        # Отдельный случай: история есть, но целиком вне окна замера.
        cases.append(("история вне окна --since", cases[1][1]))
        for i, (name, repo) in enumerate(cases):
            out = os.path.join(root, f"m{i}.json")
            extra = ["--since", "1.minutes.ago"] if "вне окна" in name else []
            proc = subprocess.run(
                [sys.executable, os.path.join(SKILL, "scripts", "measure.py"), repo, "--out", out, *extra],
                capture_output=True,
                text=True,
                timeout=300,
            )
            errs = []
            if proc.returncode != 0:
                errs.append(f"measure.py вернул {proc.returncode}")
            if "Traceback" in proc.stderr:
                errs.append("трассировка в stderr")
            if not os.path.exists(out):
                errs.append("файл замера не создан")
            else:
                with open(out, encoding="utf-8") as fh:
                    m = json.load(fh)
                b, st = m.get("behavior", {}), m.get("structure", {})
                if b.get("available") is not False and "error" not in b:
                    errs.append("поведенческая ось не объявила себя недоступной")
                if "Traceback" in json.dumps(m, ensure_ascii=False):
                    errs.append("трассировка внутри JSON")
                if m.get("confidence", {}).get("ceiling") != "finding":
                    errs.append("потолок не опущен до finding")
                if st.get("error") and "не найдено файлов" not in st["error"]:
                    errs.append(f"структурная ось упала неожиданно: {st['error'][:80]}")
            if errs:
                fails += [f"[{name}] {e}" for e in errs]
                print(f"FAIL {name}")
                for e in errs:
                    print(f"      {e}")
            else:
                print(f"ok   {name}")
    print(f"\nrobust: {len(cases) - len({f.split(']')[0] for f in fails})}/{len(cases)} без сюрпризов")
    return 1 if fails else 0


def run_perf(budget_fixture: float = 10.0, budget_real: float = 30.0) -> int:
    """Бюджет времени. Без него регрессия скорости замера видна только тогда,
    когда прогон уже стал невыносимым."""
    import time as _time

    targets = [("фикстура churn-pain-vs-extension", fixtures_build.build("churn-pain-vs-extension"), budget_fixture)]
    real = os.path.expanduser("~/projects/repo-A")
    if os.path.isdir(os.path.join(real, ".git")):
        targets.append(("repo-A (495 файлов, 365 коммитов)", real, budget_real))

    rc = 0
    for name, repo, budget in targets:
        out = os.path.join(HERE, ".perf.json")
        t0 = _time.monotonic()
        subprocess.run(
            [sys.executable, os.path.join(SKILL, "scripts", "measure.py"), repo, "--out", out],
            capture_output=True,
            text=True,
            timeout=900,
        )
        dt = _time.monotonic() - t0
        over = dt > budget
        rc |= 1 if over else 0
        print(f"{'FAIL' if over else 'ok  '} {name}: {dt:.1f} с при бюджете {budget:.0f} с")
        if os.path.exists(out):
            os.unlink(out)
    return rc


# ── Trigger-eval ────────────────────────────────────────────────────────────


def run_trigger() -> int:
    """Trigger-eval: материал для прогона и результат последнего прогона.

    Сам прогон здесь не запускается — он требует модели. Печатается то, что
    можно напечатать без неё: кейсы и вердикт из `trigger_results.json`,
    записанного `trigger_run.py`. Отсутствие файла — не «ok», а «не мерено».
    """
    cases = json.load(open(TRIGGER, encoding="utf-8"))
    should = [c for c in cases["cases"] if c["should_trigger"]]
    should_not = [c for c in cases["cases"] if not c["should_trigger"]]
    print(f"trigger-eval: {len(should)} should + {len(should_not)} should-not")
    print("Прогон: python3 evals/trigger_run.py --emit  →  агенты  →  --score")

    results = os.path.join(HERE, "trigger_results.json")
    if not os.path.exists(results):
        print("Результата прогона нет — слой не мерен.")
        return 1

    r = json.load(open(results, encoding="utf-8"))
    m = r["baseline"]["metrics"]
    print(f"\nпоследний прогон: вердикт {r['baseline']['verdict']}")
    print(f"  train recall {m['train_recall']:.2f} · fp {m['train_fp']:.2f}")
    print(f"  test  recall {m['test_recall']:.2f} · fp {m['test_fp']:.2f}")
    if "sensitivity" in r:
        n = len(r["sensitivity"]["changed_cases"])
        print(f"  чувствительность к описанию: {n} кейсов сменили исход после правки")
    if "harness_control" in r:
        c = r["harness_control"]
        print(f"  контроль чужой областью: {'ok' if c['ok'] else 'FAIL'} — {c['detail']}")
    return 0 if r["baseline"]["verdict"] == "pass" else 1


def main():
    ap = argparse.ArgumentParser(description="Evals зодчего")
    ap.add_argument("--unit", action="store_true")
    ap.add_argument("--frozen", action="store_true")
    ap.add_argument("--trigger", action="store_true")
    ap.add_argument("--robust", action="store_true")
    ap.add_argument("--perf", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    if not (a.unit or a.frozen or a.trigger or a.robust or a.perf):
        a.unit = a.frozen = a.robust = True

    rc = 0
    if a.unit:
        print("── юнит-слой " + "─" * 50)
        rc |= run_unit()
    if a.frozen:
        print("\n── frozen-слой " + "─" * 48)
        rc |= run_frozen(rebuild=a.rebuild)
    if a.robust:
        print("\n── устойчивость замера " + "─" * 40)
        rc |= run_robust()
    if a.perf:
        print("\n── бюджет времени " + "─" * 45)
        rc |= run_perf()
    if a.trigger:
        print("\n── trigger " + "─" * 52)
        rc |= run_trigger()
    sys.exit(rc)


if __name__ == "__main__":
    main()
