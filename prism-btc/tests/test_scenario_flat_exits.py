"""Flat cleanup must use owned, terminal-confirmed exchange evidence."""
import json

import pytest

from tests.test_scenario_execution import live as execution_fixture, persist


@pytest.fixture
def live(tmp_path):
    return execution_fixture.__wrapped__(tmp_path)


def closed_with_orphan(live):
    broker, exchange = live
    plan = persist(broker, chase=dict(max_bps=0, max_reprices=0),
        take_profits=[dict(id="tp", price=105, fraction=1)],
        partial_stops=[dict(id="sl", price=99, fraction=.5)])
    broker.execute(plan, "a1")
    entry = next(c for c in broker.children() if c["kind"] == "entry")
    exchange.fill(entry["link_id"], .1)
    broker.reconcile()
    tp = next(c for c in broker.children() if c["kind"] == "tp")
    exchange.fill(tp["link_id"], .1)
    return broker, exchange, next(c for c in broker.children() if c["kind"] == "partial_sl")


def test_flat_tp_cleans_owned_partial_stop_and_settles_once(live):
    broker, exchange, orphan = closed_with_orphan(live)
    result = broker.reconcile()
    assert exchange.orders[orphan["link_id"]]["orderStatus"] == "Cancelled"
    assert result["settlement"] is not None
    writes = list(exchange.writes)
    broker.reconcile()
    assert exchange.writes == writes


@pytest.mark.parametrize("case", ["disabled", "foreign_order", "new_position", "unknown"])
def test_flat_cleanup_fences_uncertain_or_unmanaged_exposure(live, case):
    broker, exchange, orphan = closed_with_orphan(live)
    if case == "disabled":
        broker.execution_enabled = False
    elif case == "foreign_order":
        exchange.orders["foreign"] = dict(exchange.orders[orphan["link_id"]], orderId="foreign", orderLinkId="foreign")
    elif case == "new_position":
        exchange.size = ".02"
    else:
        exchange.get_order_history = lambda **kw: dict(retCode=1)
    writes = list(exchange.writes)
    result = broker.reconcile()
    assert not [w for w in exchange.writes[len(writes):] if w[0] == "cancel"]
    assert result["settlement"] is None


def test_flat_cleanup_waits_for_terminal_not_cancel_ack(live):
    broker, exchange, orphan = closed_with_orphan(live)
    exchange.cancel_order = lambda **kw: dict(retCode=0, result={})
    assert broker.reconcile()["settlement"] is None
    assert exchange.orders[orphan["link_id"]]["orderStatus"] == "New"


@pytest.mark.parametrize("kind", ["tp", "partial_sl"])
def test_short_flat_cleanup_is_symmetric(live, kind):
    broker, exchange, orphan = closed_with_orphan(live)
    state = json.loads(broker.conn.execute("SELECT body FROM llm_scenario_state").fetchone()[0])
    state["active"].update(side="SHORT", hard_stop=102)
    broker.conn.execute("UPDATE llm_scenario_state SET body=?", (json.dumps(state),))
    for child in broker.children():
        request = dict(child["request"], side="Sell" if child["request"]["side"] == "Buy" else "Buy")
        if child["kind"] == "entry":
            request["stopLoss"] = "102"
        broker.conn.execute("UPDATE llm_scenario_children SET request=? WHERE link_id=?", (json.dumps(request), child["link_id"]))
    for order in exchange.orders.values():
        order["side"] = "Sell" if order["side"] == "Buy" else "Buy"
        if order.get("reduceOnly") is False:
            order["stopLoss"] = "102"
    for fill in exchange.fills:
        fill["side"] = "Sell" if fill["side"] == "Buy" else "Buy"
    broker.conn.execute("UPDATE llm_scenario_children SET kind=? WHERE link_id=?", (kind, orphan["link_id"]))
    broker.conn.commit()
    assert broker.reconcile()["settlement"] is not None
    assert exchange.orders[orphan["link_id"]]["orderStatus"] == "Cancelled"


def test_flat_cleanup_rechecks_position_after_exact_order_query(live):
    broker, exchange, orphan = closed_with_orphan(live)
    original = broker._query_child
    reads = []
    def racing_query(child):
        result = original(child)
        if child["link_id"] == orphan["link_id"]:
            reads.append(child)
            if len(reads) == 2:
                exchange.size = ".02"
        return result
    broker._query_child = racing_query
    writes = list(exchange.writes)
    assert broker.reconcile()["settlement"] is None
    assert not [w for w in exchange.writes[len(writes):] if w[0] == "cancel"]


def test_flat_cleanup_keeps_exits_while_entry_remains_live(live):
    broker, exchange, orphan = closed_with_orphan(live)
    entry = next(c for c in broker.children() if c["kind"] == "entry")
    request = dict(entry["request"], qty=".2")
    broker.conn.execute("UPDATE llm_scenario_children SET request=? WHERE link_id=?", (json.dumps(request), entry["link_id"]))
    broker.conn.commit()
    exchange.orders[entry["link_id"]].update(qty=".2", leavesQty=".1", orderStatus="PartiallyFilled")
    writes = list(exchange.writes)
    assert broker.reconcile()["settlement"] is None
    assert not [w for w in exchange.writes[len(writes):] if w[0] == "cancel"]


def test_flat_cleanup_does_not_cancel_native_stop(live):
    broker, exchange, orphan = closed_with_orphan(live)
    exchange.orders["native"]["orderStatus"] = "Untriggered"
    writes = list(exchange.writes)
    assert broker.reconcile()["settlement"] is None
    assert not [w for w in exchange.writes[len(writes):] if w[0] == "cancel"]


def test_flat_cleanup_rejects_changed_reduce_only_terms(live):
    broker, exchange, orphan = closed_with_orphan(live)
    exchange.orders[orphan["link_id"]]["reduceOnly"] = False
    writes = list(exchange.writes)
    assert broker.reconcile()["settlement"] is None
    assert exchange.writes == writes


def test_flat_cleanup_ack_loss_with_exact_terminal_proof_settles(live):
    broker, exchange, orphan = closed_with_orphan(live)
    cancel = exchange.cancel_order
    def lost_ack(**kw):
        cancel(**kw)
        raise TimeoutError()
    exchange.cancel_order = lost_ack
    assert broker.reconcile()["settlement"] is not None
    assert exchange.orders[orphan["link_id"]]["orderStatus"] == "Cancelled"


def test_delayed_cancel_terminal_on_next_tick_releases_settlement(live):
    broker, exchange, orphan = closed_with_orphan(live)
    exchange.cancel_order = lambda **kw: dict(retCode=0, result={})
    assert broker.reconcile()["settlement"] is None
    exchange.orders[orphan["link_id"]]["orderStatus"] = "Cancelled"
    assert broker.reconcile()["settlement"] is not None


def test_unfilled_flat_entry_is_not_cancelled_as_orphan(live):
    broker, exchange = live
    broker.execute(persist(broker, chase=dict(max_bps=0, max_reprices=0)), "a1")
    writes = list(exchange.writes)
    assert broker.reconcile()["settlement"] is None
    assert exchange.writes == writes


def add_known_order(broker, exchange, template, *, scenario_id, kind, link):
    order = dict(exchange.orders[template["link_id"]], orderId=link, orderLinkId=link,
                 reduceOnly=kind != "entry")
    request = dict(template["request"], reduceOnly=kind != "entry")
    evidence = dict(order=order, executions=[])
    broker.conn.execute("INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,?,?,?,?)",
        (link, template["intent_id"], scenario_id, kind, link, json.dumps(request),
         "LIVE", link, json.dumps(evidence), broker.clock()))
    broker.conn.commit()
    exchange.orders[link] = order


@pytest.mark.parametrize("kind", ["entry", "tp", "partial_sl"])
def test_flat_cleanup_blocks_known_other_scenario_order(live, kind):
    broker, exchange, orphan = closed_with_orphan(live)
    # This ID is known globally, so capture_account's legacy fence is not enough.
    add_known_order(broker, exchange, orphan, scenario_id="other", kind=kind, link="other-order")
    writes = list(exchange.writes)
    assert broker.reconcile()["settlement"] is None
    assert exchange.writes == writes
    assert exchange.orders[orphan["link_id"]]["orderStatus"] == "New"


def test_flat_cleanup_accepts_multiple_current_scenario_orphans(live):
    broker, exchange, orphan = closed_with_orphan(live)
    add_known_order(broker, exchange, orphan, scenario_id="s1", kind="tp", link="another-exit")
    assert broker.reconcile()["settlement"] is not None
    assert exchange.orders[orphan["link_id"]]["orderStatus"] == "Cancelled"
    assert exchange.orders["another-exit"]["orderStatus"] == "Cancelled"
