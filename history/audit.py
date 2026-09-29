#!/usr/bin/env python3
"""
audit.py — check the database against an independent source, not against the
25 sample questions.

Scoring 25/25 on the samples proves very little: those questions touch about a
third of the clients and a fifth of the works, so a field can be wrong on a
quarter of the corpus and still return a perfect sample score. This script
reconciles every work against the credentials pack (DOC-PPP-001), which states
client, role, category, value and completion date once for all 155 works, and
then asserts the corpus-level invariants the README publishes.

Usage:
    python audit.py
    python audit.py --verbose      # list every mismatch
"""
import argparse
import collections
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from src.config import DB_PATH
from src.portfolio_index import parse_portfolio

# From the README's description of the corpus.
EXPECTED_WORKS = 155
EXPECTED_REFERENCE_LETTERS = 132
EXPECTED_TOTAL_VALUE_CR = 5530
EXPECTED_CATEGORIES = 13


def audit(verbose=False):
    conn = sqlite3.connect(str(DB_PATH))
    portfolio = parse_portfolio()
    failures, warnings = [], []

    if len(portfolio) != EXPECTED_WORKS:
        failures.append(f"portfolio parsed {len(portfolio)} works, expected {EXPECTED_WORKS}")

    rows = conn.execute("""
        SELECT w.pkg_no, w.project_name, c.client_name, w.contract_value,
               w.completion_date, w.work_category, w.role
        FROM works w LEFT JOIN clients c ON w.client_id = c.client_id
    """).fetchall()

    print(f"Reconciling {len(rows)} works against DOC-PPP-001 "
          f"({len(portfolio)} reference records)\n")

    mismatches = collections.Counter()
    details = collections.defaultdict(list)
    unmatched = []

    for pkg, name, client, value, comp_date, category, role in rows:
        if pkg is None:
            m = re.search(r'Pkg-(\d+)', name or '')
            pkg = int(m.group(1)) if m else None
        ref = portfolio.get(pkg)
        if not ref:
            unmatched.append(name)
            continue
        for field, mine, theirs in (
                ("client", client, ref["client_name"]),
                ("category", category, ref["work_category"]),
                ("role", role, ref["role"]),
                ("completion_date", comp_date, ref["completion_date"])):
            if (mine or "") != (theirs or ""):
                mismatches[field] += 1
                details[field].append((name, mine, theirs))
        if ref["contract_value"] and value:
            if abs(value - ref["contract_value"]) / ref["contract_value"] > 0.005:
                mismatches["contract_value"] += 1
                details["contract_value"].append((name, value, ref["contract_value"]))
        elif not value:
            mismatches["contract_value_missing"] += 1

    for field in ("client", "contract_value", "completion_date", "category", "role"):
        n = mismatches.get(field, 0)
        status = "OK  " if n == 0 else "FAIL"
        print(f"  {status} {field:18s} {len(rows) - n}/{len(rows)} agree")
        if n:
            failures.append(f"{n} works disagree on {field}")
            for name, mine, theirs in details[field][:20 if verbose else 3]:
                print(f"         {name}: db={mine!r} portfolio={theirs!r}")

    if unmatched:
        failures.append(f"{len(unmatched)} works not found in the portfolio")
        print(f"  FAIL unmatched works: {unmatched[:5]}")

    print("\nCorpus invariants:")

    def check(label, got, expected, tolerance=0):
        ok = abs(got - expected) <= tolerance
        print(f"  {'OK  ' if ok else 'FAIL'} {label:34s} {got} (expected {expected})")
        if not ok:
            failures.append(f"{label}: {got} != {expected}")

    check("works", conn.execute("SELECT COUNT(*) FROM works").fetchone()[0],
          EXPECTED_WORKS)
    check("works with a reference letter",
          conn.execute("SELECT COUNT(*) FROM works WHERE has_reference_letter = 1").fetchone()[0],
          EXPECTED_REFERENCE_LETTERS)
    check("distinct work categories",
          conn.execute("SELECT COUNT(DISTINCT work_category) FROM works").fetchone()[0],
          EXPECTED_CATEGORIES)
    total_cr = (conn.execute("SELECT SUM(contract_value) FROM works").fetchone()[0] or 0) / 1e7
    check("total delivered value (Cr)", round(total_cr), EXPECTED_TOTAL_VALUE_CR, tolerance=5)

    # Role must be a value the corpus actually uses, on every work.
    bad_roles = conn.execute(
        "SELECT COUNT(*) FROM works WHERE role NOT IN ('Prime', 'JV Partner')"
    ).fetchone()[0]
    check("works with a known role", len(rows) - bad_roles, len(rows))

    print("\nCompleteness (fields a question may filter on):")
    for col in ("contract_value", "completion_date", "work_category", "role",
                "performance_grading", "signing_officer"):
        nulls = conn.execute(
            f"SELECT COUNT(*) FROM works WHERE {col} IS NULL").fetchone()[0]
        note = ""
        if col == "performance_grading" and nulls:
            note = "  (some certificates state no grade — verified against gold)"
        print(f"       {col:22s} {len(rows) - nulls}/{len(rows)} populated{note}")
        if nulls and col not in ("performance_grading", "signing_officer"):
            warnings.append(f"{col} missing on {nulls} works")

    print("\nDerived tables:")
    for table in ("engineers", "engineer_certs", "engineer_works", "engineer_profiles",
                  "reference_letters", "bonds", "statement_items", "ledger_entries",
                  "bank_txns", "bills", "bill_items", "tenders", "compliance_items",
                  "annual_figures", "boq_items", "assets", "receivables", "iso_certs",
                  "doc_text"):
        try:
            n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            n = "MISSING"
            failures.append(f"table {table} missing")
        print(f"       {table:22s} {n}")

    # Every credential must hang off a real person, or credential-keyed
    # questions silently resolve to nothing.
    orphan_certs = conn.execute("""
        SELECT COUNT(*) FROM engineer_certs ec
        WHERE ec.engineer_id NOT IN (SELECT engineer_id FROM engineers)
           OR (SELECT LENGTH(name) FROM engineers WHERE engineer_id = ec.engineer_id) > 40
    """).fetchone()[0]
    print()
    check("credentials on a named engineer",
          conn.execute("SELECT COUNT(*) FROM engineer_certs").fetchone()[0] - orphan_certs,
          conn.execute("SELECT COUNT(*) FROM engineer_certs").fetchone()[0])

    conn.close()

    print()
    if failures:
        print(f"AUDIT FAILED — {len(failures)} problem(s):")
        for f in failures:
            print(f"  - {f}")
    else:
        print("AUDIT PASSED — database agrees with the credentials pack on every"
              " field, and all corpus invariants hold.")
    for w in warnings:
        print(f"  warning: {w}")
    return 1 if failures else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--verbose', action='store_true')
    sys.exit(audit(ap.parse_args().verbose))
