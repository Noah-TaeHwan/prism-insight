import copy
import json
import sqlite3

import pytest

from live.scenario_entry_contract import bind_contract, contract_context, observe_contract, valid_record, validate_metadata
from live.scenario_runtime import ScenarioRuntime
from tests.test_scenario_runtime import Broker, proposal, terminal, wait_proposal


def definition(side="LONG"):
    return dict(version=1, regime="BREAKOUT", premise="breakout", expected_progress="continuation",
        explicit_failure=dict(source="MARK_PRICE",
        operator="le" if side == "LONG" else "ge", price=95 if side == "LONG" else 105, minimum_observations=2))


def active(side="LONG"):
    plan = dict(action="OPEN", scenario_id="s", action_id="a", input_id="i", side=side,
                entries=[dict(price=100)], hard_stop=90 if side == "LONG" else 110)
    return dict(scenario_id="s", side=side, entry_contract=bind_contract(definition(side), plan, 900))


def context(a, at=1200, mark=94):
    return dict(scenario_id=a["scenario_id"], account_captured_at=at, mark_price=mark, account_version=str(at),
        entry_thesis=dict(status="VERIFIED", side=a["side"], first_fill_at=950, observed_at=at, current_mark=mark))


@pytest.mark.parametrize("side,mark", [("LONG", 94), ("SHORT", 106)])
def test_distinct_adjacent_samples_latch_and_restart(side, mark):
    a = active(side)
    observe_contract(a, context(a, mark=mark), 4, 1200)
    assert a["entry_contract_observation"]["count"] == 1
    observe_contract(a, context(a, mark=mark), 4, 1200)
    assert a["entry_contract_observation"]["count"] == 1
    a = copy.deepcopy(a)
    observe_contract(a, context(a, 1500, mark), 5, 1500)
    assert a["entry_contract_observation"]["status"] == "FAILED"
    observe_contract(a, context(a, 1800, 100), 6, 1800)
    assert a["entry_contract_observation"]["status"] == "FAILED"


@pytest.mark.parametrize("alter", ["future", "stale", "no_fill", "wrong_side", "mark_mismatch", "wrong_scenario"])
def test_untrusted_sample_does_not_count(alter):
    a = active()
    c = context(a)
    if alter == "future":
        c["account_captured_at"] = 1201
    if alter == "stale":
        c["account_captured_at"] = 1000
    if alter == "no_fill":
        c["entry_thesis"]["status"] = "NO_CONFIRMED_INITIAL_FILL"
    if alter == "wrong_side":
        c["entry_thesis"]["side"] = "SHORT"
    if alter == "mark_mismatch":
        c["entry_thesis"]["current_mark"] = 90
    if alter == "wrong_scenario":
        c["scenario_id"] = "other"
    observe_contract(a, c, 4, 1200)
    assert a["entry_contract_observation"]["status"] == "UNKNOWN"


def test_gap_and_price_reclaim_reset():
    a = active()
    for at, mark, expected in [(1200, 94, 1), (1800, 94, 1), (2100, 100, 0), (2400, 94, 1)]:
        observe_contract(a, context(a, at, mark), at//300, at)
        assert a["entry_contract_observation"]["count"] == expected


def test_same_slot_recovery_resets_confirmation():
    a = active()
    observe_contract(a, context(a), 4, 1200)
    observe_contract(a, context(a, 1220, 100), 4, 1220)
    observe_contract(a, context(a, 1500, 94), 5, 1500)
    assert a["entry_contract_observation"]["count"] == 1


@pytest.mark.parametrize("status,count", [("FAILED", 2), ("CONFIRMING", 99)])
def test_forged_latch_or_count_without_sample_evidence_is_not_authority(status, count):
    a = active()
    a["entry_contract_observation"] = dict(contract_id=a["entry_contract"]["contract_id"],
        slot=3, captured_at=900, status=status, count=count)
    observe_contract(a, context(a), 4, 1200)
    assert a["entry_contract_observation"]["status"] == "CONFIRMING"
    assert a["entry_contract_observation"]["count"] == 1


@pytest.mark.parametrize("alter", ["future_samples", "changed_fill", "missing_fill", "inconsistent_fill"])
def test_latched_samples_require_past_time_and_original_fill(alter):
    a = active()
    observe_contract(a, context(a), 4, 1200)
    observe_contract(a, context(a, 1500, 94), 5, 1500)
    c = context(a, 1800, 94)
    if alter == "future_samples":
        for sample in a["entry_contract_observation"]["samples"]:
            sample["slot"] += 10
            sample["captured_at"] += 3000
        a["entry_contract_observation"].update(slot=15, captured_at=4500)
    if alter == "changed_fill":
        c["entry_thesis"]["first_fill_at"] = 999
    if alter == "missing_fill":
        c["entry_thesis"] = None
    if alter == "inconsistent_fill":
        a["entry_contract_observation"]["samples"][1]["first_fill_at"] = 999
    observe_contract(a, c, 6, 1800)
    assert (a["entry_contract_observation"]["status"] == "FAILED") == (alter == "missing_fill")


@pytest.mark.parametrize("alter", ["price", "minimum_observations", "remove", "missing_origin", "side"])
def test_original_registry_detects_rehashed_contract_swap(tmp_path, alter):
    b = Broker()
    r = ScenarioRuntime(sqlite3.connect(tmp_path / "r.db"), b,
        lambda s, c: dict(proposal(s, c), entry_contract=definition(), thesis_assessment=None),
        lambda: dict(valid=True, as_of_ms=1800000000000), clock=lambda: 1800000000., entry_contract_enabled=True)
    assert r.tick()["status"] == "intent_pending"
    state = r.state()
    original = copy.deepcopy(state["active"]["entry_contract"])
    if alter == "remove":
        del state["active"]["entry_contract"]
    elif alter == "missing_origin":
        r.conn.execute("DELETE FROM llm_scenario_intents WHERE id=?", (original["origin"]["action_id"],))
        r.conn.commit()
    elif alter == "side":
        state["active"]["side"] = "SHORT"
    else:
        altered = copy.deepcopy(original["definition"])
        altered["explicit_failure"][alter] = 96 if alter == "price" else 3
        state["active"]["entry_contract"] = bind_contract(altered,
            dict(action="OPEN", side="LONG", entries=[dict(price=100)], hard_stop=90,
                 **{k: original["origin"][k] for k in ("scenario_id", "action_id", "input_id")}),
            original["origin"]["registered_at"])
    r.entry_contract_enabled = False
    ctx = r._context(state)
    assert ctx["entry_contract_status"] == "CORRUPT"
    assert r.conn.execute("SELECT body FROM llm_scenario_entry_contracts").fetchone()[0]
    assert len(b.executed) == 1


def test_record_tamper_and_metadata_rules():
    a = active()
    ctx = contract_context(a, True)
    ctx["scenario_id"] = "s"
    p = dict(action="WAIT", entries=[], entry_contract=None, thesis_assessment=dict(
        contract_id=a["entry_contract"]["contract_id"], status="FAILED", reason="failed"))
    with pytest.raises(ValueError, match="requires_exit"):
        validate_metadata(p, ctx)
    validate_metadata(p, ctx, allow_failed_wait_template=True)
    validate_metadata(dict(p, action="EXIT"), ctx)
    a["entry_contract"]["definition"]["premise"] = "replacement"
    assert not valid_record(a["entry_contract"], "s")
    assert contract_context(a, True)["entry_contract_status"] == "CORRUPT"


@pytest.mark.parametrize("side,operator,price", [("LONG", "le", 95), ("SHORT", "ge", 105)])
@pytest.mark.parametrize("minimum", [2, 3])
def test_host_failure_rule_exact_authoritative_predicate(side, operator, price, minimum):
    d = definition(side)
    d["explicit_failure"]["minimum_observations"] = minimum
    plan = dict(action="OPEN", scenario_id="s", action_id="a", input_id="i", side=side,
                entries=[dict(price=100)], hard_stop=90 if side == "LONG" else 110)
    a = dict(scenario_id="s", side=side, entry_contract=bind_contract(d, plan, 900))
    assert contract_context(a, True)["entry_contract_failure_rule"] == dict(
        source="MARK_PRICE", operator=operator, price=price, minimum_observations=minimum,
        confirmation_scope="DISTINCT_CONSECUTIVE_FRESH_DECISION_SLOTS")


@pytest.mark.parametrize("a", [None, {"scenario_id": "s"}, {"scenario_id": "s", "entry_contract": {}}])
def test_unknown_contract_does_not_render_failure_rule(a):
    assert contract_context(a, True)["entry_contract_failure_rule"] is None


def test_old_narrative_failure_basis_is_rejected():
    d = dict(definition(), failure_basis="another price")
    plan = dict(action="OPEN", scenario_id="s", action_id="a", input_id="i", side="LONG",
                entries=[dict(price=100)], hard_stop=90)
    with pytest.raises(ValueError, match="invalid_registration"):
        bind_contract(d, plan, 900)
    with pytest.raises(ValueError, match="definition_invalid"):
        validate_metadata(dict(plan, entry_contract=d, thesis_assessment=None),
                          dict(entry_contract_version=1, scenario_id=None))


@pytest.mark.parametrize("exit_error", [None, "cancel_pending", "timeout"])
@pytest.mark.parametrize("claimed_failure_slot", [False, True])
def test_runtime_host_exit_actual_adapter_and_restart_no_second_post(tmp_path, exit_error, claimed_failure_slot):
    path = tmp_path / "runtime.db"
    b, now, calls, posts = Broker(), [1800000000.], [], []
    def model(s, c):
        calls.append(c)
        if not c["scenario_id"]:
            return dict(proposal(s, c), entry_contract=definition(), thesis_assessment=None)
        return dict(wait_proposal(s, c, action_id="wait"), entry_contract=None,
            thesis_assessment=dict(contract_id=c["registered_entry_contract"]["contract_id"], status="INTACT", reason="holding"))
    def execute(payload, ident):
        posts.append(copy.deepcopy(payload))
        if payload["action"] == "EXIT":
            if exit_error == "cancel_pending":
                from live.scenario_broker import BrokerCancellationPending
                raise BrokerCancellationPending("not confirmed")
            if exit_error == "timeout":
                raise TimeoutError("uncertain")
    b.execute = execute
    r = ScenarioRuntime(sqlite3.connect(path), b, model, lambda: dict(valid=True, as_of_ms=now[0]*1000),
                        clock=lambda: now[0], entry_contract_enabled=True)
    assert r.tick()["status"] == "intent_pending"
    registered = r.state()["active"]["entry_contract"]
    assert registered["origin"]["action_id"] == "action-1"
    b.evidence = dict(intents=[terminal()])
    b.ctx["positions"] = [dict(price=100, quantity=.5)]
    b.ctx["pending_entries"] = [dict(id="pending", price=100, quantity=.5)]
    for index in (1, 2):
        now[0] += 300
        if index == 2 and claimed_failure_slot:
            r.conn.execute("INSERT INTO llm_scenario_slots VALUES(?, 'CLAIMED')", (int(now[0]//300),))
            r.conn.commit()
        b.ctx.update(account_captured_at=now[0], mark_price=94, entry_thesis=dict(status="VERIFIED",
            side="LONG", first_fill_at=1800000001., observed_at=now[0], current_mark=94))
        result = r.tick()
        assert result["status"] == ("wait" if index == 1 else "intent_pending")
    if exit_error == "timeout":
        from live import tracking
        from live.scenario_runner import observe_pending_market, record_health
        assert result['reason'] == 'submission_unknown'
        row = r.conn.execute('SELECT outcome FROM llm_scenario_decisions WHERE slot=?',
                             (int(now[0]//300),)).fetchone()
        assert row is None or row[0] is None
        tracking.ensure_schema(r.conn)
        result = observe_pending_market(result, lambda: dict(valid=True, as_of_ms=now[0]*1000),
                                        clock=lambda: now[0])
        record_health(r.conn, result, loop='decision', clock=lambda: now[0])
        assert r.conn.execute("SELECT count(*) FROM btc_events WHERE mode='demo' AND level='error' "
                              "AND message='scenario tick: submission_unknown'").fetchone()[0] == 1
    assert len(calls) == 2  # OPEN + first confirming sample; failure bypasses model.
    assert posts[-1]["action"] == "EXIT" and posts[-1]["cancel_entry_ids"] == ["pending"]
    assert r.state()["active"]["entry_contract"] == registered
    r.conn.close()
    now[0] += 300
    restarted = ScenarioRuntime(sqlite3.connect(path), b, model, lambda: {}, clock=lambda: now[0])
    assert restarted.tick()["status"] == "intent_pending"
    assert len(posts) == 2


@pytest.mark.parametrize("missing", ["entries", "side", "hard_stop"])
def test_bind_requires_original_open_economic_fields(missing):
    plan = dict(action="OPEN", scenario_id="s", action_id="a", input_id="i", side="LONG",
                entries=[dict(price=100)], hard_stop=90)
    del plan[missing]
    with pytest.raises(ValueError):
        bind_contract(definition(), plan, 900)


def test_open_contract_and_intent_atomic_failure(tmp_path):
    b = Broker()
    r = ScenarioRuntime(sqlite3.connect(tmp_path / "r.db"), b,
        lambda s, c: dict(proposal(s, c), entry_contract=definition(), thesis_assessment=None),
        lambda: dict(valid=True, as_of_ms=1800000000000), clock=lambda: 1800000000., entry_contract_enabled=True)
    r.conn.execute("CREATE TRIGGER reject_intent BEFORE INSERT ON llm_scenario_intents BEGIN SELECT RAISE(ABORT, 'test'); END")
    r.conn.commit()
    assert r.tick()["status"] == "blocked"
    assert r.state()["active"] is None
    assert not r.conn.execute("SELECT 1 FROM llm_scenario_entry_contracts").fetchone()
    assert not b.executed


def test_qualitative_failure_cannot_resume_wait_after_terminal_exit_rejection(tmp_path):
    b, now, calls = Broker(), [1800000000.], []
    def model(s, c):
        calls.append(c)
        if not c["scenario_id"]:
            return dict(proposal(s, c), entry_contract=definition(), thesis_assessment=None)
        return dict(wait_proposal(s, c, action_id="failed-exit"), action="EXIT", entry_contract=None,
            thesis_assessment=dict(contract_id=c["registered_entry_contract"]["contract_id"], status="FAILED", reason="failure"))
    r = ScenarioRuntime(sqlite3.connect(tmp_path / "r.db"), b, model,
        lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0], entry_contract_enabled=True)
    assert r.tick()["status"] == "intent_pending"
    b.evidence = dict(intents=[terminal()])
    b.ctx["positions"] = [dict(price=100, quantity=1)]
    now[0] += 300
    assert r.tick()["status"] == "intent_pending"
    assert len(calls) == 2
    # Exact rejection/terminal evidence does not mean the held position vanished.
    r.conn.execute("UPDATE llm_scenario_intents SET status='TERMINAL' WHERE id='failed-exit'")
    r.conn.commit()
    state = r.state()
    state["active"].pop("entry_contract_model_failure")  # Independent receipt survives mutable-state loss.
    r._save(state)
    now[0] += 300
    result = r.tick()
    assert result["status"] == "blocked"
    assert result["reason"] == "entry_contract_failed_exit_unresolved"
    assert len(calls) == 2 and len(b.executed) == 2


@pytest.mark.parametrize("source", ["numeric", "qualitative"])
@pytest.mark.parametrize("damage", ["registry", "origin", "cache", "failure_row_and_cache"])
@pytest.mark.parametrize("terminal_exit", [False, True])
def test_durable_failure_survives_cache_or_source_damage(tmp_path, source, damage, terminal_exit):
    b, now, calls = Broker(), [1800000000.], []
    def model(s, c):
        calls.append(c)
        if not c["scenario_id"]:
            return dict(proposal(s, c), entry_contract=definition(), thesis_assessment=None)
        return dict(wait_proposal(s, c, action_id="failed-exit" if source == "qualitative" else "holding"),
            action="EXIT" if source == "qualitative" else "WAIT", entry_contract=None,
            thesis_assessment=dict(contract_id=c["registered_entry_contract"]["contract_id"],
                status="FAILED" if source == "qualitative" else "INTACT", reason="assessment"))
    r = ScenarioRuntime(sqlite3.connect(tmp_path / "r.db"), b, model,
        lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0], entry_contract_enabled=True)
    r.tick()
    b.evidence = dict(intents=[terminal()])
    b.ctx["positions"] = [dict(price=100, quantity=1)]
    for _ in range(2 if source == "numeric" else 1):
        now[0] += 300
        b.ctx.update(account_captured_at=now[0], mark_price=94, entry_thesis=dict(status="VERIFIED",
            side="LONG", first_fill_at=1800000001., observed_at=now[0], current_mark=94))
        r.tick()
    assert len(b.executed) == 2
    state = r.state()
    if terminal_exit:
        r.conn.execute("UPDATE llm_scenario_intents SET status='TERMINAL'")
    if damage == "registry":
        r.conn.execute("DELETE FROM llm_scenario_entry_contracts")
    elif damage == "origin":
        r.conn.execute("DELETE FROM llm_scenario_intents WHERE id='action-1'")
    else:
        for key in ("entry_contract_observation", "entry_contract_model_failure", "entry_contract_failure"):
            state["active"].pop(key, None)
        if damage == "failure_row_and_cache":
            r.conn.execute("DELETE FROM llm_scenario_entry_failures")
    r._save(state)
    old_calls = len(calls)
    now[0] += 300
    outcome = r.tick()
    assert outcome["status"] in {"blocked", "intent_pending"}
    assert len(calls) == old_calls and len(b.executed) == 2
    if terminal_exit:
        assert outcome["status"] == "blocked"
        assert outcome["reason"] == ("entry_contract_failed_exit_unresolved" if damage == "cache"
                                       else "entry_contract_failed_state_untrusted")


def test_failure_registered_during_model_call_overrides_late_wait(tmp_path):
    b, now = Broker(), [1800000000.]
    def model(s, c):
        if not c["scenario_id"]:
            return dict(proposal(s, c), entry_contract=definition(), thesis_assessment=None)
        record = c["registered_entry_contract"]
        assessment = dict(contract_id=record["contract_id"], status="FAILED", reason="prior failure")
        failure = dict(source="MODEL_ASSESSMENT", contract=record, contract_id=record["contract_id"],
                       intent_id="concurrent-exit", assessment=assessment)
        exit_plan = dict(wait_proposal(s, c, action_id="concurrent-exit"), action="EXIT")
        r.conn.execute("INSERT INTO llm_scenario_entry_failures VALUES(?,?)",
                       (c["scenario_id"], json.dumps(failure)))
        r.conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,'TERMINAL',NULL)",
                       ("concurrent-exit", c["scenario_id"], json.dumps(exit_plan)))
        r.conn.commit()
        return dict(wait_proposal(s, c, action_id="late-wait"), entry_contract=None,
                    thesis_assessment=dict(assessment, status="INTACT"))
    r = ScenarioRuntime(sqlite3.connect(tmp_path / "r.db"), b, model,
        lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0], entry_contract_enabled=True)
    r.tick()
    b.evidence = dict(intents=[terminal()])
    b.ctx["positions"] = [dict(price=100, quantity=1)]
    now[0] += 300
    outcome = r.tick()
    assert outcome["reason"] == "entry_contract_failed_exit_unresolved"
    assert outcome["status"] == "blocked" and len(b.executed) == 1


def test_calculated_final_wait_broker_latency_preserves_confirmation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from live import scenario_review_guard
    from live.scenario_review_adapter import make_calculated_review_proposer
    from tests.test_scenario_review_runtime import Broker as ReviewBroker, wire

    now, captured, calls, guard_counts = [1800000000.], {}, [], []
    broker = ReviewBroker(now[0])
    original_context = broker.context
    def delayed_context():
        now[0] += .75  # REST returns after the runtime's pre-capture commit_at.
        broker.ctx["account_captured_at"] = now[0]
        if broker.ctx["positions"]:
            broker.ctx["entry_thesis"] = dict(status="VERIFIED", side="LONG", first_fill_at=first_fill,
                observed_at=now[0], current_mark=broker.ctx["mark_price"])
        return original_context()
    broker.context = delayed_context
    runtime = ScenarioRuntime(sqlite3.connect(tmp_path / "latency.db"), broker,
        lambda s, c: dict(proposal(s, c), entry_contract=definition(), thesis_assessment=None),
        lambda: dict(valid=True, as_of_ms=now[0]*1000), clock=lambda: now[0], entry_contract_enabled=True)
    assert runtime.tick()["status"] == "intent_pending"
    first_fill = now[0]+1
    broker.ctx.update(positions=[dict(price=100, quantity=1)], mark_price=94)
    broker.reconcile = lambda: dict(intents=[terminal()])
    def generate(**request):
        calls.append(request)
        wait = dict(wire(captured, "WAIT"), entry_contract=None, thesis_assessment=dict(
            contract_id=captured["registered_entry_contract"]["contract_id"], status="INTACT", reason="observe"))
        return SimpleNamespace(text=json.dumps(dict(wait_proposal=wait, candidates=[], no_candidate_reason="observe")))
    adapter = make_calculated_review_proposer(generate=generate, clock=lambda: now[0])
    def propose_review(snapshot, ctx, *, record):
        captured.update(copy.deepcopy(ctx))
        return adapter(snapshot, ctx, record=record)
    original_guard = scenario_review_guard.guard_review
    def guard(*args, **kwargs):
        fresh = args[4]
        guard_counts.append(fresh["entry_contract_observation"]["count"])
        return original_guard(*args, **kwargs)
    monkeypatch.setattr(scenario_review_guard, "guard_review", guard)
    runtime.calculated_review_enabled = True
    runtime.calculated_review_propose = propose_review
    runtime.conn.executescript("""
        CREATE TABLE llm_scenario_review_events(slot INTEGER, sequence INTEGER, body TEXT, PRIMARY KEY(slot,sequence));
        CREATE TABLE llm_scenario_review_tapes(slot INTEGER PRIMARY KEY, body TEXT);
    """)
    now[0] = 1800000300.
    outcome = runtime.tick()
    assert outcome["status"] == "wait", outcome
    assert guard_counts == [1, 1]
    assert runtime.state()["active"]["entry_contract_observation"]["count"] == 1
    now[0] = 1800000600.
    assert runtime.tick()["status"] == "intent_pending"
    assert len(calls) == 1
    assert broker.executed[-1]["action"] == "EXIT"
