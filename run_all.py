"""Runs the whole pipeline in order: generate -> reconcile -> score -> SQL -> Excel."""
import subprocess
import sys

STEPS = ["generate_data", "reconcile", "score", "run_sql", "export_excel"]

for step in STEPS:
    print(f"\n{'=' * 20} {step}.py {'=' * 20}")
    subprocess.run([sys.executable, f"src/{step}.py"], check=True)
