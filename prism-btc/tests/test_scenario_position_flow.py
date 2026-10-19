import copy
import json
import sqlite3

import pytest

from live.scenario_entry_contract import bind_contract
from live.scenario_position_flow import MAX_BYTES, SCAN_LIMIT, build_position_flow
from live.scenario_position_flow import project_position_flow_input


@pytest.fixture
def history():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE llm_scenario_intents(id TEXT,scenario_id TEXT,payload TEXT,status TEXT);
        CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,context TEXT,proposal TEXT,outcome TEXT);
    """)
    plan = dict(scenario_id="s", action_id="open", input_id="input", action="OPEN",
                side="LONG", entries=[dict(id="entry", price=81000.12345, quantity=.0137)],
                hard_stop=80000.98765, take_profits=[dict(price=81500.12345, fraction=.6)],
                rationale="Initial recovery premise", revision=0)
    definition = dict(version=1, regime="TREND", premise="Rising lows support recovery",
        expected_progress="Hold the breakout and advance", explicit_failure=dict(
            source="MARK_PRICE", operator="le", price=80200, minimum_observations=2))
    contract = bind_contract(definition, plan, 300)
    conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,?)",
                 ("open", "s", json.dumps(plan), "LIVE_RECONCILED"))
    active = dict(scenario_id="s", entry_contract=contract)
    ctx = dict(scenario_id="s", registered_entry_contract=contract, account_captured_at=9000,
        positions=[dict(quantity=.0137, price=81000.12345)], previous_hard_stop=80000.98765,
        target_status=[], accounting_status="confirmed", review_memory=[dict(id="checkpoint")],
        entry_thesis=dict(status="VERIFIED", first_fill_at=350, initial_fill_average=81000.12345,
                          initial_filled_quantity=.0137, observed_at=9000))
    yield conn, active, ctx, plan
    conn.close()


def decision(history, slot, status="INTACT", action="WAIT", outcome="wait", scenario="s", reason="Observed progress"):
    conn, active, context, original = history
    ctx = copy.deepcopy(context)
    ctx.update(scenario_id=scenario, account_captured_at=slot * 300 + 1)
    proposal = dict(scenario_id=scenario, action=action, action_id=f"a{slot}", rationale=reason,
        thesis_assessment=dict(contract_id=active["entry_contract"]["contract_id"], status=status, reason=reason))
    if outcome == "intent_pending":
        plan = dict(original, **{k: v for k, v in proposal.items() if k != "thesis_assessment"})
        plan["hard_stop"] = 80500.987654321
        conn.execute("INSERT INTO llm_scenario_intents VALUES(?,?,?,?)",
                     (proposal["action_id"], scenario, json.dumps(plan), "PENDING"))
    conn.execute("INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)",
                 (slot, json.dumps(ctx), json.dumps(proposal), json.dumps(dict(status=outcome))))


def test_original_anchor_and_accepted_transitions_are_retained_without_writes(history):
    conn, active, ctx, plan = history
    decision(history, 2)
    decision(history, 3)
    decision(history, 4, "WEAKENING", reason="Breakout stopped advancing")
    decision(history, 5, "INTACT", reason="Price reclaimed prior breakout")
    before = conn.total_changes
    flow = build_position_flow(conn, active, ctx, 9000)
    assert conn.total_changes == before
    assert flow["original_contract"] == ctx["registered_entry_contract"]
    assert flow["original_requested_plan"]["plan"]["rationale"] == plan["rationale"]
    assert [x["status"] for x in flow["thesis_transitions"]] == ["INTACT", "WEAKENING", "INTACT"]
    assert flow["initial_fill_evidence"]["initial_filled_quantity"] == .0137
    assert flow["current_context_references"]["advisory_checkpoints_not_exit_rules"] == "review_memory"


def test_other_scenario_future_and_rejected_are_excluded(history):
    conn, active, ctx, _ = history
    decision(history, 2)
    decision(history, 3, "FAILED", scenario="other")
    decision(history, 4, "FAILED", outcome="blocked")
    decision(history, 30, "FAILED")  # current slot is not a completed past decision
    decision(history, 31, "FAILED")
    flow = build_position_flow(conn, active, ctx, 9000)
    assert [x["status"] for x in flow["thesis_transitions"]] == ["INTACT"]
    assert flow["coverage"]["same_scenario_rows"] == 2
    assert flow["coverage"]["rejected_rows"] == 1


def test_pending_adjust_is_requested_not_fill_and_numeric_values_are_exact(history):
    conn, active, ctx, _ = history
    decision(history, 2)
    decision(history, 3, "WEAKENING", action="ADJUST", outcome="intent_pending")
    flow = build_position_flow(conn, active, ctx, 9000)
    change = flow["requested_changes"][0]
    assert change["source"] == "REQUESTED_PLAN_NOT_FILL"
    assert change["intent_status_at_read"] == "PENDING"
    assert change["plan"]["hard_stop"] == 80500.987654321
    assert change["plan"]["entries"][0]["quantity"] == .0137
    assert "filled_quantity" not in change
    assert len(flow["requested_changes"]) == 1


def test_missing_intent_rejects_pending_assessment(history):
    conn, active, ctx, _ = history
    decision(history, 2, "FAILED", action="EXIT", outcome="intent_pending")
    conn.execute("DELETE FROM llm_scenario_intents WHERE id='a2'")
    flow = build_position_flow(conn, active, ctx, 9000)
    assert flow["thesis_transitions"] == []
    assert flow["coverage"]["invalid_rows"] == 1


def test_multilingual_maximum_reasons_preserved_or_wholly_omitted(history):
    conn, active, ctx, _ = history
    reason = "상승근거를재평가합니다🙂" * 20
    reason = reason[:240]
    for slot in range(2, 20):
        decision(history, slot, "INTACT" if slot % 2 else "WEAKENING", reason=reason)
    flow = build_position_flow(conn, active, ctx, 9000)
    assert len(json.dumps(flow, ensure_ascii=False, separators=(",", ":")).encode()) <= MAX_BYTES
    assert flow["thesis_transitions"]
    assert all(row["reason"] == reason for row in flow["thesis_transitions"])
    assert flow["coverage"]["omitted_items"] > 0
    assert flow["status"] == "PARTIAL"


def test_long_history_discloses_scan_limit_and_preserves_original_anchor(history):
    conn, active, ctx, plan = history
    for slot in range(2, SCAN_LIMIT + 10):
        decision(history, slot, "INTACT" if slot % 2 else "WEAKENING")
    now = (SCAN_LIMIT + 20) * 300
    flow = build_position_flow(conn, active, ctx, now)
    assert flow["coverage"]["scanned_rows"] == SCAN_LIMIT
    assert flow["coverage"]["older_history"] == "OMITTED_SCAN_LIMIT"
    assert flow["coverage"]["source_first_slot"] > 2
    assert flow["original_requested_plan"]["plan"]["action_id"] == "open"
    assert flow["thesis_transitions"][-1]["slot"] == SCAN_LIMIT + 9


def test_missing_tables_and_malformed_history_are_optional(history):
    conn, active, ctx, _ = history
    conn.execute("INSERT INTO llm_scenario_decisions VALUES(2,'{','{','{')")
    flow = build_position_flow(conn, active, ctx, 9000)
    assert flow["status"] == "PARTIAL"
    assert flow["coverage"]["invalid_rows"] == 1
    conn.execute("DROP TABLE llm_scenario_decisions")
    assert build_position_flow(conn, active, ctx, 9000)["status"] == "PARTIAL"
    assert build_position_flow(conn, None, ctx, 9000)["status"] == "NOT_ACTIVE"


def test_future_observations_and_invalid_assessment_are_not_facts(history):
    conn, active, ctx, _ = history
    decision(history, 2, reason="x" * 241)
    decision(history, 3)
    raw = json.loads(conn.execute("SELECT context FROM llm_scenario_decisions WHERE slot=3").fetchone()[0])
    raw["account_captured_at"] = 99999
    conn.execute("UPDATE llm_scenario_decisions SET context=? WHERE slot=3", (json.dumps(raw),))
    ctx["entry_thesis"]["observed_at"] = 99999
    flow = build_position_flow(conn, active, ctx, 9000)
    assert flow["initial_fill_status"] == "UNAVAILABLE"
    assert flow["historical_observation_changes"] == []
    assert flow["coverage"]["invalid_rows"] == 2


def test_oversized_reason_is_omitted_not_truncated(history):
    conn, active, ctx, _ = history
    decision(history, 2, action="ADJUST", outcome="intent_pending", reason="가" * 5000)
    flow = build_position_flow(conn, active, ctx, 9000)
    assert flow["requested_changes"] == []
    assert len(json.dumps(flow, ensure_ascii=False, separators=(",", ":")).encode()) <= MAX_BYTES
    assert flow["coverage"]["omitted_items"] + flow["coverage"]["invalid_rows"] > 0


def test_mismatched_current_context_is_not_attached_to_scenario(history):
    conn, active, ctx, _ = history
    ctx["scenario_id"] = "other"
    flow = build_position_flow(conn, active, ctx, 9000)
    assert flow == dict(version=1, status="UNAVAILABLE", reason="context_scenario_mismatch")


@pytest.mark.parametrize("field", ["now", "input_captured_at", "account_captured_at"])
def test_future_time_cannot_hide_behind_old_slot(history, field):
    conn, active, ctx, _ = history
    decision(history, 2, action="ADJUST", outcome="intent_pending")
    stored = json.loads(conn.execute("SELECT context FROM llm_scenario_decisions WHERE slot=2").fetchone()[0])
    stored[field] = 9001
    conn.execute("UPDATE llm_scenario_decisions SET context=? WHERE slot=2", (json.dumps(stored),))
    flow = build_position_flow(conn, active, ctx, 9000)
    assert flow["thesis_transitions"] == flow["requested_changes"] == []
    assert flow["coverage"]["invalid_rows"] == 1


def test_price_ticks_do_not_crowd_position_observation_changes(history):
    conn, active, ctx, _ = history
    for slot in range(2, 6):
        decision(history, slot)
        stored = json.loads(conn.execute("SELECT context FROM llm_scenario_decisions WHERE slot=?", (slot,)).fetchone()[0])
        stored["mark_price"] = 81000 + slot * 100
        conn.execute("UPDATE llm_scenario_decisions SET context=? WHERE slot=?", (json.dumps(stored), slot))
    flow = build_position_flow(conn, active, ctx, 9000)
    assert len(flow["historical_observation_changes"]) == 1
    assert flow["historical_observation_changes"][0]["mark_price"] == 81200


def test_blank_assessment_reason_is_invalid(history):
    conn, active, ctx, _ = history
    decision(history, 2, reason=" ")
    flow = build_position_flow(conn, active, ctx, 9000)
    assert flow["thesis_transitions"] == []
    assert flow["coverage"]["invalid_rows"] == 1


def test_old_sqlite_scalar_serialization_cannot_round_history(history):
    conn, active, ctx, _ = history
    price = 81000.12345678912
    quantity = .01371234567891234
    net = 1.2345678901234567
    stamp = 1792344016.2444782
    decision(history, 2)
    stored = json.loads(conn.execute("SELECT context FROM llm_scenario_decisions WHERE slot=2").fetchone()[0])
    stored.update(now=stamp, input_captured_at=stamp, account_captured_at=stamp,
                  mark_price=price, previous_hard_stop=price, recorded_scenario_net=net,
                  positions=[dict(price=price, quantity=quantity)],
                  target_status=[dict(price=price, filled_quantity=quantity)])
    conn.execute("UPDATE llm_scenario_decisions SET context=? WHERE slot=2", (json.dumps(stored),))
    statements = []
    conn.set_trace_callback(statements.append)
    # Emulate the lossy JSON scalar writer in old SQLite. If the SQL starts
    # assembling objects again, these doubles round even on a modern machine.
    def old_json_object(*args):
        return json.dumps({args[i]: float(format(args[i + 1], ".15g"))
            if isinstance(args[i + 1], float) else args[i + 1]
            for i in range(0, len(args), 2)})
    conn.create_function("json_object", -1, old_json_object)
    assert json.loads(old_json_object("price", price))["price"] != price
    flow = build_position_flow(conn, active, ctx, stamp + 10)
    observation = flow["historical_observation_changes"][0]
    assert observation["captured_at"] == stamp
    assert observation["mark_price"] == observation["previous_hard_stop"] == price
    assert observation["recorded_scenario_net"] == net
    assert observation["positions"] == stored["positions"]
    assert observation["target_status"] == stored["target_status"]
    assert not any("json_object(" in sql.lower() for sql in statements)


def test_small_model_input_preserves_exact_existing_projection(history):
    from live.scenario_model_input import project_model_input
    conn, active, ctx, _ = history
    decision(history, 2)
    payload = dict(market_snapshot=dict(price=81000.12345678912), contract_context=dict(
        ctx, position_flow=build_position_flow(conn, active, ctx, 9000)))
    source = copy.deepcopy(payload)
    assert project_position_flow_input(payload) == project_model_input(payload)
    assert payload == source


def test_budget_discards_only_whole_optional_history_and_preserves_source(monkeypatch):
    import live.scenario_position_flow as module
    from live.scenario_model_input import project_model_input, restore_model_input
    flow = dict(version=1, status="AVAILABLE", coverage={}, original_requested_plan=dict(
        price=81000.12345678912), historical_observation_changes=[dict(reason="가" * 240, n=1),
        dict(reason="Complete reason", n=2)], requested_changes=[dict(price=80500.987654321)],
        thesis_transitions=[dict(status="WEAKENING", reason="Reclaim failed")])
    payload = dict(market_snapshot=dict(price=81000.12345678912), calculated_candidates=[dict(quantity=.01371234567891234)],
        contract_context=dict(position_flow=flow, mandatory="x" * 400))
    source = copy.deepcopy(payload)
    initial, _ = project_model_input(payload)
    monkeypatch.setattr(module, "_MODEL_INPUT_LIMIT", len(json.dumps(initial, ensure_ascii=False, separators=(",", ":")).encode()) - 200)
    projected, audit = project_position_flow_input(payload)
    restored = restore_model_input(projected)
    retained = restored["contract_context"]["position_flow"]
    assert retained["historical_observation_changes"] == flow["historical_observation_changes"][1:]
    assert retained["requested_changes"] == flow["requested_changes"]
    assert retained["thesis_transitions"] == flow["thesis_transitions"]
    assert retained["original_requested_plan"] == flow["original_requested_plan"]
    assert retained["coverage"]["model_budget_omitted_items"] == 1
    assert retained["status"] == "PARTIAL"
    assert restored["market_snapshot"] == payload["market_snapshot"]
    assert restored["calculated_candidates"] == payload["calculated_candidates"]
    assert restored["contract_context"]["mandatory"] == payload["contract_context"]["mandatory"]
    assert audit["projected_utf8_bytes"] <= module._MODEL_INPUT_LIMIT
    assert payload == source


def test_budget_stub_does_not_hide_oversized_mandatory_input(monkeypatch):
    import live.scenario_position_flow as module
    from live.scenario_model_input import restore_model_input
    monkeypatch.setattr(module, "_MODEL_INPUT_LIMIT", 500)
    payload = dict(market_snapshot=dict(mandatory="x" * 1000), contract_context=dict(
        position_flow=dict(version=1, original_requested_plan=dict(reason="가" * 240),
                           thesis_transitions=[dict(reason="Meaning remains complete")])) )
    source = copy.deepcopy(payload)
    projected, audit = project_position_flow_input(payload)
    restored = restore_model_input(projected)
    assert restored["contract_context"]["position_flow"]["status"] == "OMITTED_INPUT_BUDGET"
    assert restored["market_snapshot"] == payload["market_snapshot"]
    assert audit["projected_utf8_bytes"] > 500
    assert payload == source


def test_oversized_input_without_optional_flow_remains_rejected(monkeypatch):
    import live.scenario_position_flow as module
    from live.scenario_model_input import project_model_input
    monkeypatch.setattr(module, "_MODEL_INPUT_LIMIT", 100)
    payload = dict(market_snapshot=dict(mandatory="x" * 1000), contract_context={})
    assert project_position_flow_input(payload) == project_model_input(payload)


@pytest.mark.parametrize("bad_value", [True, False, "81000.12345678912", {"price": 81000}])
def test_sql_projection_rejects_non_numeric_scalars(history, bad_value):
    conn, active, ctx, _ = history
    decision(history, 2)
    stored = json.loads(conn.execute("SELECT context FROM llm_scenario_decisions WHERE slot=2").fetchone()[0])
    stored["mark_price"] = bad_value
    conn.execute("UPDATE llm_scenario_decisions SET context=? WHERE slot=2", (json.dumps(stored),))
    flow = build_position_flow(conn, active, ctx, 9000)
    assert flow["historical_observation_changes"] == []
    assert flow["coverage"]["invalid_rows"] == 1
