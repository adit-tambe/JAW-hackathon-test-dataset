"""
money.py — Indian currency parser.

Handles all formats found in the BITS Hackathon corpus:
  - INR 33.38 Cr / Crore / Crores        → 333800000
  - 3,338.00 Lakh / Lakhs                → 333800000
  - 33,38,00,000 (Indian digit grouping) → 333800000
  - 333800000 (raw integer)              → 333800000
  - Rs. / ₹ prefixed values              → stripped and parsed
  - Word-form: "seventy-three crore"      → 730000000

The BRIEFING says: "Reading money back out is a parsing problem with a
correct answer, not an approximation you have to tolerate."
Every conversion here must be LOSSLESS.
"""
import re
from typing import Optional


# ── Word-to-number mapping ──────────────────────────────────────────────────
# Questions use prose like "seventy-three crore mark"
ONES = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
    'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13,
    'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
    'eighteen': 18, 'nineteen': 19,
}
TENS = {
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
}
SCALES = {
    'hundred': 100,
    'thousand': 1_000,
    'lakh': 100_000, 'lakhs': 100_000,
    'crore': 10_000_000, 'crores': 10_000_000,
    'million': 1_000_000,
    'billion': 1_000_000_000,
}


def _words_to_number(text: str) -> Optional[float]:
    """
    Convert number words to a numeric value.
    Handles patterns like "seventy-three crore" or "six crore".
    Returns None if the text doesn't contain recognizable number words.
    """
    text = text.lower().strip()
    # Replace hyphens with spaces for compound numbers like "seventy-three"
    text = text.replace('-', ' ')
    words = text.split()
    
    if not words:
        return None
    
    # Quick check: if text contains any digits, it's not a word-form number
    if any(c.isdigit() for c in text):
        return None
    
    current = 0
    result = 0
    found_number = False
    scale_applied = False
    
    for word in words:
        if word in ONES:
            current += ONES[word]
            found_number = True
        elif word in TENS:
            current += TENS[word]
            found_number = True
        elif word == 'hundred':
            current *= 100
            found_number = True
        elif word in ('thousand', 'lakh', 'lakhs', 'crore', 'crores',
                       'million', 'billion'):
            if current == 0:
                current = 1
            current *= SCALES[word]
            result += current
            current = 0
            found_number = True
            scale_applied = True
        elif word == 'and':
            continue
        # Skip non-number words
    
    result += current
    
    if not found_number:
        return None
    return float(result)


def _strip_currency_prefix(text: str) -> str:
    """Remove currency symbols and prefixes like INR, Rs., ₹, Rs"""
    text = text.strip()
    # Remove common prefixes (case insensitive)
    text = re.sub(r'^(INR|Rs\.?|₹)\s*', '', text, flags=re.IGNORECASE)
    return text.strip()


def _is_indian_grouped(text: str) -> bool:
    """
    Check if a string uses Indian digit grouping: X,XX,XX,XXX
    Indian grouping: rightmost group is 3 digits, all others are 2 digits.
    Examples: 33,38,00,000  or  1,23,45,678
    """
    return bool(re.match(r'^\d{1,2}(,\d{2})*(,\d{3})$', text))


def parse_indian_money(text: str) -> Optional[int]:
    """
    Parse an Indian monetary string into a raw integer (rupees).
    
    Returns None if the text cannot be parsed as a monetary value.
    Returns an integer for exact values.
    
    Examples:
        parse_indian_money("INR 33.38 Cr")      → 333800000
        parse_indian_money("3,338.00 Lakh")      → 333800000
        parse_indian_money("33,38,00,000")        → 333800000
        parse_indian_money("333800000")           → 333800000
        parse_indian_money("₹33.38 Crore")        → 333800000
        parse_indian_money("Rs. 33,38,00,000")    → 333800000
    """
    if text is None:
        return None
    
    text = str(text).strip()
    if not text or text.lower() in ('nil', 'na', 'n/a', '-', '—', ''):
        return None
    
    # Strip currency prefix and trailing /- or dashes
    text = _strip_currency_prefix(text)
    text = re.sub(r'[\/\-]', '', text).strip()
    
    if not text:
        return None
    
    # Try word-form first: "seventy-three crore"
    word_result = _words_to_number(text)
    if word_result is not None:
        return int(round(word_result))
    
    # Detect multiplier suffix (Cr/Crore/Lakh/Lakhs)
    multiplier = 1
    suffix_match = re.search(
        r'(Cr(?:ore)?s?|Lakh?s?)\s*$', text, re.IGNORECASE
    )
    if suffix_match:
        suffix = suffix_match.group(1).lower()
        if suffix.startswith('cr'):
            multiplier = 10_000_000  # 1 Crore = 10 million
        elif suffix.startswith('lakh') or suffix.startswith('lac'):
            multiplier = 100_000     # 1 Lakh = 100 thousand
        text = text[:suffix_match.start()].strip()
    
    # Remove any remaining non-numeric characters except digits, commas, dots, minus
    # But first check for Indian digit grouping
    clean = text.strip()
    
    if _is_indian_grouped(clean):
        # Indian grouping: remove commas and parse as integer
        value = int(clean.replace(',', ''))
        return int(round(value * multiplier))
    
    # Remove Western-style commas (1,000,000)
    clean = clean.replace(',', '')
    
    # Try to parse as a number
    try:
        value = float(clean)
        result = value * multiplier
        return int(round(result))
    except ValueError:
        return None


def format_as_answer(value: float) -> float:
    """
    Format a numeric value for submission.
    - Integer values → return as int
    - Percentage values → return rounded to 2 decimal places
    """
    if value == int(value):
        return int(value)
    return round(value, 2)


# ── Self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_cases = [
        ("INR 33.38 Cr", 333800000),
        ("3,338.00 Lakh", 333800000),
        ("33,38,00,000", 333800000),
        ("333800000", 333800000),
        ("Rs.33.38 Crore", 333800000),
        ("Rs. 33,38,00,000", 333800000),
        ("INR 1.34 Cr", 13400000),
        ("INR 73.02 Cr", 730200000),
        ("81.44 Cr", 814400000),
        ("6.92 Cr", 69200000),
        ("INR 2.33 Cr", 23300000),
        ("1,34,00,000", 13400000),
        ("seventy-three crore", 730000000),
        ("six crore", 60000000),
        ("twenty crore", 200000000),
        ("Nil", None),
        ("", None),
        (None, None),
        ("193299999", 193299999),
        ("INR 19.33 Cr", 193300000),  # Close to but not exactly 193299999
    ]
    
    passed = 0
    failed = 0
    for input_val, expected in test_cases:
        result = parse_indian_money(input_val)
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            print(f"  {status} parse_indian_money({input_val!r}) = {result}, expected {expected}")
            failed += 1
        else:
            print(f"  {status} parse_indian_money({input_val!r}) = {result}")
            passed += 1
    
    print(f"\n{passed}/{passed+failed} tests passed")
