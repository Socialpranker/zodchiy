#!/usr/bin/env python3
"""Фаза «Считать»: гоняет обе оси, калибрует, сводит в один JSON.

Калибровка не опция. Замерено на живом репозитории: доля правок, режущих
≥2 слоя, — 83% на сырой метрике и 49% после отсечки тестов. Без калибровки
скилл сообщил бы ложную катастрофу.

    python3 measure.py <repo> [--out .zodchiy/measure.json] [--since ...]

Выход — единственный вход для фазы «Судить». Числа оттуда не пересчитываются.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run_axis(script: str, repo: str, extra: list[str]) -> dict:
    proc = subprocess.run(
        [sys.executable, os.path.join(HERE, script), repo, *extra],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        return {
            "error": proc.stderr.strip()[-500:] or "ось не отработала",
            "available": False,
        }
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {
            "error": "ось вернула не JSON",
            "available": False,
            "head": proc.stdout[:300],
        }


# ── Калибровка ──────────────────────────────────────────────────────────────


def calibrate(behavior: dict, structure: dict) -> dict:
    """Контрольные проверки ДО того, как числа станут находками.

    Каждая проверка отвечает на «можно ли этой метрике верить», а не
    «хорош ли код». Провал проверки блокирует метрику, а не осуждает проект.
    """
    checks: list[dict] = []

    def add(name, ok, detail, blocks=()):
        checks.append(
            {
                "check": name,
                "passed": bool(ok),
                "detail": detail,
                "blocks": list(blocks),
            }
        )

    # 1. Глубина истории. Три коммита дают числа, похожие на метрику.
    if behavior.get("available"):
        n = behavior["commits_with_code"]
        add("глубина истории", True, f"{n} коммитов с кодом")
    else:
        add(
            "глубина истории",
            False,
            behavior.get("reason", "поведенческая ось недоступна"),
            blocks=["temporal_coupling", "containment", "hotspots", "knowledge_risk"],
        )

    # 2. Отделены ли тесты. Если доля тестовых правок нулевая на репо с тестами,
    #    классификатор сломан, и containment меряет не то.
    prof = behavior.get("churn_profile", {})
    code, test = prof.get("code", 0), prof.get("test", 0)
    if code:
        share = test / (code + test)
        add(
            "тесты отделены от кода",
            test > 0,
            f"правок кода {code}, тестов {test} ({share:.0%} от суммы)",
            blocks=[] if test else ["containment"],
        )
    else:
        add(
            "тесты отделены от кода",
            False,
            "правок кода не нашлось",
            blocks=["containment"],
        )

    # 3. Бэкенд разбора. regex вместо дерева = потолок уверенности ниже.
    backends = structure.get("parser", {}).get("backends", {})
    ts, rx = backends.get("tree-sitter", 0), backends.get("regex", 0)
    add(
        "точный разбор импортов",
        rx == 0,
        f"tree-sitter {ts} файлов, regex {rx}"
        + ("" if rx == 0 else "; потолок находок по структуре — finding"),
        blocks=[],
    )

    # 4. Граф не пустой. Ноль рёбер на непустом репо — сломан резолвер,
    #    а не «зависимостей нет». Контрольная группа обязательна.
    files, edges = structure.get("files", 0), structure.get("edges", 0)
    ratio = edges / files if files else 0
    add(
        "граф импортов связен",
        ratio >= 0.3,
        f"{edges} рёбер на {files} файлов ({ratio:.2f} на файл)"
        + ("" if ratio >= 0.3 else "; резолвер импортов, скорее всего, сломан"),
        blocks=[] if ratio >= 0.3 else ["cycles", "hubs", "fan_in"],
    )

    # 5. Контрольная группа сцепления: если ВСЁ сцеплено со всем, порог негоден.
    if behavior.get("available"):
        total = behavior.get("temporal_coupling_total", 0)
        considered = behavior["containment"]["commits_considered"] or 1
        noisy = total > considered * 0.5
        add(
            "порог сцепления различает",
            not noisy,
            f"{total} пар выше порога на {considered} коммитов"
            + ("; порог слишком низкий, метрика не различает" if noisy else ""),
            blocks=["temporal_coupling"] if noisy else [],
        )

    blocked = sorted({m for c in checks if not c["passed"] for m in c["blocks"]})
    return {
        "passed": not blocked,
        "checks": checks,
        "blocked_metrics": blocked,
        "note": (
            "Находки по заблокированным метрикам не выносятся: сначала чинится метрика."
            if blocked
            else "Все контрольные проверки прошли."
        ),
    }


def confidence_ceiling(behavior, structure, calib) -> dict:
    """Выше какого статуса находки в этом прогоне подняться не могут."""
    reasons = []
    if not behavior.get("available"):
        reasons.append(
            "поведенческая ось недоступна — сходимость трёх осей недостижима"
        )
    if structure.get("parser", {}).get("backends", {}).get("regex"):
        reasons.append("часть файлов разобрана регулярками")
    if not calib["passed"]:
        reasons.append(f"калибровка не прошла: {', '.join(calib['blocked_metrics'])}")
    return {"ceiling": "finding" if reasons else "verdict", "reasons": reasons}


def main():
    ap = argparse.ArgumentParser(description="Фаза «Считать» — zodchiy")
    ap.add_argument("repo")
    ap.add_argument("--out", default=None)
    ap.add_argument("--since", default=None)
    ap.add_argument("--roots", default=None)
    ap.add_argument("--layer-depth", type=int, default=1)
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    common = ["--layer-depth", str(args.layer_depth)]
    if args.roots:
        common += ["--roots", args.roots]

    behavior = run_axis(
        "behavior.py", repo, common + (["--since", args.since] if args.since else [])
    )
    structure = run_axis("structure.py", repo, common)
    calib = calibrate(behavior, structure)

    out = {
        "tool": "zodchiy/measure",
        "repo": repo,
        "snapshot": behavior.get("snapshot", {}),
        "calibration": calib,
        "confidence": confidence_ceiling(behavior, structure, calib),
        "behavior": behavior,
        "structure": structure,
    }
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        # В stdout — только сводка: полный JSON в контекст модели не нужен,
        # она прочитает файл прицельно.
        print(
            json.dumps(
                {
                    "written": args.out,
                    "calibration_passed": calib["passed"],
                    "blocked_metrics": calib["blocked_metrics"],
                    "confidence_ceiling": out["confidence"]["ceiling"],
                    "commits_with_code": behavior.get("commits_with_code"),
                    "files": structure.get("files"),
                    "edges": structure.get("edges"),
                    "runtime_cycles": len(structure.get("cycles", [])),
                    "type_only_cycles": len(structure.get("cycles_type_only", [])),
                    "containment": behavior.get("containment", {}).get("ratio"),
                    "coupling_pairs": behavior.get("temporal_coupling_total"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(text)


if __name__ == "__main__":
    main()
