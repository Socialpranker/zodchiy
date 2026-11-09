#!/usr/bin/env python3
"""Поведение сценария `workflows/refactor.js`: отсечки и приёмка кодом, без модели.

Сценарий исполняется в `node` с подставными глобалами Workflow (`agent`, `pipeline`,
`parallel`, `log`, `phase`); ответы агентов заготовлены по метке вызова. Проверяются
не слова в тексте, а что сценарий ЗВАЛ и что записал в decisions.json. Каждая отсечка
покрыта мутацией: инвертируем её в копии сценария — сценарий-проверка обязан покраснеть,
иначе проверка ничего не ловит.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
SCRIPT = os.path.join(SKILL, "workflows", "refactor.js")

HARNESS = r"""
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const [scriptPath, configPath] = process.argv.slice(2);
const cfg = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const src = fs.readFileSync(scriptPath, 'utf8').replace(/^export const meta/m, 'const meta');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

const calls = [];
const logs = [];
const scribe = {};
const counters = {};
const judgeQueue = JSON.parse(JSON.stringify(cfg.judge || {}));
const variant = { title: 't', summary: 's', files: ['a.py'], remedy_files: 1, forecast_metric: 'm', forecast_after: 2, deletion_test: 'ok' };
const leadDefault = { chosen: 'v1', reserve: 'v2', reason: 'ё «r» 😀 "q" \\ \t', questions: [], stage: 'simplify', risk: 'low', depends_on: [] };

function bump(key) { counters[key] = (counters[key] || 0) + 1; return counters[key]; }

async function agent(prompt, opts) {
  const label = opts.label;
  calls.push({ label, agentType: opts.agentType || null, model: opts.model || null, effort: opts.effort || null, prompt });
  let m;
  if (label === 'замер') return { ok: !cfg.run_dir_busy, run_dir_free: !cfg.run_dir_busy, head: 'abc', dirty: false, tools: 'git', ceiling: 'finding' };
  if (label === 'аналитик') return { total_candidates: cfg.total, findings: cfg.analyst };
  if ((m = label.match(/^(F\d+): срез$/))) {
    if ((cfg.fail_slice || []).includes(m[1])) return null;
    const painCfg = cfg.pain || {};
    const res = { path: 'x', bytes: 100, payback_max_quarters: cfg.payback_max ?? 4, dropped: (cfg.dropped || {})[m[1]] || {} };
    const pain = m[1] in painCfg ? painCfg[m[1]] : 10;
    if (pain !== null) res.pain = pain;
    return res;
  }
  if ((m = label.match(/^(F\d+): (L\d)$/))) {
    const v = (cfg.verdicts || {})[`${m[1]}:${m[2]}`] || 'survives';
    return (cfg.mute_critics || []).includes(m[1]) ? null : { verdict: v, reason: 'ё «r» 😀 "q" \\ \t' };
  }
  if ((m = label.match(/^(F\d+): лид$/))) return { ...leadDefault, ...((cfg.leads || {})[m[1]] || {}) };
  if ((m = label.match(/^(F\d+): prepare (v\d)/))) {
    const n = bump(`prep:${m[1]}`);
    return { worktree: `/wt/${m[1]}-${n}`, branch: `zodchiy/${m[1]}-s${n}`, branch_requested: `zodchiy/${m[1]}-s`, branch_collision: false, base_head: 'abc', base_dirty: false, measure_before: null };
  }
  if (opts.agentType === 'sonnet-implementer') return { done: true, files: ['a.py'], ...(cfg.exec_phase && /запасной/.test(label) ? { phase: cfg.exec_phase } : {}) };
  if ((m = label.match(/^(F\d+): judge (v\d)/))) {
    const q = judgeQueue[m[1]] || [];
    const status = q.length ? q.shift() : 'accepted';
    return { variant: m[2], checks: [{ name: 'pytest', status: 'pass' }], gain_metric: 'm', gain_before: 3, gain_after: 2, gain_target: 2, gain_hit: true, remedy_files: 1, status, reasons: status === 'rejected' ? ['gain not reached'] : [] };
  }
  if (/^F\d+: cleanup/.test(label)) return { cleaned: 'x' };
  if (label.startsWith('запись decisions.json')) {
    // писарь = одна команда: кладём промпт в расшифровку субагента так, как это делает харнесс
    // (заголовок и отступ в две пробела на строку), и гоняем НАСТОЯЩИЙ `zodchiy.py scribe`
    const attempt = bump('scribe');
    if (cfg.lazy_scribe) {
      // писарь, не запускающий команду: берёт из промпта 16-значные hex-токены как «отпечатки»
      const toks = prompt.match(/\b[0-9a-f]{16}\b/g) || [];
      const pick = (i) => toks[i] || toks[0] || '';
      const idsLine = prompt.split('\n').find((l, i, a) => a[i - 1] && a[i - 1].trim() === '<<<ZODCHIY findings.run.csv');
      return { schema_errors: [], decisions_digest: pick(0), refutation_digest: pick(1), csv_ids: JSON.parse(idsLine) };
    }
    const nonce = (prompt.match(/scribe --nonce "([^"]+)" --run-dir "[^"]+"/) || [])[1];
    if (!nonce) throw new Error('в промпте писаря нет команды scribe');
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'zscribe-'));
    try {
      const runDir = path.join(tmp, 'run');
      const sub = path.join(tmp, 'cfg', 'projects', 'p', 's', 'subagents', 'workflows', 'wf_1');
      fs.mkdirSync(runDir, { recursive: true });
      fs.mkdirSync(sub, { recursive: true });
      const ids = (cfg.analyst || []).map((x) => x.id).concat(['F99']);
      fs.writeFileSync(path.join(runDir, 'findings.csv'), 'id,title\n' + ids.map((i) => `${i},t ${i}\n`).join(''));
      let shown = prompt;
      // подмена содержимого в пути от сценария до писаря: отпечаток обязан разойтись
      const swapping = cfg.swap && attempt <= (cfg.swap_attempts || Infinity);
      if (swapping) shown = shown.replace(new RegExp(`(<<<ZODCHIY ${cfg.swap === 'decisions' ? 'decisions' : 'refutation'}\\.json\\n(?:\\[|\\{)[^\\n]*?"reason":")(.)`), (m, a, c) => a + (c === 'X' ? 'Y' : 'X'));
      const framed = '[Workflow harness — computed task] header\nThe computed task text follows:\n' + shown.split('\n').map((l) => '  ' + l).join('\n');
      fs.writeFileSync(path.join(sub, 'agent-a1.jsonl'), JSON.stringify({ type: 'user', message: { role: 'user', content: framed } }) + '\n');
      let out;
      try {
        out = execFileSync('python3', [path.join(cfg.skill_root, 'zodchiy.py'), 'scribe', '--nonce', cfg.wrong_nonce ? 'nope' : nonce, '--run-dir', runDir], { env: { ...process.env, CLAUDE_CONFIG_DIR: path.join(tmp, 'cfg') } }).toString();
      } catch (e) {
        out = e.stdout.toString();
      }
      const res = JSON.parse(out);
      const read = (n) => fs.readFileSync(path.join(runDir, n), 'utf8');
      if (!res.error) {
        Object.assign(scribe, { refutations: JSON.parse(read('refutation.json')), decisions: JSON.parse(read('decisions.json')), ids: res.csv_ids });
      }
      return { schema_errors: [], ...res, ...(cfg.schema_errors ? { schema_errors: cfg.schema_errors } : {}) };
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }
  if (label === 'отчёт') return { ok: true, selfcheck_result: 'OK' };
  if ((m = label.match(/^(F\d+): /))) return { ...variant, remedy_files: (cfg.remedy_files || {})[m[1]] ?? variant.remedy_files };
  throw new Error(`нет заготовки для ${label}`);
}

async function pipeline(items, ...stages) {
  return Promise.all(items.map(async (it, i) => {
    try { let r = it; for (const s of stages) r = await s(r, it, i); return r; } catch (e) { return null; }
  }));
}
async function parallel(thunks) { return Promise.all(thunks.map((t) => t().catch(() => null))); }

const args = { repo: '/r', top: 3, dry: false, lang: 'ru', skill: '/s', run_dir: '/r/.zodchiy/run-1', date: '2026-09-29', ...(cfg.args || {}) };
new AsyncFunction('args', 'agent', 'pipeline', 'parallel', 'log', 'phase', src)(args, agent, pipeline, parallel, (m) => logs.push(m), () => {})
  .then((result) => console.log(JSON.stringify({ result, calls, logs, scribe })))
  .catch((e) => console.log(JSON.stringify({ error: String(e && e.stack || e), calls, logs, scribe })));
"""

ANALYST = [
    {"id": "F1", "title": "t1", "class": "quick-fix", "slug": "s1", "priority": 8, "files": ["a.py"]},
    {"id": "F2", "title": "t2", "class": "architectural", "slug": "s2", "priority": 7, "files": ["b.py"]},
    {"id": "F3", "title": "t3", "class": "scheduled", "slug": "s3", "priority": 6, "files": ["c.py"]},
]
PHASES = [{"title": "PHASE-ONE", "steps": ["a"]}, {"title": "PHASE-TWO", "steps": ["b"]}]
F2_LEAD = {"phases": PHASES, "stage": "decouple", "depends_on": ["F1"]}


def cfg(**kw):
    base = {
        "total": 9,
        "analyst": ANALYST,
        "leads": {"F2": F2_LEAD, "F3": {"questions": ["забытый экран?"]}},
    }
    base.update(kw)
    return base


def run_node(config: dict, script: str = SCRIPT) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        h, c = os.path.join(tmp, "h.js"), os.path.join(tmp, "c.json")
        with open(h, "w", encoding="utf-8") as fh:
            fh.write(HARNESS)
        with open(c, "w", encoding="utf-8") as fh:
            json.dump({**config, "skill_root": SKILL}, fh, ensure_ascii=False)
        r = subprocess.run(["node", h, script, c], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def kinds(out: dict, fid: str) -> dict:
    """Сколько раз сценарий позвал каждую роль по находке."""
    n = {"slice": 0, "critic": 0, "designer": 0, "lead": 0, "prepare": 0, "executor": 0, "judge": 0, "cleanup": 0}
    for c in out["calls"]:
        label = c["label"]
        if not label.startswith(f"{fid}: "):
            continue
        rest = label[len(fid) + 2:]
        if c["agentType"] == "sonnet-implementer":
            n["executor"] += 1
        elif rest == "срез":
            n["slice"] += 1
        elif re.fullmatch(r"L\d", rest):
            n["critic"] += 1
        elif rest == "лид":
            n["lead"] += 1
        elif rest.startswith("prepare"):
            n["prepare"] += 1
        elif rest.startswith("judge"):
            n["judge"] += 1
        elif rest.startswith("cleanup"):
            n["cleanup"] += 1
        else:
            n["designer"] += 1
    return n


def dec(out: dict, fid: str):
    return next((f for f in out["scribe"]["decisions"]["findings"] if f["id"] == fid), None)


def executor_prompts(out: dict, fid: str) -> list[str]:
    return [c["prompt"] for c in out["calls"] if c["agentType"] == "sonnet-implementer" and c["label"].startswith(f"{fid}: ")]


class Fails(list):
    def check(self, cond, msg):
        if not cond:
            self.append(msg)


def s_flow(script=SCRIPT) -> list[str]:
    """Все выжили; F1 quick-fix, F2 architectural (запасной), F3 с вопросом владельцу."""
    o = run_node(cfg(judge={"F2": ["rejected", "accepted"]}), script)
    f = Fails()
    if "error" in o:
        return [o["error"]]
    analyst = next((c["prompt"] for c in o["calls"] if c["label"] == "аналитик"), "")
    f.check("confidence ∈ hypothesis | finding | verdict" in analyst, "в промпте аналитика нет перечня confidence")
    f.check("потолка confidence.ceiling" in analyst and "(finding)" in analyst, "в промпте аналитика нет потолка из measure.json")
    k1, k2, k3 = kinds(o, "F1"), kinds(o, "F2"), kinds(o, "F3")
    f.check(k1["designer"] == 1 and k1["lead"] == 0, f"quick-fix: 1 проектировщик и без лида, а было {k1}")
    f.check(any("удали, а не абстрагируй" in c["label"] for c in o["calls"] if c["label"].startswith("F1: ")), "у quick-fix не тот характер")
    f.check(k2["designer"] == 3 and k2["lead"] == 1, f"architectural: 3 проектировщика и лид, а было {k2}")
    f.check(k3["designer"] == 3 and k3["lead"] == 1, f"scheduled: 3 проектировщика и лид, а было {k3}")
    f.check(k3["prepare"] == k3["executor"] == k3["judge"] == 0, f"вопрос лида — проба не идёт, а было {k3}")
    f.check(dec(o, "F3") is not None and "trial" not in dec(o, "F3"), "у F3 с вопросом не должно быть trial")
    f.check(dec(o, "F3")["lead"]["questions"] == ["забытый экран?"], "questions[] не дошли до decisions")
    for key in ("prepare", "executor", "judge", "cleanup"):
        f.check(k2[key] == 2, f"F2: основной + запасной ровно по разу ({key}), а было {k2[key]}")
    ex = executor_prompts(o, "F2")
    f.check(len(ex) == 2 and all("PHASE-ONE" in p or "самодостаточную фазу" in p for p in ex), "architectural: исполнитель получает только первую фазу")
    f.check(all("PHASE-TWO" not in p for p in ex), "architectural: вторая фаза утекла исполнителю")
    f.check(dec(o, "F2")["trial"]["variant"] == "v2" and dec(o, "F2")["trial"]["status"] == "accepted", "итог F2 — запасной v2 accepted")
    f.check(any("основной вариант v1 отклонён" in r for r in dec(o, "F2")["trial"]["reasons"]), "отказ основного не записан в reasons")
    f.check(dec(o, "F1")["trial"]["status"] == "accepted", "F1 accepted")
    ids = [x["id"] for x in o["scribe"]["decisions"]["findings"]]
    f.check(ids.index("F1") < ids.index("F2"), f"F2 зависит от F1, порядок {ids}")
    f.check([x["lead"]["order"] for x in o["scribe"]["decisions"]["findings"]] == list(range(len(ids))), "order не сквозной")
    f.check(sorted(o["result"]["branches"]) == ["zodchiy/F1-s1", "zodchiy/F2-s2"], f"ветки принятых: {o['result']['branches']}")
    f.check(any("отброшено 6" in m for m in o["logs"]), f"нет строки об отброшенном top: {o['logs']}")
    f.check(any(m.startswith("F1: критики сняли 0 из 3") for m in o["logs"]), "нет строки про критиков")
    f.check(any("F1: прогноз m 3→2, факт 2 ✓" in m for m in o["logs"]), "нет строки прогноз → факт")
    f.check(o["result"]["decisions_path"] and o["result"]["errors"] == [], f"итог: {o['result']}")
    f.check(o["scribe"]["decisions"]["run"]["cost"]["agents"] == o["result"]["agents"], "cost.agents не равен числу агентов")
    return f


def s_accepted_first(script=SCRIPT) -> list[str]:
    o = run_node(cfg(judge={"F2": ["accepted"]}), script)
    k2 = kinds(o, "F2")
    return [] if k2["prepare"] == k2["executor"] == k2["judge"] == 1 else [f"принятый основной — запасной не нужен: {k2}"]


def s_reserve_once(script=SCRIPT) -> list[str]:
    o = run_node(cfg(judge={"F2": ["rejected", "rejected", "rejected"]}), script)
    f = Fails()
    k2 = kinds(o, "F2")
    f.check(k2["prepare"] == 2 and k2["judge"] == 2 and k2["executor"] == 2, f"запасной — одна попытка, а было {k2}")
    f.check(dec(o, "F2")["trial"]["status"] == "rejected", "итог F2 — rejected")
    f.check("branch" not in dec(o, "F2")["trial"], "ветка отклонённой удалена, в decisions её быть не должно")
    f.check("zodchiy/F2-s2" not in o["result"]["branches"] and "zodchiy/F2-s1" not in o["result"]["branches"], "отклонённая ветка попала в branches")
    return f


def s_reserve_phases(script=SCRIPT) -> list[str]:
    """Запасной architectural: фазы основного не описывают ветку — в decisions только фаза, которую сделал исполнитель."""
    phase = {"title": "RESERVE-CUT", "steps": ["x"], "risk": "r"}
    o = run_node(cfg(judge={"F2": ["rejected", "accepted"]}, exec_phase=phase), script)
    f = Fails()
    if "error" in o:
        return [o["error"]]
    lead = dec(o, "F2")["lead"]
    f.check(lead.get("phases") == [phase], f"фазы после запасного: {lead.get('phases')}")
    f.check("PHASE-ONE" not in json.dumps(dec(o, "F2"), ensure_ascii=False), "фазы отвергнутого основного остались в decisions")
    f.check("фаз" in lead["reason"] and "запасн" in lead["reason"], f"нет оговорки в reason: {lead['reason']}")
    o = run_node(cfg(judge={"F2": ["accepted"]}, exec_phase=phase), script)
    f.check(dec(o, "F2")["lead"].get("phases") == PHASES, "основной принят — фазы лида остаются")
    return f


def s_phase_of(script=SCRIPT) -> list[str]:
    """architectural: judge знает, что проба — первая фаза (иначе цель находки отклонит её заведомо)."""
    o = run_node(cfg(judge={"F2": ["rejected", "accepted"]}), script)
    if "error" in o:
        return [o["error"]]
    f = Fails()
    judge = {c["label"]: c["prompt"] for c in o["calls"] if ": judge " in c["label"]}
    f.check("--phase-of 2" in judge.get("F2: judge v1", ""), "основной architectural: число фаз лида не передано judge")
    f.check("--phase-of ?" in judge.get("F2: judge v2", ""), "запасной architectural: фаз нет от лида, ждём '?'")
    f.check("F1: judge v1" in judge and "--phase-of" not in judge["F1: judge v1"], "F1 не architectural: --phase-of не нужен")
    return f


def s_run_dir_busy(script=SCRIPT) -> list[str]:
    """Каталог прогона не пуст: сценарий падает сразу и называет путь, ни одного агента дальше."""
    o = run_node(cfg(run_dir_busy=True), script)
    f = Fails()
    f.check("error" in o and "/r/.zodchiy/run-1" in o["error"] and "не пуст" in o["error"], f"нет громкого отказа с путём: {o.get('error') or o.get('result')}")
    f.check([c["label"] for c in o["calls"]] == ["замер"], f"после отказа шли агенты: {[c['label'] for c in o['calls']]}")
    prompt = o["calls"][0]["prompt"]
    f.check(0 <= prompt.find("ls -A") < prompt.find("mkdir"), "проверка пустоты каталога должна идти раньше mkdir")
    ok = run_node(cfg(judge={"F2": ["accepted"]}), script)
    f.check("error" not in ok, "свободный каталог не должен ронять прогон")
    return f


def s_critics_remove(script=SCRIPT) -> list[str]:
    o = run_node(cfg(verdicts={"F3:L2": "dropped"}), script)
    f = Fails()
    k3 = kinds(o, "F3")
    f.check(k3["critic"] == 3, f"критиков F3 три, а было {k3}")
    f.check(k3["designer"] == k3["lead"] == k3["prepare"] == 0, f"снята критиками — дальше никого, а было {k3}")
    f.check(dec(o, "F3") is None, "снятая находка не должна быть в decisions")
    f.check(any(r["finding_id"] == "F3" and r["verdict"] == "dropped" for r in o["scribe"]["refutations"]), "вердикт dropped не записан")
    f.check("F3" in o["scribe"]["ids"], "снятая находка остаётся в CSV прогона (есть вердикты)")
    f.check(any(m == "F3: критики сняли 1 из 3 возражений" for m in o["logs"]), "нет строки про снятую находку")
    skipped = {x["id"]: x["reason"] for x in o["scribe"]["decisions"]["run"]["skipped"]}
    f.check(skipped.get("F3") == "снята критиком: L2", f"снятая критиком должна быть в run.skipped с линзой: {skipped}")
    o = run_node(cfg(verdicts={"F3:L1": "dropped", "F3:L3": "dropped"}), script)
    skipped = {x["id"]: x["reason"] for x in o["scribe"]["decisions"]["run"]["skipped"]}
    f.check(skipped.get("F3") == "снята критиком: L1, L3", f"обе линзы в причине: {skipped}")
    return f


def s_dry(script=SCRIPT) -> list[str]:
    o = run_node(cfg(args={"dry": True}), script)
    f = Fails()
    for fid in ("F1", "F2", "F3"):
        k = kinds(o, fid)
        f.check(k["prepare"] == k["executor"] == k["judge"] == k["cleanup"] == 0, f"--dry: проб быть не должно, {fid} {k}")
    f.check(kinds(o, "F2")["lead"] == 1 and kinds(o, "F3")["lead"] == 1, "--dry останавливается ПОСЛЕ лида: лид звать")
    f.check(all("trial" not in x for x in o["scribe"]["decisions"]["findings"]), "--dry: trial в decisions")
    f.check(o["result"]["branches"] == [], "--dry: веток нет")
    return f


def s_top(script=SCRIPT) -> list[str]:
    o = run_node(cfg(args={"top": 2}, total=9), script)
    f = Fails()
    f.check(sum(kinds(o, x)["slice"] for x in ("F1", "F2", "F3")) == 2 and kinds(o, "F3")["slice"] == 0, "top=2: обработаны только две лучшие")
    f.check(any("отброшено 7" in m for m in o["logs"]), f"top: число отброшенного (9-2=7) в логе: {o['logs']}")
    f.check(o["scribe"]["ids"] == ["F1", "F2"], f"лишняя строка CSV сверх top не должна идти в отчёт: {o['scribe']['ids']}")
    return f


def s_failed_slice(script=SCRIPT) -> list[str]:
    o = run_node(cfg(args={"top": 2}, fail_slice=["F2"]), script)
    f = Fails()
    if "error" in o:
        return [o["error"]]
    k2 = kinds(o, "F2")
    f.check(k2["critic"] == k2["designer"] == k2["lead"] == 0, f"упавший срез — дальше по F2 никого, а было {k2}")
    f.check(kinds(o, "F1")["prepare"] == 1, "F1 должна пройти до конца")
    f.check(any(c["label"] == "отчёт" for c in o["calls"]), "отчёт обязан собраться")
    f.check(o["scribe"]["ids"] == ["F1"], f"CSV прогона без упавшей: {o['scribe']['ids']}")
    f.check(dec(o, "F2") is None, "упавшая — не в decisions")
    f.check([x["id"] for x in o["scribe"]["decisions"]["run"]["skipped"]] == ["F2"], "F2 записана как пропущенная")
    f.check(o["result"]["report_html"] and o["result"]["plan_md"], f"отчёт есть: {o['result']}")
    f.check(any("F2: сбой этапа" in m for m in o["logs"]), "сбой не залогирован")
    return f


def s_mute_critics(script=SCRIPT) -> list[str]:
    """Критики не ответили: нет вердиктов — находка не в CSV прогона и не в refutation."""
    o = run_node(cfg(args={"top": 2}, mute_critics=["F2"]), script)
    f = Fails()
    f.check("F2" not in o["scribe"]["ids"], "без вердиктов находке нечего делать в selfcheck")
    f.check(all(r["finding_id"] != "F2" for r in o["scribe"]["refutations"]), "чужих вердиктов F2 нет")
    f.check(kinds(o, "F2")["designer"] == 0, "без критиков проектировщиков не зовём")
    return f


def s_scribe_verbatim(script=SCRIPT) -> list[str]:
    f = Fails()
    o = run_node(cfg(), script)
    f.check(o["result"]["decisions_path"] and o["result"]["errors"] == [], f"дословный писарь проходит: {o['result']}")
    f.check(any(c["label"] == "отчёт" for c in o["calls"]), "дословная запись — отчёт строим")
    o = run_node(cfg(schema_errors=["$.run: нет поля"]), script)
    f.check(o["result"]["decisions_path"] is None and o["result"]["errors"], "ошибки схемы — путь не отдаём")
    return f


def scribe_calls(o) -> list[dict]:
    return [c for c in o["calls"] if c["label"].startswith("запись decisions.json")]


def s_scribe_swap(script=SCRIPT) -> list[str]:
    """Содержимое в пути от сценария до писаря подменено на такое же по длине: содержимое, а не длина, решает.
    Повтора нет: файлы пишет код, второй вызов дал бы тот же отпечаток."""
    f = Fails()
    for what, needle in (("decisions", "decisions.json записан не дословно"), ("refutations", "refutation.json записан не дословно")):
        o = run_node(cfg(swap=what), script)
        if "error" in o:
            return [o["error"]]
        f.check(len(scribe_calls(o)) == 1, f"{what}: писарь звался {len(scribe_calls(o))} раз, ожидалось ровно 1")
        f.check(o["result"]["decisions_path"] is None, f"{what}: подмена — путь к файлу не отдаём")
        f.check(any(needle in e for e in o["result"]["errors"]), f"{what}: errors: {o['result']['errors']}")
        f.check(any(needle in m for m in o["logs"]), f"{what}: расхождение не залогировано")
        f.check(not any(c["label"] == "отчёт" for c in o["calls"]), f"{what}: по подменённому файлу отчёт не строим")
    return f


def s_scribe_command(script=SCRIPT) -> list[str]:
    """Писарь не получает задачи печатать файлы: одна команда, содержимое — блоками между маркерами."""
    f = Fails()
    o = run_node(cfg(), script)
    if "error" in o:
        return [o["error"]]
    calls = scribe_calls(o)
    f.check(len(calls) == 1, f"писарь звался {len(calls)} раз, ожидался 1")
    if calls:
        prompt = calls[0]["prompt"]
        m = re.search(r'python3 "/s/zodchiy.py" scribe --nonce "([0-9a-f]{16})" --run-dir "/r/.zodchiy/run-1"', prompt)
        f.check(bool(m), "в промпте нет команды scribe с nonce и run-dir")
        if m:
            f.check(f"\nZODCHIY-SCRIBE-NONCE: {m.group(1)}\n" in prompt, "маркер nonce отдельной строкой")
        for name in ("refutation.json", "decisions.json", "findings.run.csv"):
            f.check(f"\n<<<ZODCHIY {name}\n" in prompt, f"нет блока {name}")
        f.check(prompt.count("\n>>>ZODCHIY") == 3, "блоки закрыты не по одному разу")
        f.check("ПОБАЙТНО" not in prompt and "def fnv" not in prompt, "писарь больше не копирует и не считает отпечаток сам")
    d = o["scribe"]["decisions"]
    f.check(next(x for x in d["findings"] if x["id"] == "F2")["lead"]["reason"].startswith('ё «r» 😀 "q" \\ \t'), "не-ASCII и экранирование дошли до файла без искажений")
    o2 = run_node(cfg(), script)
    f.check([c["prompt"] for c in scribe_calls(o2)] == [c["prompt"] for c in calls], "промпт писаря недетерминирован — resume не сможет кэшировать")
    return f


def s_scribe_lazy(script=SCRIPT) -> list[str]:
    """Писарь вернул «отпечатки», выдернутые из промпта, и не запускал команду: ворота обязаны отказать.
    Значит, в промпте нет ни ожидаемых отпечатков, ни чего-то, из чего они складываются без вычисления."""
    f = Fails()
    o = run_node(cfg(lazy_scribe=True), script)
    if "error" in o:
        return [o["error"]]
    f.check(o["result"]["decisions_path"] is None, f"ленивый писарь прошёл ворота: {o['result']}")
    f.check(any("записан не дословно" in e for e in o["result"]["errors"]), f"errors: {o['result']['errors']}")
    f.check(not any(c["label"] == "отчёт" for c in o["calls"]), "по неподтверждённой записи отчёт не строим")
    return f


def s_scribe_error(script=SCRIPT) -> list[str]:
    """Команда не нашла промпт: путь не отдаём, ошибка названа, отчёт не строим, повтора нет."""
    f = Fails()
    o = run_node(cfg(wrong_nonce=True), script)
    if "error" in o:
        return [o["error"]]
    f.check(len(scribe_calls(o)) == 1, f"писарь звался {len(scribe_calls(o))} раз, ожидался 1")
    f.check(o["result"]["decisions_path"] is None, "нет промпта — путь не отдаём")
    f.check(any("писарь:" in e and "не найден" in e for e in o["result"]["errors"]), f"errors: {o['result']['errors']}")
    f.check(not any(c["label"] == "отчёт" for c in o["calls"]), "отчёт не строим")
    # ошибки схемы — тоже без повтора
    o = run_node(cfg(schema_errors=["$.run: нет поля"]), script)
    f.check(len(scribe_calls(o)) == 1, f"ошибка схемы: писарь звался {len(scribe_calls(o))} раз, ожидался 1")
    return f


def s_payback(script=SCRIPT) -> list[str]:
    """L4 кодом на этапе лида: боль и порог из среза, remedy_files выбранного варианта, не-ok → без пробы."""
    f = Fails()

    def lead_of(o, fid):
        d = dec(o, fid)
        return d["lead"] if d else None

    # ok: 3 ÷ 45.75 = 0.0656 → 0.066; проба идёт, лид получил поля
    o = run_node(cfg(pain={"F1": 20, "F2": 45.75}, remedy_files={"F1": 1, "F2": 3}, judge={"F2": ["accepted"]}), script)
    l2 = lead_of(o, "F2")
    f.check(l2 and l2.get("remedy_files") == 3 and l2.get("payback") == 0.066 and l2.get("payback_verdict") == "ok", f"ok: поля лида {l2}")
    f.check(kinds(o, "F2")["judge"] == 1, "ok: проба должна идти")
    l1 = lead_of(o, "F1")
    f.check(l1 and l1.get("payback") == 0.05 and l1.get("payback_verdict") == "ok", f"quick-fix (лида нет) тоже считается кодом: {l1}")

    # too_costly: 5 ÷ 1 = 5 > 4
    o = run_node(cfg(pain={"F2": 1}, remedy_files={"F2": 5}), script)
    l2 = lead_of(o, "F2")
    f.check(l2 and l2.get("payback") == 5 and l2.get("payback_verdict") == "too_costly", f"too_costly: {l2}")
    k2 = kinds(o, "F2")
    f.check(k2["prepare"] == k2["executor"] == k2["judge"] == 0, f"too_costly: проба не идёт, а было {k2}")
    f.check("trial" not in dec(o, "F2"), "too_costly: trial быть не должно")
    f.check(l2 and "L4:" in l2["reason"] and "5" in l2["reason"].split("L4:")[-1], f"too_costly: причина L4 с арифметикой: {l2 and l2['reason']}")

    # порог берётся из среза, а не константой в сценарии: 5 ≤ 6
    o = run_node(cfg(pain={"F2": 1}, remedy_files={"F2": 5}, payback_max=6), script)
    f.check(lead_of(o, "F2").get("payback_verdict") == "ok" and kinds(o, "F2")["judge"] >= 1, "порог 6 из среза: 5 кв. проходит")

    # cold: боли нет (0 либо поля нет вовсе)
    for pain in (0, None):
        o = run_node(cfg(pain={"F2": pain}), script)
        l2 = lead_of(o, "F2")
        f.check(l2 and l2.get("payback") is None and l2.get("payback_verdict") == "cold", f"cold (pain={pain}): {l2}")
        f.check(kinds(o, "F2")["prepare"] == 0, f"cold (pain={pain}): проба не идёт")
        f.check("L4:" in (l2 or {}).get("reason", ""), f"cold (pain={pain}): причина L4")

    # лид назвал несуществующий вариант и сам вписал «ok»: чужие числа в decisions не попадают
    o = run_node(cfg(leads={"F2": {**F2_LEAD, "chosen": "v9", "payback": 0.001, "payback_verdict": "ok", "remedy_files": 1}}), script)
    l2 = lead_of(o, "F2")
    f.check(l2 and not {"payback", "payback_verdict", "remedy_files"} & set(l2), f"числа модели без выбранного варианта: {l2}")
    f.check(kinds(o, "F2")["prepare"] == 0, "неизвестный вариант — пробы нет")

    # quick-fix минует лида, но L4 к нему применяется
    o = run_node(cfg(pain={"F1": 0}), script)
    f.check(kinds(o, "F1")["lead"] == 0 and lead_of(o, "F1").get("payback_verdict") == "cold" and kinds(o, "F1")["prepare"] == 0, "quick-fix cold: без пробы")
    return f


def s_dropped_meta(script=SCRIPT) -> list[str]:
    """Ничего не срезается молча: отсечка аналитика и *_dropped среза доходят до decisions.json."""
    f = Fails()
    o = run_node(cfg(dropped={"F2": {"neighbors_dropped": 148, "hubs_dropped": 0}}), script)
    d = o["scribe"]["decisions"]
    f.check(d["run"].get("candidates") == {"total": 9, "taken": 3, "dropped": 6}, f"run.candidates: {d['run'].get('candidates')}")
    f.check(dec(o, "F2").get("slice_dropped") == {"neighbors_dropped": 148}, f"slice_dropped F2 (нули не пишем): {dec(o, 'F2').get('slice_dropped')}")
    f.check("slice_dropped" not in dec(o, "F1"), "у F1 срез ничего не терял — поля нет")
    return f


SCENARIOS = {
    "flow": s_flow,
    "accepted_first": s_accepted_first,
    "reserve_once": s_reserve_once,
    "reserve_phases": s_reserve_phases,
    "phase_of": s_phase_of,
    "run_dir_busy": s_run_dir_busy,
    "critics_remove": s_critics_remove,
    "dry": s_dry,
    "top": s_top,
    "failed_slice": s_failed_slice,
    "mute_critics": s_mute_critics,
    "scribe_verbatim": s_scribe_verbatim,
    "scribe_swap": s_scribe_swap,
    "scribe_command": s_scribe_command,
    "scribe_error": s_scribe_error,
    "scribe_lazy": s_scribe_lazy,
    "payback": s_payback,
    "dropped_meta": s_dropped_meta,
}

# (сценарий, что ломаем, на что заменяем): каждая отсечка инвертируется, проверка обязана покраснеть
MUTATIONS = {
    "критики снимают находку": ("critics_remove", "if (removed > 0) st.stop", "if (removed > 99) st.stop"),
    "quick-fix: один проектировщик": ("flow", "f.class === 'quick-fix' ? [QUICK_FIX_CHARACTER] : CHARACTERS", "CHARACTERS"),
    "quick-fix: без лида": ("flow", "    if (f.class === 'quick-fix') {\n      st.lead", "    if (false) {\n      st.lead"),
    "вопросы — не в пробу": ("flow", "if (lead.questions && lead.questions.length > 0) {", "if (false) {"),
    "architectural: только phases[0]": (
        "flow",
        "const first = lead.phases && lead.phases[0];",
        "const first = lead.phases && { title: lead.phases.map((p) => p.title).join('+'), steps: [] };",
    ),
    "запасной только при rejected": ("accepted_first", "attempt.judged.status === 'rejected' && reserve", "reserve"),
    "запасной — один раз": ("reserve_once", "if (second) attempt = second;", "if (second) attempt = second;\n      if (second && second.judged.status === 'rejected') await tryVariant(f, reserve, phaseNote, true);"),
    "запасной architectural: фазы исполнителя": ("reserve_phases", "if (second && f.class === 'architectural') {", "if (false) {"),
    "architectural: judge знает фазу": ("phase_of", "${phaseOf ? ` --phase-of ${phaseOf}` : ''}", "${''}"),
    "каталог прогона: непустой — отказ": ("run_dir_busy", "measured.run_dir_free !== true", "false"),
    "dry: стоп после лида": ("dry", "    if (dry) {", "    if (false) {"),
    "top-N": ("top", "ranked.slice(0, top)", "ranked.slice(0, top + 1)"),
    "top-N: число в лог": ("top", "отброшено ${dropped}", "отброшено ${0}"),
    "order по depends_on": ("flow", "if (deps.length && Math.max(...deps) > at) {", "if (false) {"),
    "писарь: отпечаток decisions": ("scribe_swap", "scribed.decisions_digest !== expectedDecisions", "false"),
    "писарь: отпечаток refutation": ("scribe_swap", "scribed.refutation_digest !== expectedRefutations", "false"),
    "писарь: nonce раскрывает отпечатки": (
        "scribe_lazy",
        "const nonce = fingerprint({ salt: 'zodchiy-scribe-nonce', decisions, refutations, csvIds });",
        "const nonce = `${expectedDecisions}-${expectedRefutations}`;",
    ),
    "писарь: ошибка команды": ("scribe_error", "} else if (scribed.error) {", "} else if (false) {"),
    "писарь: ошибки схемы": ("scribe_verbatim", "for (const e of scribed.schema_errors) errors.push", "for (const e of []) errors.push"),
    "сбой находки не идёт дальше": ("failed_slice", "if (st.failed || st.stop) return st;", "if (st.stop) return st;"),
    "L4: не в пробу при не-ok": ("payback", "if (lead.payback_verdict !== 'ok') {", "if (false) {"),
    "L4: порог из среза": ("payback", "ratio <= paybackMax ?", "ratio <= 4 ?"),
    "L4: боль ≤ 0 — cold": ("payback", "const ratio = pain > 0 ?", "const ratio = pain >= 0 ?"),
    "L4: чужие числа лида вычищаются": ("payback", "'payback_verdict']) delete st.lead[k];", "'payback_verdict']) void st.lead[k];"),
    "L4: считается и для quick-fix": ("payback", "      applyPayback(st);\n      return st;\n    }\n    const siblings", "      return st;\n    }\n    const siblings"),
    "снятая критиком — в run.skipped": ("critics_remove", ".filter((st) => st.failed || st.stop || st.critics.length < MIN_CRITICS)", ".filter((st) => st.failed || st.critics.length < MIN_CRITICS)"),
    "отсечка аналитика в decisions": ("dropped_meta", "candidates: {", "candidates_off: {"),
    "срез: *_dropped в decisions": ("dropped_meta", "if (Object.keys(st.sliceDropped).length > 0) out.slice_dropped", "if (false) out.slice_dropped"),
    "CSV прогона — только обработанные": ("top", "const csvIds = done.map((st) => st.f.id);", "const csvIds = analysis.findings.map((x) => x.id);"),
}


@unittest.skipUnless(shutil.which("node"), "нет node")
class WorkflowFlow(unittest.TestCase):
    def test_scenarios(self):
        for name, fn in SCENARIOS.items():
            with self.subTest(name):
                self.assertEqual(list(fn()), [])

    def test_mutations_turn_scenarios_red(self):
        with open(SCRIPT, encoding="utf-8") as fh:
            text = fh.read()
        for name, (scenario, old, new) in MUTATIONS.items():
            with self.subTest(name):
                self.assertEqual(text.count(old), 1, f"фрагмент для мутации не найден ровно раз: {old!r}")
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, "mutant.js")
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(text.replace(old, new))
                    try:
                        red = SCENARIOS[scenario](path)
                    except AssertionError:
                        red = ["упал сам прогон"]
                self.assertTrue(red, f"мутация «{name}» не покраснила сценарий {scenario}: проверка её не ловит")


if __name__ == "__main__":
    unittest.main()
