"""
extract_records.py — extractors for the financial and commercial document
families that the certificate-oriented extractors do not cover.

These carry the figures the credentials documents never state: money actually
invoiced and received, account balances, bill-level measured quantities, bid
values, and the accreditations a tender asks a bidder to prove. Several
reasoning patterns can only be answered from here.

Two corpus properties drive the design:

  * Statement layouts shift across reporting eras — the older statements use
    sentence case where the newer ones use caps — so section detection is
    case-insensitive and keyed off the section letter, not the heading text.
  * Statements are denominated in Lakhs while ledgers and bank statements are
    in rupees. Everything below is normalised to rupees on the way out, and
    the source unit is recorded so a caller can tell which it was.
"""
import re

LAKH = 100_000
CRORE = 10_000_000

# A number as these documents write it: 1,234 / -1,234 / (1,234) / 1234.56
NUM = r'-?\(?\s*-?[\d,]+(?:\.\d+)?\s*\)?'


def _num(tok: str) -> float:
    """Parse one accounting number. Parentheses mean negative."""
    if tok is None:
        return None
    tok = tok.strip()
    if not tok:
        return None
    neg = tok.startswith('(') and tok.endswith(')')
    tok = tok.strip('()').replace(',', '').strip()
    if tok in ('', '-', '—'):
        return None
    try:
        val = float(tok)
    except ValueError:
        return None
    return -val if neg else val


def _lines(text: str) -> list:
    return [ln.strip() for ln in text.split('\n') if ln.strip()]


def _fy(text: str):
    """Leading year of the fiscal year this document covers."""
    m = (re.search(r'FY\s*(\d{4})[–\-—/](\d{2,4})', text)
         or re.search(r'financial year ended 31st March\s+(\d{4})', text, re.I)
         or re.search(r'FISCAL YEAR\s+(\d{4})', text, re.I))
    if not m:
        return None
    year = int(m.group(1))
    # "ended 31st March 2020" is FY2019-20.
    if 'ended 31st March' in m.group(0):
        year -= 1
    return year


def _money_cr(text: str):
    """First 'INR 12.34 Cr' style figure in text, in rupees.

    The unit can wrap onto the next line, and a bare 'INR 0' carries no unit
    at all, so the unit is optional and matched across whitespace.
    """
    m = re.search(r'INR\s*([\d,]+(?:\.\d+)?)\s*(Cr|Crore|Crores|Lakh|Lakhs)?\b',
                  text or '', re.I | re.S)
    if not m:
        return None
    unit = (m.group(2) or '').lower()
    mult = LAKH if unit.startswith('lakh') else (CRORE if unit else 1)
    return int(round(float(m.group(1).replace(',', '')) * mult))


def _iso_date(s: str):
    """Normalise the date renderings these documents use."""
    if not s:
        return None
    s = s.strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    months = {m[:3].lower(): i + 1 for i, m in enumerate(
        ['January', 'February', 'March', 'April', 'May', 'June', 'July',
         'August', 'September', 'October', 'November', 'December'])}
    m = re.match(r'([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})', s)
    if m and m.group(1)[:3].lower() in months:
        return f"{m.group(3)}-{months[m.group(1)[:3].lower()]:02d}-{int(m.group(2)):02d}"
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return None


# ── Financial statements ────────────────────────────────────────────────────

# Section letter -> statement it introduces. Headings change case between
# eras, so only the letter and a keyword are matched.
FS_SECTIONS = [
    (r'^A\.\s+Revenue', 'revenue'),
    (r'^B\.\s+Expenses', 'expenses'),
    (r'^C\.\s+Profit', 'profit'),
    (r'^D\.\s+Balance\s+Sheet', 'balance_sheet'),
    (r'^E\.\s+Notes', 'notes'),
    (r'^F\.', 'other'),
]


def extract_financial_statement(text: str, doc_id: str) -> dict:
    """Extract the line items of a statutory financial statement.

    Layout is 'label, current year, previous year' with each cell on its own
    line, and the previous-year column is absent for some rows (the profit
    block). Values are in Lakhs and are converted to rupees.
    """
    lines = _lines(text)
    unit_lakh = bool(re.search(r'amounts?\s+in\s+Lakhs', text, re.I))
    mult = LAKH if unit_lakh else 1

    items = []
    section = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        for pat, name in FS_SECTIONS:
            if re.match(pat, ln, re.I):
                section = name
                break
        # A label followed by one or two numeric lines is a data row.
        if (section and not re.match(r'^[A-F]\.\s', ln)
                and not re.match(rf'^{NUM}$', ln)
                and ln.lower() not in ('particulars', 'current year', 'previous year')
                and not re.match(r'^(EQUITY AND LIABILITIES|ASSETS)$', ln, re.I)
                and not re.match(r'^Current Year|^Previous Year', ln, re.I)):
            nums = []
            j = i + 1
            while j < len(lines) and len(nums) < 2 and re.match(rf'^{NUM}$', lines[j]):
                nums.append(_num(lines[j]))
                j += 1
            if nums:
                items.append({
                    "section": section,
                    "particulars": ln,
                    "current_year": int(round(nums[0] * mult)) if nums[0] is not None else None,
                    "previous_year": (int(round(nums[1] * mult))
                                      if len(nums) > 1 and nums[1] is not None else None),
                })
                i = j
                continue
        i += 1

    return {
        "_doc_id": doc_id,
        "_doc_type": "financial_statement",
        "fiscal_year": _fy(text),
        "statement_no": (re.search(r'Statement No:\s*(\S+)', text) or [None, None])[1]
                        if re.search(r'Statement No:\s*(\S+)', text) else None,
        "source_unit": "lakh" if unit_lakh else "rupee",
        "line_items": items,
    }


# ── General ledger ──────────────────────────────────────────────────────────

def extract_ledger_book(text: str, doc_id: str) -> dict:
    """Extract ledger postings, grouped under their account headings.

    A posting is a date line followed by a narration that may wrap across
    lines, then the amount columns. Only two of debit/credit/balance are
    present on any given row, and the running balance carries a Dr/Cr marker
    on its own line, so amounts are read positionally from the row's numbers:
    the last number is the balance and the one before it is the movement.
    """
    lines = _lines(text)
    account, account_name = None, None
    entries = []

    i = 0
    while i < len(lines):
        m = re.match(r'ACCOUNT\s+(\d+)\s*[—\-]\s*(.+)$', lines[i], re.I)
        if m:
            account, account_name = m.group(1), m.group(2).strip()
            i += 1
            continue

        if re.match(r'^\d{4}-\d{2}-\d{2}$', lines[i]):
            date = lines[i]
            narration, nums = [], []
            j = i + 1
            while j < len(lines) and not re.match(r'^\d{4}-\d{2}-\d{2}$', lines[j]):
                if re.match(r'^ACCOUNT\s+\d+', lines[j], re.I):
                    break
                if re.match(rf'^{NUM}$', lines[j]):
                    nums.append(_num(lines[j]))
                elif lines[j] in ('Dr', 'Cr'):
                    pass
                elif not re.match(r'^(DOC-|Page \d+|DATE|VOUCHER)', lines[j]):
                    narration.append(lines[j])
                j += 1
            if nums:
                entries.append({
                    "account": account,
                    "account_name": account_name,
                    "date": date,
                    "narration": re.sub(r'\s+', ' ', ' '.join(narration)).strip(),
                    "amount": nums[-2] if len(nums) >= 2 else nums[0],
                    "balance": nums[-1],
                })
            i = j
            continue
        i += 1

    return {
        "_doc_id": doc_id,
        "_doc_type": "general_ledger_book",
        "fiscal_year": _fy(text),
        "entries": entries,
    }


# ── Bank statement ──────────────────────────────────────────────────────────

def extract_bank_statement(text: str, doc_id: str) -> dict:
    """Extract bank transactions.

    Withdrawal and deposit share a row with the running balance and only one
    of the two is filled, so direction is inferred from the balance movement
    rather than from column position, which the text extraction does not
    preserve.
    """
    lines = _lines(text)
    txns = []
    prev_balance = None

    i = 0
    while i < len(lines):
        if re.match(r'^\d{4}-\d{2}-\d{2}$', lines[i]):
            date = lines[i]
            desc, nums = [], []
            j = i + 1
            while j < len(lines) and not re.match(r'^\d{4}-\d{2}-\d{2}$', lines[j]):
                if re.match(rf'^{NUM}$', lines[j]):
                    nums.append(_num(lines[j]))
                elif not re.match(r'^(DOC-|Page \d+)', lines[j]):
                    desc.append(lines[j])
                j += 1
            if nums:
                balance = nums[-1]
                amount = nums[-2] if len(nums) >= 2 else None
                direction = None
                if amount is not None and prev_balance is not None:
                    direction = "deposit" if balance > prev_balance else "withdrawal"
                txns.append({
                    "date": date,
                    "particulars": re.sub(r'\s+', ' ', ' '.join(desc)).strip(),
                    "amount": amount,
                    "direction": direction,
                    "balance": balance,
                })
                prev_balance = balance
            i = j
            continue
        i += 1

    bank = lines[0] if lines else None
    acct = re.search(r'A/c:\s*([\d ]+)', text)
    return {
        "_doc_id": doc_id,
        "_doc_type": "bank_statement",
        "fiscal_year": _fy(text),
        "bank_name": bank,
        "account_no": acct.group(1).strip() if acct else None,
        "transactions": txns,
    }


# ── Running-account bills ───────────────────────────────────────────────────

def _boq_items(text: str) -> list:
    """BOQ rows: item no, description, unit, rate, quantity, amount."""
    lines = _lines(text)
    items = []
    i = 0
    while i < len(lines) - 5:
        if re.match(r'^\d{1,3}$', lines[i]):
            desc = lines[i + 1]
            unit = lines[i + 2]
            if re.match(r'^(cum|MT|rmt|sqm|LS|nos|km|kg|litre)$', unit, re.I):
                rate, qty, amount = (_num(lines[i + 3]), _num(lines[i + 4]),
                                     _num(lines[i + 5]))
                if None not in (rate, qty, amount):
                    items.append({"item_no": int(lines[i]), "description": desc,
                                  "unit": unit, "rate": rate, "quantity": qty,
                                  "amount": amount})
                    i += 6
                    continue
        i += 1
    return items


def _labelled(text: str, label: str, pattern: str = NUM):
    """Value on the line(s) after a label."""
    m = re.search(rf'{label}\s*\n\s*({pattern})', text, re.I)
    return m.group(1).strip() if m else None


def extract_ra_bill(text: str, doc_id: str, doc_type: str = "ra_bill") -> dict:
    """Extract a running-account bill: BOQ detail plus the money summary."""
    contract = re.search(r'Contract\s*#?(\d+)\s*[·|]\s*([^\n·]+)', text)
    bill_no = re.search(r'Bill No:\s*(\S+)', text)
    date = re.search(r'Date:\s*([^\n]+)', text)
    ra_no = re.search(r'RA\s+(\d+)', text)

    awarded = re.search(r'Awarded Value\s*\n\s*(INR[^\n]+)', text, re.I)
    billed = re.search(r'Total Value of Work Billed\s*\n\s*(INR[^\n]+)', text, re.I)
    period = re.search(r'Period\s*\n\s*([A-Za-z]+ \d{1,2}, \d{4})\s*[—\-–]\s*'
                       r'([A-Za-z]+ \d{1,2}, \d{4})', text)

    return {
        "_doc_id": doc_id,
        "_doc_type": doc_type,
        "contract_no": contract.group(1) if contract else None,
        "client_name": contract.group(2).strip() if contract else None,
        "bill_no": bill_no.group(1) if bill_no else None,
        "bill_date": _iso_date(date.group(1)) if date else None,
        "ra_number": int(ra_no.group(1)) if ra_no else None,
        "awarded_value": _money_cr(awarded.group(1)) if awarded else None,
        "total_billed": _money_cr(billed.group(1)) if billed else None,
        "period_start": _iso_date(period.group(1)) if period else None,
        "period_end": _iso_date(period.group(2)) if period else None,
        "value_of_work": _num(_labelled(text, r'Value of work done\s*[—\-–]?\s*this bill')),
        "gst": _num(_labelled(text, r'Add: GST @\s*\d+%')),
        "retention": _num(_labelled(text, r'Less: Retention @\s*[\d.]+%')),
        "net_claimed": _num(_labelled(text, r'Net claimed \(before client TDS\)')),
        "cumulative": _num(_labelled(text, r'Cumulative up to & incl\. RA \d+')),
        "items": _boq_items(text),
    }


def extract_final_ra_bill(text: str, doc_id: str) -> dict:
    return extract_ra_bill(text, doc_id, doc_type="final_ra_bill")


# ── Tender dossier ─────────────────────────────────────────────────────────

def extract_tender_dossier(text: str, doc_id: str) -> dict:
    tender = re.search(r'Tender\s+(RFP-\S+)', text)
    bid = re.search(r'Bid value:\s*(INR[^\n]+)', text, re.I)
    submitted = re.search(r'Submitted:\s*([^\n]+)', text)
    emd = re.search(r'Earnest money of\s*(INR\s*[\d,.]+(?:\s*(?:Cr|Crore|Lakh))?)', text, re.I)
    category = re.search(r'^([A-Za-z &]+?)\s+Works\s+[—\-–]\s+Tender', text, re.M)
    relevant = re.search(r'Past performance\s*[—\-–]\s*(\d+)\s+relevant works', text, re.I)

    # The authority addressed after "To," is the inviting client.
    client = re.search(r'The Tender Inviting Authority,\s*\n\s*([^\n]+)', text)

    return {
        "_doc_id": doc_id,
        "_doc_type": "tender_dossier",
        "tender_ref": tender.group(1) if tender else None,
        "bid_value": _money_cr(bid.group(1)) if bid else None,
        "client_name": client.group(1).strip() if client else None,
        "work_category": category.group(1).strip() if category else None,
        "submitted_date": _iso_date(submitted.group(1)) if submitted else None,
        "earnest_money": _money_cr(emd.group(1)) if emd else (0 if emd else None),
        "relevant_works_cited": int(relevant.group(1)) if relevant else None,
    }


# ── ISO certificates ───────────────────────────────────────────────────────

def extract_iso_certificate(text: str, doc_id: str) -> dict:
    lines = _lines(text)
    cert_no = re.search(r'Certificate No:\s*(\S+)', text)
    standard = re.search(r'(ISO\s*\d+:\d{4})', text)
    initial = re.search(r'Initial Certification Date\s*\n\s*(\S+)', text)
    valid = re.search(r'Valid Until\s*\n\s*(\S+)', text)
    scope = re.search(r'Scope of Registration\s*\n\s*(.+?)(?:\n[A-Z][a-z]|\Z)', text, re.S)

    return {
        "_doc_id": doc_id,
        "_doc_type": "iso_certificate",
        "cert_number": cert_no.group(1) if cert_no else None,
        "cert_type": standard.group(1).replace(' ', '') if standard else None,
        "certification_body": lines[0] if lines else None,
        "valid_from": _iso_date(initial.group(1)) if initial else None,
        "valid_to": _iso_date(valid.group(1)) if valid else None,
        "scope": re.sub(r'\s+', ' ', scope.group(1)).strip() if scope else None,
    }


# ── Compliance matrix ──────────────────────────────────────────────────────

def extract_compliance_matrix(text: str, doc_id: str) -> dict:
    """Extract the checklist rows: requirement, status, evidence."""
    lines = _lines(text)
    tender = re.search(r'Tender\s+(RFP-\S+)\s*[·|]\s*([^\n]+)', text)
    items = []

    i = 0
    while i < len(lines):
        if re.match(r'^\d{1,2}$', lines[i]):
            body, status = [], None
            j = i + 1
            while j < len(lines) and not re.match(r'^\d{1,2}$', lines[j]):
                if lines[j] in ('Complied', 'Not Complied', 'Partially Complied',
                                'Noted', 'N/A'):
                    status = lines[j]
                elif not re.match(r'^(DOC-|Page \d+)', lines[j]):
                    body.append(lines[j])
                j += 1
            if status:
                items.append({"item_no": int(lines[i]),
                              "requirement": re.sub(r'\s+', ' ', ' '.join(body)).strip(),
                              "status": status})
            i = j
            continue
        i += 1

    return {
        "_doc_id": doc_id,
        "_doc_type": "compliance_matrix",
        "tender_ref": tender.group(1) if tender else None,
        "work_category": tender.group(2).strip() if tender else None,
        "items": items,
        "complied_count": sum(1 for x in items if x["status"] == "Complied"),
        "item_count": len(items),
    }


# ── Annual report ──────────────────────────────────────────────────────────

def _rs_amount(s: str):
    """'Rs. 23356.37 Lakh' / 'Rs. -3,90,26,159' -> rupees.

    The unit routinely wraps onto the following line, so it is matched across
    whitespace rather than on the same line.
    """
    if not s:
        return None
    m = re.search(r'Rs\.?\s*(-?[\d,]+(?:\.\d+)?)\s*(Lakh|Lakhs|Cr|Crore)?', s,
                  re.I | re.S)
    if not m:
        return None
    val = float(m.group(1).replace(',', ''))
    unit = (m.group(2) or '').lower()
    if unit.startswith('lakh'):
        val *= LAKH
    elif unit.startswith('cr'):
        val *= CRORE
    return int(round(val))


def extract_annual_report(text: str, doc_id: str) -> dict:
    """Extract the annual report's headline figures and its registers."""
    lines = _lines(text)

    def after(label, count=2):
        m = re.search(rf'{label}\s*\n((?:[^\n]*\n){{0,{count}}})', text, re.I)
        return m.group(1).split('\n') if m else []

    highlights = {}
    for label, key in [(r'Gross billings', 'gross_billings'),
                       (r'Net revenue from operations', 'net_revenue'),
                       (r'Profit for the year', 'profit')]:
        vals = [_rs_amount(v) for v in after(label) if _rs_amount(v) is not None]
        if vals:
            highlights[key] = vals[0]
            if len(vals) > 1:
                highlights[key + "_prior"] = vals[1]

    contracts = re.search(r'(\d+)\s+contracts remained in execution', text)
    # The Lakh unit sits on the line after the figure, so the capture has to
    # cross the newline and stop at the parenthetical instead.
    order_book = re.search(r'aggregate awarded value of\s*(Rs\.[^(]{0,40})', text, re.S)
    variations = re.search(r'approved variations of\s*(Rs\.[^)]{0,40}?)\s*across\s*(\d+)\s*variation',
                           text, re.S)

    # Segment revenue table: category then two year columns.
    segments = []
    m = re.search(r'SEGMENT REVENUE[^\n]*\n(.*?)(?:QUARTERLY|CLIENT CONCENTRATION|\Z)',
                  text, re.S | re.I)
    if m:
        seg_lines = _lines(m.group(1))
        for i, ln in enumerate(seg_lines[:-1]):
            if (not re.match(rf'^{NUM}$', ln) and i + 1 < len(seg_lines)
                    and re.match(rf'^{NUM}$', seg_lines[i + 1])
                    and not ln.upper().startswith(('THE ', 'FY', 'PARTICULARS'))):
                prior = (_num(seg_lines[i + 2])
                         if i + 2 < len(seg_lines) and re.match(rf'^{NUM}$', seg_lines[i + 2])
                         else None)
                segments.append({"segment": ln,
                                 "revenue": _num(seg_lines[i + 1]),
                                 "revenue_prior": prior})

    return {
        "_doc_id": doc_id,
        "_doc_type": "annual_report",
        "fiscal_year": _fy(text),
        **highlights,
        "contracts_in_execution": int(contracts.group(1)) if contracts else None,
        "order_book_value": _rs_amount(order_book.group(1)) if order_book else None,
        "variation_value": _rs_amount(variations.group(1)) if variations else None,
        "variation_orders": int(variations.group(2)) if variations else None,
        "segment_revenue": segments,
    }
