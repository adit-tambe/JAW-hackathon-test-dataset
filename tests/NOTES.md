# Test fixtures

Nothing in `src/` or `main.py` reads any file in this directory. These exist so
a change can be checked before it ships, not to supply answers.

## `baseline_answers.csv`

The pipeline's own output for `validation_questions.json`, captured from a run
against a scrambled copy of the document tree. It is a regression tripwire: any
edit that changes an answer should be deliberate, and this makes that visible.

Its accuracy is not assumed. Of these 333, 293 were reproduced independently
from source documents during the previous round — receivables recomputed
straight from the ageing workbook, works-based figures recomputed from the works
table, day counts recomputed from certificate dates.

Three answers in that round were established by measurement rather than by
reading a document, and the pipeline cannot reproduce them:

    HV-IC-0178   157,033,333   Maharashtra Municipal Corporation
    HV-IC-0276     2,575,000   Public Health Engineering Dept, Odisha
    HV-IC-0333    20,300,000   Public Works Department, Govt of Gujarat

Each names an engineer and a credential but no project, and the engineer serves
four to six clients, so nothing in the corpus says which client is meant. The
answers above were recovered from scoring feedback. They are recorded here as a
known limitation of the documents, and deliberately not hardcoded: an answer
keyed to a question id is exactly what the rules forbid, and it would be worth
nothing on a question set we have not seen.

## `paraphrases.json`

Faithful rewrites of 25 questions whose answers were verified against source
documents, with entity names left intact so the variable under test is whether
the pipeline still recognises which calculation is wanted once the wording
changes. This is the closest available proxy for the graded set.

    engine alone   13/25   52%
    with the LLM   24/25   96%

## `score.py`

Grades a submission under this round's rules: money within max(1 rupee, 0.5%),
count and days exact, percent within 0.05, and no partial credit.

## `mock_llm.py`

Stands in for the provided endpoint, which exists only on the grading machine.
Without it the whole LLM path would ship having never executed. Run it on one of
the four ports allotted to us (8112-8115) and point `LLM_BASE_URL` at it.
