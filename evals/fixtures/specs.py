#!/usr/bin/env python3
"""Спецификации мини-репозиториев под frozen-слой evals.

Правило один-к-одному: у каждого риска есть пара «грязный / похожий, но
невиновный». Без пары метрика «скилл нашёл N» не отличима от «нашёл N и ещё
десять ложных» — это метод OWASP Benchmark, и ровно тех четырёх ложняков,
которые SKILL.md уже описывает как инцидент, регрессией сейчас ничто не держит.

Фикстура — не каталог в репозитории скилла, а РЕЦЕПТ: файлы плюс план коммитов.
Репозиторий строится `build.py` во временный кэш. Причина: поведенческая ось
читает git-историю, а вложенный .git внутри ~/.claude не переживёт ни клона,
ни архива, и «история» фикстуры молча выродилась бы в один коммит.
"""

from __future__ import annotations

# ── Общая подложка ──────────────────────────────────────────────────────────
# Нужна не для красоты: measure.calibrate() блокирует метрики, если тестовых
# правок ноль (containment) или рёбер меньше 0.3 на файл (cycles/hubs/fan_in).
# Без подложки каждая фикстура падала бы на калибровке, а не на своей проверке.

BASE_FILES = {
    "src/__init__.py": "",
    "src/core/__init__.py": "",
    "src/core/util.py": "def util():\n    return 1\n",
    "src/app/__init__.py": "",
    "src/app/filler_a.py": "from src.core import util\n\n\ndef a():\n    return util.util()\n",
    "src/app/filler_b.py": "from src.core import util\n\n\ndef b():\n    return util.util()\n",
    "src/app/filler_c.py": "from src.core import util\n\n\ndef c():\n    return util.util()\n",
    "tests/test_smoke.py": "from src.core import util\n\n\ndef test_util():\n    assert util.util() == 1\n",
}

# Файлы, по которым крутятся добивочные коммиты до порога min_commits=60.
FILLER_CYCLE = [
    "src/app/filler_a.py",
    "src/app/filler_b.py",
    "src/app/filler_c.py",
    "tests/test_smoke.py",
]

MIN_CODE_COMMITS = 64  # порог behavior.py — 60; берём с запасом


def _repeat(subject: str, files: list[str], n: int, start: int = 1) -> list[dict]:
    return [
        {"subject": subject.format(i=i), "files": files}
        for i in range(start, start + n)
    ]


FIXTURES: dict[str, dict] = {
    # ── Циклы: рантаймовый против типового ──────────────────────────────────
    "cycle-runtime": {
        "doc": "Настоящий рантайм-цикл: обе стороны выполняются при импорте.",
        "files": {
            "src/order/__init__.py": "",
            "src/order/service.py": (
                "from src.order import repository\n"
                "\n"
                "\n"
                "def place(order):\n"
                "    return repository.save(order)\n"
            ),
            "src/order/repository.py": (
                "from src.order import service\n"
                "\n"
                "\n"
                "def save(order):\n"
                "    return service.place(order)\n"
            ),
        },
        "plan": _repeat("feat: order tweak {i}", ["src/order/service.py"], 4),
    },
    "cycle-type-only": {
        "doc": (
            "Цикл держится только на `if TYPE_CHECKING:` — он для того и заведён, "
            "чтобы разорвать рантайм-зависимость. Ложняк v1: 4 таких цикла были "
            "объявлены дефектами."
        ),
        "files": {
            "src/order/__init__.py": "",
            "src/order/service.py": (
                "from __future__ import annotations\n"
                "\n"
                "from typing import TYPE_CHECKING\n"
                "\n"
                "if TYPE_CHECKING:\n"
                "    from src.order.repository import Repository\n"
                "\n"
                "\n"
                "def place(order, repo: Repository):\n"
                "    return repo.save(order)\n"
            ),
            "src/order/repository.py": (
                "from src.order import service\n"
                "\n"
                "\n"
                "class Repository:\n"
                "    def save(self, order):\n"
                "        return service.place(order, self)\n"
            ),
        },
        "plan": _repeat("feat: order tweak {i}", ["src/order/service.py"], 4),
    },
    # ── Сцепление: R2 (связаны через barrel) против R3 (связи нет) ──────────
    "coupling-r2-vs-r3": {
        "doc": (
            "Две пары с одинаковым поведением и разной структурой. Пара user — "
            "сцеплена И связана импортом, но ребро идёт через `__init__.py`; "
            "без прохода сквозь barrel она выглядит как R3. Пара billing/report — "
            "сцеплена и не связана ничем, это настоящий R3."
        ),
        "files": {
            "src/user/__init__.py": 'from src.user.model import User\n\n__all__ = ["User"]\n',
            "src/user/model.py": (
                "class User:\n"
                "    def __init__(self, uid):\n"
                "        self.uid = uid\n"
            ),
            "src/user/repository.py": (
                "from src.user import User\n"
                "\n"
                "\n"
                "def load(uid):\n"
                "    return User(uid)\n"
            ),
            "src/billing/__init__.py": "",
            "src/billing/tariff.py": 'PRICES = {"month": 490}\n',
            "src/report/__init__.py": "",
            "src/report/pricing.py": 'TITLES = {"month": "Month"}\n',
        },
        "plan": (
            _repeat(
                "feat: user field {i}",
                ["src/user/model.py", "src/user/repository.py"],
                8,
            )
            + _repeat(
                "feat: price {i}",
                ["src/billing/tariff.py", "src/report/pricing.py"],
                8,
            )
        ),
    },
    # ── Churn: боль против точки расширения ─────────────────────────────────
    "churn-pain-vs-extension": {
        "doc": (
            "Один и тот же высокий churn с разной долей багфиксов. "
            "composition.py — 30 правок, 2 фикса: точка расширения, не долг "
            "(зафиксированный ложняк v1). payments.py — 20 правок, 9 фиксов: "
            "боль. Плюс barrel с fan-in 8 — тоже ложняк v1 (там было 109)."
        ),
        "files": {
            "src/shared/__init__.py": "from src.shared.helpers import helper\n",
            "src/shared/helpers.py": "def helper():\n    return 1\n",
            "src/features/__init__.py": "",
            **{
                f"src/features/f{i}.py": (
                    "from src.shared import helper\n"
                    "from src.legacy import registry\n"
                    "\n"
                    "\n"
                    f"def f{i}():\n"
                    "    return registry.resolve(helper())\n"
                )
                for i in range(1, 9)
            },
            "src/legacy/__init__.py": "",
            # Тот же fan-in, что у barrel, но это не реэкспорт: своя логика и
            # своя сложность. Пара к barrel-ложняку — без неё «высокий fan-in»
            # не отличим от «высокий fan-in по замыслу».
            "src/legacy/registry.py": (
                "def resolve(key):\n"
                + "".join(
                    f"    if key == {i}:\n        return {i} * 2\n"
                    for i in range(1, 13)
                )
                + "    return 0\n"
            ),
            "src/payments.py": (
                "from src.shared import helper\n"
                "\n"
                "\n"
                "def charge(amount):\n"
                "    if amount <= 0:\n"
                "        raise ValueError(amount)\n"
                "    return helper() * amount\n"
            ),
            "src/composition.py": (
                "from src.features import f1\n"
                "from src.features import f2\n"
                "from src.features import f3\n"
                "from src.features import f4\n"
                "from src.features import f5\n"
                "from src.features import f6\n"
                "from src.features import f7\n"
                "from src.features import f8\n"
                "from src import payments\n"
                "\n"
                "\n"
                "def build():\n"
                "    return [f1, f2, f3, f4, f5, f6, f7, f8, payments]\n"
            ),
        },
        "plan": (
            _repeat("feat: wire dependency {i}", ["src/composition.py"], 28)
            + _repeat("fix: wrong provider {i}", ["src/composition.py"], 2)
            + _repeat("feat: payments option {i}", ["src/payments.py"], 11)
            + _repeat("fix: payments rounding {i}", ["src/payments.py"], 9)
        ),
    },
    # ── Скорость и устойчивость ─────────────────────────────────────────────
    "velocity-stability": {
        "doc": (
            "Одинаковый churn, разная устойчивость и разная ширина правки. "
            "fragile.py: за каждой правкой через сутки идёт фикс. solid.py: "
            "столько же правок и ни одного фикса. wide/: одно изменение трогает "
            "шесть файлов, narrow/: один."
        ),
        "files": {
            "src/fragile.py": "def fragile(x):\n    return x + 1\n",
            "src/solid.py": "def solid(x):\n    return x * 2\n",
            "src/burst.py": "def burst(x):\n    return x - 1\n",
            "src/narrow/__init__.py": "",
            "src/narrow/one.py": "from src.core import util\n\n\ndef one():\n    return util.util()\n",
            "src/wide/__init__.py": "",
            **{
                f"src/wide/w{i}.py": f"from src.core import util\n\n\ndef w{i}():\n    return util.util()\n"
                for i in range(1, 7)
            },
        },
        "plan": (
            # правка -> фикс через сутки: доделка попадает в окно rework_window_days
            [
                c
                for i in range(1, 13)
                for c in (
                    {"subject": f"feat: fragile {i}", "files": ["src/fragile.py"], "gap_days": 4},
                    {"subject": f"fix: fragile {i}", "files": ["src/fragile.py"], "gap_days": 1},
                )
            ]
            + _repeat("feat: solid {i}", ["src/solid.py"], 12)
            # четыре эпизода по три коммита: внутри эпизода сутки, между — 20 дней
            + [
                c
                for e in range(1, 5)
                for c in (
                    {"subject": f"feat: burst {e}.1", "files": ["src/burst.py"], "gap_days": 20},
                    {"subject": f"feat: burst {e}.2", "files": ["src/burst.py"], "gap_days": 1},
                    {"subject": f"feat: burst {e}.3", "files": ["src/burst.py"], "gap_days": 1},
                )
            ]
            + _repeat(
                "feat: wide change {i}",
                [f"src/wide/w{i}.py" for i in range(1, 7)],
                8,
            )
            + _repeat("feat: narrow change {i}", ["src/narrow/one.py"], 8)
        ),
    },
    # ── Сложность: порог McCabe задан на функцию, не на файл ────────────────
    "complexity-normalization": {
        "doc": (
            "Ловушка ненормализованной метрики: сумма по файлу растёт с длиной "
            "файла и с порогом «>10» несопоставима. Баг v1 №3 — ровно это."
        ),
        "files": {
            "src/simple_many.py": "".join(
                f"def s{i}(x):\n"
                f"    if x == {i}:\n"
                f"        return 1\n"
                f"    if x > {i}:\n"
                f"        return 2\n"
                f"    if x < -{i}:\n"
                f"        return 3\n"
                f"    return 0\n"
                "\n"
                "\n"
                for i in range(1, 11)
            ),
            "src/one_hairy.py": (
                "def hairy(x):\n"
                + "".join(
                    f"    if x == {i}:\n        return {i}\n" for i in range(1, 15)
                )
                + "    return 0\n"
            ),
        },
        "plan": _repeat("feat: tweak {i}", ["src/simple_many.py"], 4),
    },
}


def _has_code(paths: list[str]) -> bool:
    """Тот же критерий, что `behavior.classify(...) == "code"`, в объёме фикстур:
    .py вне tests/ и без пометок generated."""
    return any(
        p.endswith(".py") and not p.startswith("tests/") and "test" not in p.rsplit("/", 1)[-1]
        for p in paths
    )


def spec(name: str) -> dict:
    """Полная спецификация: подложка + собственные файлы + добивка коммитов."""
    raw = FIXTURES[name]
    files = {**BASE_FILES, **raw["files"]}
    plan = list(raw["plan"])
    # Один коммит на файл при создании: иначе первый коммит трогает всё дерево,
    # даёт ложное сцепление всех со всеми и попадает в mega_commit_files.
    creation = [{"subject": f"feat: add {p}", "files": [p], "create": True} for p in files]
    commits = creation + plan
    # Добиваем до порога ИМЕННО коммитами с кодом: behavior.py считает
    # `commits_with_code`, а коммит, тронувший только tests/, туда не входит.
    # Считать «все коммиты» — как раз тот замер, который врёт зелёным.
    i = 0
    while sum(1 for c in commits if _has_code(c["files"])) < MIN_CODE_COMMITS:
        commits.append(
            {
                "subject": f"chore: tick {i}",
                "files": [FILLER_CYCLE[i % len(FILLER_CYCLE)]],
            }
        )
        i += 1
    return {
        "name": name,
        "doc": raw["doc"],
        "files": files,
        "commits": commits,
    }


def all_names() -> list[str]:
    return sorted(FIXTURES)
