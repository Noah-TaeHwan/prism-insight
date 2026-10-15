"""The execution reprice bound used by allocation sizing survives restarts."""
import json

import pytest

from live.scenario_broker import BrokerNotReady, ScenarioDemoBroker
from live.scenario_risk_allocation import allocate_entry_risk
from tests.test_scenario_execution import live as execution_fixture, persist


@pytest.mark.parametrize("side,stop,first,last,outside,partial_fill", [
    ("LONG", 98, 100.1, 100.2, 100.3, False),
    ("SHORT", 102, 99.9, 99.8, 99.7, False),
    ("LONG", 98, 100.1, 100.2, 100.3, True),
])
def test_execution_reprices_remain_inside_original_reserved_anchor(tmp_path, side, stop, first, last, outside, partial_fill):
    broker, exchange = execution_fixture.__wrapped__(tmp_path)
    state = json.loads(broker.conn.execute("SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])
    state["active"].update(side=side, hard_stop=stop)
    broker.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (json.dumps(state),))
    broker.conn.commit()
    context = dict(initial_equity=10000, positions=[], pending_entries=[], realized_loss=0,
        fees_paid=0, funding_paid=0, estimated_cost_rate=.002, slippage_bps=20,
        price_tick=.1, quantity_step=.001, minimum_quantity=.001, minimum_notional=5)
    raw = dict(action="OPEN", side=side, hard_stop=stop, confidence=.75,
        entries=[dict(id="e1", price=100, allocation_fraction=.1)],
        chase=dict(max_bps=20, max_reprices=3), take_profits=[])
    allocated, receipt = allocate_entry_risk(raw, context)
    payload = persist(broker, **allocated)
    broker.execute(payload, "a1")
    if partial_fill:
        exchange.fill(next(iter(exchange.orders)), .001)
        broker.reconcile()
    broker.chase("a1", "e1", first)
    restarted = ScenarioDemoBroker(broker.conn, session=exchange, expected_main_uid="123",
        clock=lambda: 1800000000, execution_enabled=True)
    restarted.chase("a1", "e1", last)
    before = len(exchange.writes)
    with pytest.raises(BrokerNotReady, match="chase_price_bound"):
        restarted.chase("a1", "e1", outside)
    assert len(exchange.writes) == before
    entries = [child for child in restarted.children() if child["kind"] == "entry"]
    assert len(entries) == 3
    from live.scenario_risk_allocation import _unit_risk
    quantity = allocated["entries"][0]["quantity"]
    for child in entries:
        expected = quantity - .001 if partial_fill and ":chase:" in child["local_id"] else quantity
        assert float(child["request"]["qty"]) == pytest.approx(expected)
        # Full original quantity stays the bound even after the partial fill.
        risk = quantity * float(_unit_risk(allocated, context, float(child["request"]["price"])))
        assert risk <= receipt["entries"][0]["allocation_budget"]
