"""
llm_extract.py — recover fields a regex extractor missed, by reading the document.

The typed extractors are written against layouts we have seen. Every family in
this estate already carries two, and a family we meet for the first time on the
grading machine may carry a third. When that happens the document types
correctly and then extracts almost nothing: the record exists, its fields are
null, and every question that needed them is answered from a fallback. Nothing
errors, so nothing tells us.

So after extraction we measure how much of each record actually filled, and for
the thin ones we ask the model to read the document and supply what is missing.
Three properties make this safe:

  * it fills gaps only. A field the regex extracted stands, because that value
    came from a pattern we wrote and checked. The single exception is a value
    known to be wrong rather than merely absent — the contractor's own name in
    the client slot — because a wrong value looks populated and would otherwise
    never be revisited;
  * the field list is derived from what the same family extracted successfully
    elsewhere, not hardcoded, so it adapts to whatever the estate contains;
  * with no endpoint it does nothing at all, and the pipeline behaves exactly as
    it did before.

This is extraction, not generation of answers. The model reads one document and
reports what it says.
"""
from __future__ import annotations

import json
import re

from src.llm import chat_json, map_concurrent

# Below this fraction of populated fields a record is considered thin enough to
# be worth a second look. Set so that a record missing roughly half its fields
# is re-read, while ordinary sparseness is left alone.
THIN_RATIO = 0.55
MAX_TEXT = 12000          # a document's own text; well inside the context window


# The contractor is the party that DID the work and can never be the client.
# One certificate layout puts the company's own name in the client slot, which
# is worse than leaving it blank: a wrong value looks populated, so it is never
# re-read and never questioned. Values matching this are treated as missing.
CONTRACTOR_MARKERS = ("national infrastructure corp", "nicl")

SUSPECT_BY_FIELD = {
    "client_name": CONTRACTOR_MARKERS,
    "contractor_name": (),
}


def is_missing(field: str, value) -> bool:
    """Whether a field still needs an answer — blank, or known to be wrong."""
    if value in (None, "", [], {}, "None"):
        return True
    markers = SUSPECT_BY_FIELD.get(field)
    if markers and isinstance(value, str):
        low = value.lower()
        return any(m in low for m in markers)
    return False


def typed_fields(record: dict) -> dict:
    return {k: v for k, v in record.items() if not k.startswith("_")}


def fill_ratio(record: dict) -> float:
    fields = typed_fields(record)
    if not fields:
        return 0.0
    filled = sum(1 for k, v in fields.items()
                 if not is_missing(k, v) and v != 0)
    return filled / len(fields)


def family_schema(records: list[dict]) -> list[str]:
    """The fields this family is known to carry, learned from its own records.

    Taken from whichever records extracted well, so a family whose second layout
    we never anticipated still gets asked for the right things.
    """
    counts: dict[str, int] = {}
    for record in records:
        for key, value in typed_fields(record).items():
            if value not in (None, "", [], {}, "None") and value != 0:
                counts[key] = counts.get(key, 0) + 1
    # Ignore nested structures: those are line-item tables, not scalar fields,
    # and are better left to the parser that understands their shape.
    return sorted(k for k, n in counts.items()
                  if n >= 2 and not isinstance(_sample(records, k), (list, dict)))


def _sample(records: list[dict], key: str):
    for record in records:
        value = record.get(key)
        if value not in (None, "", "None"):
            return value
    return None


_NUMERIC_HINT = re.compile(r"value|amount|cost|total|count|number|_no$|billed|"
                           r"claimed|gst|retention|cumulative", re.I)


def _schema_for(fields: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {f: {"type": "string",
                           "description": "Exactly as the document states it, "
                                          "or empty if the document does not say."}
                       for f in fields},
        "required": fields,
        "additionalProperties": False,
    }


SYSTEM = """\
You read one business document and report the fields it states. You are not \
answering a question and not calculating anything.

Rules:
- Copy values as the document gives them. Do not infer, round or convert.
- Dates as YYYY-MM-DD. Amounts as plain digits with no separators, symbols or \
units; if the document says a figure in crore or lakh, convert it to rupees \
(1 crore = 10000000, 1 lakh = 100000).
- The client is the party the work was done FOR. National Infrastructure Corp. \
Ltd. is the contractor who did the work, and is never the client.
- If the document does not state a field, return an empty string for it. An \
empty answer is correct and useful; a guess is not.
"""


def _coerce(field: str, value: str):
    value = (value or "").strip()
    if not value or value.lower() in ("none", "null", "n/a", "-"):
        return None
    if _NUMERIC_HINT.search(field):
        cleaned = re.sub(r"[^\d.\-]", "", value)
        if cleaned not in ("", "-", "."):
            try:
                number = float(cleaned)
                return int(number) if number == int(number) else number
            except ValueError:
                return value
    return value


def enrich_one(doc_type: str, text: str, record: dict, fields: list[str]) -> dict:
    """Ask for the fields this record is missing; fill only those."""
    missing = [f for f in fields if is_missing(f, record.get(f))]
    if not missing:
        return record
    payload = chat_json(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content":
             f"Document type: {doc_type.replace('_', ' ')}\n\n"
             f"--- document ---\n{text[:MAX_TEXT]}\n--- end ---\n\n"
             f"Report these fields: {', '.join(missing)}"}],
        _schema_for(missing), temperature=0.0)
    if not payload:
        return record
    for field in missing:
        value = _coerce(field, str(payload.get(field, "")))
        if value is not None:
            record[field] = value
            record.setdefault("_llm_filled", []).append(field)
    return record


def enrich_thin_records(records_by_type: dict[str, list[tuple[str, dict, str]]],
                        verbose: bool = True) -> int:
    """Re-read every record that extracted thin. Returns how many were improved.

    `records_by_type` maps a doc type to (doc_id, record, text) triples.
    """
    jobs: list[tuple[str, str, dict, list[str]]] = []
    for doc_type, entries in records_by_type.items():
        records = [rec for _, rec, _ in entries]
        fields = family_schema(records)
        if not fields:
            continue
        for _, record, text in entries:
            if text and fill_ratio(record) < THIN_RATIO:
                jobs.append((doc_type, text, record, fields))
    if not jobs:
        if verbose:
            print("  every record extracted cleanly — no re-reads needed")
        return 0

    if verbose:
        counts: dict[str, int] = {}
        for doc_type, _, _, _ in jobs:
            counts[doc_type] = counts.get(doc_type, 0) + 1
        print(f"  {len(jobs)} record(s) extracted thin; re-reading with the model:")
        for doc_type, n in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"      {doc_type:34s} {n}")

    before = [fill_ratio(rec) for _, _, rec, _ in jobs]
    map_concurrent(lambda job: enrich_one(job[0], job[1], job[2], job[3]), jobs)
    improved = sum(1 for (_, _, rec, _), was in zip(jobs, before)
                   if fill_ratio(rec) > was)
    if verbose:
        print(f"  {improved} of {len(jobs)} improved")
    return improved
