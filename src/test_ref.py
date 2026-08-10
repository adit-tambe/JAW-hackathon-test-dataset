import fitz, re, sys
from pathlib import Path

ref_dir = Path('documents/reference_letter')
missing = 0
for pdf_file in ref_dir.glob('*.pdf'):
    doc = fitz.open(pdf_file)
    raw_text = '\n'.join(page.get_text() for page in doc)
    text = re.sub(r'\s+', ' ', raw_text)
    
    proj_m = re.search(r'Project Name\s*\n\s*([^\n]+)', raw_text) or \
             re.search(r'Work Executed\s*:?\s*([^\(]+?)(?=Value|Date|Completed|Contact|\Z)', text, re.IGNORECASE) or \
             re.search(r'Subject:.*?[“"]([^”"]+)[”"]', text) or \
             re.search(r'work\s+[“"]([^”"]+)[”"]', text) or \
             re.search(r'for the work\s+[“"]?([^”"]+?)[”"]?\s*\(INR', text, re.IGNORECASE)
             
    proj = proj_m.group(1).strip() if proj_m else None
    if not proj:
        missing += 1
        print('Missing:', pdf_file.name)

print(f'Total REF missing project_name: {missing}')
