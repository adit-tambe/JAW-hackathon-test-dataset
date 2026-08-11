"""
config.py — Central configuration for the BITS Hackathon pipeline.
All paths, API keys, and constants live here.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

import warnings
warnings.filterwarnings("ignore")

# ── Paths ───────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
DOCUMENTS_DIR = PROJECT_ROOT / "documents"
DATA_DIR = PROJECT_ROOT / "data"
EXTRACTED_DIR = DATA_DIR / "extracted"
DB_PATH = DATA_DIR / "company.db"
SAMPLE_QUESTIONS_PATH = PROJECT_ROOT / "sample_questions.json"

# Load .env file explicitly from PROJECT_ROOT
load_dotenv(PROJECT_ROOT / ".env")

# Ensure output directories exist
EXTRACTED_DIR.mkdir(parents=True, exist_ok=True)

# ── Gemini API ──────────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Rate limit settings (free tier: 15 RPM, we use 12 for safety)
GEMINI_RPM = 12
GEMINI_DELAY_SECONDS = 60.0 / GEMINI_RPM  # ~5 seconds between requests
GEMINI_MODEL = "gemini-3.5-flash"

# ── Document types and their priorities ─────────────────────────────────────
# Processing order: highest priority first
DOC_TYPE_PRIORITY = [
    "past_performance_portfolio",   # 1 doc — may be a master index
    "completion_certificate",       # 155 — core project data
    "personnel_certificate",        # 48 — engineer ↔ cert links
    "cv",                           # 39 — engineer ↔ project links
    "reference_letter",             # 132 — presence/absence tracking
    "company_completion_certificate",  # 155 — cross-validation
    "performance_bond",             # 60 — bond data
    "general_ledger_book",          # 8 — financial entries
    "bank_statement",               # 8 — cash movements
    "financial_statement",          # 7 — statutory accounts
    "annual_report",                # 2 — narrative reports
    "compliance_matrix",            # 40 — tender compliance
    "tender_dossier",               # 6 — bid documents
    "ra_bill",                      # 6 — running account bills
    "final_ra_bill",                # 6 — final RA bills
    "iso_certificate",              # 5 — quality accreditations
]
