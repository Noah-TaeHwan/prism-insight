"""Lossless explanation aliases preserve complete typed sources and sealed receipts."""
from copy import deepcopy

import pytest

from engine.scenario_ma_context import build_ma_structure_context
from engine.scenario_snapshot import TIMEFRAME_MS,candle_start
from live.scenario_explanation import compile_explanation,digest,source_from_structure
from live.scenario_model_input import project_model_input,restore_model_input


def fixture():
    now=2_000_000_000_000
    frames={}
    for frame in ('30m','1h','4h','12h','1d','1w'):
        start=candle_start(now,TIMEFRAME_MS[frame])
        def point(offset,confirmed):
            price=80500+offset*10
            return dict(open_time_ms=start-offset*TIMEFRAME_MS[frame],observed_at_ms=now-1000,
                        open=price,high=price+5,low=price-5,close=price,ma10=price+1,ma35=price-1,
                        is_confirmed=confirmed)
        frames[frame]={'ma_path':dict(version=1,confirmed_points=[point(i,True) for i in (3,2,1)],
                                     forming_point=point(0,False))}
    snapshot=dict(valid=True,as_of_ms=now,timeframes=frames)
    context=dict(explanation_contract_version=1,scenario_id='s',positions=[dict(price=80600,quantity=.02)],
                 side='SHORT',mark_price=80500,account_captured_at=now/1000,now=now/1000,
                 current_plan=dict(action='OPEN',side='SHORT',take_profits=[dict(id='old',price=80400,fraction=.5)]))
    context['ma_structure']=build_ma_structure_context(snapshot,context)
    context['explanation_source']=source_from_structure(context['ma_structure'],context)
    proposals=[]
    for ident,action in (('WAIT','WAIT'),('c0','ADJUST'),('c1','REDUCE')):
        target='c0:TP:new' if action=='ADJUST' else 'CURRENT:TP:old'
        raw=dict(interpretation=(ident+' historical interpretation without authority. ')*2,
                 evidence_refs=['MARK','P:30m:0:low','P:1h:1:high'],
                 comparisons=[dict(level_ref=ref,target_ref=target) for ref in
                              ('P:30m:0:low','P:1h:1:high','MA:4h.ma10.forming')])
        proposals.append(dict(action=action,rationale=raw,entries=[],
                              take_profits=[dict(id='new',price=80350,fraction=.5)] if action=='ADJUST' else []))
    wrapper=dict(wait_proposal=proposals[0],candidates=proposals[1:],no_candidate_reason=deepcopy(proposals[0]['rationale']))
    receipts=[]
    for ident,proposal in zip(('WAIT','c0','c1'),proposals):
        effective=deepcopy(proposal)
        proof=compile_explanation(proposal['rationale'],context,effective,scope=ident,snapshot=snapshot)
        effective['rationale']=proof['text']
        receipt=dict(id=ident,eligible=True,raw_model_wrapper=deepcopy(wrapper),raw_model_wrapper_sha256=digest(wrapper),
                     raw_allocation_proposal=deepcopy(proposal),raw_allocation_sha256=digest(proposal),
                     raw_proposal=deepcopy(proposal),raw_sha256=digest(proposal),
                     effective_plan=effective,effective_sha256=digest(effective),explanation=proof)
        receipt['receipt_sha256']=digest(receipt)
        receipts.append(receipt)
    bundle=dict(wait=receipts[0],candidates=receipts[1:],no_candidate_reason=deepcopy(wrapper['no_candidate_reason']))
    return dict(market_snapshot=snapshot,contract_context=context,response_contract={},calculated_candidates=bundle,
                typed_evidence=dict(boolean=True,integer=1,decimal=1.0,ordered=[3,1,2]))


def seal(receipt):
    receipt['receipt_sha256']=digest({k:v for k,v in receipt.items() if k!='receipt_sha256'})


def marker_nodes(value,key):
    if isinstance(value,dict):
        if key in value:
            yield value
        for child in value.values():
            yield from marker_nodes(child,key)
    elif isinstance(value,list):
        for child in value:
            yield from marker_nodes(child,key)


def projected():
    source=fixture()
    result,_=project_model_input(source,allow_gap_rescue=True)
    assert result['model_input_projection']['explanation_copy_version']==1
    assert result['model_input_projection']['explanation_source_version']==1
    return source,result


def test_full_catalog_and_all_seven_proofs_restore_canonical_and_receipt_hashes():
    source,result=projected()
    before=deepcopy(source)
    metadata=result['model_input_projection']
    assert set(metadata['explanation_copy_paths'])=={'WAIT.raw','WAIT.text','c0.raw','c0.text','c1.raw','c1.text','no_candidate_reason'}
    assert metadata['explanation_source_path']=='contract_context.explanation_source'
    assert len(list(marker_nodes(result,'$explanation_copy')))==7
    assert len(list(marker_nodes(result,'$explanation_source')))==1
    restored=restore_model_input(result)
    assert digest(restored)==digest(source) and source==before
    assert restored['calculated_candidates']==source['calculated_candidates']
    assert restored['contract_context']['explanation_source']['sha256']==source['contract_context']['explanation_source']['sha256']
    for receipt in [restored['calculated_candidates']['wait']]+restored['calculated_candidates']['candidates']:
        assert receipt['receipt_sha256']==digest({k:v for k,v in receipt.items() if k!='receipt_sha256'})
        assert receipt['explanation']['sha256']==digest({k:v for k,v in receipt['explanation'].items() if k!='sha256'})


@pytest.mark.parametrize('fault',['duplicate_path','unknown_path','unknown_version','missing_path',
                                  'copy_hash','copy_kind','cross_candidate','missing_wrapper','source_hash',
                                  'source_path','source_version','missing_structure','missing_mark','missing_capture',
                                  'copy_version_bool','source_version_bool','point_price','point_order'])
def test_registered_alias_tampering_is_rejected(fault):
    _,result=projected()
    metadata=result['model_input_projection']
    copies=list(marker_nodes(result,'$explanation_copy'))
    if fault=='duplicate_path':
        metadata['explanation_copy_paths'].append(metadata['explanation_copy_paths'][0])
    elif fault=='unknown_path':
        metadata['explanation_copy_paths'][0]='c9.raw'
    elif fault=='unknown_version':
        metadata['explanation_copy_version']=2
    elif fault=='missing_path':
        metadata['explanation_copy_paths'].pop()
    elif fault=='copy_hash':
        copies[0]['restored_sha256']='0'*64
    elif fault=='copy_kind':
        copies[0]['$explanation_copy']='foreign.raw'
    elif fault=='cross_candidate':
        candidates=result['calculated_candidates']['candidates']
        candidates[1]['explanation']['raw']=deepcopy(candidates[0]['explanation']['raw'])
    elif fault=='missing_wrapper':
        result['calculated_candidates']['wait'].pop('raw_model_wrapper')
    elif fault=='source_hash':
        next(marker_nodes(result,'$explanation_source'))['restored_sha256']='0'*64
    elif fault=='source_path':
        metadata['explanation_source_path']='calculated_candidates.wait.explanation'
    elif fault=='source_version':
        metadata['explanation_source_version']=2
    elif fault=='copy_version_bool':
        metadata['explanation_copy_version']=True
    elif fault=='source_version_bool':
        metadata['explanation_source_version']=True
    elif fault in {'point_price','point_order'}:
        points=result['contract_context']['ma_structure']['primary']['30m']['points']
        if isinstance(points,dict):
            table=points['$row_table']
            if fault=='point_order':
                table['rows'].reverse()
            else:
                table['rows'][0][table['columns'].index('low')]+=1
        elif fault=='point_order':
            points.reverse()
        else:
            points[0]['low']+=1
    elif fault=='missing_structure':
        result['contract_context'].pop('ma_structure')
    elif fault=='missing_mark':
        result['contract_context'].pop('mark_price')
    else:
        result['contract_context'].pop('account_captured_at')
    with pytest.raises(ValueError):
        restore_model_input(result)


@pytest.mark.parametrize('marker',[{'$explanation_copy':'raw','restored_sha256':'0'*64},
                                  {'$explanation_source':'ma-structure-v1','restored_sha256':'0'*64}])
@pytest.mark.parametrize('operation',[project_model_input,restore_model_input])
def test_unregistered_or_input_reserved_markers_are_rejected(marker,operation):
    raw=dict(market_snapshot={},contract_context={},response_contract={},foreign=marker)
    with pytest.raises(ValueError):
        operation(raw)


@pytest.mark.parametrize('status',['UNKNOWN','OMITTED_INPUT_BUDGET'])
def test_unknown_source_is_never_rederived_or_promoted(status):
    source=fixture()
    source['contract_context']['explanation_source']=dict(version=1,status=status)
    for receipt in [source['calculated_candidates']['wait']]+source['calculated_candidates']['candidates']:
        proof=receipt['explanation']
        proof['source_status']='UNKNOWN'
        proof['source_sha256']=None
        proof['sha256']=digest({k:v for k,v in proof.items() if k!='sha256'})
        seal(receipt)
    result,_=project_model_input(source)
    assert not list(marker_nodes(result,'$explanation_source'))
    restored=restore_model_input(result)
    assert digest(restored)==digest(source)
    assert restored['contract_context']['explanation_source']['status']==status
    assert all(r['explanation']['source_status']=='UNKNOWN' for r in restored['calculated_candidates']['candidates'])


@pytest.mark.parametrize('replacement',[80500.0,True])
def test_numeric_type_changes_do_not_share_derived_source(replacement):
    source=fixture()
    source['contract_context']['explanation_source']['points'][0][1]=replacement
    evidence=source['contract_context']['explanation_source']
    evidence['sha256']=digest({k:v for k,v in evidence.items() if k!='sha256'})
    result,_=project_model_input(source)
    assert not list(marker_nodes(result,'$explanation_source'))
    assert digest(restore_model_input(result))==digest(source)


def test_legacy_projection_bytes_remain_pinned_without_new_contract():
    source=dict(market_snapshot={'timeframes':{'30m':{'recent_confirmed_bars':[
        dict(open_time_ms=i,close=float(i+100),is_confirmed=True) for i in range(30)]}}},
        contract_context={},response_contract={})
    result,_=project_model_input(source)
    assert result['model_input_projection']['version']==2
    assert digest(result)=='fb328858c5e8eac69a6b8c5e0d4a0075031b42a7dde84dedd4c06a02ce30d1c2'
    assert digest(restore_model_input(result))==digest(source)
