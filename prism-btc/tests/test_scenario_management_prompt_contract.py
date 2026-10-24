"""Management wording matches host state identity and observed execution outcomes."""
import pytest

from live.scenario_llm import entry_contract_prompt,immediate_reduce_instructions


@pytest.mark.parametrize('version',[1,2])
def test_assessment_contract_compares_state_not_identical_reason(version):
    text=' '.join(entry_contract_prompt({'entry_contract_version':version}).split())
    assert 'same contract_id and status; reason wording may differ' in text
    assert 'exact same thesis_assessment' not in text
    assert 'FAILED likewise requires EXIT' in text


def test_reduce_preconditions_do_not_ban_verified_live_exit_orders():
    text=' '.join(immediate_reduce_instructions('',{'immediate_reduce_version':1}).split())
    assert 'no pending_entries and no unknown_intents' in text
    assert 'Verified LIVE TP/partial-SL orders are not pending entries' in text
    assert 'without pending/unknown orders' not in text


def test_target_restoration_is_an_observed_outcome_not_a_promise():
    text=' '.join(immediate_reduce_instructions('',{'immediate_reduce_version':1}).split())
    assert 'On successful target restoration' in text
    assert 'immediate_reduce_result' in text
    assert 'RESTORE_FAILED and SKIPPED_FAILED are not successful restoration' in text
    assert 'FAILED requires full EXIT' in text
