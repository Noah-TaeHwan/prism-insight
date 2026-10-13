"""No-network evidence boundaries; synthetic cases are not profitability tests."""
import copy
import json
import sqlite3

import pytest

from live.scenario_entry_thesis import entry_thesis


def fixture(side="LONG"):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE llm_scenario_intents(id TEXT,scenario_id TEXT,payload TEXT)")
    plan = dict(action="OPEN", action_id="a", scenario_id="s", side=side, hard_stop=90,
                entries=[dict(id="e", price=100, quantity=1)], rationale="never forwarded")
    conn.execute("INSERT INTO llm_scenario_intents VALUES('a','s',?)", (json.dumps(plan),))
    active = dict(scenario_id="s", side=side, hard_stop=98)
    child = dict(kind="entry", scenario_id="s", intent_id="a", local_id="e",
                 request=dict(side="Buy" if side == "LONG" else "Sell", price="100.1",
                              triggerPrice="100", triggerBy="MarkPrice", triggerDirection=1 if side == "LONG" else 2),
                 evidence=dict(executions=[dict(execId="x", execTime="1000000", execQty=".2", execPrice="100.2")]))
    observed = dict(captured_at=1300, mark_price=101)
    return conn, active, [child], observed


@pytest.mark.parametrize("side,progress", [("LONG", .8), ("SHORT", -.8)])
def test_symmetric_endpoint_facts_not_failure_verdict(side, progress):
    args = fixture(side)
    before = copy.deepcopy(args[1:])
    changes = args[0].total_changes
    result = entry_thesis(*args)
    assert result["status"] == "VERIFIED"
    assert result["favorable_price_change_from_initial_average"] == pytest.approx(progress)
    assert result["initial_hard_stop"] == 90  # Not revised current SL.
    assert result["initial_order_anchors"][0]["limit_price"] == 100.1  # Submitted, not proposed.
    assert result["elapsed_since_first_fill_seconds"] == 300
    assert "rationale" not in json.dumps(result)
    assert len(json.dumps(result).encode()) < 2048
    assert args[0].total_changes == changes
    assert args[1:] == before
    assert not {"exit", "failed", "mfe", "mae"} & result.keys()


def test_partial_fills_and_additions_never_reset_original_anchor():
    args = fixture()
    args[2][0]["evidence"]["executions"].append(dict(execId="y", execTime="1100000", execQty=".1", execPrice="100.5"))
    added = copy.deepcopy(args[2][0])
    added.update(intent_id="b", local_id="add")
    added["evidence"]["executions"] = [dict(execId="z", execTime="1200000", execQty=".3", execPrice="101")]
    args[2].append(added)
    plan = dict(action="ADJUST", action_id="b", scenario_id="s", side="LONG", entries=[dict(id="add")])
    args[0].execute("INSERT INTO llm_scenario_intents VALUES('b','s',?)", (json.dumps(plan),))
    result = entry_thesis(*args)
    assert result["first_fill_at"] == 1000
    assert result["latest_entry_fill_at"] == 1200
    assert result["additional_entry_fills"] == 1
    assert result["initial_filled_quantity"] == pytest.approx(.3)
    assert result["initial_fill_average"] == pytest.approx(100.3)


@pytest.mark.parametrize("field,value", [("execTime", None), ("execTime", "nan"), ("execTime", "1400000"),
                                       ("execQty", -1), ("execPrice", True), ("side", "Sell"), ("execId", "")])
def test_invalid_execution_is_unknown(field, value):
    args = fixture()
    args[2][0]["evidence"]["executions"][0][field] = value
    assert entry_thesis(*args)["status"] == "MISSING"


@pytest.mark.parametrize("field,value", [("scenario_id", "other"), ("local_id", "wrong")])
def test_child_identity_mismatch(field, value):
    args = fixture()
    args[2][0][field] = value
    assert entry_thesis(*args)["status"] == "MISSING"


def test_pending_is_not_filled_or_zero_age():
    args = fixture()
    args[2][0]["evidence"]["executions"] = []
    result = entry_thesis(*args)
    assert result["status"] == "NO_CONFIRMED_INITIAL_FILL"
    assert "elapsed_since_first_fill_seconds" not in result


@pytest.mark.parametrize("mutation", ["missing_plan", "wrong_side", "duplicate_fill", "no_evidence", "invalid_mark"])
def test_unavailable_provenance_does_not_fabricate(mutation):
    args = fixture()
    if mutation == "missing_plan":
        args[0].execute("DELETE FROM llm_scenario_intents")
    elif mutation == "wrong_side":
        args[1]["side"] = "SHORT"
    elif mutation == "duplicate_fill":
        args[2][0]["evidence"]["executions"] *= 2
    elif mutation == "no_evidence":
        args[2][0]["evidence"] = None
    else:
        args[3]["mark_price"] = float("inf")
    assert entry_thesis(*args)["status"] == "MISSING"


def test_no_active_no_database_required():
    assert entry_thesis(None, None, [], {}) == {"status": "NOT_ACTIVE"}


def test_addition_without_exact_intent_remains_unknown():
    args = fixture()
    child = copy.deepcopy(args[2][0])
    child.update(intent_id="unlinked", local_id="add")
    child["evidence"]["executions"][0]["execId"] = "add-fill"
    args[2].append(child)
    assert entry_thesis(*args)["status"] == "MISSING"


@pytest.mark.parametrize("mutation", ["cumulative", "request_quantity", "order_id", "before_creation", "nondict"])
def test_reconciliation_and_chronology_guards(mutation):
    args = fixture()
    child = args[2][0]
    if mutation == "cumulative":
        child["evidence"]["order"] = dict(cumExecQty=".3")
    elif mutation == "request_quantity":
        child["request"]["qty"] = ".1"
    elif mutation == "order_id":
        child["order_id"] = "expected"
        child["evidence"]["order"] = dict(orderId="wrong")
    elif mutation == "before_creation":
        args[1]["created_at"] = 1001
    else:
        args[2].append(None)
    assert entry_thesis(*args)["status"] == "MISSING"


def test_unfilled_staged_order_is_not_an_executed_anchor():
    args = fixture()
    plan = json.loads(args[0].execute("SELECT payload FROM llm_scenario_intents").fetchone()[0])
    plan["entries"].append(dict(id="unfilled"))
    args[0].execute("UPDATE llm_scenario_intents SET payload=?", (json.dumps(plan),))
    staged = copy.deepcopy(args[2][0])
    staged["local_id"] = "unfilled"
    staged["evidence"]["executions"] = []
    args[2].append(staged)
    result = entry_thesis(*args)
    assert result["status"] == "VERIFIED"
    assert result["initial_order_anchors"] == [dict(entry_id="e", limit_price=100.1,
        activation_trigger=100.0, confirmed_filled_quantity=.2)]


def test_verified_evidence_survives_actual_model_projection_without_mutation():
    from types import SimpleNamespace
    from tests.test_scenario_llm import context, wire
    from live.scenario_llm import propose
    from live.scenario_preview import response_contract
    from live.scenario_model_input import restore_model_input
    ctx = context(True)
    ctx["entry_thesis"] = entry_thesis(*fixture())
    before = copy.deepcopy(ctx)
    calls = []
    def generate(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=json.dumps(wire(ctx)))
    propose(dict(valid=True, as_of_ms=1000000), ctx, response_contract(ctx),
            generate=generate, clock=lambda: 1000)
    restored = restore_model_input(json.loads(calls[0]["user_prompt"]))["contract_context"]
    assert restored["entry_thesis"] == ctx["entry_thesis"]
    for key in ("positions", "pending_entries", "initial_equity", "previous_hard_stop", "estimated_cost_rate"):
        assert restored[key] == ctx[key]
    assert ctx == before
    assert len(calls) == 1


def test_broker_context_integration(live):  # noqa: F811
    # Real context path, no entry fills yet; existing broker fixture blocks network.
    from tests.test_scenario_execution import persist
    broker, _ = live
    persist(broker)
    result = broker.context()
    assert result["entry_thesis"]["status"] == "NO_CONFIRMED_INITIAL_FILL"


from tests.test_scenario_execution import live  # noqa: E402,F401
