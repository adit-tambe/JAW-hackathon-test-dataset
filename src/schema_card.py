"""
schema_card.py — describe the database to the model.

Text-to-SQL is only as good as the description of the schema it is given, and
the failure mode that matters here is not bad SQL but plausible SQL over the
wrong column. So the card carries three things beyond the column list:

  * the vocabulary — the exact spelling of every client, category and role, so
    the model matches on values that exist rather than ones it invents;
  * a worked row per table, so column meaning is not guessed from its name;
  * the conventions this company's figures actually follow, which we measured
    against the leaderboard rather than assumed.

Everything is introspected. Nothing here names a table we happen to have today,
so a richer document estate simply produces a richer card.
"""
from __future__ import annotations

import re
import sqlite3

# Low-cardinality columns worth enumerating in full: the model needs the exact
# spelling to filter on them.
MAX_ENUM_VALUES = 60

CONVENTIONS = """\
Conventions this company's figures follow (measured, not assumed):
  - A "work" is one completed contract. works.contract_value is in rupees.
  - Group works by client NAME. A client may appear under several
    client_office numbers; those are issuing offices, not separate clients.
  - Difference between two categories, or between two years: absolute value.
  - Mean-minus-median gap: signed (negative when the mean is lower). Median of
    an even count is the average of the two middle values.
  - Unbilled gap = |sum(contract_value) - sum(invoiced)|, absolute.
  - Outstanding balance = SUM(receivables.outstanding), signed. Some invoices
    are over-received and carry a negative outstanding; those net off rather
    than being dropped, and a client can legitimately total negative.
  - Collection percent = sum(received) / sum(invoiced) * 100.
    receivables.outstanding = invoiced - received holds for every row.
  - Threshold filters are inclusive: "clearing the X mark" means >= X.
  - Day counts are exclusive: (completion_date - issue_date) in days.
  - 1 crore = 10,000,000 rupees. 1 lakh = 100,000 rupees.
"""


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _column_info(conn: sqlite3.Connection, table: str) -> list[tuple[str, str]]:
    return [(r[1], r[2]) for r in conn.execute(f'PRAGMA table_info("{table}")')]


_DATEISH = re.compile(r"^\d{4}-\d{2}(-\d{2})?$|^\d{4}$")
_IDISH = re.compile(r"^[A-Z]{2,}[-/][A-Z0-9\-/]+$")


def _distinct(conn: sqlite3.Connection, table: str, column: str) -> list[str] | None:
    """Enumerate a text column when it is small enough to be a vocabulary.

    Identifiers and dates are excluded. Listing every doc_id teaches the model
    nothing it can filter on and crowds out the vocabulary that matters — the
    exact spelling of client names, categories and roles.
    """
    lowered = column.lower()
    if lowered == "doc_id" or lowered.endswith("_id") or "date" in lowered:
        return None
    try:
        n = conn.execute(
            f'SELECT COUNT(DISTINCT "{column}") FROM "{table}"').fetchone()[0]
    except sqlite3.Error:
        return None
    if not n or n > MAX_ENUM_VALUES:
        return None
    rows = conn.execute(
        f'SELECT DISTINCT "{column}" FROM "{table}" '
        f'WHERE "{column}" IS NOT NULL ORDER BY 1').fetchall()
    values = [str(r[0]) for r in rows]
    # Only useful if the values are short labels, not free text or keys.
    if any(len(v) > 70 for v in values):
        return None
    if values and all(_DATEISH.match(v) or _IDISH.match(v) for v in values):
        return None
    return values


def build_card(conn: sqlite3.Connection, include_samples: bool = True) -> str:
    """Render the schema, vocabulary and conventions as prompt text."""
    parts: list[str] = ["SQLite schema. Tables, columns and row counts:\n"]

    for table in _tables(conn):
        try:
            count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except sqlite3.Error:
            continue
        if count == 0:
            continue                      # an empty table is only a distraction
        columns = _column_info(conn, table)
        cols = ", ".join(f"{name} {ctype or ''}".strip() for name, ctype in columns)
        parts.append(f"\nTABLE {table}  ({count} rows)\n  {cols}")

        if include_samples:
            try:
                row = conn.execute(f'SELECT * FROM "{table}" LIMIT 1').fetchone()
            except sqlite3.Error:
                row = None
            if row:
                pairs = ", ".join(
                    f"{name}={str(value)[:44]!r}"
                    for (name, _), value in zip(columns, row) if value is not None)
                parts.append(f"  e.g. {pairs}")

        for name, ctype in columns:
            if ctype and ctype.upper() not in ("TEXT", "VARCHAR", ""):
                continue
            values = _distinct(conn, table, name)
            if values and len(values) > 1:
                parts.append(f"  {name} is one of: " + " | ".join(values))

    parts.append("\n" + CONVENTIONS)
    return "\n".join(parts)


if __name__ == "__main__":                                  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.config import DB_PATH
    print(build_card(sqlite3.connect(DB_PATH)))
