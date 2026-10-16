# resell-toolkit

Local, boring tools for an electronics reselling side business: sourcing in China (Xianyu/Taobao
via Superbuy) and selling in Germany (Kleinanzeigen, eBay, Vinted). Everything runs on your machine.
The only data store is a SQLite file. Your workbook is never written to.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
mkdir -p data && cp ~/Downloads/Orders_and_cost_breakdown.xlsx data/
```

`data/`, `reports/`, `*.db` and `*.xlsx` are gitignored, so your numbers never end up in git.

## Phase 0: import and analyse the sheet

```bash
resell import     # workbook -> data/resell.db (tables: sales, purchases, parse_issues)
resell report     # import + reports/phase0/report.md with PNG charts
```

**What it does**
- **Sales Sheet** → `sales`, one row per unit. Rows without a Price Sold or selling date are stock.
  For each row it derives:
  - `category` from regex rules in `config.yaml`
  - `grade`, the number after the colour ("white 98")
  - `flawed`, `flaw_minor`, `flaw_tags`, `flaw_matches` from keywords in the item name and comment
  - `dif_seller`, `platform`/`account` from Selling Place, and `days_to_sell`
  - `seller` with a `seller_confidence`
- **Order #N** tabs → `purchases`.
  - Each header row starts a new block.
  - Tabs without headers (Order #6/#7) use the link | item | date | qty | RMB | EUR | fee | ship | total layout.
  - Effective rate = unit RMB × qty / line EUR.
  - Repair parts are tagged `kind = part`.
  - Blocks that repeat an earlier block with other prices (what-if copies) are excluded.
  - Side tables, unlabelled numbers and nameless rows go to `parse_issues` instead of being guessed.
- **Matching:** order lines are matched to sales rows by category, price (±€2.50 per unit), date and
  name similarity.
- **Seller identification**, in order of confidence:
  1. A shop name in Source.
  2. A Goofish `userId` link.
  3. The storefront code inside Goofish links (`spm=a21ybx.personal.feeds.N.XXXX`, the 4 hex chars).
     These are learned from rows that have both a name and a link, and only kept when ≥80% of those
     rows agree.
  4. Since `dif_seller_convention_since`, an N3DS without "dif seller" is attributed to `main_seller`
     (confidence `inferred`).
  5. Otherwise the listing ID.
- **Report contents:**
  - your stated numbers vs the sheet
  - per-category profit, days to sell and flaw rate
  - N3DS flaw trend and flaw types
  - grade vs flaw rate with a Fisher exact test
  - flaw price impact, excluding bulk sales
  - serial prefixes and duplicates
  - seller scorecard
  - buy/sell price per quarter and effective RMB/€ per month
  - stock aging, platform mix and eBay per-account revenue vs your cap
  - inconsistencies, order lines without a sales row, the full flaw-keyword review and the parse log

  If `reports/phase0/findings.md` exists (a hand-written summary), the report links to it.

**Tuning:** everything is in `config.yaml`: category rules, flaw keywords, minor qualifiers and
negations, stock-aging days, platform names, main seller and the eBay cap. To correct a single
row's flaw flag, add `flaw_overrides: {<sheet row>: true|false}`.

**Known limits**
- Flaw detection is keyword-based. Check report §9, which lists every flagged row *and* every
  unflagged comment.
- Storefront codes are a heuristic. One seller can have several codes, and a code seen only once is
  weak evidence.
- Name/price drift between the Order tabs and the Sales Sheet leaves some lines unmatched. They are
  listed in report §8, not guessed.
- The 2024 Japan (Yen) orders have no Sales Sheet rows, so they only appear in `purchases`.
- `costs:` in config.yaml are placeholders for Phase 1. eBay private-seller fees haven't been looked up yet.

## Phase 1: landed-cost and buy calculator

```bash
resell maxbuy -k N3DS -m 98                 # most RMB to pay, using your own sales of "…98" units
resell maxbuy -k iPod -m 1T --profit 80     # override a target
resell calc 1580 -k N3DS -m 98              # full breakdown + 5-criteria verdict for one listing
resell calc 900 -k Minidisc --sell 180      # new category: import VAT is on by default
resell serve                                # web page at http://127.0.0.1:8765
```

Run `resell import` first, so the calculator can use your sales history.

**Landed cost per unit** (everything configured under `costs:` in config.yaml):
- RMB ÷ your effective rate. The config value always wins. Your last-60-days order median and the
  live ECB rate are shown next to it as hints.
- Agent fee.
- International shipping: weight × €/kg, with a minimum. The weight comes from the category or `--weight`.
- Customs: since 1 Jul 2026, consignments ≤ €150 pay a flat €3 per tariff line. This is shared
  across `--qty` units of the same kind. Above €150, the category's `duty_rate` applies (default 0%).
- Import VAT at 19% on goods + shipping + customs. It is **off** for the categories you already buy
  (your order sheets show no EUSt) and **on** for anything new. Force it with `--vat/--no-vat`.
- Repair/upgrade parts.

**Sell side:**
- Platform fee: private eBay.de and Kleinanzeigen sales have no seller fee, and Vinted's fee is paid
  by the buyer. These were checked Oct 2026 and are kept in config so they're easy to update.
- Shipping to the buyer.
- A returns/defect reserve, as a % per category.

The sell price defaults to the median of your own sales: last 12 months, lost units and bulk sales
excluded. `--match` narrows that to item names containing the text.

**Outputs:**
- profit, profit on cost, margin on revenue and break-even sell price
- the 5-criteria score, with PASS / BORDERLINE / FAIL and the reason for each:
  1. discontinued (from `category_profiles`)
  2. not hype: your 12-month sell-price trend, clean units only, or `hype: true`
  3. China edge (modded/refurbished pass, arbitrage borderline)
  4. testability, downgraded to borderline if more than 40% of your units arrived flawed
  5. profit ≥ €50 and ≥ 50% on cost; 40–50% on cost is borderline; cheap fast items have their own lower bar

**Reverse mode** finds the highest RMB price per unit that still meets both the profit and
profit-on-cost targets at the expected sell price, and says which target set the limit.

**The web page** is a single page, with "Max price" and "Check a listing" modes. It updates as you
type and accepts prices pasted as `¥1,580` or `1580元`. It uses HTMX from unpkg, and without it the
form still works as a normal page. It binds to 127.0.0.1 only.

**Known limits**
- The hype trend uses only your own sales. Categories you haven't sold show UNKNOWN until Phase 4
  adds EU sold data.
- Customs duty rates per tariff code aren't looked up. Set `duty_rate` per category if one applies.
- Shipping is a flat €/kg estimate. Calibrate `shipping_eur_per_kg` against a few Superbuy parcels.

## Tests

```bash
pytest
```

The tests use made-up rows only.
