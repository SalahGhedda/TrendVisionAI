from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from . import desktop_ui as base
from .ai_review import (
    AIReviewResult,
    AIReviewStore,
    DEFAULT_MODEL,
    analyze_snapshot,
    build_review_snapshot,
    delete_api_key,
    get_api_key,
    save_api_key,
)
from .attention import evaluate_attention


class ReviewWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, *, snapshot: dict[str, Any], model: str, api_key: str) -> None:
        super().__init__()
        self.snapshot = snapshot
        self.model = model
        self.api_key = api_key

    def run(self) -> None:
        try:
            result = analyze_snapshot(
                self.snapshot,
                api_key=self.api_key,
                model=self.model,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def _format_review(review: dict[str, Any]) -> str:
    verdict = str(review.get("verdict") or "-")
    confidence = str(review.get("confidence") or "-")
    model = str(review.get("model") or "-")
    created = str(review.get("created_at") or "-")
    summary = str(review.get("summary") or "")

    lines = [
        f"VERDICT: {verdict}    CONFIDENCE: {confidence}",
        f"Model: {model}    Reviewed: {created}",
        "",
        summary,
    ]

    sections = [
        ("Positive factors", review.get("positive_factors") or []),
        ("Risks", review.get("risk_factors") or []),
        ("Missing information", review.get("missing_information") or []),
        ("Next signals to watch", review.get("next_signals_to_watch") or []),
    ]
    for title, values in sections:
        lines.extend(["", title + ":"])
        if values:
            lines.extend(f"  • {value}" for value in values)
        else:
            lines.append("  • None identified from the supplied alert data")
    return "\n".join(lines)


class AITickerMemoryPage(base.TickerMemoryPage):
    def __init__(self, repo: base.DashboardRepository) -> None:
        super().__init__(repo)
        self.review_store = AIReviewStore(repo.database_path)
        self.settings = QSettings("TrendVisionAI", "TrendVisionAI")
        self._worker: ReviewWorker | None = None
        self._active_snapshot: dict[str, Any] | None = None

        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 13, 16, 13)
        card_layout.setSpacing(8)

        top = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("AI Candidate Review")
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        subtitle = QLabel(
            "Manual review using only the TrendVision data already captured for this ticker."
        )
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        top.addLayout(title_box, 1)
        self.analyze_button = QPushButton("Analyze with AI")
        self.analyze_button.setObjectName("primary")
        self.analyze_button.clicked.connect(self.analyze_current)
        top.addWidget(self.analyze_button)
        card_layout.addLayout(top)

        self.review_status = QLabel(
            "Open a ticker, then analyze it when you want a second-pass review."
        )
        self.review_status.setObjectName("muted")
        card_layout.addWidget(self.review_status)

        self.review_text = QTextEdit()
        self.review_text.setReadOnly(True)
        self.review_text.setMaximumHeight(230)
        self.review_text.setPlaceholderText("No AI review saved for this ticker yet.")
        card_layout.addWidget(self.review_text)

        layout = self.layout()
        if layout is not None:
            layout.addWidget(card)

    def load_ticker(self, ticker: str) -> None:
        super().load_ticker(ticker)
        self._load_latest_review()

    def _load_latest_review(self) -> None:
        if not self.current_ticker:
            return
        latest = self.review_store.latest(self.current_ticker)
        if latest is None:
            self.review_text.clear()
            self.review_status.setText(
                f"No saved AI review for {self.current_ticker}. Analysis is manual and only runs when you click the button."
            )
            return
        self.review_text.setPlainText(_format_review(latest))
        self.review_status.setText(f"Showing latest saved AI review for {self.current_ticker}.")

    def analyze_current(self) -> None:
        ticker = self.current_ticker.upper().strip()
        if not ticker:
            self.review_status.setText("Open a ticker first.")
            return
        if self._worker is not None and self._worker.isRunning():
            return

        api_key = get_api_key()
        if not api_key:
            self.review_status.setText(
                "OpenAI API key is not configured. Add it under Listener & System, then try again."
            )
            return

        state = self.repo.ticker_state(ticker)
        if state is None:
            self.review_status.setText(f"No stored scanner events for {ticker}.")
            return

        convergence = self.repo.convergence(ticker, 30)
        if not convergence.get("events"):
            self.review_status.setText(
                f"{ticker} has no scanner events inside the current 30-minute review window."
            )
            return

        attention = asdict(evaluate_attention(convergence))
        snapshot = build_review_snapshot(
            ticker=ticker,
            state=state,
            convergence=convergence,
            attention=attention,
        )
        model = str(self.settings.value("openai/model", DEFAULT_MODEL) or DEFAULT_MODEL).strip()

        self._active_snapshot = snapshot
        self.analyze_button.setEnabled(False)
        self.analyze_button.setText("Analyzing...")
        self.review_status.setText(
            f"Analyzing {ticker} with {model}. No external market-data lookup is being performed."
        )

        worker = ReviewWorker(snapshot=snapshot, model=model, api_key=api_key)
        self._worker = worker
        worker.completed.connect(self._analysis_completed)
        worker.failed.connect(self._analysis_failed)
        worker.finished.connect(self._analysis_finished)
        worker.start()

    def _analysis_completed(self, result: AIReviewResult) -> None:
        snapshot = self._active_snapshot or {}
        self.review_store.save(result, snapshot)
        data = result.to_dict()
        self.review_text.setPlainText(_format_review(data))
        self.review_status.setText(
            f"Review saved for {result.ticker}. Verdict: {result.verdict} ({result.confidence} confidence)."
        )

    def _analysis_failed(self, message: str) -> None:
        self.review_status.setText("AI review failed. Check Listener & System for API configuration.")
        self.review_text.setPlainText(message)

    def _analysis_finished(self) -> None:
        self.analyze_button.setEnabled(True)
        self.analyze_button.setText("Analyze with AI")
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None
        self._active_snapshot = None


class AISystemPage(base.SystemPage):
    def __init__(self, database_path) -> None:
        super().__init__(database_path)
        self.settings = QSettings("TrendVisionAI", "TrendVisionAI")

        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 13, 16, 13)
        layout.setSpacing(8)

        title = QLabel("OpenAI Candidate Review")
        title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        layout.addWidget(title)

        description = QLabel(
            "The API is used only when you manually click Analyze with AI. The key is stored in Windows Credential Manager, not in config.json or GitHub."
        )
        description.setObjectName("muted")
        description.setWordWrap(True)
        layout.addWidget(description)

        key_row = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText("Paste a new OpenAI API key to save it")
        key_row.addWidget(QLabel("API key"))
        key_row.addWidget(self.api_key_input, 1)
        save_button = QPushButton("Save key")
        save_button.setObjectName("primary")
        save_button.clicked.connect(self._save_key)
        remove_button = QPushButton("Remove")
        remove_button.setObjectName("secondary")
        remove_button.clicked.connect(self._remove_key)
        key_row.addWidget(save_button)
        key_row.addWidget(remove_button)
        layout.addLayout(key_row)

        model_row = QHBoxLayout()
        self.model_input = QLineEdit()
        self.model_input.setText(
            str(self.settings.value("openai/model", DEFAULT_MODEL) or DEFAULT_MODEL)
        )
        self.model_input.setPlaceholderText(DEFAULT_MODEL)
        model_row.addWidget(QLabel("Model"))
        model_row.addWidget(self.model_input, 1)
        save_model = QPushButton("Save model")
        save_model.setObjectName("secondary")
        save_model.clicked.connect(self._save_model)
        model_row.addWidget(save_model)
        layout.addLayout(model_row)

        self.api_status = QLabel()
        self.api_status.setObjectName("muted")
        layout.addWidget(self.api_status)
        self._refresh_api_status()

        root = self.layout()
        if root is not None:
            root.insertWidget(3, card)

    def _refresh_api_status(self) -> None:
        if get_api_key():
            source = "OPENAI_API_KEY environment variable" if os.getenv("OPENAI_API_KEY") else "Windows Credential Manager"
            self.api_status.setText(f"API key configured via {source}.")
        else:
            self.api_status.setText("No OpenAI API key configured yet. Listener/scanner features still work normally.")

    def _save_key(self) -> None:
        value = self.api_key_input.text().strip()
        if not value:
            self.api_status.setText("Paste a key before clicking Save key.")
            return
        try:
            save_api_key(value)
        except Exception as exc:
            self.api_status.setText(f"Could not save key: {type(exc).__name__}: {exc}")
            return
        self.api_key_input.clear()
        self._refresh_api_status()

    def _remove_key(self) -> None:
        delete_api_key()
        self.api_key_input.clear()
        self._refresh_api_status()

    def _save_model(self) -> None:
        model = self.model_input.text().strip() or DEFAULT_MODEL
        self.model_input.setText(model)
        self.settings.setValue("openai/model", model)
        self.api_status.setText(f"Model saved: {model}")


# Patch the already-working desktop shell rather than duplicating it. MainWindow
# resolves these classes from desktop_ui's module globals when it is created.
base.TickerMemoryPage = AITickerMemoryPage
base.SystemPage = AISystemPage


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
