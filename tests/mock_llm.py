#!/usr/bin/env python3
"""
mock_llm.py — a stand-in for the provided vLLM endpoint, for local testing.

The real endpoint only exists on the grading machine, so without this the whole
LLM path would ship having never executed. It speaks just enough of the
OpenAI-compatible surface to exercise ours: /chat/completions, json_schema
response_format, and the reasoning-model behaviour that trips people up —
returning finish_reason "length" with null content when max_tokens is small.

    python tests/mock_llm.py --port 8112 [--mode sql|judge|flaky|length]

It is a test fixture. Nothing in the pipeline imports it.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE = "sql"
CALLS = {"n": 0}


def _sql_for(question: str) -> str:
    """A crude intent guess — enough to produce executable, varied SQL."""
    q = question.lower()
    client = None
    m = re.search(r"(national expressway development authority|trishakti power "
                  r"generation corporation|arunodaya infrastructure|subarnarekha "
                  r"valley corporation|lakshya engineering & construction|"
                  r"mahanadi steel corporation|suvarna projects limited|"
                  r"peninsular petroleum corporation|meridian constructors & co\.|"
                  r"mega infrastructure authority)", q)
    if m:
        client = m.group(1)
    where = (f"WHERE cl.client_name = '{client.title()}'" if client else "")
    if "median" in q:
        return ("SELECT AVG(w.contract_value) FROM works w "
                "JOIN clients cl ON cl.client_id = w.client_id " + where)
    if "outstanding" in q or "unpaid" in q or "still due" in q:
        return "SELECT SUM(outstanding) FROM receivables"
    if "how many" in q and "categor" in q:
        return "SELECT COUNT(DISTINCT work_category) FROM works"
    if "days" in q or "interval" in q:
        return "SELECT 536"
    return ("SELECT SUM(w.contract_value) FROM works w "
            "JOIN clients cl ON cl.client_id = w.client_id " + where)


def _just_the_question(prompt: str) -> str:
    """Strip the glossary and schema card the prompt wraps around the question.

    Without this the fixture classifies on our own prompt text — the glossary
    contains the word "median" — and every question comes back the same, which
    makes the test look like a pipeline failure.
    """
    marker = "Question"
    idx = prompt.rfind(marker)
    return prompt[idx:] if idx >= 0 else prompt


# Ordered most-specific first. This stands in for a 35B instruction-tuned
# model, so it is allowed to be reasonably capable — the point of the fixture is
# to test whether the ARCHITECTURE recovers when the classifier is competent,
# not to simulate a weak one. Deliberately keyed on paraphrases the
# deterministic engine does not recognise, since those are the interesting case.
_SHAPE_CUES: list[tuple[str, str]] = [
    ("midpoint value", "mean_median_diff"),
    ("mean and the median", "mean_median_diff"),
    ("median", "mean_median_diff"),
    ("no client reference letter", "absence"),
    ("lack a client reference", "absence"),
    ("written endorsement", "referenced_share"),
    ("testimonial", "referenced_share"),
    ("out of one hundred", "referenced_share"),
    ("different kinds of work", "distinct_count"),
    ("distinct work categor", "distinct_count"),
    ("how long did it take", "date_span"),
    ("how many days", "date_span"),
    ("interval", "date_span"),
    ("not yet raised an invoice", "unbilled_gap"),
    ("have we claimed on bills", "unbilled_gap"),
    ("value we have claimed", "unbilled_gap"),
    ("unbilled", "unbilled_gap"),
    ("awarded and the amount we have actually invoiced", "unbilled_gap"),
    ("still not paid us", "outstanding_balance"),
    ("left owing", "outstanding_balance"),
    ("outstanding", "outstanding_balance"),
    ("unpaid", "outstanding_balance"),
    ("still due", "outstanding_balance"),
    ("proportion has actually reached us", "collection_percent"),
    ("collection", "collection_percent"),
    ("by how much are we short", "gap_to_threshold"),
    ("how much more", "gap_to_threshold"),
    ("we are short", "gap_to_threshold"),
    ("runner-up", "rank_value"),
    ("immediately below", "rank_value"),
    ("exceeds the second", "rank_value"),
    ("next one down", "rank_value"),
    ("second-largest", "rank_value"),
    ("after she already held", "temporal_chain"),
    ("after he already held", "temporal_chain"),
    ("completed after", "temporal_chain"),
    ("typical contract", "avg_work_size"),
    ("average size", "avg_work_size"),
    ("average", "avg_work_size"),
    ("leave the", "exclusion_aggregate"),
    ("strip out", "exclusion_aggregate"),
    ("exclud", "exclusion_aggregate"),
    ("or more", "threshold_aggregate"),
    ("or above", "threshold_aggregate"),
    ("crore mark", "threshold_aggregate"),
    ("crore threshold", "threshold_aggregate"),
    ("how far apart in value", "category_difference"),
    ("what separates the two", "category_difference"),
    ("difference in value between", "category_difference"),
    ("move between", "yearly_diff"),
    ("size of the swing", "yearly_diff"),
    ("as prime", "role_split"),
    ("graded", "doc_filtered_aggregate"),
]


def _shape_for(question: str) -> str:
    q = _just_the_question(question).lower()
    for needle, shape in _SHAPE_CUES:
        if needle in q:
            return shape
    return "general_aggregate"


_WORD_NUMBERS = {
    "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100,
    "twenty-three": 23, "twenty-one": 21, "twenty-five": 25,
    "thirty-five": 35, "forty-three": 43, "seventy-three": 73, "six": 6, "two": 2,
}
_CATEGORIES = [
    "Water Treatment", "Roads Highways", "Bridges Flyovers", "Expressways",
    "Tunnels", "Irrigation", "Sewerage Drainage", "Buildings", "Small Buildings",
    "Large Bridges", "Water Supply", "Industrial Epc", "Roads Maintenance",
]


def _params_for(prompt: str) -> dict:
    """Pull the parameters a question states. Crude, but independent of the engine."""
    q = _just_the_question(prompt)
    low = q.lower()
    out = {"category_a": "", "category_b": "", "excluded_category": "",
           "year_a": "", "year_b": "", "threshold_rupees": ""}

    mentioned = [c for c in _CATEGORIES if c.lower() in low]
    if re.search(r"leave the|strip out|exclud|without the|minus the", low) and mentioned:
        out["excluded_category"] = mentioned[0]
    elif len(mentioned) >= 2:
        out["category_a"], out["category_b"] = mentioned[0], mentioned[1]

    years = re.findall(r"\b(20[0-2]\d)\b", q)
    years = [y for y in years if y != "2021"]
    if len(years) >= 2:
        out["year_a"], out["year_b"] = years[0], years[1]

    m = re.search(r"([\w-]+|\d+(?:\.\d+)?)\s*(crore|lakh)", low)
    if m:
        token = m.group(1)
        try:
            amount = float(token)
        except ValueError:
            amount = _WORD_NUMBERS.get(token, 0)
        if amount:
            out["threshold_rupees"] = str(
                int(amount * (10_000_000 if m.group(2) == "crore" else 100_000)))
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass                                    # keep the test output readable

    def do_POST(self):                          # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        CALLS["n"] += 1

        if MODE == "flaky" and CALLS["n"] % 3 == 0:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"error":"overloaded"}')
            return

        # The documented trap: a reasoning model spends its budget on a hidden
        # trace, so a small cap yields finish_reason "length" and null content.
        if body.get("max_tokens", 4096) < 2048 or MODE == "length":
            payload = {"choices": [{"finish_reason": "length",
                                    "message": {"role": "assistant",
                                                "content": None,
                                                "reasoning": "thinking..."}}]}
            self._send(payload)
            return

        question = ""
        for message in body.get("messages", []):
            if message.get("role") == "user":
                question = message.get("content", "")
        schema_name = ((body.get("response_format") or {})
                       .get("json_schema") or {}).get("schema") or {}
        props = set((schema_name.get("properties") or {}).keys())

        if "choice" in props:                   # the adjudication call
            content = json.dumps({"choice": random.choice(["A", "B"]),
                                  "why": "mock adjudication"})
        elif "shape" in props:                  # the intent call
            payload = {"shape": _shape_for(question),
                       "confidence": "high", "why": "mock"}
            payload.update(_params_for(question))
            content = json.dumps(payload)
        elif "sql" in props:
            content = json.dumps({"reasoning": "mock", "sql": _sql_for(question)})
        else:
            content = "1"

        self._send({"choices": [{"finish_reason": "stop",
                                 "message": {"role": "assistant",
                                             "content": content,
                                             "reasoning": "mock trace"}}]})

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    global MODE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8112)
    ap.add_argument("--mode", default="sql",
                    choices=["sql", "judge", "flaky", "length"])
    args = ap.parse_args()
    MODE = args.mode
    print(f"mock llm on :{args.port} mode={args.mode}", flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
