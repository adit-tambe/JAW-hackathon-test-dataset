"""
llm_crosscheck.py — an independent second opinion, for finding our own bugs.

This is not a replacement for the rule engine. It exists because comparing two
independently-built answer paths is the cheapest bug-finder available: diffing
our answers against the adit branch produced exactly two disagreements and
*both* were real bugs, one in each system, at zero cost in leaderboard attempts.

So the goal here is disagreement, not answers. The design follows from that:

  * **Independence.** The prompt carries the schema and facts that are
    objectively true of the data, but not our interpretive choices, so the model
    can disagree with us on the things still in doubt. Conventions already
    settled against the live scorer *are* stated — they are measurements now,
    not opinions, and re-litigating them would only add noise.

  * **Self-consistency.** A single sample of LLM-written SQL is unreliable: the
    adit branch's cache contains SQL that returned 0 for a question whose gold
    is 2. So each question is asked k times and the modal answer wins. A
    question where the model cannot agree with itself is reported as low
    confidence rather than as a disagreement with us.

  * **Execution, not arithmetic.** The model only writes SQL. Every number comes
    from SQLite, so the model never does mental maths on crore figures.

Usage:
    python -m src.llm_crosscheck --self-test              # gate on the 25 samples
    python -m src.llm_crosscheck --shape category_difference
    python -m src.llm_crosscheck --all --k 3
"""
import argparse
import collections
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import DB_PATH, PROJECT_ROOT

CACHE_PATH = PROJECT_ROOT / "data" / "llm_crosscheck_cache.json"
# gemini-2.5-flash is closed to new API keys ("no longer available to new
# users"), and the adit branch's hardcoded gemini-3.5-flash predates that too.
# gemini-flash-latest tracks whatever the current flash model is.
MODEL = "gemini-flash-latest"
ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/"
            "models/{model}:generateContent?key={key}")


def api_key() -> str:
    env = PROJECT_ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return os.getenv("GEMINI_API_KEY", "")


def schema_text() -> str:
    conn = sqlite3.connect(str(DB_PATH))
    rows = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name") if r[0]]
    conn.close()
    return ";\n".join(rows)


# Facts about the data, and the conventions already settled by measurement
# against the live scorer. Everything still in doubt is deliberately omitted so
# the model's answer stays independent of ours.
GROUND_RULES = """
## What is true of this data

- 155 completed works, 29 clients, 39 engineers. `works.contract_value` is in
  whole rupees already — never divide or multiply by 100000 / 10000000.
- `works.role` is exactly 'Prime' or 'JV Partner'. There is no sub-contractor.
- `works.work_category` is one of exactly 13 values, Title Case:
  Bridges Flyovers, Buildings, Expressways, Industrial Epc, Irrigation,
  Large Bridges, Roads Highways, Roads Maintenance, Sewerage Drainage,
  Small Buildings, Tunnels, Water Supply, Water Treatment.
  Match categories EXACTLY (=), never with LIKE: '%buildings%' would wrongly
  include Small Buildings.
- `works.project_name` looks like 'Ring Road - Maharashtra Pkg-125'. A question
  naming "Pkg-125" means that work. Match with `project_name LIKE '%Pkg-125'`.
- An engineer links to works through `engineer_works`. Nine engineers hold two
  credentials in `engineer_certs`: a PMP issued 2021-03-10 and a Six Sigma
  Black Belt issued 2023-01-01. Filter by `cert_type` when the question names
  one, otherwise you may use the wrong issue date.
- `receivables` is the accounts-receivable ageing register, one row per
  invoice, with `invoiced`, `received` and `outstanding` where
  outstanding = invoiced - received exactly (it is negative on invoices that
  over-received). Join it by `client_name`, not `client_id` — some rows have a
  null client_id.
- "value of work completed in YEAR" means works whose `completion_date` falls
  in that year.

## Settled conventions (measured against the official scorer — follow these)

- A "difference" / "gap" / "spread" / "variance" between two categories, or
  between two years, is the ABSOLUTE value.
- A mean-minus-median question is SIGNED: mean - median, negative if the mean
  is lower.
- An outstanding balance is SUM(outstanding) over all the client's invoices,
  signed, including the negative ones.
- An unbilled gap is ABS(total awarded - total invoiced).
- When a question names an engineer *and* a project or client, and asks for an
  aggregate "for that client", the set is the client's ENTIRE portfolio — the
  engineer is the entry point, not a filter.

## Output contract

Return ONE SQLite query and nothing else, in a ```sql fence. It must return
exactly one row with one numeric column. A percentage must be returned as a
number out of 100, not a fraction.
"""


def build_prompt(question: str, answer_type: str) -> str:
    return (f"You write SQLite queries against this schema:\n\n```sql\n"
            f"{schema_text()}\n```\n{GROUND_RULES}\n"
            f"## Question (expected answer type: {answer_type})\n\n{question}\n")


def call_model(prompt: str, temperature: float, key: str, retries: int = 3):
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature, "maxOutputTokens": 4096},
    }).encode()
    url = ENDPOINT.format(model=MODEL, key=key)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.load(r)
            return "".join(p.get("text", "")
                           for p in d["candidates"][0]["content"]["parts"])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < retries - 1:
                time.sleep(3)
                continue
            return None
    return None


def extract_sql(text: str) -> str:
    if not text:
        return None
    m = re.search(r"```sql\s*(.+?)```", text, re.S | re.I)
    sql = (m.group(1) if m else text).strip()
    # Keep a single statement; refuse anything that writes.
    sql = sql.split(";")[0].strip()
    if re.search(r"\b(insert|update|delete|drop|alter|create|attach)\b", sql, re.I):
        return None
    return sql if sql.lower().startswith(("select", "with")) else None


def run_sql(sql: str):
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        row = conn.execute(sql).fetchone()
        if row is None or row[0] is None:
            return None
        return float(row[0])
    except Exception:
        return None
    finally:
        conn.close()


def quantise(v: float, answer_type: str):
    if v is None:
        return None
    if answer_type == "percent":
        return round(v, 2)
    if answer_type in ("count", "days"):
        return int(round(v))
    return int(round(v))


def ask(question: str, answer_type: str, k: int = 3, key: str = None,
        cache: dict = None) -> dict:
    """Ask k times, return the modal answer and how strongly it agreed."""
    key = key or api_key()
    ck = hashlib.md5(f"{question}|{answer_type}|{k}|{MODEL}".encode()).hexdigest()
    if cache is not None and ck in cache:
        return cache[ck]

    answers, sqls = [], []
    for i in range(k):
        text = call_model(build_prompt(question, answer_type),
                          0.0 if i == 0 else 0.6, key)
        sql = extract_sql(text)
        if not sql:
            continue
        val = quantise(run_sql(sql), answer_type)
        if val is not None:
            answers.append(val)
            sqls.append(sql)

    result = {"answer": None, "votes": 0, "k": k, "answers": answers, "sql": None}
    if answers:
        counts = collections.Counter(answers)
        best, votes = counts.most_common(1)[0]
        result.update(answer=best, votes=votes,
                      sql=sqls[answers.index(best)])
    if cache is not None:
        cache[ck] = result
    return result


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict):
    CACHE_PATH.write_text(json.dumps(cache, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--self-test", action="store_true",
                    help="gate the path on the 25 sample questions, where gold is known")
    ap.add_argument("--shape", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", default="llm_crosscheck.json")
    a = ap.parse_args()

    key = api_key()
    if not key:
        print("no GEMINI_API_KEY in .env")
        return 1
    cache = load_cache()

    if a.self_test:
        from evaluate import score_one
        qs = json.loads((PROJECT_ROOT / "sample_questions.json")
                        .read_text(encoding="utf-8"))["questions"]
        if a.limit:
            qs = qs[:a.limit]
        total = 0.0
        for q in qs:
            gold = q.get("answer")
            at = ("percent" if isinstance(gold, float) and gold < 100
                  else ("count" if abs(gold) < 100 else "money"))
            r = ask(q["question"], at, a.k, key, cache)
            s = score_one(gold, r["answer"])
            total += s
            flag = "OK " if s == 1.0 else f"{s:.2f}"
            print(f"  {flag} {q['qid']}  llm={r['answer']}  gold={gold}  "
                  f"votes={r['votes']}/{r['k']}")
            save_cache(cache)
        print(f"\nLLM path on samples: {total:.2f} / {len(qs)} = {total/len(qs):.1%}")
        return 0

    import csv
    from src.answer_engine import parse_question, reconcile_shape
    qs = json.loads((PROJECT_ROOT / "validation_questions.json")
                    .read_text(encoding="utf-8"))["questions"]
    ours = {r["question_id"]: r["answer"]
            for r in csv.DictReader((PROJECT_ROOT / "submission.csv").open())}
    conn = sqlite3.connect(str(DB_PATH))
    targets = set(a.shape)
    picked = []
    for q in qs:
        p = parse_question(conn, q["question"])
        shp = reconcile_shape(p["question_shape"], q.get("answer_type"), p)
        if a.all or shp in targets:
            picked.append((q, shp))
    if a.limit:
        picked = picked[:a.limit]

    print(f"cross-checking {len(picked)} questions with k={a.k}\n")
    out, disagree = [], 0
    for i, (q, shp) in enumerate(picked, 1):
        r = ask(q["question"], q.get("answer_type", "money"), a.k, key, cache)
        mine = float(ours[q["qid"]])
        theirs = r["answer"]
        agree = theirs is not None and abs(theirs - mine) <= max(1.0, abs(mine) * 0.005)
        if not agree:
            disagree += 1
        out.append({"qid": q["qid"], "shape": shp, "ours": mine,
                    "llm": theirs, "votes": r["votes"], "k": r["k"],
                    "agree": agree, "sql": r["sql"],
                    "question": q["question"]})
        mark = "agree" if agree else "DISAGREE"
        print(f"  [{i}/{len(picked)}] {q['qid']} {shp:22s} ours={mine:<16.0f} "
              f"llm={theirs} votes={r['votes']}/{r['k']}  {mark}")
        save_cache(cache)

    (PROJECT_ROOT / a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\n{disagree} disagreements of {len(picked)} -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
