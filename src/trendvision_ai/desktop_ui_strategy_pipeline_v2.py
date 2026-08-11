from __future__ import annotations

import time

from . import desktop_ui_strategy_pipeline as strategy


base = strategy.base


class StrategyPipelineMainWindowV2(strategy.StrategyPipelineMainWindow):
    """Strategy pipeline with bounded background calibration refresh."""

    def __init__(self) -> None:
        self._last_live_evidence_refresh = 0.0
        super().__init__()

    def _run_live_pipeline(self) -> None:
        now = time.monotonic()
        if now - self._last_live_evidence_refresh >= 30.0:
            try:
                self.live_qualification.refresh(limit=500)
            except Exception as exc:
                try:
                    self.live_page.set_status(
                        f"Strategy calibration refresh deferred: {type(exc).__name__}: {exc}"
                    )
                except Exception:
                    pass
            self._last_live_evidence_refresh = now
        super()._run_live_pipeline()


base.MainWindow = StrategyPipelineMainWindowV2


def main() -> int:
    return strategy.main()


if __name__ == "__main__":
    raise SystemExit(main())
