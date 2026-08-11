"""
extract_pdfs.py — Extract structured data from all PDFs using Gemini 2.0 Flash.

Sends each PDF to Gemini with a document-type-specific JSON schema prompt.
Gemini sees the visual layout natively, avoiding the silent-data-loss problem
warned about in the BRIEFING.

Features:
  - Rate limiting (12 RPM with 5s delay)
  - Exponential backoff on 429 errors
  - Crash-safe: saves each result immediately to data/extracted/{doc_id}.json
  - Idempotent: skips already-extracted docs on re-run
  - Progress bar with tqdm

Usage:
    python src/extract_pdfs.py                  # Extract all PDFs
    python src/extract_pdfs.py --doc-type cv    # Extract only CVs
    python src/extract_pdfs.py --doc-id DOC-CC-001  # Extract one document
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

from tqdm import tqdm

# The offline extractor (extract_local_fast.py) imports load_document_index
# from this module, so the Gemini client must stay optional: the local path
# needs no API key and no network.
try:
    import google.generativeai as genai
except ImportError:
    genai = None

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import (
    GEMINI_API_KEY, GEMINI_MODEL, GEMINI_DELAY_SECONDS,
    DOCUMENTS_DIR, EXTRACTED_DIR, PROJECT_ROOT, DOC_TYPE_PRIORITY,
)


# ── Configure Gemini ────────────────────────────────────────────────────────
if genai is not None:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
else:
    model = None


# ── Document-type-specific extraction prompts ───────────────────────────────
# Each prompt tells Gemini exactly what fields to extract and how to format them.
# The JSON schema ensures structured, machine-readable output.

EXTRACTION_PROMPTS = {
    "completion_certificate": """
Extract ALL of the following fields from this completion certificate PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "project_name": "full project name including location and package number, e.g. 'Ring Road - Maharashtra Pkg-125'",
  "client_name": "the full name of the client/issuing authority/department who issued this certificate",
  "contract_value": "the contract/work value as a string exactly as written in the document (e.g. 'INR 33.38 Cr' or '33,38,00,000')",
  "contract_value_raw": "your best conversion of the contract value to a plain integer in rupees",
  "completion_date": "completion date in YYYY-MM-DD format",
  "commencement_date": "commencement/start date in YYYY-MM-DD format, or null if not present",
  "performance_grading": "the client's written assessment/grading of performance (e.g. 'Excellent', 'Very Good', 'Good', 'Satisfactory'), exactly as written",
  "role": "whether the company was 'Prime' contractor or 'Sub-contractor', or null if not stated",
  "work_category": "the category/type of work (e.g. 'Highway', 'Flyover', 'Water Treatment Plant', 'Bridge', 'Drainage', etc.)",
  "signing_officer": "name of the officer who signed the certificate, or null",
  "work_description": "brief description of the work scope if available"
}

IMPORTANT:
- Extract the contract value EXACTLY as written (preserve the original format in contract_value)
- Also provide your conversion to raw integer rupees in contract_value_raw
- The performance grading is often in a sentence like "The quality of work is rated as Very Good"
- The client name is the organization that ISSUED the certificate, not the contractor
- Return ONLY the JSON object, no other text
""",

    "company_completion_certificate": """
Extract ALL of the following fields from this company's own completion certificate PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "project_name": "full project name including location and package number",
  "client_name": "the client/department for whom the work was done",
  "contract_value": "the contract value as written (original format)",
  "contract_value_raw": "conversion to plain integer rupees",
  "completion_date": "completion date in YYYY-MM-DD format",
  "commencement_date": "commencement/start date in YYYY-MM-DD format, or null",
  "work_category": "type of work (Highway, Bridge, WTP, etc.)",
  "role": "'Prime' or 'Sub-contractor' or null",
  "work_description": "brief description of scope"
}

Return ONLY the JSON object, no other text.
""",

    "reference_letter": """
Extract ALL of the following fields from this reference letter PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "project_name": "full project name the reference is about",
  "client_name": "the organization that issued/wrote this reference letter",
  "letter_date": "date of the letter in YYYY-MM-DD format, or null",
  "remarks": "any specific remarks about quality or performance, or null",
  "signatory": "name of person who signed, or null"
}

Return ONLY the JSON object, no other text.
""",

    "personnel_certificate": """
Extract ALL of the following fields from this personnel/professional certificate PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "person_name": "full name of the certificate holder",
  "cert_type": "type of certification (e.g. 'PMP', 'Six Sigma Black Belt', 'Six Sigma Green Belt', etc.)",
  "cert_id": "the certificate ID/number (e.g. 'PMI-200029')",
  "issue_date": "date of issue in YYYY-MM-DD format",
  "expiry_date": "expiry date in YYYY-MM-DD format, or null",
  "issuing_body": "the organization that issued the certificate"
}

Return ONLY the JSON object, no other text.
""",

    "cv": """
Extract ALL of the following fields from this CV/resume PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "person_name": "full name of the person",
  "current_designation": "their current job title/designation",
  "years_of_experience": "total years of experience as a number, or null",
  "qualifications": ["list of educational qualifications"],
  "certifications": ["list of professional certifications held"],
  "projects_led": [
    {
      "project_name": "full project name including location and package number",
      "client_name": "client for this project",
      "role_on_project": "role on this project (Project Manager, Site Engineer, etc.)",
      "contract_value": "value as written, or null",
      "completion_date": "in YYYY-MM-DD format, or null"
    }
  ]
}

IMPORTANT: Extract ALL projects listed in the CV, not just the first few.
Return ONLY the JSON object, no other text.
""",

    "performance_bond": """
Extract ALL of the following fields from this performance bond/bank guarantee PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "project_name": "full project name",
  "client_name": "the client/beneficiary",
  "contractor_name": "the contractor for whom the bond is issued",
  "bond_value": "the bond/guarantee amount as written",
  "bond_value_raw": "conversion to plain integer rupees",
  "contract_value": "the underlying contract value as written, or null",
  "contract_value_raw": "conversion to plain integer rupees, or null",
  "bank_name": "the issuing bank",
  "bond_number": "the bond/guarantee number",
  "issue_date": "issue date in YYYY-MM-DD, or null",
  "expiry_date": "expiry date in YYYY-MM-DD, or null"
}

Return ONLY the JSON object, no other text.
""",

    "general_ledger_book": """
Extract ALL data from this general ledger book PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "year": "the financial year this ledger covers",
  "entries": [
    {
      "date": "entry date in YYYY-MM-DD format",
      "description": "the narration/description",
      "project_name": "related project name if identifiable, or null",
      "client_name": "related client if identifiable, or null",
      "entry_type": "invoice/receipt/credit_note/journal/payment/other",
      "debit": "debit amount as a number, or 0",
      "credit": "credit amount as a number, or 0"
    }
  ]
}

IMPORTANT: Extract ALL entries from ALL pages. Do not truncate.
Return ONLY the JSON object, no other text.
""",

    "bank_statement": """
Extract ALL data from this bank statement PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "year": "the year this statement covers",
  "bank_name": "the name of the bank",
  "account_number": "the account number, or null",
  "opening_balance": "opening balance as a number",
  "closing_balance": "closing balance as a number",
  "transactions": [
    {
      "date": "transaction date in YYYY-MM-DD",
      "description": "transaction description/narration",
      "debit": "withdrawal amount as number, or 0",
      "credit": "deposit amount as number, or 0",
      "balance": "running balance as number"
    }
  ]
}

IMPORTANT: Extract ALL transactions from ALL pages.
Return ONLY the JSON object, no other text.
""",

    "financial_statement": """
Extract ALL financial data from this financial statement/statutory accounts PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "year": "the financial year (e.g. '2023-24' or '2024')",
  "statement_type": "the type: balance_sheet, profit_and_loss, cash_flow, or combined",
  "revenue": "total revenue/income as a number in rupees, or null",
  "expenses": "total expenses as a number, or null",
  "profit_before_tax": "profit before tax as a number, or null",
  "profit_after_tax": "net profit after tax as a number, or null",
  "total_assets": "total assets as a number, or null",
  "total_liabilities": "total liabilities as a number, or null",
  "shareholders_equity": "shareholders equity/net worth as a number, or null",
  "current_assets": "current assets as a number, or null",
  "current_liabilities": "current liabilities as a number, or null",
  "cash_and_equivalents": "cash and cash equivalents, or null",
  "key_ratios": {
    "current_ratio": "number or null",
    "debt_equity_ratio": "number or null",
    "net_profit_margin": "number or null"
  },
  "additional_notes": "any important notes or qualifications, or null"
}

Extract values in raw rupees (not Lakhs/Crores) where possible.
Return ONLY the JSON object, no other text.
""",

    "compliance_matrix": """
Extract ALL data from this compliance matrix PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "tender_reference": "the tender reference number or project name",
  "project_name": "the project this compliance matrix relates to, or null",
  "client_name": "the client/department, or null",
  "items": [
    {
      "requirement": "the tender requirement description",
      "compliance_status": "Compliant/Non-Compliant/Partially Compliant",
      "reference_document": "document referenced for proof, or null",
      "remarks": "any remarks, or null"
    }
  ]
}

Return ONLY the JSON object, no other text.
""",

    "tender_dossier": """
Extract ALL data from this tender dossier/bid document PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "tender_name": "the name/title of the tender",
  "project_name": "the project being tendered",
  "client_name": "the client/department issuing the tender",
  "bid_value": "our bid amount as written",
  "bid_value_raw": "conversion to plain integer rupees",
  "estimated_value": "estimated contract value if given, or null",
  "submission_date": "bid submission date in YYYY-MM-DD, or null",
  "key_personnel_listed": ["names of key personnel included in the bid"],
  "projects_cited": ["names of past projects referenced in the bid"]
}

Return ONLY the JSON object, no other text.
""",

    "ra_bill": """
Extract ALL data from this Running Account (RA) bill PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "bill_number": "the RA bill number",
  "project_name": "the project name",
  "client_name": "the client, or null",
  "bill_date": "date in YYYY-MM-DD, or null",
  "bill_amount": "total bill amount as a number in rupees",
  "cumulative_amount": "cumulative amount to date, or null",
  "items": [
    {
      "description": "item description",
      "quantity": "quantity as a number",
      "rate": "rate per unit as a number",
      "amount": "item total as a number"
    }
  ]
}

Return ONLY the JSON object, no other text.
""",

    "final_ra_bill": """
Extract ALL data from this Final Running Account (RA) bill PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "bill_number": "the final bill number",
  "project_name": "the project name",
  "client_name": "the client, or null",
  "contract_number": "contract/agreement number, or null",
  "bill_date": "date in YYYY-MM-DD, or null",
  "final_bill_amount": "final bill amount in rupees",
  "total_contract_value": "total contract value, or null",
  "items": [
    {
      "description": "item description",
      "quantity": "quantity as a number",
      "rate": "rate per unit as a number",
      "amount": "item total as a number"
    }
  ]
}

Return ONLY the JSON object, no other text.
""",

    "iso_certificate": """
Extract ALL of the following fields from this ISO/quality certificate PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "cert_type": "ISO certification type (e.g. 'ISO 9001:2015', 'ISO 14001', 'OHSAS 18001')",
  "cert_number": "the certificate number",
  "company_name": "the certified company name",
  "scope": "the scope/description of certification",
  "valid_from": "validity start date in YYYY-MM-DD",
  "valid_to": "validity end/expiry date in YYYY-MM-DD",
  "issuing_body": "the certification body that issued it"
}

Return ONLY the JSON object, no other text.
""",

    "annual_report": """
Extract ALL key data from this annual report PDF.
Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "year": "the reporting year",
  "company_name": "the company name",
  "revenue": "total revenue/turnover in rupees, or null",
  "profit": "net profit in rupees, or null",
  "employee_count": "total number of employees, or null",
  "business_units": ["list of business unit names"],
  "key_projects": [
    {
      "project_name": "project name",
      "client_name": "client, or null",
      "value": "value as written, or null",
      "status": "completed/ongoing/other"
    }
  ],
  "project_completion_summary": "any tabular summary of completed projects, or null",
  "directors_report_highlights": "key points from directors report, or null",
  "additional_data": "any other important structured data found, or null"
}

IMPORTANT: Extract ALL projects mentioned, ALL financial figures, ALL personnel mentioned.
Return ONLY the JSON object, no other text.
""",

    "past_performance_portfolio": """
Extract ALL data from this past performance portfolio/credentials document.
This is potentially a MASTER INDEX of all company projects.

Return a JSON object with exactly these keys:

{
  "doc_id": "the document ID if visible, or null",
  "company_name": "the company name",
  "projects": [
    {
      "project_name": "full project name including location and package",
      "client_name": "the client/department",
      "contract_value": "value as written",
      "contract_value_raw": "conversion to integer rupees",
      "completion_date": "in YYYY-MM-DD, or null",
      "work_category": "category of work",
      "role": "'Prime' or 'Sub-contractor' or null",
      "status": "Completed/Ongoing/other"
    }
  ],
  "total_value": "total portfolio value if stated, or null"
}

CRITICAL: This document may list ALL projects. Extract EVERY SINGLE ONE.
Do not truncate or summarize. Return the complete list.
Return ONLY the JSON object, no other text.
""",
}


def load_document_index():
    """Load the document_index.csv and return a list of dicts."""
    index_path = PROJECT_ROOT / "document_index.csv"
    docs = []
    with open(index_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            docs.append(row)
    return docs


def extract_single_pdf(doc_id: str, doc_type: str, filepath: Path,
                       retry_count: int = 3) -> dict:
    """
    Send a single PDF to Gemini for structured extraction.
    Returns the extracted JSON as a dict.
    """
    prompt = EXTRACTION_PROMPTS.get(doc_type)
    if prompt is None:
        # Fallback generic prompt for any unknown type
        prompt = f"""
Extract ALL structured data from this {doc_type} PDF document.
Return a comprehensive JSON object capturing every field, table, 
and data point in the document. Include the doc_id if visible.
Return ONLY the JSON object, no other text.
"""

    # Read PDF bytes
    pdf_bytes = filepath.read_bytes()
    
    for attempt in range(retry_count):
        try:
            # Upload PDF to Gemini (it understands PDF natively)
            response = model.generate_content(
                [
                    prompt,
                    {"mime_type": "application/pdf", "data": pdf_bytes}
                ],
                generation_config=genai.GenerationConfig(
                    response_mime_type="application/json",
                    temperature=0.0,  # Deterministic extraction
                ),
                request_options={"timeout": 60},
            )
            
            # Parse the JSON response
            text = response.text.strip()
            # Sometimes Gemini wraps in ```json ... ```
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()
            
            data = json.loads(text)
            data["_doc_id"] = doc_id
            data["_doc_type"] = doc_type
            data["_source_file"] = str(filepath)
            return data
            
        except Exception as e:
            error_str = str(e)
            if "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower():
                # Rate limited — exponential backoff
                wait_time = 60 * (2 ** attempt)
                print(f"\n  Rate limited on {doc_id}. Waiting {wait_time}s...")
                time.sleep(wait_time)
            elif "500" in error_str or "503" in error_str:
                # Server error — retry after short wait
                wait_time = 10 * (attempt + 1)
                print(f"\n  Server error on {doc_id}. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"\n  Error extracting {doc_id}: {e}")
                if attempt < retry_count - 1:
                    time.sleep(5)
                else:
                    return {
                        "_doc_id": doc_id,
                        "_doc_type": doc_type,
                        "_source_file": str(filepath),
                        "_error": str(e),
                    }
    
    return {
        "_doc_id": doc_id,
        "_doc_type": doc_type,
        "_source_file": str(filepath),
        "_error": "Max retries exceeded",
    }


def extract_all(doc_type_filter: str = None, doc_id_filter: str = None):
    """
    Extract all PDFs (or a filtered subset).
    Saves each result to data/extracted/{doc_id}.json.
    Skips already-extracted docs (idempotent).
    """
    docs = load_document_index()
    
    # Filter to PDFs only (skip .xlsx workbooks)
    pdf_docs = [d for d in docs if d['filename'].endswith('.pdf')]
    
    # Apply filters
    if doc_type_filter:
        pdf_docs = [d for d in pdf_docs if d['doc_type'] == doc_type_filter]
    if doc_id_filter:
        pdf_docs = [d for d in pdf_docs if d['doc_id'] == doc_id_filter]
    
    # Sort by priority
    priority_map = {t: i for i, t in enumerate(DOC_TYPE_PRIORITY)}
    pdf_docs.sort(key=lambda d: priority_map.get(d['doc_type'], 999))
    
    # Count already done
    already_done = sum(
        1 for d in pdf_docs
        if (EXTRACTED_DIR / f"{d['doc_id']}.json").exists()
    )
    
    print(f"Total PDFs to process: {len(pdf_docs)}")
    print(f"Already extracted: {already_done}")
    print(f"Remaining: {len(pdf_docs) - already_done}")
    print(f"Estimated time: ~{(len(pdf_docs) - already_done) * GEMINI_DELAY_SECONDS / 60:.1f} minutes")
    print()
    
    extracted_count = 0
    error_count = 0
    
    for doc in tqdm(pdf_docs, desc="Extracting PDFs"):
        doc_id = doc['doc_id']
        doc_type = doc['doc_type']
        filepath = DOCUMENTS_DIR / doc['filename']
        output_path = EXTRACTED_DIR / f"{doc_id}.json"
        
        # Skip if already extracted
        if output_path.exists():
            continue
        
        # Check file exists
        if not filepath.exists():
            print(f"\n  WARNING: File not found: {filepath}")
            error_count += 1
            continue
        
        # Extract
        data = extract_single_pdf(doc_id, doc_type, filepath)
        
        # Save immediately (crash-safe)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        if "_error" in data:
            error_count += 1
        else:
            extracted_count += 1
        
        # Rate limit delay
        time.sleep(GEMINI_DELAY_SECONDS)
    
    print(f"\nExtraction complete!")
    print(f"  Extracted: {extracted_count}")
    print(f"  Errors: {error_count}")
    print(f"  Previously done: {already_done}")


def main():
    parser = argparse.ArgumentParser(description="Extract PDFs using Gemini")
    parser.add_argument("--doc-type", help="Filter by document type")
    parser.add_argument("--doc-id", help="Extract a single document by ID")
    args = parser.parse_args()
    
    if not GEMINI_API_KEY:
        print("ERROR: GEMINI_API_KEY not set. Add it to .env file.")
        sys.exit(1)
    
    extract_all(
        doc_type_filter=args.doc_type,
        doc_id_filter=args.doc_id,
    )


if __name__ == "__main__":
    main()
