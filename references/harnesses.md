# Харнессы: что проверено и куда класть

Проверено веб-поиском по первоисточникам **01.09.2026**. Это знание о чужих
продуктах — оно протухает быстрее кода: перед сборкой адаптеров сверить заново,
особенно лимиты и имена файлов. Дата в шапке — не украшение, а срок годности.

## Матрица

| Харнесс | Доктрина | Глобальный путь | Команды | Ограничение, о котором надо знать |
|---|---|---|---|---|
| Claude Code | `SKILL.md` + YAML-frontmatter | `~/.claude/skills/<имя>/` | скилл вызывается по описанию | прогрессивная загрузка `references/` — есть только здесь |
| Codex | `AGENTS.md`, `AGENTS.override.md` | `~/.codex/AGENTS.md` | — | **`project_doc_max_bytes` = 32 KiB** на весь набор; один файл на каталог |
| Grok Build | `AGENTS.md` · `Agents.md` · `AGENT.md` · `CLAUDE.md` · `Claude.md` · `CLAUDE.local.md` · `.grok/rules/*.md` (плюс `.claude/rules/`, `.cursor/rules/` для совместимости) | `~/.grok/` | скиллы `SKILL.md` + frontmatter в `~/.grok/skills/` | размер не режется; `grok inspect` показывает, что подхватилось |
| Gemini CLI | `GEMINI.md` | `~/.gemini/GEMINI.md` | `~/.gemini/commands/*.toml` | `AGENTS.md` **не читается по умолчанию** — только через `context.fileName` |
| Qwen Code | `QWEN.md` | `~/.qwen/QWEN.md` | как в Gemini CLI (форк) | `AGENTS.md` не дефолт (issue QwenLM/qwen-code#504) |
| iFlow CLI | `IFLOW.md` | `~/.iflow/` | `~/.iflow/commands/` — TOML и Markdown | форк Gemini CLI, иерархия global → project → subdir |

## Что из этого следует для адаптеров

**Доктрину целиком в `AGENTS.md` класть нельзя.** `SKILL.md` — 21.4 KiB при
лимите Codex 32 KiB на весь набор проектных документов. Глобальный
`~/.codex/AGENTS.md` грузится в КАЖДОМ проекте и в каждом запросе, даже когда
аудита никто не просил, и вытеснит правила самого проекта. Поэтому адаптер —
короткий маршрутизатор (~4 KiB): что это, команда, порядок шагов, пути к полным
файлам доктрины. Полное читается с диска, когда аудит начался.

Grok это подтверждает с другой стороны: «короткие инструкции выполняются
надёжнее длинных» — при том что размер он не режет вовсе.

**Три имени — один текст.** `GEMINI.md`, `QWEN.md`, `IFLOW.md` различаются
только именем: Qwen Code и iFlow — форки Gemini CLI с той же механикой
(иерархия каталогов, импорт `@path/to/file.md`). Собираются из одного рендера.

**Формат скилла у Grok тот же.** `SKILL.md` с YAML-frontmatter, лишние ключи
игнорируются, поля `name`/`description`/`user-invocable`. То есть каталог скилла
кладётся в `~/.grok/skills/zodchiy/` как есть — адаптер не нужен, нужен только
путь.

## Не проверено

- **Читает ли Grok Build скиллы из `.claude/skills/`.** Источники расходятся:
  обзорная страница обещает «читает скиллы Claude Code», страница про скиллы
  перечисляет источники `./.grok/skills/`, `~/.grok/skills/`, каталоги плагинов
  и явно заданные пути — `.claude/skills/` среди них нет. Пока считаем, что
  каталог надо класть в `~/.grok/skills/`; проверяется одним `grok inspect`.
- **Формат кастомных команд iFlow.** Что TOML поддерживается — сказано; что
  поля те же, что у Gemini CLI, — вывод из факта форка, а не прочитанная
  спецификация.
- **`project_doc_fallback_filenames` у Codex** — ключ существует, значения по
  умолчанию в документации не названы.
- Ни один адаптер не запускался в целевом харнессе. Собранное ≠ работающее.

## Источники

- Codex: <https://learn.chatgpt.com/docs/agent-configuration/agents-md.md>
- Gemini CLI: <https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md>,
  <https://geminicli.com/docs/cli/custom-commands/>
- Qwen Code: <https://qwenlm.github.io/qwen-code-docs/en/users/configuration/settings/>
- iFlow: <https://github.com/iflow-ai/iflow-cli>
- Grok Build: <https://docs.x.ai/build/features/project-rules>,
  <https://docs.x.ai/build/features/skills-plugins-marketplaces>
