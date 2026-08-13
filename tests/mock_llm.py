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


def _shape_for(question: str) -> str:
    """A crude independent classifier, so both agree and reroute paths run."""
    q = _just_the_question(question).lower()
    for needle, shape in [
        ("median", "mean_median_diff"),
        ("lack", "absence"),
        ("out of one hundred", "referenced_share"),
        ("share", "referenced_share"),
        ("distinct work categor", "distinct_count"),
        ("days", "date_span"),
        ("interval", "date_span"),
        ("outstanding", "outstanding_balance"),
        ("unpaid", "outstanding_balance"),
        ("still due", "outstanding_balance"),
        ("collect", "collection_percent"),
        ("unbilled", "unbilled_gap"),
        ("exceeds the second", "rank_value"),
        ("next one down", "rank_value"),
        ("average", "avg_work_size"),
        ("exclud", "exclusion_aggregate"),
        ("crore mark", "threshold_aggregate"),
        ("crore line", "threshold_aggregate"),
        ("as prime", "role_split"),
        ("graded", "doc_filtered_aggregate"),
        ("satisfactory", "doc_filtered_aggregate"),
        ("after", "temporal_chain"),
    ]:
        if needle in q:
            return shape
    return "general_aggregate"


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
            content = json.dumps({"shape": _shape_for(question),
                                  "confidence": "high", "why": "mock"})
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
