"""
run_sql.py
Loads the CSVs into SQLite (data/recon.db), runs each named query in
sql/reconcile.sql, and checks the SQL break counts against the Python
exception log. Any difference would itself be a finding.
"""
import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
SQL_FILE = ROOT / "sql" / "reconcile.sql"


def parse_blocks(text: str) -> dict[str, str]:
    """Split the .sql file on '-- name: <x>' markers."""
    blocks, name, lines = {}, None, []
    for line in text.splitlines():
        if line.startswith("-- name:"):
            if name:
                blocks[name] = "\n".join(lines)
            name, lines = line.split(":", 1)[1].strip(), []
        elif name:
            lines.append(line)
    if name:
        blocks[name] = "\n".join(lines)
    return blocks


def main() -> None:
    con = sqlite3.connect(DATA / "recon.db")
    for table in ["security_master", "custodian", "accounting"]:
        df = pd.read_csv(DATA / f"{table}.csv", dtype={"cusip": str, "position_id": str})
        df.to_sql(table, con, if_exists="replace", index=False)

    blocks = parse_blocks(SQL_FILE.read_text())
    con.executescript(blocks.pop("setup"))

    impact = pd.read_sql(blocks.pop("mv_dollar_impact"), con)
    sql_counts = {}
    for name, query in blocks.items():
        result = pd.read_sql(query, con)
        for bt, n in result.break_type.value_counts().items():
            sql_counts[bt] = sql_counts.get(bt, 0) + n
        print(f"-- {name}: {len(result)} rows")

    py_counts = pd.read_csv(OUTPUT / "exception_log.csv").break_type.value_counts().to_dict()
    types = sorted(set(sql_counts) | set(py_counts))
    compare = pd.DataFrame({
        "break_type": types,
        "python": [py_counts.get(t, 0) for t in types],
        "sql": [sql_counts.get(t, 0) for t in types],
    })
    compare["match"] = compare.python == compare.sql

    print("\n=== Python vs SQL ===")
    print(compare.to_string(index=False))
    print(f"\nMV dollar impact (SQL): abs={impact.abs_impact[0]:,.2f}  net={impact.net_impact[0]:,.2f}")
    print("ALL MATCH" if compare.match.all() else "MISMATCH -- investigate")
    compare.to_csv(OUTPUT / "python_vs_sql.csv", index=False)
    con.close()


if __name__ == "__main__":
    main()
