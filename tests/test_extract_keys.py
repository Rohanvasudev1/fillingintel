"""Node keys built in code from labels and names (Step 8; filer-scoped keys in Step 9;
the metric value's unit class in Step 10)."""
import pytest

from extract.keys import metric_key, node_key, norm
from extract.units import UnknownUnit

ACCESSION = "0001045810-26-000021"
CIK = "1045810"
AMD_CIK = "2488"


def test_norm_applies_nfkc_casefold_and_collapsed_whitespace():
    assert norm("  Taiwan  Semiconductor\nManufacturing ") == "taiwan semiconductor manufacturing"
    assert norm("ﬁber") == "fiber"
    assert norm("Straße") == "strasse"


def test_norm_keeps_legal_suffixes_for_step_9():
    assert norm("Samsung Electronics Co., Ltd.") == "samsung electronics co., ltd."


def test_cross_filing_key_is_label_and_normalized_name():
    assert node_key("Organization", "TSMC", ACCESSION, CIK) == "Organization:tsmc"
    assert node_key("Organization", " tsmc ", ACCESSION, CIK) == node_key(
        "Organization", "TSMC", ACCESSION, CIK)
    assert node_key("Product", "TSMC", ACCESSION, CIK) != node_key(
        "Organization", "TSMC", ACCESSION, CIK)


def test_segment_and_market_platform_keys_hold_the_filers_cik():
    assert node_key("Segment", "Compute & Networking", ACCESSION, CIK) == (
        "Segment:1045810:compute & networking"
    )
    assert node_key("MarketPlatform", " Gaming", ACCESSION, CIK) == (
        "MarketPlatform:1045810:gaming"
    )
    assert node_key("MarketPlatform", "Gaming", "0000002488-26-000018", AMD_CIK) == (
        "MarketPlatform:2488:gaming"
    )


def test_a_segment_key_does_not_depend_on_the_filing():
    assert node_key("Segment", "Graphics", ACCESSION, CIK) == node_key(
        "Segment", "Graphics", "0001045810-25-000230", CIK)


def test_a_metric_measuring_a_segment_carries_the_segment_key():
    subject = node_key("Segment", "Graphics", ACCESSION, CIK)
    assert metric_key(ACCESSION, subject, "Revenue", "USD millions", "FY2026") == (
        f"{ACCESSION}:Segment:1045810:graphics:revenue:currency:FY2026"
    )


def test_risk_factor_key_is_per_filing():
    key = node_key("RiskFactor", "Export controls could harm us", ACCESSION, CIK)
    assert key == f"{ACCESSION}:export controls could harm us"
    assert key != node_key("RiskFactor", "Export controls could harm us",
                           "0000002488-26-000018", AMD_CIK)


def test_metric_key_names_filing_subject_metric_unit_class_and_period():
    assert metric_key(ACCESSION, "1045810", "Revenue", "USD millions", "FY2026") == (
        f"{ACCESSION}:1045810:revenue:currency:FY2026"
    )


def test_net_income_in_percent_and_in_usd_gives_two_keys():
    # NVIDIA FY2026 10-K: net income 55.6% of revenue (chunk 0066), $120,067 million (0063)
    percent = metric_key(ACCESSION, "1045810", "Net income", "percent", "FY2026")
    usd = metric_key(ACCESSION, "1045810", "Net income", "USD millions", "FY2026")
    assert percent == f"{ACCESSION}:1045810:net income:percent:FY2026"
    assert usd == f"{ACCESSION}:1045810:net income:currency:FY2026"


def test_one_figure_at_two_scales_keeps_one_key():
    assert metric_key(ACCESSION, "2488", "Net revenue", "USD billions", "FY2024-Q1") == (
        metric_key(ACCESSION, "2488", "Net revenue", "USD millions", "FY2024-Q1"))


def test_a_metric_key_with_an_unknown_unit_fails_with_its_name():
    with pytest.raises(UnknownUnit, match="'furlongs'"):
        metric_key(ACCESSION, "1045810", "Revenue", "furlongs", "FY2026")


def test_metric_period_is_kept_as_written_but_escaped():
    key = metric_key(ACCESSION, "1045810", "Revenue", "percent", "FY2026:Q3")
    assert key.endswith(":percent:FY2026%3aQ3")


def test_a_name_holding_the_separator_is_escaped():
    assert node_key("Product", "GB200: Grace", ACCESSION, CIK) == "Product:gb200%3a grace"
    assert node_key("Product", "a%3a", ACCESSION, CIK) != node_key(
        "Product", "a:", ACCESSION, CIK)


def test_metric_keys_stay_distinct_when_names_hold_the_separator():
    subject = node_key("Segment", "Compute & Networking", ACCESSION, CIK)
    assert metric_key(ACCESSION, subject, "revenue:gross", "USD", "FY2026") != metric_key(
        ACCESSION, subject, "revenue", "USD", "gross:FY2026",
    )
