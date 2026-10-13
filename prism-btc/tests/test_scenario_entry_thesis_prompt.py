"""Holding-review framing contract; these tests do not prove trading returns."""
from live.scenario_llm import SYSTEM_PROMPT, MA_STRUCTURE_PROMPT


def test_entry_specific_failure_not_ma_order_only():
    assert 'When entry_thesis is VERIFIED' in SYSTEM_PROMPT
    assert 'An unchanged bullish/bearish MA order alone is not a reason' in SYSTEM_PROMPT
    assert 'prefer EXIT or a justified executable conditional reduction' in SYSTEM_PROMPT
    assert 'state the current evidence' in SYSTEM_PROMPT


def test_trigger_and_elapsed_are_not_automatic_exit_rules():
    assert 'Entry activation triggers are not automatic invalidation lines' in SYSTEM_PROMPT
    assert 'a normal retest, elapsed time, or slow favorable progress alone' in SYSTEM_PROMPT
    assert 'no fixed holding-time stop' in SYSTEM_PROMPT
    assert 'Holding is not a fresh-entry test' in SYSTEM_PROMPT


def test_missing_endpoints_and_additions_are_not_fabricated_paths():
    assert 'endpoint comparison is not the intervening' in SYSTEM_PROMPT
    assert 'MISSING entry evidence is unknown' in SYSTEM_PROMPT
    assert 'partial fills, reductions and later additions separately' in SYSTEM_PROMPT
    assert 'not remaining-lot cost' in SYSTEM_PROMPT


def test_existing_symmetric_protection_and_timeframe_rules_remain():
    assert 'Symmetric rules apply to SHORT' in SYSTEM_PROMPT
    assert 'Immediate partial market reduction is unsupported' in SYSTEM_PROMPT
    assert 'Keep fixed 10x, the original' in SYSTEM_PROMPT
    assert '30m/1h drive direction and 15m refines timing' in MA_STRUCTURE_PROMPT


def test_entry_helper_is_in_policy_provenance():
    from live import scenario_entry_thesis  # noqa: F401
    from live.scenario_provenance import _manifest
    manifest = _manifest()
    assert 'live/scenario_entry_thesis.py' in manifest['source_hashes']
