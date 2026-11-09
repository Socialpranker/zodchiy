#!/usr/bin/env python3
"""Отчёт зодчего: HTML-чертёж, markdown и план фаз из одного набора JSON.

    python3 zodchiy.py report --measure M --findings F --decisions D \\
        --format html|md|plan [--lang ru|en] --out P

Рендерер не считает и не выдумывает: каждое число берётся из `measure.json`,
`findings.json` или `decisions.json`, а в HTML сидит в `<span class="num"
title="<файл>:<путь>">`. Модель пишет только текстовые поля в JSON; этот
модуль только раскладывает их по местам. Поэтому нет ни одной цифры в
шаблоне, кроме номеров фаз плана, и нет статистики поверх входа — только
перечисление. Отсутствующее необязательное поле — «—», а не исключение.

HTML — одна строка-шаблон, самодостаточный файл: без скриптов, ссылок на
шрифты и внешних адресов. `.html` в `SHIP_EXT` не входит, шаблон живёт здесь.
"""

from __future__ import annotations

import argparse
import html as _html
import json
import os
import re
import sys

from ledger import resolve_path

DASH = "—"
NUM_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?(?!\w)")
FILE_SEL = re.compile(r"\[file=([^\]]+)\]")
INF = 10**9

LABELS = {
    "ru": {
        "title": "Отчёт зодчего",
        "repo": "репозиторий",
        "head": "HEAD",
        "date": "дата",
        "calibration": "калибровка",
        "calibration_ok": "пройдена",
        "calibration_fail": "не пройдена",
        "blocked": "заблокировано",
        "ceiling": "потолок",
        "roles": "режим ролей",
        "checklist": "Чеклист",
        "cost": "цена прогона",
        "tokens": "токенов",
        "agents": "агентов",
        "base_dirty": "пробы не включают незакоммиченное",
        "skipped": "пропущено находок",
        "todo": "Что делать",
        "cards": "Карточки решений",
        "blind": "Слепые зоны",
        "appendix": "Приложение",
        "finding": "Находка",
        "verdict": "Вердикт",
        "forecast": "Прогноз → факт",
        "command": "Команда",
        "class": "Класс",
        "mode": "Режим",
        "confidence": "Уверенность",
        "owner_q": "вопрос владельцу",
        "accept": "принять",
        "reject": "отклонена",
        "unverified": "не проверено",
        "no_trial": "проба не запускалась",
        "no_decision": "нет решения лида",
        "pain": "Боль",
        "path": "Путь",
        "fact": "Факт",
        "place": "Место",
        "variants": "Варианты",
        "lead": "Выбор лида",
        "chosen": "выбран",
        "reserve": "запасной",
        "tried_reserve": "проба шла по запасному",
        "reason": "причина",
        "questions": "Решение за Иваном",
        "order": "порядок",
        "depends_on": "после",
        "critics": "Возражения критиков",
        "lens": "линза",
        "trial": "Проверка делом",
        "check": "проверка",
        "status": "статус",
        "metric": "метрика",
        "target": "цель",
        "before_source": "«до» взято из",
        "remedy_files": "файлов лечения",
        "payback": "окупаемость, кварталов",
        "payback_verdict": "вердикт окупаемости",
        "basis_forecast": "прогноз",
        "basis_fact": "факт пробы",
        "l4_pain": "боль, файлов/кв",
        "l4_max": "порог",
        "l4_out": "отсечена по окупаемости, проба не запускалась",
        "candidates": "кандидатов",
        "taken": "взято",
        "dropped_n": "отброшено",
        "slice_dropped": "срез обрезан",
        "branch": "ветка",
        "collision": "ветка уже была, взята другая; запрошена",
        "map": "Карта",
        "layers": "слои",
        "files": "файлов",
        "removed": "Снятые находки",
        "tool_missing": "инструмента нет",
        "lint_off": "lint недоступен",
        "grammars": "нет грамматики",
        "plan_title": "План рефакторинга",
        "plan_status": "**Статус:**",
        "plan_snapshot": "**Снимок:**",
        "plan_rules": "## Правила для всех фаз",
        "plan_measure": "## Что показал замер",
        "plan_phase": "## Фаза",
        "plan_excluded": "## Не входит и почему",
        "plan_price": "## Порядок и цена",
        "status_text": "план по замеру зодчего; фазу с вопросом владельцу не начинать без ответа",
        "accepted_trials": "пробы приняты для",
        "steps": "**Шаги:**",
        "p_forecast": "**Прогноз:**",
        "p_risk": "**Риск:**",
        "p_owner": "**Решение за Иваном:**",
        "p_start": "**Условие старта:**",
        "p_check": "**Проверка делом:**",
        "p_branch": "**Ветка:**",
        "variant": "вариант",
        "cond_answer": "ответ Ивана на вопросы выше",
        "cond_after": "принята",
        "cond_prev_phase": "принята предыдущая фаза этой находки",
        "cond_unverified": "проба не подтверждена",
        "cond_no_trial": "проба не запускалась",
        "cond_ready": "нет: проба принята",
        "rules": [
            "Фаза — один PR, одна ветка, один исполнитель.",
            "Проверки идут по одной; вердикт — по итоговой строке каждой, а не по коду выхода цепочки `a && b && c`.",
            "Для Python-диффа всегда добавлять `ruff check --select F821`: перенос кода ловит `NameError` только так.",
            "Проверка, которой нечем запуститься (нет Docker и т. п.), — `unverified` с причиной, не «красный».",
            "Фаза с непустым «Решение за Иваном» не стартует, пока нет ответа.",
            "Ветки `zodchiy/F<n>-<суть>`; пробы сами ничего не мержат.",
            "Числа плана — из `measure.json`, `findings.json`, `decisions.json`; пути указаны рядом.",
        ],
        "measure_head": "| Место | Факт | Путь в `measure.json` |",
        "price_head": "| Фаза | Находка | Порядок | После | Файлов лечения | Окупаемость, кварталов | Вердикт |",
    },
    "en": {
        "title": "Zodchiy report",
        "repo": "repository",
        "head": "HEAD",
        "date": "date",
        "calibration": "calibration",
        "calibration_ok": "passed",
        "calibration_fail": "failed",
        "blocked": "blocked",
        "ceiling": "ceiling",
        "roles": "role mode",
        "checklist": "Checklist",
        "cost": "run cost",
        "tokens": "tokens",
        "agents": "agents",
        "base_dirty": "trials do not include uncommitted changes",
        "skipped": "findings skipped",
        "todo": "What to do",
        "cards": "Decision cards",
        "blind": "Blind spots",
        "appendix": "Appendix",
        "finding": "Finding",
        "verdict": "Verdict",
        "forecast": "Forecast → actual",
        "command": "Command",
        "class": "Class",
        "mode": "Mode",
        "confidence": "Confidence",
        "owner_q": "owner question",
        "accept": "accept",
        "reject": "rejected",
        "unverified": "unverified",
        "no_trial": "no trial run",
        "no_decision": "no lead decision",
        "pain": "Pain",
        "path": "Path",
        "fact": "Fact",
        "place": "Place",
        "variants": "Variants",
        "lead": "Lead's choice",
        "chosen": "chosen",
        "reserve": "reserve",
        "tried_reserve": "trial ran on the reserve",
        "reason": "reason",
        "questions": "Owner decision",
        "order": "order",
        "depends_on": "after",
        "critics": "Critics' objections",
        "lens": "lens",
        "trial": "Proof by trial",
        "check": "check",
        "status": "status",
        "metric": "metric",
        "target": "target",
        "before_source": '"before" taken from',
        "remedy_files": "remedy files",
        "payback": "payback, quarters",
        "payback_verdict": "payback verdict",
        "basis_forecast": "forecast",
        "basis_fact": "trial fact",
        "l4_pain": "pain, files/quarter",
        "l4_max": "threshold",
        "l4_out": "cut by payback, no trial run",
        "candidates": "candidates",
        "taken": "taken",
        "dropped_n": "dropped",
        "slice_dropped": "slice trimmed",
        "branch": "branch",
        "collision": "branch already existed, another taken; requested",
        "map": "Map",
        "layers": "layers",
        "files": "files",
        "removed": "Withdrawn findings",
        "tool_missing": "tool missing",
        "lint_off": "lint unavailable",
        "grammars": "grammar missing",
        "plan_title": "Refactoring plan",
        "plan_status": "**Status:**",
        "plan_snapshot": "**Snapshot:**",
        "plan_rules": "## Rules for every phase",
        "plan_measure": "## What the measurement showed",
        "plan_phase": "## Phase",
        "plan_excluded": "## Out of scope and why",
        "plan_price": "## Order and cost",
        "status_text": "plan from the zodchiy measurement; a phase with an owner question does not start without an answer",
        "accepted_trials": "trials accepted for",
        "steps": "**Steps:**",
        "p_forecast": "**Forecast:**",
        "p_risk": "**Risk:**",
        "p_owner": "**Owner decision:**",
        "p_start": "**Start condition:**",
        "p_check": "**Proof by trial:**",
        "p_branch": "**Branch:**",
        "variant": "variant",
        "cond_answer": "the owner's answer to the questions above",
        "cond_after": "accepted",
        "cond_prev_phase": "the previous phase of this finding is accepted",
        "cond_unverified": "trial not confirmed",
        "cond_no_trial": "no trial was run",
        "cond_ready": "none: trial accepted",
        "rules": [
            "A phase is one PR, one branch, one executor.",
            "Checks run one by one; the verdict is the final line of each, not the exit code of an `a && b && c` chain.",
            "For a Python diff always add `ruff check --select F821`: moved code raises `NameError` only that way.",
            "A check that cannot run (no Docker etc.) is `unverified` with a reason, not red.",
            'A phase with a non-empty "Owner decision" does not start until answered.',
            "Branches `zodchiy/F<n>-<slug>`; trials never merge anything.",
            "Plan numbers come from `measure.json`, `findings.json`, `decisions.json`; paths are next to them.",
        ],
        "measure_head": "| Place | Fact | Path in `measure.json` |",
        "price_head": "| Phase | Finding | Order | After | Remedy files | Payback, quarters | Verdict |",
    },
}

CSS = """
:root{--paper:#FDFBF7;--ink:#1A1A1A;--pencil:#4A4A4A;--grid:#E0E0E0;--redline:#B3261E;
--serif:"Source Serif 4",Georgia,serif;--mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}
html{background:var(--paper)}
body{margin:0;padding:1rem;color:var(--ink);font:1.0625rem/1.55 var(--serif);overflow-wrap:anywhere}
.sheet{max-width:64rem;margin:0 auto;border:3px solid var(--ink);padding:1.25rem;background:var(--paper)}
header.top{display:flex;flex-wrap:wrap;gap:1rem;justify-content:space-between;align-items:flex-start;
border-bottom:2px solid var(--ink);padding-bottom:1rem}
h1{font-size:1.6rem;line-height:1.2;margin:0;flex:1 1 16rem}
h2{font-size:1.15rem;text-transform:uppercase;letter-spacing:.08em;margin:2rem 0 .75rem;
border-top:2px solid var(--ink);padding-top:.5rem}
h3{font-size:1.1rem;margin:0 0 .5rem}
h4{font:600 .8rem var(--mono);text-transform:uppercase;letter-spacing:.06em;color:var(--pencil);margin:1rem 0 .25rem}
p{margin:.35rem 0}
code,.num,pre,.mono{font-family:var(--mono);font-size:.9rem}
.num{font-variant-numeric:tabular-nums;border-bottom:1px dotted var(--pencil)}
.stamp{flex:0 1 22rem;min-width:0;border:2px solid var(--ink);font-size:.9rem}
.stamp div{display:flex;justify-content:space-between;gap:.75rem;padding:.25rem .5rem;border-top:1px solid var(--grid)}
.stamp div:first-child{border-top:0}
.stamp dt{color:var(--pencil);flex:0 0 auto;white-space:nowrap;overflow-wrap:normal;word-break:normal}
.stamp dd{margin:0;font-family:var(--mono);text-align:right;min-width:0;overflow-wrap:anywhere}
.stamp dl{margin:0}
.note{border:1px solid var(--pencil);padding:.4rem .6rem;margin:1rem 0;font-family:var(--mono);font-size:.9rem}
.scroll{overflow-x:auto;max-width:100%;margin:.5rem 0}
table{border-collapse:collapse;width:100%;min-width:32rem;font-size:.95rem}
th,td{border:1px solid var(--grid);padding:.35rem .5rem;text-align:left;vertical-align:top}
th,.tag{overflow-wrap:normal;word-break:normal}
th{font:600 .8rem var(--mono);color:var(--pencil);text-transform:uppercase;letter-spacing:.04em;border-bottom:2px solid var(--ink)}
.card{border:1px solid var(--pencil);padding:1rem;margin:1rem 0}
.card>h3{border-bottom:1px solid var(--grid);padding-bottom:.4rem}
.miss{color:var(--redline);font-weight:600}
.q{border-left:3px solid var(--ink);padding-left:.6rem;margin:.4rem 0}
.tag{font:.75rem var(--mono);border:1px solid var(--pencil);padding:0 .3rem;margin-right:.3rem;color:var(--pencil)}
pre.tail{margin:.25rem 0;padding:.4rem;border:1px solid var(--grid);white-space:pre-wrap;color:var(--pencil)}
ul{margin:.3rem 0;padding-left:1.2rem}
@media(max-width:40rem){body{padding:.4rem}.sheet{padding:.75rem}.stamp{flex-basis:100%}}
@media print{
@page{margin:14mm}
html,body{background:#fff}body{padding:0;font-size:10.5pt}
.sheet{border-width:2px;max-width:none;padding:0 .5rem}
.card,tr{break-inside:avoid}
.scroll{overflow:visible}table{min-width:0}
a{color:inherit;text-decoration:none}}
"""


# ---------- доступ к данным ----------


def g(obj, *keys, default=None):
    """Безопасный спуск по вложенным dict: нет ключа или не dict → default."""
    for k in keys:
        if not isinstance(obj, dict) or obj.get(k) is None:
            return default
        obj = obj[k]
    return obj


def as_list(x):
    return x if isinstance(x, list) else []


class V:
    """Число (или значение) вместе с путём, откуда оно взято."""

    def __init__(self, value, path):
        self.value, self.path = value, path

    def text(self):
        v = self.value
        if isinstance(v, list):
            return ", ".join(map(str, v)) or DASH
        return DASH if v is None else str(v)


def val(value, path):
    return V(value, path)


def dec_path(fid, tail):
    return f"decisions.json:findings[id={fid}].{tail}"


def fnd_path(fid, tail):
    return f"findings.json:findings[id={fid}].{tail}"


# ---------- модель ----------


class Item:
    def __init__(self, fid, f, d, index, skip_reason=None):
        self.id, self.f, self.d, self.index = fid, f, d, index
        # причина из run.skipped (сбой этапа, снята критиком): честнее, чем «нет решения лида»
        self.skip_reason = skip_reason
        self.lead = d.get("lead") if isinstance(d.get("lead"), dict) else {}
        self.trial = d.get("trial") if isinstance(d.get("trial"), dict) else {}
        self.status = self.trial.get("status")
        self.decided = bool(d) and bool(self.lead)
        self.title = f.get("title") or DASH
        self.questions = [q for q in as_list(self.lead.get("questions")) if q]
        order = self.lead.get("order")
        self.order = (
            order if isinstance(order, int) and not isinstance(order, bool) else None
        )
        # L4 по прогнозу лида: не ok и пробы не было — находка не входит в план
        self.l4_stop = (
            self.decided
            and not self.trial
            and self.lead.get("payback_verdict") in ("too_costly", "cold")
        )
        self.excluded = (not self.decided) or self.status == "rejected" or self.l4_stop

    def sort_key(self):
        return (INF if self.order is None else self.order, self.index)

    def no_decision(self, L):
        return self.skip_reason or L["no_decision"]

    def slice_dropped(self):
        """Что срез обрезал под этой находкой: [(имя, V)] только ненулевые — из decisions.json."""
        box = self.d.get("slice_dropped")
        if not isinstance(box, dict):
            return []
        return [
            (k, val(v, dec_path(self.id, f"slice_dropped.{k}")))
            for k, v in box.items()
            if isinstance(v, int) and not isinstance(v, bool) and v > 0
        ]

    def variant_id(self):
        """Вариант, который лежит в ветке: у пробы он свой (запасной после отказа основному), иначе выбор лида."""
        return self.trial.get("variant") or self.lead.get("chosen")

    def tried_reserve(self):
        """id запасного, если проба шла не по выбору лида; иначе None."""
        tried = self.trial.get("variant")
        return tried if tried and tried != self.lead.get("chosen") else None

    def variant(self):
        """Вариант плана (см. variant_id) из decisions.findings[].variants; нет — None."""
        vid = self.variant_id()
        for v in as_list(self.d.get("variants")):
            if isinstance(v, dict) and vid is not None and v.get("id") == vid:
                return v
        return None

    def payback(self, L):
        """Окупаемость L4: факт пробы, если проба была, иначе прогноз лида (по варианту, кодом сценария).
        None — нет ни того, ни другого. `basis` — пометка, откуда числа."""
        if self.trial:
            src, basis, tail = self.trial, L["basis_fact"], "trial"
        elif any(self.lead.get(k) is not None for k in ("remedy_files", "payback", "payback_verdict")):
            src, basis, tail = self.lead, L["basis_forecast"], "lead"
        else:
            return None
        return {
            "basis": basis,
            "remedy_files": val(src.get("remedy_files"), dec_path(self.id, f"{tail}.remedy_files")),
            "payback": val(src.get("payback"), dec_path(self.id, f"{tail}.payback")),
            "verdict": src.get("payback_verdict"),
        }

    def l4_reason(self, L):
        """Почему не входит: числа L4 из лида, без пересчёта."""
        lead, fid = self.lead, self.id
        parts = [
            (L["remedy_files"], val(lead.get("remedy_files"), dec_path(fid, "lead.remedy_files"))),
            (L["l4_pain"], val(lead.get("pain"), dec_path(fid, "lead.pain"))),
            (L["payback"], val(lead.get("payback"), dec_path(fid, "lead.payback"))),
            (L["l4_max"], val(lead.get("payback_max_quarters"), dec_path(fid, "lead.payback_max_quarters"))),
        ]
        text = "; ".join(f"{k} {v.text()}" for k, v in parts if v.value is not None)
        return f"{L['payback_verdict']}: {lead.get('payback_verdict')} ({L['basis_forecast']}); {text}"

    def phases(self):
        cut = [p for p in as_list(self.lead.get("phases")) if isinstance(p, dict)]
        return cut or [None]

    def forecast(self):
        """(metric, before, after, target, hit) — hit None, если не измерялось."""
        t, fid = self.trial, self.id
        measured = (
            bool(t)
            and t.get("gain_reason") is None
            and t.get("gain_metric") is not None
        )
        if not t:
            return None
        hit = t.get("gain_hit")
        return {
            "metric": t.get("gain_metric"),
            "before": val(t.get("gain_before"), dec_path(fid, "trial.gain_before")),
            "after": val(t.get("gain_after"), dec_path(fid, "trial.gain_after")),
            "target": val(t.get("gain_target"), dec_path(fid, "trial.gain_target")),
            "reason": t.get("gain_reason"),
            "hit": bool(hit) if measured and isinstance(hit, bool) else None,
        }

    def branch(self):
        return self.trial.get("branch") or None


class Model:
    def __init__(self, measure, findings, decisions):
        self.measure = measure if isinstance(measure, dict) else {}
        self.fdoc = findings if isinstance(findings, dict) else {"findings": findings}
        self.decisions = decisions if isinstance(decisions, dict) else {}
        fl = [f for f in as_list(self.fdoc.get("findings")) if isinstance(f, dict)]
        dl = {
            d.get("id"): d
            for d in as_list(self.decisions.get("findings"))
            if isinstance(d, dict)
        }
        self.run = g(self.decisions, "run", default={})
        skips = {
            str(x.get("id")): x["reason"]
            for x in as_list(self.run.get("skipped"))
            if isinstance(x, dict) and x.get("reason")
        }
        items, seen = [], set()
        for f in fl:
            fid = str(f.get("id", DASH))
            seen.add(fid)
            items.append(Item(fid, f, dl.get(fid, {}), len(items), skips.get(fid)))
        for fid, d in dl.items():
            if fid not in seen:
                items.append(Item(str(fid), {}, d, len(items), skips.get(str(fid))))
        self.items = sorted(items, key=Item.sort_key)
        # база для `git diff`: коммит, от которого trial строил worktree
        self.base = (
            self.run.get("head")
            or self.fdoc.get("head")
            or g(self.measure, "snapshot", "head")
        )

    def phases(self):
        """Плоский список фаз плана: (номер, Item, фаза|None, первая ли у находки)."""
        out = []
        for it in self.items:
            if it.excluded:
                continue
            for k, ph in enumerate(it.phases()):
                out.append((len(out) + 1, it, ph, k == 0))
        return out

    def excluded(self):
        return [it for it in self.items if it.excluded]

    def stamp(self):
        repo = self.fdoc.get("repo") or self.measure.get("repo")
        head = (
            self.run.get("head")
            or self.fdoc.get("head")
            or g(self.measure, "snapshot", "head")
        )
        cal = self.measure.get("calibration")
        ceiling = self.fdoc.get("confidence_ceiling") or g(
            self.measure, "confidence", "ceiling"
        )
        modes = []
        for m in [self.run.get("mode"), *[it.d.get("mode") for it in self.items]]:
            if m and m not in modes:
                modes.append(m)
        if self.run.get("head"):
            head_path = "decisions.json:run.head"
        elif self.fdoc.get("head"):
            head_path = "findings.json:head"
        else:
            head_path = "measure.json:snapshot.head"
        repo_path = "findings.json:repo" if self.fdoc.get("repo") else "measure.json:repo"

        def pair(key, a, b):
            box = self.run.get(key)
            if not isinstance(box, dict) or box.get(a) is None or box.get(b) is None:
                return None
            return tuple(val(box[k], f"decisions.json:run.{key}.{k}") for k in (a, b))

        return {
            "repo": repo,
            "head": head,
            "repo_v": val(repo, repo_path),
            "head_v": val(head, head_path),
            "date_v": val(self.run.get("date"), "decisions.json:run.date"),
            "ceiling_v": val(
                ceiling,
                "findings.json:confidence_ceiling"
                if self.fdoc.get("confidence_ceiling")
                else "measure.json:confidence.ceiling",
            ),
            "checklist": pair("checklist", "passed", "total"),
            "cost": self.cost_parts(),
            "date": self.run.get("date"),
            "cal": cal,
            "ceiling": ceiling,
            "modes": modes,
            "dirty": bool(self.run.get("base_dirty")),
            "skipped": [
                (str(x.get("id", DASH)), x.get("reason"))
                for x in as_list(self.run.get("skipped"))
                if isinstance(x, dict)
            ],
        }

    def cost_parts(self):
        """[(V, ключ метки)] для того, что есть в run.cost: Workflow пишет только agents."""
        box = self.run.get("cost")
        if not isinstance(box, dict):
            return []
        return [
            (val(box[k], f"decisions.json:run.cost.{k}"), k)
            for k in ("tokens", "agents")
            if box.get(k) is not None
        ]

    def candidates(self):
        """Отсечка аналитика (total, taken, dropped) как V с путями, либо None."""
        box = self.run.get("candidates")
        keys = ("total", "taken", "dropped")
        if not isinstance(box, dict) or any(box.get(k) is None for k in keys):
            return None
        return tuple(val(box[k], f"decisions.json:run.candidates.{k}") for k in keys)

    def sources(self, it):
        """Строки таблицы «что показал замер»: (место, V(факт), путь)."""
        rows = []
        for k, s in enumerate(as_list(it.f.get("source"))):
            if not isinstance(s, dict) or not s.get("path"):
                continue
            path, fact = s["path"], s.get("resolved")
            try:
                fact = resolve_path(self.measure, path)
            except (KeyError, IndexError, TypeError):
                pass
            m = FILE_SEL.search(path)
            place = m.group(1) if m else ".".join(re.split(r"[\[.]", path)[:2])
            rows.append((place, val(fact, f"measure.json:{path}"), path))
        return rows

    def blind_spots(self):
        """Слепые зоны — механически из полей входа: (текст, путь-источник)."""
        out, meas = [], self.measure
        cal = (
            meas.get("calibration") if isinstance(meas.get("calibration"), dict) else {}
        )
        for c in as_list(cal.get("checks")):
            if isinstance(c, dict) and c.get("passed") is False:
                out.append(
                    (
                        f"{c.get('check', DASH)}: {c.get('detail', DASH)}",
                        "measure.json:calibration.checks",
                    )
                )
        if cal.get("blocked_metrics"):
            out.append(
                (
                    "{blocked}: " + ", ".join(map(str, cal["blocked_metrics"])),
                    "measure.json:calibration.blocked_metrics",
                )
            )
        for name, t in (
            (meas.get("tools") or {}).items()
            if isinstance(meas.get("tools"), dict)
            else []
        ):
            if isinstance(t, dict) and t.get("available") is False:
                out.append((f"{{tool_missing}}: {name}", f"measure.json:tools.{name}"))
        lint = meas.get("lint") if isinstance(meas.get("lint"), dict) else {}
        if lint.get("available") is False:
            out.append(
                (
                    f"{{lint_off}}: {lint.get('reason') or DASH}",
                    "measure.json:lint.reason",
                )
            )
        elif lint.get("note"):
            out.append((str(lint["note"]), "measure.json:lint.note"))
        gm = g(meas, "structure", "parser", "grammars_missing", default=[])
        if gm:
            out.append(
                (
                    "{grammars}: " + ", ".join(map(str, gm)),
                    "measure.json:structure.parser.grammars_missing",
                )
            )
        for r in as_list(g(meas, "confidence", "reasons", default=[])):
            out.append((str(r), "measure.json:confidence.reasons"))
        for r in as_list(self.fdoc.get("ceiling_reasons")):
            out.append((str(r), "findings.json:ceiling_reasons"))
        disc = g(self.fdoc, "refutation", "disclosure")
        if disc and disc not in self.fdoc.get("ceiling_reasons", []):
            out.append((str(disc), "findings.json:refutation.disclosure"))
        for it in self.items:
            if it.status == "unverified":
                for r in as_list(it.trial.get("reasons")):
                    out.append((f"{it.id}: {r}", dec_path(it.id, "trial.reasons")))
        return out

    def removed(self, lang):
        """Снятые: отклонённые пробой и снятые линзой опровержения — с причиной."""
        out = []
        for it in self.items:
            for r in as_list(it.f.get("refutations")):
                if isinstance(r, dict) and r.get("verdict") == "dropped":
                    out.append(
                        (
                            it.id,
                            it.title,
                            f"{r.get('lens', DASH)}: {r.get('reason', DASH)}",
                            fnd_path(it.id, "refutations"),
                        )
                    )
            if it.status == "rejected":
                reasons = "; ".join(map(str, as_list(it.trial.get("reasons")))) or DASH
                out.append((it.id, it.title, reasons, dec_path(it.id, "trial.reasons")))
            elif it.l4_stop:
                out.append((it.id, it.title, it.l4_reason(LABELS[lang]), dec_path(it.id, "lead.payback_verdict")))
        return out


# ---------- рендер-примитивы ----------


def sub_labels(text, L):
    return re.sub(r"\{(\w+)\}", lambda m: L.get(m.group(1), m.group(0)), text)


class Html:
    """Числа — <span class="num" title="путь">, текст экранируется."""

    def __init__(self, L):
        self.L = L

    @staticmethod
    def esc(s):
        return _html.escape(str(s), quote=False)

    @staticmethod
    def attr(s):
        return _html.escape(str(s), quote=True)

    def n(self, v: V):
        if v is None or v.value is None:
            return DASH
        return (
            f'<span class="num" title="{self.attr(v.path)}">{self.esc(v.text())}</span>'
        )

    def p(self, text, path):
        """Текст с числами в span'ах: путь — поле, откуда взят весь абзац."""
        if text is None or text == "":
            return DASH
        s = self.esc(text)
        t = self.attr(path)
        return NUM_RE.sub(
            lambda m: f'<span class="num" title="{t}">{m.group(0)}</span>', s
        )

    def code(self, s):
        return f"<code>{self.esc(s)}</code>"


class Md:
    def __init__(self, L):
        self.L = L

    @staticmethod
    def esc(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    def n(self, v: V):
        return v.text() if v is not None else DASH

    def p(self, text, path=None):
        return self.esc(text) if text not in (None, "") else DASH

    def code(self, s):
        return f"`{s}`"


def no_trial_label(it, L):
    return L["l4_out"] if it.l4_stop else L["no_trial"]


def verdict_label(it, L):
    return {
        "accepted": L["accept"],
        "rejected": L["reject"],
        "unverified": L["unverified"],
    }.get(it.status, no_trial_label(it, L) if it.decided else it.no_decision(L))


def diff_cmd(m: Model, it):
    b = it.branch()
    return f"git diff {m.base}...{b}" if b and m.base else None


def forecast_cell(R, it):
    """(html/md-строка, miss). «до → после (цель …)» либо причина, почему не измерено."""
    L, fc = R.L, it.forecast()
    if fc is None:
        return DASH, False
    if (
        fc["after"].value is None
        and fc["before"].value is None
        and fc["target"].value is None
    ):
        return (
            R.p(fc["reason"], dec_path(it.id, "trial.gain_reason"))
            if fc["reason"]
            else DASH
        ), False
    text = (
        f"{R.n(fc['before'])} → {R.n(fc['after'])} ({L['target']} {R.n(fc['target'])})"
    )
    if fc["hit"] is True:
        text += " ✓"
    elif fc["hit"] is False:
        text += " ✗"
    elif fc["reason"]:
        text += " — " + R.p(fc["reason"], dec_path(it.id, "trial.gain_reason"))
    return text, fc["hit"] is False


def candidates_text(m, R):
    c = m.candidates()
    if c is None:
        return None
    L = R.L
    return f"{R.esc(L['candidates'])} {R.n(c[0])}, {R.esc(L['taken'])} {R.n(c[1])}, {R.esc(L['dropped_n'])} {R.n(c[2])}"


def slice_dropped_text(R, it):
    rows = it.slice_dropped()
    if not rows:
        return None
    return f"{R.esc(R.L['slice_dropped'])}: " + ", ".join(f"{R.esc(k)}: {R.n(v)}" for k, v in rows)


def payback_text(R, it):
    """«файлов лечения: … · окупаемость: … · вердикт: … (прогноз|факт пробы)» либо None."""
    L = R.L
    pb = it.payback(L)
    if pb is None:
        return None
    verdict = pb["verdict"]
    return " · ".join(
        [
            f"{R.esc(L['remedy_files'])}: {R.n(pb['remedy_files'])}",
            f"{R.esc(L['payback'])}: {R.n(pb['payback'])}",
            f"{R.esc(L['payback_verdict'])}: {R.esc(verdict or DASH)}"
            + (f" ({R.esc(pb['basis'])})" if verdict else ""),
        ]
    )


# ---------- HTML ----------


def miss_wrap(cell, miss):
    """Красная правка — только «прогноз не сбылся»."""
    return f'<span class="miss">{cell}</span>' if miss else cell



def render_html(m: Model, lang):
    L = LABELS[lang]
    R = Html(L)
    e, p, n = R.esc, R.p, R.n
    st = m.stamp()
    out = [
        "<!doctype html>",
        f'<html lang="{lang}"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{e(L['title'])} · {e(st['repo'] or DASH)}</title>",
        f'<style>{CSS}</style></head><body><main class="sheet">',
        f'<header class="top"><h1>{e(L["title"])}<br><span class="mono">{n(st["repo_v"])}</span></h1>',
    ]
    cal = st["cal"]
    if isinstance(cal, dict) and "passed" in cal:
        cal_txt = L["calibration_ok"] if cal["passed"] else L["calibration_fail"]
        blocked = as_list(cal.get("blocked_metrics"))
        if blocked:
            cal_txt += f" · {L['blocked']}: {', '.join(map(str, blocked))}"
    else:
        cal_txt = DASH
    chk = st["checklist"]
    cost = st["cost"]
    rows = [
        (L["repo"], n(st["repo_v"])),
        (L["head"], n(st["head_v"])),
        (L["date"], n(st["date_v"])),
        (L["calibration"], e(cal_txt)),
        (L["ceiling"], n(st["ceiling_v"])),
        (L["roles"], e(" · ".join(st["modes"]) or DASH)),
        (L["checklist"], f"{n(chk[0])}/{n(chk[1])}" if chk else DASH),
        (
            L["cost"],
            " · ".join(f"{n(v)} {e(L[k])}" for v, k in cost) or DASH,
        ),
    ]
    out.append('<aside class="stamp"><dl>')
    for k, v in rows:
        out.append(f"<div><dt>{e(k)}</dt><dd>{v}</dd></div>")
    out.append("</dl></aside></header>")
    if st["dirty"]:
        out.append(f'<p class="note">{e(L["base_dirty"])}</p>')
    if st["skipped"]:
        items = "; ".join(
            f"{e(fid)}: {p(why, f'decisions.json:run.skipped[id={fid}].reason')}"
            for fid, why in st["skipped"]
        )
        count = n(val(len(st["skipped"]), "decisions.json:run.skipped"))
        out.append(f'<p class="note">{e(L["skipped"])}: {count} — {items}</p>')
    cand = candidates_text(m, R)
    if cand:
        out.append(f'<p class="note">{cand}</p>')

    # что делать
    out.append(
        f'<section id="todo"><h2>{e(L["todo"])}</h2><div class="scroll"><table><thead><tr>'
    )
    out.append(
        "".join(
            f"<th>{e(L[k])}</th>" for k in ("finding", "verdict", "forecast", "command")
        )
    )
    out.append("</tr></thead><tbody>")
    for it in m.items:
        cell, miss = forecast_cell(R, it)
        cmd = diff_cmd(m, it)
        flag = f' <span class="tag">{e(L["owner_q"])}</span>' if it.questions else ""
        out.append(
            f'<tr id="todo-{R.attr(it.id)}"><td><a href="#card-{R.attr(it.id)}">{e(it.id)}</a> {p(it.title, fnd_path(it.id, "title"))}</td>'
            f"<td>{e(verdict_label(it, L))}{flag}</td>"
            f"<td>{miss_wrap(cell, miss)}</td>"
            f"<td>{R.code(cmd) if cmd else DASH}</td></tr>"
        )
    if not m.items:
        out.append(f'<tr><td colspan="4">{DASH}</td></tr>')
    out.append("</tbody></table></div></section>")

    # карточки
    out.append(f'<section id="cards"><h2>{e(L["cards"])}</h2>')
    if not m.items:
        out.append(f"<p>{DASH}</p>")
    for it in m.items:
        out.append(html_card(m, it, R))
    out.append("</section>")

    # слепые зоны
    out.append(f'<section id="blind"><h2>{e(L["blind"])}</h2><ul>')
    blind = m.blind_spots()
    for text, path in blind:
        out.append(f"<li>{p(sub_labels(text, L), path)}</li>")
    if not blind:
        out.append(f"<li>{DASH}</li>")
    out.append("</ul></section>")

    # приложение
    out.append(
        f'<section id="appendix"><h2>{e(L["appendix"])}</h2><h4>{e(L["map"])}</h4>'
    )
    layers = g(m.measure, "structure", "layers", default={})
    if isinstance(layers, dict) and layers:
        out.append(
            f'<div class="scroll"><table><thead><tr><th>{e(L["layers"])}</th><th>{e(L["files"])}</th></tr></thead><tbody>'
        )
        for name, count in layers.items():
            out.append(
                f"<tr><td>{R.code(name)}</td><td>{n(val(count, f'measure.json:structure.layers.{name}'))}</td></tr>"
            )
        out.append("</tbody></table></div>")
    else:
        out.append(f"<p>{DASH}</p>")
    out.append(f"<h4>{e(L['removed'])}</h4>")
    removed = m.removed(lang)
    if removed:
        out.append(
            f'<div class="scroll"><table><thead><tr><th>{e(L["finding"])}</th><th>{e(L["reason"])}</th></tr></thead><tbody>'
        )
        for fid, title, reason, path in removed:
            out.append(
                f"<tr><td>{e(fid)} {p(title, fnd_path(fid, 'title'))}</td><td>{p(reason, path)}</td></tr>"
            )
        out.append("</tbody></table></div>")
    else:
        out.append(f"<p>{DASH}</p>")
    out.append("</section></main></body></html>\n")
    return "\n".join(out)


def html_kv_table(R, head, rows):
    """Таблица в собственной обёртке прокрутки: широкая таблица не растягивает страницу."""
    h = "".join(f"<th>{R.esc(x)}</th>" for x in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="scroll"><table><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table></div>'


def html_card(m: Model, it: Item, R: Html):
    L, e, p, n = R.L, R.esc, R.p, R.n
    f, lead, t, fid = it.f, it.lead, it.trial, it.id
    a = [
        f'<article class="card" id="card-{R.attr(fid)}"><h3>{e(fid)} · {p(it.title, fnd_path(fid, "title"))}</h3>'
    ]
    tags = [
        ("class", it.d.get("class")),
        ("mode", it.d.get("mode")),
        ("confidence", f.get("confidence")),
    ]
    a.append(
        "<p>"
        + "".join(f'<span class="tag">{e(L[k])}: {e(v)}</span>' for k, v in tags if v)
        + "</p>"
    )
    if not it.decided:
        a.append(f"<p>{e(it.no_decision(L))}</p>")

    a.append(f"<h4>{e(L['pain'])}</h4>")
    for key in ("symptom", "cost_pain", "cost_spread"):
        a.append(f"<p>{p(f.get(key), fnd_path(fid, key))}</p>")
    src = m.sources(it)
    if src:
        a.append(
            html_kv_table(
                R,
                (L["place"], L["fact"], L["path"]),
                [(R.code(pl), n(v), R.code(pt)) for pl, v, pt in src],
            )
        )

    cut = slice_dropped_text(R, it)
    if cut:
        a.append(f"<p>{cut}</p>")

    variants = [v for v in as_list(it.d.get("variants")) if isinstance(v, dict)]
    a.append(f"<h4>{e(L['variants'])}</h4>")
    if variants:
        cols = list(dict.fromkeys(k for v in variants for k in v))
        rows = []
        for k, v in enumerate(variants):
            rows.append(
                [
                    p(
                        v[c]
                        if isinstance(v.get(c), str)
                        else json.dumps(v.get(c), ensure_ascii=False)
                        if c in v
                        else None,
                        dec_path(fid, f"variants[{k}].{c}"),
                    )
                    for c in cols
                ]
            )
        a.append(html_kv_table(R, cols, rows))
    else:
        a.append(f"<p>{DASH}</p>")

    a.append(f"<h4>{e(L['lead'])}</h4>")
    a.append(
        f"<p>{e(L['chosen'])}: <b>{p(lead.get('chosen'), dec_path(fid, 'lead.chosen'))}</b> · "
        f"{e(L['reserve'])}: {p(lead.get('reserve'), dec_path(fid, 'lead.reserve'))}"
        + (f" · {e(L['tried_reserve'])}: <b>{p(it.tried_reserve(), dec_path(fid, 'trial.variant'))}</b>" if it.tried_reserve() else "")
        + "</p>"
    )
    a.append(
        f"<p>{e(L['reason'])}: {p(lead.get('reason'), dec_path(fid, 'lead.reason'))}</p>"
    )
    if it.order is not None:
        a.append(
            f"<p>{e(L['order'])}: {n(val(it.order, dec_path(fid, 'lead.order')))}</p>"
        )
    if lead.get("depends_on"):
        a.append(
            f"<p>{e(L['depends_on'])}: {e(', '.join(map(str, as_list(lead['depends_on']))))}</p>"
        )
    for q in it.questions:
        a.append(
            f'<p class="q"><b>{e(L["questions"])}:</b> {p(q, dec_path(fid, "lead.questions"))}</p>'
        )

    a.append(f"<h4>{e(L['critics'])}</h4>")
    crit = [r for r in as_list(f.get("refutations")) if isinstance(r, dict)]
    if crit:
        a.append(
            html_kv_table(
                R,
                (L["lens"], L["verdict"], L["reason"]),
                [
                    (
                        e(r.get("lens", DASH)),
                        e(r.get("verdict", DASH)),
                        p(r.get("reason"), fnd_path(fid, "refutations")),
                    )
                    for r in crit
                ],
            )
        )
    else:
        a.append(f"<p>{p(f.get('refutation'), fnd_path(fid, 'refutation'))}</p>")

    a.append(f"<h4>{e(L['trial'])}</h4>")
    if not t:
        a.append(f"<p>{e(no_trial_label(it, L))}</p>")
        line = payback_text(R, it)
        if line:
            a.append(f"<p>{line}</p>")
    else:
        cell, miss = forecast_cell(R, it)
        a.append(
            f"<p>{e(L['status'])}: <b>{e(verdict_label(it, L))}</b> · {e(L['metric'])}: {R.code(t.get('gain_metric') or DASH)}</p>"
        )
        a.append(
            f"<p>{e(L['forecast'])}: {miss_wrap(cell, miss)}</p>"
        )
        a.append(
            f"<p>{e(L['before_source'])}: {e(t.get('before_source') or DASH)} · {payback_text(R, it)}</p>"
        )
        if t.get("branch"):
            extra = ""
            if t.get("branch_collision"):
                extra = f" ({e(L['collision'])} {R.code(t.get('branch_requested') or DASH)})"
            a.append(f"<p>{e(L['branch'])}: {R.code(t['branch'])}{extra}</p>")
        for r in as_list(t.get("reasons")):
            a.append(f'<p class="q">{p(r, dec_path(fid, "trial.reasons"))}</p>')
        checks = [c for c in as_list(t.get("checks")) if isinstance(c, dict)]
        if checks:
            a.append(
                html_kv_table(
                    R,
                    (L["check"], L["status"], L["reason"]),
                    [
                        (
                            R.code(c.get("name", DASH)),
                            e(c.get("status", DASH)),
                            p(c.get("reason"), dec_path(fid, "trial.checks")),
                        )
                        for c in checks
                    ],
                )
            )
            for c in checks:
                if c.get("tail"):
                    a.append(f'<pre class="tail">{e(c["tail"])}</pre>')
    a.append("</article>")
    return "\n".join(a)


# ---------- Markdown ----------


def md_table(head, rows):
    return [
        "| " + " | ".join(head) + " |",
        "|" + "|".join("---" for _ in head) + "|",
    ] + ["| " + " | ".join(r) + " |" for r in rows]


def snapshot_lines(m: Model, L, R):
    st = m.stamp()
    cal = st["cal"]
    if isinstance(cal, dict) and "passed" in cal:
        cal_txt = L["calibration_ok"] if cal["passed"] else L["calibration_fail"]
        if cal.get("blocked_metrics"):
            cal_txt += (
                f" ({L['blocked']}: {', '.join(map(str, cal['blocked_metrics']))})"
            )
    else:
        cal_txt = DASH
    rows = [
        (L["repo"], st["repo"]),
        (L["head"], st["head"]),
        (L["date"], st["date"]),
        (L["calibration"], cal_txt),
        (L["ceiling"], st["ceiling"]),
        (L["roles"], " · ".join(st["modes"]) or None),
        (
            L["checklist"],
            f"{st['checklist'][0].text()}/{st['checklist'][1].text()}"
            if st["checklist"]
            else None,
        ),
        (
            L["cost"],
            " · ".join(f"{v.text()} {L[k]}" for v, k in st["cost"]) or None,
        ),
    ]
    return [f"- {k}: {R.esc(v) if v else DASH}" for k, v in rows], st["dirty"]


def render_md(m: Model, lang):
    L = LABELS[lang]
    R = Md(L)
    st_lines, dirty = snapshot_lines(m, L, R)
    out = [f"# {L['title']} · {R.esc(m.stamp()['repo'] or DASH)}", "", *st_lines, ""]
    if dirty:
        out += [f"> {L['base_dirty']}", ""]
    skipped = m.stamp()["skipped"]
    if skipped:
        items = "; ".join(f"{R.esc(fid)}: {R.p(why)}" for fid, why in skipped)
        out += [f"> {L['skipped']}: {len(skipped)} — {items}", ""]
    cand = candidates_text(m, R)
    if cand:
        out += [f"> {cand}", ""]

    out += [f"## {L['todo']}", ""]
    rows = []
    for it in m.items:
        cmd = diff_cmd(m, it)
        rows.append(
            [
                f"{it.id} {R.esc(it.title)}",
                verdict_label(it, L) + (f" ({L['owner_q']})" if it.questions else ""),
                forecast_cell(R, it)[0],
                f"`{cmd}`" if cmd else DASH,
            ]
        )
    out += md_table(
        [L[k] for k in ("finding", "verdict", "forecast", "command")],
        rows or [[DASH] * 4],
    ) + [""]

    out += [f"## {L['cards']}", ""]
    for it in m.items:
        out += md_card(m, it, R, L)
    if not m.items:
        out += [DASH, ""]

    out += [f"## {L['blind']}", ""]
    blind = m.blind_spots()
    out += [f"- {R.esc(sub_labels(t, L))}" for t, _ in blind] or [f"- {DASH}"]
    out += ["", f"## {L['appendix']}", "", f"### {L['map']}", ""]
    layers = g(m.measure, "structure", "layers", default={})
    if isinstance(layers, dict) and layers:
        out += md_table(
            [L["layers"], L["files"]], [[f"`{k}`", str(c)] for k, c in layers.items()]
        )
    else:
        out.append(DASH)
    out += ["", f"### {L['removed']}", ""]
    removed = m.removed(lang)
    out += (
        md_table(
            [L["finding"], L["reason"]],
            [
                [f"{fid} {R.esc(title)}", R.esc(reason)]
                for fid, title, reason, _ in removed
            ],
        )
        if removed
        else [DASH]
    )
    return "\n".join(out) + "\n"


def md_card(m: Model, it: Item, R: Md, L):
    f, lead, t, fid = it.f, it.lead, it.trial, it.id
    a = [f"### {fid} · {R.esc(it.title)}", ""]
    if not it.decided:
        a += [f"_{R.esc(it.no_decision(L))}_", ""]
    a += [
        f"**{L['pain']}.** {R.p(f.get('symptom'))} {R.p(f.get('cost_pain'))} {R.p(f.get('cost_spread'))}",
        "",
    ]
    src = m.sources(it)
    if src:
        a += md_table(
            [L["place"], L["fact"], L["path"]],
            [[f"`{pl}`", R.n(v), f"`{pt}`"] for pl, v, pt in src],
        ) + [""]
    cut = slice_dropped_text(R, it)
    if cut:
        a += [cut, ""]
    tried = f"; {L['tried_reserve']}: {R.p(it.tried_reserve())}" if it.tried_reserve() else ""
    a += [
        f"**{L['lead']}.** {L['chosen']}: {R.p(lead.get('chosen'))}; {L['reserve']}: {R.p(lead.get('reserve'))}{tried}; "
        f"{L['reason']}: {R.p(lead.get('reason'))}",
        "",
    ]
    for q in it.questions:
        a += [f"**{L['questions']}:** {R.p(q)}", ""]
    crit = [r for r in as_list(f.get("refutations")) if isinstance(r, dict)]
    if crit:
        a += (
            [f"**{L['critics']}.**", ""]
            + [
                f"- {R.esc(r.get('lens', DASH))} — {R.esc(r.get('verdict', DASH))}: {R.p(r.get('reason'))}"
                for r in crit
            ]
            + [""]
        )
    if t:
        a += [
            f"**{L['trial']}.** {L['status']}: {verdict_label(it, L)}; {L['forecast']}: {forecast_cell(R, it)[0]}",
            "",
        ]
        a += [payback_text(R, it), ""]
        if t.get("branch"):
            a += [f"{L['branch']}: `{t['branch']}`", ""]
        for r in as_list(t.get("reasons")):
            a.append(f"- {R.esc(r)}")
        a += (
            md_table(
                [L["check"], L["status"], L["reason"]],
                [
                    [
                        f"`{c.get('name', DASH)}`",
                        str(c.get("status", DASH)),
                        R.p(c.get("reason")),
                    ]
                    for c in as_list(t.get("checks"))
                    if isinstance(c, dict)
                ],
            )
            if as_list(t.get("checks"))
            else []
        )
        a.append("")
    else:
        a += [f"**{L['trial']}.** {no_trial_label(it, L)}", ""]
        line = payback_text(R, it)
        if line:
            a += [line, ""]
    return a


# ---------- план фаз ----------


def start_condition(m: Model, it: Item, first: bool, L):
    conds = []
    if it.questions:
        conds.append(L["cond_answer"])
    for dep in as_list(it.lead.get("depends_on")):
        conds.append(f"{dep} {L['cond_after']}")
    if not first:
        conds.append(L["cond_prev_phase"])
    if first:
        if not it.trial:
            conds.append(L["cond_no_trial"])
        elif it.status == "unverified":
            reasons = "; ".join(map(str, as_list(it.trial.get("reasons")))) or DASH
            conds.append(f"{L['cond_unverified']}: {reasons}")
    return conds or [L["cond_ready"]]


def render_plan(m: Model, lang):
    L = LABELS[lang]
    R = Md(L)
    st = m.stamp()
    accepted = [it.id for it in m.items if it.status == "accepted"]
    status = L["status_text"]
    if accepted:
        status += f"; {L['accepted_trials']}: {', '.join(accepted)}"
    snap = ", ".join(
        f"{k} {R.esc(v)}"
        for k, v in (
            (L["repo"], st["repo"]),
            (L["head"], st["head"]),
            (L["date"], st["date"]),
            (L["ceiling"], st["ceiling"]),
        )
        if v
    )
    out = [
        f"# {L['plan_title']} · {R.esc(st['repo'] or DASH)}",
        "",
        f"{L['plan_status']} {status}.",
        f"{L['plan_snapshot']} {snap or DASH}. `measure.json`, `findings.json`, `decisions.json`.",
    ]
    if st["dirty"]:
        out.append(f"> {L['base_dirty']}")
    cand = candidates_text(m, R)
    if cand:
        out.append(f"> {cand}")
    out += [
        "",
        L["plan_rules"],
        "",
        *[f"- {r}" for r in L["rules"]],
        "",
        L["plan_measure"],
        "",
    ]
    rows = []
    for it in m.items:
        for place, v, path in m.sources(it):
            rows.append([f"{it.id} · `{place}`", R.n(v), f"`{path}`"])
    out += (
        [L["measure_head"], "|---|---|---|"]
        + (
            ["| " + " | ".join(r) + " |" for r in rows]
            or [f"| {DASH} | {DASH} | {DASH} |"]
        )
        + [""]
    )
    cuts = [(it, slice_dropped_text(R, it)) for it in m.items]
    out += [f"- {it.id} · {text}" for it, text in cuts if text]
    if any(text for _, text in cuts):
        out.append("")

    for num, it, ph, first in m.phases():
        title = R.p(it.title)
        if ph is not None and ph.get("title"):
            title += f" · {R.p(ph['title'])}"
        out += [f"{L['plan_phase']} {num}. {it.id} — {title}", ""]
        if first and it.branch():
            out += [f"{L['p_branch']} `{it.branch()}` — `{diff_cmd(m, it)}`", ""]
        steps = as_list(ph.get("steps")) if ph else []
        variant = it.variant_id()
        picked = it.variant()  # вариант из ветки важнее remedy аналитика: тот мог быть отвергнут
        out += [L["steps"]]
        if steps:
            out += [f"- {R.p(s)}" for s in steps]
        elif picked:
            head = f"- {L['variant']}: {R.p(variant)}"
            out.append(f"{head} — {R.p(picked.get('title'))}" if picked.get("title") else head)
            if picked.get("summary"):
                out.append(f"- {R.p(picked['summary'])}")
        else:
            if variant:
                out.append(f"- {L['variant']}: {R.p(variant)}")
            out.append(f"- {R.p(it.f.get('remedy'))}")
        out.append("")
        if first:
            fc, _ = forecast_cell(R, it)
            metric = it.trial.get("gain_metric") if it.trial else None
            if it.trial:
                out.append(f"{L['p_forecast']} {f'`{metric}`: ' if metric else ''}{fc}")
            elif picked:
                after = val(picked.get("forecast_after"), dec_path(it.id, f"variants[id={variant}].forecast_after"))
                if picked.get("forecast_metric") and after.value is not None:
                    out.append(f"{L['p_forecast']} `{picked['forecast_metric']}` → {R.n(after)}")
                else:
                    out.append(f"{L['p_forecast']} {DASH}")
            else:
                out.append(
                    f"{L['p_forecast']} {R.p(it.f.get('gain'))} ({L['target']}: {R.p(it.f.get('gain_target'))})"
                )
            out.append("")
        risk = (ph or {}).get("risk") or (
            picked.get("risk") if picked else it.f.get("remedy_cost")
        )
        out += [f"{L['p_risk']} {R.p(risk)}", ""]
        for q in it.questions:
            out += [f"{L['p_owner']} {R.p(q)}", ""]
        out.append(L["p_start"])
        out += [f"- {c}" for c in start_condition(m, it, first, L)]
        out.append("")
        if first and it.trial:
            out.append(f"{L['p_check']} {verdict_label(it, L)}")
            out += [
                f"- `{c.get('name', DASH)}`: {c.get('status', DASH)}"
                + (f" — {R.esc(c['reason'])}" if c.get("reason") else "")
                for c in as_list(it.trial.get("checks"))
                if isinstance(c, dict)
            ]
            out.append("")

    out += [L["plan_excluded"], ""]
    ex = m.excluded()
    for it in ex:
        if it.status == "rejected":
            why = "; ".join(map(str, as_list(it.trial.get("reasons")))) or DASH
        elif it.l4_stop:
            why = it.l4_reason(L)
        else:
            why = it.no_decision(L)
        out.append(f"- **{it.id}** — {R.esc(it.title)}: {R.esc(why)}")
    listed = {it.id for it in ex}
    for fid, why in st["skipped"]:
        if fid not in listed:  # упала до критиков: ни находки, ни решения — но причина должна остаться
            out.append(f"- **{R.esc(fid)}** — {R.esc(why or DASH)}")
            listed.add(fid)
    if not listed:
        out.append(DASH)
    out += ["", L["plan_price"], ""]
    rows = []
    for num, it, ph, first in m.phases():
        if not first:
            continue
        pb = it.payback(L) or {"basis": None, "remedy_files": None, "payback": None, "verdict": None}
        verdict = pb["verdict"]
        rows.append(
            [
                str(num),
                it.id,
                R.n(val(it.order, "")) if it.order is not None else DASH,
                ", ".join(map(str, as_list(it.lead.get("depends_on")))) or DASH,
                R.n(pb["remedy_files"]),
                R.n(pb["payback"]),
                f"{verdict} ({pb['basis']})" if verdict else DASH,
            ]
        )
    out += [L["price_head"], "|---|---|---|---|---|---|---|"] + (
        ["| " + " | ".join(r) + " |" for r in rows]
        or ["| " + " | ".join([DASH] * 7) + " |"]
    )
    return "\n".join(out) + "\n"


# ---------- вход ----------


def render(measure, findings, decisions, fmt="html", lang="ru") -> str:
    """measure: dict; findings: dict документа findings.json или список находок;
    decisions: dict decisions.json. Неизвестные fmt/lang — ValueError."""
    if lang not in LABELS:
        raise ValueError(f"lang: {lang!r} не из {sorted(LABELS)}")
    if fmt not in ("html", "md", "plan"):
        raise ValueError(f"format: {fmt!r} не из html|md|plan")
    m = Model(measure, findings, decisions)
    return {"html": render_html, "md": render_md, "plan": render_plan}[fmt](m, lang)


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(
        description="Отчёт зодчего из measure/findings/decisions — zodchiy"
    )
    ap.add_argument("--measure", required=True)
    ap.add_argument("--findings", required=True)
    ap.add_argument("--decisions", required=True)
    ap.add_argument("--format", choices=("html", "md", "plan"), default=None)
    ap.add_argument("--html", action="store_true", help="то же, что --format html")
    ap.add_argument("--lang", choices=sorted(LABELS), default="ru")
    ap.add_argument("--out", help="файл; без него — в stdout")
    args = ap.parse_args()
    fmt = args.format or ("html" if args.html else None)
    if fmt is None:
        ap.error("нужен --format html|md|plan (или --html)")
    text = render(
        _load(args.measure), _load(args.findings), _load(args.decisions), fmt, args.lang
    )
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(args.out)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
