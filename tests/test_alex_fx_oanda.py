"""Offline tests for the OANDA v20 adapter (alex_fx/oanda.py) — no network."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alex_fx import oanda  # noqa: E402


def test_instrument_name():
    assert oanda.instrument("EUR/USD") == "EUR_USD"
    assert oanda.instrument("gbp/jpy") == "GBP_JPY"


def test_parse_candles_mid_and_incomplete():
    payload = {"candles": [
        {"time": "1735689600.000000000", "volume": 10, "complete": True,
         "mid": {"o": "1.1000", "h": "1.1020", "l": "1.0990", "c": "1.1010"}},
        {"time": "1735690500.000000000", "volume": 5, "complete": False,   # dropped
         "mid": {"o": "1.1010", "h": "1.1015", "l": "1.1005", "c": "1.1012"}},
    ]}
    candles = oanda.parse_candles(payload)
    assert len(candles) == 1                       # incomplete bar dropped
    c = candles[0]
    assert c.open == 1.1000 and c.close == 1.1010
    assert c.time == 1735689600
    # keep incomplete when asked
    assert len(oanda.parse_candles(payload, drop_incomplete=False)) == 2


def test_broker_units_sign_and_lots():
    b = oanda.OandaBroker(env="practice")
    captured = {}

    def fake_order(pair, units, stop=None, take_profit=None):
        captured["units"] = units
        captured["stop"] = stop
        return {"orderFillTransaction": {"id": "999", "price": "1.10000"}}

    b.client.market_order = fake_order

    f = b.market("EUR/USD", "buy", 0.5, 1.1000)        # 0.5 lot -> 50,000 units
    assert f.ok and captured["units"] == 50000
    b.market("EUR/USD", "sell", 0.5, 1.1000)           # sell -> negative units
    assert captured["units"] == -50000


def test_broker_env_practice_is_paper():
    assert oanda.OandaBroker(env="practice").env == "practice"
    assert oanda.HOSTS["practice"].endswith("fxpractice.oanda.com")
    assert oanda.HOSTS["live"].endswith("fxtrade.oanda.com")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("all oanda tests passed")
