"""10/19~21: US re-entry orders failed as `error:ModuleNotFoundError` only - the cause (`No module named
'cores.buy_gate'`) and the child's traceback were dropped, so nobody saw a system fault. Keep both."""
import logging
import subprocess
from types import SimpleNamespace

from prism_core import reentry_v3_live as live


def test_error_reason_keeps_the_message_and_redacts_it():
    reason = live._error_reason("error", ModuleNotFoundError("No module named 'cores.buy_gate'"))
    assert reason == "error:ModuleNotFoundError: No module named 'cores.buy_gate'"
    assert live.outcome_status({"bought": False, "reason": reason}) == "ERROR"
    assert live._error_reason("executor_error", RuntimeError()) == "executor_error:RuntimeError"
    token = "123456789:" + "A" * 35
    assert token not in live._error_reason("error", RuntimeError(f"bot{token}/sendMessage failed"))


def _child(monkeypatch, stdout, stderr, returncode=0):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=stdout, stderr=stderr,
                                                                          returncode=returncode))


def test_a_failed_child_order_keeps_its_traceback_in_the_parent_log(monkeypatch, caplog):
    _child(monkeypatch, '{"outcomes": {"k": {"bought": false, "reason": "error:ModuleNotFoundError: x"}}}',
           "Traceback (most recent call last):\nModuleNotFoundError: No module named 'cores.buy_gate'")
    with caplog.at_level(logging.WARNING, logger=live.__name__):
        out = live.subprocess_executor("US", [{"key": "k"}])
    assert out["k"]["reason"].startswith("error:ModuleNotFoundError")
    assert "No module named 'cores.buy_gate'" in caplog.text and "stderr tail" in caplog.text


def test_a_clean_child_run_logs_nothing_extra(monkeypatch, caplog):
    _child(monkeypatch, '{"outcomes": {"k": {"bought": true, "reason": "bought"}}}', "INFO noise")
    with caplog.at_level(logging.WARNING, logger=live.__name__):
        live.subprocess_executor("KR", [{"key": "k"}])
    assert "stderr tail" not in caplog.text
