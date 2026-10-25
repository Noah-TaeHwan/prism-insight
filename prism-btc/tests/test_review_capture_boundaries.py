"""Persisted raw proposal and executable renewal are separate audit boundaries."""
import json

import pytest

from live import scenario_provenance as audit
from tests import test_scenario_explanation_pipeline as explanation
from tests import test_scenario_pending_renewal_runtime as renewal


@pytest.fixture(autouse=True)
def capture(monkeypatch):
    monkeypatch.setattr('live.scenario_ledger.forward_audits', lambda events: None)
    for module in (explanation, renewal):
        original = module.wire
        def with_review(ctx, action, original=original):
            return dict(original(ctx, action), review={'conditions': [], 'acknowledgements': []})
        monkeypatch.setattr(module, 'wire', with_review)


def transitions(runtime):
    return [json.loads(r[0]) for r in runtime.conn.execute(
        "SELECT body FROM llm_scenario_audit_events WHERE kind='review_transition' ORDER BY rowid")]


@pytest.mark.parametrize('action', ['WAIT', 'ADJUST', 'EXIT', 'REDUCE'])
def test_compiled_rationale_keeps_raw_proposal_binding(tmp_path, action):
    runtime, _, outcome, *_ = explanation.run_pipeline(tmp_path, action=action, capture_audit=True)
    assert outcome['status'] == ('wait' if action == 'WAIT' else 'intent_pending'), outcome
    rows = transitions(runtime)
    assert len(rows) == 1
    body = rows[0]
    assert audit._review_commit_matches(runtime.conn, body)
    raw = json.loads(runtime.conn.execute('SELECT proposal FROM llm_scenario_decisions').fetchone()[0])
    assert raw['rationale'].startswith('모델 해석(미검증):')
    assert '공식' not in raw['rationale']
    assert body['original_proposal_hash'] == audit.digest(raw)
    assert body['original_proposal_hash'] != body['validated_payload_hash']
    assert not audit._review_commit_matches(runtime.conn, dict(body, original_proposal_hash='foreign'))


@pytest.mark.parametrize('side', ['LONG', 'SHORT'])
def test_wait_renewal_requires_exact_intent(tmp_path, side):
    runtime, broker, exchange, *_ = renewal.renewal_runtime(tmp_path, side=side)
    with audit.run_capture(runtime.conn):
        outcome = runtime.tick()
    assert outcome['status'] == 'intent_pending', outcome
    writes = list(exchange.writes)
    exchange.now += 301
    with audit.run_capture(runtime.conn):
        assert runtime.tick()['status'] == 'intent_pending'
    assert exchange.writes == writes
    body = transitions(runtime)[-1]
    assert body['action'] == 'WAIT'
    assert body['requires_intent'] is True
    assert audit._review_commit_matches(runtime.conn, body)
    runtime.conn.execute('DELETE FROM llm_scenario_intents WHERE id=?', (body['action_id'],))
    assert not audit._review_commit_matches(runtime.conn, body)
