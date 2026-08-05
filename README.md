# BITS Hackathon — Bid Intelligence over a Document Estate

You are given the complete document archive of a construction company. **There is no database.**
Your task is to build a system that reads those documents and answers precise numerical questions
about the business.

---

## The company

**National Infrastructure Corp. Ltd.** — an Indian infrastructure contractor, founded 2005, head
office in Salt Lake, Kolkata. It builds highways, water treatment plants, flyovers, drainage,
tunnels and power infrastructure for state and central government bodies.

| | |
|---|---|
| Completed works | 155, delivered 2010 – 2025 |
| Clients | 62 government departments and authorities |
| Employees on record | 486 |
| Business units | 6 |
| Total delivered value | ~₹5,530 crore |

Everything about this company is synthetic. It has never existed, and the identifiers in these
documents (CIN, GST, PAN) are deliberately invalid — see *Rules* below.

---

## What you are given

**687 documents, 20 types, ~39 MB.** 678 PDFs and 9 Excel workbooks, in `documents/`, grouped by
type.

| Document type | Count | What it holds |
|---|---:|---|
| `completion_certificate` | 155 | Client's sign-off on a finished work: value, dates, and the client's **written grading** of our performance |
| `company_completion_certificate` | 155 | Our own record of the same work |
| `reference_letter` | 132 | Client testimonials — note that not every work has one |
| `performance_bond` | 60 | Bank guarantees issued against contracts |
| `personnel_certificate` | 48 | PMP, Six Sigma and other credentials held by staff |
| `cv` | 39 | Engineer profiles: which works they led |
| `compliance_matrix` | 40 | Tender compliance checklists |
| `general_ledger_book` | 8 | Full journal — invoices, receipts, credit notes |
| `bank_statement` | 8 | Cash movements |
| `financial_statement` | 7 | Statutory accounts, several reporting eras |
| `ra_bill` / `final_ra_bill` | 12 | Running-account bills with BOQ detail |
| `tender_dossier` | 6 | Bids submitted |
| `iso_certificate` | 5 | Quality and safety accreditations |
| `annual_report` | 2 | Narrative reports with registers and tables |
| `past_performance_portfolio` | 1 | Consolidated credentials pack |
| **workbooks** (`.xlsx`) | 9 | BOQ, ageing, trial balance, asset register — with live formulas |

Plus:

- `document_index.csv` — `doc_id`, `doc_type`, `filename`, `size_bytes`. **It deliberately does not
  tell you which document is about which project or client.** Working that out is part of the task.
- `sample_questions.json` — 25 worked examples, with answers and reasoning (see below).
- `evaluate.py` — the exact scorer we will run, so you can measure yourself.
- `sample_submission.jsonl` — the submission format.

### What you are deliberately NOT given

No database, no knowledge graph, no schema, no extracted fact table, and no mapping from documents to
entities. If we handed you a database this would be a SQL exercise. The interesting problem is
getting structured, queryable knowledge **out of** 687 unstructured documents — so that is the part
we left for you.

---

## What you have to build

A system that takes a question in plain English and returns a number.

The questions are written the way people on a bid desk actually ask them — sometimes a formal note,
sometimes a hurried message before a deadline. They are not templated, and no two are phrased alike.

Answering them generally requires **several documents at once**. A typical question names an
engineer's certificate, expects you to find which project that engineer led, work out which client
commissioned it, gather *every* project for that client, and total their values — where each value
must be read out of that project's own certificate. Four documents minimum, often more.

**Three things make this harder than it first looks:**

1. **Money is written the way people write it.** A contract worth 333,800,000 rupees appears in
   documents as `INR 33.38 Cr`, or `3,338.00 Lakh`, or `33,38,00,000` in Indian digit grouping. Your
   extraction has to handle all of it. (The rendering is lossless — no precision is hidden from you.)

2. **Some facts exist only in prose.** A client's opinion of our work — "Very Good", "Satisfactory" —
   appears in the text of a completion certificate and nowhere else. Questions that filter on it
   cannot be answered by any amount of table parsing.

3. **Absence is a real answer.** "How many completed works have no reference letter on file?"
   requires proving something is *missing* across a client's whole portfolio. A system that
   hallucinates connections will confidently say zero.

---

## What the questions look like

All 25 examples are in `sample_questions.json`, with answers and a step-by-step derivation. Three of
them:

> **Regarding Asha Nair’s PMP work on the Cable Stayed Bridge — Jharkhand Pkg-115, what is the defensible average size across all completed projects for the commissioning client?**
> → `537933333`

> **Cross-checking the completion date against Asha Nair's PMP for 2021-03-10, what number of days passed from issuance to finish for School Building — Madhya Pradesh Pkg-145?**
> → `1569`

> **Jal Nigam, Jharkhand is our starting point for the audit, so what whole number out of one hundred represents the defensible share of completed assignments that carry formal verification on file?**
> → `33.33`

Each sample carries `reasoning_steps` showing the path from question to answer, and the individual
values that had to be read out of documents along the way. Use them to calibrate — they are the same
kinds of question you will be scored on, only easier.

**Answers are always a plain number**: rupees (no units, no commas), a count, a percentage out of
100, or a number of days. Every question states which.

---

## Scoring

You will be scored on a **larger, harder hidden set** you never see. It contains the same kinds of
question as the samples, across all 21 reasoning patterns.

Close answers earn partial credit — the aim is to reward a system that reasons correctly and misses
one contributor, over one that guesses:

**Money and other large values** (|answer| ≥ 100)

| Relative error | Score |
|---|---|
| ≤ 0.5% | **1.0** |
| ≤ 2% | 0.7 |
| ≤ 10% | 0.3 |
| more | 0 |

**Counts and percentages** (|answer| < 100)

| | Score |
|---|---|
| Exact | **1.0** |
| Off by one | 0.3 |
| more | 0 |

Run it yourself:

```bash
python evaluate.py --self-test                                  # confirm the bands
python evaluate.py --submission my_answers.jsonl --per-question  # score against the samples
```

### Submission format

One JSON object per line, `qid` and `answer`:

```json
{"qid": "HV-IC-0001", "answer": 1069600000}
{"qid": "HV-IC-0002", "answer": 58.96}
```

Answer every question — an unanswered one scores zero, and a wrong one costs nothing extra.

---

## Rules

- **The corpus is synthetic.** The company, people, clients and projects were generated. Searching
  the internet for "National Infrastructure Corp" will not help you, and any real company by a
  similar name is unrelated.
- **Identifiers are intentionally invalid.** The CIN, GST and PAN numbers fail their check digits by
  design. Do not use them to look anything up.
- **Everything you need is in `documents/`.** Every value required by every question was verified to
  be readable from the shipped documents before the question was accepted. If you cannot find
  something, it is a retrieval problem, not a missing file.
- Use any tools, models or libraries you like.

---

## Getting started

1. Read three or four completion certificates by hand. Notice how the value, dates, client and
   grading are laid out, and how much the layout varies between documents.
2. Open `sample_questions.json` and follow one `reasoning_steps` chain through the actual PDFs.
3. That will tell you what your extraction has to produce. Build that, then build the reasoning on
   top of it.

Good luck.
