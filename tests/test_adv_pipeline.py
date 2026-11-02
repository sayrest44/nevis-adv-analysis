"""Unit tests for adv_pipeline on small synthetic inputs (no raw files needed).

Run: python -m pytest tests -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import adv_pipeline as ap  # noqa: E402


def test_parse_num_handles_all_file_formats():
    s = pd.Series(["30,000,000.00", "511001949", ".00", "", "$1,000", "11-25"])
    out = ap.parse_num(s)
    assert out.iloc[:3].tolist() == [30_000_000.0, 511_001_949.0, 0.0]
    assert np.isnan(out.iloc[3]) and out.iloc[4] == 1000.0 and np.isnan(out.iloc[5])
    assert ap.invalid_num(s).tolist() == [False, False, False, False, False, True]


def test_parse_date_handles_csv_iso_and_excel_serial():
    s = pd.Series(["3/31/2021", "03/31/2026", "2023-06-28 00:00:00", "45016", ""])
    out = ap.parse_date(s)
    assert out.iloc[0] == pd.Timestamp("2021-03-31")
    assert out.iloc[1] == pd.Timestamp("2026-03-31")
    assert out.iloc[2] == pd.Timestamp("2023-06-28")
    assert out.iloc[3] == pd.Timestamp("2023-03-31")  # Excel serial 45016
    assert pd.isna(out.iloc[4])


def test_normalize_url_reduces_sites_to_host_and_flags_social():
    s = pd.Series(["HTTP://WWW.BEACONPOINTE.COM/about/", "https://www.linkedin.com/company/acme/",
                   "https://x.com/acme", "", "www.foxbusiness.com"])
    web, social = ap.normalize_url(s)
    assert web.iloc[0] == "www.beaconpointe.com"
    assert web.iloc[1] == "www.linkedin.com/company/acme" and social.iloc[1]
    assert social.iloc[2]
    assert pd.isna(web.iloc[3]) and not social.iloc[3]
    assert not social.iloc[4]  # 'x' must match the whole label, not a suffix


def _raw(**cols):
    n = len(next(iter(cols.values())))
    base = {c: [""] * n for c in ap.REQUIRED_COLUMNS}
    base.update(cols)
    return pd.DataFrame(base)


def test_client_counts_imputes_fewer_than_5():
    raw = _raw(**{"5D(a)(1)": ["10", ""], "5D(b)(1)": ["", ""],
                  "5D(b)(2)": ["Fewer than 5 clients", "Fewer than 5 clients"],
                  "5D(d)(1)": ["3", ""]})
    cc = ap.client_counts(raw)
    assert cc["clients_raum"].tolist() == [10 + 2 + 3, 2]
    assert cc["n_fewer_than_5_flags"].tolist() == [1, 1]
    assert cc["clients_individuals"].tolist() == [12, 2]


def test_standardize_total_clients_and_5c_entry_errors():
    raw = _raw(**{"Organization CRD#": ["1", "2", "3"], "5D(a)(1)": ["100", "5", "5"],
                  "5C(1)": ["20", "2356471235", "11-25"],
                  "5F(2)(c)": ["1000000", "2356471235", "5"]})
    std = ap.standardize(raw, 2026)
    assert std["clients_total"].tolist() == [120, 5, 5]
    assert std["clients_nonraum_invalid"].tolist() == [False, True, True]


def test_location_fallback_chain():
    raw = _raw(**{
        "Organization CRD#": ["1", "2", "3"],
        "Main Office City": ["BOSTON", "", ""], "Main Office State": ["MA", "", ""],
        "Main Office Country": ["United States", "", ""],
        "Mail Office City": ["", "COLUMBUS", ""], "Mail Office State": ["", "OH", ""],
    })
    p = ap.resolve_locations(ap.standardize(raw, 2025))
    assert p["city"].tolist()[:2] == ["Boston", "Columbus"]
    assert p["location_source"].tolist() == ["main_office", "mail_office", "missing"]


def test_location_and_website_borrowed_from_other_year():
    y1 = _raw(**{"Organization CRD#": ["7"], "Main Office City": ["DALLAS"],
                 "Main Office State": ["TX"], "Website Address": ["http://www.acme.com"]})
    y2 = _raw(**{"Organization CRD#": ["7"], "Website Address": ["https://linkedin.com/company/acme"]})
    p = pd.concat([ap.standardize(y1, 2025), ap.standardize(y2, 2026)], ignore_index=True)
    p = ap.resolve_websites(ap.resolve_locations(p))
    last = p.iloc[1]
    assert last["city"] == "Dallas" and last["location_source"] == "other_year"
    assert last["website"] == "www.acme.com" and last["website_from_other_year"]


def test_growth_table_cagr_and_gap():
    panel = pd.DataFrame({
        "crd": [1, 1], "year": [2023, 2026], "aum": [100e6, 800e6], "advisors": [2, 2],
        "aum_per_advisor": [50e6, 400e6], "is_stale": [False, False],
        "umbrella": [False, False], "n_relying_advisers": [np.nan, np.nan],
        "n_acquired_firms": [0, 0], "segment": ["<$1B", "<$1B"],
    })
    g = ap.growth_table(panel, 2026, 3)
    assert np.isclose(g.loc[0, "aum_cagr"], 1.0)        # 8x over 3 years = 100%/yr
    assert np.isclose(g.loc[0, "advisor_cagr"], 0.0)
    assert np.isclose(g.loc[0, "growth_gap"], 1.0)
    assert not g.loc[0, "consolidation_flag"]


def test_digital_platforms_excluded_from_wealth_focused():
    p = pd.DataFrame({
        "aum": [1e9, 5e9, 1e9, 1e9], "aum_individuals": [0.9e9, 5e9, 0.2e9, 0.9e9],
        "clients_raum": [300, 500_000, 50, 300], "clients_total": [300, 500_000, 50, 300],
        "advisors": [5, 10, 5, 0], "advisors_iar": [5, 10, 5, 0],
        "offers_planning": [True] * 4, "selects_other_advisers": [False] * 4, "aum_wrap_subadviser": [0.0] * 4,
        "filing_date": pd.to_datetime(["2026-03-31"] * 4), "year": 2026,
    })
    m = ap.add_metrics(p)
    assert m["digital_platform"].tolist() == [False, True, False, False]   # 50,000 clients/advisor
    assert m["wealth_focused"].tolist() == [True, False, False, True]      # 3rd: institutional


def test_investment_managers_excluded_from_wealth_focused():
    # all individual-focused and none offer planning or select other advisers, unless stated
    cases = [  # (aum, clients, advisors, planning, selects, wrap_subadviser_aum, expected manager)
        (5e9, 8_000, 10, False, False, 0.0, True),     # SMA/direct indexing: 800 clients/adv, 10 advisors, $625K/client
        (5e9, 300, 10, False, False, 3e9, True),       # wrap sub-adviser: 60% of AUM in others' programs
        (5e9, 300, 10, False, False, 0.0, False),      # investment counsel: 30 clients/advisor, no wrap
        (1.3e8, 2_800, 9, False, False, 0.0, False),   # mass-affluent / bank channel (Nest Egg): ~$46K/client
        (9e8, 470, 1, False, False, 0.0, False),       # one-person boutique: 470 clients/advisor but 1 advisor
        (5e9, 8_000, 10, True, False, 0.0, False),     # offers planning
        (5e9, 8_000, 10, False, True, 0.0, False),     # selects other advisers
    ]
    aum, clients, advisors, planning, selects, wrap, expected = map(list, zip(*cases))
    p = pd.DataFrame({
        "aum": aum, "aum_individuals": [0.9 * a for a in aum], "clients_raum": clients, "clients_total": clients,
        "advisors": advisors, "advisors_iar": advisors, "offers_planning": planning,
        "selects_other_advisers": selects, "aum_wrap_subadviser": wrap,
        "filing_date": pd.to_datetime(["2026-03-31"] * len(cases)), "year": 2026,
    })
    m = ap.add_metrics(p)
    assert m["investment_manager"].tolist() == expected
    assert m["wealth_focused"].tolist() == [not e for e in expected]


def test_write_csv_zip_roundtrip_and_reproducible(tmp_path):
    df = pd.DataFrame({"crd": [1, 2], "aum": [1.5e9, 0.0]})
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    ap.write_csv_zip(df, a, "panel.csv")
    ap.write_csv_zip(df, b, "panel.csv")
    assert a.read_bytes() == b.read_bytes()            # deterministic
    pd.testing.assert_frame_equal(pd.read_csv(a), df)  # pandas reads the zip directly


def test_display_name():
    assert ap.display_name("MERCURY ADVISORY, LLC") == "Mercury Advisory"
    assert ap.display_name("MACK & CO. PARTNERS, LP") == "Mack & Co. Partners"
    assert ap.display_name("J.P. MORGAN PRIVATE INVESTMENTS INC.") == "J.P. Morgan Private Investments"
    assert ap.display_name("&PARTNERS") == "&Partners"
    assert ap.display_name("VISE AI ADVISORS") == "Vise AI Advisors"
    assert ap.display_name("BANK OF THE WEST") == "Bank of the West"
    assert ap.display_name("CCM INVESTMENT ADVISERS, LLC") == "CCM Investment Advisers"  # consonant-only acronym


def test_few_client_ultra_wealthy_firms_stay_wealth_focused():
    # Family offices are not split out: a firm with a handful of very large relationships is still wealth-focused
    # (the Q2 capacity ranking handles it with a 20-client floor instead)
    p = pd.DataFrame({
        "aum": [1.35e9, 1.35e9], "aum_individuals": [1.3e9, 1.3e9],
        "clients_raum": [11, 250], "clients_total": [11, 250], "advisors": [4, 4], "advisors_iar": [4, 4],
        "offers_planning": [True] * 2, "selects_other_advisers": [False] * 2, "aum_wrap_subadviser": [0.0] * 2,
        "filing_date": pd.to_datetime(["2026-03-31"] * 2), "year": 2026,
    })
    m = ap.add_metrics(p)
    assert "family_office" not in m.columns
    assert m["wealth_focused"].tolist() == [True, True]


def test_stale_flag_allows_non_december_fiscal_years():
    # snapshot ~ 2026-08-31 (latest filing in the year). A firm with a September fiscal year-end files its
    # annual amendment in December, so a 2025-12-15 filing is current; only filings > 15 months old are stale.
    p = pd.DataFrame({
        "aum": [1e9] * 4, "aum_individuals": [9e8] * 4, "clients_raum": [100] * 4, "clients_total": [100] * 4,
        "advisors": [5] * 4, "advisors_iar": [5] * 4, "offers_planning": [True] * 4,
        "selects_other_advisers": [False] * 4, "aum_wrap_subadviser": [0.0] * 4, "year": 2026,
        "filing_date": pd.to_datetime(["2026-08-31", "2026-03-30", "2025-12-15", "2025-04-01"]),
    })
    assert ap.add_metrics(p)["is_stale"].tolist() == [False, False, False, True]


def test_segments_are_half_open():
    seg = ap.assign_segment(pd.Series([0, 999_999_999, 1e9, 1e10, 5e10, 6e10]))
    assert seg.tolist() == ["<$1B", "<$1B", "$1B-$10B", "$10B-$50B", ">$50B", ">$50B"]
