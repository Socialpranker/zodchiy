# zodchiy — architectural audit with measured materiality

[![evals](https://github.com/Socialpranker/zodchiy/actions/workflows/evals.yml/badge.svg)](https://github.com/Socialpranker/zodchiy/actions/workflows/evals.yml)

**Materiality is measured, not asserted.**

Every architecture linter can tell you that a module has fan-in 40 or that
there is an import cycle. None of them can tell you what it costs, because the
cost is not in the import graph — it is in the git history. `zodchiy` reads
both, and the cost is not decoration: it is the admission rule. A finding that
cannot be priced does not enter the report.

The name is Russian: *зодчий*, a master builder.

## What it does

An audit is six steps. The first is a script and needs no model at all; the
rest are a doctrine an agent executes, and every one of them has something
mechanical holding it honest.

1. **Measure.** One pass over the import graph and the git history writes a
   single `measure.json`: runtime cycles (type-only ones kept apart — they
   exist to break cycles, flagging them invents a defect), hubs and fan-in
   through barrel files, per-function complexity, temporal coupling, hotspots
   ranked by fix share, how many files a typical change touches and in which
   layer, how often a change has to be reworked within days, how much
   activity stays inside one layer, and where knowledge sits with a single
   owner. Every absolute number comes with its percentile in this repository.
   Calibration is part of the output: which metrics were blocked, which files
   fell back to regex parsing, how far back the history window reaches.
2. **Map, without verdicts.** The agent reads the top of each ranking — never
   the tree of directory names — and marks every claim `OBSERVED`, `INFERRED`
   or `UNKNOWN`. Judgement at this step is a defect of the run, not haste:
   a verdict issued before the map drags the whole analysis behind it.
3. **Judge.** A risk catalogue (R1–R6) with a materiality gate — Pain ×
   Spread. Each catalogue entry carries its own *what not to flag*: a
   composition root with high fan-out is a design, not a mess.
4. **Refute.** Every high-priority finding is attacked through four lenses.
   What survives is marked as surviving; what does not is dropped, not
   softened. Without subagents the lenses run sequentially — and then the
   finding's confidence is capped and the degradation is printed in the report.
5. **Report.** Opens with `Checklist: X/Y` and closes with a mandatory blind
   spots section, assembled mechanically rather than recalled: blocked
   metrics, regex-parsed files, the history window, links no import graph can
   see (DI, registries, reflection, string keys), and how step 4 actually ran.
   Truncating a top-N list silently is forbidden — say how many were dropped.
6. **Artefacts.** A current-state document, ADRs, a migration plan, and a
   findings ledger that the next run compares itself against.

Three modes: `audit` (your own repository), `plan` (findings into decisions
and their order — decoupling first, simplification after), `recon` (a
repository you have just been handed — never run yet, see below).

Out of a run you get: `measure.json`, a Markdown report, `findings.csv`,
`baseline.json`, and machine-readable findings as JSON or SARIF 2.1.0.

**[`examples/rich/`](examples/rich/) holds a complete run** on a repository
nobody here maintains — measurement, ledger, twelve refutation verdicts,
report, SARIF. Read `report.md` first: the largest number in that measurement,
a 50-file dependency cycle, is the one thing the report refuses to turn into a
recommendation, because its cost could not be established.

## What changes

- **A refactor gets a price.** Not "there is a cycle in `billing`" but "this
  file is in the top decile by fix share, its changes drag two other layers
  along, and a third of them come back for rework within a week". Work can
  then be ranked against other work instead of smells against smells.
- **Single-axis noise never reaches the report.** A structural smell nobody
  ever pays for stays a `hypothesis` and stays out. This is the rule that
  makes the output short enough to act on.
- **Numbers survive contact with the model.** Every figure carries a path
  into `measure.json`, and `selfcheck` resolves it. A number that was
  remembered rather than measured cannot quietly reach the reader — the
  failure mode that makes most model-written audits unusable.
- **Thresholds travel between repositories.** Percentiles are the product;
  absolute numbers are a local default.
- **Drift gets a gate, not a dashboard.** `gate` exits 1 in CI on a new
  runtime cycle or a metric that got worse. Detection without a gate does not
  fix drift — it only documents it.
- **The audit is falsifiable afterwards.** Every finding must predict a
  `gain`; `verify` compares that prediction against the next measurement.
  An audit nobody can be wrong about is not an audit.
- **What was not looked at is printed.** Silence reads as full coverage, and
  that is the one error in a report a reader cannot catch.

It is not a linter, not a security scanner, and not a diff review — it does
not tell you what is wrong with the change you are about to commit. It tells
you which part of the repository is expensive to keep, and how sure you are
allowed to be about that.

## The hard rule

```
Numbers come from a script. Judgement comes from the model. Never the reverse.
No metric is eyeballed — not fan-in, not complexity, not a cycle.
No finding is issued before the full map is built.
```

Every number in a finding carries a `source`: a path into `measure.json`, such
as `behavior.hotspots[file=src/x.py].fix_share` — not the prose "according to
the measurement". Prose cannot be told apart from a number recalled from
memory; a path is checked mechanically by `zodchiy.py selfcheck`, which refuses
to sign off a report whose numbers do not resolve.

## Three axes, and a finding admitted by convergence

| Axis | Source | Blind to |
|---|---|---|
| **Intent** | ADR, README, agent instructions, import-linter / ArchUnit / eslint-boundaries | lies once the docs fall behind the code |
| **Structure** | `scripts/structure.py` — import graph, cycles, fan-in/out, complexity | DI, registries, reflection, string keys |
| **Behaviour** | `scripts/behavior.py` — churn, fix share, co-change, ownership | code that has not changed yet |

| Axes agreeing | Status | Fate |
|---|---|---|
| 1 | `hypothesis` | stays out of the report |
| 2 | `finding` | enters the report, with the missing axis named |
| 3 | `verdict` | goes first; good enough to base an ADR on |

A structural smell nobody ever pays for is not a finding here. Neither is a
painful file with no structural cause — that one is a staffing problem.

## Quickstart

Python 3.11+, `git`. No dependencies outside the standard library, by design:
this is a tool that audits other people's repositories, and it should not add
a supply chain to do it.

```sh
python3 zodchiy.py measure /path/to/repo --out .zodchiy/measure.json
python3 zodchiy.py snapshot .zodchiy/measure.json --out .zodchiy/baseline.json
python3 zodchiy.py gate .zodchiy/measure.json --baseline .zodchiy/baseline.json   # exit 1 on regression
python3 zodchiy.py export --findings .zodchiy/findings.csv --measure .zodchiy/measure.json --format sarif
```

`measure` is the whole first step: no model is involved, and none is needed.
The audit itself is a doctrine, not a script — it is in `SKILL.md`, and it is
executed by an agent that has read it.

## Install

The canonical form is an agent skill. Drop the directory in place, or run
`./install.sh`, which finds the harnesses present on the machine and refuses
to overwrite anything it did not write.

### Doctrine

One text under four names — each harness looks for a different filename.

| File | Goes to |
|---|---|
| `AGENTS.md` | Codex (`~/.codex/AGENTS.md`, or the repository root), Grok Build, Cursor, Zed |
| `GEMINI.md` | Gemini CLI (`~/.gemini/GEMINI.md`, or the repository root) |
| `QWEN.md` | Qwen Code (`~/.qwen/QWEN.md`) |
| `IFLOW.md` | iFlow CLI (`~/.iflow/IFLOW.md`) |

### Slash commands

| File | Goes to |
|---|---|
| `gemini/commands/zodchiy.toml` | `~/.gemini/commands/zodchiy.toml` |
| `iflow/commands/zodchiy.toml` | `~/.iflow/commands/zodchiy.toml` |

Claude Code and Grok Build need no adapter — the whole directory goes to
`~/.claude/skills/zodchiy/` or `~/.grok/skills/zodchiy/`. Adapters are generated
from `SKILL.md` by `scripts/build_adapters.py`; do not edit `dist/` by hand.

## What is verified, and what is not

The same rule this tool applies to other people's code applies to its own
claims. Verified:

- 164 unit tests, green
- 14 frozen end-to-end cases against ground truth, each `dirty`
  case paired with a `clean` one — without the pair, "found N" cannot be told
  apart from "found N plus ten false ones" (method borrowed from OWASP Benchmark)
- a robustness layer of degenerate repositories: no commits, one commit,
  non-UTF-8 file, detached HEAD, not a git repository at all. Each must answer
  with an explicit field, not a traceback
- the harness itself was checked by mutation: it goes red when it should

Not verified, stated plainly because a tool that hides its own gaps has no
business auditing yours:

- **no adapter has ever been run inside its target harness.** Built is not working
- the trigger evaluation (does the skill fire on indirect phrasing?) has been
  written and never run
- step 4, the refutation pass, has never been executed end to end — only the
  mechanics of recording its verdict are covered
- `recon` mode has never been run
- thresholds were calibrated on two repositories and will drift on a third.
  Percentiles travel between projects; absolute numbers do not. See CALIBRATION.md

## Language

The doctrine — `SKILL.md`, `SPEC.md` and `references/` — is in Russian. It is
read by a model, and models read Russian. Translating 280 KiB would create a
second copy that drifts from the first within a month, which is the exact
failure mode this repository exists to detect. English is the interface:
README, installer, CLI help of the public documents.

## License

MIT. See LICENSE.
