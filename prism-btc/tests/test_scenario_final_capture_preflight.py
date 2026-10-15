"""An intent is not a promise of submission: executor rechecks exchange facts."""
import pytest

from live.scenario_broker import BrokerNotReady
from tests.test_scenario_execution import live, persist, reply  # noqa: F401 — pytest fixture


@pytest.mark.parametrize("change", ["stop_crossed", "external_fill"])
def test_fact_change_after_intent_before_executor_never_submits(live, change):  # noqa: F811 — imported fixture
    broker, exchange = live
    payload = persist(broker)  # Runtime's coherent observation/intent already ended.
    assert broker.conn.execute("SELECT COUNT(*) FROM llm_scenario_intents").fetchone()[0] == 1
    if change == "external_fill":
        exchange.size = "0.1"
        reason = "legacy_exposure_or_unknown_order"
    else:
        exchange.get_tickers = lambda **kw: reply([dict(symbol="BTCUSDT", markPrice="97", lastPrice="97")])
        reason = "stop_crossed_market"
    with pytest.raises(BrokerNotReady, match=reason):
        broker.execute(payload, payload["action_id"])
    assert exchange.writes == []
