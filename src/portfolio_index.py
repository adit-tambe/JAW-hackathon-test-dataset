"""
portfolio_index.py — parse DOC-PPP-001, the consolidated credentials pack.

The portfolio's detail pages carry one authoritative block per completed work:

    25. rCC BridGe - maharashtra PkG-50
    Client
    Maharashtra Municipal Corporation (Prime)
    Category
    Bridges Flyovers
    Executed Value
    INR 57.37 Cr
    Completed
    February 28, 2021 - Certificate CC/21/2021/050

That block names the client, the contractor's role, the canonical category, the
executed value, the completion date, and the certificate reference whose last
segment is the package number. It covers all 155 works, so it doubles as a
reconciliation source for fields the individual certificates state ambiguously
(role, category casing) and as a cross-check on everything else.
"""
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.config import DOCUMENTS_DIR

PORTFOLIO_PDF = DOCUMENTS_DIR / "past_performance_portfolio" / "DOC-PPP-001.pdf"

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}

ROLES = ("Prime", "JV Partner", "Sub-contractor")


def _iso(datestr: str) -> str:
    """'February 28, 2021' -> '2021-02-28'."""
    m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})", (datestr or "").strip())
    if not m or m.group(1) not in MONTHS:
        return None
    return f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"


def _crore(valstr: str) -> int:
    """'INR 57.37 Cr' -> 573700000. Lakh renderings are handled too."""
    m = re.search(r"INR\s*([\d,]+(?:\.\d+)?)\s*(Cr|Crore|Crores|Lakh|Lakhs)",
                  valstr or "", re.I)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    mult = 100_000 if m.group(2).lower().startswith("lakh") else 10_000_000
    return int(round(num * mult))


def parse_portfolio(pdf_path: Path = PORTFOLIO_PDF) -> dict:
    """Return {pkg_number: {...}} for every work in the portfolio detail pages."""
    if not Path(pdf_path).exists():
        return {}

    doc = fitz.open(pdf_path)
    text = "\n".join(page.get_text() for page in doc)
    doc.close()

    # Detail blocks run from one "N. TITLE" heading to the next. The heading's
    # own casing is decorative, so the package number comes from the
    # certificate reference instead, which is unambiguous.
    blocks = re.split(r"\n(?=\d{1,3}\.\s+\S)", text)

    works = {}
    for blk in blocks:
        cert = re.search(r"Certificate\s+(CC/(\d+)/(\d{4})/(\d+))", blk)
        if not cert:
            continue

        # The client line wraps mid-name and even mid-role ("(JV\nPartner)"),
        # so flatten the Client..Category span before reading it.
        client_span = re.search(r"Client\s*\n(.*?)(?:\n\s*Category\b|\Z)", blk, re.S)
        client_m = None
        if client_span:
            flat = re.sub(r"\s+", " ", client_span.group(1)).strip()
            client_m = re.search(r"^(.*?)\s*\(\s*(Prime|JV\s+Partner|Sub-?contractor)\s*\)",
                                 flat, re.I)
        cat_m = re.search(r"Category\s*\n\s*([^\n]+)", blk)
        val_m = re.search(r"Executed Value\s*\n\s*([^\n]+(?:\n[^\n]+)?)", blk)
        done_m = re.search(r"Completed\s*\n\s*([A-Za-z]+\s+\d{1,2},?\s+\d{4})", blk)

        role = None
        if client_m:
            role = re.sub(r"\s+", " ", client_m.group(2)).title().replace("Jv", "JV")

        pkg = int(cert.group(4))
        works[pkg] = {
            "pkg": pkg,
            "client_name": re.sub(r"\s+", " ", client_m.group(1)).strip() if client_m else None,
            "role": role,
            "work_category": re.sub(r"\s+", " ", cat_m.group(1)).strip() if cat_m else None,
            "contract_value": _crore(val_m.group(1)) if val_m else None,
            "completion_date": _iso(done_m.group(1)) if done_m else None,
            "certificate_ref": cert.group(1),
            "client_office": int(cert.group(2)),
        }
    return works


def canonical_categories(works: dict = None) -> set:
    """The category vocabulary as the portfolio spells it (Title Case)."""
    works = works if works is not None else parse_portfolio()
    return {w["work_category"] for w in works.values() if w.get("work_category")}


if __name__ == "__main__":
    import collections
    w = parse_portfolio()
    print(f"works parsed: {len(w)}")
    print("roles:", collections.Counter(x["role"] for x in w.values()))
    print("categories:", sorted(canonical_categories(w)))
    print("clients:", len({x["client_name"] for x in w.values()}))
    missing = [k for k, v in w.items()
               if not all([v["client_name"], v["role"], v["work_category"],
                           v["contract_value"], v["completion_date"]])]
    print("incomplete records:", missing)
    total = sum(x["contract_value"] or 0 for x in w.values())
    print(f"total delivered value: INR {total/1e7:,.2f} Cr")
