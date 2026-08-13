"""
llm_intent.py — have the model name what a question is asking for.

The deterministic engine decides that by matching phrases, and phrases were what
we had in front of us when we wrote them. A question that means "what is still
unpaid" but says it in words we never saw either falls through to a generic
aggregate or, worse, trips a neighbouring rule and is answered confidently and
wrongly. That is the single biggest generalisation risk in the system.

So this asks the model for one thing only: which of our shapes does this
question want? That is a far easier task than writing SQL, and it plays to each
system's strength — the model reads intent, and the arithmetic stays in handlers
that were checked question by question against the source documents. When the
model names a different shape, we re-run the engine's own handler for that
shape rather than trusting a number the model produced.

Entity resolution is deliberately left to the engine: it matches client and
engineer names against the database, so it cannot invent one.
"""
from __future__ import annotations

from src.llm import chat_json

# One line each, in the vocabulary of the question rather than of the code.
SHAPE_GLOSSARY = {
    "general_aggregate":    "total/combined value of all a client's completed works",
    "avg_work_size":        "average (mean) contract value across a client's works",
    "mean_median_diff":     "gap between the mean and the median contract value",
    "category_difference":  "difference in value between two named work categories",
    "yearly_diff":          "difference in completed value between two named years",
    "exclusion_aggregate":  "a client's total with one named category removed",
    "threshold_aggregate":  "total of only those works at or above a value threshold",
    "gap_to_threshold":     "how much more value is needed to reach a target",
    "rank_value":           "by how much the largest work exceeds the next largest",
    "distinct_count":       "how many distinct work categories someone completed",
    "referenced_share":     "percentage of works that carry a client reference letter",
    "absence":              "how many works lack a client reference letter",
    "date_span":            "number of days between a certification date and completion",
    "temporal_chain":       "value of works completed after a person's certification date",
    "outstanding_balance":  "amount still unpaid across a client's invoices",
    "collection_percent":   "percentage of invoiced value that has been received",
    "unbilled_gap":         "gap between value awarded and value invoiced",
    "role_split":           "value of works performed in a particular contractual role",
    "doc_filtered_aggregate": "total of works carrying a particular performance grade",
    "other":                "none of the above",
}

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "shape": {"type": "string", "enum": list(SHAPE_GLOSSARY)},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "why": {"type": "string", "description": "One short sentence."},
    },
    "required": ["shape", "confidence", "why"],
    "additionalProperties": False,
}

SYSTEM = """\
You classify questions about a construction company's completed contracts and \
its invoices. Decide which single calculation the question is asking for.

Judge by what is being asked, not by which words appear. Questions are \
deliberately paraphrased, and some contain a mistaken recollection ("that \
number looks off", "I'm half-remembering") which is narrative framing, not part \
of the calculation. Ignore it.
"""


def _prompt(question: str, answer_type: str, engine_shape: str) -> list[dict]:
    glossary = "\n".join(f"  {name}: {desc}" for name, desc in SHAPE_GLOSSARY.items())
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content":
            f"Available calculations:\n{glossary}\n\n"
            f"The answer must be a {answer_type} value.\n\n"
            f"Question:\n{question}\n\n"
            "Which calculation does it ask for?"},
    ]


def classify(question: str, answer_type: str, engine_shape: str = "") -> dict | None:
    """Return {'shape', 'confidence', 'why'} or None if the endpoint failed."""
    return chat_json(_prompt(question, answer_type, engine_shape),
                     INTENT_SCHEMA, temperature=0.0)


def run_shape(conn, params: dict, shape: str):
    """Run the engine's own handler for a given shape, with the engine's params."""
    from src.answer_engine import SHAPE_HANDLERS, format_as_answer

    handler = SHAPE_HANDLERS.get(shape)
    if handler is None:
        return None
    local = dict(params)
    local["question_shape"] = shape
    try:
        value = handler(conn, local)
    except Exception:
        return None
    return None if value is None else format_as_answer(value)
