from pathlib import Path
from types import SimpleNamespace

from observability import btc_scenario_export, shipper


def args(tmp_path, disabled=False):
    source = tmp_path/'source.sqlite'
    source.touch()
    return SimpleNamespace(no_btc_export=disabled, btc_source=source, btc_state=None,
        checkpoint=tmp_path/'checkpoint.json', spool=tmp_path/'events.jsonl')


def test_disabled_does_not_touch_source(monkeypatch, tmp_path):
    monkeypatch.setattr(btc_scenario_export, 'export_once', lambda *a, **kw: (_ for _ in ()).throw(AssertionError()))
    assert shipper._export_btc(args(tmp_path, True)) == {'status':'DISABLED'}


def test_bounded_existing_worker_integration(monkeypatch, tmp_path):
    calls = []
    def capture(*a, **kw):
        calls.append((a,kw))
        return {'status':'OK','captured':0,'emitted':0}
    monkeypatch.setattr(btc_scenario_export, 'export_once', capture)
    values = args(tmp_path)
    assert shipper._export_btc(values)['status']=='OK'
    assert calls[0][0] == (values.btc_source, tmp_path/'btc-research-export.sqlite', values.spool)
    assert calls[0][1] == {'limit':32}


def test_export_error_does_not_prevent_main_shipment(monkeypatch, tmp_path, capsys):
    values = args(tmp_path)
    def broken(*a, **kw):
        raise ValueError('do not expose private source contents')
    monkeypatch.setattr(btc_scenario_export, 'export_once', broken)
    shipped = []
    monkeypatch.setattr(shipper, 'run_once', lambda **kw: shipped.append(True) or 1)
    monkeypatch.setattr(shipper.signal, 'signal', lambda *a: None)
    assert shipper.main(['--once','--btc-source',str(values.btc_source),'--spool',str(values.spool),
                        '--checkpoint',str(values.checkpoint)]) == 0
    assert shipped == [True]
    output = capsys.readouterr()
    assert 'private source' not in output.err


def test_explicit_state_override(monkeypatch, tmp_path):
    values=args(tmp_path);values.btc_state=Path(tmp_path/'explicit.sqlite')
    def capture(source,state,spool,**kw):
        assert state==values.btc_state
        return {'status':'OK','emitted':0}
    monkeypatch.setattr(btc_scenario_export,'export_once',capture)
    assert shipper._export_btc(values)['status']=='OK'


def test_research_correlation_fields_are_indexed_without_changing_body():
    import json
    event = {'event_type':'btc.research.source','event_id':'a'*32,'trace_id':'b'*32,
             'span_id':'c'*16,'attributes':{'scenario_id_ref':'d'*64,'intent_id_ref':'e'*64,
             'decision_slot':123,'source_kind':'OUTCOME','ledger_status':'available'}}
    payload = shipper.build_otlp_payload([event])
    record = payload['resourceLogs'][0]['scopeLogs'][0]['logRecords'][0]
    attrs = {row['key']:row['value'] for row in record['attributes']}
    assert attrs['prism.scenario_ref']['stringValue']=='d'*64
    assert attrs['prism.decision_slot']['intValue']=='123'
    assert attrs['prism.ledger_status']['stringValue']=='available'
    assert json.loads(record['body']['stringValue'])==event


def test_checkpoint_is_fsynced_before_acceptance_proof_can_be_removed(tmp_path, monkeypatch):
    import os
    import stat
    calls=[]
    original=shipper.os.fsync
    def record(descriptor):
        calls.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        original(descriptor)
    monkeypatch.setattr(shipper.os,'fsync',record)
    path=tmp_path/'checkpoint.json'
    shipper.save_checkpoint(path,shipper.Checkpoint(1,100))
    assert calls == [False,True]
    assert shipper.load_checkpoint(path)==shipper.Checkpoint(1,100)
