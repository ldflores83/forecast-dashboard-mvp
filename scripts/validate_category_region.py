"""
validate_category_region.py
Quick validation of Category and Opp_Owner_Region fields
after re-running sf_export_dashboard.py with new fields.

Run from project root:
    python scripts/validate_category_region.py
"""

import os
import pandas as pd

CSV = "dashboard_export.csv"

if not os.path.exists(CSV):
    print(f"ERROR: {CSV} not found. Run sf_export_dashboard.py first.")
    exit(1)

df = pd.read_csv(CSV)
print("=" * 65)
print("CATEGORY + REGION FIELD VALIDATION")
print("=" * 65)

# ── FIELD CHECK ───────────────────────────────────────────────────────────────
print("\n[1/5] Field presence check:")
for field in ['Category', 'Opp_Owner_Region']:
    present = field in df.columns
    pop     = df[field].notna().sum() if present else 0
    pct     = pop/len(df)*100 if present else 0
    print(f"  {'✓' if present else '✗'} {field}: {pop:,}/{len(df):,} populated ({pct:.1f}%)")

if 'Category' not in df.columns or 'Opp_Owner_Region' not in df.columns:
    print("\n  STOP: Re-run sf_export_dashboard.py first to get new fields.")
    exit(1)

# ── CATEGORY DISTRIBUTION ─────────────────────────────────────────────────────
print("\n[2/5] Category distribution (all opps):")
print(df['Category'].value_counts(dropna=False).to_string())

# ── CATEGORY BY SALES MOTION ──────────────────────────────────────────────────
print("\n[3/5] Category breakdown for Sales motions (Net New / Expansion / Migration):")
EXCL = ['Combined','Credited','Closed-Duplicate','Junk']
sales = df[
    df['Sales_Motion'].isin(['Net New','Expansion','Migration']) &
    df['BU'].isin(['ERP BU','Supply Chain BU','Redzone BU']) &
    ~df['Substage'].isin(EXCL) &
    ~df['Name'].str.contains('Amendment', case=False, na=False)
]

cat_summary = sales.groupby('Category').agg(
    opps   = ('Id','count'),
    acv    = ('ACV','sum')
).sort_values('acv', ascending=False)
cat_summary['acv_fmt'] = cat_summary['acv'].apply(lambda x: f"${x:,.0f}")
print(cat_summary[['opps','acv_fmt']].to_string())

print()
solutions = sales[sales['Category'] == 'Solutions']
services  = sales[sales['Category'] == 'Services']
null_cat  = sales[sales['Category'].isna()]
print(f"  Solutions only: {len(solutions):,} opps | ${solutions['ACV'].sum():,.0f}")
print(f"  Services only:  {len(services):,} opps  | ${services['ACV'].sum():,.0f}")
print(f"  Null/Other:     {len(null_cat):,} opps  | ${null_cat['ACV'].sum():,.0f}")

# ── IMPACT ON KEY METRICS ──────────────────────────────────────────────────────
print("\n[4/5] Impact on key metrics (ERP + SC + Redzone, clean filter):")
clean = df[
    df['BU'].isin(['ERP BU','Supply Chain BU','Redzone BU']) &
    ~df['Substage'].isin(EXCL) &
    ~df['Name'].str.contains('Amendment', case=False, na=False) &
    ~df['Name'].str.contains('zzz', case=False, na=False)
]

won = clean[clean['Is_Won'] == True]
lost_ren = clean[(clean['Sales_Motion']=='Renewal') & (clean['Is_Lost']==True) & (clean['ATR_Value']>0)]

SALES_MOTIONS = ['Net New','Expansion','Migration']

# Before filter (all categories)
sales_won_all = won[won['Sales_Motion'].isin(SALES_MOTIONS)]['ACV'].sum()
churn         = lost_ren['ATR_Value'].sum()

# After filter (Solutions only)
sales_won_sol = won[
    won['Sales_Motion'].isin(SALES_MOTIONS) &
    (won['Category'] == 'Solutions')
]['ACV'].sum()

print(f"  {'Metric':<30} {'Before':>12} {'After (Solutions)':>18} {'Delta':>12}")
print(f"  {'-'*75}")
print(f"  {'Sales New Revenue':<30} ${sales_won_all:>11,.0f} ${sales_won_sol:>17,.0f} ${sales_won_sol-sales_won_all:>11,.0f}")
print(f"  {'Churn (ATR lost)':<30} ${churn:>11,.0f} {'(unchanged)':>18} {'—':>12}")
cov_before = sales_won_all/churn*100 if churn else 0
cov_after  = sales_won_sol/churn*100 if churn else 0
print(f"  {'Sales Coverage %':<30} {cov_before:>11.1f}% {cov_after:>17.1f}% {cov_after-cov_before:>+11.1f}%")

# By BU
print()
print("  By BU:")
for bu in ['ERP BU','Supply Chain BU','Redzone BU']:
    bu_won = won[won['BU']==bu]
    s_all  = bu_won[bu_won['Sales_Motion'].isin(SALES_MOTIONS)]['ACV'].sum()
    s_sol  = bu_won[bu_won['Sales_Motion'].isin(SALES_MOTIONS) & (bu_won['Category']=='Solutions')]['ACV'].sum()
    print(f"    {bu:<22}: ${s_all:>10,.0f} → ${s_sol:>10,.0f}  (delta ${s_sol-s_all:>10,.0f})")

# ── REGION DISTRIBUTION ───────────────────────────────────────────────────────
print("\n[5/5] Opp_Owner_Region distribution (all Sales motions, Solutions only, won):")
region_won = won[
    won['Sales_Motion'].isin(SALES_MOTIONS) &
    (won['Category'] == 'Solutions')
]
region_summary = region_won.groupby('Opp_Owner_Region').agg(
    opps = ('Id','count'),
    acv  = ('ACV','sum')
).sort_values('acv', ascending=False)
region_summary['acv_fmt'] = region_summary['acv'].apply(lambda x: f"${x:,.0f}")
print(region_summary[['opps','acv_fmt']].to_string())

# ── CHANNEL vs DIRECT ─────────────────────────────────────────────────────────
print("\n[6/6] Channel vs Direct split (Solutions won, by BU):")
sol_won = won[
    won['Sales_Motion'].isin(SALES_MOTIONS) &
    (won['Category'] == 'Solutions')
]
for bu in ['ERP BU', 'Supply Chain BU', 'Redzone BU']:
    bu_df   = sol_won[sol_won['BU'] == bu]
    direct  = bu_df[bu_df['Is_Channel'] != True]
    channel = bu_df[bu_df['Is_Channel'] == True]
    total   = bu_df['ACV'].sum()
    print(f"  {bu}:")
    print(f"    Direct:  {len(direct):>3} opps | ${direct['ACV'].sum():>10,.0f}")
    print(f"    Channel: {len(channel):>3} opps | ${channel['ACV'].sum():>10,.0f}")
    print(f"    Total:                  ${total:>10,.0f}")

print()
print("=" * 65)
print("VALIDATION COMPLETE")
print("If numbers look reasonable → run setup_views.py + deploy.ps1")
print("=" * 65)