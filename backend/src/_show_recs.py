"""Show portfolio recommendations summary."""
import sys, json, os
sys.stdout = open(sys.stdout.fileno(), 'w', encoding='utf-8', closefd=False)
sys.path.insert(0, r'E:\DEV\opensource_contrib\PTCK_VNSTOCK\backend')
import src.config

p = os.path.join(src.config.DATA_DIR, 'output', 'portfolio_recommendations.json')
with open(p, 'r', encoding='utf-8') as f:
    d = json.load(f)

print(f"Date: {d.get('date','')}")
print(f"Regime: {d.get('regime',{}).get('status','')}\n")

recs = d.get('recommendations', {})
print('Tiers found:', list(recs.keys()))
print()

for tier, items in recs.items():
    print(f'=== {tier.upper()} ({len(items)} picks) ===')
    for it in items[:8]:
        sym = it['symbol']
        tier_vn = it.get('tier_vn', '')
        conv = it.get('conviction', 0)
        entry = it.get('entry_suggestion', '')
        sector = it.get('sector', '')
        print(f'  {sym:6s} | {conv:5.1f} | {sector:6s} | {tier_vn:20s} | {entry}')
    if len(items) > 8:
        print(f'  ... + {len(items)-8} more')
    print()
