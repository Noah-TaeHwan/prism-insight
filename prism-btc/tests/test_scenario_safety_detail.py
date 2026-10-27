"""Only fixed failure categories may leave the runtime safety boundary."""
import pytest

from live.scenario_runtime import _fresh_safety_detail


@pytest.mark.parametrize(('code', 'detail'), [
    ('review_guard_context_changed:account_version', 'context_changed'),
    ('review_guard_context_changed:private-account-value', 'context_changed'),
    ('review_guard_effective_plan_changed', 'effective_plan_changed'),
    ('review_guard_stale_account', 'stale_account'),
    ('stop_crossed_market', 'stop_crossed_market'),
])
def test_safety_detail_is_a_fixed_category(code, detail):
    assert _fresh_safety_detail(code) == detail
    assert 'private-account-value' not in detail


@pytest.mark.parametrize('code', [None, '', 'unknown-secret', 'review_guard_stale_account:secret',
                                    'review_guard_context_changed', 42])
def test_other_errors_do_not_acquire_a_detail(code):
    assert _fresh_safety_detail(code) is None
