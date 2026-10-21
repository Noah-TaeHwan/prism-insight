"""Operations watchdog (tools/ops_watchdog.py): failed or missing jobs reach the maintenance chat (2026-10-21)."""
import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from tools import ops_watchdog as W

ROOT = Path(__file__).resolve().parents[1]
KST = W.KST


def _job(key):
    return next(job for job in W.JOBS if job.key == key)


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "ROOT", tmp_path)
    (tmp_path / "logs").mkdir()
    return tmp_path / "runtime" / "state.json"


def _write(tmp_path, rel, text, mode="a"):
    path = tmp_path / rel
    with path.open(mode, encoding="utf-8") as handle:
        handle.write(text)


def test_heartbeats_and_failure_strings_exist_in_the_job_sources():
    # The watchdog reads these lines; if a loop renames them the watchdog would go blind.
    sources = {
        "tools/hardstop_seller.py": ['"Hardstop %s summary: %s"', "KIS sell success=%s", "KIS sell failed after sim close",
                                     "trading context failed", "holdings load failed", "sell action failed"],
        "tools/trend_exit_seller.py": ['"Trend-exit %s summary: %s"', "KIS sell success=%s", "trading context failed",
                                       "live regime compute failed", "price fetch failed"],
        "tools/fill_chaser.py": ['"Fill-chaser %s summary: %s"', "amend success=False", "cancel success=False",
                                 "open-order inquiry failed"],
        "prism_core/reentry_v3_live.py": ["[REENTRY_V3_LIVE][%s] entry failed for %s",
                                          "[REENTRY_V3_LIVE][%s] executor failed"],
        "observability/reentry_v3_shadow.py": ['"live_results"', '"phase": phase'],
        "prism_core/sell_facts.py": ["[sell_facts]"],
    }
    for path, needles in sources.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        for needle in needles:
            assert needle in text, (path, needle)


def test_scan_finds_order_failures_tracebacks_and_skips_logging_slips():
    now = datetime(2026, 10, 21, 2, 55, tzinfo=KST)
    lines = [
        "2026-10-21 02:52:18,881 - prism_core.reentry_v3_live - ERROR - [REENTRY_V3_LIVE][US] entry failed for NVDA",
        "Traceback (most recent call last):",
        '  File "x.py", line 1, in <module>',
        "ModuleNotFoundError: No module named 'cores.buy_gate'",
        "2026-10-21 02:53:00,000 - hardstop - WARNING - [LIVE][US] FRO KIS sell success=False order_no=None msg=rejected",
        "2026-10-21 02:53:01,000 - hardstop - WARNING - US trading context failed: token expired",
        "--- Logging error ---",
        "Traceback (most recent call last):",
        "requests.exceptions.JSONDecodeError: Expecting value",
        "During handling of the above exception, another exception occurred:",
        "Traceback (most recent call last):",
        "TypeError: not all arguments converted during string formatting",
        "Call stack:",
        "2026-10-21 02:54:00,000 - hardstop - INFO - Hardstop US summary: {'checked': 5}",
        "2026-10-21 02:54:01,000 - cores.market_data - ERROR - price_history: no source could answer (kis: rate)",
    ]
    beats, failures, _ = W.scan_lines(_job("hardstop_us"), lines, now)
    labels = [(f["label"], f["severity"], f["market"]) for f in failures]
    assert ("재진입 주문 실패", "장애", "US") in labels
    assert ("프로그램 오류", "장애", "US") in labels
    assert ("매도 주문 실패", "장애", "US") in labels and ("시장 전체 건너뜀", "장애", "US") in labels
    assert ("데이터 조회 실패", "주의", "US") in labels
    assert not any("not all arguments" in f["text"] for f in failures)       # the logging slip is ignored
    assert beats == ["2026-10-21T02:54:00+09:00"]
    assert not any("sell success=True" in f["text"] for f in failures)


def test_known_non_failures_are_ignored():
    now = datetime(2026, 10, 21, 3, 0, tzinfo=KST)
    lines = ["2026-10-21 03:00:00,000 - yfinance - ERROR - $SVA: possibly delisted; no price data found",
             "2026-10-21 03:00:01,000 - x - ERROR - Actual purchase failed: Micro-split budget below one share"]
    assert W.scan_lines(_job("us_morning"), lines, now)[1] == []


def test_reentry_summary_with_order_errors_is_a_failure_and_counts():
    now = datetime(2026, 10, 21, 3, 0, tzinfo=KST)
    summary = {"phase": "intraday", "decision_day": "2026-10-20", "new_triggers": 2, "live_results": {"ERROR": 2}}
    beats, failures, reentry = W.scan_lines(_job("reentry_us"), [json.dumps(summary)], now)
    assert len(beats) == 1 and reentry == [summary]
    assert failures[0]["label"] == "재진입 주문 실패" and "주문 오류 2건" in failures[0]["text"]
    ok = dict(summary, live_results={"BOUGHT": 1, "NOT_BOUGHT": 1})
    assert W.scan_lines(_job("reentry_us"), [json.dumps(ok)], now)[1] == []


def test_run_alerts_once_then_holds_repeats_and_sends_the_count(tmp_path, monkeypatch):
    state = _setup(tmp_path, monkeypatch)
    log = "logs/loop_a_hardstop.log"
    _write(tmp_path, log, "", "w")
    sent = []
    t0 = datetime(2026, 10, 21, 10, 0, tzinfo=KST)
    assert W.run(t0, state_path=state, sender=lambda m: sent.append(m) or True) == []   # first run: start at EOF
    fail = "2026-10-21 10:01:00,000 - hardstop - WARNING - [LIVE][KR] 005930 KIS sell success=False msg=x\n"
    _write(tmp_path, log, fail)
    W.run(t0.replace(minute=5), state_path=state, sender=lambda m: sent.append(m) or True)
    assert len(sent) == 1 and sent[0].startswith("[장애]") and "손절 감시(한국) — 매도 주문 실패 1건" in sent[0]
    _write(tmp_path, log, fail + fail)
    W.run(t0.replace(minute=10), state_path=state, sender=lambda m: sent.append(m) or True)
    assert len(sent) == 1                                                  # held back inside REPEAT_HOURS
    _write(tmp_path, log, fail)
    W.run(t0.replace(hour=16, minute=30), state_path=state, sender=lambda m: sent.append(m) or True)
    assert len(sent) == 2 and "매도 주문 실패 3건, 2번째 알림" in sent[1]


def test_stopped_loop_alerts_inside_its_window_and_recovers(tmp_path, monkeypatch):
    state = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(W, "_is_session", lambda market, day: day.weekday() < 5)
    log = "logs/loop_a_hardstop.log"
    _write(tmp_path, log, "", "w")
    sent = []
    send = lambda m: sent.append(m) or True  # noqa: E731
    W.run(datetime(2026, 10, 21, 9, 0, tzinfo=KST), state_path=state, sender=send)   # Wednesday
    _write(tmp_path, log, "2026-10-21 09:20:00,000 - hardstop - INFO - Hardstop KR summary: {}\n")
    W.run(datetime(2026, 10, 21, 9, 25, tzinfo=KST), state_path=state, sender=send)
    assert sent == []
    W.run(datetime(2026, 10, 21, 9, 55, tzinfo=KST), state_path=state, sender=send)
    assert len(sent) == 1 and "손절 감시(한국) — 작업 멈춤" in sent[0] and "09:20" in sent[0]
    W.run(datetime(2026, 10, 21, 10, 0, tzinfo=KST), state_path=state, sender=send)
    assert len(sent) == 1                                                  # one alert per stop
    _write(tmp_path, log, "2026-10-21 10:02:00,000 - hardstop - INFO - Hardstop KR summary: {}\n")
    W.run(datetime(2026, 10, 21, 10, 5, tzinfo=KST), state_path=state, sender=send)
    assert len(sent) == 2 and "[복구] 손절 감시(한국)" in sent[1]


def test_no_stale_alert_outside_session_or_window(tmp_path, monkeypatch):
    state = {"started": "2026-10-24T08:00:00+09:00"}
    monkeypatch.setattr(W, "_is_session", lambda market, day: day.weekday() < 5)
    saturday = datetime(2026, 10, 24, 12, 0, tzinfo=KST)
    assert W.stale_jobs(state, saturday) == []
    evening = datetime(2026, 10, 21, 18, 0, tzinfo=KST)                   # KR closed, US not open yet
    assert [j.key for j, _, _ in W.stale_jobs({"started": "2026-10-21T08:00:00+09:00"}, evening)] == ["reentry_kr"]


def test_daily_reentry_deadline_and_morning_summary(tmp_path, monkeypatch):
    state = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(W, "_is_session", lambda market, day: day.weekday() < 5)
    for job in W.JOBS:
        if "{date}" not in job.log:
            _write(tmp_path, job.log, "", "w")
    sent = []
    send = lambda m: sent.append(m) or True  # noqa: E731
    W.run(datetime(2026, 10, 20, 20, 0, tzinfo=KST), state_path=state, sender=send)
    summary = {"phase": "intraday", "decision_day": "2026-10-20", "new_triggers": 2, "live_results": {"ERROR": 2}}
    _write(tmp_path, "logs/reentry_v3_shadow_us.log", json.dumps(summary) + "\n")
    W.run(datetime(2026, 10, 21, 2, 55, tzinfo=KST), state_path=state, sender=send)
    assert "재진입(미국) — 재진입 주문 실패 1건" in sent[-1]
    W.run(datetime(2026, 10, 21, 8, 30, tzinfo=KST), state_path=state, sender=send)
    daily = sent[-1]
    assert daily.startswith("[운영 점검] 감시 정상 작동")
    assert "재진입(미국): 실행 1회, 신호 2건 → 매수 0건, 주문 오류 2건" in daily
    W.run(datetime(2026, 10, 21, 8, 35, tzinfo=KST), state_path=state, sender=send)
    assert sent[-1] == daily                                               # once a day


def test_daily_logs_are_read_from_the_start_when_they_appear(tmp_path, monkeypatch):
    state = _setup(tmp_path, monkeypatch)
    sent = []
    send = lambda m: sent.append(m) or True  # noqa: E731
    W.run(datetime(2026, 10, 21, 9, 0, tzinfo=KST), state_path=state, sender=send)
    _write(tmp_path, "us_stock_tracking_20261021.log",
           "2026-10-21 09:01:00,000 - x - CRITICAL - database is locked\n", "w")
    W.run(datetime(2026, 10, 21, 9, 5, tzinfo=KST), state_path=state, sender=send)
    assert sent and "매매 판단(미국) — 심각 오류 1건" in sent[0]


def test_alert_text_is_redacted_and_bounded():
    now = datetime(2026, 10, 21, 9, 0, tzinfo=KST)
    line = "2026-10-21 09:00:00,000 - x - ERROR - bot123456789:" + "A" * 35 + " failed " + "y" * 400
    _, failures, _ = W.scan_lines(_job("us_tracking"), [line], now)
    assert "A" * 35 not in failures[0]["text"] and len(failures[0]["text"]) <= 220
    many = [(dict(failures[0], text=f"{i} " + "z" * 200), 1, 1) for i in range(60)]
    assert len(W.build_alert(many, [], [], now)) <= W.MESSAGE_LIMIT


def test_switch_off_and_cron_safety(monkeypatch, capsys):
    monkeypatch.setenv("OPS_WATCHDOG_ENABLED", "false")
    assert W.main([]) == 0 and "skipped" in capsys.readouterr().out
    monkeypatch.setenv("OPS_WATCHDOG_ENABLED", "true")
    monkeypatch.setattr(W, "run", lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert W.main([]) == 1                                                 # reported, never raised


@pytest.mark.parametrize("job", W.JOBS, ids=lambda j: j.key)
def test_job_definitions_are_consistent(job):
    if job.heartbeat:
        re.compile(job.heartbeat)
        assert job.market in W.TZ and (job.every_min and job.window or job.deadline)
