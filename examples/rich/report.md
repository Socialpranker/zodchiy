# Audit report — Textualize/rich

**Checklist: 5/6.** Step 6 (artefacts) was not performed: a current-state
document, ADRs and a migration plan belong to the repository's maintainers,
and this run is a demonstration of the method on a repository we do not own.
Steps 1–5 were performed in full; step 4 ran in degraded mode, declared below.

This is an example of what the tool produces, not a review of the project or
of anyone's work. `rich` was chosen because it is well maintained: a report on
a healthy repository shows what the admission rules keep *out*, which is the
part that matters.

## Snapshot

| | |
|---|---|
| repo | `Textualize/rich` |
| branch | `main` |
| HEAD | `9d8f9a372cc5` (2026-06-23) |
| worktree | clean |
| history window | whole history (`--since "20 years ago"`) |
| measured | 213 files, 750 edges, 1942 commits with code |
| parser | tree-sitter on 213 of 213 files, no regex fallback |

## Calibration and ceiling

Calibration passed: all control checks green, no blocked metrics. The
measurement ceiling was therefore `verdict`.

**The report ceiling is `finding`, not `verdict`**, for two independent
reasons. Step 4 ran sequentially — one model, one pass, no independent
lenses — which caps confidence by the degradation rule. And the intent axis
was not read: no ADRs, design documents or declared boundaries were consulted,
so no finding here has more than two axes behind it.

## Map

`rich` is a flat package: 213 files directly under `rich/`, two barrels
(`rich/__init__.py`, `rich/_unicode_data/__init__.py`), 750 runtime edges.
Change traffic splits into 4073 code edits, 1246 test edits and 2981 that the
classifier calls noise.

Structure concentrates sharply. `console.py` has fan-in 110 — every second
file in the package imports it — with 2699 LOC and 116 functions. Behind it:
`text.py` (57), `style.py` (47), `table.py` (29), `panel.py` (29). Complexity
peaks elsewhere: `pretty.py` carries a single function at cyclomatic 76.

Behaviour agrees on where the traffic goes. `console.py` is the most edited
file (463 edits) and the least stable (rework rate lower bound 0.50 against
0.263 for the repository). Typical change is small — median 1 file per commit,
p90 of 4 — and 37% of change episodes need more than one commit, with a
median span of 1.1 days.

One runtime strongly connected component of 53 files. One type-only cycle of
two files, correctly kept separate: it exists to break a runtime cycle, and
flagging it would invent a defect.

## Findings

### F1 — `console.py` concentrates the widest fan-in and the highest rework rate — `finding`, priority 1

2699 LOC, 116 functions, max nesting 11, fan-in 110 (100th percentile). 463
edits, more than any other file, and a Wilson lower bound on the rework rate
of 0.50: half the changes to this file come back as a fix within days, against
0.263 across the repository. Median fix latency 4.13 days.

*Source:* `structure.hubs[file=rich/console.py].fan_in`,
`structure.complex_files[file=rich/console.py].max_nesting`,
`behavior.hotspots[file=rich/console.py].edits`,
`behavior.stability.unstable_files[file=rich/console.py].rework_rate_lb`,
`behavior.stability.rework_rate`.

**Refutation.** L1 ("this is normal") — a documented facade earns a wide
fan-in, and the finding survives it, because intent explains the fan-in and
not the rework rate. L2 ("the metric measures something else") — survives:
calibration passed, all files parsed by tree-sitter, and the rework rate is a
lower bound over 409 changes rather than a raw share over a handful. L3 ("the
cost is inflated") — survives: the edits span the window instead of clustering
into one migration. L4 ("the cure costs more than the disease") — **demoted**:
110 importers and a documented public API make the extraction expensive.

**Remedy.** Extract the render pipeline into its own module, leaving `Console`
a facade that delegates; an extraction with the public API unchanged, not a
redesign. Predicted gain: `rework_rate_lb` for `console.py` at or below 0.40
in the next measurement.

### F2 — one function in `pretty.py` at cyclomatic complexity 76 — `finding`, priority 2

Cyclomatic 76 with nesting 9 in a 1017-line file; the worst function starts at
line 580. Fix share 0.19 over 156 edits — the 84th percentile of this
repository for the share of edits that are corrective.

*Source:* `structure.complex_files[file=rich/pretty.py].cyclomatic_per_function_max`,
`…worst_function_line`, `…max_nesting`,
`behavior.hotspots[file=rich/pretty.py].fix_share_pct`,
`behavior.hotspots[file=rich/pretty.py].edits`.

**Refutation.** All four lenses survived. The one that mattered was L2: the
threshold is defined per function, so the file's 1017 lines do not carry the
finding — 76 belongs to a single function.

**Remedy.** Replace the branch cascade at line 580 with a dispatch table keyed
by the node type. Contained in one module; predicted gain
`cyclomatic_per_function_max` at or below 40.

### F3 — a 50-file strongly connected component — `hypothesis`, not admitted to the report

`structure.cycles` reports one runtime SCC of 53 files. Recomputing it with
`rich/__init__.py` removed still leaves 50, so it is not the usual artefact of
`from rich import …` edges through the package barrel. The shape is real.

It is recorded here as a hypothesis and **kept out of the findings** because
its cost could not be established: every file inside the SCC that shows pain
is already explained by F1, and this measurement cannot attribute any of that
pain to the cycle itself. L3 dropped it on exactly that ground, L4 dropped it
for having no remedy to weigh.

This is the admission rule doing its job. A 50-file cycle is the most
alarming-looking number in the whole measurement, and it is the one number
this report refuses to turn into a recommendation.

## Deliberately not flagged

- **`_unicode_data/unicode10-0-0.py` … `unicode15-0-0.py`**, pairwise coupling
  degree 1.00. Generated data tables, updated together by construction. The
  catalogue excludes generated code from coupling findings.
- **`rich/__init__.py`, fan-in 43.** A barrel. High fan-in there is intent.
- **Single-owner concentration.** One contributor holds 0.89–0.92 of the edits
  in the core modules. That is a project risk, not a code defect, and this
  tool reports it without proposing anything.

## Blind spots

- **The intent axis was not read.** No ADRs or design documents were
  consulted. Every finding above stands on two axes at most.
- **Step 4 ran sequentially**, without independent lenses. Confidence is
  capped at `finding`; `verdict` was unreachable in this run by construction.
- **The behaviour axis includes deleted code.** The window is the whole
  history, so `rich/tui/*` — extracted into Textual years ago — still supplies
  the strongest temporal coupling (degree 1.00) and every entry in
  `velocity.slowest_files`. The structural axis only sees HEAD, so those pairs
  can never converge into a finding; they are noise from the past, and reading
  the coupling table without this note would mislead.
- **"Layers" here are single modules.** `rich` is flat, and layer depth 1 makes
  every top-level file its own layer. `containment` 0.672 therefore means "67%
  of commits touch one module", not architectural containment.
- **The window changes what may be claimed.** On the default window the
  coupling threshold stops discriminating on this repository and the metric is
  blocked outright. The thresholds were calibrated on two repositories; this
  is the third, and it moved them.
- **Nothing was truncated silently.** Top-N lists above are the tool's own
  ranked heads; the full arrays are in `measure.json`.
