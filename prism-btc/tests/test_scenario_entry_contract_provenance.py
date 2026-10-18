"""Entry-failure authority must be in both policy and execution identity."""
import hashlib
from pathlib import Path

from live import scenario_entry_contract, scenario_provenance
from live.scenario_runtime import ScenarioRuntime


def test_contract_module_is_bound_to_review_and_execution_manifest():
    name = 'live/scenario_entry_contract.py'
    source = Path(scenario_entry_contract.__file__).read_bytes()
    binding = scenario_provenance.review_policy_binding(research_mode='economic-reject-calculated-v1')
    assert binding['source_sha256'][name] == hashlib.sha256(source).hexdigest()
    assert binding['loaded_parity'][name] == 'VERIFIED'
    manifest = scenario_provenance._manifest()
    assert manifest['source_hashes'][name] == hashlib.sha256(source).hexdigest()
    assert manifest['execution_loaded_code_checks'][name] == 'VERIFIED'
    assert manifest['judgment_loaded_code_checks'][name] == 'VERIFIED'


def test_host_failure_exit_is_part_of_runtime_execution_parity(monkeypatch):
    def changed(*args, **kwargs):
        return None
    changed.__name__ = '_entry_contract_exit'
    changed.__qualname__ = 'ScenarioRuntime._entry_contract_exit'
    changed.__module__ = 'live.scenario_runtime'
    monkeypatch.setattr(ScenarioRuntime, '_entry_contract_exit', changed)
    manifest = scenario_provenance._manifest()
    assert manifest['execution_loaded_code_checks']['live/scenario_runtime.py'] == 'MIXED'


def test_contract_observation_context_is_execution_authority(monkeypatch):
    def changed(*args, **kwargs):
        return None
    changed.__name__ = '_context'
    changed.__qualname__ = 'ScenarioRuntime._context'
    changed.__module__ = 'live.scenario_runtime'
    monkeypatch.setattr(ScenarioRuntime, '_context', changed)
    manifest = scenario_provenance._manifest()
    assert manifest['execution_loaded_code_checks']['live/scenario_runtime.py'] == 'MIXED'


def test_replaced_contract_observer_cannot_keep_verified_identity(monkeypatch):
    def changed(*args, **kwargs):
        return None
    changed.__name__ = 'observe_contract'
    changed.__qualname__ = 'observe_contract'
    changed.__module__ = 'live.scenario_entry_contract'
    monkeypatch.setattr(scenario_entry_contract, 'observe_contract', changed)
    manifest = scenario_provenance._manifest()
    assert manifest['execution_loaded_code_checks']['live/scenario_entry_contract.py'] == 'MIXED'


def test_replay_preserves_original_contract_and_failure_registrations():
    from analysis.scenario_replay import RUNTIME_EVENT_QUERIES
    assert RUNTIME_EVENT_QUERIES['llm_scenario_entry_contracts'] == (
        'SELECT * FROM llm_scenario_entry_contracts ORDER BY rowid')
    assert RUNTIME_EVENT_QUERIES['llm_scenario_entry_failures'] == (
        'SELECT * FROM llm_scenario_entry_failures ORDER BY rowid')
