#!/usr/bin/env python3
"""Боль в файлах и окупаемость лечения — арифметика L4 режима `refactor`.

Валюта — затронутые файлы (спека §1, L4):
  боль        = эпизоды фиксов в файлах находки за квартал × медиана файлов на эпизод;
  лечение     = файлы в диффе варианта;
  окупаемость = лечение ÷ боль, в кварталах.

Отдельный модуль от `slice.py`: те же числа нужны `trial` (гейт по факту диффа),
а тянуть ради них сборку среза незачем.
"""

from __future__ import annotations

import datetime
import time

from behavior import DEFAULTS, FIX_RE, classify, read_log
from common import median as _median

DAY = 86400

# Стартовое допущение, калибруется по первым прогонам (спека §1, L4).
PAYBACK_MAX_QUARTERS = 4


def payback_quarters(remedy_files: int, pain_files_per_quarter: float) -> float | None:
    """Через сколько кварталов лечение окупится. `None` — боли нет, окупаться нечему."""
    if not pain_files_per_quarter or pain_files_per_quarter <= 0:
        return None
    return remedy_files / pain_files_per_quarter


def payback_verdict(q: float | None) -> str:
    """ok | too_costly | cold. Ровно порог — ещё ok: «выше порога» значит строго выше."""
    if q is None:
        return "cold"
    return "ok" if q <= PAYBACK_MAX_QUARTERS else "too_costly"


def _date(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


def pain_per_quarter(repo: str, files: list[str], since_days: int = 365) -> dict:
    """Боль в файлах находки по истории git за `since_days` дней.

    Эпизод фикса — серия коммитов по файлам находки ВМЕСТЕ (разрыв ≤ episode_gap_days
    по объединению файлов, а не по каждому файлу отдельно: иначе один эпизод, виденный
    из двух файлов с разным набором коммитов, считался бы дважды), в которой есть
    коммит с `FIX_RE`. Файлов на эпизод — объединение кодовых файлов
    всех его коммитов (мега-коммиты, как и в сцеплении, в счёт не идут).
    `edits_by_quarter` — коммиты по файлам находки, индекс 0 — самый старый
    квартал окна, 3 — самый свежий. `last_edit` — последняя правка внутри окна.

    Нет правок за окно → боль 0, `last_edit` None: холодный файл, «не чинить».
    """
    cfg = DEFAULTS
    wanted = set(files)
    commits = [
        c
        for c in read_log(repo, f"{since_days}.days.ago")
        if sum(1 for f in c["files"] if classify(f) == "code") <= cfg["mega_commit_files"]
    ]

    touching = [c for c in commits if wanted & {f for f in c["files"] if classify(f) == "code"}]
    span = since_days * DAY / 4
    now = time.time()
    by_quarter = [0, 0, 0, 0]
    for c in touching:
        age = max(0.0, now - c["ts"])
        by_quarter[3 - min(3, int(age // span))] += 1

    gap = cfg["episode_gap_days"] * DAY
    episodes: list[list[dict]] = []
    for c in sorted(touching, key=lambda c: c["ts"]):
        if episodes and c["ts"] - episodes[-1][-1]["ts"] <= gap:
            episodes[-1].append(c)
        else:
            episodes.append([c])
    fixes = [g for g in episodes if any(FIX_RE.match(c["subject"]) for c in g)]
    widths = [len({f for c in g for f in c["files"] if classify(f) == "code"}) for g in fixes]

    quarters = since_days / 91.25
    per_quarter = len(fixes) / quarters
    median_width = _median(widths) or 0.0
    return {
        "fix_episodes_per_quarter": round(per_quarter, 3),
        "median_files_per_episode": float(median_width),
        "pain_files_per_quarter": round(per_quarter * median_width, 3),
        "edits_by_quarter": by_quarter,
        "last_edit": _date(max(c["ts"] for c in touching)) if touching else None,
    }
