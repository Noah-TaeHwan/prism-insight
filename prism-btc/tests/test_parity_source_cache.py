"""Warm comparison inputs must never hide runtime or source mutations."""
import sys
import types

import pytest

from live.scenario_provenance import _loaded_parity

SOURCE = "POLICY = {'size': 1}\ndef rule():\n    return POLICY['size']\n"
NAME = 'live.parity_cache_probe'


@pytest.fixture
def probe(monkeypatch):
    module = types.ModuleType(NAME)
    exec(compile(SOURCE, '<fixture>', 'exec'), module.__dict__)
    monkeypatch.setitem(sys.modules, NAME, module)
    assert _loaded_parity(NAME+'.py', SOURCE) == 'VERIFIED'
    assert _loaded_parity(NAME+'.py', SOURCE) == 'VERIFIED'
    return module


def test_warm_source_still_checks_actual_loaded_function(probe):
    probe.rule.__code__ = (lambda: 99).__code__
    assert _loaded_parity(NAME+'.py', SOURCE) == 'MIXED'


def test_warm_source_still_checks_mutable_literal_configuration(probe):
    probe.POLICY['size'] = 2
    assert _loaded_parity(NAME+'.py', SOURCE) == 'MIXED'


def test_source_content_not_module_name_selects_expected_values(probe):
    changed = SOURCE.replace("'size': 1", "'size': 2")
    assert _loaded_parity(NAME+'.py', changed) == 'MIXED'
    exec(compile(changed, '<fixture>', 'exec'), probe.__dict__)
    assert _loaded_parity(NAME+'.py', changed) == 'VERIFIED'
    assert _loaded_parity(NAME+'.py', SOURCE) == 'MIXED'


def test_missing_module_is_not_a_cached_success(probe, monkeypatch):
    monkeypatch.delitem(sys.modules, NAME)
    assert _loaded_parity(NAME+'.py', SOURCE) == 'UNKNOWN'


def test_changed_source_function_body_is_not_replaced_by_warm_compilation(probe):
    changed = SOURCE.replace("return POLICY['size']", 'return 99')
    assert _loaded_parity(NAME+'.py', changed) == 'MIXED'
    exec(compile(changed, '<fixture>', 'exec'), probe.__dict__)
    assert _loaded_parity(NAME+'.py', changed) == 'VERIFIED'
    assert _loaded_parity(NAME+'.py', SOURCE) == 'MIXED'
