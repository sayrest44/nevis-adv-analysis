# Nevis: RIA market analysis from SEC Form ADV data

Take-home analysis for Nevis. It turns six years of SEC data on registered investment advisers (RIAs), one snapshot each September from 2021 to 2026, into a clean firm-year table. It then answers four questions:

1. How many RIAs fall in each AUM segment (<\$1B, \$1B–\$10B, \$10B–\$50B, >\$50B)?
2. Who are the fastest-growing firms over 1, 3 and 5 years?
3. Which firms are capacity-strained (highest AUM per advisor)?
4. Which firms appear under-staffed (AUM growing faster than advisor headcount)?

**This README is the single reference for methodology and assumptions.** The answers are in the notebook.

---

## Contents
1. [Where to find what](#1-where-to-find-what)
2. [Repository map](#2-repository-map)
3. [Data sources](#3-data-sources)
4. [Methodology](#4-methodology)
5. [Assumptions and limitations](#5-assumptions-and-limitations)
6. [Parameters](#6-parameters)
7. [Outputs: data dictionary](#7-outputs-data-dictionary)
8. [How to run](#8-how-to-run)
9. [How AI was used](#9-how-ai-was-used)

---

## 1. Where to find what

| If you want to… | Go to |
|---|---|
| Read the answers quickly | **`nevis-adv-analysis.ipynb` → Executive summary** (top of the notebook) |
| See full rankings, charts and commentary | `nevis-adv-analysis.ipynb` → Sections 5–8 |
| Understand **how** anything was calculated | This README → [§4 Methodology](#4-methodology) |
| See **every assumption and limitation** in one list | This README → [§5 Assumptions and limitations](#5-assumptions-and-limitations) |
| Find a threshold (e.g. the \$100M growth floor) and where it is set | This README → [§6 Parameters](#6-parameters) |
| Know what a column in an output file means | This README → [§7 Outputs](#7-outputs-data-dictionary) |
| Check how raw SEC columns map to each field, file formats, schema differences | `docs/field_mapping.md` |
| See data-quality counts per year (missing values, imputations, stale filings) | `nevis-adv-analysis.ipynb` → Section 3 |
| Use the data without running code | `outputs/` (all files are pre-generated) |
| Read or change the parsing / cleaning code | `adv_pipeline.py` |
| Check that the cleaning rules work | `tests/test_adv_pipeline.py` |
| See how AI tools were used | This README → [§9 How AI was used](#9-how-ai-was-used) |
| Read the original brief | `docs/Nevis - Data Analysis Hometask (Deployment).pdf` |

## 2. Repository map

| Path | Contains | Read it for |
|---|---|---|
| `README.md` | Methodology, all assumptions and limitations, parameters, output data dictionary, run instructions, AI usage | The canonical "how and why" |
| `nevis-adv-analysis.ipynb` | Executed notebook. It opens with an executive summary that answers every question and states the scope and key assumptions. Then: data build and validation (§2), data quality (§3), the firm table (§4), segments (§5), Q1–Q3 (§6–8), with charts and commentary | The answers and the evidence |
| `adv_pipeline.py` | Loading, parsing, cleaning, firm-type classification and growth helpers used by the notebook. All classification thresholds live at the top of this file | The implementation |
| `tests/test_adv_pipeline.py` | Unit tests on small synthetic inputs (parsers, imputation, location/website fallbacks, firm types, stale-filing rule, growth math) | Verification |
| `docs/field_mapping.md` | Raw-data reference: the six source files, formats, schema differences between years, and the column-by-column mapping from raw SEC fields to output columns | Data lineage |
| `docs/Nevis - Data Analysis Hometask (Deployment).pdf` | The assignment | The brief |
| `raw_data/` | The six SEC snapshot files, unchanged | Inputs |
| `outputs/` | Every table the notebook produces, committed so no code needs to be run (see [§7](#7-outputs-data-dictionary)) | Using the data |
| `requirements.txt` | Python dependencies | Setup |

## 3. Data sources

Six SEC *Information about Registered Investment Advisers* snapshots, one per year:

| Snapshot | File | Firms |
|---|---|---:|
| Sept 2021 | `ia090121.csv` | 14,638 |
| Sept 2022 | `ia090122.csv` | 15,362 |
| Sept 2023 | `ia090623.xlsx` | 15,528 |
| Sept 2024 | `ia090324.xlsx` | 15,923 |
| Sept 2025 | `ia09022025.xlsx` | 16,359 |
| Sept 2026 | `2026_IA_SEC_-_FIRM_ROSTER_FOIA_DOWNLOAD_-_34655879.CSV` | 17,149 |

- **Two column layouts:** 262 columns (2021–22) and 448 columns (2023–26). The 262 columns are a strict subset with identical names, so every field is mapped **by name**.
- **The 2026 roster** has the same schema as 2025; only its encoding (cp1252) and date/number formats differ.
- **Firm key:** `Organization CRD#`, unique within every file. It links firms across years: 21,240 distinct firms in total, 11,258 present in all six years.

File formats, schema differences and the full raw-column mapping are in `docs/field_mapping.md`.

## 4. Methodology

### 4.1 Building the firm-year table (`adv_pipeline.build_panel`)

| Step | What happens |
|---|---|
| 1. Load | Each file is read with every value as text. CSVs are tried as UTF-8, then cp1252. Required columns are checked by name |
| 2. Standardize | Each snapshot is mapped to one schema ([§4.2](#42-field-definitions)). Numbers are parsed from three formats (`"1,234.00"`, `".00"`, plain), dates from three (m/d/yyyy, Excel serial, ISO) |
| 3. Stack | The six years are stacked into one table with one row per firm per year (94,959 rows). The notebook asserts that CRD is unique per year |
| 4. Clean | Fallbacks for missing location, website and SEC#; 5C(1) error handling ([§4.3](#43-cleaning-rules)) |
| 5. Derive | Ratios (AUM per client / advisor, clients per advisor), individual-AUM share, stale-filing flag, AUM segment, firm type ([§4.4](#44-firm-types-and-why-the-rankings-focus-on-wealth-focused-rias)) |
| 6. Validate | Hard assertions in notebook §2.2: expected row counts, unique firm-years, discretionary + non-discretionary AUM = total AUM, advisors ≤ employees, no negative values, all filing dates parsed, SEC# format |

### 4.2 Field definitions

Every required field and how it is defined. The raw-column detail is in `docs/field_mapping.md`.

| Field | Output column | Definition |
|---|---|---|
| SEC# | `sec_number` | `SEC#` as filed (`801-NNNNN`) |
| Firm name | `firm_name` | `Primary Business Name`. Tables show a readable version ("Mercury Advisory" instead of "MERCURY ADVISORY, LLC"); the CSVs keep the raw name |
| Website | `website` | `Website Address`, reduced to the host (e.g. `www.beaconpointe.com`) |
| City / State | `city`, `state` | Main office, with fallbacks ([§4.3](#43-cleaning-rules)) |
| AUM (USD) | `aum` | Regulatory AUM, Form ADV Item 5F(2)(c) |
| Number of clients | `clients_raum` | Sum of Item 5D client counts by client type. Each "fewer than 5 clients" checkbox counts as **2** (midpoint of 1–4) |
| Number of clients (incl. non-AUM) | `clients_total` | `clients_raum` + Item 5C(1), clients advised without managed assets (e.g. planning-only) |
| Average AUM per client | `aum_per_client` | `aum / clients_raum`. Blank when there are no clients |
| Number of employees | `employees` | Item 5A |
| Number of advisors | `advisors` | Item 5B(1): employees performing investment advisory functions, research included. Form ADV has no "advisor" field; this is the standard proxy |
| AUM per advisor | `aum_per_advisor` | `aum / advisors`. Blank when there are no advisors |
| Filing date / year | `filing_date`, `year` | `Latest ADV Filing Date` and the snapshot year |
| Individual AUM share | `pct_aum_individuals` | AUM from individuals + high-net-worth individuals (Item 5D(a)(3) + 5D(b)(3)) ÷ AUM |
| Segment | `segment` | AUM band, half-open: [0, \$1B), [\$1B, \$10B), [\$10B, \$50B), [\$50B, ∞) |

### 4.3 Cleaning rules

| Issue in the raw data | Rule |
|---|---|
| AUM = 0 (~630–690 firms/yr; pension consultants, new registrants) | Kept. They fall in the <\$1B segment and are excluded from growth by the \$100M floor |
| Advisors = 0 while AUM > 0 (~90–120 firms/yr) | AUM per advisor left blank, so these firms never top a ranking through division by zero |
| "Fewer than 5 clients" checkbox instead of a count (~8–9k per year) | Counted as 2 clients per client type |
| Item 5C(1) entry errors (e.g. a firm's AUM typed into the client-count box) and legacy text bands ("11-25") | Set to blank and flagged (`clients_nonraum_invalid`) when the value equals AUM, exceeds 100M or isn't numeric |
| Stale filings: the latest ADV is more than 15 months older than the snapshot, so the firm missed its annual update and AUM is a repeat (41–70 firms/yr, ~0.4%) | Flagged (`is_stale`) and excluded from growth and ratio rankings. The annual amendment is due within 90 days of each firm's fiscal year-end, so a filing up to 15 months old is current. For example, a December filing from a firm with a September year-end is current, not stale |
| City/state blank because SEC suppresses private-residence main offices | Filled from the mailing address, then the books-and-records location (2023+), then the same firm in the nearest other year. The source is recorded in `location_source`. ~3.5% of 2026 firms still have no city |
| Website is a social-media profile (the file holds only one of several URLs) | Replaced with the firm's real website from another year where available; otherwise kept and flagged (`website_is_social`) |
| SEC# blank (1 firm in 2026) | Filled from the same firm in another year |

### 4.4 Firm types, and why the rankings focus on wealth-focused RIAs

**Why focus on wealth-focused RIAs (for now):**
- **Buyers:** Nevis sells an AI platform to wealth managers, firms whose advisors look after individual clients.
- **Meaningful metrics:** capacity measures such as AUM and clients per advisor only signal "strain" in an advice business. A fund manager with \$1B per advisor and two clients is not capacity-strained.
- **Look-alikes:** some firms look like wealth managers in the data but aren't advisor practices:
  - digital platforms serve clients through technology;
  - investment managers used by RIAs serve them through other advisers.

  These are partners, channels or competitors rather than buyers of advisor-productivity tools.
- **Nothing is discarded:** every ranking also has an all-RIA list, and every firm keeps its type flags, so the scope can be widened (e.g. to hybrid advisors or institutional consultants) by changing one filter.

Every firm gets exactly one type. The rules are applied in this order:

| Type | Rule | 2026 firms | 2026 AUM |
|---|---|---:|---:|
| `digital_platform` (excluded) | More than **1,000 clients per advisor**. No human advisor can personally serve 1,000+ clients; the 99th percentile of individual-focused firms is ~775. Examples: Betterment, Acorns, Wealthfront, Human Interest, AssetMark | 115 | \$7.3T |
| `investment_manager` (excluded) | At least 50% individual AUM, **but** offers no financial planning (Item 5G(1); no planning clients in 5H) **and** does not select other advisers (5G(7)), **and** either (a) at least **25% of AUM** managed as portfolio manager in other firms' wrap programs (5I(2)(b)), or (b) more than **200 clients per advisor** with at least **10 advisors** and at least **\$250K AUM per client**. These are direct-indexing, SMA and model providers used *by* RIAs (Quantinno, Vise AI, Aperio, Breckinridge, Vestmark), which report the end investors in other advisers' programs as their own clients | 52 | \$450B |
| `wealth_focused` (the focus) | At least **50% of AUM** from individuals and high-net-worth individuals (Item 5D(a)+(b)), and not one of the types above | 9,062 | \$23.4T |
| `<50% individual AUM` | Everything else: mostly fund, ETF, pension and other institutional managers | 7,920 | \$145.8T |

**Why the investment-manager rule's branch (b) needs all three conditions.** Client load alone (more than 200 per advisor) flagged 164 firms, including real advisory firms:
- mass-affluent and bank-channel advisers with small accounts (e.g. Nest Egg, ~\$47K per client);
- one-person investment-counsel boutiques.

The advisor and account-size conditions keep those firms wealth-focused. Traditional investment counsel that simply don't offer planning (e.g. Sarofim Trust, Greenhaven) also stay wealth-focused.

**Why family offices are not a separate type.** A family-office type (fewer than 20 clients and at least \$100M) was tested and dropped, because family offices can't be identified reliably from Form ADV:
- **The real ones aren't here.** Single-family offices are exempt from SEC registration (the Family Office Rule), so the "family offices" in this data are multi-family offices and wealth firms. Most are advisor-led wealth managers, which is Nevis's market.
- **The name is branding.** Many firms called "family office" run ordinary practices with hundreds of clients at \$1–5M each.
- **A client-count rule is unstable.** Ultra-wealthy boutiques move across any client-count line as they grow. Gleneagles Investment Advisors had 3 clients in 2021 and 51 (at ~\$60M each) in 2026; a16z Perennial had 18 in 2023 and 73 in 2026.
- **An AUM-per-client rule sweeps in large multi-family offices** (Pathstone, BBR, Tiedemann) with dozens to hundreds of advisors.

The real distortion, AUM per advisor driven by a handful of very large relationships, is handled in the capacity ranking by its 20-client floor ([§4.5](#45-analysis-methods)).

### 4.5 Analysis methods

**Segments:**
- latest-year (2026) AUM, in the half-open bands above;
- growth tables group firms by their 2026 segment.

**Q1 – Fastest growing:**
- **Metric:** AUM CAGR (compound annual growth rate) from the start snapshot to 2026, over 1 year (2025→26), 3 years (2023→26) and 5 years (2021→26). Total growth over the window is also shown. The growth summary (median, 90th percentile, eligible firms) is reported separately for wealth-focused RIAs and for all RIAs.
- **Eligible firms:**
  - present in both years;
  - at least \$100M AUM at the start;
  - current (non-stale) filings at both ends;
  - AUM above zero at the end.
- **Consistent growers:** firms in the top quartile of their segment's AUM CAGR in all three windows. For the wealth-focused list, quartiles are calculated among wealth-focused peers only.
- **M&A flag:** `consolidation_flag` marks firms that became umbrella filers, added relying advisers or report acquired firms. It is shown, not used to exclude firms.

**Q2 – Capacity-strained:**
- **Metric:** AUM per advisor in 2026.
- **Eligible firms:** at least 2 advisors and a current filing. The **wealth-focused list also requires at least 20 clients**, so high AUM per advisor reflects a client book rather than a few very large relationships.
- **Context shown:** clients per advisor alongside, and medians by firm type.
- **Sensitivity check:** the ranking is recomputed with Item 5B(3) (state-registered investment adviser representatives) as the advisor count. The rank correlation is 0.92–0.96 by segment for wealth-focused firms (5–8 of each top 10 unchanged), and 0.79–0.95 across all RIAs.

**Q3 – Under-staffed:**
- **Metric:** growth gap = AUM CAGR − advisor CAGR, over the same three windows.
- **Eligible firms:**
  - at least \$100M AUM and at least 2 advisors at the start;
  - at least 1 advisor at the end;
  - current filings at both ends.
- **Under-staffed:** gap > 0 and AUM CAGR > 0.
- **Material gap:** at least 10 points a year. Markets alone lift AUM ~10% a year while headcount is sticky, so "any gap" covers 68–80% of all RIAs and 78–91% of wealth-focused firms. Shares are reported separately for both universes.
- **Persistently under-staffed:** a material gap in all three windows.

**Ranking presentation:** each question shows the top 10 wealth-focused firms per segment first, then the top 5 across all RIAs. The CSVs hold the top 10 for both.

## 5. Assumptions and limitations

This is the complete list. The notebook's executive summary repeats the most important ones.

**Data and definitions**
1. **Universe.** SEC-registered investment advisers only. State-registered advisers (mostly under \$100M AUM) and exempt reporting advisers are not in these files, so the <\$1B segment understates the long tail of very small RIAs.
2. **Timing.** Six September snapshots. AUM is self-reported as of each firm's fiscal year-end (mostly the prior December) and filed annually, mostly in March. Growth therefore includes market returns, not only net new money.
3. **AUM** means regulatory AUM (Item 5F(2)(c)), as defined by Form ADV.
4. **AUM totals double-count some assets.** RAUM is reported firm by firm, so assets one adviser manages as a sub-adviser for another are counted by both; at least \$3.3T in 2026 is explicitly reported as wrap sub-advisory. Firm counts and per-firm metrics are unaffected, but market-wide totals and the segments' shares of AUM are somewhat overstated.
5. **Advisors are a proxy.** Item 5B(1) counts everyone doing investment advisory work, research included, so it overstates client-facing advisors at institutional managers. The 5B(3) sensitivity check keeps the results: rank correlation 0.92–0.96 for wealth-focused firms (0.79–0.95 across all RIAs), though some individual top-10 names change.
6. **Clients are approximate.**
   - "Fewer than 5" checkboxes are counted as 2.
   - Fund complexes and platforms report millions of underlying investors.
   - `clients_total` adds Item 5C(1) clients with no managed assets; obvious 5C(1) entry errors are blanked.
7. **Segments** use latest-year AUM. The 629 firms with zero AUM fall in <\$1B.

**Classification and eligibility**

8. **Firm types are rule-based.** They come from self-reported Form ADV answers with judgment-call thresholds ([§4.4](#44-firm-types-and-why-the-rankings-focus-on-wealth-focused-rias)), set where the data shows natural breaks. Known edge cases:
   - Edelman Financial Engines (human planners plus a large 401(k) advice business) is excluded by the 1,000-client platform rule;
   - asset managers that also report planning (e.g. GW&K) stay wealth-focused;
   - family offices are not separated (see [§4.4](#44-firm-types-and-why-the-rankings-focus-on-wealth-focused-rias)): ultra-wealthy boutiques such as Gleneagles stay wealth-focused, and the 20-client floor keeps the few-client extremes out of the capacity ranking.

   Schedule D and the Part 2 brochures, which aren't in this data, would refine the rules.
9. **Eligibility floors** keep tiny bases and a handful of large relationships from producing meaningless ratios: \$100M start AUM for growth, 2 advisors for ratios, and 20 clients for the wealth-focused capacity ranking. They also mean the smallest firms are not ranked.
10. **Stale filings** (more than 15 months older than the snapshot, ~0.4% of firms a year) are excluded from growth and ratio rankings, because their AUM is a repeat.
11. **The materiality threshold** for under-staffing (10 points a year) is a judgment call that separates real gaps from market drift.

**Interpreting growth and staffing**

12. **Inorganic growth.** M&A and umbrella consolidation inflate growth. `consolidation_flag` catches only part of it (~5% of the top-list firms are flagged): an acquisition structured as a new registration or an asset transfer doesn't show. The 1-year leaders in particular show multi-fold jumps that usually mean an acquisition or re-registration.
13. **Survivorship.** Growth is measured only for firms present at both ends of a window. Roughly 700–900 firms exit and 1,000–1,700 enter each year.
14. **Headcount changes can be re-classifications.** A drop in 5B(1) may mean staff moved to an affiliate rather than left. Large single-firm gaps should be checked before outreach.

**Contact fields**

15. **Website** comes from a single URL field; the full list is in Schedule D, which isn't in this data. About 12% of 2026 firms show only a social-media profile.
16. **Location.** Private-residence main offices are suppressed by the SEC. After the fallbacks, ~3.5% of 2026 firms still have no city.

## 6. Parameters

Every threshold, its value, and where to change it.

| Parameter | Value | Defined in | Used for |
|---|---|---|---|
| `SEGMENT_BINS` | \$0 / \$1B / \$10B / \$50B | `adv_pipeline.py` | AUM segments |
| `FEWER_THAN_5_IMPUTE` | 2 | `adv_pipeline.py` | Client-count imputation |
| `NONRAUM_CLIENTS_MAX` | 100,000,000 | `adv_pipeline.py` | 5C(1) entry-error rule |
| `STALE_AFTER_MONTHS` | 15 | `adv_pipeline.py` | Stale-filing rule |
| `WEALTH_AUM_SHARE` | 50% | `adv_pipeline.py` | Wealth-focused threshold |
| `PLATFORM_CLIENTS_PER_ADVISOR` | 1,000 | `adv_pipeline.py` | Digital-platform rule |
| `MANAGER_WRAP_SUBADVISER_SHARE` | 25% | `adv_pipeline.py` | Investment-manager rule, branch (a) |
| `MANAGER_CLIENTS_PER_ADVISOR` | 200 | `adv_pipeline.py` | Investment-manager rule, branch (b) |
| `MANAGER_MIN_ADVISORS` | 10 | `adv_pipeline.py` | Investment-manager rule, branch (b) |
| `MANAGER_MIN_AUM_PER_CLIENT` | \$250,000 | `adv_pipeline.py` | Investment-manager rule, branch (b) |
| `LATEST` | 2026 | notebook §1 | Latest snapshot |
| `HORIZONS` | 1, 3, 5 years | notebook §1 | Growth windows |
| `MIN_START_AUM` | \$100M | notebook §1 | Growth / staffing eligibility |
| `MIN_ADVISORS` | 2 | notebook §1 | AUM-per-advisor and staffing eligibility |
| `MIN_CLIENTS` | 20 | notebook §1 | Wealth-focused capacity ranking (Q2) |
| `MATERIAL_GAP` | 10 points/yr | notebook §1 | Material under-staffing |
| `TOP_N` | 10 | notebook §1 | Firms shown per segment |

## 7. Outputs: data dictionary

All files are written by the notebook and committed. Money is in USD; shares and rates are fractions (0.25 = 25%).

| File | Rows | Contents |
|---|---:|---|
| `ria_panel.zip` | 94,959 | The full firm-year table, 2021–2026, all 47 columns (a zip holding `ria_panel.csv`; pandas reads it directly) |
| `ria_firms_latest.csv` | 17,149 | The same columns for 2026 only |
| `segment_summary.csv` | 4 | Per segment: firms, total and median AUM, median advisors, AUM per advisor and clients, counts by firm type, shares of firms and AUM |
| `q1_fastest_growing.csv` | 240 | Top 10 per segment × window (1y/3y/5y) × universe (wealth-focused / all RIAs) by AUM CAGR |
| `q1_growth_summary.csv` | 24 | Per universe (wealth-focused / all RIAs) × window × segment: eligible firms, median and 90th-percentile AUM CAGR |
| `q1_consistent_growers.csv` | 1,549 | All consistent top-quartile growers: 553 wealth-focused, 996 across all RIAs, with their percentile in each window |
| `q2_capacity_strained.csv` | 80 | Top 10 per segment × universe by AUM per advisor, with clients per advisor |
| `q2_medians_by_firm_type.csv` | 20 | Median AUM and clients per advisor by segment × firm type |
| `q3_understaffed.csv` | 240 | Top 10 per segment × window × universe by growth gap |
| `q3_gap_shares.csv` | 24 | Per universe (wealth-focused / all RIAs) × window × segment: share of firms with any gap, share and count with a material gap |
| `q3_persistently_understaffed.csv` | 2,103 | Firms with a material gap in all three windows (filter on `wealth_focused` for the 1,347 wealth-focused ones) |
| `digital_platforms.csv` | 115 | Firms classified as digital platforms |
| `investment_managers.csv` | 52 | Firms classified as investment managers used by RIAs, with their wrap sub-adviser share |

**Main columns of the firm table** (`ria_panel.zip`, `ria_firms_latest.csv`). Required fields and definitions are in [§4.2](#42-field-definitions).

| Group | Columns |
|---|---|
| Identity | `year`, `crd`, `sec_number`, `firm_name`, `legal_name`, `website`, `city`, `state`, `country` |
| Size and ratios | `aum`, `aum_discretionary`, `aum_nondiscretionary`, `employees`, `advisors`, `advisors_iar` (5B(3)), `aum_per_advisor`, `aum_per_iar`, `clients_raum`, `clients_total`, `aum_per_client`, `aum_per_client_total`, `clients_per_advisor`, `accounts`, `segment` |
| Client mix | `clients_individuals`, `aum_individuals`, `pct_aum_individuals`, `clients_reported` (before imputation), `n_fewer_than_5_flags`, `clients_nonraum` (5C(1)) |
| Firm type | `wealth_focused`, `digital_platform`, `investment_manager`, and the inputs `offers_planning`, `selects_other_advisers`, `aum_wrap_subadviser` |
| Quality flags | `filing_date`, `is_stale`, `clients_nonraum_invalid`, `location_source`, `private_residence`, `website_is_social`, `website_from_other_year` |
| Structure | `sec_status`, `umbrella`, `n_relying_advisers`, `n_acquired_firms` |

## 8. How to run

No code needs to be run to read the results: the notebook is saved with its outputs, and `outputs/` holds every table.

To reproduce:
```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (source .venv/bin/activate elsewhere)
pip install -r requirements.txt
python -m pytest tests -q         # pipeline unit tests
```
Then open `nevis-adv-analysis.ipynb` in PyCharm, VS Code or Jupyter and run all cells (about 1 minute). To execute it headless:
```bash
python -c "import nbformat; from nbclient import NotebookClient; nb=nbformat.read('nevis-adv-analysis.ipynb', 4); NotebookClient(nb, kernel_name='python3').execute(); nbformat.write(nb, 'nevis-adv-analysis.ipynb')"
```
A fresh clone of this repository reproduces every file in `outputs/` byte-for-byte.

## 9. How AI was used

This analysis was built with an AI coding agent (Claude Code), working in the repository under my direction. The aim was to move fast while keeping every number checkable.

**What the AI did:**
- **Read the brief and profiled the raw data:** every row of all six files, to find schema differences, formats and quirks (`docs/field_mapping.md`).
- **Wrote the code:** the parsing and cleaning pipeline (`adv_pipeline.py`), its unit tests, and the notebook's analysis, charts and tables.
- **Investigated questions I raised**, using read-only queries against the data. Examples: which Form ADV fields can separate investment managers from advisors, and how many firms each candidate threshold would affect.
- **Ran its own review passes** that found and fixed real problems:
  - a stale-filing rule that misflagged firms with non-December fiscal years;
  - quoted figures that had drifted from the outputs;
  - "NaN" showing in tables.

**What I did and decided:**
- **The data:** selected the data files to use from the SEC's website, and inspected the data to understand its quirks,
  gaps and edge cases.
- **Every analytical judgment call:**
  - the advisor definition (5B(1), with 5B(3) as a check);
  - reporting clients both with and without non-AUM clients;
  - excluding robo-advisers and digital platforms, which I noticed via implausible clients-per-advisor figures;
  - separating investment managers, after spotting Quantinno and Vise among the "wealth" leaders;
  - tightening that rule when it wrongly caught Nest Egg, a real advisory firm;
  - adding the 20-client floor to the capacity ranking, and dropping a family-office type once Gleneagles and a16z Perennial showed it couldn't be identified reliably;
  - the focus on wealth-focused firms.
- **Scope, presentation and when the work was ready.**

**How the output was verified:**
- **Unit tests** for each parsing and classification rule.
- **Hard assertions** in the notebook: row counts, unique firm-years, AUM components summing to the total.
- **Figure cross-checks:** every figure quoted in the text was checked against the output files.
- **Spot checks:** individual firms (e.g. Beacon Pointe, Nest Egg) checked against their raw filings.
- **Fresh-clone run:** a run from a fresh clone confirmed the notebook reproduces all outputs exactly.
