"""
answer_engine.py — Deterministic question answering engine (100% offline).

Two-stage architecture:
  Stage 1: Pattern-based question parser (no LLM needed)
  Stage 2: SQL-driven answer computation

Usage:
    python src/answer_engine.py
    python src/answer_engine.py --questions path/to/questions.json
    python src/answer_engine.py --output submission.jsonl
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import DB_PATH, SAMPLE_QUESTIONS_PATH, PROJECT_ROOT
from src.money import parse_indian_money, format_as_answer


# ── Stage 1: Deterministic Question Parser ─────────────────────────────────

def parse_question(conn, question_text: str) -> dict:
    """Parse question using pattern matching against DB entities."""
    # Load entity lists from DB
    db_clients = [r[0] for r in
                  conn.execute('SELECT client_name FROM clients').fetchall()]
    db_engineers = [r[0] for r in
                    conn.execute('SELECT name FROM engineers').fetchall()]
    db_projects = [r[0] for r in
                   conn.execute('SELECT project_name FROM works').fetchall()]
    
    qlow = question_text.lower()
    
    # Match client (longest name first to avoid partial matches)
    client = None
    for c in sorted(db_clients, key=len, reverse=True):
        if c.lower() in qlow:
            client = c
            break
    
    # Match engineer
    eng = None
    for e in sorted(db_engineers, key=len, reverse=True):
        if e.lower() in qlow:
            eng = e
            break
    
    # Match project
    proj = None
    for p in sorted(db_projects, key=len, reverse=True):
        clean_p = re.sub(r'[\u2014\u2013\-]', ' ', p).lower()
        clean_q = re.sub(r'[\u2014\u2013\-]', ' ', question_text).lower()
        if clean_p in clean_q:
            proj = p
            break
    if not proj:
        pkg_m = re.search(r'(?:package|pkg)\s*[-#]?\s*(\d+)', question_text, re.I)
        if pkg_m:
            cur = conn.execute(
                "SELECT project_name FROM works WHERE project_name LIKE ?",
                (f"%Pkg-{pkg_m.group(1)}%",))
            row = cur.fetchone()
            if row:
                proj = row[0]
    
    # Extract grading keyword
    grading = None
    if 'excellent' in qlow:
        grading = 'Excellent'
    elif 'very good' in qlow:
        grading = 'Very Good'
    elif 'satisfactory' in qlow:
        grading = 'Satisfactory'
    elif 'good' in qlow and 'very good' not in qlow:
        grading = 'Good'
    
    # Extract role
    role = None
    if 'as prime' in qlow:
        role = 'Prime'
    elif 'sub-contractor' in qlow or 'as sub' in qlow:
        role = 'Sub-contractor'
    
    # Extract exclusion category
    exclude = None
    excl_m = re.search(r'excluding\s+([\w\s]+?)(?:\s+projects|\s+contracts|\s+works|,|\?|$)',
                       qlow)
    if excl_m:
        exclude = excl_m.group(1).strip()
    
    # Extract threshold value (numbers in words or digits)
    threshold_val = None
    thresh_m = re.search(
        r'([\w-]+(?:\s+[\w-]+)*)\s+crore\s+(?:mark|threshold|level|limit|line)',
        qlow)
    if thresh_m:
        from src.money import _words_to_number
        word_val = _words_to_number(thresh_m.group(1) + ' crore')
        if word_val:
            threshold_val = int(word_val)
    if not threshold_val:
        thresh_m = re.search(
            r'(?:crossing|hitting|above|exceeding|over)\s+(?:the\s+)?'
            r'(?:INR\s+)?([\d.]+)\s*(?:Cr|Crore)', qlow)
        if thresh_m:
            threshold_val = int(float(thresh_m.group(1)) * 10_000_000)
    
    # Extract target value (for gap_to_threshold)
    target_val = None
    tgt_m = re.search(
        r'(?:credential target|target|reach)\s+(?:of\s+)?'
        r'(?:INR\s+)?([\d.]+)\s*(?:Cr|Crore)', qlow)
    if tgt_m:
        target_val = int(float(tgt_m.group(1)) * 10_000_000)
    if not target_val:
        tgt_m = re.search(r'inr\s+(\d+)\s*(?:cr|crore)', qlow) or \
                re.search(r'(\d+)\s*(?:cr|crore)\s+(?:mark|credential|target)', qlow)
        if tgt_m:
            target_val = int(float(tgt_m.group(1)) * 10_000_000)
    
    # Extract date reference
    date_ref = None
    dm = re.search(r'(\d{4}-\d{2}-\d{2})', question_text)
    if dm:
        date_ref = dm.group(1)
    else:
        # Try "Month DD, YYYY"
        dm = re.search(r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})', question_text)
        if dm:
            from src.extract_local_fast import parse_date
            date_ref = parse_date(dm.group(1))
    
    # Extract cert type
    cert_type = None
    if 'pmp' in qlow:
        cert_type = 'PMP'
    elif 'six sigma black belt' in qlow:
        cert_type = 'Six Sigma Black Belt'
    elif 'six sigma green belt' in qlow:
        cert_type = 'Six Sigma Green Belt'
    elif 'six sigma' in qlow:
        cert_type = 'Six Sigma'
    
    # Extract cert id
    cert_id = None
    cidm = re.search(r'(PMI-\d+|ASQ-\d+)', question_text)
    if cidm:
        cert_id = cidm.group(1)
    
    # Classify question shape
    shape = classify_shape(qlow)
    
    return {
        'question_shape': shape,
        'client_name': client,
        'engineer_name': eng,
        'project_name': proj,
        'filter_grading': grading,
        'filter_role': role,
        'exclude_category': exclude,
        'threshold_value': threshold_val,
        'target_value': target_val,
        'date_reference': date_ref,
        'cert_type': cert_type,
        'cert_id': cert_id,
    }


def classify_shape(qlow: str) -> str:
    """Classify the question into a shape based on keyword patterns."""
    # Absence: missing reference letters
    if ('lack' in qlow and 'reference letter' in qlow) or \
       'no client reference letter' in qlow or \
       'missing.*reference' in qlow:
        return 'absence'
    
    # Date span: days between two events
    if 'days passed' in qlow or 'exact interval' in qlow or \
       'days elapsed' in qlow or 'how many days' in qlow:
        return 'date_span'
    
    # Distinct count: number of unique categories
    if 'different categories' in qlow or 'distinct work' in qlow or \
       'how many distinct' in qlow:
        return 'distinct_count'
    
    # Referenced share: percentage with reference letters
    if ('share' in qlow and 'reference' in qlow) or \
       'divided by the total' in qlow or \
       'share of completed assignments' in qlow or \
       'formal verification' in qlow or \
       ('percentage' in qlow and 'reference' in qlow):
        return 'referenced_share'
    
    # Gap to threshold: how much more work needed
    if 'additional work' in qlow or 'credential target' in qlow or \
       'how much more' in qlow or 'shortfall' in qlow:
        return 'gap_to_threshold'
    
    # Rank value: difference between largest and second-largest
    if 'largest completed work exceed' in qlow or \
       'difference between the largest' in qlow or \
       'largest work value' in qlow:
        return 'rank_value'
    
    # Threshold aggregate: sum of works above a value
    if 'crossing' in qlow or 'hitting' in qlow or 'exceeding' in qlow:
        return 'threshold_aggregate'
    
    # Role split: sum filtered by Prime/Sub role
    if 'as prime' in qlow:
        return 'role_split'
    
    # Exclusion aggregate: sum excluding a category
    if 'excluding' in qlow:
        return 'exclusion_aggregate'
    
    # Doc filtered aggregate: sum filtered by grading
    if 'graded excellent' in qlow or 'marked satisfactory' in qlow or \
       'graded' in qlow or 'rated' in qlow:
        return 'doc_filtered_aggregate'
    
    # Average work size
    if 'average size' in qlow or 'mean size' in qlow or 'average value' in qlow:
        return 'avg_work_size'
    
    # Temporal chain: sum of works after a date
    if 'wrapped up after' in qlow or 'completed after' in qlow or \
       'finished after' in qlow:
        return 'temporal_chain'
    
    # Hop aggregate: multi-hop to sum all works for a commissioning client
    if 'combined value of every' in qlow or \
       'total value of all completed' in qlow or \
       'aggregate value' in qlow or \
       'commissioning client' in qlow:
        return 'hop_aggregate'
    
    # Count works
    if 'how many works' in qlow or 'how many projects' in qlow or \
       'number of works' in qlow or 'number of projects' in qlow:
        return 'count_works'
    
    # General aggregate: sum of all works for a client
    if 'total' in qlow and ('value' in qlow or 'worth' in qlow):
        return 'general_aggregate'
    
    return 'other'


# ── Stage 2: Query Handlers ─────────────────────────────────────────────────

def find_client_id(conn, client_name: str):
    """Find client_id by name with progressively fuzzy matching."""
    if not client_name:
        return None
    
    # Exact
    cur = conn.execute(
        "SELECT client_id FROM clients WHERE client_name = ?",
        (client_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    
    # Case-insensitive
    cur = conn.execute(
        "SELECT client_id FROM clients WHERE LOWER(client_name) = LOWER(?)",
        (client_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    
    # LIKE (substring)
    cur = conn.execute(
        "SELECT client_id FROM clients WHERE client_name LIKE ?",
        (f"%{client_name}%",))
    row = cur.fetchone()
    if row:
        return row[0]
    
    # Try splitting on comma and matching parts
    parts = [p.strip() for p in client_name.split(',')]
    for part in parts:
        if len(part) > 5:
            cur = conn.execute(
                "SELECT client_id FROM clients WHERE client_name LIKE ?",
                (f"%{part}%",))
            row = cur.fetchone()
            if row:
                return row[0]
    
    return None


def find_engineer_id(conn, engineer_name: str):
    """Find engineer_id by name."""
    if not engineer_name:
        return None
    
    cur = conn.execute(
        "SELECT engineer_id FROM engineers WHERE name = ?",
        (engineer_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    
    cur = conn.execute(
        "SELECT engineer_id FROM engineers WHERE name LIKE ?",
        (f"%{engineer_name}%",))
    row = cur.fetchone()
    if row:
        return row[0]
    
    return None


def get_engineer_cert(conn, engineer_id: int,
                      cert_type: str = None, cert_id: str = None) -> dict:
    """Get a specific certificate for an engineer."""
    if cert_id:
        cur = conn.execute("""
            SELECT cert_type, cert_id, issue_date, expiry_date
            FROM engineer_certs
            WHERE engineer_id = ? AND cert_id = ?
        """, (engineer_id, cert_id))
    elif cert_type:
        cur = conn.execute("""
            SELECT cert_type, cert_id, issue_date, expiry_date
            FROM engineer_certs
            WHERE engineer_id = ? AND cert_type LIKE ?
        """, (engineer_id, f"%{cert_type}%"))
    else:
        cur = conn.execute("""
            SELECT cert_type, cert_id, issue_date, expiry_date
            FROM engineer_certs WHERE engineer_id = ?
        """, (engineer_id,))
    
    row = cur.fetchone()
    if row:
        return {"cert_type": row[0], "cert_id": row[1],
                "issue_date": row[2], "expiry_date": row[3]}
    return None


# ── Shape Handlers ──────────────────────────────────────────────────────────

def handle_absence(conn, params: dict) -> float:
    """Count works for a client that have no reference letter."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    cur = conn.execute("""
        SELECT COUNT(*) FROM works
        WHERE client_id = ? AND has_reference_letter = 0
    """, (client_id,))
    return cur.fetchone()[0]


def handle_date_span(conn, params: dict) -> float:
    """Calculate days between a cert issue date and a project completion date."""
    engineer_id = find_engineer_id(conn, params.get("engineer_name"))
    project_name = params.get("project_name")
    date_ref = params.get("date_reference")
    
    # If no date_ref, try to get it from the engineer's cert
    if not date_ref and engineer_id:
        cert = get_engineer_cert(conn, engineer_id,
                                 params.get("cert_type"),
                                 params.get("cert_id"))
        if cert:
            date_ref = cert.get("issue_date")
    
    if not date_ref:
        return 0
    
    # Get the completion date of the project
    comp_date = None
    if project_name:
        cur = conn.execute(
            "SELECT completion_date FROM works WHERE project_name = ?",
            (project_name,))
        row = cur.fetchone()
        if row:
            comp_date = row[0]
    
    # If we have an engineer but no specific project, find their projects
    if not comp_date and engineer_id:
        cur = conn.execute("""
            SELECT w.completion_date FROM works w
            JOIN engineer_works ew ON w.work_id = ew.work_id
            WHERE ew.engineer_id = ? AND w.completion_date IS NOT NULL
            ORDER BY w.completion_date DESC LIMIT 1
        """, (engineer_id,))
        row = cur.fetchone()
        if row:
            comp_date = row[0]
    
    if not comp_date or not date_ref:
        return 0
    
    try:
        d1 = datetime.strptime(date_ref, "%Y-%m-%d")
        d2 = datetime.strptime(comp_date, "%Y-%m-%d")
        return abs((d2 - d1).days)
    except (ValueError, TypeError):
        return 0


def handle_distinct_count(conn, params: dict) -> float:
    """Count distinct work categories for an engineer."""
    engineer_id = find_engineer_id(conn, params.get("engineer_name"))
    if not engineer_id:
        return 0
    cur = conn.execute("""
        SELECT COUNT(DISTINCT w.work_category)
        FROM works w
        JOIN engineer_works ew ON w.work_id = ew.work_id
        WHERE ew.engineer_id = ? AND w.work_category IS NOT NULL
    """, (engineer_id,))
    return cur.fetchone()[0]


def handle_hop_aggregate(conn, params: dict) -> float:
    """Multi-hop: find the commissioning client of a project, then sum
    ALL works for that client."""
    client_name = params.get("client_name")
    project_name = params.get("project_name")
    
    # If we have a project but no client, look up the client
    client_id = None
    if client_name:
        client_id = find_client_id(conn, client_name)
    
    if not client_id and project_name:
        cur = conn.execute(
            "SELECT client_id FROM works WHERE project_name LIKE ?",
            (f"%{project_name[:30]}%",))
        row = cur.fetchone()
        if row:
            client_id = row[0]
    
    if not client_id:
        return 0
    
    cur = conn.execute("""
        SELECT SUM(contract_value) FROM works
        WHERE client_id = ? AND contract_value IS NOT NULL
    """, (client_id,))
    result = cur.fetchone()[0]
    return result if result else 0


def handle_temporal_chain(conn, params: dict) -> float:
    """Sum contract values of an engineer's works completed AFTER a date."""
    engineer_id = find_engineer_id(conn, params.get("engineer_name"))
    if not engineer_id:
        return 0
    
    date_ref = params.get("date_reference")
    if not date_ref:
        cert = get_engineer_cert(conn, engineer_id,
                                 params.get("cert_type"),
                                 params.get("cert_id"))
        if cert:
            date_ref = cert.get("issue_date")
    
    if not date_ref:
        return 0
    
    cur = conn.execute("""
        SELECT SUM(w.contract_value)
        FROM works w
        JOIN engineer_works ew ON w.work_id = ew.work_id
        WHERE ew.engineer_id = ?
          AND w.completion_date > ?
          AND w.contract_value IS NOT NULL
    """, (engineer_id, date_ref))
    result = cur.fetchone()[0]
    return result if result else 0


def handle_avg_work_size(conn, params: dict) -> float:
    """Average contract value for a client's works."""
    client_name = params.get("client_name")
    project_name = params.get("project_name")
    
    # If we have a project but need the client
    if not client_name and project_name:
        cur = conn.execute("""
            SELECT c.client_name FROM works w
            JOIN clients c ON w.client_id = c.client_id
            WHERE w.project_name LIKE ?
        """, (f"%{project_name[:30]}%",))
        row = cur.fetchone()
        if row:
            client_name = row[0]
    
    client_id = find_client_id(conn, client_name)
    if not client_id:
        return 0
    
    cur = conn.execute("""
        SELECT contract_value FROM works
        WHERE client_id = ? AND contract_value IS NOT NULL
    """, (client_id,))
    values = [r[0] for r in cur.fetchall()]
    if not values:
        return 0
    
    avg = sum(values) / len(values)
    return int(avg)


def handle_doc_filtered_aggregate(conn, params: dict) -> float:
    """Sum contract values filtered by performance grading."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    
    grading = params.get("filter_grading", "")
    if not grading:
        return 0
    
    cur = conn.execute("""
        SELECT SUM(contract_value) FROM works
        WHERE client_id = ? AND performance_grading = ?
          AND contract_value IS NOT NULL
    """, (client_id, grading))
    result = cur.fetchone()[0]
    return result if result else 0


def handle_exclusion_aggregate(conn, params: dict) -> float:
    """Sum all works for a client EXCLUDING a specific category."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    
    exclude = params.get("exclude_category", "")
    
    cur = conn.execute("""
        SELECT SUM(contract_value) FROM works
        WHERE client_id = ?
          AND LOWER(work_category) NOT LIKE ?
          AND contract_value IS NOT NULL
    """, (client_id, f"%{exclude.lower()}%"))
    result = cur.fetchone()[0]
    return result if result else 0


def handle_gap_to_threshold(conn, params: dict) -> float:
    """How much more work to reach a target credential value."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    
    target = params.get("target_value")
    if not target:
        return 0
    
    cur = conn.execute("""
        SELECT SUM(contract_value) FROM works
        WHERE client_id = ? AND contract_value IS NOT NULL
    """, (client_id,))
    current = cur.fetchone()[0] or 0
    gap = target - current
    return max(0, gap)


def handle_rank_value(conn, params: dict) -> float:
    """Difference between largest and second-largest work value."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    
    cur = conn.execute("""
        SELECT contract_value FROM works
        WHERE client_id = ? AND contract_value IS NOT NULL
        ORDER BY contract_value DESC
        LIMIT 2
    """, (client_id,))
    rows = cur.fetchall()
    if len(rows) < 2:
        return 0
    return abs(rows[0][0] - rows[1][0])


def handle_referenced_share(conn, params: dict) -> float:
    """Percentage of a client's works that have reference letters."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    
    cur = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN has_reference_letter = 1 THEN 1 ELSE 0 END)
        FROM works WHERE client_id = ?
    """, (client_id,))
    row = cur.fetchone()
    total = row[0]
    ref_count = row[1] or 0
    
    if total == 0:
        return 0
    
    percentage = (ref_count / total) * 100
    return round(percentage, 2)


def handle_role_split(conn, params: dict) -> float:
    """Sum contract values filtered by role (Prime/Sub-contractor)."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    
    role = params.get("filter_role", "Prime")
    
    cur = conn.execute("""
        SELECT SUM(contract_value) FROM works
        WHERE client_id = ? AND role = ?
          AND contract_value IS NOT NULL
    """, (client_id, role))
    result = cur.fetchone()[0]
    return result if result else 0


def handle_threshold_aggregate(conn, params: dict) -> float:
    """Sum works for a client that are above a value threshold."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    
    threshold = params.get("threshold_value")
    if not threshold:
        return 0
    
    cur = conn.execute("""
        SELECT SUM(contract_value) FROM works
        WHERE client_id = ? AND contract_value >= ?
    """, (client_id, threshold))
    result = cur.fetchone()[0]
    return result if result else 0


def handle_count_works(conn, params: dict) -> float:
    """Count works for a client."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    cur = conn.execute(
        "SELECT COUNT(*) FROM works WHERE client_id = ?",
        (client_id,))
    return cur.fetchone()[0]


def handle_general_aggregate(conn, params: dict) -> float:
    """General sum of contract values for a client."""
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    cur = conn.execute("""
        SELECT SUM(contract_value) FROM works
        WHERE client_id = ? AND contract_value IS NOT NULL
    """, (client_id,))
    result = cur.fetchone()[0]
    return result if result else 0


# ── Shape Dispatcher ────────────────────────────────────────────────────────

SHAPE_HANDLERS = {
    "absence": handle_absence,
    "date_span": handle_date_span,
    "distinct_count": handle_distinct_count,
    "hop_aggregate": handle_hop_aggregate,
    "temporal_chain": handle_temporal_chain,
    "avg_work_size": handle_avg_work_size,
    "doc_filtered_aggregate": handle_doc_filtered_aggregate,
    "exclusion_aggregate": handle_exclusion_aggregate,
    "gap_to_threshold": handle_gap_to_threshold,
    "rank_value": handle_rank_value,
    "referenced_share": handle_referenced_share,
    "role_split": handle_role_split,
    "threshold_aggregate": handle_threshold_aggregate,
    "count_works": handle_count_works,
    "general_aggregate": handle_general_aggregate,
}


def answer_question(conn, question_text: str, qid: str = None) -> float:
    """Answer a single question using the two-stage pipeline."""
    # Stage 1: Parse the question (100% deterministic)
    params = parse_question(conn, question_text)
    shape = params.get("question_shape", "other")
    
    if qid:
        print(f"  [{qid}] Shape: {shape}")
        for key in ["client_name", "engineer_name", "project_name",
                     "filter_grading", "filter_role", "exclude_category",
                     "threshold_value", "target_value", "date_reference",
                     "cert_type", "cert_id"]:
            if params.get(key):
                print(f"         {key}: {params[key]}")
    
    # Stage 2: Execute the appropriate handler
    handler = SHAPE_HANDLERS.get(shape)
    
    if handler:
        try:
            answer = handler(conn, params)
            return format_as_answer(answer)
        except Exception as e:
            print(f"  Error in handler {shape}: {e}")
            import traceback
            traceback.print_exc()
            return 0
    else:
        print(f"  WARNING: No handler for shape '{shape}', returning 0")
        return 0


# ── Main ────────────────────────────────────────────────────────────────────

def answer_all_questions(questions_path: str, output_path: str = None):
    """Answer all questions from a JSON file."""
    with open(questions_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    questions = data.get("questions", data) if isinstance(data, dict) else data
    
    conn = sqlite3.connect(str(DB_PATH))
    
    print(f"Answering {len(questions)} questions...")
    print(f"Database: {DB_PATH}")
    print()
    
    results = []
    correct = 0
    total_with_expected = 0
    
    for q in questions:
        qid = q["qid"]
        question_text = q["question"]
        expected = q.get("answer")
        
        answer = answer_question(conn, question_text, qid)
        results.append({"qid": qid, "answer": answer})
        
        if expected is not None:
            total_with_expected += 1
            match = "OK" if answer == expected else "WRONG"
            if match == "OK":
                correct += 1
            print(f"  -> Answer: {answer} (expected: {expected}) [{match}]")
        else:
            print(f"  -> Answer: {answer}")
        print()
    
    conn.close()
    
    if total_with_expected > 0:
        print(f"{'='*50}")
        print(f"Score: {correct}/{total_with_expected}")
        print(f"{'='*50}")
    
    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
        print(f"\nSubmission written to: {output_path}")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Answer questions")
    parser.add_argument("--questions", default=str(SAMPLE_QUESTIONS_PATH),
                       help="Path to questions JSON file")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "submission.jsonl"),
                       help="Path to write submission JSONL")
    args = parser.parse_args()
    
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        print("Run 'python src/build_db.py' first.")
        sys.exit(1)
    
    answer_all_questions(args.questions, args.output)


if __name__ == "__main__":
    main()
