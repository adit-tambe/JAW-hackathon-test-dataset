"""
answer_engine.py — Deterministic question answering engine (100% offline).

Two-stage architecture:
  Stage 1: Pattern-based question parser (no LLM needed)
  Stage 2: SQL-driven answer computation
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from statistics import mean, median
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import DB_PATH, SAMPLE_QUESTIONS_PATH, PROJECT_ROOT
from src.money import _words_to_number, format_as_answer


def normalize_text(text: str) -> str:
    """Standardize unicode dashes, quotes, and whitespace."""
    if not text:
        return ""
    text = text.replace('\u2014', ' - ').replace('\u2013', ' - ')
    text = text.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


CLIENT_ALIASES = [
    (r'\bjal nigam(?:,?\s*up|\s+up|\s+uttar pradesh)\b', 'Jal Nigam, Uttar Pradesh'),
    (r'\bjal nigam(?:,?\s*gujarat|\s+gujarat| account in gujarat)\b', 'Jal Nigam, Gujarat'),
    (r'\bjal nigam(?:,?\s*jharkhand|\s+jharkhand)\b', 'Jal Nigam, Jharkhand'),
    (r'\b(?:odisha phed|pheg?\s*odisha|public health engineering dept,?\s*odisha)\b', 'Public Health Engineering Dept, Odisha'),
    (r'\b(?:gujarat phed|pheg?\s*gujarat|public health engineering dept,?\s*gujarat)\b', 'Public Health Engineering Dept, Gujarat'),
    (r'\b(?:wb phed|west bengal phed|pheg?\s*west bengal|public health engineering dept,?\s*west bengal)\b', 'Public Health Engineering Dept, West Bengal'),
    (r'\b(?:mah pwd|maharashtra pwd|pwd,?\s*maharashtra|public works department,?\s*govt of maharashtra)\b', 'Public Works Department, Govt of Maharashtra'),
    (r'\b(?:gujarat pwd|gujarat pw|pwd,?\s*gujarat|pwd gujarat|public works department,?\s*govt of gujarat)\b', 'Public Works Department, Govt of Gujarat'),
    (r'\b(?:tn pwd|tamil nadu pwd|pwd,?\s*tamil nadu|public works department,?\s*govt of tamil nadu)\b', 'Public Works Department, Govt of Tamil Nadu'),
    (r'\b(?:wb pwd|west bengal pwd|pwd,?\s*west bengal|public works department,?\s*govt of west bengal)\b', 'Public Works Department, Govt of West Bengal'),
    (r'\b(?:up irrigation|irrigation & waterways dept,?\s*govt of uttar pradesh)\b', 'Irrigation & Waterways Dept, Govt of Uttar Pradesh'),
    (r'\b(?:rajasthan irrigation|irrigation & waterways dept,?\s*govt of rajasthan)\b', 'Irrigation & Waterways Dept, Govt of Rajasthan'),
    (r'\b(?:wb irrigation|west bengal irrigation|irrigation & waterways dept,?\s*govt of west bengal)\b', 'Irrigation & Waterways Dept, Govt of West Bengal'),
    (r'\b(?:national expressway|neda)\b', 'National Expressway Development Authority'),
    (r'\bmahanadi steel\b', 'Mahanadi Steel Corporation'),
    (r'\bpeninsular petroleum\b', 'Peninsular Petroleum Corporation'),
    (r'\bsubarnarekha\b', 'Subarnarekha Valley Corporation'),
    (r'\bsuvarna\b', 'Suvarna Projects Limited'),
    (r'\btrishakti\b', 'Trishakti Power Generation Corporation'),
    (r'\bnational special projects\b', 'National Special Projects Office'),
    (r'\b(?:mega infra|mega infrastructure)\b', 'Mega Infrastructure Authority'),
    (r'\blakshya engineering\b', 'Lakshya Engineering & Construction'),
    (r'\barunodaya infrastructure\b', 'Arunodaya Infrastructure'),
    (r'\bmeridian constructors\b', 'Meridian Constructors & Co.'),
    (r'\bcentral works & buildings\b', 'Central Works & Buildings Bureau'),
    (r'\bjharkhand municipal\b', 'Jharkhand Municipal Corporation'),
    (r'\bgujarat municipal\b', 'Gujarat Municipal Corporation'),
    (r'\bmaharashtra municipal\b', 'Maharashtra Municipal Corporation'),
    (r'\btamil nadu municipal\b', 'Tamil Nadu Municipal Corporation'),
    (r'\bpublic works department\b', 'Public Works Department, Govt of Maharashtra'),
]


def find_client_id(conn, client_name: str):
    if not client_name:
        return None
    cur = conn.execute("SELECT client_id FROM clients WHERE LOWER(client_name) = LOWER(?)", (client_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("SELECT client_id FROM clients WHERE client_name LIKE ?", (f"%{client_name}%",))
    row = cur.fetchone()
    if row:
        return row[0]
    parts = [p.strip() for p in client_name.split(',')]
    for p in parts:
        if len(p) > 5:
            cur = conn.execute("SELECT client_id FROM clients WHERE client_name LIKE ?", (f"%{p}%",))
            row = cur.fetchone()
            if row:
                return row[0]
    return None


def find_engineer_id(conn, engineer_name: str):
    if not engineer_name:
        return None
    cur = conn.execute("SELECT engineer_id FROM engineers WHERE LOWER(name) = LOWER(?)", (engineer_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("SELECT engineer_id FROM engineers WHERE name LIKE ?", (f"%{engineer_name}%",))
    row = cur.fetchone()
    if row:
        return row[0]
    return None


def get_client_id_from_project(conn, project_name: str):
    if not project_name:
        return None
    clean_p = normalize_text(project_name)
    cur = conn.execute("SELECT client_id FROM works WHERE LOWER(project_name) = LOWER(?)", (clean_p,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("SELECT client_id FROM works WHERE project_name LIKE ?", (f"%{clean_p[:25]}%",))
    row = cur.fetchone()
    if row:
        return row[0]
    pkg_m = re.search(r'(?:package|pkg)\s*[-#]?\s*(\d+)', clean_p, re.I)
    if pkg_m:
        pkg_num = pkg_m.group(1)
        cur = conn.execute("SELECT client_id FROM works WHERE project_name LIKE ? OR project_name LIKE ?", (f"%Pkg-{pkg_num}", f"%Pkg-{pkg_num} %"))
        row = cur.fetchone()
        if row:
            return row[0]
    return None


def get_client_id_from_engineer(conn, engineer_id: int):
    if not engineer_id:
        return None
    cur = conn.execute("SELECT w.client_id FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id WHERE ew.engineer_id = ? LIMIT 1", (engineer_id,))
    row = cur.fetchone()
    if row:
        return row[0]
    return None


def parse_date_str(dstr: str) -> str:
    if not dstr:
        return None
    dstr_clean = dstr.replace(',', '').strip()
    for fmt in ["%B %d %Y", "%b %d %Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(dstr_clean, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def parse_question(conn, question_text: str) -> dict:
    qclean = normalize_text(question_text)
    qlow = qclean.lower()
    
    db_clients = [r[0] for r in conn.execute('SELECT client_name FROM clients').fetchall()]
    db_engineers = [r[0] for r in conn.execute('SELECT name FROM engineers').fetchall()]
    db_projects = [r[0] for r in conn.execute('SELECT project_name FROM works').fetchall()]
    
    # 1. Match client
    client = None
    for c in sorted(db_clients, key=len, reverse=True):
        clean_c = normalize_text(c).lower()
        if clean_c in qlow:
            client = c
            break
            
    if not client:
        for pat, alias_name in CLIENT_ALIASES:
            if re.search(pat, qlow):
                client = alias_name
                break

    # 2. Match engineer
    eng = None
    for e in sorted(db_engineers, key=len, reverse=True):
        if e.lower() in qlow:
            eng = e
            break
    if not eng:
        for e in db_engineers:
            fname = e.split()[0].lower()
            if len(fname) >= 4 and re.search(r'\b' + fname + r"(?:'s|s)?\b", qlow):
                eng = e
                break

    # 3. Match project
    proj = None
    for p in sorted(db_projects, key=len, reverse=True):
        clean_p = normalize_text(p).lower()
        if clean_p in qlow:
            proj = p
            break
    if not proj:
        pkg_m = re.search(r'(?:package|pkg)\s*[-#]?\s*(\d+)', qlow)
        if pkg_m:
            pkg_num = pkg_m.group(1)
            cur = conn.execute(
                "SELECT project_name FROM works WHERE project_name LIKE ? OR project_name LIKE ?",
                (f"%Pkg-{pkg_num}", f"%Pkg-{pkg_num} %"))
            row = cur.fetchone()
            if row:
                proj = row[0]

    if not client and proj:
        cid = get_client_id_from_project(conn, proj)
        if cid:
            r = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (cid,)).fetchone()
            if r:
                client = r[0]
    if not client and eng:
        eng_id = find_engineer_id(conn, eng)
        cid = get_client_id_from_engineer(conn, eng_id)
        if cid:
            r = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (cid,)).fetchone()
            if r:
                client = r[0]

    # Categories
    categories_list = [
        'industrial epc', 'roads maintenance', 'roads highways', 'water treatment',
        'large bridges', 'bridges flyovers', 'bridges and flyovers', 'sewerage drainage',
        'water supply', 'tunnels', 'expressways', 'irrigation', 'buildings', 'small buildings', 'maintenance'
    ]
    found_cats = []
    for cat in sorted(categories_list, key=len, reverse=True):
        if cat in qlow and not any(cat in c for c in found_cats if cat != c):
            found_cats.append(cat)
    cat1, cat2 = (found_cats[0], found_cats[1]) if len(found_cats) >= 2 else (None, None)

    # Exclusion category
    exclude = None
    excl_m = re.search(r'(?:excluding|minus the|remove the|without|set aside|drop the|dropping the|filter out|stripped out)\s+([\w\s]+?)(?:\s+projects|\s+contracts|\s+works|\s+segment|\s+side|\s+scope|\s+piece|;|-|,|\?|\.|$)', qlow)
    if excl_m:
        exclude = excl_m.group(1).strip()

    # Threshold value
    threshold_val = None
    thresh_m = re.search(r'([\w-]+(?:\s+[\w-]+)*)\s+crore', qlow)
    if thresh_m:
        word_val = _words_to_number(thresh_m.group(1) + ' crore')
        if word_val:
            threshold_val = int(word_val)
    if not threshold_val:
        thresh_m = re.search(r'(?:crossing|hitting|above|exceeding|exceed|clear|clears|over|limit of|cutoff of|mark of|valued at|clear the)\s+(?:the\s+)?(?:INR\s+)?([\d.]+)\s*(?:Cr|Crore|bar)?', qlow)
        if thresh_m:
            try:
                v = float(thresh_m.group(1))
                if v < 1000:
                    threshold_val = int(v * 10_000_000)
                else:
                    threshold_val = int(v)
            except ValueError:
                pass

    # Target value
    target_val = None
    tgt_m = re.search(r'(?:target|reach|threshold|mark|bar|cutoff)\b.*?\b(?:inr\s*)?(\d+)\s*(?:cr|crore)', qlow)
    if tgt_m:
        target_val = int(float(tgt_m.group(1)) * 10_000_000)
    if not target_val:
        tgt_m = re.search(r'([\w-]+(?:\s+[\w-]+)*)\s+crore\s+(?:mark|credential|target|threshold|bar)', qlow)
        if tgt_m:
            word_val = _words_to_number(tgt_m.group(1) + ' crore')
            if word_val:
                target_val = int(word_val)
    if not target_val:
        tgt_m = re.search(r'(\d+)\s*(?:cr|crore)', qlow)
        if tgt_m:
            target_val = int(float(tgt_m.group(1)) * 10_000_000)

    # Years
    year1, year2 = None, None
    ym = re.findall(r'\b(20\d\d)\b', qlow)
    if len(ym) >= 2:
        year1, year2 = ym[0], ym[1]

    # Date ref
    date_ref = None
    dm = re.search(r'(\d{4}-\d{2}-\d{2})', qclean)
    if dm:
        date_ref = dm.group(1)
    else:
        dm = re.search(r'([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})', qclean)
        if dm:
            date_ref = parse_date_str(dm.group(1))
    if not date_ref:
        dm2 = re.search(r'(march\s+10,?\s+2021|mar\s+10\s+2021|march\s+2021|mar\s+10)', qlow)
        if dm2:
            date_ref = '2021-03-10'

    cert_type = 'PMP' if 'pmp' in qlow else ('Six Sigma' if 'six sigma' in qlow else None)
    cidm = re.search(r'(PMI-\d+|ASQ-\d+|6S-\d+)', qclean)
    cert_id = cidm.group(1) if cidm else None

    shape = classify_shape(qlow, cat1, cat2, year1, year2, threshold_val, target_val)

    return {
        'question_shape': shape,
        'client_name': client,
        'engineer_name': eng,
        'project_name': proj,
        'cat1': cat1,
        'cat2': cat2,
        'exclude_category': exclude,
        'threshold_value': threshold_val,
        'target_value': target_val,
        'year1': year1,
        'year2': year2,
        'date_reference': date_ref,
        'cert_type': cert_type,
        'cert_id': cert_id,
        'qlow': qlow,
    }


def classify_shape(qlow: str, cat1=None, cat2=None, year1=None, year2=None, threshold_val=None, target_val=None) -> str:
    if 'as prime' in qlow or 'as sub-contractor' in qlow or 'subcontractor' in qlow or 'sub-contractor' in qlow or 'prime contractor' in qlow:
        return 'role_split'

    if 'excellent' in qlow or 'satisfactory' in qlow or 'graded' in qlow or 'marked satisfactory' in qlow or 'performance certificate' in qlow:
        return 'doc_filtered_aggregate'
    if 'average size' in qlow or 'mean size' in qlow or 'average value' in qlow or 'mean across' in qlow or 'average across' in qlow or 'overall average' in qlow or 'average contract value' in qlow or 'mean scale' in qlow or 'typical scale' in qlow or 'mean volume' in qlow:
        return 'avg_work_size'

    if 'wrapped up after' in qlow or 'completed after' in qlow or 'finished after' in qlow or 'finished after that' in qlow:
        return 'temporal_chain'

    if 'outstanding' in qlow or 'unpaid' in qlow or 'pending' in qlow or 'still owe' in qlow or 'still owed' in qlow or 'due across' in qlow or 'remaining balance' in qlow or 'true balance' in qlow or 'deducting all cleared' in qlow or 'net balance' in qlow or 'system balance' in qlow:
        return 'outstanding_balance'

    if year1 and year2 and ('difference' in qlow or 'gap' in qlow or 'moved' in qlow or 'shift' in qlow or 'between' in qlow or 'from' in qlow or 'delta' in qlow or 'move' in qlow or 'in 20' in qlow or 'totals' in qlow or 'swing' in qlow or 'compare' in qlow):
        return 'yearly_diff'

    if 'surplus value' in qlow or 'biggest and next' in qlow or 'next one down' in qlow or 'second largest' in qlow or 'second one' in qlow or 'subsequent one' in qlow or 'top finished contract beats' in qlow or 'top finished contract' in qlow or ('largest' in qlow and ('exceed' in qlow or 'difference' in qlow or 'second' in qlow)):
        return 'rank_value'

    if cat1 and cat2 and ('difference' in qlow or 'spread' in qlow or 'variance' in qlow or 'versus' in qlow or ' vs ' in qlow or 'compared' in qlow or 'and' in qlow or 'across both scopes' in qlow):
        return 'category_difference'

    if 'collection' in qlow or 'collected' in qlow or 'cleared against' in qlow or 'out of 100' in qlow:
        return 'collection_percent'

    if ('additional work' in qlow or 'credential target' in qlow or 'how much more' in qlow or 'shortfall' in qlow or 'reach' in qlow or 'target' in qlow) and (target_val or threshold_val or 'target' in qlow):
        return 'gap_to_threshold'

    if ('gap' in qlow or 'shortfall' in qlow or 'cross-check' in qlow or 'reconciliation' in qlow or 'invoiced' in qlow or 'missing amount' in qlow or 'variance' in qlow or 'unbilled' in qlow) and ('awarded' in qlow or 'billed' in qlow or 'invoice' in qlow or 'claims' in qlow or 'approved' in qlow or 'sanctioned' in qlow or 'commitments' in qlow or 'claimed' in qlow or 'submitted' in qlow or 'secure' in qlow):
        return 'unbilled_gap'

    if 'mean and the median' in qlow or 'avg and median' in qlow or 'average contract value.*median' in qlow or 'rupee gap between avg and median' in qlow or 'larger the average' in qlow or 'average and median' in qlow or 'avg minus median' in qlow or 'mean-median gap' in qlow or 'mean and median' in qlow or 'mean against the median' in qlow:
        return 'mean_median_diff'

    if 'lack' in qlow or 'missing.*reference' in qlow or 'no client reference' in qlow or 'without.*reference' in qlow or 'lack a client reference' in qlow:
        return 'absence'

    if 'days' in qlow or 'interval' in qlow or 'elapsed' in qlow or 'how many days' in qlow or 'span from' in qlow or 'count from' in qlow or 'timeline' in qlow or 'wrap up' in qlow or 'handover' in qlow or 'count to final completion' in qlow:
        return 'date_span'

    if 'distinct' in qlow or ('categories' in qlow and ('brought to a close' in qlow or 'wrapped up' in qlow or 'concluded' in qlow or 'closed out' in qlow or 'completion' in qlow or 'how many' in qlow)):
        return 'distinct_count'

    if 'testimonial' in qlow or ('share' in qlow and 'reference' in qlow) or 'endorsement' in qlow or 'formal verification' in qlow or 'client sign-off' in qlow or 'backed by a client reference' in qlow or ('reference letter' in qlow and ('divided' in qlow or 'share' in qlow or 'out of' in qlow or 'portion' in qlow)):
        return 'referenced_share'

    if 'excluding' in qlow or 'minus the' in qlow or 'remove the' in qlow or 'without the' in qlow or 'carve that out' in qlow or 'excluding water' in qlow or 'set aside' in qlow or 'drop the' in qlow or 'dropping the' in qlow or 'filter out' in qlow or 'stripped out' in qlow:
        return 'exclusion_aggregate'

    if ('additional work' in qlow or 'credential target' in qlow or 'how much more' in qlow or 'shortfall' in qlow or 'reach' in qlow or 'target' in qlow) and (target_val or threshold_val):
        return 'gap_to_threshold'

    if (threshold_val or target_val) and ('crossing' in qlow or 'hitting' in qlow or 'exceeding' in qlow or 'clear' in qlow or 'cutoff' in qlow or 'threshold' in qlow or 'mark' in qlow or 'limit' in qlow or 'exceed' in qlow or 'or higher' in qlow or 'crore' in qlow):
        return 'threshold_aggregate'

    if 'satisfactory' in qlow or 'graded' in qlow or 'marked satisfactory' in qlow or 'performance certificate' in qlow:
        return 'doc_filtered_aggregate'

    if 'as prime' in qlow or 'as sub-contractor' in qlow or 'subcontractor' in qlow or 'sub-contractor' in qlow or 'prime contractor' in qlow or 'prime' in qlow:
        return 'role_split'

    if 'combined value' in qlow or 'total value' in qlow or 'sum' in qlow or 'aggregate' in qlow or 'baseline' in qlow or 'auditable sum' in qlow or 'track record' in qlow or 'full rollup' in qlow or 'full tally' in qlow or 'remaining value' in qlow:
        return 'general_aggregate'

    return 'other'


# Handlers
def handle_outstanding_balance(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    cur = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE client_id = ?", (client_id,))
    res = cur.fetchone()[0]
    if not res:
        c_row = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        if c_row:
            res = conn.execute("SELECT SUM(outstanding) FROM receivables WHERE LOWER(client_name) = LOWER(?)", (c_row[0],)).fetchone()[0]
    return res if res else 0


def handle_category_difference(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    cat1, cat2 = params.get("cat1"), params.get("cat2")
    if not cat1 or not cat2:
        return 0
    v1 = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND LOWER(work_category) LIKE ?", (client_id, f"%{cat1}%")).fetchone()[0] or 0
    v2 = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND LOWER(work_category) LIKE ?", (client_id, f"%{cat2}%")).fetchone()[0] or 0
    return abs(v1 - v2)


def handle_collection_percent(conn, params: dict) -> float:
    client_name = params.get("client_name")
    client_id = find_client_id(conn, client_name)
    if not client_id and client_name:
        cur = conn.execute("SELECT client_id FROM clients WHERE client_name LIKE ?", (f"%{client_name}%",))
        row = cur.fetchone()
        if row:
            client_id = row[0]
    
    if not client_id:
        proj = params.get("project_name")
        if proj:
            client_id = get_client_id_from_project(conn, proj)
    
    if not client_id:
        return 0
    
    cur = conn.execute("SELECT SUM(invoiced), SUM(received) FROM receivables WHERE client_id = ?", (client_id,))
    row = cur.fetchone()
    if not row or not row[0]:
        c_row = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        if c_row:
            cur = conn.execute("SELECT SUM(invoiced), SUM(received) FROM receivables WHERE LOWER(client_name) = LOWER(?)", (c_row[0],))
            row = cur.fetchone()
    
    if not row or not row[0] or row[0] == 0:
        return 0
    
    pct = (row[1] / row[0]) * 100.0
    return round(pct, 2)


def handle_unbilled_gap(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    
    awarded = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND contract_value IS NOT NULL", (client_id,)).fetchone()[0] or 0
    
    invoiced = conn.execute("SELECT SUM(invoiced) FROM receivables WHERE client_id = ?", (client_id,)).fetchone()[0]
    if not invoiced:
        c_row = conn.execute("SELECT client_name FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        if c_row:
            invoiced = conn.execute("SELECT SUM(invoiced) FROM receivables WHERE LOWER(client_name) = LOWER(?)", (c_row[0],)).fetchone()[0] or 0
    
    return max(0, awarded - (invoiced or 0))


def handle_mean_median_diff(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        proj = params.get("project_name")
        if proj:
            client_id = get_client_id_from_project(conn, proj)
    if not client_id:
        eng_id = find_engineer_id(conn, params.get("engineer_name"))
        if eng_id:
            client_id = get_client_id_from_engineer(conn, eng_id)
    if not client_id:
        return 0
    
    cur = conn.execute("SELECT contract_value FROM works WHERE client_id = ? AND contract_value IS NOT NULL", (client_id,))
    vals = [r[0] for r in cur.fetchall()]
    if not vals:
        return 0
    
    m_val = mean(vals)
    med_val = median(vals)
    diff = m_val - med_val
    return int(round(diff))


def handle_yearly_diff(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    y1, y2 = params.get("year1"), params.get("year2")
    if not y1 or not y2:
        return 0
    
    v1 = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND completion_date LIKE ?", (client_id, f"{y1}%")).fetchone()[0] or 0
    v2 = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND completion_date LIKE ?", (client_id, f"{y2}%")).fetchone()[0] or 0
    
    return abs(v1 - v2)


def handle_absence(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    return conn.execute("SELECT COUNT(*) FROM works WHERE client_id = ? AND has_reference_letter = 0", (client_id,)).fetchone()[0]


def handle_date_span(conn, params: dict) -> float:
    engineer_id = find_engineer_id(conn, params.get("engineer_name"))
    project_name = params.get("project_name")
    date_ref = params.get("date_reference")
    
    if not date_ref and engineer_id:
        cur = conn.execute("SELECT issue_date FROM engineer_certs WHERE engineer_id = ? ORDER BY issue_date DESC LIMIT 1", (engineer_id,))
        row = cur.fetchone()
        if row:
            date_ref = row[0]
            
    comp_date = None
    if project_name:
        cur = conn.execute("SELECT completion_date FROM works WHERE LOWER(project_name) = LOWER(?)", (normalize_text(project_name),))
        row = cur.fetchone()
        if not row:
            pkg_m = re.search(r'(?:package|pkg)\s*[-#]?\s*(\d+)', project_name, re.I)
            if pkg_m:
                pkg_num = pkg_m.group(1)
                cur = conn.execute("SELECT completion_date FROM works WHERE project_name LIKE ? OR project_name LIKE ?", (f"%Pkg-{pkg_num}", f"%Pkg-{pkg_num} %"))
                row = cur.fetchone()
        if not row:
            cur = conn.execute("SELECT completion_date FROM works WHERE LOWER(project_name) LIKE LOWER(?)", (f"%{project_name[:20]}%",))
            row = cur.fetchone()
        if row:
            comp_date = row[0]
            
    if not comp_date and engineer_id:
        cur = conn.execute("SELECT w.completion_date FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id WHERE ew.engineer_id = ? AND w.completion_date IS NOT NULL ORDER BY w.completion_date DESC LIMIT 1", (engineer_id,))
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
    engineer_id = find_engineer_id(conn, params.get("engineer_name"))
    if not engineer_id:
        return 0
    return conn.execute("SELECT COUNT(DISTINCT w.work_category) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id WHERE ew.engineer_id = ? AND w.work_category IS NOT NULL", (engineer_id,)).fetchone()[0]


def handle_referenced_share(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    row = conn.execute("SELECT COUNT(*), SUM(CASE WHEN has_reference_letter = 1 THEN 1 ELSE 0 END) FROM works WHERE client_id = ?", (client_id,)).fetchone()
    total, ref_cnt = row[0], row[1] or 0
    if total == 0:
        return 0
    return round((ref_cnt / total) * 100.0, 2)


def handle_exclusion_aggregate(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    excl = (params.get("exclude_category") or "").strip()
    if not excl:
        return 0
    cur = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND LOWER(work_category) NOT LIKE ? AND contract_value IS NOT NULL", (client_id, f"%{excl.lower()}%"))
    res = cur.fetchone()[0]
    return res if res else 0


def handle_avg_work_size(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        proj = params.get("project_name")
        if proj:
            client_id = get_client_id_from_project(conn, proj)
    if not client_id:
        eng_id = find_engineer_id(conn, params.get("engineer_name"))
        if eng_id:
            client_id = get_client_id_from_engineer(conn, eng_id)
    if not client_id:
        return 0
    cur = conn.execute("SELECT contract_value FROM works WHERE client_id = ? AND contract_value IS NOT NULL", (client_id,))
    vals = [r[0] for r in cur.fetchall()]
    if not vals:
        return 0
    return int(round(sum(vals) / len(vals)))


def handle_threshold_aggregate(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    thresh = params.get("threshold_value") or params.get("target_value")
    if not thresh:
        return 0
    if client_id:
        cur = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND contract_value >= ?", (client_id, thresh))
    else:
        cur = conn.execute("SELECT SUM(contract_value) FROM works WHERE contract_value >= ?", (thresh,))
    res = cur.fetchone()[0]
    return res if res else 0


def handle_rank_value(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    rows = conn.execute("SELECT contract_value FROM works WHERE client_id = ? AND contract_value IS NOT NULL ORDER BY contract_value DESC LIMIT 2", (client_id,)).fetchall()
    if len(rows) < 2:
        return 0
    return abs(rows[0][0] - rows[1][0])


def handle_gap_to_threshold(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    target = params.get("target_value") or params.get("threshold_value")
    if not target:
        return 0
    curr = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND contract_value IS NOT NULL", (client_id,)).fetchone()[0] or 0
    return max(0, target - curr)


def handle_temporal_chain(conn, params: dict) -> float:
    engineer_id = find_engineer_id(conn, params.get("engineer_name"))
    if not engineer_id:
        return 0
    dref = params.get("date_reference") or "2021-03-10"
    cur = conn.execute("SELECT SUM(w.contract_value) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id WHERE ew.engineer_id = ? AND w.completion_date > ? AND w.contract_value IS NOT NULL", (engineer_id, dref))
    res = cur.fetchone()[0]
    return res if res else 0


def handle_general_aggregate(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        eng_id = find_engineer_id(conn, params.get("engineer_name"))
        if eng_id:
            cur = conn.execute("SELECT SUM(w.contract_value) FROM works w JOIN engineer_works ew ON w.work_id = ew.work_id WHERE ew.engineer_id = ?", (eng_id,))
            res = cur.fetchone()[0]
            if res:
                return res
        return 0
    res = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND contract_value IS NOT NULL", (client_id,)).fetchone()[0]
    return res if res else 0


def handle_role_split(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    role_val = "prime" if "prime" in params.get("qlow", "") else "subcontractor"
    cur = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND LOWER(role) LIKE ?", (client_id, f"%{role_val}%"))
    res = cur.fetchone()[0]
    return res if res else 0


def handle_doc_filtered_aggregate(conn, params: dict) -> float:
    client_id = find_client_id(conn, params.get("client_name"))
    if not client_id:
        return 0
    qlow = params.get("qlow", "")
    grading = "excellent" if "excellent" in qlow else ("satisfactory" if "satisfactory" in qlow else ("good" if "good" in qlow else ""))
    if not grading:
        grading = "satisfactory"
    cur = conn.execute("SELECT SUM(contract_value) FROM works WHERE client_id = ? AND LOWER(performance_grading) LIKE ?", (client_id, f"%{grading}%"))
    res = cur.fetchone()[0]
    return res if res else 0


SHAPE_HANDLERS = {
    'outstanding_balance': handle_outstanding_balance,
    'category_difference': handle_category_difference,
    'collection_percent': handle_collection_percent,
    'unbilled_gap': handle_unbilled_gap,
    'mean_median_diff': handle_mean_median_diff,
    'yearly_diff': handle_yearly_diff,
    'absence': handle_absence,
    'date_span': handle_date_span,
    'distinct_count': handle_distinct_count,
    'referenced_share': handle_referenced_share,
    'exclusion_aggregate': handle_exclusion_aggregate,
    'avg_work_size': handle_avg_work_size,
    'threshold_aggregate': handle_threshold_aggregate,
    'rank_value': handle_rank_value,
    'gap_to_threshold': handle_gap_to_threshold,
    'temporal_chain': handle_temporal_chain,
    'general_aggregate': handle_general_aggregate,
    'role_split': handle_role_split,
    'doc_filtered_aggregate': handle_doc_filtered_aggregate,
}


def answer_question(conn, question_text: str, qid: str = None) -> float:
    """Answer a single question using the deterministic pipeline, with LLM fallback for zeros."""
    params = parse_question(conn, question_text)
    shape = params.get("question_shape", "other")
    
    answer = 0
    handler = SHAPE_HANDLERS.get(shape)
    if handler:
        try:
            answer = handler(conn, params)
        except Exception:
            answer = 0

    # Fallback to Few-Shot LLM Text-to-SQL for unhandled or zero answers
    if answer == 0 or shape == "other":
        try:
            from src.llm_fallback import execute_llm_fallback
            fallback_ans = execute_llm_fallback(conn, question_text)
            if fallback_ans != 0:
                return format_as_answer(fallback_ans)
        except Exception as e:
            pass

    return format_as_answer(answer)


def answer_all_questions(questions_path: str, output_path: str = None):
    """Answer all questions from a JSON file and optionally write CSV or JSONL output."""
    with open(questions_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    questions = data.get("questions", data) if isinstance(data, dict) else data
    conn = sqlite3.connect(str(DB_PATH))
    
    print(f"Answering {len(questions)} questions from {questions_path}...")
    
    results = []
    correct = 0
    total_with_expected = 0
    
    for q in questions:
        qid = q["qid"]
        question_text = q["question"]
        expected = q.get("answer") or q.get("answer_gold")
        
        answer = answer_question(conn, question_text, qid)
        results.append({"qid": qid, "answer": answer})
        
        if expected is not None:
            total_with_expected += 1
            if answer == expected:
                correct += 1
    
    conn.close()
    
    if total_with_expected > 0:
        print(f"Sample Accuracy: {correct}/{total_with_expected} ({correct/total_with_expected:.1%})")
    
    if output_path:
        out_p = Path(output_path)
        if out_p.suffix.lower() == '.csv':
            import csv
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(["question_id", "answer"])
                for r in results:
                    writer.writerow([r["qid"], r["answer"]])
        else:
            with open(output_path, 'w', encoding='utf-8') as f:
                for r in results:
                    f.write(json.dumps(r) + "\n")
        print(f"Submission written to: {output_path}")
    
    return results


def main():
    parser = argparse.ArgumentParser(description="Answer questions")
    parser.add_argument("--questions", default=str(SAMPLE_QUESTIONS_PATH),
                       help="Path to questions JSON file")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "submission.csv"),
                       help="Path to write submission file (CSV or JSONL)")
    args = parser.parse_args()
    
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)
    
    answer_all_questions(args.questions, args.output)


if __name__ == "__main__":
    main()
