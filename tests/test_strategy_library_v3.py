from trendvision_ai.strategy_library_v3 import _stabilize_match


def test_first_pullback_instance_does_not_change_with_live_entry_reference():
    base = {
        "strategy_id": "FIRST_PULLBACK",
        "instance_key": "FIRST_PULLBACK|20260811T1402|1.8750",
        "key_levels": {
            "pullback_low": 1.72,
            "entry_reference": 1.875,
        },
    }
    later = {
        "strategy_id": "FIRST_PULLBACK",
        "instance_key": "FIRST_PULLBACK|20260811T1402|1.9100",
        "key_levels": {
            "pullback_low": 1.72,
            "entry_reference": 1.91,
        },
    }

    assert _stabilize_match(base)["instance_key"] == _stabilize_match(later)["instance_key"]


def test_new_first_pullback_low_creates_new_instance():
    first = {
        "strategy_id": "FIRST_PULLBACK",
        "instance_key": "FIRST_PULLBACK|20260811T1402|1.8750",
        "key_levels": {"pullback_low": 1.72, "entry_reference": 1.875},
    }
    second = {
        "strategy_id": "FIRST_PULLBACK",
        "instance_key": "FIRST_PULLBACK|20260811T1406|1.9000",
        "key_levels": {"pullback_low": 1.76, "entry_reference": 1.90},
    }

    assert _stabilize_match(first)["instance_key"] != _stabilize_match(second)["instance_key"]


def test_vwap_reclaim_ignores_small_vwap_drift_for_same_reclaim_bar():
    first = {
        "strategy_id": "VWAP_RECLAIM_HOLD",
        "instance_key": "VWAP_RECLAIM_HOLD|20260811T1410|2.0041",
        "key_levels": {"session_vwap": 2.0041, "entry_reference": 2.0041},
    }
    later = {
        "strategy_id": "VWAP_RECLAIM_HOLD",
        "instance_key": "VWAP_RECLAIM_HOLD|20260811T1410|2.0063",
        "key_levels": {"session_vwap": 2.0063, "entry_reference": 2.0063},
    }

    assert _stabilize_match(first)["instance_key"] == _stabilize_match(later)["instance_key"]
