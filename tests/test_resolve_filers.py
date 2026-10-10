"""The hand-written filer-name table (Step 9)."""
import pytest

from extract.filers import FILER_CIKS
from resolve.filers import FILER_TABLE, filer_entry


@pytest.mark.parametrize(("name", "ticker"), [
    ("NVIDIA", "NVDA"), ("NVIDIA Corp.", "NVDA"), ("Advanced Micro Devices", "AMD"),
    ("AMD", "AMD"), ("Intel Corporation", "INTC"), ("Intel", "INTC"),
])
def test_filer_names_map_to_their_cik(name, ticker):
    entry = filer_entry("Organization", name)
    assert entry is not None
    assert (entry.ticker, entry.cik) == (ticker, FILER_CIKS[ticker])


@pytest.mark.parametrize("name", ["Intel Foundry", "Xilinx", "Mellanox", "NVIDIA GeForce"])
def test_other_names_do_not_map(name):
    assert filer_entry("Organization", name) is None


def test_only_organizations_map():
    assert filer_entry("Product", "NVIDIA") is None


def test_every_filer_has_an_entry():
    assert {e.ticker for e in FILER_TABLE.values()} == set(FILER_CIKS)
