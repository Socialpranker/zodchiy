# Calibration

Thresholds in `zodchiy` were derived from two private repositories, referred to
in the source as `repo-A` and `repo-B`. Their real names are stripped when
this tree is built; what matters is their shape:

| | `repo-A` | `repo-B` |
|---|---|---|
| language | Python | TypeScript / React |
| files | 495 | not recorded |
| history | 543 commits | 471 commits |
| architecture | layered, contracts enforced by import-linter | feature-sliced design |

Two repositories are not a corpus. This has three consequences that survive
into your own use of the tool:

1. **Absolute thresholds will drift.** `fix_share 0.43` means nothing in your
   repository. `top 7% of this repository` travels. Every rankable metric
   carries a `*_pct` field next to the absolute number — read that one.
2. **The known defect stays known.** `FIX_RE` in `behavior.py` starts with
   `^\s*fix` without a word boundary, so a commit subject like `fixture: ...`
   counts as a bugfix. Measured cost on the calibration pair: 0 false
   positives out of 215 matches. It is left alone, and deliberately not
   frozen by a test — a test would legitimise the defect.
3. **A third repository is the next real evaluation.** Until then, treat the
   normalised percentiles as the product and the raw thresholds as a default.

The performance budget in `evals/run_evals.py` points at `~/projects/repo-A`,
which does not exist on your machine. That target is skipped silently by
design — the fixture budget still runs.
