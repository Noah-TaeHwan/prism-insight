"""The opt-in policy loader has a literal, closed import surface."""
import importlib

from live.scenario_provenance import review_policy_binding


def test_review_binding_does_not_dispatch_module_names(monkeypatch):
    before = review_policy_binding()  # Warm ordinary dependencies first.
    assert len(before['source_sha256']) == 15
    assert set(before['loaded_parity'].values()) == {'VERIFIED'}

    def forbidden(*_args, **_kwargs):
        raise AssertionError('dynamic module dispatch is forbidden')

    monkeypatch.setattr(importlib, 'import_module', forbidden)
    assert review_policy_binding() == before
