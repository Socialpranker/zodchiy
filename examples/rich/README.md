# Example run — Textualize/rich

A complete pass of the doctrine over a repository nobody here maintains, kept
in the repository so the output can be read before the tool is installed.

`rich` was picked because it is well maintained. A report on a healthy
codebase shows what the admission rules keep *out* — the largest number in
this measurement, a 50-file dependency cycle, is the one thing the report
refuses to turn into a recommendation.

| File | What it is |
|---|---|
| `report.md` | the report a reader gets — start here |
| `measure.json` | the measurement everything else cites, 120 KB |
| `findings.csv` | the ledger the next run compares itself against |
| `refutation.json` | 12 verdicts: four lenses over three findings |
| `findings.json` | the same findings as a document under `schema/findings.schema.json` |
| `findings.sarif` | the same findings as SARIF 2.1.0, for code scanning |
| `baseline.json` | the snapshot `gate` would compare against in CI |

## Reproducing it

```sh
git clone https://github.com/Textualize/rich /tmp/rich
cd /tmp/rich && git checkout 9d8f9a372cc5

python3 zodchiy.py measure /tmp/rich --since "20 years ago" --out measure.json
python3 zodchiy.py snapshot measure.json --out baseline.json
python3 zodchiy.py selfcheck --findings findings.csv --measure measure.json \
    --refutations refutation.json
python3 zodchiy.py export --findings findings.csv --measure measure.json \
    --refutations refutation.json --format sarif --out findings.sarif
```

Step 1 is a script and reproduces exactly at that commit. Steps 2 through 5 —
the map, the judgement, the refutation and the report — are the doctrine, and
they are executed by an agent that has read `SKILL.md`. A different model will
not write the same prose; what it must not do is issue a finding whose cost is
not in `measure.json`, and `selfcheck` is what enforces that.

## What this run cost the tool

Two defects surfaced within the first live pass and are fixed in this tree:
`add` accepted a JSON array for `axes` and stored the Python repr of it, which
`export` then failed to read back; and `gain_metric` only accepts a snapshot
field, so a per-file prediction has to fall back to `manual` — visible in
`findings.csv`, where two of three findings do exactly that.
