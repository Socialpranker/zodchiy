#!/usr/bin/env python3
"""Леджер находок, база сравнения между прогонами и CI-гейт.

Прогон без базы — снимок. Прогон с базой — ответ на вопрос «стало хуже или
лучше», а это и есть то, ради чего аудит повторяют.

    ledger.py snapshot <measure.json> [--out .zodchiy/baseline.json]
    ledger.py diff     <measure.json> --baseline .zodchiy/baseline.json
    ledger.py gate     <measure.json> --baseline ... [--max-new-cycles 0]
    ledger.py add      --findings .zodchiy/findings.csv --json '<находка>'
    ledger.py refute   --out .zodchiy/verify/refutation.json --json '<вердикт линзы>'
    ledger.py selfcheck --findings ... --measure ... [--refutations ...]
    ledger.py verify   --findings ... --measure ...      # сбылся ли прогноз gain

Гейт срабатывает на ДЕЛЬТУ, а не на абсолют: legacy-репо не должен падать
вечно из-за долга, который никто в этом PR не создавал.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import re
import sys
import time

FIELDS = [
    "id",
    "title",
    "risk",
    "symptom",
    "axes",
    "source",
    "cost_pain",
    "cost_spread",
    "priority",
    "remedy",
    # Цена лечения. «Лечение дороже болезни» перечислено как линза опровержения,
    # но сравнивать было нечем: цена проблемы измерялась, цена лечения — нет.
    "remedy_cost",
    "gain",
    # Прогноз в машинной форме: метрика снимка + целевое число. Без них `gain`
    # остаётся прозой, и следующий прогон не может его проверить — то есть
    # скилл требует измеримости от чужого кода и не требует от своих советов.
    "gain_metric",
    "gain_target",
    "gain_direction",
    "gain_actual",
    "gain_verdict",
    "verified_at",
    "alternatives",
    "refutation",
    "confidence",
    "status",
    "first_seen",
    "last_seen",
    "reopen_trigger",
]


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def snapshot(m: dict) -> dict:
    """Сжатый слепок прогона: только то, по чему считается тренд."""
    b, s = m.get("behavior", {}), m.get("structure", {})
    cont = b.get("containment", {})
    hot = b.get("hotspots", [])
    return {
        "snapshot": m.get("snapshot", {}),
        "calibration_passed": m.get("calibration", {}).get("passed"),
        "confidence_ceiling": m.get("confidence", {}).get("ceiling"),
        "files": s.get("files"),
        "edges": s.get("edges"),
        "runtime_cycles": len(s.get("cycles", [])),
        "cycle_members": sorted(
            {f for c in s.get("cycles", []) for f in c.get("members", [])}
        ),
        "containment_ratio": cont.get("ratio"),
        "coupling_pairs": b.get("temporal_coupling_total"),
        "commits_with_code": b.get("commits_with_code"),
        # Файлы, где болит: churn высок И доля багфиксов высока. Именно эта
        # пара, а не churn сам по себе: точка расширения тоже часто меняется.
        "painful_files": sorted(
            h["file"]
            for h in hot
            if h.get("fix_share", 0) >= 0.25 and h.get("edits", 0) >= 10
        ),
        "top_hotspots": [h["file"] for h in hot[:10]],
        # Цена изменения во времени и доля неудач — вторая половина «материальности».
        "touch_cost_p90": b.get("velocity", {}).get("touch_cost", {}).get("p90_files_per_commit"),
        "episodes_multi_share": b.get("velocity", {}).get("episodes", {}).get("multi_commit_share"),
        "rework_rate": b.get("stability", {}).get("rework_rate"),
        "fix_latency_median_days": b.get("stability", {}).get("fix_latency_median_days"),
        # Файл, чьи правки не держатся: нижняя граница доли доделок, а не сырая
        # доля — «5 из 5» иначе обгоняет «55 из 67».
        "unstable_files": sorted(
            u["file"]
            for u in b.get("stability", {}).get("unstable_files", [])
            if u.get("rework_rate_lb", 0) >= 0.5 and u.get("changes", 0) >= 10
        ),
    }


def diff(cur: dict, base: dict, max_new_cycles: int = 0) -> dict:
    """Что изменилось. Регрессия — то, чего раньше не было или стало хуже.

    `max_new_cycles` — порог числа новых рантайм-циклов (файлов, вошедших в
    цикл впервые); по умолчанию 0, то есть любой новый цикл — регрессия.
    """

    def d(key):
        a, b = cur.get(key), base.get(key)
        if a is None or b is None:
            return None
        return round(a - b, 4)

    new_cycles = sorted(set(cur["cycle_members"]) - set(base.get("cycle_members", [])))
    new_pain = sorted(set(cur["painful_files"]) - set(base.get("painful_files", [])))
    healed = sorted(set(base.get("painful_files", [])) - set(cur["painful_files"]))
    new_unstable = sorted(
        set(cur.get("unstable_files", [])) - set(base.get("unstable_files", []))
    )
    cont_d = d("containment_ratio")
    rework_d = d("rework_rate")

    regressions = []
    if len(new_cycles) > max_new_cycles:
        regressions.append({"kind": "новые рантайм-циклы", "items": new_cycles})
    if cont_d is not None and cont_d < -0.03:
        regressions.append(
            {
                "kind": "containment просел",
                "items": [f"{base['containment_ratio']} → {cur['containment_ratio']}"],
            }
        )
    if new_pain:
        regressions.append({"kind": "новые болевые файлы", "items": new_pain})
    if new_unstable:
        regressions.append({"kind": "правки перестали держаться", "items": new_unstable})
    if rework_d is not None and rework_d > 0.05:
        regressions.append(
            {
                "kind": "доля доделок выросла",
                "items": [f"{base['rework_rate']} → {cur['rework_rate']}"],
            }
        )

    return {
        "baseline_head": base.get("snapshot", {}).get("head"),
        "current_head": cur.get("snapshot", {}).get("head"),
        "delta": {
            k: d(k)
            for k in (
                "containment_ratio",
                "runtime_cycles",
                "coupling_pairs",
                "edges",
                "files",
                "rework_rate",
                "touch_cost_p90",
                "fix_latency_median_days",
            )
        },
        "new_runtime_cycles": new_cycles,
        "new_painful_files": new_pain,
        "new_unstable_files": new_unstable,
        "healed_files": healed,
        "regressions": regressions,
        "gate_result": "REGRESSION" if regressions else "OK",
    }



# ── Разрешение ссылки находки в measure.json ────────────────────────────────

PATH_SEG = re.compile(r"([A-Za-z_][\w]*)((?:\[[^\]]+\])*)")
PATH_IDX = re.compile(r"\[([^\]]+)\]")


def _split_path(path: str) -> list[str]:
    """Разбить путь по точкам ВНЕ скобок.

    Наивный `path.split(".")` рвёт `[file=repo-A/config.py]` пополам: точка есть
    и в разделителе, и в данных. Тот же класс, что `splitlines()` по `\x1e` —
    делитель, встречающийся внутри значения.
    """
    segs, buf, depth = [], [], 0
    for ch in path:
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        if ch == "." and depth == 0:
            segs.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    segs.append("".join(buf))
    return [x for x in segs if x]


def resolve_path(obj, path: str):
    """`behavior.hotspots[file=src/x.py].fix_share` -> значение или KeyError.

    Поле `source` находки обязано быть путём, а не прозой: «по данным замера»
    неотличимо от числа, названного по памяти, а железное правило скилла —
    число из скрипта. С путём сверка механическая.

    Поддерживает: `a.b`, `a[0]`, `a[key=value]`, `a["ключ/со/слэшем"]`.
    """
    cur = obj
    for raw in _split_path(path):
        m = PATH_SEG.fullmatch(raw.strip())
        if not m:
            raise KeyError(f"не разбирается сегмент {raw!r}")
        name, idx = m.group(1), m.group(2)
        if not isinstance(cur, dict) or name not in cur:
            raise KeyError(f"нет ключа {name!r}")
        cur = cur[name]
        for token in PATH_IDX.findall(idx):
            token = token.strip()
            if token.isdigit():
                cur = cur[int(token)]
            elif token[:1] in "\"'" and token[-1:] == token[:1]:
                cur = cur[token[1:-1]]
            elif "=" in token:
                k, v = token.split("=", 1)
                hit = next((x for x in cur if str(x.get(k)) == v), None)
                if hit is None:
                    raise KeyError(f"нет записи с {k}={v}")
                cur = hit
            else:
                raise KeyError(f"не разбирается индекс [{token}]")
    return cur


REQUIRED = (
    "id",
    "title",
    "risk",
    "symptom",
    "source",
    "cost_pain",
    "remedy",
    "remedy_cost",
    "gain",
    "gain_metric",
    "gain_target",
    "refutation",
    "confidence",
)

REFUTE_VERDICTS = ("survives", "demoted", "dropped")

# Как физически шёл шаг 4. Субагентов нет ни в одном харнессе поголовно, и
# «опровержение было» без этого поля означало бы разное в разных прогонах:
# четыре независимые линзы или один проход одной модели. Поле обязательное —
# умолчание здесь было бы тихой деградацией, ровно тем дефектом, который
# скилл ищет у других.
REFUTE_MODES = ("parallel", "sequential")


def read_findings(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_findings(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


SCHEMA_VERSION = "1.0"
SCHEMA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema", "findings.schema.json")
CEILINGS = ("hypothesis", "finding", "verdict")
FILE_IN_SOURCE = re.compile(r"\[file=([^\]]+)\]")


def _lower_ceiling(a: str, b: str) -> str:
    """Потолок прогона — минимум из потолков, а не последний назначенный."""
    order = {c: i for i, c in enumerate(CEILINGS)}
    return a if order.get(a, 99) <= order.get(b, 99) else b


def _sources(raw: str, measure: dict) -> list:
    """`source` в CSV — строка с путями через `;`. В документе это разрешённые
    значения: JSON должен читаться без measure.json под рукой, иначе он не
    машиночитаемый выход, а ссылка на файл, которого у читателя нет."""
    out = []
    for one in [x.strip() for x in (raw or "").split(";") if x.strip()]:
        item = {"path": one}
        try:
            item["resolved"] = resolve_path(measure, one)
        except (KeyError, IndexError, TypeError) as e:
            item["resolved"] = None
            item["error"] = str(e)
        out.append(item)
    return out


def document(findings, measure, refutations, generated_from: str | None = None) -> dict:
    """Находки в машиночитаемой форме. Markdown пишет модель — его объём и
    структура зависят от харнесса; в CI и в сравнении прогонов опираться
    можно только на это."""
    by_id = collections.defaultdict(list)
    for r in refutations:
        by_id[r.get("finding_id")].append(r)
    sequential = [
        fid
        for fid, refs in by_id.items()
        if not any(r.get("mode") == "parallel" for r in refs)
    ]
    ref_mode = refutation_mode(refutations, sequential)
    conf = measure.get("confidence", {}) or {}
    ceiling = conf.get("ceiling", "finding")
    reasons = list(conf.get("reasons", []))
    if ref_mode.get("ceiling_cap"):
        ceiling = _lower_ceiling(ceiling, ref_mode["ceiling_cap"])
        reasons.append(ref_mode["disclosure"])

    rows = []
    for row in findings:
        item = {k: v for k, v in row.items() if k in FIELDS and (v or "").strip()}
        item["source"] = _sources(row.get("source"), measure)
        files = sorted({m for one in item["source"] for m in FILE_IN_SOURCE.findall(one["path"])})
        if files:
            item["files"] = files
        if row.get("axes"):
            item["axes"] = [a.strip() for a in re.split(r"[,\s]+", row["axes"]) if a.strip()]
        try:
            item["priority"] = int(row.get("priority") or 0)
        except ValueError:
            item["priority"] = 0
        refs = [
            {k: r.get(k) for k in ("lens", "verdict", "reason", "mode")}
            for r in by_id.get(row.get("id"), [])
        ]
        if refs:
            item["refutations"] = refs
        rows.append(item)

    return {
        "tool": "zodchiy/findings",
        "schema_version": SCHEMA_VERSION,
        "repo": measure.get("repo", ""),
        "head": (measure.get("snapshot", {}) or {}).get("head", ""),
        "generated_from": generated_from or "",
        "confidence_ceiling": ceiling,
        "ceiling_reasons": reasons,
        "refutation": ref_mode,
        "findings": rows,
    }


def validate(doc, schema: dict, root: dict | None = None, path: str = "$") -> list:
    """Мини-валидатор под то подмножество JSON Schema, которым описан документ.

    Своё, а не `jsonschema`: у скилла ноль зависимостей вне stdlib, и заводить
    их ради проверки собственного выхода — цена выше пользы. Источник правды
    остаётся один — файл схемы; здесь только его исполнение.
    """
    root = root if root is not None else schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            return [f"{path}: неподдержанный $ref {ref}"]
        return validate(doc, root["$defs"][ref.split("/")[-1]], root, path)
    bad = []
    t = schema.get("type")
    types = {
        "object": dict, "array": list, "string": str,
        "integer": int, "number": (int, float), "boolean": bool,
    }
    if t and not isinstance(doc, types[t]):
        return [f"{path}: ожидался {t}, пришёл {type(doc).__name__}"]
    if t == "integer" and isinstance(doc, bool):
        return [f"{path}: ожидался integer, пришёл bool"]
    if "const" in schema and doc != schema["const"]:
        bad.append(f"{path}: ожидалось {schema['const']!r}")
    if "enum" in schema and doc not in schema["enum"]:
        bad.append(f"{path}: {doc!r} не из {schema['enum']}")
    if isinstance(doc, (int, float)) and not isinstance(doc, bool):
        if "minimum" in schema and doc < schema["minimum"]:
            bad.append(f"{path}: {doc} меньше минимума {schema['minimum']}")
        if "maximum" in schema and doc > schema["maximum"]:
            bad.append(f"{path}: {doc} больше максимума {schema['maximum']}")
    if isinstance(doc, dict):
        for k in schema.get("required", []):
            if k not in doc:
                bad.append(f"{path}: нет обязательного поля {k!r}")
        for k, sub in schema.get("properties", {}).items():
            if k in doc:
                bad += validate(doc[k], sub, root, f"{path}.{k}")
    if isinstance(doc, list) and "items" in schema:
        for i, el in enumerate(doc):
            bad += validate(el, schema["items"], root, f"{path}[{i}]")
    return bad


def validate_document(doc) -> list:
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    return validate(doc, schema, schema)


def sarif(doc: dict) -> dict:
    """SARIF 2.1.0 — общий язык с CI и code scanning.

    Уровень берётся из приоритета, а не из `confidence`: в CI важно «насколько
    больно», уверенность едет отдельным свойством и не превращает гипотезу в
    ошибку сборки молча."""
    rules, seen = [], {}
    for f in doc["findings"]:
        rid = f.get("risk") or "R0"
        if rid not in seen:
            seen[rid] = True
            rules.append({
                "id": rid,
                "name": rid,
                "shortDescription": {"text": f.get("title", rid)},
                "helpUri": "https://github.com/zodchiy/zodchiy/blob/main/references/risks.md",
            })
    results = []
    for f in doc["findings"]:
        prio = f.get("priority", 0)
        level = "error" if prio >= 8 else "warning" if prio >= 5 else "note"
        res = {
            "ruleId": f.get("risk") or "R0",
            "level": level,
            "message": {"text": f"{f.get('title', '')}. {f.get('symptom', '')}".strip()},
            "partialFingerprints": {"zodchiyFindingId": f.get("id", "")},
            "properties": {
                "priority": prio,
                "confidence": f.get("confidence", ""),
                "cost_pain": f.get("cost_pain", ""),
                "remedy": f.get("remedy", ""),
                "remedy_cost": f.get("remedy_cost", ""),
                "gain": f.get("gain", ""),
                "gain_metric": f.get("gain_metric", ""),
                "gain_target": f.get("gain_target", ""),
                "source": [s["path"] for s in f.get("source", [])],
            },
        }
        locs = [
            {"physicalLocation": {"artifactLocation": {"uri": u}}}
            for u in f.get("files", [])
        ]
        if locs:
            res["locations"] = locs
        results.append(res)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "zodchiy",
                "version": doc["schema_version"],
                "informationUri": "https://github.com/zodchiy/zodchiy",
                "rules": rules,
            }},
            "results": results,
            "properties": {
                "confidenceCeiling": doc["confidence_ceiling"],
                "ceilingReasons": doc.get("ceiling_reasons", []),
                "refutationMode": doc["refutation"]["mode"],
                # Деградация шага 4 обязана доехать и до машинного выхода:
                # в markdown её объявляет модель, здесь — скрипт.
                "disclosure": doc["refutation"].get("disclosure", ""),
            },
        }],
    }


def lens_key(raw) -> str:
    """Линзы записываются свободным текстом («L2 метрика меряет не то»).
    Различаем по метке L1..L4, иначе по самой строке: четыре записи одной
    линзы — это один взгляд, а не четыре."""
    m = re.match(r"\s*(L[1-4])\b", raw or "")
    return m.group(1) if m else (raw or "").strip().lower() or "<без линзы>"


def refutation_mode(refutations, sequential_findings) -> dict:
    """Как шёл шаг 4 и что из этого следует для отчёта.

    Молчаливая деградация здесь хуже её отсутствия: «опровержение было»
    станет неправдой. Поэтому режим не выводится по умолчанию, а собирается
    из объявленных и отдаётся строкой для секции «слепые зоны»."""
    modes = {r.get("mode") for r in refutations if r.get("mode")}
    if not refutations:
        mode = "none"
    elif modes == {"parallel"}:
        mode = "parallel"
    elif modes == {"sequential"}:
        mode = "sequential"
    else:
        mode = "mixed"
    out = {"mode": mode, "sequential_findings": sorted(set(sequential_findings))}
    if mode in ("sequential", "mixed"):
        out["ceiling_cap"] = "finding"
        out["disclosure"] = (
            "Шаг 4 шёл последовательно: одна модель, один проход, без независимых "
            "линз. Потолок уверенности — finding; статус verdict в этом прогоне "
            "недостижим."
        )
    return out


def selfcheck(findings, measure, refutations) -> dict:
    """Брак находки ловится механически, а не вычиткой отчёта.

    Три вещи, которые до сих пор держались только на дисциплине модели:
    поля на месте, `source` ведёт в реальное число, находка высокого приоритета
    прошла опровержение.
    """
    problems = []
    snap_keys = set(snapshot(measure))
    ref_by_id = collections.defaultdict(list)
    for r in refutations:
        ref_by_id[r.get("finding_id")].append(r)
    sequential_findings = []

    for row in findings:
        fid = row.get("id") or "<без id>"
        for k in REQUIRED:
            if not (row.get(k) or "").strip():
                problems.append({"id": fid, "problem": f"пустое обязательное поле {k}"})
        src = (row.get("source") or "").strip()
        if src:
            for one in [x.strip() for x in src.split(";") if x.strip()]:
                try:
                    resolve_path(measure, one)
                except (KeyError, IndexError, TypeError) as e:
                    problems.append(
                        {"id": fid, "problem": f"source не разрешается: {one} ({e})"}
                    )
        gm = (row.get("gain_metric") or "").strip()
        if gm and gm != "manual" and gm not in snap_keys:
            problems.append(
                {"id": fid, "problem": f"gain_metric {gm!r} не поле снимка и не 'manual'"}
            )
        try:
            prio = int(row.get("priority") or 0)
        except ValueError:
            prio = 0
        refs = ref_by_id.get(fid, [])
        claims_verdict = (row.get("confidence") or "") == "verdict"
        needs_refutation = prio >= 6 or claims_verdict
        if needs_refutation and not refs:
            problems.append(
                {"id": fid, "problem": "приоритет >=6 или confidence=verdict, но опровержения нет"}
            )
        elif needs_refutation:
            modes = {r.get("mode") or "" for r in refs}
            lenses = {lens_key(r.get("lens")) for r in refs}
            if "" in modes:
                problems.append(
                    {"id": fid, "problem": "у вердикта линзы не объявлен mode прогона"}
                )
            if claims_verdict and "parallel" not in modes:
                problems.append(
                    {
                        "id": fid,
                        "problem": "confidence=verdict, а линзы шли последовательно "
                        "(одна модель, один проход) — потолок этой находки finding",
                    }
                )
            if claims_verdict and len(lenses) < 2:
                problems.append(
                    {
                        "id": fid,
                        "problem": f"confidence=verdict при одной линзе ({'/'.join(sorted(lenses))}): "
                        "это один взгляд, а не опровержение разными способами",
                    }
                )
            if "parallel" not in modes:
                sequential_findings.append(fid)

    out = {
        "findings": len(findings),
        "refutations": len(refutations),
        "refutation": refutation_mode(refutations, sequential_findings),
        "problems": problems,
        "result": "BROKEN" if problems else "OK",
    }
    return out


def verify_gains(findings, measure) -> tuple[list, list]:
    """Сбылся ли прогноз. Скилл требует проверяемого `gain` — и до сих пор
    никто его не проверял. Здесь петля замыкается."""
    snap = snapshot(measure)
    stamp = time.strftime("%Y-%m-%d")
    report = []
    for row in findings:
        if (row.get("status") or "").strip().lower() not in ("done", "fixed", "закрыта"):
            continue
        gm = (row.get("gain_metric") or "").strip()
        try:
            target = float(row.get("gain_target") or "")
        except ValueError:
            target = None
        if gm == "manual" or not gm or target is None:
            row["gain_verdict"] = "не измеримо"
            row["verified_at"] = stamp
        else:
            actual = snap.get(gm)
            if isinstance(actual, list):
                actual = len(actual)
            if not isinstance(actual, (int, float)):
                row["gain_verdict"] = "не измеримо"
            else:
                row["gain_actual"] = actual
                # Направление берётся из самого прогноза: цель выше нынешнего
                # значения на момент находки — рост, ниже — падение. Хранить
                # отдельным полем не нужно, достаточно сравнения с целью.
                row["gain_verdict"] = "сбылось" if _reached(actual, target, row) else "не сбылось"
            row["verified_at"] = stamp
        report.append(
            {
                "id": row.get("id"),
                "metric": gm,
                "target": row.get("gain_target"),
                "actual": row.get("gain_actual"),
                "verdict": row.get("gain_verdict"),
            }
        )
    return findings, report


def _reached(actual: float, target: float, row) -> bool:
    """Цель достигнута. Направление задаёт поле `gain_direction`, если оно есть;
    иначе — «не хуже цели» в сторону, куда цель отстоит от нуля метрики роста."""
    direction = (row.get("gain_direction") or "").strip().lower()
    if direction in ("up", "рост", "вверх"):
        return actual >= target
    if direction in ("down", "падение", "вниз"):
        return actual <= target
    # Направление не указано: сравниваем с целью по обе стороны с допуском 1%.
    return abs(actual - target) <= abs(target) * 0.01 or actual >= target


def main():
    ap = argparse.ArgumentParser(description="Леджер и гейт zodchiy")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("snapshot")
    p.add_argument("measure")
    p.add_argument("--out")
    p = sub.add_parser("diff")
    p.add_argument("measure")
    p.add_argument("--baseline", required=True)
    p = sub.add_parser("gate")
    p.add_argument("measure")
    p.add_argument("--baseline", required=True)
    p.add_argument(
        "--max-new-cycles",
        type=int,
        default=0,
        help="порог числа новых рантайм-циклов; превышение — REGRESSION (по умолчанию 0)",
    )
    p = sub.add_parser("add")
    p.add_argument("--findings", required=True)
    p.add_argument("--json", required=True)
    p = sub.add_parser("refute")
    p.add_argument("--out", default=".zodchiy/verify/refutation.json")
    p.add_argument("--json", required=True)
    p = sub.add_parser("selfcheck")
    p.add_argument("--findings", required=True)
    p.add_argument("--measure", required=True)
    p.add_argument("--refutations", default=".zodchiy/verify/refutation.json")
    p = sub.add_parser("verify")
    p.add_argument("--findings", required=True)
    p.add_argument("--measure", required=True)
    p = sub.add_parser("export")
    p.add_argument("--findings", required=True)
    p.add_argument("--measure", required=True)
    p.add_argument("--refutations", default=".zodchiy/verify/refutation.json")
    p.add_argument("--format", choices=("json", "sarif"), default="json")
    p.add_argument("--out")

    a = ap.parse_args()

    if a.cmd == "refute":
        rec = json.loads(a.json)
        missing = [
            k for k in ("finding_id", "lens", "verdict", "reason", "mode") if not rec.get(k)
        ]
        if missing:
            sys.exit(f"вердикт линзы неполон: нет полей {', '.join(missing)}")
        if rec["verdict"] not in REFUTE_VERDICTS:
            sys.exit(f"verdict должен быть одним из {', '.join(REFUTE_VERDICTS)}")
        if rec["mode"] not in REFUTE_MODES:
            sys.exit(f"mode должен быть одним из {', '.join(REFUTE_MODES)}")
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        cur = load(a.out) if os.path.exists(a.out) else []
        cur.append(rec)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(cur, fh, ensure_ascii=False, indent=2)
        print(json.dumps({"added": rec["finding_id"], "file": a.out}, ensure_ascii=False))
        return

    if a.cmd == "selfcheck":
        refs = load(a.refutations) if os.path.exists(a.refutations) else []
        res = selfcheck(read_findings(a.findings), load(a.measure), refs)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(1 if res["problems"] else 0)

    if a.cmd == "export":
        measure = load(a.measure)
        refs = load(a.refutations) if os.path.exists(a.refutations) else []
        rows = read_findings(a.findings)
        # Экспорт — тот же гейт, что перед сдачей отчёта. Машиночитаемый выход
        # из брака хуже отсутствия: его прочитает CI и поверит.
        res = selfcheck(rows, measure, refs)
        if res["problems"]:
            print(json.dumps(res, ensure_ascii=False, indent=2), file=sys.stderr)
            sys.exit(1)
        doc = document(rows, measure, refs, generated_from=a.measure)
        bad = validate_document(doc)
        if bad:
            print("\n".join(bad), file=sys.stderr)
            sys.exit(1)
        out = sarif(doc) if a.format == "sarif" else doc
        text = json.dumps(out, ensure_ascii=False, indent=2)
        if a.out:
            os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
            with open(a.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print(json.dumps(
                {"written": a.out, "format": a.format, "findings": len(doc["findings"])},
                ensure_ascii=False,
            ))
        else:
            print(text)
        return

    if a.cmd == "verify":
        rows, report = verify_gains(read_findings(a.findings), load(a.measure))
        write_findings(a.findings, rows)
        print(json.dumps({"verified": report}, ensure_ascii=False, indent=2))
        return

    if a.cmd == "add":
        rec = json.loads(a.json)
        missing = [k for k in REQUIRED if not rec.get(k)]
        if missing:
            sys.exit(f"находка неполна, брак: нет полей {', '.join(missing)}")
        new = not os.path.exists(a.findings)
        os.makedirs(os.path.dirname(os.path.abspath(a.findings)) or ".", exist_ok=True)
        with open(a.findings, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(rec)
        print(json.dumps({"added": rec["id"], "file": a.findings}, ensure_ascii=False))
        return

    cur = snapshot(load(a.measure))

    if a.cmd == "snapshot":
        text = json.dumps(cur, ensure_ascii=False, indent=2)
        if a.out:
            os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
            open(a.out, "w", encoding="utf-8").write(text)
            print(json.dumps({"written": a.out}, ensure_ascii=False))
        else:
            print(text)
        return

    if not os.path.exists(a.baseline):
        # Нет базы — не повод падать: первый прогон её и создаёт.
        print(
            json.dumps(
                {
                    "gate_result": "NO_BASELINE",
                    "note": "базы нет, сравнивать не с чем; сначала ledger.py snapshot",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(0)

    res = diff(cur, load(a.baseline), max_new_cycles=getattr(a, "max_new_cycles", 0))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    if a.cmd == "gate" and res["gate_result"] == "REGRESSION":
        sys.exit(1)


if __name__ == "__main__":
    main()
