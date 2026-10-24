"""An unresolved order is not a fresh decision and must remain visible."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from live import healthcheck, scenario_runner, tracking


def connection():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    tracking.ensure_schema(c)
    c.execute('CREATE TABLE llm_scenario_control(id INTEGER,body TEXT)')
    c.execute('INSERT INTO llm_scenario_control VALUES(1,?)', (json.dumps(dict(version=1,state='active',main_uid='123')),))
    c.execute('CREATE TABLE llm_scenario_intents(id TEXT,status TEXT)')
    c.execute("INSERT INTO llm_scenario_intents VALUES('r','PENDING')")
    c.execute('CREATE TABLE llm_scenario_children(intent_id TEXT,status TEXT,created_at REAL)')
    c.execute("INSERT INTO llm_scenario_children VALUES('r','UNKNOWN',1000)")
    c.execute('CREATE TABLE llm_scenario_decisions(slot INTEGER)')
    c.execute('INSERT INTO llm_scenario_decisions VALUES(3)')
    return c


def test_fresh_observation_does_not_hide_stalled_execution_or_advance_decision_input():
    c=connection()
    now=2200
    tracking.set_meta(c,'scenario_input_asof_ms',1000000,'demo')
    scenario_runner.record_health(c,dict(status='intent_pending',market_observation_only=True,
        market_observation_status='observed',model_called=False),
        snapshot=dict(valid=True,as_of_ms=2199000),loop='decision',clock=lambda:now)
    assert tracking.get_meta(c,'scenario_input_asof_ms','demo')==1000000
    assert tracking.get_meta(c,'scenario_market_observed_ms','demo')==2199000
    status=json.loads(tracking.get_meta(c,'scenario_health_decision','demo'))
    assert status['market_observation_only'] is True and status['model_called'] is False
    assert healthcheck._check_price_stale(c,'demo',datetime.fromtimestamp(now,timezone.utc)) is None
    issue=healthcheck._check_execution_stalled(c,'demo',datetime.fromtimestamp(now,timezone.utc))
    assert issue['code']=='execution_stalled' and issue['level']=='alert'
    assert '20분' in issue['msg']
    assert '정상' not in issue['msg']


def test_failed_read_cannot_advance_either_timestamp():
    c=connection()
    tracking.set_meta(c,'scenario_input_asof_ms',1000000,'demo')
    tracking.set_meta(c,'scenario_market_observed_ms',1000000,'demo')
    scenario_runner.record_health(c,dict(status='intent_pending',market_observation_only=True,
        market_observation_status='failed'),snapshot=dict(valid=True,as_of_ms=999999999),
        loop='decision',clock=lambda:2200)
    assert tracking.get_meta(c,'scenario_input_asof_ms','demo')==1000000
    assert tracking.get_meta(c,'scenario_market_observed_ms','demo')==1000000
    assert healthcheck._check_price_stale(c,'demo',datetime.fromtimestamp(2200,timezone.utc))['code']=='price_stale'


def test_short_pending_and_other_modes_do_not_raise_stall():
    c=connection()
    assert healthcheck._check_execution_stalled(c,'demo',datetime.fromtimestamp(1100,timezone.utc)) is None
    assert healthcheck._check_execution_stalled(c,'shadow',datetime.fromtimestamp(2200,timezone.utc)) is None
    c.execute("UPDATE llm_scenario_intents SET status='TERMINAL'")
    assert healthcheck._check_execution_stalled(c,'demo',datetime.fromtimestamp(2200,timezone.utc)) is None


@pytest.mark.parametrize('bad', ['invalid','future','stale','exception'])
def test_observation_failure_preserves_pending_and_never_claims_model_call(bad):
    result=dict(status='intent_pending',reason='awaiting_exact_evidence')
    def snapshot():
        if bad=='exception':
            raise RuntimeError('secret')
        return dict(valid=bad!='invalid',as_of_ms={'invalid':1000000,'future':2000000,'stale':1}[bad])
    out=scenario_runner.observe_pending_market(result,snapshot,clock=lambda:1000)
    assert out['status']=='intent_pending' and out['reason']=='awaiting_exact_evidence'
    assert out['market_observation_status']=='failed' and out['model_called'] is False
    assert 'secret' not in str(out)


def test_observation_is_not_a_decision_or_order():
    seen=[]
    result=scenario_runner.observe_pending_market(dict(status='intent_pending'),
        lambda:seen.append('snapshot') or dict(valid=True,as_of_ms=999000),clock=lambda:1000)
    assert seen==['snapshot'] and result['market_observation_status']=='observed'
    assert result['model_called'] is False


@pytest.mark.parametrize('execute,already_collected,expected_reads', [(True,False,1),(True,True,1),(False,False,0)])
def test_cli_observes_only_pending_decision_without_prior_snapshot(tmp_path, monkeypatch, capsys, execute, already_collected, expected_reads):
    import sys
    import time
    from concurrent.futures import ThreadPoolExecutor
    from live import scenario_broker, scenario_control
    from live.shared_entry_coordinator import mutation_lock
    path=tmp_path/'main.db'
    c=tracking.get_connection(path)
    tracking.ensure_schema(c)
    c.close()
    monkeypatch.setattr(sys,'argv',['scenario_runner','--execute' if execute else '--protect-only','--root-db',str(path)])
    monkeypatch.setattr(scenario_control,'read_control',lambda c:dict(main_uid='123',state='active'))
    monkeypatch.setattr(scenario_control,'existing_account_bindings',lambda c:('123','456'))
    monkeypatch.setattr(scenario_broker,'ScenarioDemoBroker',lambda *a,**k:object())
    reads=[]
    def other_thread_lock():
        connection=sqlite3.connect(path)
        with mutation_lock(connection):
            pass
        connection.close()
    def snapshot():
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(other_thread_lock).result(timeout=3)
        reads.append('read')
        return dict(valid=True,as_of_ms=time.time()*1000)
    monkeypatch.setattr(scenario_runner,'collect_snapshot',snapshot)
    def runtime(c,b,**kwargs):
        with mutation_lock(c):
            pass
        if already_collected:
            kwargs['snapshot']()
        return dict(status='intent_pending')
    monkeypatch.setattr(scenario_runner,'run_once',runtime)
    monkeypatch.setattr(scenario_runner,'deliver_notices',lambda *a:None)
    monkeypatch.setattr(scenario_runner,'forward_run',lambda *a,**k:None)
    assert scenario_runner.main()==0
    result=json.loads(capsys.readouterr().out)
    assert len(reads)==expected_reads
    assert result['status']=='intent_pending'
    if execute and not already_collected:
        assert result['market_observation_only'] is True and result['model_called'] is False


def test_stall_is_visible_in_watchdog_even_with_fresh_market_and_protection():
    c=connection()
    tracking.set_meta(c,'scenario_market_observed_ms',2199000,'demo')
    tracking.set_meta(c,'scenario_health_protection',json.dumps(dict(at=2199,status='protection_checked')),'demo')
    issues=healthcheck.run_healthcheck(c,'demo',datetime.fromtimestamp(2200,timezone.utc))
    issue=next(i for i in issues if i['code']=='execution_stalled')
    assert issue['level']=='alert' and '최근 보호 점검은 정상' in issue['msg']
    assert '[주문확인정체]' in healthcheck._build_alert_message([issue],'demo',datetime.fromtimestamp(2200,timezone.utc))


@pytest.mark.parametrize('protection',['broken','[]',json.dumps(dict(at=2300,status='protection_checked'))])
def test_missing_or_future_protection_never_hides_known_stall(protection):
    c=connection()
    tracking.set_meta(c,'scenario_health_protection',protection,'demo')
    issue=healthcheck._check_execution_stalled(c,'demo',datetime.fromtimestamp(2200,timezone.utc))
    assert issue['code']=='execution_stalled' and issue['level']=='alert'
    assert '최근 보호 점검은 정상' not in issue['msg']

