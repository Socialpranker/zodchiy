# `measure.json` — что в файле лежит

Единственный источник чисел для фазы «Судить». Читать прицельно по путям
отсюда, не целиком: на 500-файловом репозитории файл занимает мегабайты.

Путь из этой таблицы — то, что кладётся в поле `source` находки. Ссылка вида
`behavior.hotspots[file=src/x.py].fix_share` проверяется механически
(`zodchiy.py selfcheck`); проза «по данным замера» — нет.

- [Верхний уровень](#верхний-уровень)
- [`behavior` — поведенческая ось](#behavior--поведенческая-ось)
- [`structure` — структурная ось](#structure--структурная-ось)
- [`tools` — что найдено на машине](#tools--что-найдено-на-машине)
- [`lint` — ruff по группам](#lint--ruff-по-группам)
- [`lint.clones` — дубли кода](#lintclones--дубли-кода)
- [Синтаксис пути](#синтаксис-пути)

## Верхний уровень

| Путь | Что |
|---|---|
| `tool`, `repo` | подпись прогона |
| `snapshot` | `repo`, `branch`, `head`, `worktree` (clean/dirty), `since` |
| `calibration.passed` | прошли ли контрольные проверки; читать ДО всего остального |
| `calibration.checks[]` | `check`, `passed`, `detail`, `blocks` — какие метрики блокирует провал |
| `calibration.blocked_metrics` | по ним находок не выносят |
| `confidence.ceiling` | `verdict` или `finding` — выше находка не поднимется |
| `confidence.reasons` | почему потолок опущен; вход для секции «слепые зоны» отчёта |

## `behavior` — поведенческая ось

| Путь | Что | Осторожно |
|---|---|---|
| `available` | доступна ли ось вообще | `false` — история короче `min_commits`, ниже ничего нет |
| `commits_total`, `commits_with_code` | глубина окна | коммит без кода в метрики не входит |
| `churn_profile` | `code` / `test` / `generated` / `noise` — правок по типу файла | `test: 0` на репо с тестами = классификатор сломан |
| `thresholds` | эффективные пороги прогона | не дефолты из кода: конфиг мог их сдвинуть |
| `temporal_coupling[]` | `a`, `b`, `shared`, `degree`, `edits_a`, `edits_b`, `commits`, `degree_pct` | `degree` по слабейшему звену (`min`), не по среднему — отход от формулы code-maat, числа с чужими бенчмарками не сравнивать |
| `temporal_coupling_total` | сколько пар выше порога всего | список обрезан 40 |
| `hotspots[]` | `file`, `edits`, `loc`, `score`, `fix_commits`, `fix_share` + `*_pct` | `score` — абсолютный churn, слабый предиктор; ранжировать по `fix_share` вместе с `edits` |
| `containment` | `ratio`, `inside_one_layer`, `across_layers`, `layers_by_activity`, `top_crossings[]` | тесты исключены намеренно: с ними метрика меряет наличие тестов |
| `knowledge_risk[]` | `file`, `owner`, `share`, `edits`, `contributors` | риск проекта, не дефект кода |
| `reverts` | откатов в окне | |
| **`velocity.touch_cost`** | `median_files_per_commit`, `p90_files_per_commit`, `by_layer_median` | цена изменения в местах: прямой ответ на «почему правка задевает пять файлов» |
| **`velocity.episodes`** | `total`, `median_commits`, `median_span_days`, `p90_span_days`, `multi_commit_share`, `median_span_days_multi`, `median_commits_multi` | медиана по всем эпизодам почти всегда 0 дней; смотреть `*_multi` и долю эпизодов с доделками |
| **`velocity.slowest_files[]`** | `file`, `episodes`, `median_span_days`, `median_commits` | только файлы с ≥ `velocity_min_episodes` эпизодов |
| **`stability.rework_rate`** | доля правок, за которыми в ≤ `rework_window_days` пришёл фикс | это НЕ `fix_share`: тот говорит «файл часто чинят», этот — «правки не держатся» |
| **`stability.fix_latency_median_days`** | сколько дней до фикса | быстро = поймал тест, медленно = поймал пользователь |
| **`stability.unstable_files[]`** | `file`, `changes`, `reworked`, `rework_rate`, `rework_rate_lb`, `rework_rate_pct`, `fix_latency_median_days`, `reverts` | ранжировать по `rework_rate_lb` (нижняя граница Уилсона): сырая доля ставит «5 из 5» выше «55 из 67» |
| `stability.revert_files_top[]` | `file`, `reverts` | |

## `structure` — структурная ось

| Путь | Что | Осторожно |
|---|---|---|
| `parser.backends` | `tree-sitter` / `regex` по числу файлов | любой `regex` опускает потолок до `finding` |
| `parser.grammars_missing` | каких грамматик не хватило | |
| `files`, `edges` | размер графа | `edges/files < 0.3` — резолвер сломан, а не «зависимостей нет» |
| `layers` | файлов по первому сегменту пути | |
| `cycles[]` | `size`, `members` — **рантаймовые** | только эти считаются дефектом |
| `cycles_type_only[]` | держатся на `if TYPE_CHECKING:` / `import type` | заведены ради разрыва цикла; флагать — выдумать дефект |
| `type_only_edges` | сколько рёбер существует только для тайпчекера | |
| `adjacency` | полный список смежности рантайма | |
| `adjacency_through_barrels` | тот же граф, barrel пройдены насквозь | **различать R2 и R3 только по нему**: `from pkg import X` даёт ребро в `__init__.py` |
| `barrels` | опознанные реэкспортёры | высокий fan-in у barrel — замысел, не бог-объект |
| `self_loops` | файл, импортирующий сам себя | |
| `hubs[]` | `file`, `fan_in`, `fan_out`, `total`, `loc`, `cyclomatic_per_function_max`, `fan_in_pct`, `loc_pct` | |
| `fan_in_top[]` | `file`, `value` | |
| `complex_files[]` | `file`, `loc`, `cyclomatic_total`, `cyclomatic_per_function_max`, `worst_function_line`, `max_nesting`, `functions`, `cyclomatic_pct`, `loc_pct` | порог McCabe задан на **функцию**: сравнивать `cyclomatic_per_function_max`, не `cyclomatic_total`; вложенные def / arrow / function expression — отдельные функции, их ветвления во внешнюю не суммируются |
| `external_deps_top[]` | `name`, `imports` | |

## `tools` — что найдено на машине

| Путь | Что | Осторожно |
|---|---|---|
| `tools.git`, `tools.tree_sitter`, `tools.ruff`, `tools.semgrep` | `available` (bool), `version` (str или null) | у `semgrep` `version` всегда null: `semgrep --version` идёт 35–60 с, замер его не опрашивает |

`available: false` — слепая зона отчёта, а не «проверено, чисто».

## `lint` — ruff по группам

Запуск: `ruff check --isolated --select <группы> --output-format json --exit-zero`,
только `.py`. Ветвления и вложенность у ruff не берём — их считает tree-sitter.

| Путь | Что | Осторожно |
|---|---|---|
| `lint.available` | ruff нашёлся и отработал | `false` → причина в `lint.reason`, идёт в «слепые зоны»; потолок прогона не опускает |
| `lint.reason` | почему недоступен (`ruff not found`, `ruff failed: …`) | `null`, если доступен |
| `lint.tool_version` | версия ruff | |
| `lint.rules` | `simplicity` (`SIM`,`C4`,`FURB`), `width` (`PLR0913`,`PLR0915`), `perf` (`PERF`) | |
| `lint.files_checked` | число `.py`-файлов, прошедших проверку | **`0` при `available: true` — «нечего проверять», не «чисто»**; тогда есть `lint.note` |
| `lint.files[]` | `file`, `simplicity`, `width`, `perf`, `simplicity_pct`, `width_pct`, `perf_pct`, `hot` | все проверенные файлы, включая нулевые; `hot` — файл из `behavior.hotspots` |
| `lint.perf_semgrep` | `available`, `reason`, `files` (`путь → число`) | есть только при `--semgrep`; считаются правила категории `performance` набора `p/python`; нет semgrep или сети → `available: false` с причиной |

Допуск: `lint` без горячего файла — `hypothesis`; `lint` + горячий файл —
`finding`; `perf` выше `finding` не поднимается.

## `lint.clones` — дубли кода

Окна из 40 нормализованных токенов tree-sitter (идентификаторы → `ID`,
литералы → `LIT`, комментарии выброшены), хэш окна; пара файлов — клон, если
делит не меньше двух окон. Расширения `.py`, `.ts`, `.tsx`, `.js`. Считается
всегда, ruff для него не нужен.

| Путь | Что | Осторожно |
|---|---|---|
| `lint.clones.available` | детектор отработал | `false` → причина в `lint.clones.reason` (нет tree-sitter или грамматик); это «слепая зона», не «дублей нет» |
| `lint.clones.window` | размер окна в токенах | см. «Слепая зона» ниже |
| `lint.clones.files_checked`, `files_skipped` | разобрано / пропущено (нет грамматики, не читается) | |
| `lint.clones.pairs[]` | `a`, `b`, `shared_windows`, `lines_a`, `lines_b` | список обрезан 40; полное число — `pairs_total` |
| `lint.clones.pairs_total` | сколько пар выше порога всего | |
| `lint.clones.groups[]` | связные компоненты пар: `size`, `files` | список обрезан 40; полное число — `groups_total` |
| `lint.clones.common_windows_skipped` | окон, встреченных в >50 файлах (шаблоны, импорты) и выброшенных | не дубль, а boilerplate; число отброшенного — не тишина |
| `lint.clones.grammars_missing` | каких грамматик не хватило | |

Калибровка: проверка `детектор клонов различает` в `calibration.checks[]`
гоняет заведомую пару копий (с переименованными идентификаторами) и заведомо
другой файл, Python и TypeScript. Провал блокирует метрику `clones`
(`calibration.blocked_metrics`); недоступный детектор — не провал, потолок
прогона он не опускает. Допуск находки по дублям — как у `lint`: без горячего файла
`hypothesis`.

**Слепая зона: короткие дубли.** Окно — 40 токенов, пара — клон от двух общих
окон; дубль в 1–3 строки столько токенов не набирает. Контрольная группа
(приватный репозиторий, прогон 4): три одинаковые функции `usd()` во фронте не найдены;
7 одинаковых разборов флагов `must be 'on' or 'off'` в `config.py` детектор
тоже не собрал. Отсутствие клона в `measure.json` ≠ отсутствие дублей.
Расширять детектор ради режима `refactor` решено не было.

## Перцентили

Поля `*_pct` — место числа в распределении **этого** репозитория. Абсолютные
пороги подобраны на двух репозиториях и на третьем поплывут; перцентиль
переносится, `fix_share = 0.43` не переносится. В находке приводить оба:
«0.43, верхние 7% репозитория».

## Синтаксис пути

```
behavior.hotspots[0].fix_share                       индекс
behavior.hotspots[file=src/payments.py].edits_pct    фильтр по полю
structure.adjacency["src/user/repository.py"]        ключ со слэшем или точкой
```

Несколько ссылок в одном `source` разделяются `;`. Разрешение — `resolve_path`
в `scripts/ledger.py`, проверка всех ссылок разом — `zodchiy.py selfcheck`.
