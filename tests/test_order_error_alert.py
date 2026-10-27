"""Real-money re-entry/add orders that end in ERROR reach the maintenance chat the same day."""
import importlib.util
import subprocess
import sys
from pathlib import Path

from prism_core import ops_alert

ROOT = Path(__file__).resolve().parents[1]


def _capture(monkeypatch):
    sent = []

    async def fake(text):
        sent.append(text)
        return True
    monkeypatch.setattr(ops_alert, "send_ops_alert", fake)
    return sent


def test_alert_lists_each_failed_order_and_redacts_tokens(monkeypatch):
    sent = _capture(monkeypatch)
    token = "123456789:" + "A" * 35
    assert ops_alert.alert_order_errors("재진입", "US", [("NVDA", "executor_error:RuntimeError"),
                                                        ("ARM", f"bot{token} failed")], "logs/x.log")
    assert len(sent) == 1
    assert "US 재진입 2건" in sent[0] and "NVDA: executor_error:RuntimeError" in sent[0]
    assert token not in sent[0] and "logs/x.log" in sent[0]


def test_no_failures_sends_nothing(monkeypatch):
    sent = _capture(monkeypatch)
    assert ops_alert.alert_order_errors("증액", "KR", [], "x") is False
    assert sent == []


def test_add_runner_crash_alerts(monkeypatch):
    sent = _capture(monkeypatch)
    spec = importlib.util.spec_from_file_location("run_b3_ae_shadow", ROOT / "tools/run_b3_ae_shadow.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    from prism_core import micro_split_live
    monkeypatch.setattr(micro_split_live, "live_enabled", lambda market: True)
    monkeypatch.setattr(micro_split_live, "plan_adds_enabled", lambda market: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "ImportError: x"))
    result = runner.live_add_provider("US")({"symbol": "AMD"}, {}, "now")
    assert result["status"] == "ERROR"
    assert len(sent) == 1 and "US 증액 1건" in sent[0] and "AMD: ImportError" in sent[0]


def test_buy_rejection_keeps_broker_refusals_and_drops_intentional_skips():
    # messages seen in production logs 10/05-10/18
    assert ops_alert.buy_rejection({"success": False, "message": "Buy failed: Buy order failed: APBKN - 해당종목정보가 없습니다."})
    for skipped in ({"success": False, "message": "Buy quantity is 0 (amount: $412.00)"},
                    {"success": False, "message": "Micro-split budget below one share; strategy entry preserved"},
                    {"success": False, "message": "Pilot broker budget unavailable"},
                    {"success": False, "status": "blocked_budget", "reason_code": "micro_split_add_below_one_share"},
                    {"success": True, "message": "Buy order accepted"}, None):
        assert ops_alert.buy_rejection(skipped) is None


def test_async_buy_rejection_alert(monkeypatch):
    import asyncio
    sent = _capture(monkeypatch)
    assert asyncio.run(ops_alert.alert_buy_rejected("US", "APBKN", {"success": False, "message": "해당종목정보가 없습니다"}, "x"))
    assert not asyncio.run(ops_alert.alert_buy_rejected("US", "TSM", {"success": False, "message": "Buy quantity is 0"}, "x"))
    assert len(sent) == 1 and "US 매수(신규·재진입) 1건" in sent[0]


def test_add_broker_refusal_alerts(monkeypatch):
    sent = _capture(monkeypatch)
    spec = importlib.util.spec_from_file_location("run_b3_ae_shadow", ROOT / "tools/run_b3_ae_shadow.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    from prism_core import micro_split_live
    monkeypatch.setattr(micro_split_live, "live_enabled", lambda market: True)
    monkeypatch.setattr(micro_split_live, "plan_adds_enabled", lambda market: True)
    out = '{"status": "EXECUTED", "broker": {"success": false, "message": "주문가능금액 부족"}}'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    assert runner.live_add_provider("KR")({"symbol": "005930"}, {}, "now")["status"] == "EXECUTED"
    out = '{"status": "EXECUTED", "broker": {"success": false, "status": "blocked_budget"}}'
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    runner.live_add_provider("KR")({"symbol": "000660"}, {}, "now")
    assert len(sent) == 1 and "005930: 주문가능금액 부족" in sent[0]


def test_worker_stop_between_ticks_exits_cleanly(monkeypatch):
    # systemd stops the unit with SIGINT; that must not print a traceback the watchdog reads as a crash.
    spec = importlib.util.spec_from_file_location("run_b3_ae_shadow", ROOT / "tools/run_b3_ae_shadow.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    from observability import b3_ae_capture
    from prism_core import micro_split_live
    monkeypatch.setattr(b3_ae_capture, "enabled", lambda: False)
    monkeypatch.setattr(micro_split_live, "live_enabled", lambda market: False)
    monkeypatch.setattr(runner, "write_health", lambda *a: None)
    monkeypatch.setattr(sys, "argv", ["run_b3_ae_shadow.py", "--market", "us"])

    def interrupted(_):
        raise KeyboardInterrupt
    monkeypatch.setattr(runner.time, "sleep", interrupted)
    assert runner.main() == 0
