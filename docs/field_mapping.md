# Raw data reference: SEC source files and field mapping

**What this document covers:** the raw data side only.
- the six source files: formats, row counts, schema differences between years;
- how each raw SEC column becomes an output column, including the parsing quirks found when profiling every row of every file.

**What it doesn't cover:** methodology, firm-type rules, analysis methods and assumptions are all in the
[README](../README.md) (§4 Methodology, §5 Assumptions and limitations). Results are in the notebook.

Profiled 2026-11-01, reading every row of every file.

## 1. Source files

| Snapshot year | File | Format | Firms (rows) | Columns | Date format | Number format |
|---|---|---|---|---|---|---|
| 2021 | `ia090121.csv` | CSV, UTF-8 (BOM) | 14,638 | 262 | `3/31/2021` | `"30,000,000.00"` strings |
| 2022 | `ia090122.csv` | CSV, UTF-8 (BOM) | 15,362 | 262 | `m/d/yyyy` | comma strings |
| 2023 | `ia090623.xlsx` | XLSX, 1 sheet | 15,528 | 448 | Excel serial (`45016`) | plain numeric |
| 2024 | `ia090324.xlsx` | XLSX, 1 sheet | 15,923 | 448 | Excel serial | plain numeric |
| 2025 | `ia09022025.xlsx` | XLSX, 1 sheet | 16,359 | 448 | Excel serial | plain numeric |
| 2026 | `2026_IA_SEC_-_FIRM_ROSTER_FOIA_DOWNLOAD_-_34655879.CSV` | CSV, **cp1252** | 17,149 | 448 | `03/31/2026` | `"1,234.00"`; zero written as `".00"` |

### Schema differences
- **Two layouts:** 262 columns (2021–2022) and 448 columns (2023–2026). The 262 columns are a **strict subset** of the 448, with identical names.
- **The 2026 roster has the same schema as 2025.** Headers are identical, so it does not have a different schema. Only its encoding (cp1252) and formats (dates, numbers) differ.
- Column *positions* differ between the layouts (e.g. `SEC#` is col 2 in 2021–22 and col 4 in 2023+). **Always select columns by name.**
- Columns only in the 448 layout: `Additional CRD Number`, `CIK#`, `Total number of relying advisers`, `Main/Mail Office Private Residence Flag`, `Total Number of Website Addresses`, `Location of Books and Records *`, `1L`, `4A/4B`, `5K`/`5L`, `5.K.(*)` derivative percentages, private-fund counts (`Any Hedge Funds`, `Total number of PE funds`, …), `Total Gross Assets of Private Funds`, `Total Custody Amount`, `Acquired Firm*`, `Count of 11*` disclosure counts, `12A`–`12C`, affiliate counts.
- None of the required fields are missing from any year.
- XLSX rows have ragged widths (trailing empty cells omitted), so they must be padded to 448.
- `Form Version` is Excel-mangled in the CSVs (`Mar-20`, `Oct-21`), an Excel serial in the XLSX files, and `10/2021` in 2026. It is informational only.

### Universe and keys
- Every row in every file is `Firm Type = Registered` (SEC-registered RIAs; no exempt reporting advisers), and every SEC# starts with `801-`.
- `Organization CRD#` and `SEC#` are both unique within each file. One 2026 row has a blank `SEC#`.
- All six files are late-summer snapshots (latest filing date runs from 2021-08-31 to 2026-08-31), giving 6 annual points. Growth windows are 1y = 2025→2026, 3y = 2023→2026 and 5y = 2021→2026.
- 11,258 CRDs appear in all six years. Each year roughly 730–900 firms exit and 1,000–1,700 enter.

## 2. Field mapping

| # | Required field | Output column | Source column(s) | Transform / rules |
|---|---|---|---|---|
| – | Firm key | `crd` | `Organization CRD#` | Join key across years (100% filled, unique per file). SEC# is not used as the key. |
| a | SEC# (XXX-YYYYY) | `sec_number` | `SEC#` | Strip. Already `801-NNNNN`; the suffix length varies, so do not pad it. 1 blank in 2026: backfill from another year for the same CRD. |
| b | Firm Name | `firm_name` (+ `legal_name`) | `Primary Business Name`, `Legal Name` | Strip and collapse whitespace. Use the latest year's name when displaying cross-year results. |
| c | Website | `website` | `Website Address` | Holds only **one** of the firm's URLs; `Total Number of Website Addresses` is often 2–7. ~2.6k (2021) to ~4.9k (2026) values are LinkedIn or other social links, and ~9% are blank. Lowercase, drop `http(s)://` and the trailing `/`, set `website_is_social`. Where possible, replace a social or blank URL with a non-social URL from another year for the same CRD. |
| d | City | `city` | `Main Office City` | Blank for ~800–1,400 firms/yr because **private-residence main offices are suppressed** (blank ⇔ `Main Office Private Residence Flag = Y`). Fallback order: `Mail Office City` → `Location of Books and Records City` (2023+) → same CRD in another year. Record the source in `location_source`. |
| e | State | `state` | `Main Office State` | Same fallback chain as city. Non-US firms (~870/yr) legitimately have no state; keep `country` from `Main Office Country`. |
| f | AUM (USD) | `aum` | `5F(2)(c)` (total regulatory AUM) | Parse by stripping `,` and `$`; `.00` → 0. Also keep `aum_discretionary` = `5F(2)(a)` and `aum_nondiscretionary` = `5F(2)(b)`. Checks: (a)+(b)=(c) for every row; Σ`5D(*)(3)` ≈ `5F(2)(c)` (≤6 rows/yr differ by more than 1%). ~630–690 firms/yr report AUM = 0. |
| g | Number of Clients (AUM clients) | `clients_raum` | `5D(a..n)(1)` + `5D(*)(2)` | Σ of client counts by type, **plus 2 for every `5D(*)(2)` cell that reads `"Fewer than 5 clients"`** (midpoint of 1–4). That literal text is the value; it is not Y/N. There are ~13k such cells per year; ~8–9k of them have no count alongside and are imputed, while the rest also report a count, which is used as is. `5D(d)`, `5D(e)` and `5D(f)` (investment companies, BDCs, pooled vehicles) have no `(2)` column. |
| g′ | Number of Clients (all) | `clients_total` | `clients_raum` + `5C(1)` | `5C(1)` = clients for whom the firm has **no** regulatory AUM (e.g. planning-only). It is 0 for ~73% of firms. It has extreme outliers (max 2.36 billion in 2026) and a handful of legacy bucket strings (`"1-10"`, `"11-25"`, `"26-100"`, `"More than 100"`). Implemented rule: set to NaN and flag (`clients_nonraum_invalid`) when the value is non-numeric, exceeds 100M, or equals the firm's AUM (some filers typed AUM into this box, e.g. Claret Asset Management, 2.36B). Ignore `5C(1)-If more than 100, how many` (legacy, ~empty). |
| h | Average AUM per Client | `aum_per_client` (+ `aum_per_client_total`) | derived | `aum / clients_raum` (primary; numerator and denominator both cover AUM clients). Alternative: `aum / clients_total`. NaN when the denominator is 0. |
| i | Number of Employees | `employees` | `5A` | Integer. ~1 blank/yr. |
| j | Number of Advisors | `advisors` (+ `advisors_iar`) | `5B(1)` (+ `5B(3)`) | **Primary: `5B(1)`**, employees performing investment advisory functions including research. Never exceeds `5A`. 0 for ~130–180 firms/yr, ~90–120 of which still report AUM > 0. Sensitivity column: `5B(3)`, employees registered as state investment adviser representatives. It is 0 for ~6k firms/yr and exceeds `5B(1)` at ~900. |
| k | AUM per Advisor | `aum_per_advisor` | derived | `aum / advisors`. NaN when advisors = 0. |
| l | Filing Date / Year | `filing_date`, `year` | `Latest ADV Filing Date` + snapshot year from the file | Parse m/d/yyyy, Excel serial (origin 1899-12-30) and mm/dd/yyyy. `year` = snapshot year. ~97% of filings fall in the snapshot year (March annual amendments), so AUM generally reflects the prior fiscal year-end. Firms with non-December fiscal years file later in the year, e.g. in December for a September year-end. `is_stale` marks filings more than 15 months older than the snapshot (rule and rationale: README §4.3). |

### Supplementary fields kept
| Output column | Source | Why |
|---|---|---|
| `country` | `Main Office Country` | Separates non-US firms from missing state values |
| `umbrella` | `Umbrella Registration` | Umbrella filers fold relying advisers into one AUM figure, which matters for growth jumps |
| `n_relying_advisers` | `Total number of relying advisers` (2023+) | Same reason |
| `sec_status` | `SEC Current Status` | `120-Day Approval` = newly registered firm |
| `accounts` | `5F(2)(f)` | Sanity check alongside the client counts |
| `clients_nonraum` | `5C(1)` | Keeps the raw component of `clients_total` |
| `aum_individuals`, `clients_individuals`, `pct_aum_individuals` | `5D(a)(3)+5D(b)(3)`, `5D(a)(1)+5D(b)(1)` | Individuals + high-net-worth individuals. Input to the wealth-focused rule (README §4.4) |
| `offers_planning` | `5G(1)` + `5H` | `5G(1) = Y` (financial planning offered) **or** a non-zero planning-client band in `5H` (`1-10` … `More than 500`). Bands and codes are identical in every year |
| `selects_other_advisers` | `5G(7)` | `5G(7) = Y` |
| `aum_wrap_subadviser` | `5I(2)(b)` | Item 5I(2) splits wrap-program AUM into **(a) sponsor, (b) portfolio manager in another firm's program, (c) both**. This order was confirmed against known firms: Vestmark has 100% in (b), AssetMark 100% in (c) |
| `clients_per_advisor` | `clients_raum / 5B(1)` | Blank when there are no advisors |
| `digital_platform`, `investment_manager`, `wealth_focused` | derived | Firm types; the rules and rationale are in README §4.4. Tested and rejected as an identifier: Item 5D(j) ("other investment advisers" as clients) is almost never used, because sub-advisers report the end investors instead |
| `advisors_iar`, `aum_per_iar` | `5B(3)` | Sensitivity check for the advisor definition |
| `n_acquired_firms` | `Total Number of Acquired Firms` (2023+) | Part of the `consolidation_flag` for inorganic growth |

### Ignored
Legacy pre-2017 `5D(1)(a..m)` / `5D(2)(a..m)` percentage columns (≤6 non-empty rows per file).

---
Assumptions and limitations that follow from these mappings (advisor proxy, client imputation, AUM timing, survivorship and
so on) are listed once, in README §5.
