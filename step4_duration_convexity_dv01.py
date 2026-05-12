# ============================================================
#  UK GILT FIXED INCOME PROJECT — STEP 4: DURATION, CONVEXITY & DV01
#  Author: Mrigang Sharma
#
#  What this calculates for each Gilt:
#    1. Macaulay Duration   — weighted average time to cashflows (years)
#    2. Modified Duration   — % price change per 1% yield move
#    3. Convexity           — curvature correction to duration
#    4. DV01 / BPV          — £ price change per 1 basis point (0.01%)
#
#  Then aggregates everything to PORTFOLIO level.
# ============================================================

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.optimize import brentq
from datetime import date
import warnings
warnings.filterwarnings("ignore")

print("=" * 65)
print("  STEP 4: DURATION, CONVEXITY & DV01")
print("=" * 65)

# ============================================================
# SECTION A — LOAD YIELD CURVE (same as Step 2)
# ============================================================

BOE_FILE = "GLC Nominal daily data current month.xlsx"

print(f"\n📂 Loading yield curve from: {BOE_FILE}")

try:
    raw = pd.read_excel(BOE_FILE, sheet_name="4. spot curve", header=None)

    maturity_row = pd.to_numeric(raw.iloc[3, 1:].values, errors="coerce")
    maturity_row = maturity_row[~np.isnan(maturity_row)]

    data_rows = raw.iloc[5:, :].copy()
    n = len(maturity_row)
    extra_cols = list(range(n + 1, len(data_rows.columns)))
    data_rows.columns = ["Date"] + list(maturity_row) + extra_cols
    maturity_cols = list(maturity_row)
    data_rows = data_rows[["Date"] + maturity_cols].copy()
    data_rows["Date"] = pd.to_datetime(data_rows["Date"], errors="coerce")
    data_rows = data_rows.dropna(subset=["Date"])
    for col in maturity_cols:
        data_rows[col] = pd.to_numeric(data_rows[col], errors="coerce")
    data_rows = data_rows.sort_values("Date").reset_index(drop=True)
    data_rows = data_rows.dropna(subset=maturity_cols, how="all")
    data_rows = data_rows.set_index("Date")

    latest_row  = data_rows.dropna(how="all").iloc[-1]
    latest_date = latest_row.name.strftime("%d %b %Y")
    print(f"✅ Yield curve loaded — {latest_date}\n")

except FileNotFoundError:
    print(f"❌ File not found: {BOE_FILE}")
    exit()


# ============================================================
# SECTION B — CORE FUNCTIONS
# ============================================================

def get_yield(maturity_years, yield_curve):
    available = yield_curve.dropna()
    mats = np.array(available.index.astype(float))
    ylds = np.array(available.values)
    return float(np.interp(maturity_years, mats, ylds))


def get_cashflows(face, coupon_rate, settlement, maturity, freq=2):
    cashflows = []
    months_between = 12 // freq
    coupon_date = maturity
    while coupon_date > settlement:
        cf = face * (coupon_rate / 100) / freq
        cashflows.append((coupon_date, cf))
        month = coupon_date.month - months_between
        year  = coupon_date.year
        if month <= 0:
            month += 12
            year  -= 1
        try:
            coupon_date = coupon_date.replace(year=year, month=month)
        except ValueError:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            coupon_date = coupon_date.replace(year=year, month=month,
                                              day=min(coupon_date.day, last_day))
    if cashflows:
        last_date, last_cf = cashflows[0]
        cashflows[0] = (last_date, last_cf + face)
    return sorted(cashflows)


def price_bond(face, coupon_rate, ytm_pct, settlement, maturity, freq=2):
    ytm = ytm_pct / 100
    cashflows = get_cashflows(face, coupon_rate, settlement, maturity, freq)
    dirty_price = 0.0
    for cf_date, cf_amount in cashflows:
        t = (cf_date - settlement).days / 365.25
        if t > 0:
            dirty_price += cf_amount / (1 + ytm / freq) ** (t * freq)
    return dirty_price


# ============================================================
# SECTION C — DURATION, CONVEXITY, DV01 FUNCTIONS
# ============================================================

def macaulay_duration(face, coupon_rate, ytm_pct, settlement, maturity, freq=2):
    """
    Macaulay Duration — plain English:
    The weighted average time (in years) you wait to receive
    the bond's cashflows. Each cashflow is weighted by its
    present value as a share of total bond price.

    A bond paying £2 every 6 months for 5 years + £100 at end:
    The £100 at year 5 dominates → duration close to 5 years.
    A bond with higher coupons → duration pulled toward shorter
    maturities because more value arrives earlier.
    """
    ytm = ytm_pct / 100
    cashflows = get_cashflows(face, coupon_rate, settlement, maturity, freq)
    dirty_price = price_bond(face, coupon_rate, ytm_pct, settlement, maturity, freq)

    weighted_time = 0.0
    for cf_date, cf_amount in cashflows:
        t = (cf_date - settlement).days / 365.25
        if t > 0:
            pv_cf = cf_amount / (1 + ytm / freq) ** (t * freq)
            weighted_time += t * (pv_cf / dirty_price)

    return weighted_time


def modified_duration(face, coupon_rate, ytm_pct, settlement, maturity, freq=2):
    """
    Modified Duration — plain English:
    If yields go up by 1%, how much does the bond price fall (in %)?

    Modified Duration = Macaulay Duration / (1 + ytm/freq)

    Example: Modified Duration of 4.5 means:
    → Yields up 1%  → Price falls ~4.5%
    → Yields down 1% → Price rises ~4.5%

    This is the NUMBER every fixed income trader uses daily
    to measure interest rate risk.
    """
    mac_dur = macaulay_duration(face, coupon_rate, ytm_pct,
                                settlement, maturity, freq)
    ytm = ytm_pct / 100
    return mac_dur / (1 + ytm / freq)


def convexity(face, coupon_rate, ytm_pct, settlement, maturity, freq=2):
    """
    Convexity — plain English:
    Duration assumes the price/yield relationship is a straight line.
    It isn't — it's a curve. Convexity measures that curvature.

    Why it matters:
    If yields rise 2%, duration alone OVERESTIMATES the price fall.
    Convexity corrects this. Higher convexity = better for the investor
    because the bond rises MORE than duration predicts when yields fall,
    and falls LESS than duration predicts when yields rise.

    Full price change formula:
    ΔP/P ≈ -ModDur × Δy + 0.5 × Convexity × (Δy)²
    """
    ytm = ytm_pct / 100
    cashflows = get_cashflows(face, coupon_rate, settlement, maturity, freq)
    dirty_price = price_bond(face, coupon_rate, ytm_pct, settlement, maturity, freq)

    conv = 0.0
    for cf_date, cf_amount in cashflows:
        t = (cf_date - settlement).days / 365.25
        if t > 0:
            pv_cf = cf_amount / (1 + ytm / freq) ** (t * freq)
            conv += t * (t + 1/freq) * (pv_cf / dirty_price)

    return conv / (1 + ytm / freq) ** 2


def dv01(face, coupon_rate, ytm_pct, settlement, maturity, freq=2):
    """
    DV01 / BPV (Dollar/Pound Value of 01) — plain English:
    How much does the bond price change if yields move by
    just 1 basis point (0.01%)?

    We calculate this by bumping the yield up 1bp and down 1bp
    and taking the average price change. This is called
    'central difference' and is more accurate than a one-sided bump.

    Example: DV01 = £0.042 means:
    → Every 1bp yield move = £0.042 price change per £100 face value
    → For a £10 million position: 1bp move = £4,200 gain/loss
    """
    price_up   = price_bond(face, coupon_rate, ytm_pct + 0.01,
                            settlement, maturity, freq)
    price_down = price_bond(face, coupon_rate, ytm_pct - 0.01,
                            settlement, maturity, freq)
    return (price_down - price_up) / 2


def price_change_estimate(mod_dur, conv, ytm_pct, delta_bps):
    """
    Estimates % price change for a given yield shock.
    Uses both Modified Duration AND Convexity for accuracy.

    ΔP/P ≈ -ModDur × Δy + 0.5 × Convexity × (Δy)²

    Without convexity (duration only) = straight line approximation
    With convexity = curved, more accurate
    """
    delta_y = delta_bps / 10000
    duration_effect   = -mod_dur * delta_y * 100
    convexity_effect  = 0.5 * conv * (delta_y ** 2) * 100
    return duration_effect + convexity_effect


# ============================================================
# SECTION D — GILT BASKET
# ============================================================

TODAY = date.today()
FACE  = 100.0

GILTS = [
    ("4.50% Treasury 2026",  4.50,  date(2026,  9,  7)),
    ("4.25% Treasury 2027",  4.25,  date(2027,  6,  7)),
    ("0.875% Treasury 2029", 0.875, date(2029, 10, 22)),
    ("4.00% Treasury 2031",  4.00,  date(2031,  1, 22)),
    ("4.25% Treasury 2032",  4.25,  date(2032,  6,  7)),
    ("4.50% Treasury 2034",  4.50,  date(2034,  9,  7)),
    ("4.25% Treasury 2036",  4.25,  date(2036,  6,  7)),
    ("1.25% Treasury 2041",  1.25,  date(2041,  7, 22)),
    ("4.50% Treasury 2042",  4.50,  date(2042,  9,  7)),
    ("3.50% Treasury 2045",  3.50,  date(2045,  1, 22)),
    ("3.75% Treasury 2052",  3.75,  date(2052,  7, 22)),
]


# ============================================================
# SECTION E — CALCULATE EVERYTHING
# ============================================================

print("─" * 65)
print("  DURATION, CONVEXITY & DV01 — ALL GILTS")
print(f"  Settlement: {TODAY.strftime('%d %b %Y')}")
print("─" * 65)

results = []

for name, coupon, maturity in GILTS:
    if maturity <= TODAY:
        continue

    yrs = (maturity - TODAY).days / 365.25
    ytm = get_yield(yrs, latest_row)

    price  = price_bond(FACE, coupon, ytm, TODAY, maturity)
    mac    = macaulay_duration(FACE, coupon, ytm, TODAY, maturity)
    mod    = modified_duration(FACE, coupon, ytm, TODAY, maturity)
    conv   = convexity(FACE, coupon, ytm, TODAY, maturity)
    dv     = dv01(FACE, coupon, ytm, TODAY, maturity)

    # Price change estimates for different rate shocks
    chg_plus50  = price_change_estimate(mod, conv, ytm, +50)
    chg_plus100 = price_change_estimate(mod, conv, ytm, +100)
    chg_plus200 = price_change_estimate(mod, conv, ytm, +200)
    chg_min100  = price_change_estimate(mod, conv, ytm, -100)

    results.append({
        "Name":           name,
        "Years Left":     round(yrs, 1),
        "YTM (%)":        round(ytm, 3),
        "Clean Price":    round(price, 2),
        "Mac Duration":   round(mac, 3),
        "Mod Duration":   round(mod, 3),
        "Convexity":      round(conv, 3),
        "DV01 (£)":       round(dv, 4),
        "+50bps (%)":     round(chg_plus50, 2),
        "+100bps (%)":    round(chg_plus100, 2),
        "+200bps (%)":    round(chg_plus200, 2),
        "-100bps (%)":    round(chg_min100, 2),
    })

df = pd.DataFrame(results)


# ============================================================
# SECTION F — PRINT RESULTS
# ============================================================

print("\n📊 RISK METRICS TABLE:")
print(df[["Name", "Years Left", "YTM (%)", "Clean Price",
          "Mac Duration", "Mod Duration", "Convexity", "DV01 (£)"]
         ].to_string(index=False))

print("\n📊 RATE SHOCK SCENARIOS (% price change):")
print(df[["Name", "Mod Duration", "+50bps (%)",
          "+100bps (%)", "+200bps (%)", "-100bps (%)"]
         ].to_string(index=False))


# ============================================================
# SECTION G — DEEP DIVE: 10YR GILT EXPLAINED
# ============================================================

print("\n" + "=" * 65)
print("  DEEP DIVE — 4.25% Treasury 2036 (~10yr Benchmark)")
print("=" * 65)

r = next(x for x in results if "2036" in x["Name"])

print(f"""
  Modified Duration : {r['Mod Duration']}
  Plain English     : If yields rise 1%, price falls ~{r['Mod Duration']:.2f}%
                      If yields fall 1%, price rises ~{r['Mod Duration']:.2f}%

  Convexity         : {r['Convexity']}
  Plain English     : The price/yield curve bends in your favour.
                      A +2% shock actually falls LESS than duration predicts.
                      A -2% shock actually rises MORE than duration predicts.

  DV01              : £{r['DV01 (£)']}
  Plain English     : Every 1bp (0.01%) yield move = £{r['DV01 (£)']:.4f} price change
                      On £10 million position = £{r['DV01 (£)']*100000:,.0f} per basis point

  Rate Shock Summary:
  ┌─────────────────┬──────────────────────────────────┐
  │ Yield Change    │ Estimated Price Change           │
  ├─────────────────┼──────────────────────────────────┤
  │ +50 bps         │ {r['+50bps (%)']:+.2f}%                            │
  │ +100 bps        │ {r['+100bps (%)']:+.2f}%                            │
  │ +200 bps        │ {r['+200bps (%)']:+.2f}%                            │
  │ -100 bps        │ {r['-100bps (%)']:+.2f}%                            │
  └─────────────────┴──────────────────────────────────┘
""")


# ============================================================
# SECTION H — PORTFOLIO LEVEL ANALYTICS
# ============================================================

print("=" * 65)
print("  PORTFOLIO LEVEL — EQUAL WEIGHTED (£1M each)")
print("=" * 65)

position_size = 1_000_000  # £1M per gilt

portfolio_rows = []
total_dv01 = 0
total_value = 0

for r in results:
    units = position_size / r["Clean Price"]
    pos_dv01 = units * r["DV01 (£)"]
    pos_value = units * r["Clean Price"]
    total_dv01  += pos_dv01
    total_value += pos_value
    portfolio_rows.append({
        "Name":       r["Name"],
        "Units":      round(units, 1),
        "Value (£)":  round(pos_value, 0),
        "Pos DV01":   round(pos_dv01, 2),
    })

# Portfolio Modified Duration = weighted average
weights = [r["Value (£)"] / total_value for r in portfolio_rows]
port_mod_dur = sum(w * results[i]["Mod Duration"]
                   for i, w in enumerate(weights))
port_convexity = sum(w * results[i]["Convexity"]
                     for i, w in enumerate(weights))

print(f"""
  Total Portfolio Value : £{total_value:>12,.0f}
  Portfolio Mod Duration: {port_mod_dur:.3f} years
  Portfolio Convexity   : {port_convexity:.3f}
  Total Portfolio DV01  : £{total_dv01:,.2f} per basis point

  Plain English:
  → A 1bp yield rise across the curve = £{total_dv01:,.0f} portfolio loss
  → A 100bp (1%) yield rise           = £{total_dv01*100:,.0f} portfolio loss
  → A 100bp (1%) yield fall           = £{total_dv01*100:,.0f} portfolio gain
""")


# ============================================================
# SECTION I — VISUALISATION
# ============================================================

print("📊 Generating charts...")

fig = plt.figure(figsize=(16, 12))
fig.suptitle(
    f"UK Gilt Risk Analytics — Step 4: Duration, Convexity & DV01\n"
    f"Settlement: {TODAY.strftime('%d %b %Y')} | Source: Bank of England",
    fontsize=13, fontweight="bold", y=0.98
)

gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.38)

# ── Chart 1: Modified Duration by Gilt
ax1 = fig.add_subplot(gs[0, :2])
colours = plt.cm.Blues(np.linspace(0.4, 0.9, len(df)))
bars = ax1.barh(df["Name"], df["Mod Duration"],
                color=colours, edgecolor="white", height=0.6)
ax1.set_xlabel("Modified Duration (years)")
ax1.set_title("Modified Duration — Higher = More Interest Rate Sensitive", fontsize=10)
for bar, val in zip(bars, df["Mod Duration"]):
    ax1.text(val + 0.05, bar.get_y() + bar.get_height()/2,
             f"{val:.2f}", va="center", fontsize=8)
ax1.grid(True, alpha=0.2, axis="x")

# ── Chart 2: Duration vs Convexity scatter
ax2 = fig.add_subplot(gs[0, 2])
scatter = ax2.scatter(df["Mod Duration"], df["Convexity"],
                      c=df["Years Left"], cmap="Blues",
                      s=80, zorder=5)
for _, row in df.iterrows():
    ax2.annotate(str(row["Years Left"]) + "yr",
                 (row["Mod Duration"], row["Convexity"]),
                 textcoords="offset points", xytext=(5, 3), fontsize=7)
ax2.set_xlabel("Modified Duration")
ax2.set_ylabel("Convexity")
ax2.set_title("Duration vs Convexity\n(longer = higher both)", fontsize=10)
ax2.grid(True, alpha=0.3)

# ── Chart 3: DV01 by Gilt
ax3 = fig.add_subplot(gs[1, 0])
ax3.barh(df["Name"], df["DV01 (£)"],
         color="#c8102e", edgecolor="white", height=0.6, alpha=0.8)
ax3.set_xlabel("DV01 (£ per £100 face)")
ax3.set_title("DV01 — £ loss per\n1bp yield rise", fontsize=10)
ax3.grid(True, alpha=0.2, axis="x")

# ── Chart 4: Rate shock scenarios
ax4 = fig.add_subplot(gs[1, 1])
shocks = ["+50bps (%)", "+100bps (%)", "+200bps (%)"]
shock_labels = ["+50bp", "+100bp", "+200bp"]
x = np.arange(len(df))
width = 0.25
colors = ["#ffaa00", "#ff6600", "#cc0000"]
for i, (shock, label, color) in enumerate(zip(shocks, shock_labels, colors)):
    ax4.bar(x + i*width, df[shock], width,
            label=label, color=color, alpha=0.85)
ax4.set_xticks(x + width)
ax4.set_xticklabels(df["Years Left"].astype(str) + "yr",
                    rotation=45, fontsize=7)
ax4.set_ylabel("Price Change (%)")
ax4.set_title("Rate Shock Scenarios\n(% price loss)", fontsize=10)
ax4.legend(fontsize=8)
ax4.grid(True, alpha=0.2, axis="y")
ax4.axhline(0, color="black", linewidth=0.8)

# ── Chart 5: Duration vs Convexity — price/yield curves
ax5 = fig.add_subplot(gs[1, 2])
ytm_range = np.linspace(1.0, 9.0, 200)
bench = next(r for r in results if "2036" in r["Name"])

# Duration-only estimate (straight line)
dur_only = [bench["Clean Price"] * (1 + price_change_estimate(
    bench["Mod Duration"], 0, bench["YTM (%)"],
    (y - bench["YTM (%)"])*100)/100) for y in ytm_range]

# Duration + Convexity (curved)
dur_conv = [bench["Clean Price"] * (1 + price_change_estimate(
    bench["Mod Duration"], bench["Convexity"], bench["YTM (%)"],
    (y - bench["YTM (%)"])*100)/100) for y in ytm_range]

ax5.plot(ytm_range, dur_only, "--", color="#888", linewidth=1.5,
         label="Duration only (linear)")
ax5.plot(ytm_range, dur_conv, color="#003087", linewidth=2,
         label="Duration + Convexity")
ax5.axvline(bench["YTM (%)"], color="red", linewidth=1,
            linestyle=":", alpha=0.7, label=f"Today: {bench['YTM (%)']:.2f}%")
ax5.set_xlabel("Yield (%)")
ax5.set_ylabel("Estimated Price (£)")
ax5.set_title("Duration vs Convexity\n4.25% Gilt 2036", fontsize=10)
ax5.legend(fontsize=7)
ax5.grid(True, alpha=0.3)

plt.savefig("step4_duration_convexity_dv01.png", dpi=150, bbox_inches="tight")
plt.show()
print("✅ Chart saved: step4_duration_convexity_dv01.png")

print("\n" + "=" * 65)
print("  ✅ STEP 4 COMPLETE!")
print("=" * 65)
print("""
  What you built:
    ✔ Macaulay Duration calculator
    ✔ Modified Duration calculator
    ✔ Convexity calculator
    ✔ DV01/BPV calculator
    ✔ Rate shock scenarios (+50, +100, +200, -100 bps)
    ✔ Portfolio-level aggregation
    ✔ Duration vs Convexity comparison chart

  Next → Step 5: Rate Shock & Scenario Analysis
    Full historical stress testing using 2025-2026 data
""")