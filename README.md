# resell-toolkit

Local, boring tools for an electronics reselling side business: sourcing in China (Xianyu/Taobao
via Superbuy) and selling in Germany (Kleinanzeigen, eBay, Vinted). Everything runs on your machine.
The only data store is a SQLite file. Your workbook is never written to.

## Setup on Windows (one click)

Double-click `windows/Install-ResellToolkit.bat`, or paste this into PowerShell:

```powershell
irm https://raw.githubusercontent.com/dom1911k/flipping_scout/claude/gracious-dirac-39brnb/windows/install.ps1 | iex
```

It installs Git, Python 3.12 and ffmpeg (via winget), downloads the toolkit to `%USERPROFILE%\ResellToolkit`,
installs its packages, asks for your keys (`.env`, opened in Notepad) and your sales sheet, adds three desktop
shortcuts (**Resell Toolkit**, **Resell Toolkit Update**, **Resell Terminal** for `resell` commands) and schedules the Sunday eBay scan. Running it
again only adds what's missing.

**Updating:** the **Update** tab in the web page, the **Resell Toolkit Update** shortcut, or `resell update`.
It pulls the latest version of this branch from GitHub and reinstalls packages when they changed. `data/`,
`.env` and `data/config.local.yaml` are never touched. Put your own settings (shipping rate, exchange rate,
targets) in `data/config.local.yaml` with the same keys as `config.yaml`; they override it and survive updates.

## Setup (manual, any OS)

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

## Phase 4 (first part): category research

This is the core feature: evaluating NEW product categories on your five criteria before you spend money.

```bash
resell research                      # compare all candidates: ✓/~/✗/? per criterion, verdict, next step
resell research vita-oled            # one candidate: findings with sources, prices, profit estimate, next steps
pbpaste | resell research-paste vita-oled --source terapeak     # EU sold prices from a Terapeak/eBay copy-paste
resell research-cn vita-oled "「psv1000 破解 64g」 ¥680"           # a Xianyu asking price (share text or number)
resell research-add "Nintendo DS Lite IPS"
resell serve                         # "Research" page (first tab): same, with paste boxes and forms
```

**Candidates** start from `resell/content/candidates_seed.yaml`. That holds your two benchmarks
(N3DS, iPod Classic) and five candidates from the brief, each with findings from a first web
research pass and a source link per finding. After that they live in the database, edited from the
web page, and `resell import` never touches them.

**Evidence and scoring**
1. **Discontinued:** what you enter (production end and source).
2. **Not hype:** the 12-month trend of EU sold prices. A least-squares slope over the last 12 months
   needs ≥6 dated sales across 6+ months; older data compares year on year. It uses the same
   thresholds as the calculator (≤ −10% fails), or you can set `hype` by hand.
3. **China edge:** modded / refurbished pass, arbitrage is borderline, none fails.
4. **Testability:** easy / medium / hard.
5. **Profit:** the Phase 1 calculator at the median Xianyu price and the median EU sold price
   (last 90 days if there are ≥3, else last 12 months). New categories include import VAT and a 5%
   returns reserve. It also gives the max RMB to pay. For categories you already sell
   (`own_sales_match`), your real last-12-month profit is used instead.

**Getting prices in:**
- **EU sold prices:** copy the results in Terapeak (Seller Hub → Research → Sold) or an eBay.de
  "Verkaufte Artikel" search, and paste. The parser reads tab-separated tables row by row, and
  eBay-style listings as title / price / "Verkauft <date>" blocks. It handles € / $ / £, and
  German and English dates. The web page shows a preview before saving.
- **Xianyu prices:** paste share texts (one per line) or type a price.

**Next steps** list what's missing, most useful first: e.g. "paste Terapeak for X (have 3 sales,
need ≥10 over 12 months)", "add 3–5 Xianyu prices", "all five pass → send the seller checklist and
do a 1–3 unit test buy". Ticking "new devices" shows an ElektroG warning.

**Known limits**
- The paste parser was written without a real Terapeak sample. If your paste reads wrong, send me
  one and I'll adapt it.
- USD/GBP prices are converted at the rough rates in `config.yaml → research.fx_to_eur`.
- Xianyu prices are asking prices, not sold prices, so expect to pay a little less.

## Phase 4d: Xianyu screen recordings → scored listings

```bash
# ANTHROPIC_API_KEY=... in .env (or the environment): recordings are read with Claude vision
resell record ~/Movies/xianyu_shop.mp4 --label 狼黑杂货铺             # Xianyu storefront or search grid
resell record ebay_sold_psp.mp4 --kind ebay_sold --candidate psp      # eBay "Verkaufte Artikel" / Terapeak
resell record ka_search.mp4 --kind kleinanzeigen --candidate psp      # Kleinanzeigen asking prices
```

**What it does:**
1. Pulls frames from the video with ffmpeg: 1 per second, near-identical frames dropped, scaled to 1568 px.
   A 12 s clip gives 11 frames.
2. Sends 4 frames per request to Claude vision with a fixed JSON schema. Per card it reads title,
   price (incl. `¥1.88万`), 想要, seller, badges, struck-through price, model (from title *and* photo,
   so placeholder titles like `没了` still get identified), product class and "likely discontinued".
3. Merges the same card seen in overlapping frames.
4. Sets **trap flags:**
   - `multi_sku`: 价格不一 / 多款 / 都有 / 可选 / 私聊 / ranges like `16到128g`, `64g/128g`.
   - `wanted_ad`: 收 / 回收 / 求购. These are excluded.
   - `service`: 寄改 / 改装费 / 代刷 / 代改 / 教程. Bare `代` is NOT used, because 3代 / 二代 mean "generation".
   - `accessory`.
   - `no_title`.
   - `variant_unclear`: the candidate is a specific variant (dual IPS, modded storage) and the title doesn't say so.
5. Matches listings to candidates through each candidate's `match_terms` regex.
6. Scores each listing in **quick mode:** landed = RMB ÷ rate + €10, and max = (min(sell − €50, sell ÷ 1.5) − €10) × rate.
   The sell price is your sold median (90 days, else 12 months). Without that it falls back to the
   eBay asking median −15%, labelled as an estimate.
   Verdicts: `buy-check`, `close`, `ask` (with the Chinese question for the seller), `too high`, `no EU price yet`.
7. **Discovery:** product types in your classes (handhelds, portable audio, headphones, cameras) that
   look discontinued and aren't candidates yet are added as `new`, together with the listing that
   triggered them.
8. **Sellers:** "likely supplier/modder" (repeats + mostly your stock, or batch/mod wording) vs
   "has your kind of stock" vs one-off. Each recording is compared with that seller's previous one
   (new items, price drops), which replaces Distill.
9. Writes `reports/recordings/<date>_<id>_<label>.md`.

The extraction is cached per video in `data/recordings/<sha256>.json`. Re-running the same video costs
nothing; `--from-json` uses a saved extraction directly.

**Cost:** about 1,600 input tokens per frame. The 12 s sample works out at roughly $0.15, and a 1-minute
recording at about $0.40–0.60. The exact cost of each run is printed in its report.

**Known limits**
- Recording tips: desktop web grid, scroll slowly and steadily, don't zoom.
- Fast scrolling can skip a row entirely. A row is only read if it appears whole in at least one frame.
- `likely_discontinued` is Claude's judgement. Discovery can add a wrong candidate; set it to `dropped`.

## Phase 4b: eBay Browse API (weekly)

```bash
# .env (gitignored) in the project folder:
#   EBAY_CLIENT_ID=...
#   EBAY_CLIENT_SECRET=...
resell ebay-scan                 # all non-dropped candidates, DE + EU-wide
resell ebay-scan -k psp --de-only
```

**What it does:**
- Gets a client-credentials token, then searches `item_summary/search` with `EBAY_DE`, fixed-price + auction, used/refurbished condition IDs,
  and `itemLocationCountry:DE` for the DE scope.
- Fetches up to 1,000 items per candidate and scope, within the candidate's eBay category IDs (devices only). Titles must match the candidate's match regex and contain
  no exclusion term (keeps games, chargers and other models out). Prices come from fixed-price listings;
  auctions only count towards the auction share.
- Stores per run: active count, p25/median/p75, auction share, and every item ID.
- **Probable sales** are items present last week, gone now, whose scheduled end hadn't passed. This is
  only computed when both runs saw every match, and it is an *estimate*, not sold data.
- Responses are cached per day; one run a week is plenty.
- **Weekly scheduling:** cron `0 8 * * 0 cd ~/flipping_scout && resell ebay-scan`, or Windows Task Scheduler.

**Policy:**
- No eBay data is ever sent to Claude. eBay's June 2025 licence restricts feeding API data to generative AI.
- Read the licence's "Restricted APIs" section when you get your keyset.

**Verified live** on 18 Oct 2026 (first scan of all 14 candidates). Also shown on the Research page:
eBay DE asking median, listing count and probable sales over 90 days.

## Phase 2: seller messages, arrival check and seller scorecard

```bash
resell msg N3DS                    # Chinese message + German translation (also: iPod, "Vita card", generic)
resell msg N3DS --zh | pbcopy      # just the Chinese, to the clipboard (Windows: | clip)
resell msg -s serial_unclear       # follow-ups: video, serial_unclear, white_screen, discount, multiple, ...
resell inspect -k N3DS --listing "<goofish link>" --serial "QJF..." --paid 215 --bought 2026-10-20
resell sellers -k N3DS             # scorecard
resell seller-note "小陈电玩" --status preferred --note "fewest major flaws"
resell serve                       # same things as web pages: Seller messages / Arrival check / Sellers
```

**Seller messages** live in `resell/content/messages.yaml`: N3DS, iPod Classic/5th gen, Vita card,
generic, and short follow-ups. Each question notes which Phase 0 problem it guards against. The
N3DS one adds the angled shot for dust and spiderwebs, a readable serial label, JP/US version,
and smoke smell. The web page has copy buttons.

**Arrival check** lives in `resell/content/checklists.yaml`: 9–16 checks per category, each mapped
to a Phase 0 flaw group.
- Mark each check OK / minor / flaw / n/a. "Minor" is only offered where it makes sense.
  Yellow and dust always count as real flaws (`flaw_always_major`).
- **Serials:** the serial is checked against `known_fake_serials` and against every serial already
  in your Sales Sheet and your past inspections.
- **Seller:** paste the Goofish link and the seller is looked up from its storefront code.
- **Checklist answer:** record whether the seller answered your message (answered / partial /
  ignored / not sent).

**Seller scorecard:** for each seller it shows units bought and sold, flaw rate, major flaw rate,
average profit, profit on cost, last purchase, checklist answer rate, storefront codes, and your
status and notes (preferred / ok / watch / avoid).
- It starts from the Sales Sheet, using Phase 0 seller identification.
- An inspection replaces the comment-based flaw flag for the same unit. Units are matched by
  serial, or by category, date and price.
- Inspected units not yet in the sheet count too.

**Storage:** inspections and seller notes are stored in the `inspections` and `seller_notes` tables.
`resell import` never drops them. The storefront map is rebuilt on every import.

**Known limits**
- Inspections are linked to sheet rows by serial, or by category + buy date + price (±€2.50).
  Enter a serial or the buy date and price so the link works.
- The checklist answer rate only counts inspections you record from now on. There is no history for it.

## Phase 3: Kleinanzeigen reply helper

```bash
resell replies-init                 # copies example fact sheets + profile to data/ (gitignored) - edit them
resell reply -l n3ds-dual-ips "Moin, am Preis ist nichts mehr machbar? Gruß Niclas"
resell reply -l ipod-classic-512 --last 210 "Wären 190 € bei Abholung heute machbar?"
pbpaste | resell reply -l n3ds-dual-ips -      # message from the clipboard
resell espanso > ~/.config/espanso/match/kleinanzeigen.yml   # text-expander snippets (:ka-verf, :ka-preis, ...)
resell serve                        # "Replies" page: paste -> draft -> copy
```

**Your files** (both gitignored, in `data/`):
- `factsheets.yaml`: one entry per listing type.
  - Asking price, `floor_pickup`, `floor_shipped`, shipping (offered / cost / included), `fixed_price`.
  - Condition, what you tested, what's included, specs.
  - `article` (die/der/das, so the grammar fits).
  - `faq`: keyword → your own answer.
  - `sold` + `alternative`, for "die ist leider weg, aber ich hab eine sehr ähnliche…".
- `seller_profile.yaml`: your name for the sign-off, area, pickup address and PayPal address.
  These are only put into a draft when the conversation calls for them: the address once a clock
  time is agreed, PayPal when the buyer asks to pay with PayPal Waren & Dienstleistungen.

**How a reply is built**
1. **Intents** are detected by keyword (German and English, whole words): availability, price,
   shipping, pickup, payment, specs, condition, included, trade.
   It also extracts the buyer's offer (ignoring GB, mAh, times and dates), pickup vs. shipping,
   a proposed time, and the buyer's name from their sign-off.
2. **The price decision is rule-based** and lives in `config.yaml → replies.negotiation`.
   - The first answer to "what's your best price?" is asking minus ~7%, rounded to 5 (300 → 280).
   - An offer at or above your price is accepted.
   - Otherwise you meet in the middle. An offer within €5 of that counter is accepted.
   - An offer below the floor gets "X würde ich noch machen, aber das wäre wirklich das Letzte".
   - Below 80% of the floor is a lowball: a polite no plus your floor.
   - Fixed-price listings answer "Festpreis", and offer free shipping if you ship.
   - **Nothing ever goes below the floor.** Pass `--last` (or fill "Your last counter") so it
     knows where the haggling stands.
3. **Scam and risk flags:**
   - Hard flags, which get a polite decline and no personal details: courier pickup, links in the
     chat, moving to WhatsApp/phone/email, fake "payment sent / confirm here" messages, and
     buyers abroad or buying for a relative.
   - Caution only: a buyer paying with *PayPal Freunde*. That's a risk for the buyer, not for you,
     apart from a hacked account reversing the payment. Fine for pickup; for shipping prefer
     Waren & Dienstleistungen or "Sicher bezahlen".
4. **The draft** comes from snippets in your tone (`resell/content/replies.yaml`).
   - Spec questions use your fact sheet's FAQ first. Anything it can't answer becomes
     **[ANTWORT ERGÄNZEN]**, plus a note.
   - **Optional:** with `ANTHROPIC_API_KEY` set and `pip install -e ".[llm]"`, tick "Word it with Claude".
     Claude rewords the draft using your past replies (`resell/content/tone_examples.yaml`) as style
     examples. It gets the price decision as a fixed rule.
   - If its draft mentions any price the rules didn't allow, the template is used instead. The
     same happens if it's too long, the API errors, or it declines.
   - Scam messages never go to the LLM.

**Known limits**
- Keyword intents miss unusual phrasing. When nothing matches, it treats the message as an
  availability question. Check the intent chips.
- It doesn't know your calendar: a proposed time gets "passt bei mir" plus a note to check.
- One message at a time. Earlier chat history only enters through "your last counter".

## Tests

```bash
pytest
```

The tests use made-up rows only.
