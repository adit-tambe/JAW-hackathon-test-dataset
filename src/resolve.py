"""
resolve.py — decide a final answer from two independent systems.

We have a deterministic engine that classifies a question into a shape and runs
a hand-written query for it, and a model that writes SQL from a schema card.
They fail in opposite directions, which is the whole point of running both:

  * the engine is exact where it recognises the question and blind where it does
    not. Its triggers were written against a question set we had in front of us,
    so a new phrasing can miss — or worse, land on a neighbouring shape;
  * the model reads intent rather than phrases, so it degrades gracefully on
    wording it has never seen, but it is likelier to aggregate over slightly the
    wrong rows.

Agreement between them is strong evidence. Disagreement is where the errors
live, so those are escalated rather than silently resolved in either system's
favour. There is no partial credit this round: a near-miss scores the same as
a wild guess, which makes an adjudicated disagreement worth the extra call.

If the endpoint is unavailable the engine answers alone, which is exactly the
system that has been verified against source documents question by question.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from src.answer_engine import answer_question, parse_question, reconcile_shape
from src.llm import chat_json
from src.llm_intent import classify, merge_params, run_shape
from src.llm_sql import answer_question_sql

_CATEGORY_CACHE: dict[int, list[str]] = {}


def _categories(conn: sqlite3.Connection) -> list[str]:
    """The exact spelling of every work category, so the model cannot invent one."""
    key = id(conn)
    if key not in _CATEGORY_CACHE:
        try:
            rows = conn.execute(
                "SELECT DISTINCT work_category FROM works "
                "WHERE work_category IS NOT NULL ORDER BY 1").fetchall()
            _CATEGORY_CACHE[key] = [r[0] for r in rows]
        except sqlite3.Error:
            _CATEGORY_CACHE[key] = []
    return _CATEGORY_CACHE[key]


def _params(conn: sqlite3.Connection, question: str, answer_type: str) -> dict | None:
    try:
        params = parse_question(conn, question)
        params["answer_type"] = answer_type
        return params
    except Exception:
        return None

# What each shape needs before its answer can be trusted. A shape whose
# parameters did not resolve produced a fallback, not an answer.
REQUIRED: dict[str, tuple[str, ...]] = {
    "category_difference":  ("client_name", "cat1", "cat2"),
    "yearly_diff":          ("client_name", "year1", "year2"),
    "exclusion_aggregate":  ("client_name", "exclude_category"),
    "threshold_aggregate":  ("client_name",),
    "gap_to_threshold":     ("client_name",),
    "general_aggregate":    ("client_name",),
    "avg_work_size":        ("client_name",),
    "mean_median_diff":     ("client_name",),
    "rank_value":           ("client_name",),
    "referenced_share":     ("client_name",),
    "absence":              ("client_name",),
    "collection_percent":   ("client_name",),
    "outstanding_balance":  ("client_name",),
    "unbilled_gap":         ("client_name",),
    "distinct_count":       ("engineer_name",),
    "temporal_chain":       ("engineer_name",),
    "date_span":            ("project_name",),
    "role_split":           ("client_name",),
    "doc_filtered_aggregate": ("client_name",),
}

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "choice": {"type": "string", "enum": ["A", "B"]},
        "why": {"type": "string"},
    },
    "required": ["choice", "why"],
    "additionalProperties": False,
}


def within_tolerance(a: float, b: float, answer_type: str) -> bool:
    """The grader's own tolerance, so 'agreement' means what it means to them."""
    if a is None or b is None:
        return False
    if answer_type == "percent":
        return abs(a - b) <= 0.05
    if answer_type in ("count", "days"):
        return round(a) == round(b)
    return abs(a - b) <= max(1.0, abs(b) * 0.005)


def deterministic(conn: sqlite3.Connection, question: str, qid: str,
                  answer_type: str) -> dict:
    """Run the shape engine and report whether it actually recognised anything."""
    try:
        params = parse_question(conn, question)
        shape = reconcile_shape(params["question_shape"], answer_type, params)
    except Exception:
        return {"value": None, "shape": "error", "confident": False}

    try:
        value = answer_question(conn, question, qid, answer_type)
    except Exception:
        value = None

    needed = REQUIRED.get(shape, ())
    resolved = all(params.get(k) not in (None, "") for k in needed)
    # A zero is not an answer here. Every shape in this set aggregates over rows
    # that exist, so zero means a filter matched nothing — a missing threshold,
    # a category that never resolved — rather than a genuine total. Treating it
    # as unconfident hands it to the model instead of shipping it.
    hollow = value is not None and float(value) == 0.0
    confident = bool(value is not None and not hollow
                     and shape != "other" and needed and resolved)
    return {"value": None if value is None else float(value),
            "shape": shape, "confident": confident,
            "missing": [k for k in needed if params.get(k) in (None, "")]}


def _judge(question: str, answer_type: str, engine: dict, llm: dict,
           card: str) -> str | None:
    """Break a tie with a third look that sees both candidates."""
    payload = chat_json(
        [{"role": "system", "content":
            "You are auditing two candidate answers to a question about a "
            "construction company's records. Exactly one is correct. There is "
            "no partial credit, so pick the one whose method matches what the "
            "question actually asks for."},
         {"role": "user", "content":
            f"{card}\n\nQuestion ({answer_type}):\n{question}\n\n"
            f"Candidate A came from a rule that classified this question as "
            f"'{engine['shape']}' and returned {engine['value']}.\n\n"
            f"Candidate B came from this SQL:\n{llm.get('sql', '')}\n"
            f"and returned {llm['value']}.\n\n"
            "Which is correct?"}],
        JUDGE_SCHEMA)
    return (payload or {}).get("choice")


def resolve(conn: sqlite3.Connection, db_path: Path, card: str, question: str,
            qid: str, answer_type: str, use_llm: bool = True,
            samples: int = 3, judge: bool = True) -> dict:
    """Produce one final answer, and say how it was reached."""
    engine = deterministic(conn, question, qid, answer_type)

    if not use_llm:
        return {"value": engine["value"], "route": "engine-only",
                "shape": engine["shape"], "engine": engine, "llm": None}

    # First ask the cheaper, easier question: what is this asking for? Naming
    # the calculation is a much lower bar than writing SQL for it, and it aims
    # straight at the engine's weak spot, which is recognising an unfamiliar
    # phrasing rather than computing the answer once recognised.
    intent = classify(question, answer_type, _categories(conn))
    llm_shape = (intent or {}).get("shape")
    if llm_shape == "other":
        llm_shape = None

    if engine["confident"]:
        # The engine recognised the question and every parameter it needs
        # resolved. That path reproduces the sample gold exactly and was checked
        # against the source documents question by question; the model's
        # classifier has no comparable record. So a disagreement here is logged,
        # not acted on. Overriding a verified answer on an unverified opinion is
        # a losing trade when there is no partial credit.
        route = "engine"
        if llm_shape and llm_shape != engine["shape"]:
            route = f"engine (model said {llm_shape})"
        return {"value": engine["value"], "route": route,
                "shape": engine["shape"], "engine": engine,
                "llm": {"shape": llm_shape}}

    # The engine did not recognise this question, or a parameter it needed never
    # resolved. Whatever it returned is a fallback, so there is nothing to
    # protect and the model can only help.
    if llm_shape:
        params = _params(conn, question, answer_type)
        if params is not None and intent:
            # The engine extracts parameters with the same phrase rules that
            # classify the shape, so an unfamiliar wording usually loses both at
            # once. Rerouting without also filling the gap would aggregate the
            # whole portfolio and look plausible.
            params = merge_params(params, intent, _categories(conn))
        # A shape that cannot produce the declared kind of number is the wrong
        # shape, whoever proposed it: a percent question answered with a crore
        # figure scores zero either way.
        compatible = (params is not None
                      and reconcile_shape(llm_shape, answer_type, params) == llm_shape)
        rerouted = run_shape(conn, params, llm_shape) if compatible else None
        if rerouted is not None:
            return {"value": float(rerouted),
                    "route": f"rescued {engine['shape']}->{llm_shape}",
                    "shape": llm_shape, "engine": engine,
                    "llm": {"shape": llm_shape, "value": float(rerouted),
                            "why": (intent or {}).get("why", "")}}

    # Spend adaptively. One sample is enough to confirm a confident engine, and
    # confirmation is the common case; the extra samples are worth their cost
    # only where the two systems actually part company. The endpoint is shared
    # across finalists during the window, so a call not made is time not lost.
    llm = answer_question_sql(db_path, card, question, answer_type, samples=1)
    cheap_agreement = (llm.get("value") is not None
                       and engine["value"] is not None
                       and within_tolerance(engine["value"], llm["value"], answer_type))
    if not cheap_agreement and samples > 1:
        llm = answer_question_sql(db_path, card, question, answer_type,
                                  samples=samples)

    # The endpoint gave us nothing usable.
    if llm.get("value") is None:
        return {"value": engine["value"], "route": "engine (llm unavailable)",
                "shape": engine["shape"], "engine": engine, "llm": llm}

    # Nothing for the engine to say.
    if engine["value"] is None:
        return {"value": llm["value"], "route": "llm (engine had no answer)",
                "shape": engine["shape"], "engine": engine, "llm": llm}

    if within_tolerance(engine["value"], llm["value"], answer_type):
        return {"value": engine["value"], "route": "agreed",
                "shape": engine["shape"], "engine": engine, "llm": llm}

    # They disagree. Where the engine recognised nothing, it has no standing and
    # the model is all we have.
    if not engine["confident"]:
        return {"value": llm["value"], "route": "llm (engine unsure)",
                "shape": engine["shape"], "engine": engine, "llm": llm}

    # Where the engine *did* recognise the question it is the stronger system:
    # every shape it resolves has been checked against the source documents, and
    # it reproduces the sample gold exactly. So overturning it needs real
    # evidence, not merely a differing opinion. A split vote is the model
    # hedging, and hedging must not cost us a question the engine had right.
    unanimous = (llm.get("samples", 1) > 1
                 and llm.get("votes", 0) == llm.get("samples"))
    if judge and unanimous:
        choice = _judge(question, answer_type, engine, llm, card)
        if choice == "B":
            return {"value": llm["value"], "route": "judged->llm",
                    "shape": engine["shape"], "engine": engine, "llm": llm}
        if choice == "A":
            return {"value": engine["value"], "route": "judged->engine",
                    "shape": engine["shape"], "engine": engine, "llm": llm}

    return {"value": engine["value"], "route": "engine (model split)"
            if not unanimous else "engine (unadjudicated)",
            "shape": engine["shape"], "engine": engine, "llm": llm}
