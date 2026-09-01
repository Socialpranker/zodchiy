#!/usr/bin/env python3
"""Сборка адаптеров под чужие харнессы из одного источника.

    python3 scripts/build_adapters.py            # собрать в dist/
    python3 scripts/build_adapters.py --check    # сверить dist/ с исходником

Источник ровно один — `SKILL.md`. Пять копий доктрины разъедутся за месяц,
поэтому адаптер не пишется руками: секции берутся из `SKILL.md` по заголовку,
и пропажа заголовка — ошибка сборки, а не тихо пустой раздел.

Адаптер — маршрутизатор, а не доктрина целиком. Причина в цифре: `SKILL.md`
весит 21 KiB при лимите Codex `project_doc_max_bytes` = 32 KiB на весь набор
проектных документов, а глобальный `~/.codex/AGENTS.md` грузится в каждом
запросе любого проекта. Полное читается с диска, когда аудит начался.
Матрица харнессов и её срок годности — `references/harnesses.md`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
DIST = os.path.join(SKILL, "dist")
DEFAULT_HOME = "~/.claude/skills/zodchiy"

# Имя файла доктрины на каждом харнессе. Проверено 01.09.2026, источники —
# references/harnesses.md. Текст у всех троих один: Qwen Code и iFlow — форки
# Gemini CLI, различие только в имени, по которому идёт поиск.
DOCTRINE_TARGETS = {
    "AGENTS.md": "Codex (`~/.codex/AGENTS.md` или корень репозитория), Grok Build, Cursor, Zed",
    "GEMINI.md": "Gemini CLI (`~/.gemini/GEMINI.md` или корень репозитория)",
    "QWEN.md": "Qwen Code (`~/.qwen/QWEN.md`)",
    "IFLOW.md": "iFlow CLI (`~/.iflow/IFLOW.md`)",
}

COMMAND_TARGETS = {
    "gemini/commands/zodchiy.toml": "~/.gemini/commands/zodchiy.toml",
    "iflow/commands/zodchiy.toml": "~/.iflow/commands/zodchiy.toml",
}

REQUIRED_SECTIONS = ("## Железное правило", "## Команда", "## Деградация", "## Справочники")


def read_skill() -> str:
    with open(os.path.join(SKILL, "SKILL.md"), encoding="utf-8") as fh:
        return fh.read()


def frontmatter(md: str) -> dict:
    if not md.startswith("---\n"):
        raise SystemExit("SKILL.md без frontmatter — собирать не из чего")
    head = md.split("---\n", 2)[1]
    out = {}
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def section(md: str, heading: str) -> str:
    """Секция по точному заголовку до следующего того же уровня.

    Пропажа заголовка валит сборку: адаптер с молча пустым разделом —
    ровно та тихая деградация, которую скилл запрещает другим."""
    level = len(heading) - len(heading.lstrip("#"))
    pat = re.compile(
        rf"^{re.escape(heading)}\s*$(.*?)(?=^#{{1,{level}}} |\Z)", re.M | re.S
    )
    m = pat.search(md)
    if not m:
        raise SystemExit(f"в SKILL.md нет секции {heading!r} — адаптеры не собрать")
    return m.group(1).strip("\n")


def absolutise(text: str, home: str) -> str:
    """Относительные пути доктрины — в абсолютные: чужой харнесс запускается
    не из каталога скилла и `references/risks.md` у него не разрешится."""
    return re.sub(r"(?<![\w/~.])(references|evals|scripts)/", rf"{home}/\1/", text)


def steps(md: str) -> str:
    rows = re.findall(r"^### (\d)\. ([^\n]+)$", md, re.M)
    return "\n".join(f"{n}. {title}" for n, title in rows)


def router(md: str, home: str) -> str:
    fm = frontmatter(md)
    body = f"""# Зодчий — архитектурный аудит по трём осям

{fm['description']}

Это маршрутизатор, а не доктрина. Полный текст лежит файлами на диске и
читается по путям ниже — целиком он сюда не влезает и вытеснил бы правила
самого проекта.

## Железное правило

{absolutise(section(md, '## Железное правило'), home)}

## Команда

{absolutise(section(md, '## Команда'), home)}

## Порядок работ — `audit`

{steps(md)}

Что делает каждый шаг, чем он ограничен и что на нём запрещено — в
`{home}/SKILL.md`. **Прочитай его целиком перед шагом 1.** Здесь только
маршрут, по нему аудит не проводится.

## Деградация

{absolutise(section(md, '## Деградация'), home)}

## Справочники

{absolutise(section(md, '## Справочники'), home)}

## Границы

Безопасность, ревью диффа, внешний ресёрч и UI — не сюда. Полный список
в `{home}/SKILL.md`.
"""
    return body.rstrip() + "\n"


def toml_command(home: str) -> str:
    return f'''description = "Архитектурный аудит репозитория: намерение, структура, поведение git-истории"

prompt = """
Проведи архитектурный аудит по доктрине зодчего.

Цель: {{{{args}}}} (пусто — текущий репозиторий).

Порядок:
1. Прочитай {home}/SKILL.md целиком. Это доктрина, а не справка;
   шаги, запреты и форма находки — оттуда.
2. Замер: python3 {home}/zodchiy.py measure <repo> --out .zodchiy/measure.json
3. Дальше по шагам SKILL.md. Справочники в {home}/references/
   читай по одному, когда доходишь до шага, которому он нужен.

Субагентов у тебя, скорее всего, нет: линзы шага 4 прогоняй последовательно,
записывай вердикты с mode=sequential и объяви деградацию в отчёте. Потолок
уверенности в этом режиме — finding, не verdict.
"""
'''


def readme(home: str) -> str:
    rows = "\n".join(
        f"| `{name}` | {where} |" for name, where in DOCTRINE_TARGETS.items()
    )
    cmds = "\n".join(f"| `{src}` | `{dst}` |" for src, dst in COMMAND_TARGETS.items())
    return f"""# dist — адаптеры зодчего под чужие харнессы

Собрано `scripts/build_adapters.py` из `SKILL.md`. **Руками не править:**
правка сюда переживёт ровно до следующей сборки, а `--check` покраснеет.
Менять надо `SKILL.md`, потом пересобрать.

## Доктрина

Один и тот же текст под четырьмя именами — харнессы ищут разные имена.

| Файл | Куда класть |
|---|---|
{rows}

## Команды

| Файл | Куда класть |
|---|---|
{cmds}

## Claude Code и Grok Build

Адаптер не нужен: формат скилла один — `SKILL.md` с YAML-frontmatter.
Каталог скилла кладётся целиком в `~/.claude/skills/zodchiy/` либо
`~/.grok/skills/zodchiy/`. Что Grok читает `.claude/skills/` — не проверено,
источники расходятся (`references/harnesses.md`).

## Чего не хватает адаптеру

Скрипты. Адаптер — текст; замер делают `{home}/scripts/*.py`, и без
самого каталога скилла на диске он бесполезен. Раскладка по харнессам —
этап U5 плана (`install.sh`), сейчас каталог кладётся руками.

Ни один адаптер не запускался в целевом харнессе. Собранное ≠ работающее.
"""


def build(home: str) -> dict[str, str]:
    md = read_skill()
    for h in REQUIRED_SECTIONS:
        section(md, h)  # ранняя проверка: пусть падает до записи файлов
    files = {name: router(md, home) for name in DOCTRINE_TARGETS}
    for path in COMMAND_TARGETS:
        files[path] = toml_command(home)
    files["README.md"] = readme(home)
    return files


def main():
    ap = argparse.ArgumentParser(description="Сборка адаптеров зодчего")
    ap.add_argument("--check", action="store_true", help="сверить dist/ с исходником")
    ap.add_argument("--home", default=DEFAULT_HOME, help="путь установки скилла")
    ap.add_argument("--out", default=DIST)
    a = ap.parse_args()

    files = build(a.home)
    if a.check:
        drift = []
        for rel, text in files.items():
            path = os.path.join(a.out, rel)
            if not os.path.exists(path):
                drift.append(f"{rel}: нет в dist/")
                continue
            with open(path, encoding="utf-8") as fh:
                if fh.read() != text:
                    drift.append(f"{rel}: разошёлся с SKILL.md")
        extra = []
        for root, _, names in os.walk(a.out):
            for n in names:
                rel = os.path.relpath(os.path.join(root, n), a.out)
                if rel not in files:
                    extra.append(f"{rel}: лишний файл в dist/")
        drift += extra
        for d in drift:
            print(d, file=sys.stderr)
        print(f"{'РАСХОЖДЕНИЕ' if drift else 'сошлось'}: {len(files)} файлов")
        sys.exit(1 if drift else 0)

    for rel, text in files.items():
        path = os.path.join(a.out, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    print(f"собрано {len(files)} файлов в {a.out}")


if __name__ == "__main__":
    main()
