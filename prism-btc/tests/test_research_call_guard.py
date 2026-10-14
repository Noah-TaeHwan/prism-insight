"""Offline durable call guard checks; no provider or network is imported."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.btc_research_call_guard import CallGuard, CallGuardError


REGISTRY = "a" * 64
REQUEST = "b" * 64


def guard(root, cap=24):
    return CallGuard(root, REGISTRY, cap)


def reserve(g, arm="A"):
    return g.reserve(arm, "DRAFT", REQUEST)


def test_success_reopen_and_no_refunds(tmp_path):
    g = guard(tmp_path, 2)
    g.finish(reserve(g), "ok", actual_http_calls=0)
    g = guard(tmp_path, 2)
    g.finish(reserve(g, "B"), "ok", actual_http_calls=1)
    assert g.snapshot()["actual_http_calls"] == 1
    assert g.snapshot()["reserved_attempts"] == 2
    with pytest.raises(CallGuardError):
        reserve(g)
    assert g.snapshot()["stop_reason"] == "call_cap_reached"


@pytest.mark.parametrize("cap", [24, 144])
def test_exact_registered_global_caps(tmp_path, cap):
    g = guard(tmp_path, cap)
    for i in range(cap):
        g.finish(reserve(g, "A" if i % 2 else "B"), "ok", 1)
    with pytest.raises(CallGuardError):
        reserve(g)
    state = g.snapshot()
    assert state["reserved_attempts"] == state["actual_http_calls"] == cap


@pytest.mark.parametrize("change", ["registry", "cap"])
def test_frozen_registry_and_cap(tmp_path, change):
    g = guard(tmp_path)
    before = g.ledger.read_bytes()
    with pytest.raises(CallGuardError):
        CallGuard(tmp_path, "c" * 64 if change == "registry" else REGISTRY,
                  144 if change == "cap" else 24)
    assert g.ledger.read_bytes() == before


@pytest.mark.parametrize("damage", ["tamper", "partial_tail", "whole_tail", "missing_ledger", "missing_anchor"])
def test_corruption_fails_closed(tmp_path, damage):
    g = guard(tmp_path)
    g.finish(reserve(g), "ok", 1)
    raw = g.ledger.read_bytes()
    if damage == "tamper":
        g.ledger.write_bytes(raw.replace(b'"DRAFT"', b'"OTHER"'))
    elif damage == "partial_tail":
        g.ledger.write_bytes(raw[:-3])
    elif damage == "whole_tail":
        g.ledger.write_bytes(b"\n".join(raw.splitlines()[:-1]) + b"\n")
    elif damage == "missing_ledger":
        g.ledger.unlink()
    else:
        (g.anchors / "00000002.sha256").unlink()
    with pytest.raises(CallGuardError):
        guard(tmp_path)
    with pytest.raises(CallGuardError):
        reserve(g)


def test_restart_after_reserve_never_resumes(tmp_path):
    g = guard(tmp_path)
    attempt = reserve(g)
    original = g.ledger.read_bytes()
    reopened = guard(tmp_path)
    assert reopened.snapshot()["stop_reason"] == "unfinished_attempt"
    assert reopened.snapshot()["unknown_http_attempts"] == 1
    assert reopened.ledger.read_bytes().startswith(original)
    # Even a delayed finish cannot clear the crash latch or refund its budget.
    reopened.finish(attempt, "ok", 1)
    with pytest.raises(CallGuardError):
        reserve(reopened, "B")
    assert reopened.snapshot()["reserved_attempts"] == 1


def test_first_arm_failure_blocks_other_arm_without_http(tmp_path):
    g = guard(tmp_path)
    actual_http = []
    attempt = reserve(g)
    actual_http.append("A")
    g.finish(attempt, "error", 1)
    with pytest.raises(CallGuardError):
        reserve(g, "B")
        actual_http.append("B")
    assert actual_http == ["A"]
    assert guard(tmp_path).snapshot()["stop_reason"] == "attempt_error"


def test_unknown_is_not_zero_and_stops(tmp_path):
    g = guard(tmp_path)
    g.finish(reserve(g), "ok")
    state = g.snapshot()
    assert state["unknown_http_attempts"] == 1
    assert state["stopped"]
    assert state["stop_reason"] == "unknown_http_count"
    with pytest.raises(CallGuardError):
        reserve(g)


def test_runtime_stop_first_reason_freezes_and_keeps_prior_ledger(tmp_path):
    g = guard(tmp_path)
    attempt = reserve(g)
    original = g.ledger.read_bytes()
    g.stop("clock_error")
    stopped = g.ledger.read_bytes()
    g.stop("auth_error")
    g.finish(attempt, "error", 0)
    assert g.ledger.read_bytes().startswith(stopped)
    assert stopped.startswith(original)
    assert g.snapshot()["stop_reason"] == "clock_error"
    with pytest.raises(CallGuardError):
        reserve(g)


def test_duplicate_finish_and_single_inflight(tmp_path):
    g = guard(tmp_path)
    attempt = reserve(g)
    with pytest.raises(CallGuardError):
        reserve(g, "B")
    g.finish(attempt, "ok", 1)
    before = g.ledger.read_bytes()
    with pytest.raises(CallGuardError):
        g.finish(attempt, "ok", 1)
    assert g.ledger.read_bytes().startswith(before)
    assert g.snapshot()["stop_reason"] == "invalid_finish"
    with pytest.raises(CallGuardError):
        reserve(g)


def test_racing_reserves_cannot_overshoot(tmp_path):
    g = guard(tmp_path, 1)

    def try_reserve(_):
        try:
            return reserve(g)
        except CallGuardError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        attempts = list(pool.map(try_reserve, range(16)))
    assert sum(a is not None for a in attempts) == 1
    g.finish(next(a for a in attempts if a), "ok", 1)
    with pytest.raises(CallGuardError):
        reserve(g)
    assert g.snapshot()["actual_http_calls"] == 1


def test_racing_exclusive_initialization(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        instances = list(pool.map(lambda _: guard(tmp_path), range(16)))
    assert all(g.snapshot()["reserved_attempts"] == 0 for g in instances)
    assert len(instances[0].ledger.read_bytes().splitlines()) == 1


def test_crash_between_ledger_and_anchor_is_not_repaired(tmp_path, monkeypatch):
    g = guard(tmp_path)
    original_open = Path.open

    def broken_open(path, *args, **kwargs):
        if path.name == "00000001.sha256":
            raise OSError("simulated crash")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", broken_open)
    with pytest.raises(OSError):
        reserve(g)
    monkeypatch.setattr(Path, "open", original_open)
    raw = g.ledger.read_bytes()
    with pytest.raises(CallGuardError):
        guard(tmp_path)
    assert g.ledger.read_bytes() == raw


@pytest.mark.parametrize("count", [True, -1, 2, 1.0, "unknown"])
def test_finish_rejects_invalid_counts(tmp_path, count):
    g = guard(tmp_path)
    attempt = reserve(g)
    with pytest.raises(ValueError):
        g.finish(attempt, "ok", count)
    assert g.snapshot()["unknown_http_attempts"] == 1


def test_no_auth_or_payload_fields(tmp_path):
    g = guard(tmp_path)
    with pytest.raises(ValueError):
        g.stop("Bearer secret token / raw transport error")
    with pytest.raises(ValueError):
        g.reserve("A", "DRAFT", "raw model prompt")
    assert g.snapshot()["reserved_attempts"] == 0
