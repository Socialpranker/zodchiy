#!/usr/bin/env python3
"""Общее для обеих осей: распределения.

Отдельный модуль, а не копия в двух скриптах: перцентиль обязан считаться
одинаково у поведения и у структуры, иначе два числа с одним именем начнут
значить разное — ровно тот дефект, из-за которого поле гейта переименовали
в `gate_result`.
"""

from __future__ import annotations

import statistics


def pct_rank(values: list[float]):
    """Функция значение -> перцентиль внутри ЭТОГО репозитория.

    Абсолютные пороги подобраны на двух репозиториях и на третьем поплывут.
    Перцентиль переносится между проектами, `fix_share = 0.43` не переносится:
    рядом с каждым абсолютным числом должно стоять его место в распределении.
    """
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return lambda v: None

    def rank(v):
        lo, hi = 0, n
        while lo < hi:  # число значений <= v
            mid = (lo + hi) // 2
            if ordered[mid] <= v:
                lo = mid + 1
            else:
                hi = mid
        return round(100 * lo / n)

    return rank


def median(xs):
    return round(statistics.median(xs), 2) if xs else None


def quantile(xs, q):
    if not xs:
        return None
    ordered = sorted(xs)
    return round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 2)


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    """Нижняя граница доли по Уилсону.

    Нужна там, где долю считают на разном числе наблюдений: 5 из 5 даёт 1.0 и
    обгоняет 55 из 67, хотя знает о файле в тринадцать раз меньше. Ранжировать
    по сырой доле — ставить шум первой строкой отчёта.
    """
    if n <= 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    margin = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5 / d
    return round(max(0.0, center - margin), 3)
