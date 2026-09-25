"""Isolated PyMuPDF table extraction.

Runs in a subprocess because pymupdf4llm's layout module alters process-wide PyMuPDF state, after
which `Page.find_tables` stops detecting ruled tables. Output: JSON list of [label, value] rows.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def extract_table_rows(page, strategy: str, forward_fill: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    try:
        tables = page.find_tables(strategy=strategy).tables
    except Exception:
        return rows
    for t in tables:
        prev_value = ""
        for raw in t.extract():
            cells = [(c or "").strip() for c in raw]
            if not any(cells):
                continue
            label = cells[0]
            values = [c for c in cells[1:] if c]
            # split-cell repair: a cell broken vertically into two fragments is re-joined line by line
            if len(values) == 2 and values[0].count("\n") > 0 and values[1][:1].islower():
                a_lines, b_lines = values[0].split("\n"), values[1].split("\n")
                b_lines += [""] * (len(a_lines) - len(b_lines))
                value = "\n".join(a.rstrip() + b for a, b in zip(a_lines, b_lines))
            else:
                value = " ".join(values)
            value = " ".join(value.split())
            label = " ".join(label.split())
            if not label and value and not rows:
                label = "Plan Name"
            if not value and forward_fill and prev_value and label:
                value = prev_value
            if value:
                prev_value = value
            if label or value:
                rows.append([label, value])
    return rows


def extract_table_rows_isolated(pdf_path: Path, page_number: int, strategy: str, forward_fill: bool) -> list[list[str]]:
    cmd = [sys.executable, "-m", "app.policies.table_extract", str(pdf_path), str(page_number), strategy, "1" if forward_fill else "0"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).resolve().parents[2]))
        if proc.returncode != 0:
            return []
        return json.loads(proc.stdout.strip() or "[]")
    except Exception:
        return []


if __name__ == "__main__":
    import pymupdf

    path, pno, strat, ff = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4] == "1"
    doc = pymupdf.open(path)
    print(json.dumps(extract_table_rows(doc[pno - 1], strat, ff)))
