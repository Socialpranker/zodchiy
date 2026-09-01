# `measure.json` — что в файле лежит

Единственный источник чисел для фазы «Судить». Читать прицельно по путям
отсюда, не целиком: на 500-файловом репозитории файл занимает мегабайты.

Путь из этой таблицы — то, что кладётся в поле `source` находки. Ссылка вида
`behavior.hotspots[file=src/x.py].fix_share` проверяется механически
(`zodchiy.py selfcheck`); проза «по данным замера» — нет.

- [Верхний уровень](#верхний-уровень)
- [`behavior` — поведенческая ось](#behavior--поведенческая-ось)
- [`structure` — структурная ось](#structure--структурная-ось)
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
| `complex_files[]` | `file`, `loc`, `cyclomatic_total`, `cyclomatic_per_function_max`, `worst_function_line`, `max_nesting`, `functions`, `cyclomatic_pct`, `loc_pct` | порог McCabe задан на **функцию**: сравнивать `cyclomatic_per_function_max`, не `cyclomatic_total` |
| `external_deps_top[]` | `name`, `imports` | |

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
