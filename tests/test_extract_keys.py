"""Node keys built in code from labels and names (Step 8)."""
from extract.keys import metric_key, node_key, norm

ACCESSION = "0001045810-26-000021"


def test_norm_applies_nfkc_casefold_and_collapsed_whitespace():
    assert norm("  Taiwan  Semiconductor\nManufacturing ") == "taiwan semiconductor manufacturing"
    assert norm("ﬁber") == "fiber"
    assert norm("Straße") == "strasse"


def test_norm_keeps_legal_suffixes_for_step_9():
    assert norm("Samsung Electronics Co., Ltd.") == "samsung electronics co., ltd."


def test_cross_filing_key_is_label_and_normalized_name():
    assert node_key("Organization", "TSMC", ACCESSION) == "Organization:tsmc"
    assert node_key("Organization", " tsmc ", ACCESSION) == node_key("Organization", "TSMC",
                                                                    ACCESSION)
    assert node_key("Product", "TSMC", ACCESSION) != node_key("Organization", "TSMC", ACCESSION)


def test_risk_factor_key_is_per_filing():
    key = node_key("RiskFactor", "Export controls could harm us", ACCESSION)
    assert key == f"{ACCESSION}:export controls could harm us"
    assert key != node_key("RiskFactor", "Export controls could harm us", "0000002488-26-000018")


def test_metric_key_names_filing_subject_metric_and_period():
    assert metric_key(ACCESSION, "1045810", "Revenue", "FY2026") == (
        f"{ACCESSION}:1045810:revenue:FY2026"
    )


def test_metric_period_is_kept_as_written_but_escaped():
    assert metric_key(ACCESSION, "1045810", "Revenue", "FY2026:Q3").endswith(":FY2026%3aQ3")


def test_a_name_holding_the_separator_is_escaped():
    assert node_key("Product", "GB200: Grace", ACCESSION) == "Product:gb200%3a grace"
    assert node_key("Product", "a%3a", ACCESSION) != node_key("Product", "a:", ACCESSION)


def test_metric_keys_stay_distinct_when_names_hold_the_separator():
    subject = node_key("Segment", "Compute & Networking", ACCESSION)
    assert metric_key(ACCESSION, subject, "revenue:gross", "FY2026") != metric_key(
        ACCESSION, subject, "revenue", "gross:FY2026",
    )
