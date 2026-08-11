"""
build_db.py — Construct the SQLite database from extracted JSON files.

Reads all JSON from data/extracted/ and populates company.db with
properly normalized and linked entities.

Usage:
    python src/build_db.py              # Build/rebuild the database
    python src/build_db.py --validate   # Run validation queries after build
"""
import argparse
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
    doc_ref_id           TEXT
);

-- Engineer <-> Work many-to-many
CREATE TABLE IF NOT EXISTS engineer_works (
    engineer_id     INTEGER REFERENCES engineers(engineer_id),
    work_id         INTEGER REFERENCES works(work_id),
    role_on_project TEXT,
    PRIMARY KEY (engineer_id, work_id)
);

-- Engineer certificates
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
                        desc = (row.get("Description") or row.get("Item")
                                or row.get("description") or "")
                        qty = row.get("Quantity") or row.get("Qty") or 0
                        rate = row.get("Rate") or row.get("Unit Rate") or 0
                        amount = row.get("Amount") or row.get("Total") or 0
                        try:
                            conn.execute("""
                                INSERT INTO boq_items
                                    (contract_ref, item_desc, quantity, rate, amount)
                                VALUES (?, ?, ?, ?, ?)
                            """, (
                                data.get("contract_ref", doc_id),
                                str(desc),
                                float(qty) if qty else 0,
                                float(rate) if rate else 0,
                                float(amount) if amount else 0,
                            ))
                        except (ValueError, TypeError):
                            pass
        
        elif doc_type == "asset_register":
            for sheet_name, sheet_data in data.get("sheets", {}).items():
                for row in sheet_data.get("data", []):
                    if isinstance(row, dict):
                        desc = (row.get("Description") or row.get("Asset")
                                or row.get("description") or "")
                        cost = (row.get("Cost") or row.get("Acquisition Cost")
                                or row.get("Value") or 0)
                        date = row.get("Date") or row.get("Acquisition Date")
                        try:
                            conn.execute("""
                                INSERT INTO assets
                                    (description, acquisition_cost, acquisition_date)
                                VALUES (?, ?, ?)
                            """, (str(desc), float(cost) if cost else 0,
                                  str(date) if date else None))
                        except (ValueError, TypeError):
                            pass

        elif doc_type == "receivables_ageing":
            for sheet_name, sheet_data in data.get("sheets", {}).items():
                if sheet_name == "Notes":
                    continue
                for row in sheet_data.get("data", []):
                    if isinstance(row, dict) and row.get("Client"):
                        cname = row.get("Client").strip()
                        c_row = conn.execute("SELECT client_id FROM clients WHERE LOWER(client_name) = LOWER(?) OR client_name LIKE ?", (cname, f"%{cname}%")).fetchone()
                        client_id = c_row[0] if c_row else None
                        inv = float(row.get("Invoiced (INR)") or 0)
                        rec = float(row.get("Received (INR)") or 0)
                        out = float(row.get("Outstanding (INR)") or 0)
                        conn.execute("""
                            INSERT INTO receivables
                                (invoice_no, client_name, client_id, invoice_date, invoiced, status, received, outstanding, doc_id)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (row.get("Invoice No"), cname, client_id, row.get("Invoice Date"), inv, row.get("Status"), rec, out, doc_id))
        
        conn.execute(
            "INSERT OR REPLACE INTO doc_metadata VALUES (?, ?, ?, 0)",
            (doc_id, doc_type, data.get("_source_file")))
        count += 1
    
    print(f"  Loaded {count} workbooks")
    return count


# ── Validation ──────────────────────────────────────────────────────────────

def validate_database(conn):
    """Run validation queries to verify database integrity."""
    print("\n=== Database Validation ===\n")
    
    checks = [
        ("Total works", "SELECT COUNT(*) FROM works", 155),
        ("Total clients", "SELECT COUNT(*) FROM clients", None),
        ("Works WITH reference letters",
         "SELECT COUNT(*) FROM works WHERE has_reference_letter = 1", None),
        ("Works WITHOUT reference letters",
         "SELECT COUNT(*) FROM works WHERE has_reference_letter = 0", None),
        ("Total reference letters",
         "SELECT COUNT(*) FROM reference_letters", 132),
        ("Total engineers", "SELECT COUNT(*) FROM engineers", None),
        ("Engineer-work links", "SELECT COUNT(*) FROM engineer_works", None),
        ("Engineer certificates", "SELECT COUNT(*) FROM engineer_certs", None),
        ("Performance bonds", "SELECT COUNT(*) FROM bonds", 60),
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
    print("\n--- Jal Nigam, Jharkhand works (test case) ---")
    cur = conn.execute("""
        SELECT w.project_name, w.contract_value, w.has_reference_letter,
               w.performance_grading, w.role
        FROM works w
        JOIN clients c ON w.client_id = c.client_id
        WHERE c.client_name LIKE '%Jal Nigam%Jharkhand%'
    """)
    expected_values = {730200000, 814400000, 69200000}
    actual_values = set()
    for row in cur:
        actual_values.add(row[1])
        print(f"  {row[0]} | val={row[1]} | ref={row[2]} | "
              f"grade={row[3]} | role={row[4]}")
    
    if actual_values == expected_values:
        print(f"  -> Values MATCH expected: {expected_values}")
    elif actual_values:
        print(f"  -> WARNING: Expected {expected_values}, got {actual_values}")
    else:
        print("  -> No matching records found")


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
    load_financial_statements(conn)
    load_ledger_books(conn)
    load_iso_certificates(conn)
    load_workbook_data(conn)
    
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
