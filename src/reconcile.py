"""
reconcile.py
Reconciles custodian vs. accounting positions and writes an exception log.

Steps
  1. Load + validate      - read IDs as strings, check counts / nulls / types
  2. Normalize IDs        - re-pad CUSIPs that lost leading zeros, log each fix
  3. Detect duplicates    - in each source, before matching
  4. Match                - outer merge on CUSIP to find missing positions
  5. Compare fields       - MV, BV, accrued interest with abs + relative tolerance
  6. Security master      - flag rating mismatches
  7. As-of date           - flag timing differences
  8. Exception log        - output/exception_log.csv
  9. Summary              - output/summary.csv
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUT = ROOT / "output"

DATE_IDENTIFIED = "2026-10-01"   # recon runs the business day after the 9/30 as-of date

# Tolerance: a difference is a break only if it beats BOTH of these.
ABS_TOL = 1.00       # $1.00  -> ignores penny rounding on small numbers
REL_TOL = 0.0001     # 0.01%  -> ignores rounding noise on very large numbers
VALUE_FIELDS = ["market_value", "book_value", "accrued_interest"]

ROOT_CAUSE = {
    "missing_in_accounting": "Trade not booked in accounting or feed failure",
    "missing_at_custodian": "Pending settlement or stale position still carried in accounting",
    "market_value_mismatch": "Different pricing source between systems",
    "book_value_mismatch": "Amortization / cost basis difference",
    "accrued_interest_mismatch": "Day-count convention or accrual cutoff difference",
    "id_format": "CUSIP read as a number upstream (Excel/CSV typing stripped leading zeros)",
    "duplicate_position": "Feed double-loaded the position",
    "rating_mismatch": "Stale security master data in accounting",
    "timing_difference": "Late feed: accounting loaded the prior day's file; value diffs on this position are timing, re-check after feeds align",
}
OWNER = {
    "missing_in_accounting": "Trade Support",
    "missing_at_custodian": "Trade Support",
    "market_value_mismatch": "Pricing",
    "book_value_mismatch": "Investment Accounting",
    "accrued_interest_mismatch": "Investment Accounting",
    "id_format": "Data Operations",
    "duplicate_position": "Data Operations",
    "rating_mismatch": "Reference Data",
    "timing_difference": "Data Operations",
}

exceptions: list[dict] = []


def log(cusip, break_type, field, cust_val, acct_val, diff=None):
    exceptions.append({
        "cusip": cusip,
        "break_type": break_type,
        "field": field,
        "custodian_value": cust_val,
        "accounting_value": acct_val,
        "difference": diff,
        "probable_root_cause": ROOT_CAUSE[break_type],
        "status": "Open",
        "owner": OWNER[break_type],
        "date_identified": DATE_IDENTIFIED,
    })


# ---------- 1. Load + validate ----------
def load(name: str) -> pd.DataFrame:
    # dtype=str on cusip is the whole ballgame: without it pandas turns
    # "007543127" into the integer 7543127 and the join silently fails.
    return pd.read_csv(DATA / name, dtype={"cusip": str, "position_id": str})


def validate(df: pd.DataFrame, name: str) -> None:
    print(f"\n[{name}] rows={len(df)}")
    nulls = df.isna().sum()
    print("  nulls:", "none" if nulls.sum() == 0 else nulls[nulls > 0].to_dict())
    for col in VALUE_FIELDS + ["par_value"]:
        if col in df and not pd.api.types.is_numeric_dtype(df[col]):
            print(f"  WARNING: {col} is not numeric")
    if "par_value" in df:
        neg = (df[VALUE_FIELDS + ["par_value"]] < 0).any(axis=1).sum()
        print(f"  negative values: {neg}")
    bad_len = (df.cusip.str.len() != 9).sum()
    print(f"  CUSIPs not 9 characters: {bad_len}")
    if "as_of_date" in df:
        print(f"  as_of_date values: {df.as_of_date.value_counts().to_dict()}")


# ---------- 2. Normalize IDs ----------
def normalize_cusips(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # Only all-digit IDs shorter than 9 can be "a number that lost its zeros".
    broken = df.cusip.str.isdigit() & (df.cusip.str.len() < 9)
    for raw in df.loc[broken, "cusip"].unique():
        fixed = raw.zfill(9)
        log(fixed, "id_format", "cusip", fixed, raw)
    df.loc[broken, "cusip"] = df.loc[broken, "cusip"].str.zfill(9)
    return df


# ---------- 3. Duplicates ----------
def dedupe(df: pd.DataFrame, source: str) -> pd.DataFrame:
    counts = df.cusip.value_counts()
    for cusip, n in counts[counts > 1].items():
        if source == "custodian":
            log(cusip, "duplicate_position", "row_count", n, None)
        else:
            log(cusip, "duplicate_position", "row_count", None, n)
    return df.drop_duplicates(subset="cusip", keep="first")


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)

    print("=== 1. Load + validate ===")
    master = load("security_master.csv")
    cust = load("custodian.csv")
    acct = load("accounting.csv")
    validate(master, "security_master")
    validate(cust, "custodian")
    validate(acct, "accounting")

    print("\n=== 2. Normalize IDs ===")
    cust = normalize_cusips(cust)
    acct = normalize_cusips(acct)
    print(f"  re-padded CUSIPs: {sum(e['break_type'] == 'id_format' for e in exceptions)}")

    print("\n=== 3. Duplicates ===")
    cust = dedupe(cust, "custodian")
    acct = dedupe(acct, "accounting")
    print(f"  duplicate CUSIPs: {sum(e['break_type'] == 'duplicate_position' for e in exceptions)}")

    print("\n=== 4. Match ===")
    merged = cust.merge(acct, on="cusip", how="outer", suffixes=("_cust", "_acct"), indicator=True)
    print(f"  {merged['_merge'].value_counts().to_dict()}")
    for c in merged.loc[merged._merge == "left_only", "cusip"]:
        log(c, "missing_in_accounting", "position", "present", "absent")
    for c in merged.loc[merged._merge == "right_only", "cusip"]:
        log(c, "missing_at_custodian", "position", "absent", "present")
    both = merged[merged._merge == "both"].copy()

    print("\n=== 7. As-of date (checked before values: timing explains value diffs) ===")
    timing = both.as_of_date_cust != both.as_of_date_acct
    for r in both[timing].itertuples():
        log(r.cusip, "timing_difference", "as_of_date", r.as_of_date_cust, r.as_of_date_acct)
    print(f"  timing differences: {timing.sum()}")

    print("\n=== 5. Compare values (abs + relative tolerance) ===")
    comparable = both[~timing]
    for field in VALUE_FIELDS:
        c = comparable[f"{field}_cust"]
        a = comparable[f"{field}_acct"]
        diff = a - c
        tol = (c.abs() * REL_TOL).clip(lower=ABS_TOL)       # max(abs_tol, rel_tol * value)
        is_break = diff.abs() > tol
        within = ((diff.abs() > 0) & ~is_break).sum()
        for idx in comparable.index[is_break]:
            log(comparable.at[idx, "cusip"], f"{field}_mismatch", field,
                round(c[idx], 2), round(a[idx], 2), round(diff[idx], 2))
        print(f"  {field:17s} breaks={is_break.sum():2d}  non-zero diffs within tolerance={within}")

    print("\n=== 6. Security master (ratings) ===")
    ref = master[["cusip", "rating"]].rename(columns={"rating": "rating_master"})
    rated = both.merge(ref, on="cusip", how="left")
    for r in rated.itertuples():
        if r.rating_acct != r.rating_master or r.rating_cust != r.rating_master:
            log(r.cusip, "rating_mismatch", "rating", r.rating_cust, r.rating_acct)
            exceptions[-1]["probable_root_cause"] += f" (security master: {r.rating_master})"
    missing_ref = rated.rating_master.isna().sum()
    print(f"  rating mismatches: {sum(e['break_type'] == 'rating_mismatch' for e in exceptions)}"
          f"  positions missing from master: {missing_ref}")

    # ---------- 8. Exception log ----------
    log_df = pd.DataFrame(exceptions)
    log_df.insert(0, "exception_id", [f"EXC-{i:04d}" for i in range(1, len(log_df) + 1)])
    log_df.to_csv(OUTPUT / "exception_log.csv", index=False)

    # ---------- 9. Summary ----------
    all_positions = set(merged.cusip)
    broken_positions = set(log_df.cusip)
    clean = len(all_positions - broken_positions)
    mv = log_df[log_df.break_type == "market_value_mismatch"]
    summary = [("break_count", bt, n) for bt, n in log_df.break_type.value_counts().items()]
    summary += [
        ("metric", "total_exceptions", len(log_df)),
        ("metric", "unique_positions", len(all_positions)),
        ("metric", "positions_with_breaks", len(broken_positions)),
        ("metric", "positions_reconciled_cleanly", clean),
        ("metric", "match_rate_pct", round(100 * clean / len(all_positions), 1)),
        ("metric", "mv_break_abs_dollar_impact", round(mv.difference.abs().sum(), 2)),
        ("metric", "mv_break_net_dollar_impact", round(mv.difference.sum(), 2)),
    ]
    summary_df = pd.DataFrame(summary, columns=["section", "name", "value"], dtype=object)
    summary_df.to_csv(OUTPUT / "summary.csv", index=False)

    print("\n=== 8/9. Output ===")
    print(summary_df.to_string(index=False))
    print(f"\nWrote {OUTPUT / 'exception_log.csv'} and {OUTPUT / 'summary.csv'}")


if __name__ == "__main__":
    main()
