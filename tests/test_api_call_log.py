from __future__ import annotations

from trendvision_ai.api_call_log import OpenAIApiCallStore


def test_api_call_journal_records_completion(tmp_path):
    store = OpenAIApiCallStore(tmp_path / "calls.db")
    call_id = store.start_call(
        ticker="ABCD",
        purpose="AUTOMATIC TRADE PLAN",
        model="gpt-5.6-terra",
        reasoning_effort="medium",
        strategy_id="HOD_BREAKOUT",
        strategy_name="High-of-Day Breakout",
        strategy_score=91,
    )

    terra_response = '{"decision":"WATCH","summary":"Wait for confirmation."}'
    store.finish_call(
        call_id,
        status="COMPLETED",
        duration_ms=1234,
        decision="WATCH",
        response_text=terra_response,
    )

    rows = store.list_calls()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "ABCD"
    assert rows[0]["model"] == "gpt-5.6-terra"
    assert rows[0]["strategy_id"] == "HOD_BREAKOUT"
    assert rows[0]["strategy_score"] == 91
    assert rows[0]["status"] == "COMPLETED"
    assert rows[0]["decision"] == "WATCH"
    assert rows[0]["duration_ms"] == 1234
    assert rows[0]["response_text"] == terra_response

    stats = store.stats()
    assert stats["total"] == 1
    assert stats["completed"] == 1
    assert stats["failed"] == 0
    assert stats["in_progress"] == 0


def test_api_call_journal_records_failure(tmp_path):
    store = OpenAIApiCallStore(tmp_path / "calls.db")
    call_id = store.start_call(
        ticker="FAIL",
        purpose="AUTOMATIC TRADE PLAN",
        model="gpt-5.6-terra",
    )
    store.finish_call(
        call_id,
        status="FAILED",
        duration_ms=500,
        error_text="Example failure",
        response_text='{"partial":"response"}',
    )

    row = store.list_calls()[0]
    assert row["status"] == "FAILED"
    assert row["error_text"] == "Example failure"
    assert row["response_text"] == '{"partial":"response"}'
    assert store.stats()["failed"] == 1
