import glob, json

for p in sorted(glob.glob('data/extracted/DOC-BANK-*.json'))[:3]:
    with open(p, 'r', encoding='utf-8') as f:
        print(p, json.load(f))
