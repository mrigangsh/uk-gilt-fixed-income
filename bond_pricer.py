# ============================================================
#  UK GILT FIXED INCOME PROJECT — STEP 2: BOND PRICER
#  Author: Mrigang Sharma
#
#  What this does:
#  Takes a UK Gilt's details (coupon, maturity, face value)
#  and calculates:
#    1. Clean Price       — the quoted market price
#    2. Dirty Price       — what you actually pay (incl. accrued interest)
#    3. Accrued Interest  — interest built up since last coupon payment
#    4. YTM               — your actual annual return if held to maturity
#    5. Current Yield     — annual coupon / price (simple measure)
#
#  Then prices a whole basket of real UK Gilts using
#  today's yields from the BoE data you downloaded in Step 1.
# ============================================================

# ── INSTALL (run once in terminal if needed) ─────────────────
#   pip install pandas numpy matplotlib openpyxl scipy
# ─────────────────────────────────────────────────────────────

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from scipy.optimize import brentq   # Used to solve for YTM numerically
from datetime import date, datetime
import warnings
warnings.filterwarnings("ignore")


# ============================================================
# SECTION A — LOAD TODAY'S YIELDS FROM YOUR BOE FILE
# ============================================================
# Remember Sheet 4 "spot curve" from Step 1?
# We load that here to get today's yield for each maturity.
# These yields are what the market is currently demanding
# to lend to the UK government.
# ============================================================

print("=" * 65)
print("  UK GILT FIXED INCOME PROJECT — STEP 2: BOND PRICER")
print("=" * 65)

# ── Point this to wherever you saved the file ───────────────
BOE_FILE = "GLC Nominal daily data current month.xlsx"
# ─────────────────────────────────────────────────────────────

print(f"\n📂 Loading yield data from: {BOE_FILE}")

try:
    # Actual file structure (confirmed from your file):
    # Row 0: "UK nominal spot curve"
    # Row 1: blank
    # Row 2: "Maturity"
    # Row 3: years (0.5, 1.0, 1.5 ... 24.5)  ← maturity labels here
    # Row 4: "#VALUE!" junk row               ← skip
    # Row 5+: actual data rows with dates + yields

    raw = pd.read_excel(
        BOE_FILE,
        sheet_name="4. spot curve",
        header=None
    )

    # ── Extract maturity labels from row 3
    maturity_row = pd.to_numeric(raw.iloc[3, 1:].values, errors="coerce")
    maturity_row = maturity_row[~np.isnan(maturity_row)]  # drop any NaN

    # ── Data rows start at row 5
    data_rows = raw.iloc[5:, :].copy()

    # ── Assign column names: Date + maturities + ignore extras
    n = len(maturity_row)
    extra_cols = list(range(n + 1, len(data_rows.columns)))
    data_rows.columns = ["Date"] + list(maturity_row) + extra_cols

    # ── Keep only Date + maturity columns
    maturity_cols = list(maturity_row)
    data_rows = data_rows[["Date"] + maturity_cols].copy()

    # ── Dates are already datetime objects in this file
    data_rows["Date"] = pd.to_datetime(data_rows["Date"], errors="coerce")
    data_rows = data_rows.dropna(subset=["Date"])

    # ── Convert all yield columns to numeric
    for col in maturity_cols:
        data_rows[col] = pd.to_numeric(data_rows[col], errors="coerce")

    # ── Sort and clean
    data_rows = data_rows.sort_values("Date").reset_index(drop=True)
    data_rows = data_rows.dropna(subset=maturity_cols, how="all")
    data_rows = data_rows.set_index("Date")

    # ── Get the most recent trading day's yields
    latest_row  = data_rows.dropna(how="all").iloc[-1]
    latest_date = latest_row.name.strftime("%d %b %Y")

    print(f"✅ Data loaded. Most recent trading day: {latest_date}")
    print(f"   Maturities available: {len(maturity_row)} points "
          f"({maturity_row[0]}yr → {maturity_row[-1]}yr)\n")

except FileNotFoundError:
    print(f"\n❌ File not found: {BOE_FILE}")
    print("   Make sure this script is in the SAME folder as the Excel file.")
    print("   Or update the BOE_FILE path at the top of this script.\n")
    exit()


# ============================================================
# SECTION B — BOND PRICING FUNCTIONS
# ============================================================
# These are the core maths functions.
# Every comment explains WHAT the formula does in plain English.
# ============================================================

def get_yield_for_maturity(maturity_years: float, yield_curve: pd.Series) -> float:
    """
    Given a maturity (e.g. 5.3 years), look up the yield from the
    BoE spot curve. If the exact maturity isn't there, interpolate
    between the two nearest points.

    e.g. If we want 5.3yr and we have 5.0yr=4.44% and 5.5yr=4.49%,
    we estimate: 4.44 + (0.3/0.5) * (4.49 - 4.44) = 4.47%
    """
    available = yield_curve.dropna()
    maturities = np.array(available.index.astype(float))
    yields     = np.array(available.values)
    # np.interp does linear interpolation — fills in the gaps
    return float(np.interp(maturity_years, maturities, yields))


def generate_cashflows(
    face_value:   float,
    coupon_rate:  float,
    settlement:   date,
    maturity:     date,
    frequency:    int = 2     # 2 = semi-annual (standard for UK Gilts)
) -> list[tuple[date, float]]:
    """
    Generates all future coupon payments + final principal repayment.

    For a bond with:
      face_value  = £100
      coupon_rate = 4.25%
      frequency   = 2 (semi-annual)

    This produces payments of £2.125 every 6 months + £100 at maturity.
    Returns a list of (payment_date, amount) tuples.
    """
    cashflows = []
    months_between = 12 // frequency   # 6 months for semi-annual

    # Work backwards from maturity to find all coupon dates
    coupon_date = maturity
    while coupon_date > settlement:
        coupon_amount = face_value * (coupon_rate / 100) / frequency
        cashflows.append((coupon_date, coupon_amount))
        # Move back by one coupon period
        month = coupon_date.month - months_between
        year  = coupon_date.year
        if month <= 0:
            month += 12
            year  -= 1
        try:
            coupon_date = coupon_date.replace(year=year, month=month)
        except ValueError:
            # Handle month-end edge cases (e.g. 31 Feb → 28 Feb)
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            coupon_date = coupon_date.replace(year=year, month=month,
                                              day=min(coupon_date.day, last_day))

    # Add the principal repayment to the final cashflow date
    if cashflows:
        last_date, last_coupon = cashflows[0]   # [0] = farthest date (maturity)
        cashflows[0] = (last_date, last_coupon + face_value)

    return sorted(cashflows)   # Sort chronologically


def price_bond(
    face_value:   float,
    coupon_rate:  float,
    ytm:          float,    # Yield to maturity in % (e.g. 4.5)
    settlement:   date,
    maturity:     date,
    frequency:    int = 2
) -> dict:
    """
    THE CORE BOND PRICING FUNCTION.

    How bond pricing works (plain English):
    A bond is just a series of future cash payments.
    Each future payment is worth LESS than the same payment today
    because money today is worth more than money tomorrow.
    (You could invest today's money and earn interest.)

    "Discounting" = calculating what a future payment is worth today.
    The discount rate we use = YTM (yield to maturity).

    Sum up all discounted cashflows → that's the bond price.

    Returns: dirty price, clean price, accrued interest,
             current yield, and all cashflows.
    """
    ytm_decimal = ytm / 100
    cashflows   = generate_cashflows(face_value, coupon_rate,
                                     settlement, maturity, frequency)

    if not cashflows:
        return {"error": "No future cashflows — bond may have matured"}

    # ── Calculate dirty price (present value of all cashflows)
    dirty_price = 0.0
    for cf_date, cf_amount in cashflows:
        # Time in years from settlement to this payment
        t = (cf_date - settlement).days / 365.25
        if t > 0:
            # Discount formula: CF / (1 + r/freq)^(t * freq)
            discount_factor = (1 + ytm_decimal / frequency) ** (t * frequency)
            dirty_price += cf_amount / discount_factor

    # ── Calculate accrued interest
    # Accrued interest = coupon that has "built up" since the last payment
    # but hasn't been paid yet. The buyer pays this to the seller.
    #
    # Example: If the last coupon was 3 months ago and the next is in 3 months,
    # you owe the seller half a coupon payment.
    months_between = 12 // frequency
    prev_coupon = maturity
    while prev_coupon > settlement:
        month = prev_coupon.month - months_between
        year  = prev_coupon.year
        if month <= 0:
            month += 12
            year  -= 1
        try:
            prev_coupon = prev_coupon.replace(year=year, month=month)
        except ValueError:
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            prev_coupon = prev_coupon.replace(year=year, month=month,
                                              day=min(prev_coupon.day, last_day))

    # Days since last coupon / days in full coupon period
    next_coupon = cashflows[0][0] if cashflows else maturity
    days_in_period  = (next_coupon - prev_coupon).days
    days_since_last = (settlement - prev_coupon).days
    coupon_payment  = face_value * (coupon_rate / 100) / frequency

    if days_in_period > 0:
        accrued = coupon_payment * (days_since_last / days_in_period)
    else:
        accrued = 0.0

    # ── Clean price = dirty price minus accrued interest
    # This is the price you see quoted on Bloomberg/Reuters
    # because it removes the "noise" of interest building up daily
    clean_price = dirty_price - accrued

    # ── Current yield = annual coupon / clean price (simple measure)
    annual_coupon  = face_value * (coupon_rate / 100)
    current_yield  = (annual_coupon / clean_price) * 100

    # ── Years to maturity
    years_to_mat = (maturity - settlement).days / 365.25

    return {
        "clean_price":      round(clean_price,  4),
        "dirty_price":      round(dirty_price,  4),
        "accrued_interest": round(accrued,       4),
        "current_yield":    round(current_yield, 4),
        "ytm":              round(ytm,           4),
        "years_to_maturity":round(years_to_mat,  2),
        "cashflows":        cashflows,
        "face_value":       face_value,
        "coupon_rate":      coupon_rate,
    }


def calculate_ytm(
    clean_price:  float,
    face_value:   float,
    coupon_rate:  float,
    settlement:   date,
    maturity:     date,
    frequency:    int = 2
) -> float:
    """
    REVERSE CALCULATION: Given a market price, what is the YTM?

    We can't solve this algebraically — instead we use
    numerical search (Brent's method): try thousands of YTM values
    until the bond price function produces our observed market price.

    Think of it like: you know the answer (price), find the question (yield).
    """
    dirty_price_target = clean_price  # simplified — ignoring accrued for search

    def price_minus_target(ytm_guess):
        result = price_bond(face_value, coupon_rate, ytm_guess * 100,
                            settlement, maturity, frequency)
        if "error" in result:
            return 0
        return result["clean_price"] - dirty_price_target

    try:
        # Search for YTM between 0.01% and 30%
        ytm_decimal = brentq(price_minus_target, 0.0001, 0.30, xtol=1e-8)
        return round(ytm_decimal * 100, 4)
    except:
        return None


# ============================================================
# SECTION C — REAL UK GILTS BASKET
# ============================================================
# These are actual UK Gilts currently in issue (as of 2025/26).
# For each one, we'll use today's BoE yield curve to price them.
#
# Format: (name, coupon_rate%, maturity_date)
# Face value for all UK Gilts = £100
# ============================================================

TODAY      = date.today()
FACE_VALUE = 100.0

GILT_BASKET = [
    # Name                    Coupon%   Maturity
    ("4.50% Treasury 2026",     4.50,  date(2026, 9,  7)),   # ~1yr   Short-dated
    ("4.25% Treasury 2027",     4.25,  date(2027, 6,  7)),   # ~2yr   Short-dated
    ("0.875% Treasury 2029",    0.875, date(2029, 10, 22)),  # ~4yr   Low coupon
    ("4.00% Treasury 2031",     4.00,  date(2031, 1, 22)),   # ~5yr   Medium-dated
    ("4.25% Treasury 2032",     4.25,  date(2032, 6,  7)),   # ~6yr   Medium-dated
    ("4.50% Treasury 2034",     4.50,  date(2034, 9,  7)),   # ~8yr   Medium-dated
    ("4.25% Treasury 2036",     4.25,  date(2036, 6,  7)),   # ~10yr  Benchmark
    ("1.25% Treasury 2041",     1.25,  date(2041, 7, 22)),   # ~15yr  Low coupon long
    ("4.50% Treasury 2042",     4.50,  date(2042, 9,  7)),   # ~16yr  Long-dated
    ("3.50% Treasury 2045",     3.50,  date(2045, 1, 22)),   # ~19yr  Long-dated
    ("3.75% Treasury 2052",     3.75,  date(2052, 7, 22)),   # ~26yr  Ultra-long
]


# ============================================================
# SECTION D — PRICE ALL GILTS AND PRINT RESULTS
# ============================================================

print("─" * 65)
print("  PRICING UK GILT BASKET — USING BOE SPOT CURVE")
print(f"  Settlement Date: {TODAY.strftime('%d %b %Y')}")
print("─" * 65)

results = []

for name, coupon, maturity in GILT_BASKET:

    # Skip gilts that have already matured
    if maturity <= TODAY:
        print(f"  ⚠️  {name} — already matured, skipping")
        continue

    # Years to maturity for this gilt
    years_to_mat = (maturity - TODAY).days / 365.25

    # Get the market yield for this maturity from the BoE curve
    market_ytm = get_yield_for_maturity(years_to_mat, latest_row)

    # Price the bond using that yield
    result = price_bond(FACE_VALUE, coupon, market_ytm,
                        TODAY, maturity, frequency=2)

    if "error" in result:
        print(f"  ⚠️  {name} — {result['error']}")
        continue

    # Store for later (charts + table)
    results.append({
        "Name":           name,
        "Coupon (%)":     coupon,
        "Maturity":       maturity.strftime("%b %Y"),
        "Years Left":     round(years_to_mat, 1),
        "Market YTM (%)": round(market_ytm, 3),
        "Clean Price (£)":result["clean_price"],
        "Dirty Price (£)":result["dirty_price"],
        "Accrued (£)":    result["accrued_interest"],
        "Current Yld (%)":result["current_yield"],
    })


# ── Print as a table
df_results = pd.DataFrame(results)
print(df_results[[
    "Name", "Years Left", "Market YTM (%)",
    "Clean Price (£)", "Accrued (£)", "Current Yld (%)"
]].to_string(index=False))


# ============================================================
# SECTION E — DEEP DIVE: ONE GILT EXPLAINED IN FULL
# ============================================================
# Pick the 5yr Gilt and show every cashflow it will pay,
# its present value, and a breakdown in plain language.
# ============================================================

print("\n" + "=" * 65)
print("  DEEP DIVE — 4.00% Treasury Gilt 2031 (5-Year Benchmark)")
print("=" * 65)

# Find the 5yr gilt in our results
deep_dive_name = "4.00% Treasury 2031"
deep_dive = next((r for r in results if r["Name"] == deep_dive_name), None)

if deep_dive:
    ytm_5yr    = deep_dive["Market YTM (%)"]
    result_5yr = price_bond(FACE_VALUE, 4.00, ytm_5yr, TODAY,
                            date(2031, 1, 22), frequency=2)

    print(f"""
  Bond Details:
  ─────────────────────────────────────────────────
  Face Value    : £{FACE_VALUE:.2f}
  Coupon Rate   : 4.00% per year → £2.00 every 6 months
  Maturity      : Jan 2031  ({result_5yr['years_to_maturity']} years away)
  Market YTM    : {ytm_5yr:.3f}%

  Pricing Results:
  ─────────────────────────────────────────────────
  Clean Price   : £{result_5yr['clean_price']:.4f}
  Accrued Int.  : £{result_5yr['accrued_interest']:.4f}
  Dirty Price   : £{result_5yr['dirty_price']:.4f}  ← what you ACTUALLY pay
  Current Yield : {result_5yr['current_yield']:.4f}%

  Plain English:
  ─────────────────────────────────────────────────
  • This bond pays £2.00 every 6 months + £100 at maturity
  • The market yield ({ytm_5yr:.2f}%) > coupon (4.00%)
    → Bond trades {'below' if result_5yr['clean_price'] < 100 else 'above'} £100 par
  • You pay £{result_5yr['dirty_price']:.2f} today (incl. £{result_5yr['accrued_interest']:.2f} accrued)
  • If you hold to maturity, you earn {ytm_5yr:.3f}% per year
    """)

    # Print first 6 cashflows
    print("  Upcoming Cashflows (first 6):")
    print(f"  {'Date':<15} {'Amount (£)':>12}  Note")
    print(f"  {'─'*15} {'─'*12}  {'─'*30}")
    for i, (cf_date, cf_amount) in enumerate(result_5yr["cashflows"][:6]):
        note = "← Coupon + Principal" if cf_amount > 50 else "← Coupon"
        print(f"  {cf_date.strftime('%d %b %Y'):<15} {cf_amount:>12.4f}  {note}")
    if len(result_5yr["cashflows"]) > 6:
        print(f"  ... and {len(result_5yr['cashflows']) - 6} more coupon payments")


# ============================================================
# SECTION F — VISUALISATION
# ============================================================

print("\n📊 Generating charts...")

fig = plt.figure(figsize=(16, 10))
fig.suptitle(
    f"UK Gilt Bond Pricer — Step 2 Output\n"
    f"Settlement: {TODAY.strftime('%d %b %Y')} | "
    f"Yield Source: Bank of England Spot Curve",
    fontsize=13, fontweight="bold", y=0.98
)

gs = gridspec.GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.38)

# ── Chart 1: Clean Price vs Par (£100) by Gilt
ax1 = fig.add_subplot(gs[0, :2])
colours = ["#c8102e" if p < 100 else "#007a33"
           for p in df_results["Clean Price (£)"]]
bars = ax1.barh(df_results["Name"], df_results["Clean Price (£)"],
                color=colours, edgecolor="white", height=0.6)
ax1.axvline(100, color="black", linewidth=1.5, linestyle="--",
            label="Par (£100)")
ax1.set_xlabel("Clean Price (£)")
ax1.set_title("Clean Price vs Par — Red = Below Par, Green = Above Par",
              fontsize=10)
ax1.legend(fontsize=9)
for bar, val in zip(bars, df_results["Clean Price (£)"]):
    ax1.text(val + 0.1, bar.get_y() + bar.get_height() / 2,
             f"£{val:.2f}", va="center", fontsize=8)
ax1.set_xlim(
    min(df_results["Clean Price (£)"]) - 5,
    max(df_results["Clean Price (£)"]) + 8
)

# ── Chart 2: YTM by Maturity (mini yield curve from our basket)
ax2 = fig.add_subplot(gs[0, 2])
ax2.scatter(df_results["Years Left"], df_results["Market YTM (%)"],
            color="#003087", zorder=5, s=60)
ax2.plot(df_results["Years Left"], df_results["Market YTM (%)"],
         color="#003087", linewidth=1.5, alpha=0.6)
ax2.set_xlabel("Years to Maturity")
ax2.set_ylabel("YTM (%)")
ax2.set_title("Yield Curve\n(from our Gilt basket)", fontsize=10)
ax2.grid(True, alpha=0.3)

# ── Chart 3: Coupon vs YTM (shows premium/discount drivers)
ax3 = fig.add_subplot(gs[1, 0])
x = range(len(df_results))
width = 0.35
ax3.bar([i - width/2 for i in x], df_results["Coupon (%)"],
        width, label="Coupon %", color="#003087", alpha=0.8)
ax3.bar([i + width/2 for i in x], df_results["Market YTM (%)"],
        width, label="Market YTM %", color="#c8102e", alpha=0.8)
ax3.set_xticks(list(x))
ax3.set_xticklabels(df_results["Years Left"].astype(str) + "yr",
                    rotation=45, fontsize=7)
ax3.set_ylabel("Rate (%)")
ax3.set_title("Coupon vs Market YTM\n(gap drives premium/discount)", fontsize=10)
ax3.legend(fontsize=8)
ax3.grid(True, alpha=0.2, axis="y")

# ── Chart 4: Price vs YTM curve for the 5yr gilt (shows inverse relationship)
ax4 = fig.add_subplot(gs[1, 1])
ytm_range  = np.linspace(1.0, 9.0, 100)
price_range = []
for y in ytm_range:
    r = price_bond(FACE_VALUE, 4.00, y, TODAY, date(2031, 1, 22))
    price_range.append(r["clean_price"])

ax4.plot(ytm_range, price_range, color="#c8102e", linewidth=2)
ax4.axhline(100, color="grey", linestyle="--", linewidth=1, alpha=0.7,
            label="Par (£100)")
if deep_dive:
    ax4.scatter([ytm_5yr], [deep_dive["Clean Price (£)"]],
                color="#003087", zorder=5, s=80, label=f"Today: {ytm_5yr:.2f}%")
ax4.set_xlabel("Yield to Maturity (%)")
ax4.set_ylabel("Clean Price (£)")
ax4.set_title("Price/Yield Relationship\n4% Gilt 2031 — inverse curve",
              fontsize=10)
ax4.legend(fontsize=8)
ax4.grid(True, alpha=0.3)

# ── Chart 5: Cashflow timeline for 5yr gilt
ax5 = fig.add_subplot(gs[1, 2])
if deep_dive:
    cf_dates  = [cf[0] for cf in result_5yr["cashflows"]]
    cf_amounts= [cf[1] for cf in result_5yr["cashflows"]]
    cf_colors = ["#c8102e" if a > 50 else "#003087" for a in cf_amounts]
    ax5.bar(range(len(cf_dates)), cf_amounts, color=cf_colors, edgecolor="white")
    ax5.set_xticks(range(len(cf_dates)))
    ax5.set_xticklabels(
        [d.strftime("%b\n%Y") for d in cf_dates],
        fontsize=6, rotation=0
    )
    ax5.set_ylabel("Cashflow (£)")
    ax5.set_title("Cashflow Timeline\n4% Gilt 2031 (red = principal repayment)",
                  fontsize=10)
    ax5.grid(True, alpha=0.2, axis="y")

plt.savefig("step2_bond_pricer.png", dpi=150, bbox_inches="tight")
plt.show()
print("✅ Chart saved as: step2_bond_pricer.png")


# ============================================================
# SECTION G — KEY INSIGHT SUMMARY
# ============================================================

print("\n" + "=" * 65)
print("  KEY INSIGHTS FROM TODAY'S PRICING")
print("=" * 65)

cheapest  = df_results.loc[df_results["Clean Price (£)"].idxmin()]
priciest  = df_results.loc[df_results["Clean Price (£)"].idxmax()]
high_ytm  = df_results.loc[df_results["Market YTM (%)"].idxmax()]

print(f"""
  📉 Cheapest Gilt (furthest below par):
     {cheapest['Name']} → £{cheapest['Clean Price (£)']:.2f}
     (YTM {cheapest['Market YTM (%)']:.3f}% >> Coupon {cheapest['Coupon (%)']:.2f}%)

  📈 Most Expensive Gilt (closest to/above par):
     {priciest['Name']} → £{priciest['Clean Price (£)']:.2f}

  🔺 Highest Yielding Gilt (most return if held to maturity):
     {high_ytm['Name']} → YTM {high_ytm['Market YTM (%)']:.3f}%

  💡 Key Relationship (core fixed income principle):
     When market yield > coupon → price < £100 (discount)
     When market yield < coupon → price > £100 (premium)
     When market yield = coupon → price = £100 (par)
     This is the fundamental law of bond pricing.
""")

print("=" * 65)
print("  ✅ STEP 2 COMPLETE!")
print("=" * 65)
print("""

  
""")
