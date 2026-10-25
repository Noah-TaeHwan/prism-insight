"""Limited management is reported without concealing unresolved execution."""
import json
from datetime import datetime, timezone

import pytest

from live import healthcheck, scenario_runner, tracking
from tests.test_pending_market_health import connection


def approval():
    return dict(status='AUTHORIZED', approval_id='admin-1', approval_sha256='a'*64,
        scenario_id='scenario', contract_id='contract', expires_at=2300,
        allowed_actions=['WAIT','EXIT'], purpose='ADMIN_AUTHORIZATION_NOT_EXCHANGE_EVIDENCE')


def test_authority_module_is_bound_to_review_judgment_and_execution():
    from live import scenario_provenance
    name='live/scenario_management_quarantine.py'
    binding=scenario_provenance.review_policy_binding(research_mode='action-allocation-calculated-v1')
    assert name in binding['source_sha256']
    manifest=scenario_provenance._manifest()
    assert manifest['judgment_loaded_code_checks'][name]=='VERIFIED'
    assert manifest['execution_loaded_code_checks'][name]=='VERIFIED'


def test_verified_management_wait_is_not_claimed_as_full_recovery():
    c=connection()
    scenario_runner.record_health(c,dict(status='wait',management_quarantine=approval()),
        snapshot=dict(valid=True,as_of_ms=2199000),loop='decision',clock=lambda:2200)
    meta=json.loads(tracking.get_meta(c,'scenario_health_decision','demo'))
    assert meta['management_only'] is True
    issue=healthcheck._check_execution_stalled(c,'demo',datetime.fromtimestamp(2200,timezone.utc))
    assert issue['code']=='execution_stalled' and issue['level']=='alert'
    assert '최근 보유 관망 판단은 제한 관리 범위에서 완료' in issue['msg']
    assert '신규 진입은 차단' in issue['msg'] and '정산' in issue['msg']
    assert c.execute('SELECT status FROM llm_scenario_children').fetchone()[0]=='UNKNOWN'


@pytest.mark.parametrize('case',['expired','unapproved','wrong_actions','invalid_time','blocked','observation_only'])
def test_unverified_or_unsuccessful_result_cannot_claim_management_wait(case):
    c=connection()
    q=approval()
    result=dict(status='wait',management_quarantine=q)
    if case=='expired':
        q['expires_at']=2199
    elif case=='unapproved':
        q['status']='UNKNOWN'
    elif case=='wrong_actions':
        q['allowed_actions']=['WAIT','OPEN']
    elif case=='invalid_time':
        q['expires_at']=True
    elif case=='blocked':
        result['status']='blocked'
    else:
        result['market_observation_only']=True
    scenario_runner.record_health(c,result,loop='decision',clock=lambda:2200)
    meta=json.loads(tracking.get_meta(c,'scenario_health_decision','demo'))
    assert meta.get('management_only') is not True


@pytest.mark.parametrize('case',['stale','expired','two_unknown','bad_meta'])
def test_old_management_success_never_hides_new_or_unresolved_stall(case):
    c=connection()
    meta=dict(at=2199,status='wait',management_only=True,management_expires_at=2300,
        management_approval_id='admin-1',management_approval_sha256='a'*64)
    if case=='stale':
        meta['at']=1700
    elif case=='expired':
        meta['management_expires_at']=2199
    elif case=='two_unknown':
        c.execute("INSERT INTO llm_scenario_children VALUES('r','UNKNOWN',1001)")
    tracking.set_meta(c,'scenario_health_decision','[]' if case=='bad_meta' else json.dumps(meta),'demo')
    issue=healthcheck._check_execution_stalled(c,'demo',datetime.fromtimestamp(2200,timezone.utc))
    assert issue['code']=='execution_stalled' and issue['level']=='alert'
    assert '최근 보유 관망 판단은 제한 관리 범위에서 완료' not in issue['msg']


def test_explicit_release_wait_is_alerted_even_without_pending_intents():
    c=connection()
    c.execute("UPDATE llm_scenario_intents SET status='TERMINAL'")
    scenario_runner.record_health(c,dict(status='blocked',reason='quarantine_release_required'),
        loop='decision',clock=lambda:2200)
    issue=healthcheck._check_execution_stalled(c,'demo',datetime.fromtimestamp(2200,timezone.utc))
    assert issue['code']=='management_release_required' and issue['level']=='alert'
    assert '관리 승인 해제' in issue['msg']
    assert c.execute("SELECT count(*) FROM btc_events WHERE level='error'").fetchone()[0]==0


def test_flat_unsettled_wait_is_not_counted_as_repeated_processing_error():
    c=connection()
    scenario_runner.record_health(c,dict(status='blocked',reason='flat_quarantined_unsettled'),
        loop='decision',clock=lambda:2200)
    assert c.execute("SELECT count(*) FROM btc_events WHERE level='error'").fetchone()[0]==0
    assert healthcheck._check_execution_stalled(c,'demo',datetime.fromtimestamp(2200,timezone.utc))['code']=='execution_stalled'


@pytest.mark.parametrize('reason,reads', [('quarantine_release_required',1),('flat_quarantined_unsettled',1),('llm_call_failed',0)])
def test_cli_keeps_market_observation_separate_during_known_quarantine_halt(tmp_path,monkeypatch,capsys,reason,reads):
    import sys
    import time
    from live import scenario_broker, scenario_control
    path=tmp_path/'db.sqlite'
    c=tracking.get_connection(path)
    tracking.ensure_schema(c)
    c.close()
    monkeypatch.setattr(sys,'argv',['scenario_runner','--execute','--root-db',str(path)])
    monkeypatch.setattr(scenario_control,'read_control',lambda c:dict(main_uid='123',state='active'))
    monkeypatch.setattr(scenario_control,'existing_account_bindings',lambda c:('123','456'))
    monkeypatch.setattr(scenario_broker,'ScenarioDemoBroker',lambda *a,**k:object())
    seen=[]
    monkeypatch.setattr(scenario_runner,'collect_snapshot',lambda:seen.append(1) or dict(valid=True,as_of_ms=time.time()*1000))
    monkeypatch.setattr(scenario_runner,'run_once',lambda *a,**k:dict(status='blocked',reason=reason))
    monkeypatch.setattr(scenario_runner,'deliver_notices',lambda *a:None)
    monkeypatch.setattr(scenario_runner,'forward_run',lambda *a,**k:None)
    assert scenario_runner.main()==1
    result=json.loads(capsys.readouterr().out)
    assert len(seen)==reads and result['status']=='blocked'
    if reads:
        assert result['market_observation_only'] is True and result['model_called'] is False
