"""Real-money re-entry/add orders that end in ERROR reach the maintenance chat the same day."""
import importlib.util
import subprocess
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
