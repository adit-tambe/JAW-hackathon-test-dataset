import json

with open('data/extracted/WB-Trial_Balance.json', 'r', encoding='utf-8') as f:
    d = json.load(f)

for sname, sdata in d['sheets'].items():
    print('Sheet:', sname, 'rows:', sdata.get('row_count'))
    print('  Headers:', sdata.get('headers'))
    if sdata.get('data'):
        print('  Row 0:', sdata['data'][0])
        print('  Row 1:', sdata['data'][1] if len(sdata['data']) > 1 else None)
