"""
generate_data.py
Builds mock investment data for two systems that *should* agree
(custodian vs. investment accounting), then plants known breaks in them.

Outputs (in data/):
  security_master.csv  - reference attributes for every security
  custodian.csv        - positions as the custodian bank reports them
  accounting.csv       - positions as the accounting platform books them
  breaks_truth.csv     - answer key: every planted break (plus decoys)

Fixed random seed -> same data on every run, so results are reproducible.
"""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
N_POSITIONS = 200
AS_OF = date(2026, 9, 30)          # quarter-end reporting date
PRIOR_DAY = date(2026, 9, 29)       # used to plant timing differences

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

rng = np.random.default_rng(SEED)

# How many of each break to plant (from the project plan)
PLAN = {
    "missing_in_accounting": 4,
    "missing_at_custodian": 3,
    "market_value_mismatch": 8,
    "accrued_interest_mismatch": 6,
    "id_format": 5,
    "duplicate_position": 3,
    "rating_mismatch": 4,
    "timing_difference": 3,
    "decoy": 10,  # tiny rounding diffs (< $1) that must NOT be flagged
}

RATINGS = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-",
           "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+"]

ASSET_CLASSES = {
    # asset class: (share of book, rating range index lo/hi, coupon lo/hi)
    "Public Fixed Income": (0.55, (0, 9), (2.0, 6.5)),
    "Structured Products": (0.20, (0, 7), (4.0, 8.0)),
    "Mortgages":           (0.15, (3, 9), (4.5, 7.5)),
    "Private Credit":      (0.10, (9, 13), (7.0, 11.5)),
}

# Fictional issuers (no real companies)
ISSUERS = {
    "Public Fixed Income": ["Northgate Utilities", "Harlow Industrial", "Cedar Bay Health",
                            "Summit Rail", "Lakeview Power", "Ironwood Chemicals",
                            "Brookline Telecom", "State of Avalon GO", "Granite Water Auth",
                            "Pioneer Foods"],
    "Structured Products": ["Harbor CLO 2021-1 A", "Beacon Auto ABS 2023-2 A2",
                            "Keystone CMBS 2022-C4 A3", "Atlas Card Trust 2024-1 A",
                            "Riverbend CLO 2020-3 B"],
    "Mortgages":           ["CML 450 Park Office", "CML Westfield Industrial",
                            "CML Marina Multifamily", "CML Oakridge Retail"],
    "Private Credit":      ["DL Meridian Software", "DL Copperline Logistics",
                            "DL Bluefin Healthcare", "DL Stonegate Services"],
}


# ---------- CUSIP helpers ----------
def cusip_check_digit(first8: str) -> str:
    """Standard CUSIP check-digit algorithm (modulus 10, double-add-double)."""
    total = 0
    for i, ch in enumerate(first8):
        v = int(ch) if ch.isdigit() else ord(ch) - 55  # A=10 ... Z=35
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def make_cusip(numeric_leading_zero: bool) -> str:
    """9-char CUSIP. Some are all digits with leading zeros -- the kind Excel/CSV
    typing silently breaks by reading them as numbers."""
    if numeric_leading_zero:
        zeros = rng.integers(1, 3)                     # 1 or 2 leading zeros
        body = "".join(rng.choice(list("0123456789"), 6 - zeros))
        issuer = "0" * zeros + body
        # make sure the char after the zeros isn't another zero
        if issuer[zeros] == "0":
            issuer = issuer[:zeros] + "7" + issuer[zeros + 1:]
    else:
        chars = list("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ")
        issuer = str(rng.integers(1, 10)) + "".join(rng.choice(chars, 4)) + rng.choice(list("ABCDEFGH"))
    issue = "".join(rng.choice(list("0123456789"), 2))
    first8 = issuer + issue
    return first8 + cusip_check_digit(first8)


def accrued_days_30_360(last_coupon: date, as_of: date) -> int:
    d1 = min(last_coupon.day, 30)
    d2 = min(as_of.day, 30)
    return (as_of.year - last_coupon.year) * 360 + (as_of.month - last_coupon.month) * 30 + (d2 - d1)


def last_coupon_date(maturity: date, as_of: date) -> date:
    """Semiannual coupons on the maturity month/day and 6 months off it."""
    months = sorted({maturity.month, (maturity.month + 5) % 12 + 1})
    candidates = []
    for yr in (as_of.year - 1, as_of.year):
        for m in months:
            d = date(yr, m, maturity.day)
            if d <= as_of:
                candidates.append(d)
    return max(candidates)


# ---------- 1. security master ----------
def build_security_master(n: int) -> pd.DataFrame:
    classes = list(ASSET_CLASSES)
    weights = [ASSET_CLASSES[c][0] for c in classes]
    rows, seen = [], set()
    while len(rows) < n:
        numeric = rng.random() < 0.35
        cusip = make_cusip(numeric)
        if cusip in seen:
            continue
        seen.add(cusip)
        ac = rng.choice(classes, p=weights)
        _, (rlo, rhi), (clo, chi) = ASSET_CLASSES[ac]
        coupon = round(float(rng.uniform(clo, chi)) * 8) / 8          # nearest 1/8th
        maturity = date(int(rng.integers(2027, 2041)), int(rng.integers(1, 13)), int(rng.integers(1, 29)))
        name = f"{rng.choice(ISSUERS[ac])} {coupon:.3f}% {maturity:%m/%y}"
        rows.append({
            "cusip": cusip,
            "security_name": name,
            "asset_class": ac,
            "coupon_rate": coupon,
            "maturity_date": maturity.isoformat(),
            "rating": RATINGS[int(rng.integers(rlo, rhi + 1))],
        })
    return pd.DataFrame(rows)


# ---------- 2. clean positions (both systems start identical) ----------
def build_positions(master: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for i, sec in enumerate(master.itertuples(index=False), start=1):
        par = float(rng.integers(200, 5001)) * 5_000                   # $1M - $25M
        price = float(rng.uniform(85, 110))
        book_price = float(rng.uniform(95, 105))
        maturity = date.fromisoformat(sec.maturity_date)
        days = accrued_days_30_360(last_coupon_date(maturity, AS_OF), AS_OF)
        rows.append({
            "position_id": f"P{i:04d}",
            "cusip": sec.cusip,
            "par_value": round(par, 2),
            "market_value": round(par * price / 100, 2),
            "book_value": round(par * book_price / 100, 2),
            "accrued_interest": round(par * sec.coupon_rate / 100 * days / 360, 2),
            "rating": sec.rating,
            "as_of_date": AS_OF.isoformat(),
        })
    return pd.DataFrame(rows)


def main() -> None:
    DATA.mkdir(exist_ok=True)

    # 200 positions held in both systems + 3 securities only accounting still carries
    extra = PLAN["missing_at_custodian"]
    master = build_security_master(N_POSITIONS + extra)
    positions = build_positions(master)

    custodian = positions.iloc[:N_POSITIONS].copy()
    accounting = positions.iloc[:N_POSITIONS].copy()
    stale_only_in_accounting = positions.iloc[N_POSITIONS:].copy()

    truth = []  # (cusip, break_type, detail)

    # Pick disjoint positions for each break so every planted break is unambiguous.
    idx = list(range(N_POSITIONS))
    numeric_pool = [i for i in idx if custodian.at[i, "cusip"].isdigit() and custodian.at[i, "cusip"][0] == "0"]
    id_rows = list(rng.choice(numeric_pool, PLAN["id_format"], replace=False))
    remaining = [i for i in idx if i not in id_rows]
    rng.shuffle(remaining)

    def take(k):
        chosen = remaining[:k]
        del remaining[:k]
        return chosen

    picks = {
        "id_format": id_rows,
        "missing_in_accounting": take(PLAN["missing_in_accounting"]),
        "market_value_mismatch": take(PLAN["market_value_mismatch"]),
        "accrued_interest_mismatch": take(PLAN["accrued_interest_mismatch"]),
        "duplicate_position": take(PLAN["duplicate_position"]),
        "rating_mismatch": take(PLAN["rating_mismatch"]),
        "timing_difference": take(PLAN["timing_difference"]),
        "decoy": take(PLAN["decoy"]),
    }

    # --- ID format: accounting strips leading zeros (CUSIP read as a number)
    for i in picks["id_format"]:
        good = accounting.at[i, "cusip"]
        accounting.at[i, "cusip"] = good.lstrip("0")
        truth.append((good, "id_format", f"accounting holds '{good.lstrip('0')}'"))

    # --- Market value: accounting priced from a different source (1-5% off)
    for i in picks["market_value_mismatch"]:
        shift = float(rng.uniform(0.01, 0.05)) * rng.choice([-1, 1])
        accounting.at[i, "market_value"] = round(accounting.at[i, "market_value"] * (1 + shift), 2)
        truth.append((accounting.at[i, "cusip"], "market_value_mismatch", f"MV shifted {shift:+.2%}"))

    # --- Accrued interest: accounting accrued 2-10 extra days (cutoff / day-count)
    for i in picks["accrued_interest_mismatch"]:
        cpn = master.loc[master.cusip == custodian.at[i, "cusip"], "coupon_rate"].iat[0]
        days = int(rng.integers(2, 11))
        one_day = custodian.at[i, "par_value"] * cpn / 100 / 360
        accounting.at[i, "accrued_interest"] = round(accounting.at[i, "accrued_interest"] + days * one_day, 2)
        truth.append((accounting.at[i, "cusip"], "accrued_interest_mismatch", f"+{days} days accrual"))

    # --- Rating: accounting's reference data is stale (one notch off the master)
    for i in picks["rating_mismatch"]:
        r = RATINGS.index(accounting.at[i, "rating"])
        new = RATINGS[r + 1] if r + 1 < len(RATINGS) else RATINGS[r - 1]
        accounting.at[i, "rating"] = new
        truth.append((accounting.at[i, "cusip"], "rating_mismatch", f"accounting shows {new}"))

    # --- Timing: accounting loaded yesterday's file (as-of date and one day less accrual)
    for i in picks["timing_difference"]:
        cpn = master.loc[master.cusip == custodian.at[i, "cusip"], "coupon_rate"].iat[0]
        one_day = custodian.at[i, "par_value"] * cpn / 100 / 360
        accounting.at[i, "as_of_date"] = PRIOR_DAY.isoformat()
        accounting.at[i, "accrued_interest"] = round(accounting.at[i, "accrued_interest"] - one_day, 2)
        truth.append((accounting.at[i, "cusip"], "timing_difference", "as_of_date = prior day"))

    # --- Decoys: rounding noise under $1 -- a correct tolerance must ignore these
    for i in picks["decoy"]:
        field = rng.choice(["market_value", "book_value", "accrued_interest"])
        cents = int(rng.integers(1, 100)) * rng.choice([-1, 1])
        accounting.at[i, field] = round(accounting.at[i, field] + cents / 100, 2)
        truth.append((accounting.at[i, "cusip"], "decoy", f"{field} off by ${cents/100:+.2f}"))

    # --- Duplicates: the feed double-loaded these rows into accounting
    dup_rows = accounting.loc[picks["duplicate_position"]]
    for c in dup_rows.cusip:
        truth.append((c, "duplicate_position", "row loaded twice"))

    # --- Missing in accounting: trade never booked / feed failure
    for i in picks["missing_in_accounting"]:
        truth.append((accounting.at[i, "cusip"], "missing_in_accounting", "row dropped"))
    accounting = accounting.drop(index=picks["missing_in_accounting"])

    # --- Missing at custodian: accounting still carries positions the custodian doesn't hold
    for c in stale_only_in_accounting.cusip:
        truth.append((c, "missing_at_custodian", "only in accounting"))

    accounting = pd.concat([accounting, dup_rows, stale_only_in_accounting], ignore_index=True)

    # Real feeds don't arrive sorted -- shuffle row order in both files
    custodian = custodian.sample(frac=1, random_state=SEED).reset_index(drop=True)
    accounting = accounting.sample(frac=1, random_state=SEED + 1).reset_index(drop=True)

    truth_df = pd.DataFrame(truth, columns=["cusip", "break_type", "detail"]).sort_values(["break_type", "cusip"])

    master.to_csv(DATA / "security_master.csv", index=False)
    custodian.to_csv(DATA / "custodian.csv", index=False)
    accounting.to_csv(DATA / "accounting.csv", index=False)
    truth_df.to_csv(DATA / "breaks_truth.csv", index=False)

    print(f"security_master.csv : {len(master)} securities")
    print(f"custodian.csv       : {len(custodian)} rows")
    print(f"accounting.csv      : {len(accounting)} rows")
    print(f"breaks_truth.csv    : {len(truth_df)} planted items")
    print(truth_df.break_type.value_counts().to_string())


if __name__ == "__main__":
    main()
