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


def run_sql(conn: sqlite3.Connection, sql: str):
    """Execute and reduce to a single number, or None."""
    if not is_safe_select(sql):
        return None
    try:
        row = conn.execute(sql).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    for value in row:                         # first numeric cell wins
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.replace(",", ""))
            except ValueError:
                continue
    return None


def _prompt(card: str, question: str, answer_type: str) -> list[dict]:
    expectations = {
        "money": "a rupee amount as a plain number",
        "count": "a whole number",
        "percent": "a percentage out of 100",
        "days": "a whole number of days",
    }.get(answer_type, "a plain number")
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content":
            f"{card}\n\nQuestion ({answer_type}, expects {expectations}):\n"
            f"{question}\n\nWrite the SQL."},
    ]


def _quantise(value: float, answer_type: str) -> float:
    """Round to the precision the grader compares at, so votes can agree."""
    if answer_type == "percent":
        return round(value, 2)
    return float(round(value))


def answer_once(db_path: Path, card: str, question: str,
                answer_type: str, temperature: float) -> tuple[float | None, str]:
    payload = chat_json(_prompt(card, question, answer_type), SQL_SCHEMA,
                        temperature=temperature)
    if not payload:
        return None, ""
    sql = (payload.get("sql") or "").strip()
    conn = open_readonly(db_path)
    try:
        return run_sql(conn, sql), sql
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
