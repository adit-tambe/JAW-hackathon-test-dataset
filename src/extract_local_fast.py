"""
extract_local_fast.py — Robust offline PDF extractor using PyMuPDF + Regex.

Processes all 678 PDFs in ~5 seconds with 0 API calls.
Fixes all known extraction bugs:
  - Handles line breaks between currency prefix and number
  - Normalizes performance grading to single keywords
  - Extracts role (Prime/Sub-contractor/JV Partner) properly
  - Handles all date formats (DD Mon YYYY, Mon DD YYYY, YYYY-MM-DD, DD/MM/YYYY)
  - Clean signing officer name extraction
"""
import json
import re
import sys
from pathlib import Path
import fitz  # PyMuPDF
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import DOCUMENTS_DIR, EXTRACTED_DIR, PROJECT_ROOT
from src.money import parse_indian_money
from src.extract_records import (
    extract_financial_statement, extract_ledger_book, extract_bank_statement,
    extract_ra_bill, extract_final_ra_bill, extract_tender_dossier,
    extract_iso_certificate, extract_compliance_matrix, extract_annual_report,
)


# ── Date Parsing ────────────────────────────────────────────────────────────

MONTHS = {
    'jan': '01', 'january': '01',
    'feb': '02', 'february': '02',
    'mar': '03', 'march': '03',
    'apr': '04', 'april': '04',
    'may': '05',
    'jun': '06', 'june': '06',
    'jul': '07', 'july': '07',
    'aug': '08', 'august': '08',
    'sep': '09', 'sept': '09', 'september': '09',
    'oct': '10', 'october': '10',
    'nov': '11', 'november': '11',
    'dec': '12', 'december': '12',
}


def parse_date(date_str: str) -> str:
    """Normalize any date string to YYYY-MM-DD format."""
    if not date_str:
        return None
    date_str = date_str.strip()

    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})$', date_str)
    if m:
        return date_str

    m = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', date_str)
    if m:
        d, mo, y = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"

    m = re.match(r'^([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})$', date_str)
    if m:
        mon, d, y = m.groups()
        mo_num = MONTHS.get(mon.lower()[:3], '01')
        return f"{y}-{mo_num}-{int(d):02d}"

    m = re.match(r'^(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})$', date_str)
    if m:
        d, mon, y = m.groups()
        mo_num = MONTHS.get(mon.lower()[:3], '01')
        return f"{y}-{mo_num}-{int(d):02d}"

    return date_str


# ── Grading Normalization ───────────────────────────────────────────────────

def normalize_grading(text: str) -> str:
    """Extract a single grading keyword from text."""
    if not text:
        return None
    t = text.lower()
    if 'excellent' in t:
        return 'Excellent'
    if 'very good' in t:
        return 'Very Good'
    if re.search(r'\bgood\b', t) and 'very good' not in t:
        return 'Good'
    if 'satisfactory' in t:
        return 'Satisfactory'
    return text.strip()


# ── Contract Value Extraction ───────────────────────────────────────────────

def extract_contract_value(text: str) -> int:
    """Extract contract value from PDF text."""
    flat = re.sub(r'\s+', ' ', text)
    
    patterns = [
        r'Contract Value \(Original\)\s*(.+?)(?:\n|Completion|Defect|Agency)',
        r'gross executed value of\s+(.+?)(?:\()',
        r'executed value of\s+(.+?)(?:\()',
    ]
    for pat in patterns:
        m = re.search(pat, flat, re.IGNORECASE)
        if m:
            val = parse_indian_money(m.group(1).strip())
            if val and val > 0:
                return val
    
    money_patterns = [
        r'(?:INR|Rs\.?|₹)\s*([\d,]+\.?\d*)\s*(Crore|Crores|Cr|Lakh|Lakhs)',
        r'(?:INR|Rs\.?|₹)\s*([\d,]+\.?\d*)\s*(?:/|-|\()',
        r'(?:INR|Rs\.?|₹)\s*([\d,]+\.?\d*)',
    ]
    for pat in money_patterns:
        m = re.search(pat, flat, re.IGNORECASE)
        if m:
            full_match = m.group(0).strip()
            full_match = re.sub(r'[/\-]+$', '', full_match).strip()
            val = parse_indian_money(full_match)
            if val and val > 0:
                return val
    
    return None


# ── Completion Date Extraction ──────────────────────────────────────────────

def extract_completion_date(text: str) -> str:
    """Extract completion date from PDF text."""
    flat = re.sub(r'\s+', ' ', text)
    
    m = re.search(r'Completion Date\s+(\S+)', flat)
    if m:
        return parse_date(m.group(1).strip())
    
    date_patterns = [
        r'completed in all respects on\s+([A-Za-z]+\s+\d{1,2},?\s+\d{4})',
        r'completed in all respects on\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})',
        r'completed in all respects on\s+(\d{4}-\d{2}-\d{2})',
        r'completed in all respects on\s+(\d{2}/\d{2}/\d{4})',
    ]
    for pat in date_patterns:
        m = re.search(pat, flat, re.IGNORECASE)
        if m:
            return parse_date(m.group(1).strip())
    
    return None


# ── Performance Grading Extraction ──────────────────────────────────────────

def extract_grading(text: str) -> str:
    """Extract performance grading from table or prose."""
    flat = re.sub(r'\s+', ' ', text)
    
    # 1. Formal Quality Assessment section/table
    m = re.search(r'Quality Assessment\s+([^\n]+)', text) or \
        re.search(r'Quality Assessment\s+(.+?)(?:\n|Parameter|Workmanship)', flat)
    if m:
        return normalize_grading(m.group(1).strip())
    
    # 2. Explicit "graded <keyword>"
    m = re.search(r'graded\s+(Excellent|Very Good|Good|Satisfactory)', flat, re.IGNORECASE)
    if m:
        return normalize_grading(m.group(1))
    
    # 3. Explicit completion grading phrase: "satisfactory completion"
    if re.search(r'satisfactory completion', flat, re.IGNORECASE):
        return 'Satisfactory'
    
    return None


# ── Role Extraction ─────────────────────────────────────────────────────────

def extract_role(text: str) -> str:
    """Extract contractor role (Prime, Sub-contractor, JV Partner)."""
    flat = re.sub(r'\s+', ' ', text).lower()
    
    if 'jv partner' in flat:
        return 'JV Partner'
    if 'sub-contractor' in flat or 'subcontractor' in flat or 'as sub' in flat:
        return 'Sub-contractor'
    if "contractor's role prime" in flat or 'role: prime' in flat:
        return 'Prime'
    
    return 'Prime'


# ── Signing Officer Extraction ──────────────────────────────────────────────

def extract_signing_officer(text: str) -> str:
    """Extract clean signing officer / project manager name."""
    # 1. Table format: Contractor's Project Manager on its own line
    m = re.search(r"Contractor's Project Manager\s*\n\s*([^\n\d]+)", text)
    if m:
        name = m.group(1).strip()
        # Clean trailing titles
        name = re.sub(r'\s+(?:2\.|Scope|Project|Manager|Chief).*$', '', name, flags=re.I).strip()
        if name and len(name.split()) <= 4 and len(name) < 40:
            return name

    # 2. Prose format: supervised on the contractor's side by <Name>.
    m = re.search(r"supervised on the contractor's side by\s+([A-Z][a-z]+\s+[A-Z][a-z]+)", text)
    if m:
        return m.group(1).strip()

    # 3. Bottom signature block
    m = re.search(r"([A-Z][a-z]+\s+[A-Z][a-z]+)\s*\n\s*Project Manager", text)
    if m:
        return m.group(1).strip()

    return None


# ── Document Extractors ─────────────────────────────────────────────────────

def extract_cc(text: str, doc_id: str) -> dict:
    """Extract fields from completion certificate."""
    flat = re.sub(r'\s+', ' ', text)
    
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    client_name = lines[0] if lines else "Unknown"
    if len(lines) > 1 and lines[1] in ('Bengal', 'Pradesh', 'Gujarat', 'Maharashtra',
                                         'Jharkhand', 'Odisha', 'Rajasthan', 'Tamil Nadu',
                                         'Karnataka', 'Delhi'):
        client_name = f"{lines[0]} {lines[1]}"
    
    proj_m = re.search(r'Name of Work\s*\n\s*([^\n]+)', text) or \
             re.search(r'work of\s+["\u201c\u201d]([^"\u201c\u201d]+)["\u201c\u201d]', flat) or \
             re.search(r'work of\s+"([^"]+)"', flat)
    project_name = proj_m.group(1).strip() if proj_m else None
    
    cat_m = re.search(r'Nature / Category\s*\n\s*([^\n]+)', text) or \
            re.search(r'work of\s+["\u201c\u201d][^"\u201c\u201d]+["\u201c\u201d]\s*\(([^)]+)\)', flat) or \
            re.search(r'work of\s+"[^"]+"\s*\(([^)]+)\)', flat)
    work_category = cat_m.group(1).strip() if cat_m else None
    
    contract_value = extract_contract_value(text)
    comp_date = extract_completion_date(text)
    grading = extract_grading(text)
    signing_officer = extract_signing_officer(text)
    role = extract_role(text)
    
    return {
        "_doc_id": doc_id,
        "_doc_type": "completion_certificate",
        "project_name": project_name,
        "client_name": client_name,
        "contract_value": contract_value,
        "contract_value_raw": None,
        "completion_date": comp_date,
        "commencement_date": None,
        "performance_grading": grading,
        "role": role,
        "work_category": work_category,
        "signing_officer": signing_officer,
        "work_description": f"{work_category or 'Construction'} work for {client_name}"
    }


def extract_ccc(text: str, doc_id: str) -> dict:
    res = extract_cc(text, doc_id)
    res["_doc_type"] = "company_completion_certificate"
    return res


def extract_ref(text: str, doc_id: str, raw_text: str = None) -> dict:
    """Extract fields from reference letter."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    client_name = lines[0] if lines else "Unknown"
    if len(lines) > 1 and lines[1] in ('Bengal', 'Pradesh', 'Gujarat', 'Maharashtra',
                                         'Jharkhand', 'Odisha', 'Rajasthan', 'Tamil Nadu'):
        client_name = f"{lines[0]} {lines[1]}"
    
    flat = re.sub(r'\s+', ' ', text)
    raw_flat = re.sub(r'\s+', ' ', raw_text) if raw_text else flat
    
    # Project name — clean match stopping before "Scope of Work"
    proj_m = re.search(r'Project Name\s*\n\s*([^\n]+)', text) or \
             re.search(r'Work Executed\s*:?\s*([^(]+?)(?=Scope of Work|Value|Date|Completed|Contact|\Z)', flat, re.I) or \
             re.search(r'Subject:.*?["\u201c]([^"\u201d]+)["\u201d]', flat) or \
             re.search(r'work\s+["\u201c]([^"\u201d]+)["\u201d]', flat) or \
             re.search(r'for the work\s+["\u201c]?([^"\u201d]+?)["\u201d]?\s*\(INR', flat, re.I)
    project_name = proj_m.group(1).strip() if proj_m else None
    if project_name:
        project_name = re.sub(r'\s+Scope of Work.*$', '', project_name, flags=re.I).strip()
    
    date_m = re.search(r'Our ref:\s*\S+\s+(\d{1,2}\s+[A-Za-z]+\s+\d{4})', text) or \
             re.search(r'Date:\s*([^\n]+)', text) or \
             re.search(r'Dated:\s*([^\n]+)', text)
    letter_date = parse_date(date_m.group(1).strip()) if date_m else None
    
    sig_m = re.search(r'Verification:\s*([^,\n]+)', text) or \
            re.search(r'Contact for Verification\s+([^·\n]+)', flat)
    signatory = sig_m.group(1).strip() if sig_m else None
    
    role = extract_role(text)
    
    return {
        "_doc_id": doc_id,
        "_doc_type": "reference_letter",
        "project_name": project_name,
        "client_name": client_name,
        "letter_date": letter_date,
        "role": role,
        "remarks": None,
        "signatory": signatory
    }


def _field_below(text: str, label: str) -> str:
    """Read a label/value pair where the value is on the following line.

    These certificates are laid out as a two-column table that extracts to
    alternating label and value lines. Flattening the page first would run
    every value into the next label, which is why the raw text is matched.
    """
    m = re.search(rf'^\s*{label}\s*\n\s*([^\n]+)', text, re.M | re.I)
    return m.group(1).strip() if m else None


def extract_pcert(text: str, doc_id: str) -> dict:
    """Extract a personnel credential.

    The holder's name is the line after "This is to certify that"; the line
    after that is the employee ID, so the match must stop at the newline.
    """
    # Two layouts are in use: a tabular one ("This is to certify that" with
    # Credential Type / Credential ID / Date of Issue rows) and a citation
    # style ("This credential is conferred upon" with Certificate No. /
    # Issued). Both are tried for every field.
    person_m = (re.search(r'This is to certify that\s*\n\s*([^\n]+)', text)
                or re.search(r'This credential is conferred upon\s*\n\s*([^\n]+)', text))
    person_name = person_m.group(1).strip() if person_m else None
    if not person_name:
        # Signature block fallback: the name sits above "Credential Holder".
        alt = re.search(r'^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*\n\s*Credential Holder',
                        text, re.M)
        person_name = alt.group(1).strip() if alt else "Unknown"
    person_name = re.sub(r'^(?:Mr|Ms|Mrs|Dr)\.?\s+', '', person_name).strip()

    cert_type = _field_below(text, 'Credential Type')
    if not cert_type:
        # The citation layout prints the credential on its own line and
        # sometimes as a heading such as "PMP CERTIFICATION".
        m = re.search(r'\b(Six Sigma Black Belt|Six Sigma Green Belt|Lean Six Sigma|PMP|'
                      r'FIDIC|NEBOSH|PRINCE2)\b', text, re.I)
        cert_type = m.group(1) if m else "Unknown"
    cert_type = re.sub(r'\s+CERTIFICATION$', '', cert_type, flags=re.I).strip()

    cert_id = (_field_below(text, 'Credential ID')
               or _field_below(text, r'Certificate No\.?'))
    if not cert_id:
        m = re.search(r'(?:Credential|Certificate)\s+(?:ID|No\.?):\s*(\S+)', text, re.I)
        cert_id = m.group(1) if m else None

    issue_raw = (_field_below(text, 'Date of Issue') or _field_below(text, 'Issued'))
    if not issue_raw:
        m = re.search(r'Issued:\s*(\S+)', text)
        issue_raw = m.group(1) if m else None

    expiry_raw = _field_below(text, 'Valid Through')
    employment = _field_below(text, 'Employment Status')
    qualification = _field_below(text, 'Highest Qualification')
    emp_id = re.search(r'Employee ID:\s*(\S+)', text)

    exp_raw = _field_below(text, 'Years of Experience') or ''
    exp_m = re.search(r'(\d+)', exp_raw)

    issuing = _field_below(text, 'Issuing Authority')

    return {
        "_doc_id": doc_id,
        "_doc_type": "personnel_certificate",
        "person_name": person_name,
        "employee_id": emp_id.group(1) if emp_id else None,
        "cert_type": cert_type,
        "cert_id": cert_id,
        "issue_date": parse_date(issue_raw) if issue_raw else None,
        "expiry_date": parse_date(expiry_raw) if expiry_raw else None,
        "employment_status": employment,
        "years_of_experience": int(exp_m.group(1)) if exp_m else None,
        "qualification": qualification,
        "issuing_body": issuing or ("PMI" if "PMP" in (cert_type or "") else "ASQ"),
    }


def _cv_field(text: str, label: str) -> str:
    """Read a CV header field. Labels and values sit on their own lines, so
    flattening the page first would run the value into the next label."""
    m = re.search(rf'^\s*{re.escape(label)}\s*\n\s*([^\n]+)', text, re.M)
    return m.group(1).strip() if m else None


def extract_cv(text: str, doc_id: str) -> dict:
    """Extract fields from an engineer CV.

    The CVs deliberately carry no project list — section 4 says assignments
    "are evidenced by the company's project records and client completion
    certificates" — so engineer-to-work links come from the certificates, not
    from here. What the CV uniquely holds is the personnel profile.
    """
    person_name = _cv_field(text, 'Name') or "Unknown"
    desig = _cv_field(text, 'Designation')
    emp_id = _cv_field(text, 'Employee ID')
    unit = _cv_field(text, 'Business Unit')
    qual = _cv_field(text, 'Qualification')
    joined = _cv_field(text, 'Date of Joining')
    wage_group = _cv_field(text, 'Wage Group')

    exp_raw = _cv_field(text, 'Total Experience') or ''
    exp_m = re.search(r'(\d+)', exp_raw)
    years_exp = int(exp_m.group(1)) if exp_m else None

    return {
        "_doc_id": doc_id,
        "_doc_type": "cv",
        "person_name": person_name,
        "employee_id": emp_id,
        "current_designation": desig,
        "business_unit": unit,
        "years_of_experience": years_exp,
        "qualification": qual,
        "date_of_joining": parse_date(joined) if joined else None,
        "wage_group": wage_group,
        "qualifications": [],
        "certifications": [],
        "projects_led": []
    }


def extract_bond(text: str, doc_id: str) -> dict:
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    bank_name = lines[0] if lines else "Unknown Bank"
    flat = re.sub(r'\s+', ' ', text)
    
    # `flat` has had its newlines collapsed, so a `[^\n]+` capture runs to the
    # end of the document — which is how the bond number came to hold three
    # pages of guarantee text. Bound the capture to the shape of a reference,
    # and accept both layouts: one says "Bond No", the other "BG No".
    bond_no_m = re.search(r'\b(?:Bond|BG)\s*(?:No|Ref|Reference)\.?:?\s*([A-Za-z0-9][\w/\-]*)',
                          flat, re.I)
    bond_number = bond_no_m.group(1).strip() if bond_no_m else None

    val_m = re.search(r'not exceeding\s+Rs\.?\s*([\d,\.]+\s*(?:Lakh|Crore|Cr)?)', flat, re.I) or \
            re.search(r'not exceeding\s+([^\n)(,]+(?:Lakh|Crore|Cr)?)', flat) or \
            re.search(r'Rs\.?\s*([\d,\.]+\s*(?:Lakh|Crore|Cr)?)', flat)
    bond_val_str = val_m.group(1).strip() if val_m else None
    raw_bond_val = parse_indian_money(bond_val_str)

    dates_m = re.search(r'valid from\s+(\S+)\s+until\s+(\S+)', flat, re.I)
    if dates_m:
        issue_date = parse_date(dates_m.group(1))
        expiry_date = parse_date(dates_m.group(2))
    else:
        # Second layout: "Date: 25 Apr 2019" ... "in force up to and including
        # 05 Jul 2021".
        issued = re.search(r'\bDate:\s*(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})', flat)
        until = re.search(r'in force up to and including\s+(\d{1,2}\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2})',
                          flat, re.I)
        issue_date = parse_date(issued.group(1)) if issued else None
        expiry_date = parse_date(until.group(1)) if until else None

    proj_m = re.search(r'work of\s+([^,\n]+)', flat)
    if not proj_m:
        proj_m = re.search(r'Subject:\s*Performance Bond\s*[—\-–]\s*([^(\n]+)', flat, re.I)
    project_name = proj_m.group(1).strip() if proj_m else None

    tender_m = re.search(r'\b(RFP-\d+)', flat)
    tender_ref = tender_m.group(1) if tender_m else None
    
    return {
        "_doc_id": doc_id,
        "_doc_type": "performance_bond",
        "project_name": project_name,
        "client_name": "Employer",
        "contractor_name": "National Infrastructure Corp. Ltd.",
        "bond_value": bond_val_str,
        "bond_value_raw": raw_bond_val,
        "contract_value": None,
        "contract_value_raw": None,
        "bank_name": bank_name,
        "bond_number": bond_number,
        "tender_ref": tender_ref,
        "issue_date": issue_date,
        "expiry_date": expiry_date
    }


def extract_generic(text: str, doc_id: str, doc_type: str) -> dict:
    return {
        "_doc_id": doc_id,
        "_doc_type": doc_type,
        "content_summary": text[:500]
    }


EXTRACTORS = {
    "completion_certificate": extract_cc,
    "company_completion_certificate": extract_ccc,
    "reference_letter": extract_ref,
    "personnel_certificate": extract_pcert,
    "cv": extract_cv,
    "performance_bond": extract_bond,
    # The financial and commercial families — see extract_records.py.
    "financial_statement": extract_financial_statement,
    "general_ledger_book": extract_ledger_book,
    "bank_statement": extract_bank_statement,
    "ra_bill": extract_ra_bill,
    "final_ra_bill": extract_final_ra_bill,
    "tender_dossier": extract_tender_dossier,
    "iso_certificate": extract_iso_certificate,
    "compliance_matrix": extract_compliance_matrix,
    "annual_report": extract_annual_report,
}


def run_fast_extraction():
    """Deprecated shim.

    Extraction used to be driven by a checked-in manifest of 687 known file
    names resolved against a fixed `documents/` directory, which only ever
    worked on the machine the manifest was built on. Discovery now happens by
    content in `src/discover.py`; this remains so the older development entry
    point keeps working.
    """
    from src.config import DOCUMENTS_DIR, EXTRACTED_DIR
    from src.ingest import ingest
    ingest(DOCUMENTS_DIR, EXTRACTED_DIR)


if __name__ == "__main__":
    run_fast_extraction()
