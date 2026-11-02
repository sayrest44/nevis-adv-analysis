"""Parsing and cleaning for SEC Form ADV investment-adviser snapshots.

Turns the six raw snapshot files in ``raw_data/`` into one firm-year panel
(one row per Organization CRD# per snapshot year). Field choices are
documented in ``docs/field_mapping.md``.

Public entry points
-------------------
- ``load_raw(path)``            -> raw DataFrame, every column as stripped str
- ``standardize(raw, year)``    -> one snapshot mapped to the common schema
- ``build_panel(files)``        -> all snapshots, cleaned, with derived metrics
- ``growth_table(panel, ...)``  -> start/end comparison for growth questions
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
RAW_DIR = Path(__file__).parent / "raw_data"

# snapshot year -> file. All six are late-summer snapshots (max filing date
# Aug/Sep of the same year), including the 2026 roster.
SNAPSHOT_FILES = {
    2021: "ia090121.csv",
    2022: "ia090122.csv",
    2023: "ia090623.xlsx",
    2024: "ia090324.xlsx",
    2025: "ia09022025.xlsx",
    2026: "2026_IA_SEC_-_FIRM_ROSTER_FOIA_DOWNLOAD_-_34655879.CSV",
}

# Columns required in every file (present in both the 262- and 448-column layouts).
REQUIRED_COLUMNS = [
    "Organization CRD#", "SEC#", "Primary Business Name", "Legal Name",
    "Main Office City", "Main Office State", "Main Office Country",
    "Mail Office City", "Mail Office State", "Mail Office Country",
    "Website Address", "Latest ADV Filing Date", "SEC Current Status",
    "Umbrella Registration", "5A", "5B(1)", "5B(3)", "5C(1)",
    "5F(2)(a)", "5F(2)(b)", "5F(2)(c)", "5F(2)(f)",
]

# Item 5.D client types a..n. Column (1) = number of clients, (2) = "fewer than
# 5 clients" checkbox, (3) = regulatory AUM. Types d, e, f (investment
# companies, BDCs, pooled vehicles) have no (2) column on the form.
CLIENT_TYPES = list("abcdefghijklmn")
FEWER_THAN_5_IMPUTE = 2  # midpoint of 1-4
NONRAUM_CLIENTS_MAX = 1e8  # 5C(1) above 100M clients is treated as an entry error

# Wealth-focused = majority of AUM from individuals + high-net-worth individuals.
WEALTH_AUM_SHARE = 0.5
# Above this many clients per advisor a firm cannot be giving bespoke advice:
# the tail is robo-advisers, app-based investing and managed-account/TAMP
# platforms (Acorns, Betterment, Human Interest, Envestnet...). 99th pct of
# wealth-focused firms is ~775, so 1,000 sits at the natural break.
PLATFORM_CLIENTS_PER_ADVISOR = 1_000
# Investment managers used by RIAs (direct indexing, SMA and model providers such as Quantinno, Vise,
# Aperio, Parametric). They report the end investors in other advisers' programs as their own "individual"
# clients, so they look wealth-focused on Item 5D alone. Flagged when an individual-focused firm offers no
# financial planning (5G(1), 5H) and does not select other advisers (5G(7)), and EITHER
#   (a) manages >= 25% of AUM as portfolio manager in other firms' wrap programs (5I(2)(b)), OR
#   (b) runs a large book behind other advisers' clients: > 200 clients per advisor AND >= 10 advisors AND
#       >= $250K AUM per client.
# Branch (b) needs all three: client load alone also catches mass-affluent / bank-channel advisers with
# small accounts (e.g. Nest Egg, ~$47K per client) and one-person investment-counsel boutiques, which are
# real advisory firms. Traditional investment counsel without planning (low client load, no wrap
# sub-advisory) stays wealth-focused.
MANAGER_WRAP_SUBADVISER_SHARE = 0.25
MANAGER_CLIENTS_PER_ADVISOR = 200
MANAGER_MIN_ADVISORS = 10
MANAGER_MIN_AUM_PER_CLIENT = 250_000
# A filing is stale when the latest ADV predates the snapshot by more than this. The annual amendment is
# due within 90 days of each firm's fiscal year-end, so a current filer's latest filing is at most
# 12 + 3 months old. A calendar-year test would wrongly flag firms with non-December fiscal years.
STALE_AFTER_MONTHS = 15
_PLANNING_CLIENT_BANDS = {"1-10", "11-25", "26-50", "51-100", "101-250", "251-500", "More than 500"}

# AUM segment bins (latest-year AUM, half-open intervals).
SEGMENT_BINS = [0, 1e9, 1e10, 5e10, np.inf]
SEGMENT_LABELS = ["<$1B", "$1B-$10B", "$10B-$50B", ">$50B"]

SOCIAL_DOMAINS = re.compile(
    r"(?:^|\.)(?:linkedin|facebook|twitter|x|instagram|youtube|vimeo|tiktok|"
    r"threads|pinterest|medium|wordpress|blogspot|substack)\.(?:com|net)$"
)


# --------------------------------------------------------------------------
# Low-level parsers
# --------------------------------------------------------------------------
def load_raw(path: str | Path) -> pd.DataFrame:
    """Read one snapshot with every value as a stripped string ('' = missing).

    CSVs are tried as UTF-8 first, then cp1252 (the 2026 roster). XLSX date
    cells come back as ISO strings; numeric cells as plain digit strings.
    """
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        df = pd.read_excel(path, dtype=str, keep_default_na=False)
    else:
        try:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="cp1252")
    df.columns = [str(c).strip() for c in df.columns]
    df = df.fillna("").apply(lambda s: s.str.strip())
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{path.name}: missing required columns {missing}")
    # drop fully blank rows (none observed, but cheap insurance)
    return df[df.ne("").any(axis=1)].reset_index(drop=True)


def parse_num(s: pd.Series) -> pd.Series:
    """'1,234.00' / '$5' / '.00' / '511001949' -> float; ''/non-numeric -> NaN."""
    cleaned = s.astype(str).str.replace(r"[,$\s]", "", regex=True)
    return pd.to_numeric(cleaned.replace("", np.nan), errors="coerce")


def invalid_num(s: pd.Series) -> pd.Series:
    """True where a value is present but not numeric (e.g. legacy '11-25')."""
    return s.astype(str).str.strip().ne("") & parse_num(s).isna()


def parse_date(s: pd.Series) -> pd.Series:
    """Parse the three filing-date encodings seen across years.

    - 'm/d/yyyy' or 'mm/dd/yyyy'  (CSV files)
    - 'yyyy-mm-dd hh:mm:ss'       (XLSX date-formatted cells)
    - Excel serial e.g. '45016'   (XLSX general-formatted cells)
    """
    s = s.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    serial = s.str.fullmatch(r"\d{4,6}(\.0+)?")
    out[serial] = pd.to_datetime(
        s[serial].astype(float), unit="D", origin="1899-12-30"
    )
    rest = ~serial & s.ne("")
    out[rest] = pd.to_datetime(s[rest], format="mixed", errors="coerce")
    return out


def normalize_url(s: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (website, is_social).

    Regular sites are reduced to their host ('www.beaconpointe.com'); social
    profiles keep their path because the path identifies the firm.
    """
    full = (
        s.astype(str).str.strip().str.lower()
        .str.replace(r"^[a-z]+:/*", "", regex=True)
        .str.replace(r"[?#].*$", "", regex=True)
        .str.rstrip("/")
    )
    host = full.str.split("/").str[0]
    bare_host = host.str.replace(r"^www\.", "", regex=True)
    is_social = bare_host.str.contains(SOCIAL_DOMAINS) & full.ne("")
    website = full.where(is_social, host)
    return website.replace("", pd.NA), is_social


# --------------------------------------------------------------------------
# Standardize one snapshot
# --------------------------------------------------------------------------
def _col(raw: pd.DataFrame, name: str) -> pd.Series:
    """Column as str, or all-blank if this layout doesn't have it."""
    return raw[name] if name in raw.columns else pd.Series("", index=raw.index)


def client_counts(raw: pd.DataFrame) -> pd.DataFrame:
    """Item 5.D client counts with 'fewer than 5' imputation.

    clients_raum = sum of 5D(x)(1) + FEWER_THAN_5_IMPUTE for each type where
    the 'fewer than 5 clients' box is checked and no count was given.
    """
    counts = pd.DataFrame({t: parse_num(_col(raw, f"5D({t})(1)")) for t in CLIENT_TYPES})
    flags = pd.DataFrame({
        t: _col(raw, f"5D({t})(2)").str.contains("fewer", case=False)
           | _col(raw, f"5D({t})(2)").eq("Y")
        for t in CLIENT_TYPES
    })
    impute = flags & (counts.isna() | counts.eq(0))
    return pd.DataFrame({
        "clients_reported": counts.sum(axis=1, min_count=1).fillna(0),
        "n_fewer_than_5_flags": impute.sum(axis=1),
        "clients_raum": counts.fillna(0).sum(axis=1) + FEWER_THAN_5_IMPUTE * impute.sum(axis=1),
        # wealth-management lens: individuals (a) + high-net-worth individuals (b)
        "clients_individuals": counts[["a", "b"]].fillna(0).sum(axis=1)
                               + FEWER_THAN_5_IMPUTE * impute[["a", "b"]].sum(axis=1),
        "aum_individuals": parse_num(_col(raw, "5D(a)(3)")).fillna(0)
                           + parse_num(_col(raw, "5D(b)(3)")).fillna(0),
    })


def standardize(raw: pd.DataFrame, year: int) -> pd.DataFrame:
    """Map one raw snapshot onto the common firm-year schema."""
    c = lambda name: _col(raw, name)
    website, is_social = normalize_url(c("Website Address"))
    clients = client_counts(raw)
    aum = parse_num(c("5F(2)(c)"))
    clients_nonraum = parse_num(c("5C(1)"))
    # 5C(1) data-entry errors: some filers typed their AUM into the client
    # count (value == AUM), or values above any plausible client base.
    implausible = (clients_nonraum > NONRAUM_CLIENTS_MAX) | (
        (aum > 0) & ((clients_nonraum - aum).abs() <= 0.01 * aum) & (clients_nonraum > 1e5)
    )
    clients_nonraum_invalid = invalid_num(c("5C(1)")) | implausible
    clients_nonraum = clients_nonraum.mask(implausible)

    df = pd.DataFrame({
        "year": year,
        "crd": parse_num(c("Organization CRD#")).astype("Int64"),
        "sec_number": c("SEC#").replace("", pd.NA),
        "firm_name": c("Primary Business Name").str.replace(r"\s+", " ", regex=True),
        "legal_name": c("Legal Name").str.replace(r"\s+", " ", regex=True),
        # location candidates; resolved in resolve_locations()
        "main_city": c("Main Office City"), "main_state": c("Main Office State"),
        "main_country": c("Main Office Country"),
        "mail_city": c("Mail Office City"), "mail_state": c("Mail Office State"),
        "mail_country": c("Mail Office Country"),
        "books_city": c("Location of Books and Records City"),
        "books_state": c("Location of Books and Records State"),
        "books_country": c("Location of Books and Records Country"),
        "private_residence": c("Main Office Private Residence Flag").eq("Y"),
        "website_raw": website,
        "website_is_social": is_social,
        "filing_date": parse_date(c("Latest ADV Filing Date")),
        "aum": aum,
        "aum_discretionary": parse_num(c("5F(2)(a)")),
        "aum_nondiscretionary": parse_num(c("5F(2)(b)")),
        "accounts": parse_num(c("5F(2)(f)")),
        "employees": parse_num(c("5A")),
        "advisors": parse_num(c("5B(1)")),
        "advisors_iar": parse_num(c("5B(3)")),
        "clients_nonraum": clients_nonraum,
        "clients_nonraum_invalid": clients_nonraum_invalid,
        "sec_status": c("SEC Current Status"),
        "umbrella": c("Umbrella Registration").eq("Y"),
        # advisory services (Item 5G/5H) and wrap-program role (Item 5I)
        "offers_planning": c("5G(1)").eq("Y") | c("5H").isin(_PLANNING_CLIENT_BANDS),
        "selects_other_advisers": c("5G(7)").eq("Y"),
        "aum_wrap_subadviser": parse_num(c("5I(2)(b)")),
        "n_relying_advisers": parse_num(c("Total number of relying advisers")),
        "n_acquired_firms": parse_num(c("Total Number of Acquired Firms")),
    })
    df = pd.concat([df, clients], axis=1)
    df["clients_total"] = df["clients_raum"] + df["clients_nonraum"].fillna(0)
    return df


# --------------------------------------------------------------------------
# Panel-level cleaning
# --------------------------------------------------------------------------
def _nearest_year_fill(panel: pd.DataFrame, valid: pd.Series) -> pd.Series:
    """For each row, the index of a 'valid' row for the same CRD: itself if
    valid, else the closest earlier year, else the closest later year."""
    src = pd.Series(np.where(valid, panel.index, np.nan), index=panel.index)
    g = src.groupby(panel["crd"])
    return src.fillna(g.ffill()).fillna(g.bfill())


def resolve_locations(panel: pd.DataFrame) -> pd.DataFrame:
    """City/state/country from the first source with a city.

    Main office (blank when it is a private residence, which SEC suppresses)
    -> mailing address -> books & records location (2023+ files)
    -> same firm in the nearest other year.
    """
    p = panel
    city = p["main_city"].copy()
    state, country = p["main_state"].copy(), p["main_country"].copy()
    source = pd.Series(np.where(city.ne(""), "main_office", ""), index=p.index)
    for prefix, label in [("mail", "mail_office"), ("books", "books_records")]:
        use = city.eq("") & p[f"{prefix}_city"].ne("")
        city[use], state[use] = p.loc[use, f"{prefix}_city"], p.loc[use, f"{prefix}_state"]
        country[use], source[use] = p.loc[use, f"{prefix}_country"], label

    tmp = pd.DataFrame({"city": city, "state": state, "country": country, "src": source})
    idx = _nearest_year_fill(p, tmp["city"].ne(""))
    fill = tmp["city"].eq("") & idx.notna()
    take = idx[fill].astype(int)
    for col in ["city", "state", "country"]:
        tmp.loc[fill, col] = tmp.loc[take, col].to_numpy()
    tmp.loc[fill, "src"] = "other_year"

    out = p.drop(columns=[c for c in p.columns
                          if c.split("_")[0] in ("main", "mail", "books")])
    out["city"] = tmp["city"].str.title().replace("", pd.NA)
    out["state"] = tmp["state"].str.upper().replace("", pd.NA)
    out["country"] = tmp["country"].replace("", pd.NA)
    out["location_source"] = tmp["src"].replace("", "missing")
    return out


def resolve_websites(panel: pd.DataFrame) -> pd.DataFrame:
    """Prefer a real (non-social) site; borrow one from the nearest other year
    for the same firm when this year's is blank or a social profile."""
    p = panel.copy()
    good = p["website_raw"].notna() & ~p["website_is_social"]
    idx = _nearest_year_fill(p, good)
    borrow = ~good & idx.notna()
    p["website"] = p["website_raw"]
    p.loc[borrow, "website"] = p.loc[idx[borrow].astype(int), "website_raw"].to_numpy()
    p["website_is_social"] = p["website_is_social"] & ~borrow
    p["website_from_other_year"] = borrow
    return p.drop(columns="website_raw")


def backfill_sec_number(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    p["sec_number"] = p["sec_number"].fillna(
        p.groupby("crd")["sec_number"].transform(lambda s: s.ffill().bfill())
    )
    return p


def add_metrics(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.copy()
    p["aum_per_client"] = p["aum"] / p["clients_raum"].where(p["clients_raum"] > 0)
    p["aum_per_client_total"] = p["aum"] / p["clients_total"].where(p["clients_total"] > 0)
    p["aum_per_advisor"] = p["aum"] / p["advisors"].where(p["advisors"] > 0)
    p["aum_per_iar"] = p["aum"] / p["advisors_iar"].where(p["advisors_iar"] > 0)
    p["pct_aum_individuals"] = (p["aum_individuals"] / p["aum"].where(p["aum"] > 0)).clip(upper=1)
    p["clients_per_advisor"] = p["clients_raum"] / p["advisors"].where(p["advisors"] > 0)
    p["digital_platform"] = p["clients_per_advisor"] > PLATFORM_CLIENTS_PER_ADVISOR
    individual_focused = p["pct_aum_individuals"] >= WEALTH_AUM_SHARE
    wrap_sub_share = p["aum_wrap_subadviser"].fillna(0) / p["aum"].where(p["aum"] > 0)
    wrap_subadviser = wrap_sub_share >= MANAGER_WRAP_SUBADVISER_SHARE
    large_book_behind_advisers = (
        (p["clients_per_advisor"] > MANAGER_CLIENTS_PER_ADVISOR)
        & (p["advisors"] >= MANAGER_MIN_ADVISORS)
        & (p["aum_per_client"] >= MANAGER_MIN_AUM_PER_CLIENT)
    )
    p["investment_manager"] = (
        individual_focused & ~p["digital_platform"] & ~p["offers_planning"] & ~p["selects_other_advisers"]
        & (wrap_subadviser | large_book_behind_advisers)
    )
    # bespoke wealth managers: Nevis's core market. Family offices are deliberately not split out: single-family
    # offices are exempt from SEC registration (Family Office Rule), and the registered "family offices" in this
    # data are multi-family offices and wealth firms that Form ADV cannot reliably tell apart (README §4.4).
    p["wealth_focused"] = individual_focused & ~p["digital_platform"] & ~p["investment_manager"]
    # stale = latest filing is more than STALE_AFTER_MONTHS older than the snapshot, so the firm missed its
    # annual amendment and the AUM is carried over. The snapshot date is approximated by the latest filing
    # date in that year's file (late August / early September).
    snapshot_date = p.groupby("year")["filing_date"].transform("max")
    p["is_stale"] = p["filing_date"] < snapshot_date - pd.DateOffset(months=STALE_AFTER_MONTHS)
    p["segment"] = assign_segment(p["aum"])
    return p


def assign_segment(aum: pd.Series) -> pd.Series:
    return pd.cut(aum, bins=SEGMENT_BINS, labels=SEGMENT_LABELS, right=False)


COLUMN_ORDER = [
    "year", "crd", "sec_number", "firm_name", "legal_name", "website", "city", "state",
    "country", "aum", "clients_raum", "clients_total", "aum_per_client",
    "aum_per_client_total", "employees", "advisors", "aum_per_advisor", "filing_date",
    "segment", "advisors_iar", "aum_per_iar", "aum_discretionary", "aum_nondiscretionary",
    "accounts", "clients_reported", "n_fewer_than_5_flags", "clients_nonraum",
    "clients_nonraum_invalid", "clients_individuals", "aum_individuals",
    "pct_aum_individuals", "clients_per_advisor", "digital_platform", "investment_manager", "wealth_focused",
    "offers_planning", "selects_other_advisers", "aum_wrap_subadviser", "is_stale", "sec_status", "umbrella", "n_relying_advisers",
    "n_acquired_firms", "private_residence", "location_source", "website_is_social",
    "website_from_other_year",
]


def build_panel(files: dict[int, str] | None = None, raw_dir: Path = RAW_DIR,
                verbose: bool = True) -> pd.DataFrame:
    """Load, standardize, clean and enrich all snapshots into one panel."""
    files = files or SNAPSHOT_FILES
    parts = []
    for year, name in sorted(files.items()):
        raw = load_raw(raw_dir / name)
        std = standardize(raw, year)
        if verbose:
            print(f"{year}: {name:<58} {len(raw):>6,} rows x {raw.shape[1]} cols")
        parts.append(std)
    panel = pd.concat(parts, ignore_index=True).sort_values(["crd", "year"], ignore_index=True)

    assert panel["crd"].notna().all(), "every row must have a CRD"
    assert not panel.duplicated(["crd", "year"]).any(), "CRD must be unique per year"

    panel = resolve_locations(panel)
    panel = resolve_websites(panel)
    panel = backfill_sec_number(panel)
    panel = add_metrics(panel)
    return panel[COLUMN_ORDER]


_NAME_SUFFIX = re.compile(
    r"[,\s]+(LLC|L\.L\.C\.|LP|L\.P\.|LLP|INC\.?|INCORPORATED|CORP\.?|CORPORATION|LTD\.?|LIMITED|PLC)\.?$", re.I)
_KEEP_UPPER = {"AI", "US", "USA", "UK", "II", "III", "IV", "RBC", "PGIM", "GW&K", "H&H", "AE", "BG", "CFI", "OC",
               "SG", "TCW", "MAI", "LMR", "NY", "ETF", "PPG", "SBG", "UBS", "BNY", "JPMORGAN", "CLO", "DIFC", "MFO", "BT"}
_SMALL_WORDS = {"of", "and", "the", "for", "&"}


def display_name(name: str) -> str:
    """Readable firm name for tables: 'MERCURY ADVISORY, LLC' -> 'Mercury Advisory'.
    The raw SEC name stays in the data and CSVs."""
    if not isinstance(name, str):
        return name
    n = name.strip()
    for _ in range(2):
        n = _NAME_SUFFIX.sub("", n).strip(" ,")
    words = []
    for i, w in enumerate(n.split()):
        core = w.strip(",.()")
        initials = "." in core.rstrip(".")  # J.P., L.S.Y. (but not "Co.")
        acronym = core.isalpha() and len(core) <= 4 and not set(core.lower()) & set("aeiouy")  # CCM, RPG, BT
        if core.upper() in _KEEP_UPPER or any(ch.isdigit() for ch in core) or initials or acronym:
            words.append(w.upper())
        elif i > 0 and w.lower() in _SMALL_WORDS:
            words.append(w.lower())
        elif w.startswith("&"):
            words.append("&" + w[1:].capitalize())
        elif "-" in w:
            words.append("-".join(p.capitalize() for p in w.split("-")))
        else:
            words.append(w.capitalize())
    return " ".join(words)


def write_csv_zip(df: pd.DataFrame, path: str | Path, archive_name: str) -> None:
    """Write df as a single CSV inside a zip (opens natively on Windows/macOS).

    The entry timestamp is fixed, so identical data always produces an
    identical file and re-running the notebook leaves git clean.
    """
    info = zipfile.ZipInfo(archive_name, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(info, df.to_csv(index=False), compresslevel=9)


# --------------------------------------------------------------------------
# Analysis helpers
# --------------------------------------------------------------------------
def growth_table(panel: pd.DataFrame, end_year: int, horizon: int) -> pd.DataFrame:
    """Firms present in both end_year and end_year - horizon, with AUM and
    advisor CAGRs. Segment = end-year segment."""
    keep = ["crd", "aum", "advisors", "aum_per_advisor", "is_stale", "umbrella",
            "n_relying_advisers"]
    end = panel[panel["year"] == end_year]
    start = panel.loc[panel["year"] == end_year - horizon, keep]
    m = end.merge(start, on="crd", suffixes=("", "_start"))
    with np.errstate(divide="ignore", invalid="ignore"):
        m["aum_growth_total"] = m["aum"] / m["aum_start"] - 1
        m["aum_cagr"] = (m["aum"] / m["aum_start"]) ** (1 / horizon) - 1
        m["advisor_growth_total"] = m["advisors"] / m["advisors_start"] - 1
        m["advisor_cagr"] = (m["advisors"] / m["advisors_start"]) ** (1 / horizon) - 1
    m["growth_gap"] = m["aum_cagr"] - m["advisor_cagr"]
    # signals that growth may be inorganic (merger / consolidation of filers)
    m["consolidation_flag"] = (
        (m["umbrella"] & ~m["umbrella_start"])
        | (m["n_relying_advisers"].fillna(0) > m["n_relying_advisers_start"].fillna(0))
        | (m["n_acquired_firms"].fillna(0) > 0)
    )
    m["horizon"] = horizon
    return m.replace([np.inf, -np.inf], np.nan)


def top_n_by_segment(df: pd.DataFrame, sort_col: str, n: int = 10,
                     columns: list[str] | None = None) -> pd.DataFrame:
    """Top n rows per segment by sort_col (descending), segments in size order."""
    out = (df.sort_values(sort_col, ascending=False)
             .groupby("segment", observed=True, sort=False).head(n))
    out = out.assign(segment=pd.Categorical(out["segment"], SEGMENT_LABELS, ordered=True))
    out = out.sort_values(["segment", sort_col], ascending=[True, False])
    out.insert(1, "rank", out.groupby("segment", observed=True).cumcount() + 1)
    return out[["segment", "rank"] + (columns or [])] if columns else out
