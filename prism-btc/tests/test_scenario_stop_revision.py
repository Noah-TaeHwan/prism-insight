"""Bounded stop restoration uses only isolated SQLite and fake exchange."""
import json

import pytest

from live.scenario_broker import BrokerNotReady
from tests.test_scenario_execution import live, persist, probe_permit


def prepared(live):
    broker, exchange = live
    opening = persist(broker)
    broker.execute(opening, "a1")
    exchange.fill(next(c["link_id"] for c in broker.children() if c["kind"] == "entry"), .1)
    broker.reconcile()
    state = broker._recovery_state()
    state["active"]["hard_stop"] = 99
    state["active"]["stop_policy"] = dict(version=1, initial_hard_stop=98, initial_action_id="a1")
    state["version"] = 1
    broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
    broker.conn.execute("UPDATE llm_scenario_intents SET status='TERMINAL'")
    broker.conn.execute("CREATE TABLE llm_scenario_decisions(context TEXT,proposal TEXT)")
    broker.conn.commit()
    broker._ensure_protection(broker.capture_account(), 99, "LONG")
    payload = persist(broker, action="ADJUST", action_id="restore", input_id="input-1", hard_stop=98.5, entries=[],
        stop_revision=dict(reason="Support remains intact", structure_reference="30m MA35", input_id="input-1"))
    broker.conn.execute("INSERT INTO llm_scenario_decisions VALUES(?,?)", (
        json.dumps(dict(input_id="input-1", input_captured_at=broker.clock(),
            account_version=broker.capture_account()["account_version"])), json.dumps(payload)))
    broker.conn.commit()
    exchange.writes.clear()
    return broker, exchange, payload


def test_restore_exact_confirmation_persisted_and_no_duplicate(live):
    broker, exchange, payload = prepared(live)
    broker.execute(payload, "restore")
    assert broker._active()["hard_stop"] == 98.5
    assert broker._recovery_state()["version"] == 2
    assert broker._stop_revision_evidence("stop_revision_confirmed", "restore")
    attempt = broker._stop_revision_evidence("stop_revision_attempt", "restore")
    assert attempt["risk_after"]["budget"] == attempt["risk_before"]["budget"]
    assert attempt["risk_after"]["total_risk"] > attempt["risk_before"]["total_risk"]
    assert attempt["consumed_costs"]["fees_paid"] > 0
    assert attempt["stop_revision"] == payload["stop_revision"]
    broker.execute(payload, "restore")
    assert len([w for w in exchange.writes if w[0] == "stop"]) == 1


@pytest.mark.parametrize("change", ["anchor", "binding", "pending", "halt", "daily", "risk", "legacy", "unknown"])
def test_restore_fails_closed_without_weakening(live, change):
    broker, exchange, payload = prepared(live)
    if change in {"anchor", "risk"}:
        state = broker._recovery_state()
        if change == "anchor":
            state["active"].pop("stop_policy")
        else:
            state["active"]["initial_equity"] = 1
        broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
        broker.conn.commit()
    elif change == "binding":
        broker.conn.execute("UPDATE llm_scenario_decisions SET context='{}'")
        broker.conn.commit()
    elif change == "pending":
        persist(broker, action_id="unresolved")
    elif change == "halt":
        broker._new_risk_enabled = lambda: False
    elif change == "daily":
        broker._daily = lambda observed: dict(new_risk_allowed=False)
    elif change == "unknown":
        broker.conn.execute("UPDATE llm_scenario_children SET status='UNKNOWN' WHERE kind='entry'")
        broker.conn.commit()
        broker._query_child = lambda child: None
    else:
        capture = broker.capture_account
        broker.capture_account = lambda: dict(capture(), legacy_fenced=True)
    with pytest.raises((BrokerNotReady, ValueError)):
        broker.execute(payload, "restore")
    assert exchange.stop == "99"
    assert not broker._stop_revision_evidence("stop_revision_confirmed", "restore")


def test_timeout_after_write_restores_old_stop_and_never_retries(live):
    broker, exchange, payload = prepared(live)
    original = exchange.set_trading_stop
    def ambiguous(**params):
        original(**params)
        raise TimeoutError()
    exchange.set_trading_stop = ambiguous
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, "restore")
    assert exchange.stop == "98.5"
    assert broker._active()["hard_stop"] == 99
    exchange.set_trading_stop = original
    broker._ensure_protection(broker.capture_account(), broker._active()["hard_stop"], "LONG")
    assert exchange.stop == "99"
    with pytest.raises(BrokerNotReady, match="already_attempted"):
        broker.execute(payload, "restore")
    assert len([w for w in exchange.writes if w[0] == "stop" and w[1]["stopLoss"] == "98.5"]) == 1


def test_unchanged_ack_is_not_confirmation(live):
    broker, exchange, payload = prepared(live)
    exchange.set_trading_stop = lambda **params: dict(retCode=0, result={})
    with pytest.raises(BrokerNotReady, match="readback_unconfirmed"):
        broker.execute(payload, "restore")
    assert broker._active()["hard_stop"] == 99


def test_recovery_consumed_permit_allows_small_budget_restore(live):
    broker, exchange, payload = prepared(live)
    probe_permit(broker, dict(action_id="a1", entries=[]))
    broker.execute(payload, "restore")
    assert exchange.stop == "98.5"


def test_short_restore_symmetric_and_initial_anchor_enforced(live):
    broker, exchange, payload = prepared(live)
    state = broker._recovery_state()
    state["active"].update(side="SHORT", hard_stop=101,
        stop_policy=dict(version=1, initial_hard_stop=102, initial_action_id="a1"))
    broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
    first = json.loads(broker.conn.execute("SELECT payload FROM llm_scenario_intents WHERE id='a1'").fetchone()[0])
    first.update(side="SHORT", hard_stop=102)
    broker.conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='a1'", (json.dumps(first),))
    payload.update(side="SHORT", hard_stop=101.5)
    broker.conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='restore'", (json.dumps(payload),))
    broker.conn.execute("UPDATE llm_scenario_decisions SET proposal=?", (json.dumps(payload),))
    broker.conn.commit()
    exchange.stop = "101"
    exchange.orders["native"].update(side="Buy", triggerPrice="101")
    for child in broker.children():
        if child["kind"] == "native_sl":
            child["request"].update(side="Buy")
            broker.conn.execute("UPDATE llm_scenario_children SET request=? WHERE link_id=?", (
                json.dumps(child["request"]), child["link_id"]))
    broker.conn.commit()
    capture = broker.capture_account
    def short_capture():
        observed = capture()
        observed["position"].update(side="Sell", stopLoss=exchange.stop)
        observed["legacy_fenced"] = False
        return observed
    broker.capture_account = short_capture
    broker.conn.execute("UPDATE llm_scenario_decisions SET context=?", (json.dumps(dict(
        input_id="input-1", input_captured_at=broker.clock(),
        account_version=broker.capture_account()["account_version"])),))
    broker.conn.commit()
    broker._risk_accounting = lambda *args: dict(positions=[dict(price=100, quantity=.1)],
        realized_loss=0, fees_paid=.01, funding_paid=0)
    broker.execute(payload, "restore")
    assert exchange.stop == "101.5"
    assert broker._active()["hard_stop"] == 101.5


def test_partial_stop_and_tp_do_not_replace_full_protection(live):
    broker, exchange, payload = prepared(live)
    payload.update(partial_stops=[dict(id="near", price=99.5, fraction=.4)],
        take_profits=[dict(id="profit", price=102, fraction=.6)])
    broker.conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='restore'", (json.dumps(payload),))
    broker.conn.execute("UPDATE llm_scenario_decisions SET proposal=?", (json.dumps(payload),))
    broker.conn.commit()
    broker.execute(payload, "restore")
    assert broker._verify_protection(broker.capture_account(), 98.5, "LONG")
    partial = next(c for c in broker.children() if c["kind"] == "partial_sl")
    assert float(partial["request"]["qty"]) == .04
    assert partial["request"]["triggerBy"] == "MarkPrice"
    assert partial["request"]["reduceOnly"] is True
    exchange.fill(partial["link_id"], .02)
    broker._sync_exits(payload, "restore")
    assert broker._verify_protection(broker.capture_account(), 98.5, "LONG")
    assert len([c for c in broker.children() if c["kind"] == "partial_sl"]) == 1


def test_size_change_during_restore_cannot_confirm(live):
    broker, exchange, payload = prepared(live)
    original = exchange.set_trading_stop
    def racing(**params):
        result = original(**params)
        exchange.size = ".08"
        exchange.orders["native"].update(qty=".08", leavesQty=".08")
        return result
    exchange.set_trading_stop = racing
    with pytest.raises(BrokerNotReady, match="readback_unconfirmed"):
        broker.execute(payload, "restore")
    assert broker._active()["hard_stop"] == 99
    assert not broker._stop_revision_evidence("stop_revision_confirmed", "restore")


def test_restore_beyond_original_stop_is_rejected(live):
    broker, exchange, payload = prepared(live)
    payload["hard_stop"] = 97.9
    broker.conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id='restore'", (json.dumps(payload),))
    broker.conn.commit()
    with pytest.raises(ValueError, match="initial"):
        broker.execute(payload, "restore")
    assert exchange.stop == "99"


@pytest.mark.parametrize("change", ["account", "age", "stronger_stop"])
def test_stale_decision_or_stronger_manual_stop_blocks_restore(live, change):
    broker, exchange, payload = prepared(live)
    context = json.loads(broker.conn.execute("SELECT context FROM llm_scenario_decisions").fetchone()[0])
    if change == "account":
        context["account_version"] = "different"
    elif change == "age":
        context["input_captured_at"] -= 121
    else:
        exchange.stop = "99.5"
        exchange.orders["native"]["triggerPrice"] = "99.5"
    broker.conn.execute("UPDATE llm_scenario_decisions SET context=?", (json.dumps(context),))
    broker.conn.commit()
    with pytest.raises(BrokerNotReady, match="stale_input_or_protection"):
        broker.execute(payload, "restore")
    assert not broker._stop_revision_evidence("stop_revision_attempt", "restore")


def test_timeout_reconcile_restores_and_runtime_resumes_wait_then_exit(live):
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_runtime import wait_proposal
    broker, exchange, payload = prepared(live)
    original = exchange.set_trading_stop
    def ambiguous(**params):
        original(**params)
        raise TimeoutError()
    exchange.set_trading_stop = ambiguous
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, "restore")
    exchange.set_trading_stop = original
    result = broker.reconcile()
    assert exchange.stop == "99"
    restored = next(row for row in result["intents"] if row["intent_id"] == "restore")
    assert restored["stop_revision_status"] == "ABORTED_RESTORED"
    assert broker._stop_revision_evidence("stop_revision_aborted_restored", "restore")
    with pytest.raises(BrokerNotReady, match="aborted_restored"):
        broker.execute(payload, "restore")
    # Upgrade the minimal fixture decision schema to the actual runtime schema.
    broker.conn.execute("ALTER TABLE llm_scenario_decisions RENAME TO old_test_decisions")
    state = broker._recovery_state()
    state["active"]["revision"] = 2
    state["breaker"] = {}
    broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
    for ident, body in broker.conn.execute("SELECT id,payload FROM llm_scenario_intents").fetchall():
        plan = json.loads(body)
        plan["revision"] = 1 if ident == "a1" else 2
        broker.conn.execute("UPDATE llm_scenario_intents SET payload=? WHERE id=?", (json.dumps(plan), ident))
    broker.conn.commit()
    now = [broker.clock()]
    runtime = ScenarioRuntime(broker.conn, broker,
        lambda snapshot, ctx: wait_proposal(snapshot, ctx, action_id="after-restore-wait"),
        lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0])
    result = runtime.tick()
    assert result["status"] == "wait", result
    assert broker.conn.execute("SELECT status FROM llm_scenario_intents WHERE id='restore'").fetchone()[0] == "TERMINAL"
    now[0] += 300
    def exit_proposal(snapshot, ctx):
        return dict(wait_proposal(snapshot, ctx, action_id="after-restore-exit"), action="EXIT")
    runtime.propose = exit_proposal
    result = runtime.tick()
    assert result["reason"] == "awaiting_exact_evidence", result
    assert any(kind == "place" and request.get("reduceOnly") is True and request["orderType"] == "Market"
               for kind, request in exchange.writes)


@pytest.mark.parametrize("uncertainty", ["accounting", "child"])
def test_restored_stop_alone_does_not_release_uncertain_intent(live, uncertainty):
    broker, exchange, payload = prepared(live)
    original = exchange.set_trading_stop
    def ambiguous(**params):
        original(**params)
        raise TimeoutError()
    exchange.set_trading_stop = ambiguous
    with pytest.raises(BrokerNotReady):
        broker.execute(payload, "restore")
    exchange.set_trading_stop = original
    def unavailable(*args):
        raise BrokerNotReady("test_uncertain")
    if uncertainty == "accounting":
        broker._accounting = unavailable
    else:
        broker._query_child = unavailable
    result = broker.reconcile()
    assert exchange.stop == "99"
    assert not any(row["intent_id"] == "restore" and row.get("terminal") for row in result["intents"])
    assert not broker._stop_revision_evidence("stop_revision_aborted_restored", "restore")
