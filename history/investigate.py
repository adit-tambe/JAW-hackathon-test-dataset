"""Investigate missing Jal Nigam work and client count."""
import sqlite3, sys, json
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('data/company.db')

print('=== Jal Nigam / Jharkhand works ===')
for row in conn.execute('''
    SELECT w.project_name, w.contract_value, c.client_name
    FROM works w JOIN clients c ON w.client_id = c.client_id
    WHERE c.client_name LIKE '%Jal%' OR c.client_name LIKE '%Jharkhand%'
'''):
    print(f'  {row[2]} | {row[0]} | {row[1]}')

print('\n=== All works with val near 69200000 ===')
for row in conn.execute('''
    SELECT w.project_name, w.contract_value, c.client_name
    FROM works w JOIN clients c ON w.client_id = c.client_id
    WHERE w.contract_value BETWEEN 60000000 AND 80000000
'''):
    print(f'  {row[2]} | {row[0]} | {row[1]}')

print('\n=== ALL 28 CLIENTS ===')
for row in conn.execute('SELECT client_name FROM clients ORDER BY client_name'):
    print(f'  {row[0]}')

# Check a few extracted JSONs for Jharkhand
for cc in ['DOC-CC-010', 'DOC-CC-012', 'DOC-CC-015']:
    try:
        j = json.load(open(f'data/extracted/{cc}.json', encoding='utf-8'))
        cn = j.get('client_name', '?')
        cv = j.get('contract_value', '?')
        pn = j.get('project_name', '?')
        print(f'\n{cc}: client=[{cn}] val=[{cv}] proj=[{pn}]')
    except:
        pass

# Find the 3rd Jal Nigam work
print('\n=== Searching for 69200000 in extracted JSONs ===')
from pathlib import Path
for jf in sorted(Path('data/extracted').glob('DOC-CC-*.json')):
    d = json.load(open(jf, encoding='utf-8'))
    cv = d.get('contract_value')
    cn = d.get('client_name', '')
    if cv == 69200000 or (isinstance(cn, str) and 'jal nigam' in cn.lower()):
        print(f'  {jf.stem}: client=[{cn}] val=[{cv}] proj=[{d.get("project_name")}]')
