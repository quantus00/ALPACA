"""Backtester for Alex's strategy on any candle series (built for forex).

Walks the candles bar by bar, at each step re-evaluating the strategy on the
history seen so far (no look-ahead), opens one position at a time, and exits on
stop / take-profit (checked intrabar) or a trend flip. Reports trade-by-trade
results plus summary stats (win rate, total R, profit factor, max drawdown).

Reuses the SAME tested engine as the live bot (alex_bot.strategy), so the
backtest and live behaviour match — exactly the point Alex makes about
back-testing before trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from alex_bot.strategy import Candle, Params, Trend, evaluate
from .instrument import from_pips, to_pips


@dataclass
class Trade:
    side: str
    entry_time: int
    entry: float
    stop: float
    take_profit: float
    exit_time: int | None = None
    exit: float | None = None
    reason: str | None = None      # "take-profit" | "stop" | "trend-flip" | "eod"

    @property
    def r_multiple(self) -> float:
        risk = abs(self.entry - self.stop)
        if not risk or self.exit is None:
            return 0.0
        pnl = (self.exit - self.entry) if self.side == "buy" else (self.entry - self.exit)
        return pnl / risk


@dataclass
class BacktestResult:
    pair: str
    trades: list[Trade] = field(default_factory=list)

    # -- summary stats --------------------------------------------------------
    @property
    def closed(self) -> list[Trade]:
        return [t for t in self.trades if t.exit is not None]

    @property
    def wins(self) -> int:
        return sum(1 for t in self.closed if t.r_multiple > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.closed if t.r_multiple <= 0)

    @property
    def win_rate(self) -> float:
        n = len(self.closed)
        return (self.wins / n * 100.0) if n else 0.0

    @property
    def total_r(self) -> float:
        return sum(t.r_multiple for t in self.closed)

    @property
    def profit_factor(self) -> float:
        gains = sum(t.r_multiple for t in self.closed if t.r_multiple > 0)
        losses = -sum(t.r_multiple for t in self.closed if t.r_multiple < 0)
        return (gains / losses) if losses else float("inf")

    @property
    def max_drawdown_r(self) -> float:
        peak = 0.0
        equity = 0.0
        dd = 0.0
        for t in self.closed:
            equity += t.r_multiple
            peak = max(peak, equity)
            dd = min(dd, equity - peak)
        return dd

    def summary(self, pips: bool = True) -> str:
        lines = [
            f"Backtest {self.pair}: {len(self.closed)} trades",
            f"  win rate      : {self.win_rate:.1f}%  ({self.wins}W / {self.losses}L)",
            f"  total R       : {self.total_r:+.2f}",
            f"  profit factor : {self.profit_factor:.2f}",
            f"  max drawdown  : {self.max_drawdown_r:.2f} R",
        ]
        if self.closed and pips:
            avg = sum(to_pips(self.pair, abs(t.exit - t.entry)) *
                      (1 if t.r_multiple > 0 else -1) for t in self.closed) / len(self.closed)
            lines.append(f"  avg trade     : {avg:+.1f} pips")
        return "\n".join(lines)


def backtest(candles: list[Candle], pair: str, params: Params | None = None,
             warmup: int = 60, spread_pips: float = 0.0,
             exit_on_flip: bool = True) -> BacktestResult:
    """Run the strategy over `candles`. `spread_pips` widens entries/exits to
    model cost. One position at a time."""
    params = params or Params()
    res = BacktestResult(pair=pair)
    spread = from_pips(pair, spread_pips)
    open_trade: Trade | None = None

    for i in range(warmup, len(candles)):
        bar = candles[i]

        if open_trade is not None:
            # -- manage the open position on this bar (intrabar stop/tp) -------
            if open_trade.side == "buy":
                if bar.low <= open_trade.stop:
                    open_trade.exit, open_trade.reason = open_trade.stop - spread, "stop"
                elif bar.high >= open_trade.take_profit:
                    open_trade.exit, open_trade.reason = open_trade.take_profit - spread, "take-profit"
            else:
                if bar.high >= open_trade.stop:
                    open_trade.exit, open_trade.reason = open_trade.stop + spread, "stop"
                elif bar.low <= open_trade.take_profit:
                    open_trade.exit, open_trade.reason = open_trade.take_profit + spread, "take-profit"
            if open_trade.exit is None and exit_on_flip:
                tr = evaluate(candles[:i + 1], candles[i - 1:i + 1], params).trend
                if (open_trade.side == "buy" and tr == Trend.DOWN) or \
                   (open_trade.side == "sell" and tr == Trend.UP):
                    open_trade.exit, open_trade.reason = bar.close, "trend-flip"
            if open_trade.exit is not None:
                open_trade.exit_time = bar.time
                open_trade = None
            continue

        # -- flat: look for an entry on this bar ------------------------------
        sig = evaluate(candles[:i + 1], candles[i - 1:i + 1], params)
        if sig.action in ("buy", "sell"):
            entry = sig.entry + (spread if sig.action == "buy" else -spread)
            open_trade = Trade(side=sig.action, entry_time=bar.time, entry=entry,
                               stop=sig.stop, take_profit=sig.take_profit)
            res.trades.append(open_trade)

    return res
