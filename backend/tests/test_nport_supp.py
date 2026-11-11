"""SEC N-PORT and iShares CSV -> supplement entries; ticker-based sub-fund match for supplements (offline fixtures)."""
import importlib.util
from pathlib import Path

from app import holdings_build as hb, nport_parser as npp

X = """<?xml version="1.0"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData>
<genInfo><seriesName>Test Option Income ETF</seriesName><repPdDate>2026-06-30</repPdDate></genInfo>
<fundInfo><netAssets>1000000</netAssets></fundInfo>
<invstOrSecs>
<invstOrSec><name>Vanguard S&amp;P 500 ETF</name><title>Vanguard S&amp;P 500 ETF</title><cusip>922908363</cusip><pctVal>90</pctVal><assetCat>EC</assetCat><issuerCat>RF</issuerCat><invCountry>US</invCountry>
  <identifiers><isin value="US9229083632"/><ticker value="VOO"/></identifiers></invstOrSec>
<invstOrSec><name>N/A</name><title>SPX 12/18/2026 5000 C</title><pctVal>-2</pctVal><assetCat>DE</assetCat><issuerCat>OTHER</issuerCat><payoffProfile>Short</payoffProfile>
  <optionSwaptionWarrantDeriv/></invstOrSec>
<invstOrSec><name>Treasury Bill</name><title>United States Treasury Bill</title><pctVal>6</pctVal><assetCat>DBT</assetCat><issuerCat>UST</issuerCat></invstOrSec>
<invstOrSec><name>Roundhill Weekly T-Bill ETF</name><title>Roundhill Weekly T-Bill ETF</title><pctVal>3</pctVal><assetCat>EC</assetCat><issuerCat>RF</issuerCat><identifiers><ticker value="WEEK"/></identifiers></invstOrSec>
</invstOrSecs></formData></edgarSubmission>""".encode()


def test_nport_kinds_and_residual():
    h = npp.with_residual(npp.to_holdings(npp.parse_nport(X)))
    k = {x["name"]: x for x in h}
    assert k["Vanguard S&P 500 ETF"]["kind"] == "fund" and k["Vanguard S&P 500 ETF"]["ticker"] == "VOO"
    assert [x for x in h if x["kind"] == "derivative"][0]["name"] == "SPX 12/18/2026 5000 C"  # 'N/A' issuer name -> the title
    assert sum(1 for x in h if x["kind"] == "cash") == 3  # T-bill, T-bill ETF, remainder
    assert abs(sum(x["w"] for x in h) - 100) < 1e-6
    assert h[-1]["name"].startswith("Cash and other net assets") and abs(h[-1]["w"] - 3) < 1e-9


class Idx:
    funds = {"US:VOO": {}}

    def resolve_fund(self, *a, **k):
        return None

    def key_for(self, m, t):
        return m + ":" + t if m + ":" + t in self.funds else None


def test_supplement_fund_row_maps_by_filing_ticker_to_xray_or_supplement_fund():
    e = {"holdings": [{"name": "Vanguard S&P 500 ETF", "ticker": "VOO", "kind": "fund", "w": 90}, {"name": "YieldMax X ETF", "ticker": "XYZY", "kind": "fund", "w": 5},
                      {"name": "Other ETF", "ticker": "NOPE", "kind": "fund", "w": 5}]}
    assert hb.supp_subs(Idx(), "US:ABCD", e, {"US:XYZY"}) == {0: "US:VOO", 1: "US:XYZY"}  # no name guessing: NOPE stays opaque


def test_ishares_csv_parser():
    spec = importlib.util.spec_from_file_location("bis", Path(__file__).resolve().parents[2] / "tools" / "build_ishares_supplement.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    csv_text = ('iShares Test ETF\nFund Holdings as of,"Nov 09, 2026"\nInception Date,"Sep 26, 2023"\n\n'
                'Ticker,Name,Type,Sector,Asset Class,Market Value,Weight (%),Notional Value,Quantity,Price,Strike price,Location,Exchange,Currency\n'
                '"AAPL","APPLE","EQUITY","Information Technology","Equity","1","90.00","1","1","1","-","United States","NASDAQ","USD"\n'
                '"XTSLA","BLK CSH FND TREASURY SL AGENCY","FUND","Cash and/or Derivatives","Money Market","1","8.00","1","1","1","-","United States","-","USD"\n'
                '"SPX","NOV26 SPX C @ 7695","OPTION","Cash and/or Derivatives","Other Derivatives","1","-1.00","1","1","1","7,695.00","United States","OPQ","USD"\n')
    name, as_of, h = m.parse(csv_text)
    assert as_of == "2026-11-09" and [x["kind"] for x in h] == ["security", "cash", "derivative"] and h[0]["sector"] == "Information Technology"
