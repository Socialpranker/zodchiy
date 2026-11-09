export const meta = {
  name: 'zodchiy-refactor',
  description:
    'Режим refactor зодчего: замер, аналитик, критики L1–L3, проектировщики с характерами, лид с окупаемостью L4, проба в git worktree, план и отчёт',
  phases: [
    { title: 'Замер', detail: 'measure.json — скриптом, моделью только запуск' },
    { title: 'Анализ', detail: 'карта, findings.csv, топ-N находок' },
    { title: 'Находки', detail: 'по каждой находке независимо: срез, критики, проектировщики, лид, проба' },
    { title: 'Отчёт', detail: 'decisions.json, report.html, report.md, plan.md' },
  ],
};

// Workflow-сценарий режима `refactor` (спека zodchiy-refactor-mode §1, §2, §5).
// Ни файлов, ни shell у сценария нет: детерминированные шаги (замер, срез, trial,
// отчёт) гоняют дешёвые агенты одной командой и возвращают её JSON по `schema`.
// Отсечки и приёмку решает КОД сценария по возвращённым полям, не модель.
//
// args: { repo, top=1, dry=false, semgrep=false, lang='ru'|'en', skill, run_dir, date }

// Модели — явными ID (спека §5.10); эффорт — по таблице §1. Одна роль — одна строка.
const ROLES = {
  measurer: { model: 'claude-haiku-5-5', effort: 'low' },
  slicer: { model: 'claude-haiku-5-5', effort: 'low' },
  analyst: { model: 'claude-opus-5-5', effort: 'medium' },
  criticL1: { model: 'claude-opus-5-5', effort: 'high' },
  criticL2: { model: 'claude-opus-5-5', effort: 'high' },
  criticL3: { model: 'claude-opus-5-5', effort: 'medium' },
  designer: { model: 'claude-opus-5-5', effort: 'medium' },
  lead: { model: 'claude-opus-5-5', effort: 'high' },
  executor: { agentType: 'sonnet-implementer' },
  trial: { model: 'claude-haiku-5-5', effort: 'low' },
  scribe: { model: 'claude-haiku-5-5', effort: 'low' },
  reporter: { model: 'claude-haiku-5-5', effort: 'low' },
};

const A = args || {};
const repo = A.repo;
const skill = A.skill;
const runDir = A.run_dir;
const date = A.date;
const lang = A.lang === 'en' ? 'en' : 'ru';
const dry = A.dry === true;
const semgrep = A.semgrep === true;
const topArg = Number(A.top);
const top = Number.isInteger(topArg) && topArg > 0 ? topArg : 1;
if (!repo || !skill || !runDir || !date) {
  throw new Error('refactor.js: нужны args.repo, args.skill, args.run_dir, args.date');
}

const CLI = `python3 "${skill}/zodchiy.py"`;
const P = {
  measure: `${runDir}/measure.json`,
  findings: `${runDir}/findings.csv`,
  findingsRun: `${runDir}/findings.run.csv`,
  findingsJson: `${runDir}/findings.json`,
  refutation: `${runDir}/refutation.json`,
  map: `${runDir}/map.md`,
  decisions: `${runDir}/decisions.json`,
  html: `${runDir}/report.html`,
  md: `${runDir}/report.md`,
  plan: `${runDir}/plan.md`,
  slices: `${runDir}/slices`,
};
const basename = (p) => p.slice(p.lastIndexOf('/') + 1);
const REF = {
  skillMd: `${skill}/SKILL.md`,
  refutation: `${skill}/references/refutation.md`,
  remedy: `${skill}/references/remedy.md`,
  schema: `${skill}/schema/decisions.schema.json`,
};

const CHARACTERS = [
  'удали, а не абстрагируй',
  'минимальный интерфейс',
  'под реального частого вызывающего',
];
const QUICK_FIX_CHARACTER = CHARACTERS[0];

const COMMON =
  'Все пути в командах бери в двойные кавычки (в них бывают пробелы и кириллица), ' +
  '`~` в пути раскрой сам. Ты не один в среде: чужие правки не откатывай, субагентов не запускай. ' +
  'Верни только то, что просит schema.';

let agents = 0;
// Единственная точка вызова агента: считает агентов и подставляет модель/effort роли.
async function ask(role, label, prompt, schema) {
  agents += 1;
  return agent(prompt, { label, schema, ...ROLES[role] });
}

const str = { type: 'string' };
const int = { type: 'integer' };
const strs = { type: 'array', items: str };

// ── Замер ───────────────────────────────────────────────────────────────────
phase('Замер');

const measured = await ask(
  'measurer',
  'замер',
  `Выполни ровно эти команды и ничего больше.
1) if [ -n "$(ls -A "${runDir}" 2>/dev/null)" ]; then echo BUSY; else mkdir -p "${runDir}" "${P.slices}" && echo FREE; fi
   Если вывод BUSY — остальные шаги не выполняй: верни ok: false, run_dir_free: false.
2) ${CLI} measure --out "${P.measure}" "${repo}"
   (замер может идти до 15 минут: Bash с timeout 600000, при необходимости запусти в фоне и дождись конца)
3) git -C "${repo}" rev-parse HEAD
Верни: run_dir_free — true, если шаг 1 напечатал FREE; ok — файл measure.json создан и разбирается как JSON; head — вывод шага 3;
dirty — true, если snapshot.worktree в measure.json равно "dirty" (git status сам не запускай:
каталог прогона .zodchiy/ замер уже исключил); tools — одной строкой, какие из git, tree-sitter, ruff, semgrep
есть по полю tools в measure.json; ceiling — confidence.ceiling из measure.json.
${COMMON}`,
  {
    type: 'object',
    properties: { ok: { type: 'boolean' }, run_dir_free: { type: 'boolean' }, head: str, dirty: { type: 'boolean' }, tools: str, ceiling: str },
    required: ['ok', 'run_dir_free', 'head', 'dirty'],
  },
);
if (measured && measured.run_dir_free !== true) {
  throw new Error(`run_dir не пуст: ${runDir} — прошлый прогон (в тот же день) не сотрётся сам; удали каталог или задай другой run_dir`);
}
if (!measured || !measured.ok) throw new Error('замер не удался: measure.json не создан');

// ── Анализ ──────────────────────────────────────────────────────────────────
phase('Анализ');

const findingRow = {
  type: 'object',
  properties: {
    id: str,
    title: str,
    class: { enum: ['quick-fix', 'scheduled', 'architectural'] },
    slug: str,
    priority: int,
    gain_metric: str,
    gain_target: str,
    files: strs,
  },
  required: ['id', 'title', 'class', 'slug', 'priority', 'files'],
};

const analysis = await ask(
  'analyst',
  'аналитик',
  `Ты аналитик режима refactor. Репозиторий: "${repo}". Замер: "${P.measure}" (300–500 КБ — целиком не читай,
выбирай нужное через python3/jq/grep). Читай по бюджету и порядку шагов из "${REF.skillMd}".
Мёртвый код и «похоже на дубль» без подтверждения второй осью — только status=hypothesis.
${semgrep ? `args.semgrep=true: если semgrep есть (tools: ${measured.tools || 'неизвестно'}), можешь прогнать его по файлам находок; результат — только в карту и с потолком hypothesis.` : ''}
1) Составь карту репозитория и запиши в "${P.map}" (слои, хабы, где боль; по-${lang === 'en' ? 'английски' : 'русски'}).
2) Отбери кандидатов в находки, упорядочи по приоритету (Pain × Spread) по убыванию.
3) Классифицируй лечение каждой по "${REF.remedy}" §3: quick-fix | scheduled | architectural.
4) В "${P.findings}" запиши РОВНО ${top} лучших находок (не больше!) командой
   ${CLI} add --findings "${P.findings}" --json '<строка со всеми обязательными полями>'
   id вида F1, F2…; gain_metric — ключ снимка measure.json (не manual), gain_target — число;
   source — пути в measure.json и [file=путь] для каждого затронутого файла.
   confidence ∈ hypothesis | finding | verdict и не выше потолка confidence.ceiling из measure.json (${measured.ceiling || 'см. measure.json'}); другие слова (high, medium) add отклонит.
Верни: total_candidates — сколько кандидатов нашёл всего (до отсечки), map_path, findings — те ${top}, что записал
(files — пути затронутых файлов относительно корня репо; slug — kebab-case латиницей, ≤ 30 символов).
${COMMON}`,
  {
    type: 'object',
    properties: { total_candidates: int, map_path: str, findings: { type: 'array', items: findingRow } },
    required: ['total_candidates', 'findings'],
  },
);
if (!analysis || !analysis.findings || analysis.findings.length === 0) {
  throw new Error('аналитик не вернул находок');
}

const ranked = [...analysis.findings].sort((a, b) => b.priority - a.priority);
const picked = ranked.slice(0, top);
const totalCandidates = Math.max(analysis.total_candidates, ranked.length);
const dropped = totalCandidates - picked.length;
log(`top ${top}: взято ${picked.length} находок, отброшено ${dropped} (кандидатов ${analysis.total_candidates})`);

// ── Находки ─────────────────────────────────────────────────────────────────
phase('Находки');

const LENSES = [
  {
    role: 'criticL1',
    key: 'L1',
    lens: 'L1 это норма',
    ask: 'место такое по замыслу? barrel, DI-корень, реестр, точка расширения — высокие числа там нормальны',
    reads: 'structure.barrels, hubs (срез) и карта',
  },
  {
    role: 'criticL2',
    key: 'L2',
    lens: 'L2 метрика меряет не то',
    ask: 'число говорит о том, что написано в находке? метрика заблокирована, разбор регулярками, порог на файл вместо функции, доля на малой выборке',
    reads: 'blocked_metrics, thresholds, ceiling (срез)',
  },
  {
    role: 'criticL3',
    key: 'L3',
    lens: 'L3 цена завышена',
    ask: 'боль настоящая или посчитана? churn от роста, а не от боли; правки перестали идти; окно захватило разовую миграцию',
    reads: 'pain, hotspots, unstable_files (срез — арифметика по кварталам уже посчитана)',
  },
];

const verdictSchema = {
  type: 'object',
  properties: {
    verdict: { enum: ['survives', 'demoted', 'dropped'] },
    reason: str,
  },
  required: ['verdict', 'reason'],
};

const variantSchema = {
  type: 'object',
  properties: {
    title: str,
    summary: str,
    files: strs,
    remedy_files: int,
    forecast_metric: str,
    forecast_after: { type: 'number' },
    deletion_test: str,
    adapters_gate: str,
    risk: str,
  },
  required: ['title', 'summary', 'files', 'remedy_files', 'forecast_metric', 'forecast_after', 'deletion_test'],
};

const leadSchema = {
  type: 'object',
  properties: {
    chosen: str,
    reserve: str,
    reason: str,
    questions: strs,
    stage: { enum: ['decouple', 'simplify'] },
    risk: { enum: ['low', 'medium', 'high'] },
    depends_on: strs,
    phases: {
      type: 'array',
      items: {
        type: 'object',
        properties: { title: str, steps: strs, risk: str },
        required: ['title'],
      },
    },
    // L4: перезаписывает код сразу после ответа лида (applyPayback), что бы модель сюда ни положила
    remedy_files: int,
    payback: {},
    payback_verdict: { enum: ['ok', 'too_costly', 'cold'] },
  },
  required: ['chosen', 'reason', 'questions', 'stage', 'risk'],
};

const prepareSchema = {
  type: 'object',
  properties: {
    worktree: str,
    branch: str,
    branch_requested: str,
    branch_collision: { type: 'boolean' },
    base_head: str,
    base_dirty: { type: 'boolean' },
    measure_before: {},
  },
  required: ['worktree', 'branch', 'base_head', 'base_dirty'],
};

// Поля `trial judge` — как в schema/decisions.schema.json#/$defs/trial; nullable — без type.
const judgeSchema = {
  type: 'object',
  properties: {
    variant: {},
    checks: {
      type: 'array',
      items: {
        type: 'object',
        properties: { name: str, status: { enum: ['pass', 'fail', 'unverified'] }, reason: str, tail: str },
        required: ['name', 'status'],
      },
    },
    gain_metric: {},
    gain_before: {},
    gain_after: {},
    gain_target: {},
    gain_hit: { type: 'boolean' },
    gain_reason: {},
    before_source: {},
    remedy_files: int,
    payback: {},
    payback_verdict: { enum: ['ok', 'too_costly', 'cold'] },
    status: { enum: ['accepted', 'rejected', 'unverified'] },
    reasons: strs,
  },
  required: ['checks', 'status', 'reasons', 'remedy_files'],
};

const designerPrompt = (f, character, slicePath) =>
  `Ты проектировщик режима refactor. Характер: «${character}».
Находка ${f.id} «${f.title}» (класс ${f.class}). Срез замера под неё: "${slicePath}" (JSON ≤ 8 КБ), карта: "${P.map}".
Читай ТОЛЬКО затронутые файлы из среза (репо "${repo}") — не сканируй остальное дерево.
Правила: "${REF.remedy}" §2 (Design It Twice; гибкость ради гибкости не проектируй — она противоречит метрике простоты)
и §4 (deletion test и правило двух адаптеров — гейты ПЕРЕД новой абстракцией; не проходит гейт — предлагай удаление/упрощение).
Предложи один вариант лечения. Метрика прогноза — gain_metric находки из среза (finding.gain_metric); forecast_after — ожидаемое
значение после правки; remedy_files — сколько файлов будет в диффе. Правок в репо не делай.
${COMMON}`;

const criticPrompt = (f, L, slicePath) =>
  `Ты критик режима refactor, линза ${L.lens}. Вопрос линзы: ${L.ask}.
Находка ${f.id} «${f.title}». Вход: срез "${slicePath}" (${L.reads}) и карта "${P.map}". Инструкция линз — "${REF.refutation}".
Возражение — факт из среза, а не мнение. verdict: survives — находка стоит; demoted — понижается; dropped — снята.
Репо не правь, вердикт командой refute сам не записывай — его запишет сценарий.
${COMMON}`;

const leadPrompt = (f, variants, slicePath, siblings) =>
  `Ты лид режима refactor. Находка ${f.id} «${f.title}», класс ${f.class}. Срез: "${slicePath}", карта: "${P.map}".
Варианты проектировщиков (JSON):
${JSON.stringify(variants)}
Остальные находки прогона: ${JSON.stringify(siblings)}
Судишь по прогнозу чисел и окупаемости (линза L4), не по числу голосов. Гейты "${REF.remedy}" §4 обязательны.
L4 — валюта затронутые файлы: боль = slice.pain (эпизоды фиксов × медиана файлов, файлов/квартал);
лечение = remedy_files варианта; окупаемость = лечение ÷ боль, в кварталах; порог slice.payback_max_quarters;
боль 0 → «не чинить»; выше порога → «знаем, не чиним». Арифметику покажи в reason.
Верни chosen и reserve (id вариантов v1…), reason, stage (decouple | simplify по "${REF.remedy}" §5: сначала расцепление, потом упрощение),
risk (низкий риск — мелкий PR — идёт первым), depends_on (id других находок, которые должны быть приняты раньше).
questions — то, что ты решить не вправе (например, «забытый экран или законный адрес?»); фаза с открытым вопросом не идёт в пробу.
${f.class === 'architectural' ? 'Класс architectural: разрежь лечение выбранного варианта на phases (каждая фаза — один PR); в пробу пойдёт только первая, она должна быть самодостаточной.' : 'Класс не architectural: phases не заполняй.'}
${COMMON}`;

const shellNote =
  'Команда может идти до 15 минут: Bash с timeout 600000; если не укладывается — запусти в фоне и дождись вывода.';

// Одна проба: prepare → исполнитель → judge → cleanup. Приёмка — по полям judge, кодом.
// phaseOf — число фаз находки (или '?'), если проба покрывает только первую: цель находки на неё не распространяется
async function tryVariant(f, variant, phaseNote, isReserve, phaseAsk = '', phaseOf = null) {
  const prep = await ask(
    'trial',
    `${f.id}: prepare ${variant.id}`,
    `Выполни ровно одну команду и верни её JSON:
${CLI} trial prepare --repo "${repo}" --id ${f.id} --slug ${f.slug}
(замер «до» внутри может идти до 15 минут: Bash с timeout 600000, при необходимости в фоне.)
measure_before — путь из вывода или null.
${COMMON}`,
    prepareSchema,
  );
  if (!prep) return null;

  const executed = await ask(
    'executor',
    `${f.id}: ${variant.id}${isReserve ? ' (запасной)' : ''}`,
    `Реализуй вариант ${variant.id} находки ${f.id} «${f.title}» в git worktree "${prep.worktree}" (ветка ${prep.branch}).
Работай ТОЛЬКО внутри "${prep.worktree}": ничего не меняй вне этого каталога, в основном репо "${repo}" тем более.
Коммитить не нужно (коммит сделает cleanup), мержить, пушить и переключать ветки — нельзя.
Реализуй ровно выбранный вариант, без соседнего рефакторинга. Затронутые файлы по замыслу: ${(variant.files || []).join(', ') || 'см. описание'}.
Вариант (JSON): ${JSON.stringify(variant)}
${phaseNote}
Перенося код, проверь, что не осталось неразрешённых имён: если есть python — \`ruff check --select F821 .\` внутри worktree.
Верни: done — true, если правка сделана; files — изменённые файлы; notes — что не удалось.${phaseAsk}
${COMMON}`,
    {
      type: 'object',
      properties: {
        done: { type: 'boolean' },
        files: strs,
        notes: str,
        phase: { type: 'object', properties: { title: str, steps: strs, risk: str }, required: ['title'] },
      },
      required: ['done'],
    },
  );
  // приёмка не по слову исполнителя (даже если он не вернулся), а по judge

  const judged = await ask(
    'trial',
    `${f.id}: judge ${variant.id}`,
    `Выполни ровно одну команду и верни её JSON как есть:
${CLI} trial judge --repo "${repo}" --worktree "${prep.worktree}" --findings "${P.findings}" --id ${f.id} --variant ${variant.id} --base-head ${prep.base_head}${phaseOf ? ` --phase-of ${phaseOf}` : ''}${prep.measure_before ? ` --measure-before "${prep.measure_before}"` : ''} --timeout 900
${shellNote}
Ничего не исправляй и не перезапускай проверки сам: статус решает скрипт.
${COMMON}`,
    judgeSchema,
  );

  const status = judged ? judged.status : 'unverified';
  await ask(
    'trial',
    `${f.id}: cleanup ${variant.id}`,
    `Выполни ровно одну команду и верни её JSON:
${CLI} trial cleanup --repo "${repo}" --worktree "${prep.worktree}"${status === 'accepted' ? ' --keep-branch' : ''}
${COMMON}`,
    { type: 'object', properties: { cleaned: str, branch_kept: { type: 'boolean' } }, required: ['cleaned'] },
  );
  return { prep, phase: executed && executed.phase, judged: judged || { checks: [], status: 'unverified', reasons: ['judge не вернул результата'], remedy_files: 0 } };
}

// L4 кодом (SKILL.md, «Окупаемость»): лечение выбранного варианта ÷ боль среза, в кварталах.
// Боль и порог — из slices/<id>.json (st.pain, st.paybackMax), не константа сценария и не пересказ модели.
// Выбранного варианта нет — судить нечем (это ловит этап пробы), а числа, которые лид вписал сам, убираем:
// в decisions.json окупаемость приходит только из кода.
function applyPayback(st) {
  const chosen = st.variants.find((v) => v.id === st.lead.chosen);
  if (!chosen) {
    for (const k of ['remedy_files', 'pain', 'payback', 'payback_max_quarters', 'payback_verdict']) delete st.lead[k];
    return;
  }
  const { pain, paybackMax } = st;
  const ratio = pain > 0 ? chosen.remedy_files / pain : null;
  st.lead.remedy_files = chosen.remedy_files;
  st.lead.pain = pain;
  st.lead.payback_max_quarters = paybackMax;
  st.lead.payback = ratio === null ? null : Math.round(ratio * 1000) / 1000;
  st.lead.payback_verdict = ratio === null ? 'cold' : ratio <= paybackMax ? 'ok' : 'too_costly';
}

// Причина отсечки по L4 — с арифметикой, чтобы её можно было проверить глазами.
function paybackReason(lead) {
  const pain = lead.pain === null ? 'нет данных' : lead.pain;
  if (lead.payback_verdict === 'cold') return `L4: боли нет (${pain} файлов/кв) — не чинить`;
  return `L4: ${lead.remedy_files} файлов лечения ÷ боль ${pain} файлов/кв = ${lead.payback} кв > порога ${lead.payback_max_quarters} — знаем, не чиним`;
}

// Состояние каждой находки живёт вне pipeline: сбой этапа не должен унести находку
// вместе с её вердиктами критиков — отчёт строится по тому, что успело случиться.
const states = new Map();

function fail(st, e) {
  st.failed = e.message;
  log(`${st.f.id}: сбой этапа — ${st.failed}; находка пропущена, остальные идут дальше`);
}

// Этап, который пропускает находку, снятую критиками или уже упавшую, и превращает throw в `failed`.
const guard = (fn) => async (st) => {
  if (st.failed || st.stop) return st;
  try {
    return await fn(st);
  } catch (e) {
    fail(st, e);
    return st;
  }
};

const MIN_CRITICS = 2; // selfcheck: вердикт уровня verdict требует ≥2 разных линз

await pipeline(
  picked,

  // 1. срез замера под находку (≤ 8 КБ) — агентам целый measure.json не отдаём
  async (f) => {
    const slicePath = `${P.slices}/${f.id}.json`;
    const st = { f, slicePath, critics: [], variants: [], stop: null, failed: null, pain: null, paybackMax: null, sliceDropped: {} };
    states.set(f.id, st);
    try {
      const s = await ask(
        'slicer',
        `${f.id}: срез`,
        `Выполни ровно две команды:
1) ${CLI} slice --id ${f.id} --measure "${P.measure}" --findings "${P.findings}" --repo "${repo}" --out "${slicePath}"
2) python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(json.dumps({"pain":(d.get("pain") or {}).get("pain_files_per_quarter"),"payback_max_quarters":d.get("payback_max_quarters"),"dropped":{k:v for k,v in d.items() if k.endswith("_dropped") and v}}))' "${slicePath}"
Затем измерь размер файла среза в байтах (wc -c).
Верни path, bytes и из вывода команды 2 символ в символ: pain (поле не возвращай, если там null), payback_max_quarters, dropped (объект как есть, пустой {} если пусто). Числа не считай и не округляй.
${COMMON}`,
        {
          type: 'object',
          properties: { path: str, bytes: int, pain: { type: 'number' }, payback_max_quarters: { type: 'number' }, dropped: { type: 'object' } },
          required: ['path', 'bytes', 'payback_max_quarters'],
        },
      );
      if (!s || s.bytes > 8192 || !(s.payback_max_quarters > 0)) {
        throw new Error('срез не получен, больше 8192 байт или без порога окупаемости');
      }
      st.pain = Number.isFinite(s.pain) ? s.pain : null;
      st.paybackMax = s.payback_max_quarters;
      // срез режется под 8 КБ; сколько отброшено, доходит до отчёта (ненулевое, целое)
      st.sliceDropped = Object.fromEntries(Object.entries(s.dropped || {}).filter(([, n]) => Number.isInteger(n) && n > 0));
    } catch (e) {
      fail(st, e);
    }
    return st;
  },

  // 2. критики L1–L3 параллельно; снята → проектировщиков не зовём
  guard(async (st) => {
    const { f, slicePath } = st;
    const verdicts = await parallel(
      LENSES.map((L) => () => ask(L.role, `${f.id}: ${L.key}`, criticPrompt(f, L, slicePath), verdictSchema)),
    );
    st.critics = verdicts
      .map((v, i) => (v ? { finding_id: f.id, lens: LENSES[i].lens, verdict: v.verdict, reason: v.reason, mode: 'parallel' } : null))
      .filter(Boolean);
    const total = st.critics.length;
    if (total < MIN_CRITICS) throw new Error(`критики ответили ${total} из ${LENSES.length}, нужно не меньше ${MIN_CRITICS}`);
    const removed = st.critics.filter((c) => c.verdict === 'dropped').length;
    log(`${f.id}: критики сняли ${removed} из ${total} возражений`);
    // причина уходит в run.skipped: без неё отчёт напишет «нет решения лида» вместо «снята критиком»
    const droppedBy = LENSES.filter((L, i) => verdicts[i] && verdicts[i].verdict === 'dropped').map((L) => L.key);
    if (removed > 0) st.stop = `снята критиком: ${droppedBy.join(', ')}`;
    return st;
  }),

  // 3. проектировщики: quick-fix — один («удали, а не абстрагируй»), иначе три характера
  guard(async (st) => {
    const { f, slicePath } = st;
    const characters = f.class === 'quick-fix' ? [QUICK_FIX_CHARACTER] : CHARACTERS;
    const made = await parallel(
      characters.map((ch, i) => () =>
        ask('designer', `${f.id}: ${ch}`, designerPrompt(f, ch, slicePath), variantSchema).then(
          (v) => (v ? { id: `v${i + 1}`, character: ch, ...v } : null),
        ),
      ),
    );
    st.variants = made.filter(Boolean);
    if (st.variants.length === 0) throw new Error('проектировщики не вернули вариантов');
    return st;
  }),

  // 4. лид (не для quick-fix); окупаемость L4 кодом по прогнозу варианта — и для quick-fix, у которого лида нет
  //    (по факту диффа её потом судит trial)
  guard(async (st) => {
    const { f, slicePath } = st;
    if (f.class === 'quick-fix') {
      st.lead = {
        chosen: st.variants[0].id,
        reason: 'quick-fix: один проектировщик, лид не нужен; окупаемость L4 считает код по прогнозу варианта, trial — по факту диффа',
        questions: [],
        stage: 'simplify',
        risk: 'low',
      };
      applyPayback(st);
      return st;
    }
    const siblings = picked.filter((g) => g.id !== f.id).map((g) => ({ id: g.id, title: g.title, files: g.files }));
    const lead = await ask('lead', `${f.id}: лид`, leadPrompt(f, st.variants, slicePath, siblings), leadSchema);
    if (!lead) throw new Error('лид не ответил');
    st.lead = lead;
    applyPayback(st);
    return st;
  }),

  // 5. проба: L4 не ok → без пробы; dry → стоп; вопросы владельцу → без пробы; architectural → только phases[0]
  guard(async (st) => {
    const { f, lead, variants } = st;
    const chosen = variants.find((v) => v.id === lead.chosen);
    const reserve = lead.reserve && lead.reserve !== lead.chosen ? variants.find((v) => v.id === lead.reserve) : null;
    if (!chosen) {
      st.skipped = `лид назвал неизвестный вариант ${lead.chosen}`;
      log(`${f.id}: ${st.skipped} — проба пропущена`);
      return st;
    }
    if (lead.payback_verdict !== 'ok') {
      st.skipped = paybackReason(lead);
      log(`${f.id}: ${st.skipped} — проба не запускается`);
      return st;
    }
    if (dry) {
      st.skipped = '--dry: остановка после лида';
      log(`${f.id}: --dry — проба не запускается`);
      return st;
    }
    if (lead.questions && lead.questions.length > 0) {
      st.skipped = `открытые вопросы владельцу: ${lead.questions.length}`;
      log(`${f.id}: у лида ${lead.questions.length} вопросов владельцу — проба не запускается`);
      return st;
    }

    let phaseNote = 'Класс не architectural: реализуй вариант целиком.';
    if (f.class === 'architectural') {
      const first = lead.phases && lead.phases[0];
      if (!first) {
        st.skipped = 'architectural без фаз от лида';
        log(`${f.id}: architectural без фаз — проба не запускается`);
        return st;
      }
      phaseNote = `Класс architectural: реализуй ТОЛЬКО первую фазу «${first.title}»: ${(first.steps || []).join('; ')}. Остальные фазы не трогай.`;
    }

    let attempt = await tryVariant(f, chosen, phaseNote, false, '', f.class === 'architectural' ? lead.phases.length : null);
    let reasonsPrefix = [];
    if (attempt && attempt.judged.status === 'rejected' && reserve) {
      reasonsPrefix = [`основной вариант ${chosen.id} отклонён: ${attempt.judged.reasons.join('; ')}`];
      log(`${f.id}: ${chosen.id} отклонён — одна попытка запасным ${reserve.id}`);
      const second = await tryVariant(
        f,
        reserve,
        f.class === 'architectural' ? 'Класс architectural: реализуй ТОЛЬКО первую самодостаточную фазу варианта, остальное не трогай.' : phaseNote,
        true,
        f.class === 'architectural' ? '\nphase — фаза, которую ты выполнил: title, steps (что сделано), risk.' : '',
        f.class === 'architectural' ? '?' : null,
      );
      if (second) attempt = second;
      // фазы лида резали основной вариант, в ветке лежит запасной: план описывает то, что выполнено
      if (second && f.class === 'architectural') {
        st.lead = {
          ...lead,
          phases: second.phase && second.phase.title ? [second.phase] : [],
          reason: `${lead.reason} [запасной ${reserve.id}: фазы основного варианта не применимы; в плане только фаза, которую выполнил исполнитель, остальные не нарезаны]`,
        };
      }
    }
    if (!attempt) {
      st.skipped = 'trial prepare не удался';
      return st;
    }

    const { prep, judged } = attempt;
    const usedVariant = variants.find((v) => v.id === judged.variant) || chosen;
    st.trial = {
      ...judged,
      reasons: [...reasonsPrefix, ...(judged.reasons || [])],
      branch_requested: prep.branch_requested || prep.branch,
      branch_collision: prep.branch_collision === true,
    };
    if (judged.status === 'accepted') st.trial.branch = prep.branch; // ветки остальных cleanup удалил
    st.baseHead = prep.base_head;
    st.baseDirty = prep.base_dirty === true;
    if (judged.gain_reason == null && judged.gain_metric != null) {
      log(
        `${f.id}: прогноз ${judged.gain_metric} ${judged.gain_before}→${usedVariant.forecast_after}, факт ${judged.gain_after} ${judged.gain_hit ? '✓' : '✗'}`,
      );
    }
    return st;
  }),
);

// ── Отчёт ───────────────────────────────────────────────────────────────────
phase('Отчёт');

const all = picked.map((f) => states.get(f.id));
// В отчёт идёт то, что дошло хотя бы до вердиктов критиков (для них есть refutation-записи).
// Остальное — «пропущено с причиной»: иначе selfcheck краснеет на находке приоритета ≥6 без опровержения.
// Снятая критиками тоже в run.skipped (её вердикты при этом остаются в refutation.json): без записи
// отчёт не знает, почему у находки нет решения лида.
const done = all.filter((st) => st.critics.length >= MIN_CRITICS);
const skippedRun = all
  .filter((st) => st.failed || st.stop || st.critics.length < MIN_CRITICS)
  .map((st) => ({ id: st.f.id, reason: st.failed || st.stop || 'нет вердиктов критиков' }));
if (skippedRun.length > 0) {
  log(`${skippedRun.length} из ${picked.length} находок пропущены до отчёта: ${skippedRun.map((x) => `${x.id} (${x.reason})`).join(', ')}`);
}

// Порядок между находками (спека §5.3): расцепление раньше упрощения, потом риск, потом приоритет.
const survivors = done.filter((st) => st.lead);
const rank = (st) => [st.lead.stage === 'decouple' ? 0 : 1, { low: 0, medium: 1, high: 2 }[st.lead.risk] ?? 1, -(st.f.priority || 0)];
const ordered = [...survivors].sort((a, b) => {
  const ra = rank(a);
  const rb = rank(b);
  return ra[0] - rb[0] || ra[1] - rb[1] || ra[2] - rb[2];
});
// зависимый не раньше того, от кого зависит
for (let pass = 0; pass < ordered.length; pass += 1) {
  let moved = false;
  for (const st of ordered) {
    const deps = (st.lead.depends_on || []).map((id) => ordered.findIndex((o) => o.f.id === id)).filter((i) => i >= 0);
    const at = ordered.indexOf(st);
    if (deps.length && Math.max(...deps) > at) {
      ordered.splice(at, 1);
      ordered.splice(Math.max(...deps), 0, st);
      moved = true;
      break;
    }
  }
  if (!moved) break;
}

const trials = survivors.filter((st) => st.trial);
const decisions = {
  run: {
    head: (trials[0] && trials[0].baseHead) || measured.head,
    base_dirty: trials.some((st) => st.baseDirty) || measured.dirty === true,
    mode: 'workflow',
    date,
    skipped: skippedRun,
    // отсечка аналитика (top) — отчёт печатает «кандидатов N, взято K, отброшено N−K»
    candidates: { total: totalCandidates, taken: picked.length, dropped },
    // scribe и reporter — ещё два вызова агентов после сборки этого объекта
    cost: { agents: agents + 2 },
  },
  findings: ordered.map((st, order) => {
    const lead = { ...st.lead, order };
    const why = st.skipped || st.failed;
    if (why) lead.reason = `${lead.reason} [проба не запускалась: ${why}]`;
    const out = { id: st.f.id, class: st.f.class, mode: 'agents', variants: st.variants, lead };
    if (st.trial) out.trial = st.trial;
    if (Object.keys(st.sliceDropped).length > 0) out.slice_dropped = st.sliceDropped;
    return out;
  }),
};

const refutations = done.flatMap((st) => st.critics);

// Отпечаток содержимого: FNV-1a 64 по UTF-8 канонического JSON (ключи по алфавиту, без пробелов, не-ASCII как есть).
// Ту же функцию считает проверка писаря в Python, так что подмена значения на такое же по длине не пройдёт.
// В песочнице сценария нет ни crypto, ни TextEncoder — всё вручную, 64 бита в двух 32-битных половинах.
const canon = (v) => {
  if (Array.isArray(v)) return `[${v.map(canon).join(',')}]`;
  if (v && typeof v === 'object') {
    const keys = Object.keys(v)
      .filter((k) => v[k] !== undefined)
      .sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(',')}}`;
  }
  return JSON.stringify(v);
};
const utf8 = (text) => {
  const out = [];
  for (const ch of text) {
    const c = ch.codePointAt(0);
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  return out;
};
const fingerprint = (obj) => {
  let lo = 0x84222325;
  let hi = 0xcbf29ce4;
  for (const b of utf8(canon(obj))) {
    lo = (lo ^ b) >>> 0;
    // умножение на простое 0x100000001b3 = (1 << 40) + 0x1b3 по модулю 2^64
    const a = lo * 0x1b3;
    const carry = Math.floor(a / 4294967296);
    hi = (hi * 0x1b3 + carry + ((lo << 8) >>> 0)) >>> 0;
    lo = a >>> 0;
  }
  const hex = (n) => n.toString(16).padStart(8, '0');
  return hex(hi) + hex(lo);
};
const expectedDecisions = fingerprint(decisions);
const expectedRefutations = fingerprint(refutations);
const csvIds = done.map((st) => st.f.id);

// Писарь не печатает файлы: 60 КБ JSON модель пересказывает, а не копирует. Содержимое лежит в его промпте
// между маркерами, файлы пишет `zodchiy.py scribe`, достав сохранённый промпт агента по nonce.
// Nonce — отпечаток содержимого с солью, без Math.random/Date: промпт детерминирован для resume.
// Ожидаемые отпечатки в нём светить нельзя: ворота сравнивают ответ писаря именно с ними, и модель,
// увидев их в промпте, вернула бы их, не запуская команду (файлов на диске при этом нет или они чужие).
const nonce = fingerprint({ salt: 'zodchiy-scribe-nonce', decisions, refutations, csvIds });
const block = (name, text) => `<<<ZODCHIY ${name}\n${text}\n>>>ZODCHIY`;

// selfcheck и export читают не findings.csv аналитика, а его копию только с находками этого прогона:
// лишние строки сверх top и упавшие находки не должны краснить selfcheck.
const scribed = await ask(
  'scribe',
  'запись decisions.json',
  `Выполни ровно одну команду. Файлов сам не пиши и текст ниже не перепечатывай: команда достаёт его из твоего же промпта и пишет сама.
${CLI} scribe --nonce "${nonce}" --run-dir "${runDir}"
Верни её JSON: schema_errors, decisions_digest, refutation_digest, csv_ids — символ в символ, как напечатала команда.
Если вместо этого напечатано {"error": ...} (код выхода 1) — верни error с этим текстом, остальные поля пусты; повторно и по-другому не пробуй.
ZODCHIY-SCRIBE-NONCE: ${nonce}
${block(basename(P.refutation), JSON.stringify(refutations))}
${block(basename(P.decisions), JSON.stringify(decisions))}
${block(basename(P.findingsRun), JSON.stringify(csvIds))}
${COMMON}`,
  {
    type: 'object',
    properties: { schema_errors: strs, decisions_digest: str, refutation_digest: str, csv_ids: strs, error: str },
    required: ['schema_errors', 'decisions_digest', 'refutation_digest', 'csv_ids'],
  },
);

// Записанное не совпало с тем, что собрал сценарий, — путь к такому файлу наружу не отдаём.
// Повтора нет: файлы пишет код, а подмена, которую он ловил, теперь невозможна; расхождение отпечатка —
// это расхождение канонов JS и Python или порча в пути, и повтор дал бы тот же ответ.
const errors = [];
if (!scribed) {
  errors.push('писарь не ответил');
} else if (scribed.error) {
  errors.push(`писарь: ${scribed.error}`);
} else {
  for (const e of scribed.schema_errors) errors.push(`decisions.json не прошёл схему: ${e}`);
  if (scribed.decisions_digest !== expectedDecisions) {
    errors.push(`decisions.json записан не дословно: отпечаток ${scribed.decisions_digest}, ожидался ${expectedDecisions}`);
  }
  if (scribed.refutation_digest !== expectedRefutations) {
    errors.push(`refutation.json записан не дословно: отпечаток ${scribed.refutation_digest}, ожидался ${expectedRefutations}`);
  }
  if (JSON.stringify([...scribed.csv_ids].sort()) !== JSON.stringify([...csvIds].sort())) {
    errors.push(`${basename(P.findingsRun)}: id ${scribed.csv_ids.join(',')}, ожидались ${csvIds.join(',')}`);
  }
}
for (const e of errors) log(e);
const scribeOk = errors.length === 0;

let reported = null;
if (scribeOk) {
  reported = await ask(
    'reporter',
    'отчёт',
    `Выполни команды по порядку и верни результат.
1) ${CLI} selfcheck --findings "${P.findingsRun}" --measure "${P.measure}" --refutations "${P.refutation}"
   Если result != OK (код выхода 1) — дальше не иди; верни selfcheck_result и problems (по строке на проблему).
2) ${CLI} export --findings "${P.findingsRun}" --measure "${P.measure}" --refutations "${P.refutation}" --out "${P.findingsJson}"
3) ${CLI} report --measure "${P.measure}" --findings "${P.findingsJson}" --decisions "${P.decisions}" --format html --lang ${lang} --out "${P.html}"
4) то же с --format md --out "${P.md}"
5) то же с --format plan --out "${P.plan}"
Числа не пересчитывай и файлы не правь. ok — true, если шаги 1–5 прошли.
${COMMON}`,
    {
      type: 'object',
      properties: { ok: { type: 'boolean' }, selfcheck_result: str, problems: strs },
      required: ['ok', 'selfcheck_result'],
    },
  );
  if (!reported || !reported.ok) {
    const why = reported ? `selfcheck ${reported.selfcheck_result}${reported.problems ? ` — ${reported.problems.join(' | ')}` : ''}` : 'нет ответа';
    errors.push(`отчёт не собран: ${why}`);
    log(errors[errors.length - 1]);
  }
}

const okReport = Boolean(reported && reported.ok);
return {
  decisions_path: scribeOk ? P.decisions : null,
  report_html: okReport ? P.html : null,
  plan_md: okReport ? P.plan : null,
  branches: trials.filter((st) => st.trial.status === 'accepted').map((st) => st.trial.branch),
  agents,
  errors,
};
