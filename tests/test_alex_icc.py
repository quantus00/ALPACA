"""Offline tests for the Alex strategy wired into the ICC app (bot/).

No network, no broker SDKs: candles are injected and orders go through a fake
broker, so config/factory/guards, sizing, entry, and exits are all exercised.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from alex_bot.strategy import Candle, Params  # noqa: E402
from bot import alex as alex_mod  # noqa: E402
from bot.brokers import get_broker  # noqa: E402
from bot.brokers.base import BrokerBase, OrderResult  # noqa: E402
from bot.brokers.coinbase import CoinbaseBroker  # noqa: E402
from bot.brokers.webull import WebullBroker  # noqa: E402
from bot.brokers.webull_openapi import (WebullOpenAPIBroker,  # noqa: E402
                                        coinbase_product, wb_crypto_symbol)
from bot.config import AssetClass, Broker, Config  # noqa: E402


def _cfg(broker, asset, symbol, size=1.0, dry_run=True):
    c = Config()
    c.broker = broker
    c.asset_class = asset
    c.symbol_override = symbol
    c.contract_size = size
    c.dry_run = dry_run
    return c


# -- config free mode --------------------------------------------------------
def test_free_mode_symbol_and_validate():
    c = _cfg(Broker.COINBASE, AssetClass.CRYPTO, "ETH-USD")
    assert c.free_mode() and c.symbol() == "ETH-USD"
    c.validate()  # coinbase can trade crypto


def test_validate_rejects_wrong_broker_for_asset():
    c = _cfg(Broker.COINBASE, AssetClass.FUTURES, "MES")
    with pytest.raises(ValueError):
        c.validate()                     # coinbase cannot trade futures


def test_futures_multiplier_by_root():
    assert _cfg(Broker.WEBULL, AssetClass.FUTURES, "MESU5").multiplier() == 5.0
    assert _cfg(Broker.WEBULL, AssetClass.FUTURES, "MNQZ5").multiplier() == 2.0
    assert _cfg(Broker.WEBULL, AssetClass.FUTURES, "ZZZ").multiplier() == 1.0
    assert _cfg(Broker.COINBASE, AssetClass.CRYPTO, "ETH-USD").multiplier() == 1.0


# -- factory / broker selection ----------------------------------------------
def test_factory_picks_openapi_for_webull():
    c = _cfg(Broker.WEBULL, AssetClass.FUTURES, "MES")
    assert isinstance(get_broker(c), WebullOpenAPIBroker)


def test_factory_community_backend_still_available():
    c = _cfg(Broker.WEBULL, AssetClass.OPTION, "SPY")
    c.webull_backend = "community"
    # community broker doesn't declare crypto/futures/option support -> rejected
    with pytest.raises(ValueError):
        get_broker(c)
    assert issubclass(WebullBroker, BrokerBase)


def test_coinbase_trades_any_pair_dry_run():
    c = _cfg(Broker.COINBASE, AssetClass.CRYPTO, "SOL-USD", size=1.0)
    b = get_broker(c)
    assert isinstance(b, CoinbaseBroker)
    r = b.place_order("buy", 1.0)
    assert r.ok and r.symbol == "SOL-USD"


def test_webull_openapi_guards_asset_class():
    c = _cfg(Broker.WEBULL, AssetClass.FUTURES, "MES")
    b = get_broker(c)
    assert b.supports_cfg()
    # stock is not wired for the openapi broker
    c.asset_class = AssetClass.STOCK
    assert not b.supports_cfg()


def test_symbol_normalizers():
    assert wb_crypto_symbol("BTC-USD") == "BTCUSD"
    assert wb_crypto_symbol("eth/usd") == "ETHUSD"
    assert coinbase_product("BTCUSD") == "BTC-USD"
    assert coinbase_product("ETH-USD") == "ETH-USD"


# -- sizing ------------------------------------------------------------------
def test_position_size_risk_and_fallback():
    # $10k, 1% risk, stop 100 away -> 100/... => (10000*0.01)/100 = 1.0 unit
    assert abs(alex_mod.position_size(10000, 1.0, 30000, 29900, 0.5) - 1.0) < 1e-9
    # no risk config -> fallback to fixed size
    assert alex_mod.position_size(0, 0, 30000, 29900, 0.5) == 0.5
    # degenerate stop -> fallback
    assert alex_mod.position_size(10000, 1.0, 30000, 30000, 0.7) == 0.7


# -- strategy integration: fake broker + injected candles --------------------
class FakeBroker(BrokerBase):
    supported_assets = (AssetClass.CRYPTO, AssetClass.FUTURES)

    def __init__(self, cfg, mark):
        super().__init__(cfg)
        self._mark = mark
        self.orders = []

    def place_order(self, side, size):
        self.orders.append((side, size))
        return OrderResult(ok=True, broker="fake", symbol=self.cfg.symbol(),
                           side=side, size=size, order_id="fake-1", fill_price=self._mark)

    def flatten(self, side=None, size=None, symbol=None, meta=None):
        self.orders.append(("flatten", size))
        return OrderResult(ok=True, broker="fake", symbol=symbol or self.cfg.symbol(),
                           side="flat", size=size or 0, order_id="fake-flat")

    def mark_price(self, symbol=None, meta=None):
        return self._mark


def _zone_line(prices):
    return [Candle(time=i, open=p, high=p + 1, low=p - 1, close=p)
            for i, p in enumerate(prices)]


def _structure_series():
    seq = [90, 95, 88, 100, 92, 112]
    for _ in range(3):
        seq += [115, 108, 100, 107, 114]
    seq += [116, 118]
    return _zone_line(seq)


def _entry_series():
    # prior bar, then a bullish rejection tagging the ~100 support zone
    return [Candle(0, 104, 105, 100.5, 101), Candle(1, 101, 101.5, 98.8, 101.2)]


def _fetch(symbol, tf, limit=300):
    return _structure_series() if tf == "1h" else _entry_series()


def test_evaluate_alex_produces_buy():
    c = _cfg(Broker.COINBASE, AssetClass.CRYPTO, "BTC-USD")
    params = Params(pivot_lookback=1, aoi_tol_frac=0.03)
    sig = alex_mod.evaluate_alex(c, params, _fetch, "15m", "1h")
    assert sig.action == "buy" and sig.stop < sig.entry < sig.take_profit


def test_trade_tick_enters_then_takes_profit(tmp_path, monkeypatch):
    state = tmp_path / "alex_state.json"
    monkeypatch.setenv("BOT_ALEX_STATE_FILE", str(state))
    monkeypatch.setenv("ALEX_ENTRY_TF", "15m")
    monkeypatch.setenv("ALEX_STRUCTURE_TF", "1h")
    c = _cfg(Broker.COINBASE, AssetClass.CRYPTO, "BTC-USD", size=0.01)
    params = Params(pivot_lookback=1, aoi_tol_frac=0.03)

    # 1) entry tick: price near entry -> opens a long, writes state
    entry_broker = FakeBroker(c, mark=101.2)
    alex_mod.trade_tick(c, params, entry_broker, _fetch, notifier=None)
    assert state.exists()
    assert entry_broker.orders and entry_broker.orders[0][0] == "buy"

    # 2) manage tick: price at/above take-profit -> flattens and clears state
    import json
    tp = json.loads(state.read_text())["tp"]
    exit_broker = FakeBroker(c, mark=tp + 1)
    alex_mod.trade_tick(c, params, exit_broker, _fetch, notifier=None)
    assert ("flatten", 0.01) in exit_broker.orders
    assert not state.exists()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except TypeError:
                pass  # fixtures-based tests run under pytest
    print("alex ICC tests: run under pytest for full coverage")
