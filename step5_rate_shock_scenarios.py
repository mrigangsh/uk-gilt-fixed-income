# ============================================================
#  UK GILT FIXED INCOME PROJECT — STEP 5: RATE SHOCK SCENARIOS
#  Author: Mrigang Sharma
#
#  What this does:
#  Takes the Gilt portfolio and stress tests it against:
#    1. Parallel shifts  (+50, +100, +200, -50, -100 bps)
#    2. Curve steepener  (short end flat, long end rises)
#    3. Curve flattener  (short end rises, long end flat)
#    4. 2022 Crisis replay (actual BoE data from 2025-26)
#
#  Uses FULL REPRICING — not duration approximation.
#  Every bond is repriced from scratch at the new yield level.
# ============================================================
 
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from datetime import date
import warnings
warnings.filterwarnings("ignore")
 
print("=" * 65)
print("  STEP 5: RATE SHOCK & SCENARIO ANALYSIS")
print("=" * 65)
 
 
# ============================================================
# SECTION A — LOAD YIELD CURVE
# ============================================================
 
BOE_CURRENT  = "GLC Nominal daily data current month.xlsx"
BOE_HISTORY  = "GLC Nominal daily data_2025 to present.xlsx"
 
def load_spot_curve(filepath):
    raw = pd.read_excel(filepath, sheet_name="4. spot curve", header=None)
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
    return data_rows, maturity_cols
 
print(f"\n📂 Loading current yield curve...")
df_current, mat_cols = load_spot_curve(BOE_CURRENT)
latest_row  = df_current.dropna(how="all").iloc[-1]
latest_date = latest_row.name.strftime("%d %b %Y")
print(f"✅ Current curve loaded — {latest_date}")
 
print(f"📂 Loading historical yield curve...")
df_history, _ = load_spot_curve(BOE_HISTORY)
print(f"✅ Historical data loaded — {len(df_history)} trading days\n")
 
 
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
            coupon_date = coupon_date.replace(
                year=year, month=month,
                day=min(coupon_date.day, last_day))
    if cashflows:
        last_date, last_cf = cashflows[0]
        cashflows[0] = (last_date, last_cf + face)
    return sorted(cashflows)
 
 
def full_reprice(face, coupon_rate, settlement, maturity,
                 shocked_curve, freq=2):
    """
    FULL REPRICING — the exact method.
    Takes every future cashflow and discounts it using
    the SHOCKED yield curve (not today's curve).
    No approximation. No formula. Pure present value.
    """
    cashflows = get_cashflows(face, coupon_rate, settlement, maturity, freq)
    price = 0.0
    for cf_date, cf_amount in cashflows:
        t = (cf_date - settlement).days / 365.25
        if t > 0:
            ytm = get_yield(t, shocked_curve) / 100
            price += cf_amount / (1 + ytm / freq) ** (t * freq)
    return price
 
 
def apply_parallel_shift(curve, shift_bps):
    """
    Parallel shift — move entire yield curve up or down
    by the same number of basis points at every maturity.
    e.g. +100bps: every yield goes up by exactly 1%
    """
    shifted = curve.copy()
    shifted = shifted + (shift_bps / 100)
    return shifted
 
 
def apply_steepener(curve, short_shift_bps=0, long_shift_bps=100):
    """
    Steepener — short end stays flat (or rises slightly),
    long end rises more. The curve becomes STEEPER.
 
    Real world cause: market fears long-term inflation
    while short rates are anchored by central bank policy.
 
    We interpolate the shift linearly from short to long end.
    e.g. 2yr gets 0bps, 25yr gets +100bps, everything in between
    gets a proportional amount.
    """
    mats = np.array(curve.index.astype(float))
    min_mat, max_mat = mats.min(), mats.max()
 
    shifts = short_shift_bps + (long_shift_bps - short_shift_bps) * \
             (mats - min_mat) / (max_mat - min_mat)
 
    shifted = curve.copy()
    for mat, shift in zip(mats, shifts):
        if mat in shifted.index:
            shifted[mat] = shifted[mat] + shift / 100
    return shifted
 
 
def apply_flattener(curve, short_shift_bps=100, long_shift_bps=0):
    """
    Flattener — short end rises, long end stays flat.
    The curve becomes FLATTER (smaller gap between short and long).
 
    Real world cause: central bank raises short rates aggressively
    while long-term inflation expectations remain anchored.
    """
    return apply_steepener(curve, short_shift_bps, long_shift_bps)
 
 
# ============================================================
# SECTION C — GILT BASKET & POSITION SIZES
# ============================================================
 
TODAY = date.today()
FACE  = 100.0
POSITION = 1_000_000  # £1 million per gilt
 
GILTS = [
    ("4.50% T2026",  4.50,  date(2026,  9,  7)),
    ("4.25% T2027",  4.25,  date(2027,  6,  7)),
    ("0.875% T2029", 0.875, date(2029, 10, 22)),
    ("4.00% T2031",  4.00,  date(2031,  1, 22)),
    ("4.25% T2032",  4.25,  date(2032,  6,  7)),
    ("4.50% T2034",  4.50,  date(2034,  9,  7)),
    ("4.25% T2036",  4.25,  date(2036,  6,  7)),
    ("1.25% T2041",  1.25,  date(2041,  7, 22)),
    ("4.50% T2042",  4.50,  date(2042,  9,  7)),
    ("3.50% T2045",  3.50,  date(2045,  1, 22)),
    ("3.75% T2052",  3.75,  date(2052,  7, 22)),
]
 
# Calculate today's base prices
base_prices = {}
for name, coupon, maturity in GILTS:
    if maturity <= TODAY:
        continue
    yrs = (maturity - TODAY).days / 365.25
    ytm = get_yield(yrs, latest_row)
    price = full_reprice(FACE, coupon, TODAY, maturity, latest_row)
    units = POSITION / price
    base_prices[name] = {
        "price": price,
        "units": units,
        "value": units * price,
        "coupon": coupon,
        "maturity": maturity,
        "ytm": ytm
    }
 
total_base_value = sum(v["value"] for v in base_prices.values())
 
 
# ============================================================
# SECTION D — RUN ALL SCENARIOS
# ============================================================
 
SCENARIOS = {
    "+50 bps":    apply_parallel_shift(latest_row, +50),
    "+100 bps":   apply_parallel_shift(latest_row, +100),
    "+200 bps":   apply_parallel_shift(latest_row, +200),
    "-50 bps":    apply_parallel_shift(latest_row, -50),
    "-100 bps":   apply_parallel_shift(latest_row, -100),
    "Steepener":  apply_steepener(latest_row, 0, +100),
    "Flattener":  apply_flattener(latest_row, +100, 0),
}
 
print("─" * 65)
print("  RATE SHOCK RESULTS — FULL REPRICING")
print(f"  Base Portfolio Value: £{total_base_value:>12,.0f}")
print("─" * 65)
 
scenario_results = {}
bond_scenario_pnl = {name: {} for name, _, _ in GILTS
                     if base_prices.get(name)}
 
for scenario_name, shocked_curve in SCENARIOS.items():
    total_shocked_value = 0
    for name, coupon, maturity in GILTS:
        if maturity <= TODAY or name not in base_prices:
            continue
        shocked_price = full_reprice(FACE, coupon, TODAY,
                                     maturity, shocked_curve)
        units = base_prices[name]["units"]
        shocked_value = units * shocked_price
        pnl = shocked_value - base_prices[name]["value"]
        bond_scenario_pnl[name][scenario_name] = pnl
        total_shocked_value += shocked_value
 
    total_pnl = total_shocked_value - total_base_value
    pnl_pct   = (total_pnl / total_base_value) * 100
    scenario_results[scenario_name] = {
        "total_value": total_shocked_value,
        "pnl":         total_pnl,
        "pnl_pct":     pnl_pct
    }
 
    print(f"\n  Scenario: {scenario_name}")
    print(f"  Portfolio P&L: £{total_pnl:>12,.0f}  ({pnl_pct:+.2f}%)")
 
 
# ============================================================
# SECTION E — BOND LEVEL BREAKDOWN
# ============================================================
 
print("\n" + "=" * 65)
print("  BOND LEVEL P&L BY SCENARIO (£)")
print("=" * 65)
 
bond_names   = list(bond_scenario_pnl.keys())
scen_names   = list(SCENARIOS.keys())
pnl_matrix   = pd.DataFrame(bond_scenario_pnl).T
pnl_matrix   = pnl_matrix.reindex(bond_names)
pnl_matrix.columns = scen_names
 
print(pnl_matrix.applymap(lambda x: f"£{x:>+,.0f}").to_string())
 
 
# ============================================================
# SECTION F — STEEPENER vs FLATTENER DEEP DIVE
# ============================================================
 
print("\n" + "=" * 65)
print("  DEEP DIVE — STEEPENER vs FLATTENER")
print("=" * 65)
print("""
  Steepener (+100bps at long end, flat at short end):
  → Short dated bonds (2026, 2027) — barely affected
  → Long dated bonds (2045, 2052) — heavily hit
  → Why: long end rises most, long bonds are most rate sensitive
 
  Flattener (+100bps at short end, flat at long end):
  → Short dated bonds (2026, 2027) — hit hardest relative to duration
  → Long dated bonds (2045, 2052) — barely affected
  → Why: short end rises most, but short bonds have low duration
 
  Real world 2022 parallel:
  → BoE raised rates 14 times from 0.1% to 5.25%
  → Short end rose dramatically (policy rates)
  → Long end also rose but less (curve flattened then inverted)
  → UK pension funds lost billions on long-dated gilt holdings
""")
 
 
# ============================================================
# SECTION G — HISTORICAL RATE MOVES FROM YOUR DATA
# ============================================================
 
print("=" * 65)
print("  HISTORICAL CONTEXT — 2025 to 2026 YIELD MOVES")
print("=" * 65)
 
key_mats = [0.5, 2.0, 5.0, 10.0, 20.0]
available_mats = [m for m in key_mats if m in df_history.columns]
 
first_row = df_history.dropna(how="all").iloc[0]
last_row  = df_history.dropna(how="all").iloc[-1]
first_date = first_row.name.strftime("%d %b %Y")
last_date  = last_row.name.strftime("%d %b %Y")
 
print(f"\n  Yield change from {first_date} → {last_date}:")
print(f"\n  {'Maturity':<12} {'Start':>8} {'End':>8} {'Change':>10}")
print(f"  {'─'*12} {'─'*8} {'─'*8} {'─'*10}")
for mat in available_mats:
    start = first_row[mat]
    end   = last_row[mat]
    chg   = (end - start) * 100
    print(f"  {str(mat)+'yr':<12} {start:>7.3f}% {end:>7.3f}% {chg:>+9.1f}bp")
 
# Max and min 10yr yield in the period
if 10.0 in df_history.columns:
    series_10yr = df_history[10.0].dropna()
    max_yield = series_10yr.max()
    min_yield = series_10yr.min()
    max_date  = series_10yr.idxmax().strftime("%d %b %Y")
    min_date  = series_10yr.idxmin().strftime("%d %b %Y")
    print(f"\n  10yr Gilt Yield Range (2025–2026):")
    print(f"  Highest: {max_yield:.3f}% on {max_date}")
    print(f"  Lowest:  {min_yield:.3f}% on {min_date}")
    print(f"  Range:   {(max_yield - min_yield)*100:.1f} basis points")
 
 
# ============================================================
# SECTION H — VISUALISATION
# ============================================================
 
print("\n📊 Generating charts...")
 
fig = plt.figure(figsize=(18, 14))
fig.suptitle(
    f"UK Gilt Portfolio — Step 5: Rate Shock & Scenario Analysis\n"
    f"Base Date: {latest_date} | Position: £1M per Gilt | "
    f"Full Repricing Method",
    fontsize=13, fontweight="bold", y=0.98
)
 
gs = gridspec.GridSpec(3, 3, figure=fig, hspace=0.5, wspace=0.38)
 
# ── Chart 1: Portfolio P&L by Scenario
ax1 = fig.add_subplot(gs[0, :2])
scen_labels = list(scenario_results.keys())
pnl_values  = [scenario_results[s]["pnl"] for s in scen_labels]
colors = ["#c8102e" if p < 0 else "#007a33" for p in pnl_values]
bars = ax1.bar(scen_labels, pnl_values, color=colors,
               edgecolor="white", width=0.6)
ax1.axhline(0, color="black", linewidth=0.8)
ax1.set_ylabel("Portfolio P&L (£)")
ax1.set_title("Total Portfolio P&L by Scenario — Full Repricing", fontsize=10)
ax1.tick_params(axis="x", rotation=15)
for bar, val in zip(bars, pnl_values):
    ax1.text(bar.get_x() + bar.get_width()/2,
             val + (5000 if val >= 0 else -15000),
             f"£{val:,.0f}", ha="center", fontsize=8,
             color="black")
ax1.yaxis.set_major_formatter(
    plt.FuncFormatter(lambda x, _: f"£{x:,.0f}"))
ax1.grid(True, alpha=0.2, axis="y")
 
# ── Chart 2: Shocked yield curves
ax2 = fig.add_subplot(gs[0, 2])
mats = np.array(latest_row.dropna().index.astype(float))
ylds = np.array(latest_row.dropna().values)
ax2.plot(mats, ylds, "k-", linewidth=2.5, label="Today", zorder=5)
colors_curve = {"Steepener": "#c8102e",
                "Flattener": "#003087",
                "+200 bps":  "#ff6600",
                "-100 bps":  "#007a33"}
for label, curve in SCENARIOS.items():
    if label in colors_curve:
        c_ylds = np.array(curve.dropna().values)
        ax2.plot(mats, c_ylds, "--", linewidth=1.5,
                 color=colors_curve[label], label=label, alpha=0.8)
ax2.set_xlabel("Maturity (years)")
ax2.set_ylabel("Yield (%)")
ax2.set_title("Shocked Yield Curves\nvs Today", fontsize=10)
ax2.legend(fontsize=7)
ax2.grid(True, alpha=0.3)
 
# ── Chart 3: Bond level heatmap — P&L by bond and scenario
ax3 = fig.add_subplot(gs[1, :])
pnl_arr = pnl_matrix.values / 1000  # convert to £k
im = ax3.imshow(pnl_arr, cmap="RdYlGn", aspect="auto",
                vmin=-200, vmax=200)
ax3.set_xticks(range(len(scen_names)))
ax3.set_xticklabels(scen_names, fontsize=9)
ax3.set_yticks(range(len(bond_names)))
ax3.set_yticklabels(bond_names, fontsize=8)
ax3.set_title("Bond P&L Heatmap by Scenario (£000s) — "
              "Green = Gain, Red = Loss", fontsize=10)
for i in range(len(bond_names)):
    for j in range(len(scen_names)):
        val = pnl_arr[i, j]
        ax3.text(j, i, f"{val:+.0f}k",
                 ha="center", va="center",
                 fontsize=7.5, fontweight="500",
                 color="black")
plt.colorbar(im, ax=ax3, label="P&L (£000s)")
 
# ── Chart 4: Steepener vs Flattener comparison
ax4 = fig.add_subplot(gs[2, 0])
steep_pnl = [bond_scenario_pnl[n]["Steepener"]
             for n in bond_names if n in bond_scenario_pnl]
flat_pnl  = [bond_scenario_pnl[n]["Flattener"]
             for n in bond_names if n in bond_scenario_pnl]
x = np.arange(len(bond_names))
ax4.bar(x - 0.2, steep_pnl, 0.4, label="Steepener",
        color="#c8102e", alpha=0.8)
ax4.bar(x + 0.2, flat_pnl,  0.4, label="Flattener",
        color="#003087", alpha=0.8)
ax4.set_xticks(x)
ax4.set_xticklabels(bond_names, rotation=45, fontsize=6)
ax4.set_ylabel("P&L (£)")
ax4.set_title("Steepener vs Flattener\nby Bond", fontsize=10)
ax4.legend(fontsize=8)
ax4.axhline(0, color="black", linewidth=0.8)
ax4.grid(True, alpha=0.2, axis="y")
ax4.yaxis.set_major_formatter(
    plt.FuncFormatter(lambda x, _: f"£{x:,.0f}"))
 
# ── Chart 5: Historical 10yr yield
ax5 = fig.add_subplot(gs[2, 1:])
if 10.0 in df_history.columns:
    hist_10yr = df_history[10.0].dropna()
    ax5.plot(hist_10yr.index, hist_10yr.values,
             color="#003087", linewidth=1.5)
    ax5.fill_between(hist_10yr.index, hist_10yr.values,
                     hist_10yr.min(), alpha=0.1, color="#003087")
    # Add shock lines
    for shock, color, label in [
        (+0.50, "#ffaa00", "+50bp shock"),
        (+1.00, "#ff6600", "+100bp shock"),
        (+2.00, "#c8102e", "+200bp shock"),
    ]:
        ax5.axhline(hist_10yr.iloc[-1] + shock,
                    color=color, linewidth=1.2,
                    linestyle="--", alpha=0.8, label=label)
    ax5.set_title("Historical 10yr Gilt Yield (2025–2026)\n"
                  "with shock levels marked", fontsize=10)
    ax5.set_ylabel("Yield (%)")
    ax5.legend(fontsize=7)
    ax5.grid(True, alpha=0.3)
    ax5.tick_params(axis="x", rotation=30)
 
plt.savefig("step5_rate_shock_scenarios.png",
            dpi=150, bbox_inches="tight")
plt.show()
print("✅ Chart saved: step5_rate_shock_scenarios.png")
 
print("\n" + "=" * 65)
print("  ✅ STEP 5 COMPLETE!")
print("=" * 65)
print("""
  What you built:
    ✔ Parallel shift scenarios (+200 to -100 bps)
    ✔ Curve steepener (long end rises)
    ✔ Curve flattener (short end rises)
    ✔ Full repricing — not duration approximation
    ✔ Bond level P&L heatmap
    ✔ Historical 10yr yield context
    ✔ Portfolio total P&L for every scenario
 
  Next → Step 6: Portfolio Analytics & GitHub
    Putting it all together + uploading to GitHub
""")