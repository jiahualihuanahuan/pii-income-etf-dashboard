"""Split-corporation statements (Quadravest, Middlefield, Ninepoint): weights are re-based to Class A net assets (offline fixtures)."""
from pathlib import Path

from app import holdings_build as hb, schedule_parser as sp
from tests.test_holdings import xray

QV = """
Statements of Financial Position
Investments                              $     1,500   $   1,400
Cash                                              100          50
Written options                                   100          50
Preferred shares                                  900         900
NET ASSETS ATTRIBUTABLE TO HOLDERS
OF REDEEMABLE CLASS A SHARES (NOTE 1)(1)         500         450

Schedule of Portfolio Investments
As at May 31, 2026 (unaudited)
Canadian Core Common Equities
    100   Bank A                             800        1,000
    200   Bank B                             300          400
Total Common Equities                       1,100       1,400
Call Options written
   (100) Bank A @ $10 June 2026               (5)        (10)
Total Investments                             1,095      1,490
"""


def test_quadravest_rebases_to_class_a_and_balances():
    p = sp.parse_split_corp_quadravest(QV)
    assert p["as_of"] == "2026-05-31" and p["net_assets"] == 500
    h = sp.to_holdings(p)
    assert abs(sum(x["w"] for x in h) - 100) < 1e-6
    assert [x["name"] for x in h if x["kind"] == "security"] == ["Bank A", "Bank B"]
    assert abs(next(x["w"] for x in h if x["name"] == "Bank A") - 200) < 1e-6  # 1,000 / 500
    assert next(x["w"] for x in h if x["kind"] == "derivative") == -20.0
    assert next(x["w"] for x in h if x["name"] == "Cash") == 20.0
    assert next(x["w"] for x in h if x["name"].startswith("Preferred")) < 0


MF = """
Statements of Financial Position
Cash                                   $   100    $ 50
Net Assets Attributable to Holders of Redeemable Class A Shares      $   400      $ 300

Schedule of Investment Portfolio
AS AT JUNE 30, 2026
Description                          No. of Securities    Average Cost    Fair Value
Allied Properties REIT                      1,000   $       500   $      600
Blackstone Inc.                               100           300          400
REAL ESTATE: 93.6%                                          800        1,000
TOTAL INVESTMENTS: 97.0%                                    800        1,000
CASH: 3.0%                                                  100          100
"""


def test_middlefield_rows_skip_subtotals():
    p = sp.parse_split_corp_middlefield(MF)
    assert p["as_of"] == "2026-06-30" and p["check"]["n"] == 2
    h = sp.to_holdings(p)
    assert abs(sum(x["w"] for x in h) - 100) < 1e-6
    assert next(x["w"] for x in h if x["name"] == "Allied Properties REIT") == 150.0


NP = """
Statements of Financial Position
Cash                                                  50        10
Net Assets attributable to holders of redeemable Class A shares        200        100

Canadian Large Cap Leaders Split Corp.
Schedule of Investment Portfolio
As at June 30, 2026 (unaudited)                       Average Cost   Fair Value
                  SHARES            EQUITIES [100.00%]
                                    FINANCIALS [60.00%]
                  50      Royal Bank of Canada                 100     200
                                    ENERGY [40.00%]
                  30      Enbridge Inc.                         80     100
Total equities                                                 180     300
"""


def test_ninepoint_sector_and_rebase():
    p = sp.parse_split_corp_ninepoint(NP)
    h = sp.to_holdings(p)
    assert next(x["sector"] for x in h if x["name"] == "Royal Bank of Canada") == "Financials"
    assert abs(sum(x["w"] for x in h) - 100) < 1e-6 and abs(h[0]["w"] - 100.0) < 1e-6


def test_supplement_only_funds_are_aliased(tmp_path):
    # aliases come from the supplement keys when the xray index has no such fund
    class Idx:
        def key_for(self, m, t):
            return None
    out = hb.dashboard_aliases(["GDV.TO", "BTCY-U.TO", "ZZZ.TO", "SPYI"], Idx(), {"CA:GDV", "CA:BTCY", "US:SPYI"})
    assert out == {"GDV.TO": "CA:GDV", "BTCY-U.TO": "CA:BTCY", "SPYI": "US:SPYI"}
