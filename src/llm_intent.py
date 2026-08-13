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
        # Parameters are asked for too, because the engine extracts them with
        # the same phrase rules that classify the shape. "leave the water
        # treatment jobs out of it" fails both at once: the shape is missed AND
        # the excluded category is never captured, so rerouting alone would
        # still aggregate the whole portfolio. Entity names are deliberately
        # absent — the engine matches those against the database and so cannot
        # invent one.
        "category_a": {"type": "string", "description": "First named work category, or empty."},
        "category_b": {"type": "string", "description": "Second named work category, or empty."},
        "excluded_category": {"type": "string", "description": "Category to leave out, or empty."},
        "year_a": {"type": "string", "description": "First year mentioned, or empty."},
        "year_b": {"type": "string", "description": "Second year mentioned, or empty."},
        "threshold_rupees": {"type": "string",
                             "description": "Any value threshold in plain rupees, or empty."},
    },
    "required": ["shape", "confidence", "why", "category_a", "category_b",
                 "excluded_category", "year_a", "year_b", "threshold_rupees"],
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


def _prompt(question: str, answer_type: str, categories: list[str]) -> list[dict]:
    glossary = "\n".join(f"  {name}: {desc}" for name, desc in SHAPE_GLOSSARY.items())
    vocab = ("\nWork categories, spelled exactly as they appear in the records:\n  "
             + " | ".join(categories) + "\n") if categories else ""
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content":
            f"Available calculations:\n{glossary}\n{vocab}\n"
            f"The answer must be a {answer_type} value.\n\n"
            f"Question:\n{question}\n\n"
            "Name the calculation, and fill in any parameter the question "
            "states. Leave a parameter empty when the question does not give "
            "it. Express a threshold in plain rupees (forty crore = 400000000)."},
    ]


def classify(question: str, answer_type: str,
             categories: list[str] | None = None) -> dict | None:
    """Return the named calculation and any parameters, or None on failure."""
    return chat_json(_prompt(question, answer_type, categories or []),
                     INTENT_SCHEMA, temperature=0.0)


def merge_params(params: dict, intent: dict, categories: list[str]) -> dict:
    """Fill gaps in the engine's parameters from the model's reading.

    Only gaps. Where the engine did extract something it stays, because it
    matched against values that exist in the database rather than recalling
    them.
    """
    merged = dict(params)
    canonical = {c.lower(): c for c in categories}

    def as_category(value: str) -> str | None:
        value = (value or "").strip()
        return canonical.get(value.lower()) if value else None

    for field, key in (("category_a", "cat1"), ("category_b", "cat2"),
                       ("excluded_category", "exclude_category")):
        if not merged.get(key):
            category = as_category(intent.get(field, ""))
            if category:
                merged[key] = category

    for field, key in (("year_a", "year1"), ("year_b", "year2")):
        if not merged.get(key):
            raw = (intent.get(field) or "").strip()
            if raw.isdigit() and len(raw) == 4:
                merged[key] = int(raw)

    if not merged.get("threshold_value") and not merged.get("target_value"):
        raw = (intent.get("threshold_rupees") or "").replace(",", "").strip()
        try:
            value = float(raw)
        except ValueError:
            value = 0.0
        if value > 0:
            merged["threshold_value"] = value
            merged["target_value"] = value
    return merged


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
