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

## Tests

```bash
pytest
```

The tests use made-up rows only.
