"""
llm_sql.py — answer a question by having the model write SQL against our schema.

Why SQL rather than retrieval-and-read: the questions are numeric aggregations
over a whole document estate, and the scoring tier that breaks ties explicitly
rewards "exhaustively covering many documents and exact arithmetic across
them". Chunk retrieval reliably misses documents and the model does the
arithmetic itself; a GROUP BY either covers every row or none, and SQLite does
the sums. So the model's job here is comprehension — which rows, which filter —
and never mental arithmetic on crore figures.

The model never sees the database, only the card. It cannot write to it either:
queries run on a read-only connection and are rejected unless they are a single
SELECT.

Self-consistency is on the *numeric result*, not the SQL text. Two correct
queries over the same rows can be spelled entirely differently, so comparing
strings would throw away agreement that is real.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter
from pathlib import Path

from src.llm import chat_json, map_concurrent

SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string",
                      "description": "One sentence: which rows and which filter."},
        "sql": {"type": "string",
                "description": "A single SQLite SELECT returning exactly one numeric value."},
    },
    "required": ["reasoning", "sql"],
    "additionalProperties": False,
}

SYSTEM = """\
You are a precise data analyst answering questions about a construction \
company's records, which have already been extracted from its documents into a \
SQLite database.

Rules:
- Answer only with a single SQLite SELECT that returns exactly ONE row and ONE \
numeric column.
- Do all arithmetic in SQL. Never compute a figure yourself.
- Filter on values exactly as they are spelled in the schema card.
- A question that names an engineer and a project means: find that project, \
take its client, then aggregate over that client's works.
- A question that names an engineer with no project means: aggregate over that \
engineer's own works, unless it explicitly asks about "the client".
- Never invent a table or a column. If something is genuinely unavailable, \
return the closest defensible aggregate rather than nothing.
"""

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|vacuum|replace)\b",
    re.I)


def is_safe_select(sql: str) -> bool:
    """A single read-only SELECT, nothing else."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped.lower().startswith(("select", "with")):
        return False
    if ";" in stripped:                       # no statement chaining
        return False
    return not _FORBIDDEN.search(stripped)


def open_readonly(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)


def run_sql(conn: sqlite3.Connection, sql: str) -> tuple[float | None, str]:
    """Execute and reduce to a single number. Returns (value, problem)."""
    if not is_safe_select(sql):
        return None, "not a single read-only SELECT"
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error as exc:
        return None, str(exc)
    if row is None:
        return None, "query matched no rows"
    for value in row:                         # first numeric cell wins
        if isinstance(value, (int, float)):
            return float(value), ""
        if isinstance(value, str):
            try:
                return float(value.replace(",", "")), ""
            except ValueError:
                continue
    return None, "query returned no numeric column"


def implausible(value: float, answer_type: str) -> str:
    """Catch an answer that cannot be right for its declared type."""
    if value != value or value in (float("inf"), float("-inf")):
        return "not a finite number"
    if answer_type == "percent" and not (-0.01 <= value <= 100.01):
        return f"{value} is not a percentage out of 100"
    if answer_type in ("count", "days") and value < 0:
        return f"{value} cannot be negative for a {answer_type}"
    return ""


# Worked examples, chosen to teach the joins and the conventions rather than
# any particular answer: client aggregation through the join, an engineer's own
# works, a date difference in days, and a percentage computed in SQL.
EXAMPLES = """\
Worked examples of the shape of a good answer:

Q: What is the total completed contract value for Trishakti Power Generation Corporation?
SQL: SELECT SUM(w.contract_value) FROM works w JOIN clients cl ON cl.client_id = w.client_id
     WHERE cl.client_name = 'Trishakti Power Generation Corporation'

Q: How many distinct work categories has Pooja Sen completed?
SQL: SELECT COUNT(DISTINCT w.work_category) FROM works w
     JOIN engineer_works ew ON ew.work_id = w.work_id
     JOIN engineers e ON e.engineer_id = ew.engineer_id WHERE e.name = 'Pooja Sen'

Q: How many days from a person's PMP issue date to the completion of a named project?
SQL: SELECT CAST(julianday(w.completion_date) - julianday(c.issue_date) AS INTEGER)
     FROM works w JOIN engineer_certs c ON UPPER(c.cert_type) = 'PMP'
     JOIN engineers e ON e.engineer_id = c.engineer_id
     WHERE w.project_name = 'STP - Odisha Pkg-45' AND e.name = 'Naveen Roy'

Q: What percentage of invoiced value has been received from Jal Nigam, Gujarat?
SQL: SELECT SUM(received) * 100.0 / SUM(invoiced) FROM receivables
     WHERE client_name = 'Jal Nigam, Gujarat'
"""


def _prompt(card: str, question: str, answer_type: str,
            failed_sql: str = "", error: str = "") -> list[dict]:
    expectations = {
        "money": "a rupee amount as a plain number",
        "count": "a whole number",
        "percent": "a percentage out of 100",
        "days": "a whole number of days",
    }.get(answer_type, "a plain number")
    body = (f"{card}\n{EXAMPLES}\n"
            f"Question ({answer_type}, expects {expectations}):\n{question}\n\n"
            "Write the SQL.")
    if failed_sql:
        # A second attempt that can see what went wrong is worth far more than
        # a second sample of the same guess. Most failures are a mistyped column
        # or a table that does not exist, both of which the error names exactly.
        body += (f"\n\nA previous attempt failed. Do not repeat it.\n"
                 f"SQL: {failed_sql}\nProblem: {error}\n"
                 f"Use only tables and columns listed in the schema above.")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": body},
    ]


def _quantise(value: float, answer_type: str) -> float:
    """Round to the precision the grader compares at, so votes can agree."""
    if answer_type == "percent":
        return round(value, 2)
    return float(round(value))


def answer_once(db_path: Path, card: str, question: str,
                answer_type: str, temperature: float,
                repairs: int = 1) -> tuple[float | None, str]:
    """One attempt, plus a repair pass that gets to see what went wrong."""
    failed_sql, problem = "", ""
    conn = open_readonly(db_path)
    try:
        for attempt in range(repairs + 1):
            payload = chat_json(
                _prompt(card, question, answer_type, failed_sql, problem),
                SQL_SCHEMA, temperature=temperature)
            if not payload:
                return None, ""
            sql = (payload.get("sql") or "").strip()
            value, problem = run_sql(conn, sql)
            if value is not None:
                problem = implausible(value, answer_type)
                if not problem:
                    return value, sql
            failed_sql = sql
        return None, failed_sql
    finally:
        conn.close()


def answer_question_sql(db_path: Path, card: str, question: str,
                        answer_type: str, samples: int = 3) -> dict:
    """Sample the model a few times and vote on the resulting number."""
    # Temperature 0 for the first sample (the model's best single answer), then
    # a little spread so the extra samples are genuinely independent rather
    # than three copies of the same mistake.
    temperatures = [0.0, 0.4, 0.7, 0.9, 1.0][:max(1, samples)]
    results = map_concurrent(
        lambda t: answer_once(db_path, card, question, answer_type, t),
        temperatures)

    values = [(_quantise(v, answer_type), s) for v, s in results if v is not None]
    if not values:
        return {"value": None, "votes": 0, "samples": len(temperatures), "sql": ""}

    tally = Counter(v for v, _ in values)
    winner, votes = tally.most_common(1)[0]
    sql = next(s for v, s in values if v == winner)
    return {"value": winner, "votes": votes, "samples": len(temperatures),
            "sql": sql, "agreement": votes / len(temperatures)}
