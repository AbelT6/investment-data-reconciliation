# Investment Data Reconciliation

A small version of an investment data operations workflow. It reconciles bond positions between a **custodian** and an **investment accounting system**, investigates the breaks, assigns a probable **root cause** to each one, and writes an **exception log**. The same checks run in Python (pandas) and SQL (SQLite), and the results are graded against an answer key.

## The problem

An insurer's portfolio lives in several systems at once. The custodian holds the securities. The accounting platform books them. The data warehouse reports on them. Each one should show the same positions, values, accrued interest, and reference data. In practice they drift apart: feeds arrive late, pricing sources differ, reference data goes stale, and IDs get mangled along the way. Every difference is a **reconciliation break**. Until it's explained, the numbers going to finance, risk, and regulators can't be trusted.

## What the pipeline does

| Step | What it checks |
|---|---|
| 1. Load + validate | Row counts, nulls, numeric types, negative values, CUSIP length, as-of dates |
| 2. Normalize IDs | Re-pads CUSIPs that lost their leading zeros (`zfill(9)`) and logs each fix |
| 3. Duplicates | Flags any CUSIP loaded more than once, before matching |
| 4. Match | Outer merge on CUSIP with `indicator=True` to find positions missing from either side |
| 5. As-of date | Flags timing differences first, because a date mismatch explains that position's value differences |
| 6. Values | Market value, book value, and accrued interest, compared within tolerance |
| 7. Security master | Checks each system's rating against the reference table |
| 8. Exception log | One row per break: type, field, both values, difference, root cause, owner, status |
| 9. Summary | Counts by break type, match rate, dollar impact of market value breaks |

**Tolerance:** a difference counts as a break only if it exceeds `max($1.00, 0.01% of value)`. The absolute floor stops penny rounding on small numbers like accrued interest from raising alerts. The relative part stops rounding noise on a $20M market value from looking like a break. With only one of the two, one of those cases produces false alarms.

## Break types and root causes

The mock data (`src/generate_data.py`, fixed seed) has 200 bond positions across public fixed income, structured products, mortgages, and private credit, with these breaks planted:

| Break type | Planted | Probable root cause | Owner |
|---|---|---|---|
| Missing in accounting | 4 | Trade not booked / feed failure | Trade Support |
| Missing at custodian | 3 | Pending settlement / stale position | Trade Support |
| Market value mismatch | 8 | Different pricing source | Pricing |
| Accrued interest mismatch | 6 | Day-count or accrual cutoff | Investment Accounting |
| ID format | 5 | CUSIP read as a number, leading zeros stripped | Data Operations |
| Duplicate position | 3 | Feed double-loaded | Data Operations |
| Rating mismatch | 4 | Stale security master | Reference Data |
| Timing difference | 3 | Late feed (prior-day file) | Data Operations |
| **Decoys** | 10 | Rounding under $1, which should **not** be flagged | n/a |

## Results

From `output/summary.csv` and `src/score.py`:

- **203** unique positions reconciled, **167** clean → **82.3% match rate**
- **36** exceptions logged across **8** break types
- **36 / 36** planted breaks caught (100% recall), **0** false positives, **0 of 10** decoys flagged
- Market value breaks: **$3,174,061.56** gross, **−$1,518,185.56** net
- SQL results match Python on every break type (`output/python_vs_sql.csv`)

**What the design choices buy you.** `score.py` also runs a naive version on the same data, with no ID normalization and exact-match comparisons:

| | Naive | This pipeline | Actual |
|---|---|---|---|
| Missing positions reported | 17 | 7 | 7 |
| Value breaks reported | 27 | 14 | 14 |

The naive run turns 5 ID-format breaks into 10 bogus "missing" positions and loses the real root cause. It also flags all 10 rounding decoys and treats the 3 timing breaks as value breaks.

## SQL

`sql/reconcile.sql` runs the same checks in SQLite, one named query per check:

- ID normalization with `substr('000000000' || cusip, -9)` and `GLOB`
- Duplicates with `GROUP BY ... HAVING COUNT(*) > 1`
- Missing positions with `LEFT JOIN ... WHERE right.key IS NULL`, in both directions
- Value breaks with `ABS(a.x - c.x) > MAX(1.0, 0.0001 * ABS(c.x))`
- Rating breaks with a three-way join: custodian, accounting, and security master

## Excel layer

`output/exception_log.xlsx` contains the exception log (`tblExceptions`) and the security master (`tblMaster`) as Excel Tables, plus the summary. On top of that, built in Excel:

- Pivot table of breaks by type and asset class
- `XLOOKUP` pulling asset class, rating, and maturity from `tblMaster` into the log
- Conditional formatting on open exceptions
- Status column with data validation (Open / Investigating / Resolved)

<!-- screenshot: docs/excel.png -->

## How to run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python run_all.py
```

Or step by step: `python src/generate_data.py`, `src/reconcile.py`, `src/score.py`, `src/run_sql.py`, `src/export_excel.py`.

## Layout

```
data/     generated CSVs + breaks_truth.csv answer key
output/   exception_log.csv/.xlsx, summary.csv, score.csv, python_vs_sql.csv
sql/      reconcile.sql
src/      generate_data.py, reconcile.py, score.py, run_sql.py, export_excel.py
```

*Simplifications: all positions pay semiannual coupons on a 30/360 basis, and all data is synthetic, with fictional issuers.*
