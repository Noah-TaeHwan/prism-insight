"""Only a verified flat WAIT may reuse its post-model account observation."""
from copy import deepcopy

import pytest

from tests.test_scenario_review_runtime import runtime


def setup_runtime(tmp_path, *, proof=True, empty=True, change=None):
    r, broker, calls = runtime(tmp_path, empty=empty)
    broker.ctx['unknown_intents'] = False
    captures = []

    def reconcile():
        captures.append('reconcile')
        if not proof:
            return {}
        return dict(protection_confirmed=True, protection_status='verified_flat_no_exposure',
                    observation=dict(account_version=broker.ctx['account_version']))

    def context():
        captures.append('context')
        broker.ctx['account_captured_at'] = r.test_clock[0]
        if change:
            change(broker.ctx)
        return deepcopy(broker.ctx)

    broker.reconcile = reconcile
    broker.context = context
    return r, broker, calls, captures


def test_verified_flat_wait_reuses_capture_without_intent(tmp_path):
    r, broker, calls, captures = setup_runtime(tmp_path)
    assert r.tick()['status'] == 'wait'
    assert captures == ['reconcile', 'context'] * 2
    assert broker.executed == []
    assert r.conn.execute('SELECT count(*) FROM llm_scenario_intents').fetchone()[0] == 0


@pytest.mark.parametrize('change', [
    lambda ctx: ctx.pop('unknown_intents', None),
    lambda ctx: ctx.update(unknown_intents=True),
    lambda ctx: ctx.update(target_status={'unconfirmed': True}),
])
def test_missing_or_uncertain_state_keeps_final_capture(tmp_path, change):
    r, broker, calls, captures = setup_runtime(tmp_path, change=change)
    r.tick()
    assert captures == ['reconcile', 'context'] * 3
    assert broker.executed == []


def test_no_broker_flat_proof_keeps_final_capture(tmp_path):
    r, broker, calls, captures = setup_runtime(tmp_path, proof=False)
    assert r.tick()['status'] == 'wait'
    assert captures == ['reconcile', 'context'] * 3


def test_open_keeps_final_capture_and_execution(tmp_path):
    r, broker, calls, captures = setup_runtime(tmp_path, empty=False)
    assert r.tick()['status'] == 'intent_pending'
    assert captures == ['reconcile', 'context'] * 3
    assert len(broker.executed) == 1


def test_reused_capture_is_not_restamped_and_final_guard_delay_is_checked(tmp_path, monkeypatch):
    import live.scenario_review_guard as module
    r, broker, calls, captures = setup_runtime(tmp_path)
    original = module.guard_review
    seen = []

    def guarded(*args, **kwargs):
        seen.append(deepcopy(args[4]))
        result = original(*args, **kwargs)
        if len(seen) == 2:
            r.test_clock[0] += 11
        return result

    monkeypatch.setattr(module, 'guard_review', guarded)
    assert r.tick()['status'] == 'stale_proposal'
    assert len(seen) == 2
    assert seen[0]['account_captured_at'] == seen[1]['account_captured_at']
    assert captures == ['reconcile', 'context'] * 2
    assert broker.executed == []


@pytest.mark.parametrize('proof,expected', [(True, 'wait'), (False, 'blocked')])
def test_post_model_duplicate_io_crosses_deadline_only_without_reuse(tmp_path, proof, expected):
    r, broker, calls, captures = setup_runtime(tmp_path, proof=proof)
    propose = r.calculated_review_propose
    reconcile, context = broker.reconcile, broker.context

    def delayed_propose(*args, **kwargs):
        # The real adapter records its own phase clocks inside the generator;
        # delay record delivery after the completed envelope instead.
        envelope = propose(*args, **kwargs)
        r.test_clock[0] += 65.5
        return envelope

    def delayed_reconcile():
        r.test_clock[0] += 3
        return reconcile()

    def delayed_context():
        r.test_clock[0] += 2
        return context()

    r.calculated_review_propose = delayed_propose
    broker.reconcile, broker.context = delayed_reconcile, delayed_context
    result = r.tick()
    assert result['status'] == expected, result
    assert broker.executed == []
    if not proof:
        assert 'review_deadline_failure' in str(result)


def test_local_state_change_during_final_guard_cannot_be_overwritten(tmp_path, monkeypatch):
    import live.scenario_review_guard as module
    r, broker, calls, captures = setup_runtime(tmp_path)
    original = module.guard_review
    guards = []

    def guarded(*args, **kwargs):
        guards.append(1)
        result = original(*args, **kwargs)
        if len(guards) == 2:
            changed = r.state()
            changed['version'] += 1
            r._save(changed)
        return result

    monkeypatch.setattr(module, 'guard_review', guarded)
    assert r.tick()['status'] == 'stale_proposal'
    assert r.state()['version'] == 1
    assert broker.executed == []


@pytest.mark.parametrize('patch', [
    {'protection_confirmed': False},
    {'protection_status': 'not_managed_by_scenario_adapter'},
    {'observation': {'account_version': 'different'}},
    {'observation': {}},
])
def test_flat_proof_is_exact_and_complete(tmp_path, patch):
    r, broker, calls, captures = setup_runtime(tmp_path)
    reconcile = broker.reconcile
    def altered():
        return dict(reconcile(), **patch)
    broker.reconcile = altered
    assert r.tick()['status'] == 'wait'
    assert captures == ['reconcile', 'context'] * 3


def test_held_wait_retains_final_reconciliation(tmp_path):
    r, broker, calls = runtime(tmp_path, empty=True, candidate_action='EXIT')
    broker.ctx['unknown_intents'] = False
    observed = []
    original = broker.reconcile
    def reconcile():
        observed.append(1)
        return original()
    broker.reconcile = reconcile
    assert r.tick()['status'] == 'wait'
    assert len(observed) == 3
    assert broker.executed == []
