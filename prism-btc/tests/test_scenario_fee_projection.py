import pytest

from live.scenario_fee_projection import fee_only_projection, observed_fee_evidence


def evidence(rate=.00055):
    return dict(status='AVAILABLE', basis='HISTORICAL_VERIFIED_EXECUTIONS',
        rates={'TAKER': dict(count=1, minimum=rate, maximum=rate, earliest_at=1000, latest_at=1000)},
        source_sha256='a'*64)


@pytest.mark.parametrize('side,exit_price', [('LONG',81100),('LONG',81200),('SHORT',80900),('SHORT',80800)])
def test_near_exit_uses_two_fees_not_leverage(side, exit_price):
    r=fee_only_projection(side=side, entry_price=81000, exit_price=exit_price,
        quantity=1, evidence=evidence(), now=1001, new_entry=True)
    assert r['status']=='AVAILABLE'
    assert r['estimated_net_before_unmodelled_costs']==pytest.approx(abs(exit_price-81000)-(81000+exit_price)*.00055)


def test_cost_can_exceed_gross():
    r=fee_only_projection(side='LONG',entry_price=81000,exit_price=81100,quantity=1,
        evidence=evidence(.001),now=1001,new_entry=True)
    assert r['estimated_net_before_unmodelled_costs']<0


def test_holding_no_double_entry_fee_and_partial_is_not_scenario():
    r=fee_only_projection(side='LONG',entry_price=81000,exit_price=81100,quantity=.5,
        evidence=evidence(),now=1001,new_entry=False)
    assert r['scope']=='LEG_ONLY'
    assert r['assumed_entry_fee']==0
    whole=fee_only_projection(side='LONG',entry_price=81000,exit_price=80800,quantity=.5,
        evidence=evidence(),now=1001,new_entry=False,recorded_net=20)
    assert whole['scope']=='WHOLE_SCENARIO'
    assert whole['estimated_net_before_unmodelled_costs']<0


@pytest.mark.parametrize('now', [999,1000+86401])
def test_stale_future_unknown(now):
    assert fee_only_projection(side='LONG',entry_price=81000,exit_price=81100,quantity=1,
        evidence=evidence(),now=now,new_entry=True)['status']=='UNKNOWN'


def test_unverified_accounting_no_fee_evidence():
    assert observed_fee_evidence({}, {'status':'pending'}, now=1000)['status']=='UNKNOWN'


def test_accounting_unchanged_optional_metadata_is_verified_and_compact():
    from tests.test_scenario_accounting import fixture, run
    parts=fixture()
    before=run(parts)
    for trade in parts[0]['trades']:
        trade['raw_execution']['isMaker']=False
    after=run(parts)
    assert before==after
    result=observed_fee_evidence(parts[0],after,now=.1)
    assert result['rates']['TAKER']['count']==3
    assert len(result['source_sha256'])==64
    assert 'MAKER' not in result['rates']
    assert 'trades' not in result


@pytest.mark.parametrize('field,value', [('isMaker','false'),('isMaker',0),('execFee',0),
    ('execFee',-1),('execFee','NaN'),('execTime',2000000),('symbol','ETHUSDT'),('execType','Funding')])
def test_bad_fee_metadata_unknown_without_touching_accounting(field,value):
    from tests.test_scenario_accounting import fixture, run
    parts=fixture()
    accounting=run(parts)
    parts[0]['trades']=parts[0]['trades'][:1]
    parts[0]['trades'][0]['raw_execution'].update(isMaker=False)
    parts[0]['trades'][0]['raw_execution'][field]=value
    assert observed_fee_evidence(parts[0],accounting,now=.1)['status']=='UNKNOWN'
    assert accounting['net_pnl']==9.7


def test_maker_only_not_assumed_taker():
    e=evidence()
    e['rates']['MAKER']=e['rates'].pop('TAKER')
    assert fee_only_projection(side='LONG',entry_price=81000,exit_price=81100,quantity=1,
        evidence=e,now=1001,new_entry=True)['status']=='UNKNOWN'


def test_candidate_projections_keep_gross_and_risk_unchanged():
    from tests.test_scenario_calculated_review import context, wire
    from live.scenario_calculated_review import calculate_candidate
    ctx=context()
    plan=wire(ctx,'LONG')
    before=calculate_candidate(plan,ctx,candidate_id='c')
    ctx['observed_execution_fee_evidence']=evidence()
    after=calculate_candidate(plan,ctx,candidate_id='c')
    assert before['raw_risk']==after['raw_risk']
    assert before['eligible']==after['eligible']
    target=after['projection']['targets'][0]
    assert target['gross_pnl']==before['projection']['targets'][0]['gross_pnl']
    assert target['fee_only_projection']['status']=='AVAILABLE'
    assert target['fee_only_projection']['scope']=='LEG_ONLY'
    assert target['fee_only_projection']['assumed_entry_fee']>0


def test_runner_keeps_stress_allowance_and_recorded_net_once():
    from tests.test_scenario_runner_economics import evidence as accounting_evidence, view
    a=accounting_evidence()
    old=view(accounting=a)
    a['observed_execution_fee_evidence']=evidence()
    new=view(accounting=a)
    for key,value in old.items():
        if key!='fee_only_projection':
            assert new[key]==value
    mark=new['fee_only_projection']['at_mark']
    assert mark['assumed_entry_fee']==0
    assert mark['estimated_net_before_unmodelled_costs']==pytest.approx(a['net_pnl']+new['remaining_gross_at_mark']-mark['assumed_exit_fee'])


def test_holding_candidate_stop_exit_and_pending_scopes():
    from tests.test_scenario_calculated_review import context, wire
    from live.scenario_calculated_review import _holding_projection
    ctx=context()
    ctx.update(scenario_id='s',side='LONG',accounting_status='confirmed',
        positions=[dict(price=83410,quantity=.01)],recorded_scenario_net=-.46,
        observed_execution_fee_evidence=evidence())
    plan=wire(ctx,'LONG')
    plan.update(action='ADJUST',entries=[])
    r=_holding_projection(plan,ctx)
    full=r['confirmed_remaining']['fee_only_projection_at_full_stop']
    assert full['scope']=='WHOLE_SCENARIO'
    assert full['assumed_entry_fee']==0
    assert full['estimated_net_before_unmodelled_costs']==pytest.approx(-.46-5.1-.01*82900*.00055)
    assert r['hypothetical_all_fills']['fee_only_projection_at_full_stop']['status']=='UNKNOWN'
    plan['action']='EXIT'
    exit_=_holding_projection(plan,ctx)['fee_only_projection']
    assert exit_['scope']=='WHOLE_SCENARIO'
    assert exit_['estimated_net_before_unmodelled_costs']==pytest.approx(-.46-.01*83410*.00055)
