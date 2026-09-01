# The doctrine, in one page

The full doctrine lives in [`SKILL.md`](SKILL.md) and is written in Russian —
it is read by a model, and translating 280 KiB would create a second copy that
drifts from the first within a month, which is the failure mode this repository
exists to detect. This page is the summary an English-reading human needs:
**what the doctrine requires**, without the reasoning behind each rule.

## The rule everything else serves

```
Numbers come from a script. Judgement comes from the model. Never the reverse.
No metric is eyeballed — not fan-in, not complexity, not a cycle.
No finding is issued before the full map is built.
```

Every number in a finding carries a `source` — a path into `measure.json`, such
as `behavior.hotspots[file=src/x.py].fix_share`. `zodchiy.py selfcheck` resolves
every path and refuses the report if one does not.

## Admission

A finding is admitted by how many axes agree about it, and by nothing else.

| Axes agreeing | Status | Fate |
|---|---|---|
| 1 | `hypothesis` | stays out of the report |
| 2 | `finding` | enters the report, with the missing axis named |
| 3 | `verdict` | goes first; enough to base an ADR on |

**Materiality gate: Pain × Spread.** Pain is what the file already costs —
fix share, rework rate, edit count. Spread is how far that cost travels — fan-in,
change spread across layers, coupling. A structural smell nobody ever pays for
is not a finding. A painful file with no structural cause is a staffing problem,
not an architectural one.

## Six steps

1. **Measure.** One pass over the import graph and the git history writes `measure.json`. No model involved. Every absolute number arrives with its percentile, and the calibration — blocked metrics, regex fallbacks, history window — is part of the output.
2. **Map, without verdicts.** Read the tops of the rankings, never the tree of directory names. Mark every claim `OBSERVED`, `INFERRED` or `UNKNOWN`. A verdict issued here drags the whole analysis behind it.
3. **Judge.** Apply the risk catalogue behind the materiality gate. Numbers come from `measure.json`; re-reading the code for meaning is allowed, for measurement it is not.
4. **Refute.** Attack every `verdict` and every finding of priority 6 or higher through four distinct lenses — not copies of one prompt. What survives is marked as surviving; what does not is dropped, not softened.
5. **Report.** Open with `Checklist: X/Y`; close with blind spots assembled mechanically. Silently truncating a top-N list is forbidden — say how many were dropped.
6. **Artefacts.** Current-state document, ADRs, migration plan, and the findings ledger the next run compares itself against.

Three modes: `audit` (your own repository), `plan` (findings into decisions and
their order — decoupling first, simplification after), `recon` (a repository you
have just been handed).

## Risk catalogue

| | Risk | What it means |
|---|---|---|
| **R1** | Cognitive overload | one unit costs too much to hold in the head |
| **R2** | Change propagation | one decision forces edits in many places |
| **R3** | Knowledge duplication | the same decision lives in two places at once |
| **R4** | Accidental complexity | the cost is not paid for by the problem being solved |
| **R5** | Dependency disorder | the graph decides the order of work instead of the domain |
| **R6** | Domain model distortion | the code says something the domain does not |

Thresholds, percentiles and the *what not to flag* section for each entry are in
[`references/risks.md`](references/risks.md).

## What not to flag

These are not politeness. On live code, without them, the tool called four
things defects that were none:

- a composition root with the highest churn in the repository — 74 edits, but
  11% of them bugfixes: an extension point by design, not debt
- a barrel file with fan-in 109 — re-export is its purpose
- four "cycles" that exist only under `if TYPE_CHECKING:`, which is precisely
  how a cycle is broken rather than how one is created
- high churn with a low bugfix share generally — read `fix_share`, not `edits`

All four are frozen as regression pairs in [`evals/`](evals/): each violation is
paired with a similar-looking innocent case.

## When the tool cannot deliver

Degradation is declared, never silent.

- Calibration fails, or a metric is blocked → the affected findings do not get
  issued, and `confidence_ceiling` drops. `measure` prints which metrics were
  blocked and why.
- No subagents for step 4 → the four lenses run sequentially, one model in one
  pass. Confidence is capped at `finding`; `verdict` becomes unreachable, and
  the report says so.
- A parser falls back from tree-sitter to regex → the file count is reported.

The reader must be able to tell what was not looked at. Silence reads as full
coverage, and that is the one error in a report a reader cannot catch.
