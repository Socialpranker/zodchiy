# dist — адаптеры зодчего под чужие харнессы

Собрано `scripts/build_adapters.py` из `SKILL.md`. **Руками не править:**
правка сюда переживёт ровно до следующей сборки, а `--check` покраснеет.
Менять надо `SKILL.md`, потом пересобрать.

## Доктрина

Один и тот же текст под четырьмя именами — харнессы ищут разные имена.

| Файл | Куда класть |
|---|---|
| `AGENTS.md` | Codex (`~/.codex/AGENTS.md` или корень репозитория), Grok Build, Cursor, Zed |
| `GEMINI.md` | Gemini CLI (`~/.gemini/GEMINI.md` или корень репозитория) |
| `QWEN.md` | Qwen Code (`~/.qwen/QWEN.md`) |
| `IFLOW.md` | iFlow CLI (`~/.iflow/IFLOW.md`) |

## Команды

| Файл | Куда класть |
|---|---|
| `gemini/commands/zodchiy.toml` | `~/.gemini/commands/zodchiy.toml` |
| `iflow/commands/zodchiy.toml` | `~/.iflow/commands/zodchiy.toml` |

## Claude Code и Grok Build

Адаптер не нужен: формат скилла один — `SKILL.md` с YAML-frontmatter.
Каталог скилла кладётся целиком в `~/.claude/skills/zodchiy/` либо
`~/.grok/skills/zodchiy/`. Что Grok читает `.claude/skills/` — не проверено,
источники расходятся (`references/harnesses.md`).

## Чего не хватает адаптеру

Скрипты. Адаптер — текст; замер делают `~/.claude/skills/zodchiy/scripts/*.py`, и без
самого каталога скилла на диске он бесполезен. Раскладка по харнессам —
этап U5 плана (`install.sh`), сейчас каталог кладётся руками.

Ни один адаптер не запускался в целевом харнессе. Собранное ≠ работающее.
