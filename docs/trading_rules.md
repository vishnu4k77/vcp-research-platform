# VCP Scanner — Trading Rules & Decision Framework
**Last reviewed:** 2026-06-01
**Style:** Swing trade, hold 7–60 days, EOD decisions only

> All signal names below match exactly what you see in the dashboard.
> Edit the Review Log at the bottom whenever you update a rule.

---

## Part 1 — What Each Dashboard Signal Means

These are the 7 checkboxes in the sidebar + the columns you see in the scanner table.
This section explains in plain English what each one actually means.

---

### ✅ Trend
**Dashboard checkbox:** Trend
**Dashboard table column:** Trend (green ✓ or red ✗)

**What it means in plain English:**
All 5 EMAs are stacked cleanly in bullish order:
EMA10 > EMA21 > EMA50 > EMA150 > EMA200, and close is above EMA200.

**When it is green (= 1):** The stock is trending up on every timeframe simultaneously — short, medium, and long term all aligned.

**When it is red (= 0):** EMAs are crossed or flat. The trend is broken or mixed.

**Trading use:** This is the foundation. Never take a breakout entry if Trend = 0. The structure must be right first.

---

### ✅ Stage 2
**Dashboard checkbox:** Stage 2
**Dashboard table column:** Stage2 (green ✓ or red ✗)
**Related column:** S2 Age(d) — how many trading days the stock has been in Stage 2

**What it means in plain English:**
Weinstein Stage 2 = the advancing phase of the market cycle.
EMA200 is rising (not falling, not flat). Stock is above a rising EMA200.

**S2 Age(d) colour coding in the table:**
- Green (≤ 20 days): Fresh Stage 2 entry — best window. Most upside ahead.
- Orange (21–60 days): Mid-run. Still valid but watch for signs of slowing.
- Red (> 60 days): Advanced run. Stock may be approaching Stage 3 distribution top.

**When it is green (= 1):** Stock is in the right phase of the market cycle.
**When it is red (= 0):** Stock is in Stage 1 (base), Stage 3 (distribution), or Stage 4 (decline). Do not enter.

**Trading use:** S2 Age(d) = 1–20 (green) is the highest priority setup. The earlier in Stage 2, the more room to run.

---

### ✅ VCP
**Dashboard checkbox:** VCP
**Dashboard table column:** VCP (green ✓ or red ✗)

**What it means in plain English:**
Volume AND volatility AND ATR (average true range) are ALL contracting at the same time, while the stock is in Stage 2.
The stock is coiling quietly. Volume drying up. Price range tightening.

**When it is green (= 1):** Institutions are accumulating quietly. The spring is compressed. A breakout is being set up.
**When it is red (= 0):** No contraction. Either volume is elevated (distribution risk) or the pattern is not formed.

**Trading use:** VCP = 1 alone is NOT an entry trigger. It means "watch this stock — a breakout trigger may come in the next few days." Entry is when Breakout fires or BO Ready transitions to Breakout.

---

### ✅ Breakout
**Dashboard checkbox:** Breakout
**Dashboard table column:** Breakout (green ✓ or red ✗)

**What it means in plain English:**
TODAY the stock broke above its prior 20-day resistance level with all of these confirmed:
- Volume ≥ 1.5× the 20-day average (institutional participation)
- Close in the upper half of today's candle (no rejection wick)
- Not an earnings day (event volume ≠ accumulation)
- Base range tight < 15% (proper consolidation, not a V-shaped recovery)
- Stock within 25% of its 52-week high (near new highs, not recovering from a crash)
- Stock above EMA50 (near-term strength)
- Prior uptrend ≥ 30% above 52-week low (established run, not a bounce)
- No gap-and-fail trap (opened big, closed below open)

**When it is green (= 1):** A CONFIRMED breakout happened today. All quality guards passed.
**When it is red (= 0):** No breakout today, OR breakout happened but failed one of the guards above.

**Trading use:** This is the primary entry trigger for Breakout Day and Composite presets.

---

### ✅ BO Ready
**Dashboard table column only:** BO Ready (no checkbox — this is a watchlist column)

**What it means in plain English:**
The stock is within 3% of its resistance level but has NOT broken out yet.
It is approaching the pivot. If volume comes in tomorrow, it could trigger a Breakout.

**When it is green (= 1):** Stock is 0–3% below its breakout pivot. Add to watchlist.
**When it is red (= 0):** Stock is more than 3% below the pivot — not approaching yet.

**Trading use:** BO Ready = 1 AND VCP = 1 AND Stage 2 = 1 is your watchlist. These are the stocks to monitor daily for a Breakout trigger.

---

### ✅ Liquid
**Dashboard checkbox:** Liquidity
**Dashboard table column:** Liquid (green ✓ or red ✗)

**What it means in plain English:**
The stock trades enough daily rupee value that you can enter and exit without moving the price against yourself.
Threshold: avg_volume × price ≥ ₹5 crore per day.

**When it is green (= 1):** The stock is liquid enough for a retail swing trade.
**When it is red (= 0):** Thin stock. Entry/exit will move the price. Avoid.

**Trading use:** Always filter for Liquid = 1. No exceptions for swing trades.

---

### ✅ Quality
**Dashboard checkbox:** Quality
**Dashboard table column:** Quality (green ✓ or red ✗)

**What it means in plain English:**
Current behaviour (technical proxy — until Screener.in fundamentals are loaded):
Quality = 1 if Stage2 = 1 AND Trend score ≥ 3 AND volume sufficient.

Future behaviour (after running fetch_fundamentals.py):
Quality = 1 if the company passes ≥ 5 of these 10 criteria:
ROE ≥ 12%, ROCE ≥ 12%, Debt/Equity ≤ 1.0, Sales growth ≥ 10%, Profit growth ≥ 10%,
Promoter holding ≥ 40%, OPM ≥ 10%, Market cap ≥ ₹500 crore, EPS accelerating, Promoter pledge ≤ 20%.

**Trading use:** For Quality Growth preset, require Quality = 1. For Breakout Day preset, not required — it's a momentum scanner, fundamentals are secondary.

---

### ✅ RS (vs Nifty)
**Dashboard checkbox:** RS (vs Nifty)
**Dashboard table column:** RS (green ✓ or red ✗)

**What it means in plain English:**
The stock's 63-trading-day return is higher than Nifty 50's 63-day return.
The stock is outperforming the benchmark.

**When it is green (= 1):** Institutions are choosing this stock over the index. Relative strength present.
**When it is red (= 0):** Stock underperforming the market. Money is flowing into other sectors.

**Trading use:** For RS Leaders preset and Minervini A+, always require RS = 1. For pure breakout trades it is a bonus, not a requirement.

---

### Score (0–100)
**Dashboard table column:** Score
**Colour:** Green ≥ 70 | Orange ≥ 50 | Red < 50

**What it means:**
A weighted sum of all the signals above, recalculated by the selected Scanner Strategy preset.
Different presets give different weights — e.g. Breakout Day weights Breakout heavily; RS Leaders weights RS heavily.

**Score ≥ 70 = Institutional Candidate (IC column = ✓)**
**Score 50–69 = Watchlist quality**
**Score < 50 = Weak setup**

---

### Dist Pivot%
Percentage above (+) or below (–) the prior 20-day resistance pivot.
- Negative = stock hasn't broken out yet
- 0 to +3% = in the breakout zone (valid entry range)
- > +5% = extended, do not chase

### Dist 52w%
Percentage below the 52-week high.
- 0% = at 52-week high (Darvas ideal)
- –10% = within 10% of high (Darvas valid, Breakout valid)
- –25% = edge of Minervini condition 8
- Beyond –25% = too far from high, overhead supply risk

---

## Part 2 — Which Checkboxes to Tick for Each Scanner Strategy

| Scanner Preset | Tick these checkboxes | What you are looking for |
|---|---|---|
| **Composite (Default)** | Stage 2, Liquidity | Broad quality. Score ≥ 70 filters the rest. |
| **VCP Setup** | Stage 2, VCP, Liquidity | Stocks coiling quietly in an uptrend. Entry when Breakout fires. |
| **Breakout Day** | Breakout, Liquidity | Active breakout happening today with volume. |
| **RS Leaders** | Trend, RS (vs Nifty), Liquidity | Stocks outrunning Nifty in an uptrend. |
| **Quality Growth** | Stage 2, Quality, Liquidity | Fundamentally strong stocks in the right phase. |
| **Minervini A+** | Trend, Stage 2, RS (vs Nifty), Liquidity | All structural conditions met. Highest quality filter. |
| **Darvas Breakout** | Breakout, Liquidity | Box breakout near 52-week highs. Check Dist 52w% < –10%. |

**Can you tick multiple checkboxes?** Yes. Every ticked checkbox = AND condition.
Ticking Stage 2 + VCP + Breakout = only stocks where ALL THREE are green show up.

---

## Part 2B — Two Entry Approaches (Weinstein vs Minervini)

**Why does this matter?**
Breakout fires rarely by design — all 10 guards must pass on the same day.
Stage 2 Age = 1 with all structural checks green is a valid entry on its own (Weinstein method).

### Weinstein Entry — early, more upside, less confirmation
**When:** Stage 2 just started (Age = 1–5 days) + Trend ✅ + RS ✅ + Liquid ✅ + Quality ✅
**Entry price:** Current close (stock just crossed above rising EMA200 into Stage 2)
**Stop:** Just below EMA200 (it is very close at Stage 2 start — tight stop)
**Target:** +20% same as always
**Advantage:** Maximum upside — entire Stage 2 run still ahead
**Risk:** Stage 2 transition can be false. Stock can re-enter Stage 1.
**Verdict:** Valid entry. Best when S2 Age = 1–5 AND sector is leading.

### Minervini Entry — later, less upside, more confirmation
**When:** Breakout ✅ fires (all 10 guards pass on same day)
**Entry price:** 0–3% above pivot (check Dist Pivot% in scanner)
**Stop:** Entry – 7%
**Target:** +20%
**Advantage:** All quality guards confirmed. Institutional volume confirmed.
**Risk:** Rare. You may wait weeks watching a good stock never fire.
**Verdict:** Highest confidence entry. Best for largest position sizes.

### Which to use when
```
S2 Age = 1–5 + all structural green
→ Weinstein entry now. Don't wait weeks for Breakout.

S2 Age = any + VCP ✅ + BO Ready ✅
→ Watch closely. Breakout may come in 1–3 days. Be ready.

S2 Age = any + Breakout ✅
→ Minervini entry. Highest confirmation.
```

---

## Part 3 — Entry Rules Per Scanner

### VCP Setup → entry trigger
- VCP = ✅ means the stock is COILING. This is NOT the entry.
- Entry comes when BREAKOUT fires the NEXT DAY or day after.
- Entry price: Within 2% of Dist Pivot% (table shows this). If Dist Pivot% > +3% on entry day → extended, skip.

### Breakout Day → entry trigger
- Breakout = ✅ means the breakout happened TODAY.
- Entry: Day 0 close OR Day 1 open (next morning).
- If Day 1 open is >3% above Day 0 close → too extended, do not enter.
- NO 2-day wait for this scanner. The guards already confirmed quality.

### Composite, Minervini A+, Quality Growth → entry trigger
- Wait 2 days after signal (see Part 4).
- Entry on Day 2 if stock still within 2% of pivot (Dist Pivot% ≤ +2%).

### RS Leaders → entry trigger
- Do NOT buy on new 52w highs. Enter on pullbacks.
- Entry: When stock pulls back to 10 or 21 EMA within Stage 2 uptrend.
- Watch table: Dist Pivot% goes from small positive to slightly negative → pullback opportunity.

### Darvas Breakout → entry trigger
- Entry: Day 0 close OR Day 1 open (same logic as Breakout Day).
- Check Dist 52w%: must be between 0% and –10%.
- If Dist 52w% < –10% → stock is NOT near new highs → skip (not a Darvas setup).

---

## Part 4 — 2-Day Confirmation (Composite, VCP, Minervini A+, Quality Growth only)

```
Day 0 — Scanner fires. Add to watchlist. Do NOT enter.
Day 1 — Observe. Check these in the table next day:
         □ Breakout column still green? (held above pivot)
         □ VCP column still green? (no distribution)
         □ Dist Pivot% still positive? (not given back gains)
         □ Nifty not down >1%?
Day 2 — Entry decision:
         □ Dist Pivot% between 0 and +3%? → ENTER
         □ Dist Pivot% > +5%? → EXTENDED, skip
         □ Breakout column turned red? → INVALID, remove from list
```

---

## Part 5 — Gap Scenarios

### Nifty opens flat (±0.5%)
**Before entry:** Best scenario. Stock opening flat at pivot = controlled. Enter if all checkboxes still green.
**During hold:** Ignore. Flat Nifty = no information for a 7–60 day swing. Check only your stop and the stock's individual action.

---

### Gap Up — Nifty or stock opens >1.5% above previous close

**Before entry (Gap up on your entry day):**
- Gap 1–3%: Still valid if Dist Pivot% is still ≤ +3%. Enter near open.
- Gap >3%: Extended. Do not chase.
  - Option A: Wait for stock to pull back to Dist Pivot% ≤ +2% (may take 2–5 days).
  - Option B: Remove from entry list if it never pulls back.

**During hold (already in position):**
- Market gap up: Ride it. Trail your stop up (see Part 6).
- Stock-specific gap up on news: Consider booking 1/3 of position if gain already > +10%.
- Never exit full position on gap up — let price reach the +20% target.

---

### Gap Down — Nifty or stock opens >1.5% below previous close

**Before entry (Gap down on your planned entry day):**
- Check dashboard Market Overview tab first: What is the Regime? BEAR? → Stand aside entirely.
- Gap down and stock falls below pivot (Dist Pivot% goes negative): CANCEL entry. Setup invalidated.
- Gap down but stock holds above pivot by close: entry still valid — this shows the stock is STRONGER than the market.

**During hold (already in position):**
- Gap down does NOT breach your stop (entry – 7%): HOLD.
  - Check: Is volume high today? High volume gap down = distribution = early exit consideration.
  - Low volume gap down = market noise. Hold.
- Gap down BREACHES your stop: EXIT at open. No exceptions. No averaging down.
  - The pivot that defined this trade is broken. The thesis is gone.

---

## Part 6 — Hold Management (7–60 Days)

### What to check daily (open dashboard once after 3:30 PM, 2 minutes)
```
□ Price above entry stop (entry price – 7%)? → If NO: exit immediately
□ Target hit (+20% above entry)? → If YES: exit
□ Market Overview tab → Regime still BULL / NEUTRAL? → If BEAR: exit all positions
```

### What to check weekly (Sunday evening, 10 minutes)
```
□ Trend column still green in scanner for your held stocks?
□ Stage2 column still green? (still in advancing phase?)
□ S2 Age(d): has it crossed 60 days (red)? If yes, watch closely for Stage 3 signs.
□ Volume pattern for the week: more green days than red days?
□ Earnings in the next 2 weeks? → Plan exit BEFORE earnings if gain < +15%
□ Regime still BULL?
```

### What you correctly identified as NOT needed during hold
```
❌ Daily sector performance — you are already in the individual stock.
   Sector monitoring matters at ENTRY (is the sector rising?).
   During hold, sector daily noise = irrelevant for a 7–60 day swing.

❌ Nifty intraday movement — you are an EOD trader.
   Open the dashboard once per day at 3:30 PM. Not during market hours.

❌ Other scanner hits while in a position — stay focused on what you own.

❌ RS column changing day to day — RS is a 63-day rolling measure.
   It doesn't change meaningfully from one day to the next.

❌ Short-term oscillators (RSI, MACD) — they cycle multiple times in 60 days.
   Meaningless for your holding period.
```

### Early exit signals (before stop or target)
```
1. Trend column turns RED for 3 consecutive days → structure breaking down → exit next open
2. High volume red candle (Dist Pivot% going significantly negative on high vol) → exit
3. Regime flips to BEAR (Market Overview tab) → exit ALL positions at next open
4. Earnings within 2 trading days AND gain < +15% → exit before earnings
5. Stage2 column turns RED (stock exited Stage 2) → exit next open
```

### Trail stop rules (protect profits)
```
Gain reaches +10% → move stop from –7% to breakeven (0%)
Gain reaches +15% → move stop to +5% (lock in profit)
Gain reaches +20% → at target: EXIT full position
               OR: if stock showing unusual strength, move stop to +12% and let it run
```

---

## Part 7 — Complete Entry Checklist (fill out before every trade)

```
ENTRY CHECKLIST
□ Scanner preset used: _________________________
□ Stock symbol: ________________________________
□ Signal date (Day 0): _________________________
□ Dashboard columns on entry day:
    Trend:    ✅ / ❌
    Stage 2:  ✅ / ❌    S2 Age(d): _______ (green < 20 / orange < 60 / red > 60)
    VCP:      ✅ / ❌
    Breakout: ✅ / ❌
    Liquid:   ✅ / ❌
    Quality:  ✅ / ❌
    RS:       ✅ / ❌
    Score: _______  (must be ≥ 70 for entry)
    Dist Pivot%: _______ (must be ≤ +3%)
    Dist 52w%:   _______ (must be ≥ –25% for Minervini)
□ 2-day wait completed? YES / NO  (Breakout Day / Darvas: skip this)
□ Nifty regime (Market Overview tab): BULL / NEUTRAL (not BEAR)
□ Earnings in next 10 trading days? YES → skip / NO → proceed
□ Entry price: _______
□ Stop loss: _______ (entry – 7%)
□ Target: _______ (entry + 20%)
□ Position size: _______% of portfolio
```

---

## Review Log
*(Add a row every time you update a rule based on real trading experience)*

| Date | Signal / Rule changed | Old rule | New rule | Why changed |
|---|---|---|---|---|
| 2026-06-01 | Initial draft | — | — | First version |

---
