# Bid intelligence pipeline — JAW 2026

Answers numerical questions about a construction company's document estate:
completion certificates, reference letters, CVs, credentials, bonds, ledgers,
bank statements, RA bills, tenders and workbooks.

```bash
pip install -r requirements.txt
./setup.sh
./run.sh --docs /path/to/documents --questions /path/to/questions.json --out submission.csv
```

No network access is required or attempted at run time. There are no model
weights to fetch, so `setup.sh` downloads nothing — it runs a preflight that
fails loudly, at install time, for the packaging mistakes that would otherwise
surface mid-run.

## How it works

```
documents/  ──►  discover  ──►  extract  ──►  SQLite  ──►  resolve  ──►  submission.csv
                (by content)    (typed)      (24 tables)   (engine + LLM)
```

**1. Discover.** Walks `--docs` recursively and types every file by what it says
on its face — never by a path or a file name, because the tree we are given is
nested differently from anything we have seen and the file names are not ours.
Ordered marker rules run over whitespace-collapsed lowercase text, since several
headings render in small caps and titles wrap across lines. Four document
families carry two layouts apiece and both are handled. Workbooks are typed from
sheet names and header rows.

**2. Extract.** Each type has a parser that pulls typed fields, and the full text
is retained so a figure can still be read straight out of a document when no
typed field holds it.

**3. Build.** Records load into SQLite and are reconciled against the credentials
pack, which states role and category once and uniformly for every work — the
individual certificates each say it their own way.

**4. Resolve.** Two independent systems answer every question, and they fail in
opposite directions:

- a **deterministic engine** classifies the question into one of nineteen shapes
  and runs a hand-written query for it. Exact where it recognises the question,
  blind where it does not;
- the **provided LLM** is asked which calculation the question wants, and for any
  parameters the question states. Naming the calculation is a far lower bar than
  writing SQL for it, and it aims at the engine's weak spot — recognising an
  unfamiliar phrasing, not computing the answer once recognised.

When the model names a shape, the engine's own handler for that shape is
re-run. The arithmetic always stays in code that was checked against source
documents; the model only ever changes the routing.

The arbitration is deliberately asymmetric:

| situation | outcome |
|---|---|
| engine confident | engine answers; a differing opinion is logged, not acted on |
| engine unsure, or its answer is zero | model reroutes, subject to an answer-type check |
| no endpoint reachable | engine alone |

Testing settled that. An earlier version let a confident classification override
the engine, and against a deliberately unreliable fixture that destroyed all 25
sample answers. The engine reproduces the sample gold exactly; the classifier has
no comparable record. With no partial credit, overriding a verified answer on an
unverified opinion is a losing trade.

## Why SQL rather than retrieval

The questions are numeric aggregations over a whole estate, and the tier that
breaks ties rewards exhaustively covering many documents with exact arithmetic
across them. Chunk retrieval reliably misses documents and leaves the model doing
the arithmetic. A `GROUP BY` either covers every row or none, and SQLite does the
sums — so the model is never asked to do mental arithmetic on crore figures.

## What has been verified

```bash
python tests/verify.py                          # everything, engine only
python tests/verify.py --llm "$LLM_BASE_URL"    # everything, model path too
```

| check | what it proves | result |
|---|---|---|
| **regression** | 333 questions with answers verified against source documents | identical to baseline |
| **samples** | the organisers' own questions, this round's exact-match tolerance | 25 / 25 |
| **paraphrases** | the same questions reworded — the closest proxy for the graded set | **25 / 25** (14 / 25 engine alone) |
| **novel** | questions reaching tables no deterministic shape computes over | **12 / 12** (0 / 12 engine alone) |
| **permutations** | the same estate in three unrelated arrangements | all three agree exactly |
| **hostile** | corrupt files, unknown families, odd question files | 5 / 5 produce a valid submission |
| **typing** | content sniffing against the manifest it replaces | 687 / 687, zero disagreements |
| **scrambled tree** | every file renamed to a hash, scattered four levels deep | output byte-identical |

Two of those numbers are the argument for the whole design. **14/25** is what
phrase-matching is worth on wording it has not seen. **0/12** is what it is worth
on a subject it was not written for. Both are what the graded set will be made of.

The permutation check exists because a bug of exactly that kind got through: an
ambiguous first name resolved by taking whichever engineer the database happened
to store first, so the same documents in a different order gave a different
answer. Nothing about a single run reveals it.

## If the run goes wrong

The deterministic pass runs first, alone, and its result is written before the
model is even contacted — about a second in, with no network. From that moment a
complete valid submission exists on disk, written atomically. Everything after
can only improve it. If the endpoint is unreachable, slow, flaky or wrong, the
worst case is the engine's own answers.

A wall-clock budget (`JAW_TIME_BUDGET`, default 90 min) hands any remaining
questions back to the engine rather than being cut off part way. Against an
endpoint failing every third call, paraphrase accuracy holds at 92%.

## Endpoint notes

`src/llm.py` is the only thing that talks to the provided endpoint, and it
handles the three documented traps: a reasoning model returns
`finish_reason: "length"` with null content when `max_tokens` is small (we retry
with more room), the trace field is `reasoning`, and `json_schema` decoding is
constrained so structured replies always parse. Concurrency is deliberately
modest — the endpoint is shared, and a client that opens hundreds of connections
loses more time than it gains.

The pipeline stands up no services of its own, so ports 8112–8115 stay free.
SQLite is in-process.

## Layout

```
main.py              --docs / --questions / --out entry point
run.sh, setup.sh     packaging
src/discover.py      find and type documents by content
src/ingest.py        walk, extract, write records + manifest
src/build_db.py      load into SQLite, reconcile against the credentials pack
src/answer_engine.py shape classification and the query per shape
src/llm.py           the only client for the provided endpoint
src/llm_intent.py    which calculation, and any stated parameters
src/llm_sql.py       text-to-SQL fallback, self-consistent over samples
src/resolve.py       arbitration between the two systems
src/schema_card.py   introspected schema + vocabulary + measured conventions
tests/               fixtures, scorer, mock endpoint — see tests/NOTES.md
history/             previous round's artefacts; nothing reads them
```
