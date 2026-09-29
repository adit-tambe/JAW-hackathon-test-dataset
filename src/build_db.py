"""
build_db.py — Construct the SQLite database from extracted JSON files.

Reads all JSON from data/extracted/ and populates company.db with
properly normalized and linked entities.

Usage:
    python src/build_db.py              # Build/rebuild the database
    python src/build_db.py --validate   # Run validation queries after build
"""
import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

# Ensure UTF-8 output encoding for Windows terminal
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import EXTRACTED_DIR, DB_PATH
from src.money import parse_indian_money
from src.portfolio_index import parse_portfolio


# ── Schema ──────────────────────────────────────────────────────────────────
SCHEMA = """
-- Core entity tables
CREATE TABLE IF NOT EXISTS clients (
    client_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    client_name     TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS engineers (
    engineer_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT UNIQUE NOT NULL
);

CREATE TABLE IF NOT EXISTS works (
    work_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_name         TEXT NOT NULL,
    client_id            INTEGER REFERENCES clients(client_id),
    contract_value       INTEGER,
    completion_date      TEXT,
    commencement_date    TEXT,
    work_category        TEXT,
    performance_grading  TEXT,
    role                 TEXT,
    signing_officer      TEXT,
    work_description     TEXT,
    has_reference_letter INTEGER DEFAULT 0,
    has_performance_bond INTEGER DEFAULT 0,
    doc_cc_id            TEXT,
    doc_ccc_id           TEXT,
    doc_ref_id           TEXT,
    pkg_no               INTEGER,
    certificate_ref      TEXT,
    client_office        INTEGER
);

-- Engineer <-> Work many-to-many
CREATE TABLE IF NOT EXISTS engineer_works (
    engineer_id     INTEGER REFERENCES engineers(engineer_id),
    work_id         INTEGER REFERENCES works(work_id),
    role_on_project TEXT,
    PRIMARY KEY (engineer_id, work_id)
);

-- Engineer certificates
CREATE TABLE IF NOT EXISTS engineer_profiles (
    engineer_id          INTEGER PRIMARY KEY REFERENCES engineers(engineer_id),
    employee_id          TEXT,
    designation          TEXT,
    business_unit        TEXT,
    years_of_experience  INTEGER,
    qualification        TEXT,
    date_of_joining      TEXT,
    wage_group           TEXT,
    doc_id               TEXT
);

CREATE TABLE IF NOT EXISTS engineer_certs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    engineer_id     INTEGER REFERENCES engineers(engineer_id),
    cert_type       TEXT NOT NULL,
    cert_id         TEXT,
    issue_date      TEXT,
    expiry_date     TEXT,
    doc_id          TEXT
);

-- Performance bonds
CREATE TABLE IF NOT EXISTS bonds (
    bond_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id         INTEGER REFERENCES works(work_id),
    bond_value      INTEGER,
    contract_value  INTEGER,
    bank_name       TEXT,
    bond_number     TEXT,
    doc_id          TEXT
);

-- Reference letters (for tracking which works have them)
CREATE TABLE IF NOT EXISTS reference_letters (
    ref_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    work_id         INTEGER REFERENCES works(work_id),
    project_name    TEXT,
    client_name     TEXT,
    letter_date     TEXT,
    doc_id          TEXT
);

-- Financial data
CREATE TABLE IF NOT EXISTS financials (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    year            TEXT,
    doc_type        TEXT,
    metric          TEXT,
    value           REAL,
    doc_id          TEXT
);

-- Ledger entries
CREATE TABLE IF NOT EXISTS ledger_entries (
    entry_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    year            TEXT,
    date            TEXT,
    description     TEXT,
    project_name    TEXT,
    work_id         INTEGER REFERENCES works(work_id),
    entry_type      TEXT,
    debit           REAL DEFAULT 0,
    credit          REAL DEFAULT 0,
    doc_id          TEXT
);

-- BOQ items
CREATE TABLE IF NOT EXISTS boq_items (
    item_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_ref    TEXT,
    item_desc       TEXT,
    quantity        REAL,
    rate            REAL,
    amount          REAL
);

-- Asset register
CREATE TABLE IF NOT EXISTS assets (
    asset_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    description     TEXT,
    acquisition_cost REAL,
    acquisition_date TEXT
);

-- ISO certificates
CREATE TABLE IF NOT EXISTS iso_certs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_type       TEXT,
    cert_number     TEXT,
    valid_from      TEXT,
    valid_to        TEXT,
    doc_id          TEXT
);

-- Bank statement summary
CREATE TABLE IF NOT EXISTS bank_statements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    year            TEXT,
    bank_name       TEXT,
    opening_balance REAL,
    closing_balance REAL,
    doc_id          TEXT
);

-- Accounts receivable ageing
CREATE TABLE IF NOT EXISTS receivables (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_no      TEXT,
    client_name     TEXT,
    client_id       INTEGER REFERENCES clients(client_id),
    invoice_date    TEXT,
    invoiced        REAL,
    status          TEXT,
    received        REAL,
    outstanding     REAL,
    doc_id          TEXT
);

-- Statement line items, normalised to rupees (statements are in Lakhs)
CREATE TABLE IF NOT EXISTS statement_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fiscal_year     INTEGER,
    section         TEXT,
    particulars     TEXT,
    current_year    REAL,
    previous_year   REAL,
    doc_id          TEXT
);

-- Bank transactions
CREATE TABLE IF NOT EXISTS bank_txns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fiscal_year     INTEGER,
    date            TEXT,
    particulars     TEXT,
    amount          REAL,
    direction       TEXT,
    balance         REAL,
    doc_id          TEXT
);

-- Running-account bills (interim and final)
CREATE TABLE IF NOT EXISTS bills (
    bill_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_type        TEXT,
    contract_no     TEXT,
    client_name     TEXT,
    client_id       INTEGER REFERENCES clients(client_id),
    bill_no         TEXT,
    bill_date       TEXT,
    ra_number       INTEGER,
    awarded_value   REAL,
    total_billed    REAL,
    period_start    TEXT,
    period_end      TEXT,
    value_of_work   REAL,
    gst             REAL,
    retention       REAL,
    net_claimed     REAL,
    cumulative      REAL,
    doc_id          TEXT
);

-- BOQ lines carried by the bills (workbook BOQ lives in boq_items)
CREATE TABLE IF NOT EXISTS bill_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id         INTEGER REFERENCES bills(bill_id),
    item_no         INTEGER,
    description     TEXT,
    unit            TEXT,
    rate            REAL,
    quantity        REAL,
    amount          REAL,
    doc_id          TEXT
);

-- Tenders submitted
CREATE TABLE IF NOT EXISTS tenders (
    tender_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_ref      TEXT,
    client_name     TEXT,
    client_id       INTEGER REFERENCES clients(client_id),
    work_category   TEXT,
    bid_value       REAL,
    earnest_money   REAL,
    submitted_date  TEXT,
    relevant_works_cited INTEGER,
    doc_id          TEXT
);

-- Bid compliance checklists
CREATE TABLE IF NOT EXISTS compliance_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_ref      TEXT,
    work_category   TEXT,
    item_no         INTEGER,
    requirement     TEXT,
    status          TEXT,
    doc_id          TEXT
);

-- Annual report headline figures, one row per metric
CREATE TABLE IF NOT EXISTS annual_figures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fiscal_year     INTEGER,
    metric          TEXT,
    segment         TEXT,
    value           REAL,
    doc_id          TEXT
);

-- Account balances by fiscal year, from the trial balance workbook. Extracted
-- since the beginning and, until now, never loaded: the workbook loader had no
-- branch for it, so seven years of account-level figures sat in the extracted
-- JSON and reached nothing that could answer a question.
CREATE TABLE IF NOT EXISTS trial_balance (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fiscal_year     INTEGER,
    account         TEXT,
    debit           REAL,
    credit          REAL,
    balance         REAL,
    doc_id          TEXT
);

-- Full document text, so a figure with no typed field is still reachable
CREATE TABLE IF NOT EXISTS doc_text (
    doc_id          TEXT PRIMARY KEY,
    doc_type        TEXT,
    text            TEXT
);

-- Raw document metadata
CREATE TABLE IF NOT EXISTS doc_metadata (
    doc_id          TEXT PRIMARY KEY,
    doc_type        TEXT,
    source_file     TEXT,
    has_error       INTEGER DEFAULT 0
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_works_client ON works(client_id);
CREATE INDEX IF NOT EXISTS idx_works_category ON works(work_category);
CREATE INDEX IF NOT EXISTS idx_works_grading ON works(performance_grading);
CREATE INDEX IF NOT EXISTS idx_works_role ON works(role);
CREATE INDEX IF NOT EXISTS idx_engineer_works_eng ON engineer_works(engineer_id);
CREATE INDEX IF NOT EXISTS idx_engineer_works_work ON engineer_works(work_id);
CREATE INDEX IF NOT EXISTS idx_engineer_certs_eng ON engineer_certs(engineer_id);
"""


# ── Normalization helpers ───────────────────────────────────────────────────

# Canonical client name mapping — fixes common extraction variations
CLIENT_CANONICAL = {
    # Add canonical mappings here if duplicates are found during validation
}


def normalize_client_name(name: str) -> str:
    """Normalize client name for consistent matching."""
    if not name:
        return name
    name = name.strip()
    name = re.sub(r'\s+', ' ', name)
    # Standardize casing for ALL CAPS client names
    if name.isupper():
        name = name.title()
    # Apply canonical mapping
    return CLIENT_CANONICAL.get(name, name)


def normalize_project_name(name: str) -> str:
    """Normalize project name for consistent matching."""
    if not name:
        return name
    name = name.strip()
    name = name.replace('\u2014', ' - ')  # em-dash
    name = name.replace('\u2013', ' - ')  # en-dash
    name = re.sub(r'\s+', ' ', name)
    return name


def get_or_create_client(conn, client_name: str) -> int:
    """Get client_id, creating the client if necessary. Case-insensitive lookup."""
    client_name = normalize_client_name(client_name)
    cur = conn.execute(
        "SELECT client_id FROM clients WHERE LOWER(client_name) = LOWER(?)",
        (client_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO clients (client_name) VALUES (?)",
                       (client_name,))
    return cur.lastrowid


def get_or_create_engineer(conn, name: str) -> int:
    """Get engineer_id, creating the engineer if necessary."""
    name = name.strip()
    cur = conn.execute("SELECT engineer_id FROM engineers WHERE name = ?",
                       (name,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur = conn.execute("INSERT INTO engineers (name) VALUES (?)", (name,))
    return cur.lastrowid


def find_work_by_project(conn, project_name: str):
    """Find a work_id by project name with fuzzy matching."""
    if not project_name:
        return None
    project_name = normalize_project_name(project_name)
    
    # Exact match
    cur = conn.execute("SELECT work_id FROM works WHERE project_name = ?",
                       (project_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    
    # Case-insensitive match
    cur = conn.execute(
        "SELECT work_id FROM works WHERE LOWER(project_name) = LOWER(?)",
        (project_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    
    # LIKE with enough of the name to be unique
    # Use min 30 chars or full name to avoid false positives
    prefix = project_name[:min(40, len(project_name))]
    cur = conn.execute(
        "SELECT work_id, project_name FROM works WHERE project_name LIKE ?",
        (f"%{prefix}%",))
    rows = cur.fetchall()
    if len(rows) == 1:
        return rows[0][0]
    
    return None


def safe_int(val) -> int:
    """Convert a value to int, handling None and strings."""
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return parse_indian_money(str(val))


# ── Loaders ─────────────────────────────────────────────────────────────────

def load_completion_certificates(conn):
    """Load completion certificates into the works table."""
    count = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-CC-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        if "_error" in data:
            conn.execute(
                "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 1)",
                (data.get("_doc_id"), data.get("_doc_type"),
                 data.get("_source_file")))
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        project_name = normalize_project_name(
            data.get("project_name", "Unknown"))
        client_name = data.get("client_name", "Unknown")
        
        # Parse contract value — prefer the already-parsed int
        contract_value = data.get("contract_value")
        if contract_value is not None:
            contract_value = safe_int(contract_value)
        
        client_id = get_or_create_client(conn, client_name)
        
        cur = conn.execute("""
            INSERT INTO works (project_name, client_id, contract_value,
                             completion_date, commencement_date,
                             work_category, performance_grading, role,
                             signing_officer, work_description, doc_cc_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            project_name,
            client_id,
            contract_value,
            data.get("completion_date"),
            data.get("commencement_date"),
            data.get("work_category"),
            data.get("performance_grading"),
            data.get("role"),
            data.get("signing_officer"),
            data.get("work_description"),
            doc_id,
        ))
        work_id = cur.lastrowid
        
        # Link signing officer as engineer
        signing_officer = data.get("signing_officer")
        if signing_officer and signing_officer.lower() not in ("none", "null", "unknown"):
            engineer_id = get_or_create_engineer(conn, signing_officer)
            conn.execute("""
                INSERT OR IGNORE INTO engineer_works
                    (engineer_id, work_id, role_on_project)
                VALUES (?, ?, 'Project Manager')
            """, (engineer_id, work_id))
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "completion_certificate", data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} completion certificates")
    return count


def load_company_completion_certificates(conn):
    """Load company completion certificates — cross-validate and fill gaps."""
    count = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-CCC-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        project_name = normalize_project_name(
            data.get("project_name", "Unknown"))
        
        work_id = find_work_by_project(conn, project_name)
        if work_id:
            conn.execute(
                "UPDATE works SET doc_ccc_id = ? WHERE work_id = ?",
                (doc_id, work_id))
            
            # Fill missing contract_value from CCC if CC had null
            cv = data.get("contract_value")
            if cv is not None:
                cv_int = safe_int(cv)
                if cv_int:
                    conn.execute("""
                        UPDATE works SET contract_value = ?
                        WHERE work_id = ? AND contract_value IS NULL
                    """, (cv_int, work_id))
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "company_completion_certificate",
             data.get("_source_file")))
        count += 1
    
    print(f"  Linked {count} company completion certificates")
    return count


def load_reference_letters(conn):
    """Load reference letters and mark which works have them."""
    count = 0
    matched = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-REF-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        project_name = normalize_project_name(
            data.get("project_name", "Unknown"))
        client_name = data.get("client_name", "")
        
        work_id = find_work_by_project(conn, project_name)
        
        conn.execute("""
            INSERT INTO reference_letters
                (work_id, project_name, client_name, letter_date, doc_id)
            VALUES (?, ?, ?, ?, ?)
        """, (work_id, project_name, client_name,
              data.get("letter_date"), doc_id))
        
        if work_id:
            conn.execute("""
                UPDATE works SET has_reference_letter = 1, doc_ref_id = ?
                WHERE work_id = ?
            """, (doc_id, work_id))
            role = data.get("role")
            if role and role != "Prime":
                conn.execute("UPDATE works SET role = ? WHERE work_id = ?", (role, work_id))
            matched += 1
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "reference_letter", data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} reference letters ({matched} matched to works)")
    return count


def load_personnel_certificates(conn):
    """Load personnel/professional certificates."""
    count = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-PCERT-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        person_name = data.get("person_name", "Unknown")
        
        engineer_id = get_or_create_engineer(conn, person_name)
        
        conn.execute("""
            INSERT INTO engineer_certs
                (engineer_id, cert_type, cert_id, issue_date, expiry_date, doc_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            engineer_id,
            data.get("cert_type", "Unknown"),
            data.get("cert_id"),
            data.get("issue_date"),
            data.get("expiry_date"),
            doc_id,
        ))
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "personnel_certificate", data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} personnel certificates")
    return count


def load_cvs(conn):
    """Load CVs and create engineer-work links."""
    count = 0
    links = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-CV-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        person_name = data.get("person_name", "Unknown")

        engineer_id = get_or_create_engineer(conn, person_name)

        # The CV is the only source for the personnel profile.
        conn.execute("""
            INSERT OR REPLACE INTO engineer_profiles
                (engineer_id, employee_id, designation, business_unit,
                 years_of_experience, qualification, date_of_joining,
                 wage_group, doc_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (engineer_id, data.get("employee_id"),
              data.get("current_designation"), data.get("business_unit"),
              data.get("years_of_experience"), data.get("qualification"),
              data.get("date_of_joining"), data.get("wage_group"), doc_id))

        projects_led = data.get("projects_led", [])
        if isinstance(projects_led, list):
            for proj in projects_led:
                if isinstance(proj, dict):
                    proj_name = normalize_project_name(
                        proj.get("project_name", ""))
                    work_id = find_work_by_project(conn, proj_name)
                    if work_id:
                        try:
                            conn.execute("""
                                INSERT OR IGNORE INTO engineer_works
                                    (engineer_id, work_id, role_on_project)
                                VALUES (?, ?, ?)
                            """, (
                                engineer_id, work_id,
                                proj.get("role_on_project", "Unknown"),
                            ))
                            links += 1
                        except sqlite3.IntegrityError:
                            pass
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "cv", data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} CVs, created {links} engineer-work links")
    return count


def load_performance_bonds(conn):
    """Load performance bonds."""
    count = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-BOND-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        project_name = normalize_project_name(
            data.get("project_name", "Unknown"))
        
        work_id = find_work_by_project(conn, project_name)
        
        bond_value = safe_int(data.get("bond_value_raw"))
        contract_value = safe_int(data.get("contract_value_raw"))
        
        conn.execute("""
            INSERT INTO bonds
                (work_id, bond_value, contract_value, bank_name, bond_number, doc_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (work_id, bond_value, contract_value,
              data.get("bank_name"), data.get("bond_number"), doc_id))
        
        if work_id:
            conn.execute(
                "UPDATE works SET has_performance_bond = 1 WHERE work_id = ?",
                (work_id,))
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "performance_bond", data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} performance bonds")
    return count


def load_financial_statements(conn):
    """Load financial statements."""
    count = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-FS-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        year = data.get("year", "")
        
        metrics = [
            ("revenue", data.get("revenue")),
            ("expenses", data.get("expenses")),
            ("profit_before_tax", data.get("profit_before_tax")),
            ("profit_after_tax", data.get("profit_after_tax")),
            ("total_assets", data.get("total_assets")),
            ("total_liabilities", data.get("total_liabilities")),
            ("shareholders_equity", data.get("shareholders_equity")),
            ("current_assets", data.get("current_assets")),
            ("current_liabilities", data.get("current_liabilities")),
            ("cash_and_equivalents", data.get("cash_and_equivalents")),
        ]
        
        for metric_name, value in metrics:
            if value is not None:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    parsed = parse_indian_money(str(value))
                    value = float(parsed) if parsed else None
                
                if value is not None:
                    conn.execute("""
                        INSERT INTO financials (year, doc_type, metric, value, doc_id)
                        VALUES (?, 'financial_statement', ?, ?, ?)
                    """, (year, metric_name, value, doc_id))
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "financial_statement", data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} financial statements")
    return count


def load_ledger_books(conn):
    """Load general ledger book entries."""
    count = 0
    entries = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-GLB-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        year = data.get("year", "")
        
        for entry in data.get("entries", []):
            if isinstance(entry, dict):
                conn.execute("""
                    INSERT INTO ledger_entries
                        (year, date, description, project_name,
                         entry_type, debit, credit, doc_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    year,
                    entry.get("date"),
                    entry.get("description"),
                    entry.get("project_name"),
                    entry.get("entry_type"),
                    entry.get("debit", 0),
                    entry.get("credit", 0),
                    doc_id,
                ))
                entries += 1
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "general_ledger_book", data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} ledger books ({entries} entries)")
    return count


def load_iso_certificates(conn):
    """Load ISO certificates."""
    count = 0
    for json_file in sorted(EXTRACTED_DIR.glob("DOC-CERT-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        
        doc_id = data.get("_doc_id", json_file.stem)
        
        conn.execute("""
            INSERT INTO iso_certs
                (cert_type, cert_number, valid_from, valid_to, doc_id)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data.get("cert_type"),
            data.get("cert_number"),
            data.get("valid_from"),
            data.get("valid_to"),
            doc_id,
        ))
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, "iso_certificate", data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} ISO certificates")
    return count


def _norm_key(name) -> str:
    """'Cost (INR)' -> 'cost'.  'Qty Measured' -> 'qtymeasured'."""
    text = re.sub(r"\(.*?\)", " ", str(name or ""))
    return re.sub(r"[^a-z0-9]", "", text.lower())


def pick(row: dict, *candidates):
    """Fetch a column by normalised name.

    Spreadsheet headers carry units and spacing that vary between workbooks —
    'Cost (INR)' against 'Cost', 'Rate (INR)' against 'Unit Rate'. Matching them
    literally is how the plant register and every BOQ rate silently loaded as
    zero: the rows were there, the numbers were not. Matching on a normalised
    key survives that, which matters most on an estate whose headers we have
    never seen.
    """
    normalised = {_norm_key(k): v for k, v in row.items()}
    for candidate in candidates:
        value = normalised.get(_norm_key(candidate))
        if value not in (None, ""):
            return value
    return None


def pick_text(row: dict, *candidates) -> str:
    value = pick(row, *candidates)
    return "" if value is None else str(value).strip()


def pick_number(row: dict, *candidates):
    value = pick(row, *candidates)
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return float(cleaned) if cleaned not in ("", "-", ".") else None
    except ValueError:
        return None


def load_workbook_data(conn):
    """Load data from extracted workbook JSON files."""
    count = 0
    for json_file in sorted(EXTRACTED_DIR.glob("WB-*.json")):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        doc_type = data.get("_doc_type", "")
        doc_id = data.get("_doc_id", json_file.stem)
        
        if doc_type == "boq_workbook":
            for sheet_name, sheet_data in data.get("sheets", {}).items():
                for row in sheet_data.get("data", []):
                    if isinstance(row, dict):
                        desc = pick_text(row, "description", "item", "particulars")
                        qty = pick_number(row, "quantity", "qty", "qtymeasured")
                        rate = pick_number(row, "rate", "unitrate")
                        amount = pick_number(row, "amount", "total", "value")
                        conn.execute("""
                            INSERT INTO boq_items
                                (contract_ref, item_desc, quantity, rate, amount)
                            VALUES (?, ?, ?, ?, ?)
                        """, (data.get("contract_ref", doc_id), desc,
                              qty or 0, rate or 0, amount or 0))

        elif doc_type == "asset_register":
            for sheet_name, sheet_data in data.get("sheets", {}).items():
                if sheet_name.lower().startswith("note"):
                    continue
                for row in sheet_data.get("data", []):
                    if not isinstance(row, dict):
                        continue
                    # The register describes an asset across several columns
                    # rather than in one "description" field, so build the label
                    # from whichever of them this workbook actually carries.
                    label = " ".join(x for x in (
                        pick_text(row, "type", "asset", "description"),
                        pick_text(row, "make", "manufacturer"),
                        pick_text(row, "location"),
                    ) if x).strip()
                    cost = pick_number(row, "cost", "acquisitioncost", "value",
                                       "grossblock")
                    date = pick_text(row, "acquired", "acquisitiondate", "date",
                                     "purchasedate")
                    if not label and cost is None:
                        continue
                    conn.execute("""
                        INSERT INTO assets
                            (description, acquisition_cost, acquisition_date)
                        VALUES (?, ?, ?)
                    """, (label, cost or 0, date or None))

        elif doc_type == "trial_balance":
            for sheet_name, sheet_data in data.get("sheets", {}).items():
                if sheet_name.lower().startswith("note"):
                    continue
                # Sheets are named for the fiscal year they cover: "TB 2019-20".
                year_match = re.search(r"(20\d{2})", sheet_name)
                fiscal_year = int(year_match.group(1)) if year_match else None
                for row in sheet_data.get("data", []):
                    if not isinstance(row, dict):
                        continue
                    account = pick_text(row, "account", "particulars", "head")
                    if not account or account.upper() == "TOTAL":
                        continue
                    conn.execute("""
                        INSERT INTO trial_balance
                            (fiscal_year, account, debit, credit, balance, doc_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (fiscal_year, account,
                          pick_number(row, "debit") or 0,
                          pick_number(row, "credit") or 0,
                          pick_number(row, "balance") or 0, doc_id))

        elif doc_type == "receivables_ageing":
            for sheet_name, sheet_data in data.get("sheets", {}).items():
                if sheet_name.lower().startswith("note"):
                    continue
                for row in sheet_data.get("data", []):
                    if not isinstance(row, dict):
                        continue
                    cname = pick_text(row, "client", "customer", "party")
                    if not cname:
                        continue
                    c_row = conn.execute(
                        "SELECT client_id FROM clients WHERE LOWER(client_name) = LOWER(?) "
                        "OR client_name LIKE ?", (cname, f"%{cname}%")).fetchone()
                    inv = pick_number(row, "invoiced", "billed", "invoiceamount") or 0
                    rec = pick_number(row, "received", "receipts", "collected") or 0
                    out = pick_number(row, "outstanding", "balance", "due")
                    # The register states outstanding, but derive it if a
                    # differently-shaped workbook does not.
                    if out is None:
                        out = inv - rec
                    conn.execute("""
                        INSERT INTO receivables
                            (invoice_no, client_name, client_id, invoice_date,
                             invoiced, status, received, outstanding, doc_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (pick_text(row, "invoiceno", "invoice", "billno"), cname,
                          c_row[0] if c_row else None,
                          pick_text(row, "invoicedate", "date"), inv,
                          pick_text(row, "status"), rec, out, doc_id))
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, doc_type, data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} workbooks")
    return count


# ── Financial and commercial record loaders ─────────────────────────────────

def _each(pattern):
    """Yield (doc_id, data) for every extracted JSON matching a glob."""
    for json_file in sorted(EXTRACTED_DIR.glob(pattern)):
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "_error" in data:
            continue
        yield data.get("_doc_id", json_file.stem), data


def load_statement_items(conn):
    """Load financial-statement line items (already normalised to rupees)."""
    docs = rows = 0
    for doc_id, data in _each("DOC-FS-*.json"):
        for item in data.get("line_items", []):
            conn.execute("""
                INSERT INTO statement_items
                    (fiscal_year, section, particulars, current_year,
                     previous_year, doc_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (data.get("fiscal_year"), item.get("section"),
                  item.get("particulars"), item.get("current_year"),
                  item.get("previous_year"), doc_id))
            rows += 1
        docs += 1
    print(f"  Loaded {docs} financial statements ({rows} line items)")
    return rows


def load_bank_txns(conn):
    docs = rows = 0
    for doc_id, data in _each("DOC-BANK-*.json"):
        for t in data.get("transactions", []):
            conn.execute("""
                INSERT INTO bank_txns
                    (fiscal_year, date, particulars, amount, direction,
                     balance, doc_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (data.get("fiscal_year"), t.get("date"), t.get("particulars"),
                  t.get("amount"), t.get("direction"), t.get("balance"), doc_id))
            rows += 1
        conn.execute("""
            INSERT INTO bank_statements (year, bank_name, opening_balance,
                                         closing_balance, doc_id)
            VALUES (?, ?, ?, ?, ?)
        """, (data.get("fiscal_year"), data.get("bank_name"),
              (data.get("transactions") or [{}])[0].get("balance"),
              (data.get("transactions") or [{}])[-1].get("balance"), doc_id))
        docs += 1
    print(f"  Loaded {docs} bank statements ({rows} transactions)")
    return rows


def load_ledger_entries(conn):
    docs = rows = 0
    for doc_id, data in _each("DOC-GLB-*.json"):
        for e in data.get("entries", []):
            conn.execute("""
                INSERT INTO ledger_entries
                    (year, date, description, project_name, entry_type,
                     debit, credit, doc_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (data.get("fiscal_year"), e.get("date"), e.get("narration"),
                  None, e.get("account_name"), e.get("amount"),
                  e.get("balance"), doc_id))
            rows += 1
        docs += 1
    print(f"  Loaded {docs} ledger books ({rows} postings)")
    return rows


def load_bills(conn):
    docs = rows = 0
    for pattern in ("DOC-RABILL-*.json", "DOC-FINBILL-*.json"):
        for doc_id, data in _each(pattern):
            client = data.get("client_name")
            cur = conn.execute("""
                INSERT INTO bills
                    (doc_type, contract_no, client_name, client_id, bill_no,
                     bill_date, ra_number, awarded_value, total_billed,
                     period_start, period_end, value_of_work, gst, retention,
                     net_claimed, cumulative, doc_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (data.get("_doc_type"), data.get("contract_no"), client,
                  get_or_create_client(conn, client) if client else None,
                  data.get("bill_no"), data.get("bill_date"),
                  data.get("ra_number"), data.get("awarded_value"),
                  data.get("total_billed"), data.get("period_start"),
                  data.get("period_end"), data.get("value_of_work"),
                  data.get("gst"), data.get("retention"),
                  data.get("net_claimed"), data.get("cumulative"), doc_id))
            bill_id = cur.lastrowid
            for it in data.get("items", []):
                conn.execute("""
                    INSERT INTO bill_items
                        (bill_id, item_no, description, unit, rate, quantity,
                         amount, doc_id)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (bill_id, it.get("item_no"), it.get("description"),
                      it.get("unit"), it.get("rate"), it.get("quantity"),
                      it.get("amount"), doc_id))
                rows += 1
            docs += 1
    print(f"  Loaded {docs} bills ({rows} BOQ lines)")
    return docs


def load_tenders(conn):
    count = 0
    for doc_id, data in _each("DOC-DOSSIER-*.json"):
        client = data.get("client_name")
        conn.execute("""
            INSERT INTO tenders
                (tender_ref, client_name, client_id, work_category, bid_value,
                 earnest_money, submitted_date, relevant_works_cited, doc_id)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (data.get("tender_ref"), client,
              get_or_create_client(conn, client) if client else None,
              data.get("work_category"), data.get("bid_value"),
              data.get("earnest_money"), data.get("submitted_date"),
              data.get("relevant_works_cited"), doc_id))
        count += 1
    print(f"  Loaded {count} tender dossiers")
    return count


def load_compliance(conn):
    docs = rows = 0
    for doc_id, data in _each("DOC-CM-*.json"):
        for it in data.get("items", []):
            conn.execute("""
                INSERT INTO compliance_items
                    (tender_ref, work_category, item_no, requirement, status, doc_id)
                VALUES (?,?,?,?,?,?)
            """, (data.get("tender_ref"), data.get("work_category"),
                  it.get("item_no"), it.get("requirement"), it.get("status"),
                  doc_id))
            rows += 1
        docs += 1
    print(f"  Loaded {docs} compliance matrices ({rows} items)")
    return rows


def load_annual_reports(conn):
    docs = rows = 0
    for doc_id, data in _each("DOC-AR-*.json"):
        fy = data.get("fiscal_year")
        for metric in ("gross_billings", "gross_billings_prior", "net_revenue",
                       "net_revenue_prior", "profit", "profit_prior",
                       "contracts_in_execution", "order_book_value",
                       "variation_value", "variation_orders"):
            if data.get(metric) is not None:
                conn.execute("""
                    INSERT INTO annual_figures (fiscal_year, metric, segment,
                                                value, doc_id)
                    VALUES (?, ?, NULL, ?, ?)
                """, (fy, metric, data[metric], doc_id))
                rows += 1
        for seg in data.get("segment_revenue", []):
            conn.execute("""
                INSERT INTO annual_figures (fiscal_year, metric, segment,
                                            value, doc_id)
                VALUES (?, 'segment_revenue', ?, ?, ?)
            """, (fy, seg.get("segment"), seg.get("revenue"), doc_id))
            rows += 1
        docs += 1
    print(f"  Loaded {docs} annual reports ({rows} figures)")
    return rows


def load_doc_text(conn):
    """Store every document's full text for fallback retrieval."""
    count = 0
    for doc_id, data in _each("*.json"):
        text = data.get("_text")
        if not text:
            continue
        conn.execute("INSERT OR REPLACE INTO doc_text VALUES (?, ?, ?)",
                     (doc_id, data.get("_doc_type"), text))
        count += 1
    print(f"  Stored full text for {count} documents")
    return count


# ── Reconciliation against the credentials pack ─────────────────────────────

def reconcile_with_portfolio(conn):
    """Reconcile the works table against DOC-PPP-001, the credentials pack.

    The individual completion certificates state the role and the category in
    prose that varies by issuing office, so reading them per-document is
    lossy: a certificate that never says "JV Partner" is indistinguishable
    from one that says nothing, and each office capitalises the category its
    own way. The portfolio states both fields once, uniformly, for all 155
    works, keyed by the package number in its certificate reference.

    So the certificates remain the source for everything they state
    unambiguously (value, dates, grading, supervising engineer) and the
    portfolio settles role and category, fills any gap, and flags every
    disagreement for inspection.
    """
    portfolio = parse_portfolio()
    if not portfolio:
        print("  WARNING: portfolio not parsed — skipping reconciliation")
        return {}

    stats = collections.Counter()
    conflicts = []

    rows = conn.execute(
        "SELECT work_id, project_name, client_id, contract_value, "
        "       completion_date, work_category, role FROM works").fetchall()

    for work_id, project_name, client_id, value, comp_date, category, role in rows:
        m = re.search(r"Pkg-(\d+)", project_name or "")
        if not m:
            stats["no_pkg_number"] += 1
            continue
        ref = portfolio.get(int(m.group(1)))
        if not ref:
            stats["not_in_portfolio"] += 1
            continue
        stats["matched"] += 1

        updates = {"pkg_no": ref["pkg"],
                   "certificate_ref": ref["certificate_ref"],
                   "client_office": ref["client_office"]}

        # Role and category: the portfolio is authoritative.
        if ref["role"] and ref["role"] != role:
            updates["role"] = ref["role"]
            stats["role_corrected"] += 1
        if ref["work_category"] and ref["work_category"] != category:
            updates["work_category"] = ref["work_category"]
            stats["category_normalised"] += 1

        # Value and date: keep the certificate's reading, which is stated to
        # the rupee, unless it is missing or materially disagrees.
        if ref["contract_value"]:
            if not value:
                updates["contract_value"] = ref["contract_value"]
                stats["value_filled"] += 1
            elif abs(value - ref["contract_value"]) / ref["contract_value"] > 0.005:
                conflicts.append(("value", project_name, value, ref["contract_value"]))
                updates["contract_value"] = ref["contract_value"]
                stats["value_corrected"] += 1
        if ref["completion_date"]:
            if not comp_date:
                updates["completion_date"] = ref["completion_date"]
                stats["date_filled"] += 1
            elif comp_date != ref["completion_date"]:
                conflicts.append(("date", project_name, comp_date, ref["completion_date"]))
                updates["completion_date"] = ref["completion_date"]
                stats["date_corrected"] += 1

        if ref["client_name"]:
            portfolio_client = get_or_create_client(conn, ref["client_name"])
            if portfolio_client != client_id:
                conflicts.append(("client", project_name, client_id, ref["client_name"]))
                updates["client_id"] = portfolio_client
                stats["client_corrected"] += 1

        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE works SET {sets} WHERE work_id = ?",
                     (*updates.values(), work_id))

    # A client row can be orphaned if every one of its works was reassigned.
    # Bills and tenders also name clients, so only rows referenced nowhere go.
    conn.execute("""
        DELETE FROM clients WHERE client_id NOT IN (
            SELECT client_id FROM works   WHERE client_id IS NOT NULL
            UNION SELECT client_id FROM bills   WHERE client_id IS NOT NULL
            UNION SELECT client_id FROM tenders WHERE client_id IS NOT NULL
        )
    """)
    conn.commit()

    print(f"  Reconciled {stats['matched']}/{len(rows)} works against DOC-PPP-001")
    for key in ("role_corrected", "category_normalised", "value_filled",
                "value_corrected", "date_filled", "date_corrected",
                "client_corrected", "no_pkg_number", "not_in_portfolio"):
        if stats[key]:
            print(f"    {key}: {stats[key]}")
    for kind, name, was, now in conflicts[:10]:
        print(f"    conflict [{kind}] {name}: certificate={was} portfolio={now}")
    return stats


# ── Validation ──────────────────────────────────────────────────────────────

def validate_database(conn):
    """Run validation queries to verify database integrity."""
    print("\n=== Database Validation ===\n")
    
    checks = [
        ("Total works", "SELECT COUNT(*) FROM works", None),
        ("Total clients", "SELECT COUNT(*) FROM clients", None),
        ("Works WITH reference letters",
         "SELECT COUNT(*) FROM works WHERE has_reference_letter = 1", None),
        ("Works WITHOUT reference letters",
         "SELECT COUNT(*) FROM works WHERE has_reference_letter = 0", None),
        ("Total reference letters",
         "SELECT COUNT(*) FROM reference_letters", None),
        ("Total engineers", "SELECT COUNT(*) FROM engineers", None),
        ("Engineer-work links", "SELECT COUNT(*) FROM engineer_works", None),
        ("Engineer certificates", "SELECT COUNT(*) FROM engineer_certs", None),
        ("Performance bonds", "SELECT COUNT(*) FROM bonds", None),
        ("Works with NULL contract_value",
         "SELECT COUNT(*) FROM works WHERE contract_value IS NULL", None),
        ("Works with NULL completion_date",
         "SELECT COUNT(*) FROM works WHERE completion_date IS NULL", None),
        ("Financial rows", "SELECT COUNT(*) FROM financials", None),
        ("Ledger entries", "SELECT COUNT(*) FROM ledger_entries", None),
        ("ISO certs", "SELECT COUNT(*) FROM iso_certs", None),
    ]
    
    for label, query, expected in checks:
        actual = conn.execute(query).fetchone()[0]
        if expected is not None:
            status = "OK" if actual == expected else "MISMATCH"
            print(f"  {status:10s} {label}: {actual} (expected {expected})")
        else:
            print(f"  {'INFO':10s} {label}: {actual}")
    
    # Show role distribution
    print("\n--- Role distribution ---")
    for row in conn.execute("SELECT role, COUNT(*) FROM works GROUP BY role"):
        print(f"  {row[0]}: {row[1]}")
    
    # Show grading distribution
    print("\n--- Grading distribution ---")
    for row in conn.execute(
            "SELECT performance_grading, COUNT(*) FROM works "
            "GROUP BY performance_grading ORDER BY COUNT(*) DESC"):
        print(f"  [{row[1]:3d}] {row[0]}")
    
    # Jal Nigam, Jharkhand test case
    print("\n--- Integrity ---")
    # Integrity checks that hold on any estate, rather than a spot check
    # against figures from the sample one. What matters is not that a
    # particular client totals a particular number, but that nothing was
    # dropped or left unresolved on the way in.
    problems = []
    checks = [
        ("works with no client", "SELECT COUNT(*) FROM works WHERE client_id IS NULL"),
        ("works with no value",
         "SELECT COUNT(*) FROM works WHERE contract_value IS NULL OR contract_value <= 0"),
        ("works with no completion date",
         "SELECT COUNT(*) FROM works WHERE completion_date IS NULL"),
        ("works with no category",
         "SELECT COUNT(*) FROM works WHERE work_category IS NULL OR work_category = ''"),
        ("receivable rows where outstanding != invoiced - received",
         "SELECT COUNT(*) FROM receivables "
         "WHERE ABS(COALESCE(outstanding,0) - (COALESCE(invoiced,0) - COALESCE(received,0))) > 1"),
    ]
    for label, query in checks:
        try:
            n = conn.execute(query).fetchone()[0]
        except sqlite3.Error:
            continue
        if n:
            problems.append(f"{n} {label}")
    if problems:
        print("  integrity warnings:")
        for problem in problems:
            print(f"      {problem}")
    else:
        print("  integrity checks passed")


# ── Main ────────────────────────────────────────────────────────────────────

def build_database():
    """Build the complete database from extracted JSON files."""
    print(f"Building database at: {DB_PATH}")
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    
    # Drop all existing tables for clean rebuild
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    for t in tables:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    
    # Create schema
    conn.executescript(SCHEMA)
    print("  Schema created\n")
    
    # Load in dependency order
    print("Loading documents...")
    load_completion_certificates(conn)
    load_company_completion_certificates(conn)
    load_reference_letters(conn)
    load_personnel_certificates(conn)
    load_cvs(conn)
    load_performance_bonds(conn)
    # load_financial_statements / load_ledger_books are superseded by the
    # typed loaders below (statement_items, ledger_entries), which read the
    # real extracted structure rather than fields the extractors never emitted.
    load_iso_certificates(conn)
    load_workbook_data(conn)

    print("\nLoading financial and commercial records...")
    load_statement_items(conn)
    load_ledger_entries(conn)
    load_bank_txns(conn)
    load_bills(conn)
    load_tenders(conn)
    load_compliance(conn)
    load_annual_reports(conn)
    load_doc_text(conn)

    conn.commit()

    print("\nReconciling against the credentials pack...")
    reconcile_with_portfolio(conn)

    conn.commit()
    print("\nDatabase built successfully!")
    
    return conn


def main():
    parser = argparse.ArgumentParser(description="Build SQLite database")
    parser.add_argument("--validate", action="store_true",
                       help="Run validation queries after build")
    args = parser.parse_args()
    
    conn = build_database()
    
    if args.validate:
        validate_database(conn)
    
    conn.close()


if __name__ == "__main__":
    main()
