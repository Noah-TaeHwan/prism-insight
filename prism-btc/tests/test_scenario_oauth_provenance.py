import hashlib
from pathlib import Path

import pytest

from live import scenario_oauth, scenario_provenance


def test_calculated_review_binds_loaded_oauth_source():
    binding = scenario_provenance.review_policy_binding(research_mode='economic-reject-calculated-v1')
    name = 'live/scenario_oauth.py'
    assert binding['source_sha256'][name] == hashlib.sha256(Path(scenario_oauth.__file__).read_bytes()).hexdigest()
    assert binding['loaded_parity'][name] == 'VERIFIED'


def test_mixed_loaded_oauth_is_not_verified(monkeypatch):
    def changed(*args, **kwargs):
        return None
    changed.__name__ = 'generate_scenario'
    changed.__qualname__ = 'generate_scenario'
    changed.__module__ = 'live.scenario_oauth'
    monkeypatch.setattr(scenario_oauth, 'generate_scenario', changed)
    with pytest.raises(ValueError, match='review_mixed_loaded_policy'):
        scenario_provenance.review_policy_binding(research_mode='economic-reject-calculated-v1')
