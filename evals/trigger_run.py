#!/usr/bin/env python3
"""Trigger-eval: проверка frontmatter-описания, а не скриптов.

Скрипты зодчего покрыты 186 тестами. Описание, по которому харнесс решает
звать его или нет, до сих пор только вычитывалось глазами — а это ровно тот
класс ошибки, который глазами не ловится: описание, попадающее в свои же
формулировки, на живой речи молчит.

Прогон требует модели, поэтому разнесён на два шага:

    python3 evals/trigger_run.py --emit            # задачи, по одной на агента
    python3 evals/trigger_run.py --score ans.json  # метрики и вердикт

Между шагами задачи прогоняются: `--backend cli` (`claude -p`, если CLI
авторизован) или руками субагентами харнесса. Агент получает каталог и
запрос — и НЕ получает подсказки, какой ответ ожидается.

Пороги заданы здесь, до прогона. Порог, выставленный после того, как увидели
число, — это не порог, а его имитация.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
CASES = os.path.join(HERE, "trigger_cases.json")
ROSTER = os.path.join(HERE, "trigger_roster.json")
RESULTS = os.path.join(HERE, "trigger_results.json")
SKILL_MD = os.path.join(SKILL, "SKILL.md")

NAME = "zodchiy"

# Пороги. Выставлены ДО первого прогона.
#
# recall — доля should-кейсов, где выбран зодчий. fp — доля should-not-кейсов,
# где он выбран ошибочно. Разные пороги для train и test не нужны: правится
# описание по train, а меряется по test, и планка обязана быть одна, иначе
# «подошло по train» станет самостоятельным успехом.
MIN_RECALL = 0.80
MAX_FP = 0.25

# Контроль харнесса. Описание из ЧУЖОЙ области обязано перестать ловить —
# иначе агент угадывает по формулировке запроса, а не читает описание, и
# зелёный базового прогона ничего не значит.
#
# Первая попытка контроля была неверной: «бледное» описание оказалось не
# бледным, а более общим, и поймало ЛУЧШЕ настоящего (4/4 против 3/4). Ушло
# в `broad` как измеренный факт, контролем служит `offdomain`.
CONTROL_MAX_RECALL = 0.25


# ── Материал ────────────────────────────────────────────────────────────────


def skill_description(path: str = SKILL_MD) -> str:
    """Описание из frontmatter — живьём, а не копией.

    Копия в фикстуре разъедется с SKILL.md на первой же правке, и eval
    начнёт мерить прошлое состояние, показывая зелёный.
    """
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        raise SystemExit("нет frontmatter в SKILL.md — мерить нечего")
    block = m.group(1)
    d = re.search(r"^description:\s*(.+?)(?=\n[a-z_]+:|\Z)", block, re.S | re.M)
    if not d:
        raise SystemExit("нет поля description в frontmatter")
    value = " ".join(d.group(1).split())
    if value[:1] in ('"', "'") and value[-1:] == value[:1]:
        value = value[1:-1]
    return value


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def catalogue(case_id: str, variant: str) -> list[dict]:
    """Каталог для одного кейса.

    Порядок перемешан детерминированно по идентификатору кейса: список, где
    зодчий всегда первый, мерил бы позицию, а не описание. Детерминированно —
    чтобы повтор прогона сравнивался с предыдущим.
    """
    roster = load(ROSTER)
    if variant == "baseline":
        desc = skill_description()
    else:
        try:
            desc = roster["control_variants"][variant]["description"]
        except KeyError:
            raise SystemExit(f"нет варианта {variant} в trigger_roster.json")
    items = list(roster["competitors"]) + [{"name": NAME, "description": desc}]
    seed = hashlib.sha256(f"{case_id}:{variant}".encode()).digest()
    return sorted(items, key=lambda it: hashlib.sha256(seed + it["name"].encode()).digest())


def agent_prompt(case: dict, variant: str) -> str:
    lines = [
        "Ты выбираешь ОДИН инструмент из каталога под запрос пользователя.",
        "",
        "КАТАЛОГ:",
    ]
    for it in catalogue(case["id"], variant):
        lines.append(f"- {it['name']}: {it['description']}")
    lines += [
        "",
        f"ЗАПРОС ПОЛЬЗОВАТЕЛЯ: «{case['prompt']}»",
        "",
        "Ответь одним словом — именем инструмента из каталога, который следует",
        "вызвать. Если ни один не подходит, ответь none. Только имя, без пояснений.",
    ]
    return "\n".join(lines)


def tasks(split: str, variant: str) -> list[dict]:
    cases = load(CASES)["cases"]
    if split != "all":
        cases = [c for c in cases if c["split"] == split]
    return [
        {
            "id": c["id"],
            "split": c["split"],
            "variant": variant,
            "prompt": agent_prompt(c, variant),
        }
        for c in cases
    ]


# ── Прогон ──────────────────────────────────────────────────────────────────


def run_cli(task: dict, model: str, timeout: int) -> str:
    """Прогон одного кейса через `claude -p`.

    Изоляция обязательна: без неё агент видит настоящий каталог скиллов
    машины и отвечает по нему, а не по каталогу из промпта.
    """
    cmd = [
        "claude", "-p",
        "--safe-mode",
        "--no-session-persistence",
        "--output-format", "json",
        "--model", model,
        "--system-prompt", "Ты маршрутизатор инструментов. Отвечай одним именем.",
        task["prompt"],
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"__error__:{type(e).__name__}"
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return "__error__:bad-json"
    if payload.get("is_error"):
        return f"__error__:{str(payload.get('result'))[:80]}"
    return normalise(str(payload.get("result", "")))


def normalise(answer: str) -> str:
    """Ответ агента → имя из каталога.

    Агент отвечает то именем, то именем в кавычках, то фразой. Нормализация
    не должна быть щедрой: `none` и неузнанное — разные исходы, и сливать их
    нельзя, иначе провал замера будет читаться как отказ модели.
    """
    a = answer.strip().strip("`\"'*. \n").lower()
    a = a.split("\n")[0].strip()
    known = {it["name"] for it in load(ROSTER)["competitors"]} | {NAME, "none"}
    if a in known:
        return a
    for k in sorted(known, key=len, reverse=True):
        if re.search(rf"\b{re.escape(k)}\b", a):
            return k
    return f"__unparsed__:{a[:60]}"


# ── Счёт ────────────────────────────────────────────────────────────────────


def score(answers: dict, variant: str = "baseline") -> dict:
    cases = {c["id"]: c for c in load(CASES)["cases"]}
    missing = [i for i in cases if i not in answers]
    rows, buckets = [], {}
    for cid, ans in sorted(answers.items()):
        c = cases.get(cid)
        if c is None:
            continue
        picked = ans if ans.startswith("__") else normalise(ans)
        hit = picked == NAME
        ok = hit == c["should_trigger"]
        rows.append({
            "id": cid,
            "split": c["split"],
            "should_trigger": c["should_trigger"],
            "picked": picked,
            "ok": ok,
            "route_to": c.get("route_to"),
        })
        key = (c["split"], c["should_trigger"])
        buckets.setdefault(key, []).append(hit)

    def share(split: str, should: bool) -> float | None:
        vals = buckets.get((split, should))
        return None if not vals else sum(vals) / len(vals)

    metrics = {
        "train_recall": share("train", True),
        "train_fp": share("train", False),
        "test_recall": share("test", True),
        "test_fp": share("test", False),
    }
    # Вердикт по порогам выносится только основному прогону. Контрольные
    # варианты меряют харнесс, а не описание, и «pass» у них читался бы как
    # «описание в порядке».
    verdict = "pass" if variant == "baseline" else "порогами не судится"
    reasons = []
    # Полнота требуется только от основного прогона: контрольные варианты
    # гоняются на подмножестве намеренно.
    if missing and variant == "baseline":
        verdict = "incomplete"
        reasons.append(f"нет ответов: {', '.join(sorted(missing))}")
    if any(r["picked"].startswith("__") for r in rows):
        verdict = "incomplete"
        reasons.append("есть неразобранные ответы — прогон не засчитан")
    if variant == "baseline":
        for k, v in metrics.items():
            if v is None:
                continue
            if k.endswith("_recall") and v < MIN_RECALL:
                verdict = "fail"
                reasons.append(f"{k}={v:.2f} < {MIN_RECALL}")
            if k.endswith("_fp") and v > MAX_FP:
                verdict = "fail"
                reasons.append(f"{k}={v:.2f} > {MAX_FP}")
    return {"variant": variant, "metrics": metrics, "rows": rows,
            "verdict": verdict, "reasons": reasons}


def check_control(base: dict, control: dict) -> tuple[bool, str]:
    """Умеет ли замер краснеть. Сравниваются только общие кейсы."""
    ids = {r["id"] for r in control["rows"]}

    def recall(res):
        vals = [r for r in res["rows"] if r["id"] in ids and r["should_trigger"]]
        return None if not vals else sum(r["picked"] == NAME for r in vals) / len(vals)

    b, c = recall(base), recall(control)
    if b is None or c is None:
        return False, "нет общих should-кейсов — контроль не проведён"
    ok = c <= CONTROL_MAX_RECALL
    return ok, f"чужая область {c:.2f} против базы {b:.2f} на тех же кейсах при пороге {CONTROL_MAX_RECALL:.2f}"


# ── Печать ──────────────────────────────────────────────────────────────────


def fmt(v) -> str:
    return "—" if v is None else f"{v:.2f}"


def sensitivity(before: dict, after: dict) -> list[str]:
    """Кейсы, сменившие исход между двумя описаниями.

    Прямое доказательство того, что замер видит описание. Контроль чужой
    областью его не заменяет: имя скилла агенту тоже видно, и на нём одном
    часть запросов доходит до цели.
    """
    b = {r["id"]: r["picked"] for r in before["rows"]}
    return sorted(r["id"] for r in after["rows"] if r["id"] in b and b[r["id"]] != r["picked"])


def report(res: dict) -> None:
    print(f"вариант: {res['variant']}")
    for r in res["rows"]:
        mark = "ok  " if r["ok"] else "FAIL"
        want = NAME if r["should_trigger"] else f"не {NAME}"
        print(f"  {mark} [{r['id']}] {r['split']:5} ждали {want:12} → {r['picked']}")
    print()
    for k, v in res["metrics"].items():
        print(f"  {k:14} {'—' if v is None else f'{v:.2f}'}")
    print(f"\n  вердикт: {res['verdict']}")
    for why in res["reasons"]:
        print(f"    · {why}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Trigger-eval описания зодчего")
    ap.add_argument("--emit", action="store_true", help="выдать задачи для агентов (JSON)")
    ap.add_argument("--run", action="store_true", help="прогнать через backend")
    ap.add_argument("--score", metavar="FILE", help="посчитать по файлу ответов {id: имя}")
    ap.add_argument("--control-score", metavar="FILE", help="ответы контрольного прогона")
    ap.add_argument("--previous", metavar="FILE",
                    help="прогон ДО правки описания — попадает в результаты как доказательство "
                         "чувствительности замера к описанию")
    ap.add_argument("--variant", default="baseline",
                    help="baseline или ключ из control_variants в trigger_roster.json")
    ap.add_argument("--split", choices=("train", "test", "all"), default="all")
    ap.add_argument("--backend", choices=("cli",), default="cli")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--save", action="store_true", help="записать trigger_results.json")
    a = ap.parse_args()

    if a.emit:
        print(json.dumps(tasks(a.split, a.variant), ensure_ascii=False, indent=2))
        return 0

    if a.run:
        answers = {}
        for t in tasks(a.split, a.variant):
            answers[t["id"]] = run_cli(t, a.model, a.timeout)
            print(f"  [{t['id']}] → {answers[t['id']]}", file=sys.stderr)
        print(json.dumps(answers, ensure_ascii=False, indent=2))
        return 0

    if not a.score:
        ap.print_help()
        return 2

    base = score(load(a.score), "baseline")
    report(base)
    rc = 0 if base["verdict"] == "pass" else 1
    payload = {"baseline": base}

    if a.previous:
        prev = score(load(a.previous), "baseline")
        changed = sensitivity(prev, base)
        print(f"\n  до правки описания: train {fmt(prev['metrics']['train_recall'])} / "
              f"test {fmt(prev['metrics']['test_recall'])}, вердикт {prev['verdict']}")
        print(f"  чувствительность к описанию: {len(changed)} кейсов сменили исход "
              f"({', '.join(changed) if changed else 'ни одного'})")
        payload["baseline_before"] = prev
        payload["sensitivity"] = {"changed_cases": changed}
        if not changed:
            print("  FAIL: правка описания ничего не изменила — замер описание не видит")
            rc |= 1

    if a.control_score:
        ctl = score(load(a.control_score), "offdomain")
        print()
        report(ctl)
        ok, why = check_control(base, ctl)
        print(f"\n  контроль харнесса: {'ok' if ok else 'FAIL'} — {why}")
        payload["control"] = ctl
        payload["harness_control"] = {"ok": ok, "detail": why}
        rc |= 0 if ok else 1

    if a.save:
        payload["thresholds"] = {"min_recall": MIN_RECALL, "max_fp": MAX_FP,
                                 "control_max_recall": CONTROL_MAX_RECALL}
        payload["description_measured"] = skill_description()
        with open(RESULTS, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"\n  записано: {os.path.relpath(RESULTS, SKILL)}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
