from __future__ import annotations

from . import desktop_ui_fast_v2 as ui_v2
from .trade_plan_calibration_v3 import (
    MAX_ACTIONABLE_OBSERVED_SPREAD_PCT,
    MAX_QUOTE_AGE_SECONDS,
    MAX_TRADE_AGE_SECONDS,
    TRADE_PLAN_VERSION,
    analyze_trade_plan_v3,
    build_trade_plan_snapshot_v3,
)


trade = ui_v2.trade
base = ui_v2.base

# Upgrade the inherited trade-plan worker/page without duplicating the existing
# optimized UI shell.
trade.analyze_trade_plan = analyze_trade_plan_v3
trade.build_trade_plan_snapshot = build_trade_plan_snapshot_v3

_original_format_plan = trade._format_plan


def _format_plan_v3(result, evaluation=None):
    text = _original_format_plan(result, evaluation)
    return text.replace(
        "v1 only keeps entry/stop/targets when the case passes POTENTIAL TRADE guardrails.",
        "Actionable entry/stop/targets are saved only when the case passes the current POTENTIAL TRADE guardrails.",
    )


trade._format_plan = _format_plan_v3


class SmoothTickerMemoryPageV3(ui_v2.SmoothTickerMemoryPageV2):
    def __init__(self, repo) -> None:
        super().__init__(repo)

        for label in self.findChildren(trade.QLabel):
            text = label.text()
            if text == "Trade Plan Experiment v2":
                label.setText(f"Trade Plan Experiment v{TRADE_PLAN_VERSION}")
            elif text.startswith("This is calibration, not a proven trade alert yet."):
                label.setText(
                    "This is calibration, not a proven trade alert yet. Trade Plan v3 checks the timestamps of the actual "
                    f"Alpaca trade and quote events separately (trade <= {MAX_TRADE_AGE_SECONDS}s, quote <= {MAX_QUOTE_AGE_SECONDS}s). "
                    "A freshly downloaded snapshot no longer makes an old latest trade look current. IEX remains partial-venue data; "
                    "third-party BUY/SL/TP overlays in screenshots are ignored. "
                    f"A fresh observed spread of {MAX_ACTIONABLE_OBSERVED_SPREAD_PCT:.0f}%+ is blocked from actionable v3 levels. "
                    "No brokerage order is ever sent."
                )


base.TickerMemoryPage = SmoothTickerMemoryPageV3


def main() -> int:
    return ui_v2.fast.main()


if __name__ == "__main__":
    raise SystemExit(main())
