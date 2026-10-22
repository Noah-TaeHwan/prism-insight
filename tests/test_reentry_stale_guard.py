"""Re-entry v3 R2S stale-level guard (REENTRY_V3_STALE_LEVEL_GUARD, default off).

KR 028670 팬오션 2026-10-21: the 10/07 held-off watch's report resistances (6,320 / 6,530) were both under
the 6,570 price, so the R/R target fell to the prior-60 high 7,830, a 10/15 intraday wick, and the
pullback buy passed the strong-bear floor (R/R 3.07). Replay (tasks/research-20261022/reentry-fix, current
enrolment, closes, E1 exits): no big winner or runner lost in KR or US, removed R2S tries were losers.
"""
from datetime import date, timedelta

import pytest

from observability import reentry_v3_shadow as V3
from prism_core import reentry_campaign as C

PANOCEAN = {"primary_support": 6000, "secondary_support": 5902, "primary_resistance": 6320,
            "secondary_resistance": 6530}


def _bars(n=70, close=6450.0):
    out, day = [], date(2026, 7, 1)
    for k in range(n):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        high = 7830.0 if k == n - 5 else close * 1.004            # the climax-day wick, 5 sessions ago
        out.append({"date": day.isoformat(), "open": close, "high": high, "low": close * 0.996, "close": close,
                    "volume": 1000.0})
        day += timedelta(days=1)
    return out


def _retest(levels, guard, price=6570.0, low=6330.0, source="LOCATION_SKIP"):
    setup = C.make_setup(source, levels, 6240, 6240, "2026-07-01")
    bars = _bars()
    policy = dict(C.POLICY, r2s_stale_guard=guard)
    return C.evaluate(setup, bars=bars, i=len(bars), flags={"armed": True, "above": True}, price=price,
                      day_low=low, regime="strong_bear", policy=policy)


def test_028670_stale_levels_fire_on_a_wick_target_without_the_guard():
    out = _retest(PANOCEAN, guard=False)
    trigger = out["trigger"]
    assert trigger["trigger"] == "R2S" and trigger["target_source"] == "prior_60_high"
    assert trigger["resistance"] == 7830.0 and trigger["rr"] >= trigger["rr_floor"] == 1.8


def test_the_guard_blocks_the_stale_pullback_with_a_stable_reason():
    out = _retest(PANOCEAN, guard=True)
    assert out["trigger"] is None and out["checks"]["R2S"] == C.STALE_LEVELS == "stale_levels"


def test_a_fresh_resistance_above_the_price_still_fires():
    fresh = dict(PANOCEAN, secondary_resistance=7400)
    trigger = _retest(fresh, guard=True)["trigger"]
    assert trigger["trigger"] == "R2S" and trigger["target_source"] == "secondary_resistance"


def test_no_report_resistance_is_not_called_stale():
    levels = {"primary_support": 6000, "secondary_support": 5902, "primary_resistance": None,
              "secondary_resistance": None}
    setup = C.make_setup("STOP_EXIT", dict(levels, primary_support=6320), 6600, 6300, "2026-07-01")
    assert not C.stale_levels(setup, 6570.0)


def test_r1c_rebreak_is_not_affected():
    setup = C.make_setup("LOCATION_SKIP", dict(PANOCEAN, secondary_resistance=None), 6240, 6240, "2026-07-01")
    bars = _bars(close=6300.0)
    out = C.evaluate(setup, bars=bars, i=len(bars), flags={"armed": True, "above": False}, price=6400.0,
                     day_low=6280.0, regime="strong_bear", policy=dict(C.POLICY, r2s_stale_guard=True))
    assert C.stale_levels(setup, 6400.0) and out["trigger"]["trigger"] == "R1C"


@pytest.mark.parametrize("value,expected", [(None, False), ("false", False), ("true", True), ("on", True)])
def test_the_switch_defaults_off_and_follows_the_environment(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("REENTRY_V3_STALE_LEVEL_GUARD", raising=False)
    else:
        monkeypatch.setenv("REENTRY_V3_STALE_LEVEL_GUARD", value)
    assert C.stale_guard_enabled(C.POLICY) is expected
    out = _retest(PANOCEAN, guard=None)
    assert (out["trigger"] is None) is expected


@pytest.mark.parametrize("market", ["KR", "US"])
def test_the_block_is_kept_as_a_near_miss_and_the_switch_in_the_ledger(market, monkeypatch):
    monkeypatch.setenv("REENTRY_V3_STALE_LEVEL_GUARD", "true")
    out = _retest(PANOCEAN, guard=None)
    decision = {"trigger": None, "checks": {"L97": out["checks"]}, "details": {"L97": out["details"]}}
    assert ("R2S", "stale_levels") in [(t, r) for t, r, _ in V3.near_misses(decision)]
    setup = C.make_setup("LOCATION_SKIP", PANOCEAN, 6240, 6240, "2026-07-01")
    ledger = C.replay(setup, _bars(), 0, market=None)
    assert ledger["rule_ids"]["r2s_stale_guard"] is True
