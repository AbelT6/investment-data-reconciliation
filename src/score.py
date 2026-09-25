"""
score.py
Grades output/exception_log.csv against the answer key data/breaks_truth.csv.

  recall          = planted breaks caught / planted breaks
  false positives = exceptions raised that aren't in the answer key
  decoys flagged  = rounding-noise rows wrongly flagged (should be 0)

Then runs a NAIVE reconciliation (no dtype=str, exact-match compares) on the
same data to show what the two key design choices actually buy you.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUT = ROOT / "output"


def grade() -> None:
    truth = pd.read_csv(DATA / "breaks_truth.csv", dtype={"cusip": str})
    log = pd.read_csv(OUTPUT / "exception_log.csv", dtype={"cusip": str})

    decoys = set(truth.loc[truth.break_type == "decoy", "cusip"])
    planted = truth[truth.break_type != "decoy"]

    expected = set(zip(planted.cusip, planted.break_type))
    found = set(zip(log.cusip, log.break_type))

    caught = expected & found
    missed = expected - found
    false_pos = found - expected
    decoys_flagged = decoys & set(log.cusip)

    rows = []
    for bt, grp in planted.groupby("break_type"):
        exp = set(zip(grp.cusip, grp.break_type))
        fp = {f for f in false_pos if f[1] == bt}
        rows.append({"break_type": bt, "planted": len(exp), "caught": len(exp & found),
                     "missed": len(exp - found), "false_positives": len(fp)})
    table = pd.DataFrame(rows)

    print("=== Score vs. answer key ===")
    print(table.to_string(index=False))
    print(f"\nRecall:          {len(caught)}/{len(expected)} = {len(caught) / len(expected):.1%}")
    print(f"False positives: {len(false_pos)}")
    print(f"Decoys flagged:  {len(decoys_flagged)} of {len(decoys)}")
    for m in sorted(missed):
        print("  MISSED:", m)
    for f in sorted(false_pos):
        print("  FALSE POSITIVE:", f)

    table.to_csv(OUTPUT / "score.csv", index=False)


def naive_baseline() -> None:
    """Same data, two common shortcuts: no ID normalization and exact equality."""
    truth = pd.read_csv(DATA / "breaks_truth.csv", dtype={"cusip": str})
    true_counts = truth.break_type.value_counts()

    cust = pd.read_csv(DATA / "custodian.csv", dtype={"cusip": str})
    acct = pd.read_csv(DATA / "accounting.csv", dtype={"cusip": str}).drop_duplicates("cusip")
    m = cust.merge(acct, on="cusip", how="outer", suffixes=("_c", "_a"), indicator=True)
    both = m[m._merge == "both"]
    left = (m._merge == "left_only").sum()
    right = (m._merge == "right_only").sum()
    exact = sum((both[f + "_c"] != both[f + "_a"]).sum()
                for f in ["market_value", "book_value", "accrued_interest"])
    true_value_breaks = true_counts.get("market_value_mismatch", 0) + true_counts.get("accrued_interest_mismatch", 0)

    print("\n=== Naive baseline: skip ID normalization, compare with exact equality ===")
    print(f"Missing in accounting: {left} reported vs {true_counts['missing_in_accounting']} real")
    print(f"Missing at custodian:  {right} reported vs {true_counts['missing_at_custodian']} real")
    print(f"  -> {left - true_counts['missing_in_accounting'] + right - true_counts['missing_at_custodian']} "
          f"bogus 'missing' breaks: the {true_counts['id_format']} stripped CUSIPs don't join, "
          f"so each shows up as missing on BOTH sides and the real root cause (ID format) is lost")
    print(f"Value breaks: {exact} reported vs {true_value_breaks} real")
    print(f"  -> exact equality also flags the {true_counts['decoy']} rounding decoys and "
          f"the {true_counts['timing_difference']} timing rows as value breaks")


if __name__ == "__main__":
    grade()
    naive_baseline()
