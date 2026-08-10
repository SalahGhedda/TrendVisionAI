from __future__ import annotations

from . import desktop_ui_fast as fast
from .trade_plan_calibration import (
    MAX_MARKET_SAMPLE_AGE_SECONDS,
    TRADE_PLAN_VERSION,
    StaleMarketDataError,
    analyze_trade_plan_v2,
    build_trade_plan_snapshot_v2,
)


trade = fast.trade
base = fast.base

# Upgrade the trade-plan globals used by the inherited worker/page methods.
trade.analyze_trade_plan = analyze_trade_plan_v2
trade.build_trade_plan_snapshot = build_trade_plan_snapshot_v2


class SmoothTickerMemoryPageV2(fast.SmoothTickerMemoryPage):
    def __init__(self, repo) -> None:
        super().__init__(repo)

        for label in self.findChildren(trade.QLabel):
            if label.text() == "Trade Plan Experiment v1":
                label.setText(f"Trade Plan Experiment v{TRADE_PLAN_VERSION}")
            elif label.text().startswith("This is calibration, not a proven trade alert yet."):
                label.setText(
                    "This is calibration, not a proven trade alert yet. Exact market numbers come from fresh Alpaca samples; "
                    "IEX is partial-venue data rather than consolidated SIP/NBBO. Trade Plan v2 refuses actionable analysis "
                    f"when the latest market sample is older than {MAX_MARKET_SAMPLE_AGE_SECONDS} seconds. No brokerage order is ever sent."
                )

    def _trade_failed(self, message: str) -> None:
        if message.startswith(StaleMarketDataError.__name__ + ":"):
            detail = message.split(":", 1)[1].strip() if ":" in message else message
            self.trade_status.setText(f"Trade Plan v2 blocked the analysis: {detail}")
            self.trade_text.setPlainText(
                "NO AI REQUEST WAS USED FOR THIS PLAN\n\n"
                + detail
                + "\n\nThis freshness guardrail prevents a fast-moving chart from being evaluated against an old Alpaca quote."
            )
            return
        super()._trade_failed(message)


base.TickerMemoryPage = SmoothTickerMemoryPageV2


def main() -> int:
    return fast.main()


if __name__ == "__main__":
    raise SystemExit(main())
