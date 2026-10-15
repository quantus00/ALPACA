"""Rule tests for the hedge module (bot/hedge). Synthetic bars, no network."""
import csv
import math
import os
import random
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.hedge import config as hcfg  # noqa: E402
from bot.hedge.engine import HedgeEngine  # noqa: E402
from bot.hedge.features import BarContext, StructureTracker  # noqa: E402
from bot.trend import Candle, analyze  # noqa: E402

T0 = 1_760_000_400 // 3600 * 3600   # some UTC hour


def ctx(i, close, *, atr=100.0, t1=1, t4=1, sh=None, sl=None, h=None, corr=None,
        funding=None, high=None, low=None):
    h = close if h is None else h
    return BarContext(time=T0 + i * 3600, open=close, high=high or close, low=low or close,
                      close=close, atr=atr, trend_1h=t1, trend_4h=t4, swing_high_1h=sh,
                      swing_low_1h=sl, h_open=h, h_high=high or h, h_low=low or h, h_close=h,
                      corr=corr, funding_rate_pct=funding)


def kinds(eng):
    return [e.kind for e in eng.events if e.kind != "FUNDING"]


def crypto(**kw):
    return replace(hcfg.load("crypto"), **kw)


def forex(**kw):
    return replace(hcfg.load("forex"), **kw)


# --------------------------------------------------------------------------- crypto
def test_atr_trigger_opens_1to1_short_hedge():
    eng = HedgeEngine(crypto(default_funding_rate_pct=0.0))
    eng.open_primary(ctx(0, 10_000, sl=9_000), side=1)
    eng.on_bar(ctx(1, 9_950))                       # -0.5 ATR: nothing
    assert eng.phase == "primary"
    eng.on_bar(ctx(2, 9_900))                       # -1.0 ATR: hedge
    assert eng.phase == "hedged"
    assert eng.hedge.side == -1
    assert math.isclose(eng.hedge.units * 9_900, eng.primary.units * 9_900)
    ev = [e for e in eng.events if e.kind == "HEDGE_OPEN"][0]
    assert "ATR" in ev.reason and ev.data["trigger_level"] == 9_900


def test_correction_break_trigger():
    eng = HedgeEngine(crypto())
    eng.open_primary(ctx(0, 10_000, atr=1_000, sl=9_950), side=1)
    eng.on_bar(ctx(1, 9_940))                       # below correction low, well inside ATR
    assert eng.phase == "hedged"
    assert "correction low" in eng.events[-1].reason


def test_lift_then_no_second_hedge():
    eng = HedgeEngine(crypto(default_funding_rate_pct=0.0))
    eng.open_primary(ctx(0, 10_000, sl=9_000), side=1)
    eng.on_bar(ctx(1, 9_890))
    assert eng.phase == "hedged"
    # reclaimed level but no continuation (close not above last swing high) -> stays hedged
    eng.on_bar(ctx(2, 9_950, sh=10_100))
    assert eng.phase == "hedged"
    # continuation (1h up + close > swing high) and reclaim -> lift
    eng.on_bar(ctx(3, 10_150, sh=10_100))
    assert eng.phase == "primary" and eng.hedge is None and eng.primary is not None
    # trigger again -> normal stop, never a second hedge
    eng.on_bar(ctx(4, 9_850))
    assert eng.is_flat
    assert kinds(eng).count("HEDGE_OPEN") == 1
    assert "one hedge per primary" in [e for e in eng.events if e.kind == "PRIMARY_STOP"][0].reason


def test_cut_primary_then_trail_hedge():
    eng = HedgeEngine(crypto(default_funding_rate_pct=0.0))
    eng.open_primary(ctx(0, 10_000), side=1)
    eng.on_bar(ctx(1, 9_890))
    eng.on_bar(ctx(2, 9_700, t4=-1))                # 4h breaks down -> cut
    assert eng.phase == "hedge_only" and eng.primary is None
    assert eng.trail_stop == 9_800                  # close + 1x ATR for the short
    eng.on_bar(ctx(3, 9_500))                       # trail ratchets down
    assert eng.trail_stop == 9_600
    eng.on_bar(ctx(4, 9_550, high=9_650))           # high through stop -> exit
    assert eng.is_flat
    assert kinds(eng)[-2:] == ["HEDGE_TRAIL_EXIT", "SETUP_CLOSED"]
    r = eng.results[0]
    assert r.hedged and r.hedge_hours > 0


def test_duration_limit_closes_both_crypto():
    eng = HedgeEngine(crypto(default_funding_rate_pct=0.0))
    eng.open_primary(ctx(0, 10_000), side=1)
    eng.on_bar(ctx(1, 9_890))
    for i in range(2, 80):
        eng.on_bar(ctx(i, 9_880))
        if eng.is_flat:
            break
    lim = [e for e in eng.events if e.kind == "LIMIT_CLOSE"][0]
    assert "duration" in lim.reason and i == 73     # 72h after the hedge opened


def test_combined_loss_limit():
    s = crypto(default_funding_rate_pct=0.0, primary_fee_pct=0.0, hedge_fee_pct=0.0)
    eng = HedgeEngine(s)
    eng.open_primary(ctx(0, 10_000), side=1)
    eng.on_bar(ctx(1, 9_890))
    # perp basis blows out: spot flat, perp +3% -> hedge loses ~$300 > 2% of $10k
    eng.on_bar(ctx(2, 9_890, h=10_200))
    lim = [e for e in eng.events if e.kind == "LIMIT_CLOSE"][0]
    assert "max combined loss" in lim.reason
    assert eng.results[0].net_pnl <= -200


def test_funding_limit_and_logging():
    eng = HedgeEngine(crypto(primary_fee_pct=0.0, hedge_fee_pct=0.0))
    eng.open_primary(ctx(0, 10_000), side=1)
    eng.on_bar(ctx(1, 9_890))
    # negative funding: shorts pay. 0.2%/h -> > 0.5% after 3 settlements
    for i in range(2, 10):
        eng.on_bar(ctx(i, 9_890, funding=-0.2))
        if eng.is_flat:
            break
    fund = [e for e in eng.events if e.kind == "FUNDING"]
    assert fund and fund[0].data["paid"] > 0
    lim = [e for e in eng.events if e.kind == "LIMIT_CLOSE"][0]
    assert "funding" in lim.reason


def test_stop_mode_is_plain_stop():
    eng = HedgeEngine(crypto(), mode="stop")
    eng.open_primary(ctx(0, 10_000), side=1)
    eng.on_bar(ctx(1, 9_890))
    assert eng.is_flat and kinds(eng)[1] == "PRIMARY_STOP"


def test_4h_break_exits_unhedged_primary():
    eng = HedgeEngine(crypto())
    eng.open_primary(ctx(0, 10_000), side=1)
    eng.on_bar(ctx(1, 10_200, t4=-1))
    assert eng.is_flat and kinds(eng)[1] == "PRIMARY_EXIT"


def test_never_stack():
    eng = HedgeEngine(crypto())
    eng.open_primary(ctx(0, 10_000), side=1)
    try:
        eng.open_primary(ctx(1, 10_000), side=1)
    except RuntimeError:
        return
    raise AssertionError("second primary must be refused")


# --------------------------------------------------------------------------- forex
def test_forex_gate_blocks_and_stops():
    eng = HedgeEngine(forex())
    eng.open_primary(ctx(0, 1.1000, atr=0.0010, h=0.8800, corr=-0.6), side=1)
    eng.on_bar(ctx(1, 1.0985, atr=0.0010, h=0.8812, corr=-0.6))
    assert eng.is_flat
    assert kinds(eng)[1:3] == ["HEDGE_SKIPPED", "PRIMARY_STOP"]


def test_forex_hedge_same_direction_and_usd_offset():
    eng = HedgeEngine(forex())
    eng.open_primary(ctx(0, 1.1000, atr=0.0010, h=0.8800, corr=-0.9, t1=-1, t4=-1), side=-1)
    eng.on_bar(ctx(1, 1.1012, atr=0.0010, h=0.8790, corr=-0.9, t1=-1, t4=-1))
    assert eng.phase == "hedged"
    assert eng.hedge.side == -1                     # short EURUSD -> short USDCHF
    eur_units = eng.primary.units
    assert math.isclose(eng.hedge.units, eur_units * 1.1012)   # USD units offset USD exposure


def test_forex_no_gate_variant_hedges():
    eng = HedgeEngine(forex(corr_gate_enabled=False))
    eng.open_primary(ctx(0, 1.1000, atr=0.0010, h=0.8800, corr=-0.1), side=1)
    eng.on_bar(ctx(1, 1.0985, atr=0.0010, h=0.8812, corr=-0.1))
    assert eng.phase == "hedged"


def test_forex_duration_is_trading_bars():
    eng = HedgeEngine(forex(corr_gate_enabled=False))
    eng.open_primary(ctx(0, 1.1000, atr=0.0010, h=0.88), side=1)
    eng.on_bar(ctx(1, 1.0985, atr=0.0010, h=0.8812))
    n = 0
    while not eng.is_flat:
        n += 1
        eng.on_bar(ctx(1 + n, 1.0986, atr=0.0010, h=0.8811))
    assert n == 5 * 24


# --------------------------------------------------------------------------- features
def test_streaming_structure_matches_trend_analyze():
    rnd = random.Random(7)
    p, bars = 100.0, []
    for i in range(600):
        p += rnd.gauss(0.05, 1.0)
        bars.append(Candle(time=i, open=p, high=p + rnd.random(), low=p - rnd.random(), close=p))
    st = StructureTracker(5)
    for b in bars:
        st.update(b)
    assert st.state == int(analyze(bars, 5).trend)


# --------------------------------------------------------------------------- smoke
def test_backtest_runs_end_to_end(tmp_path):
    from backtest import hedge_backtest as hb


    def write(name, start, path):
        rows = []
        for i, r in enumerate(path):
            px = start * math.exp(r)
            rows.append((T0 + i * 3600, px, px * 1.002, px * 0.998, px, 0))
        with open(tmp_path / name, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "open", "high", "low", "close", "volume"])
            w.writerows(rows)

    def walk(n, vol, drift=0.0, seed=1):
        rnd, x, out = random.Random(seed), 0.0, []
        for _ in range(n):
            x += rnd.gauss(drift, vol)
            out.append(x)
        return out

    n = 24 * 120
    eur = walk(n, 0.001, seed=2)
    noise = walk(n, 0.0003, seed=3)
    write("BTCUSD_1h.csv", 60_000, walk(n, 0.006, 0.0002))
    write("EURUSD_1h.csv", 1.10, eur)
    write("USDCHF_1h.csv", 0.88, [-0.9 * a + b for a, b in zip(eur, noise)])
    out, evs = tmp_path / "r.md", tmp_path / "e.csv"
    hb.main(["--data-dir", str(tmp_path), "--months", "3", "--out", str(out), "--events", str(evs)])
    text = out.read_text()
    assert "(a) ICC + normal stop" in text and "(c) ICC + hedge, no corr gate" in text
