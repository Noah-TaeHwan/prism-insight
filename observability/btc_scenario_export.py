"""Bounded read-only BTC evidence bridge. Delivery is at-least-once by stable ID.

No trading imports, source writes, network calls, historical backfill or order authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time

from .events import build_event

STREAMS = ('llm_scenario_audit_events', 'llm_scenario_recovery_events',
           'llm_scenario_broker_evidence', 'llm_scenario_outbox')
NUMBERS = {'quantity', 'price', 'mark_price', 'hard_stop', 'previous_hard_stop', 'initial_equity',
           'realized_loss', 'fees_paid', 'funding_paid', 'net_pnl', 'gross_pnl', 'fees', 'funding',
           'budget', 'total_risk', 'available', 'proposed_risk', 'reserved_risk', 'confidence',
           'ma10', 'ma35', 'open', 'high', 'low', 'close', 'volume', 'progress', 'remaining_ms',
           'captured_at', 'observed_at_ms', 'open_time_ms', 'revision', 'scenario_risk_fraction'}
NUMBERS |= {'realized_pnl', 'net_realized_pnl', 'funding_net', 'cumulative_qty', 'entry_quantity',
            'exit_quantity', 'remaining_quantity', 'entry_notional', 'exit_notional',
            'fraction', 'avgPrice', 'stopLoss', 'takeProfit', 'markPrice', 'size', 'equity',
            'old_stop', 'new_stop', 'proposed_stop', 'old_hard_stop', 'new_hard_stop',
            'initial_hard_stop', 'totalEquity', 'positionValue', 'positionIM', 'leverage',
            'old_risk', 'new_risk', 'risk_before', 'risk_after', 'ma_gap', 'ma_gap_fraction'}
BOOLEANS = {'protection_confirmed', 'terminal', 'protection_ok', 'orders_reconciled'}
ENUMS = {'action', 'side', 'status', 'accounting_status', 'loaded_code_status',
         'price_position', 'ma_order', 'gap_state'}
ENUM_VALUES = frozenset(('WAIT OPEN ADJUST EXIT LONG SHORT Buy Sell buy sell confirmed pending '
    'unknown UNKNOWN VERIFIED MIXED blocked ok accepted rejected intent_pending terminal '
    'TERMINAL PENDING SUBMITTED FILLED CANCELLED CANCELED QUEUED SENDING SENT ACK '
    'ABOVE BELOW BETWEEN AT_MA10 AT_MA35 AT_BOTH BULLISH BEARISH EQUAL WIDENING '
    'NARROWING FLAT UNAVAILABLE unavailable available').split())
REASON_CODES = frozenset(('input_size snapshot_unavailable snapshot_stale invalid_contract_context '
    'invalid_input oauth_model_failed late_response invalid_json model_contract_failed '
    'llm_input_preparation_failed llm_output_contract_failed llm_call_failed '
    'reconciliation_or_proposal_failed awaiting_exact_evidence submission_unknown '
    'another_protection_or_execution_tick').split())
CONTAINERS = {'risk', 'positions', 'pending_entries', 'before', 'after', 'presented', 'outcome',
              'confirmed', 'forming', 'ohlcv', 'timeframes', '15m', '30m', '1h', '4h', '12h', '1d', '1w'}
CONTAINERS |= {'result', 'observation', 'owned_orders', 'position', 'take_profits',
              'partial_stops', 'current_plan', 'stop_policy', 'risk_before', 'risk_after',
              'consumed_costs', 'settlement'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _facts(value, prefix='', depth=0):
    """Exact typed numeric facts and code enums, never arbitrary free text or raw IDs."""
    out = {}
    if depth > 5 or not isinstance(value, dict):
        return out
    for key, item in value.items():
        path = prefix + ('ledger_status' if key == 'accounting_status' else key)
        if key in NUMBERS and type(item) in (int, float) and math.isfinite(item):
            out[path] = item
        elif key in NUMBERS and isinstance(item, str) and len(item) <= 80:
            try:
                if Decimal(item).is_finite():
                    out[path] = item  # Preserve broker numeric-string representation exactly.
            except InvalidOperation:
                pass
        elif key in BOOLEANS and type(item) is bool:
            out[path] = item
        elif key in ENUMS and isinstance(item, str) and item in ENUM_VALUES:
            out[path] = item
        elif key in {'reason', 'failure_code'} and isinstance(item, str):
            if item in REASON_CODES:
                out[path] = item
            else:
                out[path + '_sha256'] = _hash(item)
                out[path + '_text_omitted'] = True
        elif key in CONTAINERS:
            if isinstance(item, dict):
                out.update(_facts(item, path + '.', depth + 1))
            elif isinstance(item, list):
                out[path + '.source_count'] = len(item)
                out[path + '.complete'] = len(item) <= 30
                for index, row in enumerate(item[:30]):
                    out.update(_facts(row, path + f'.{index}.', depth + 1))
    return out


def _identity(path):
    stat = path.stat()
    return _hash(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}')


def _guard_paths(source, state, spool):
    """Reject all configured aliases before chmod, creation, or SQLite writes."""
    groups = [[Path(str(base) + suffix) for suffix in ('', '-wal', '-shm', '-journal')]
              for base in (source, state)] + [[spool]]
    if state.is_symlink() or spool.is_symlink():
        raise ValueError('unsafe_output_symlink')
    for i, group in enumerate(groups):
        for other in groups[i + 1:]:
            for left in group:
                for right in other:
                    if left.resolve() == right.resolve() or (left.exists() and right.exists() and left.samefile(right)):
                        raise ValueError('source_state_spool_alias')


def _queue(state, identity, key, kind, attributes, stamp):
    event_id = _hash(identity + ':' + key)[:32]
    if attributes.get('scenario_id_ref'):
        trace_key = 'scenario:' + attributes['scenario_id_ref']
    elif attributes.get('decision_slot') is not None:
        trace_key = 'slot:' + str(attributes['decision_slot'])
    elif attributes.get('input_id_ref'):
        trace_key = 'input:' + attributes['input_id_ref']
    else:
        trace_key = 'source:' + key
    attrs = dict(attributes, source_identity=identity, export_observed_at=time.time(),
                 source_event_time=stamp, deployment_scope='UNKNOWN_NOT_ATTESTED',
                 evidence_scope='OBSERVED_SOURCE_ONLY_NOT_FILL_OR_DELIVERY_INFERENCE')
    value = dict(event_type='btc.research.' + kind, service='prism-btc-research-export',
                 event_id=event_id, market='CRYPTO', ticker='BTCUSDT', attributes=attrs,
                 event_time=datetime.fromtimestamp(stamp, timezone.utc),
                 trace_id=_hash(identity + ':' + trace_key)[:32], span_id=event_id[:16])
    if len(_json(attrs).encode()) > 56000:
        value['attributes'] = dict(source_identity=identity, gap='SUMMARY_SIZE_LIMIT',
                                    retained_summary_sha256=_hash(_json(attrs)))
    built = build_event(**value)
    built['attributes']['exporter_git_revision'] = built.pop('git_sha', None)
    built['git_sha'] = built['attributes'].get('source_git_revision')
    built['policy_version'] = built['attributes'].get('source_policy_hash')
    state.execute('INSERT OR IGNORE INTO outbox VALUES(?,?)', (event_id, _json(built)))


def _manifest(source, manifest_id):
    if not manifest_id:
        return {'policy_evidence': 'UNKNOWN'}
    row = source.execute('SELECT body FROM llm_scenario_audit_manifests WHERE manifest_id=?', (manifest_id,)).fetchone()
    if not row:
        return {'policy_evidence': 'MISSING_MANIFEST'}
    body = json.loads(row[0])
    result = {'manifest_ref': _hash(str(manifest_id)), 'manifest_sha256': _hash(row[0])}
    revision = body.get('git_revision')
    if isinstance(revision, str) and re.fullmatch('[a-f0-9]{40}', revision):
        result['source_git_revision'] = revision
    for key in ('policy_hash', 'execution_hash'):
        if isinstance(body.get(key), str) and re.fullmatch('[a-f0-9]{64}', body[key]):
            result['source_' + key] = body[key]
    result.update(_facts(body))
    return result


def _notice(state, identity, row, now):
    event = row['event_id']
    status = row['status']
    previous = state.execute('SELECT status FROM notices WHERE id=?', (event,)).fetchone()
    if previous and previous[0] == status:
        return
    attrs = {'notice_ref': _hash(event), 'body_sha256': _hash(row['body']),
             'notice_status': status if status in {'QUEUED', 'SENDING', 'SENT', 'UNKNOWN'} else 'UNRECOGNIZED',
             'receipt_observed_at': now, 'actual_delivery_time': 'UNKNOWN',
             'delivery_evidence': 'SOURCE_STATUS_ONLY'}
    if row['message_id'] is not None:
        attrs['receipt_ref'] = _hash(str(row['message_id']))
    _queue(state, identity, 'notice:' + event + ':' + str(status), 'notice', attrs, now)
    state.execute('INSERT OR REPLACE INTO notices VALUES(?,?,?)', (event, status, now))


def _references(*values):
    refs = {}
    for key in ('event_id', 'scenario_id', 'intent_id', 'run_id', 'input_id', 'action_id'):
        found = {v[key] for v in values if isinstance(v, dict) and isinstance(v.get(key), str) and v[key]}
        if len(found) == 1:
            refs[key + '_ref'] = _hash(next(iter(found)))
        elif len(found) > 1:
            refs[key + '_ref_status'] = 'CONFLICT'
    return refs


def _model_metadata(body):
    result = {}
    model = body.get('model')
    if isinstance(model, str) and re.fullmatch(r'gpt-[a-z0-9.-]{1,40}', model):
        result['model'] = model
    if body.get('effort') in ('low', 'medium', 'high', 'xhigh', 'max'):
        result['effort'] = body['effort']
    if type(body.get('fast')) is bool:
        result['fast'] = body['fast']
    for source, target in (('request_hash', 'source_request_hash'), ('sha256', 'source_raw_sha256')):
        if isinstance(body.get(source), str) and re.fullmatch('[a-f0-9]{64}', body[source]):
            result[target] = body[source]
    for key in ('system_prompt', 'user_prompt', 'raw'):
        if isinstance(body.get(key), str):
            result[key + '_sha256'] = _hash(body[key])
            if key == 'user_prompt':
                result['input_utf8_bytes'] = len(body[key].encode())
    if isinstance(body.get('response_schema'), dict):
        result['response_schema_sha256'] = _hash(_json(body['response_schema']))
    return result


def _capture(source, state, identity, stream, row):
    now = time.time()
    if stream == 'llm_scenario_outbox':
        _notice(state, identity, row, now)
        return
    raw = row['body']
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError('source_body_not_object')
    kind = row['kind']
    attrs = {'source_stream': stream, 'source_rowid': row['source_rowid'],
             'source_kind': kind if re.fullmatch(r'[A-Za-z_]{1,80}', kind) else 'UNKNOWN',
             'body_sha256': _hash(raw), 'facts': _facts(body), 'policy_evidence': 'UNKNOWN'}
    attrs['facts_scope'] = 'ALLOWLISTED_SUBSET_RAW_SOURCE_RETAINED'
    attrs['granular_fill_coverage'] = 'UNKNOWN_NOT_EXTRACTED'
    stamp = row['observed_at'] if 'observed_at' in row.keys() else row['captured_at']
    slot = row['slot'] if 'slot' in row.keys() else row['decision_slot'] if 'decision_slot' in row.keys() else None
    if slot is not None:
        attrs['decision_slot'] = slot
    if 'manifest_id' in row.keys():
        attrs.update(_manifest(source, row['manifest_id']))
    if kind == 'accounting_observation':
        ledger = body.get('result', {})
        settlement = ledger.get('settlement') if isinstance(ledger, dict) else None
        confirmed = isinstance(ledger, dict) and ledger.get('status') == 'confirmed' and 'execution_ids' in ledger
        if isinstance(ledger, dict) and ledger.get('status') == 'available' and isinstance(settlement, dict):
            confirmed = all(settlement.get(k) is True for k in ('flat_confirmed', 'orders_terminal', 'executions_complete'))
            ids = settlement.get('execution_ids')
        else:
            ids = ledger.get('execution_ids') if isinstance(ledger, dict) else None
        if confirmed and isinstance(ids, list) and all(isinstance(i, str) and i for i in ids):
            attrs['confirmed_ledger_execution_refs'] = [_hash(i) for i in ids[:200]]
            attrs['ledger_execution_ref_count'] = len(ids)
            attrs['ledger_execution_refs_complete'] = len(ids) <= 200
        else:
            attrs['ledger_execution_evidence'] = 'UNKNOWN_OR_UNCONFIRMED'
    refs_sources = [dict(row), body]
    attrs.update(_references(*refs_sources))
    if stream == 'llm_scenario_recovery_events' and kind in {'MODEL_REQUEST', 'MODEL_RAW'}:
        state.execute('INSERT OR REPLACE INTO model_refs VALUES(?,?,?)', (slot, kind, _hash(raw)))
        metadata = dict(_model_metadata(body), source_event_time=stamp)
        state.execute('INSERT OR REPLACE INTO model_metadata VALUES(?,?,?)', (slot, kind, _json(metadata)))
        attrs['model_metadata'] = metadata
        attrs['raw_manifest_only'] = True
    if stream == 'llm_scenario_recovery_events' and kind == 'OUTCOME' and slot is not None:
        decision = source.execute('SELECT snapshot,context,proposal,outcome FROM llm_scenario_decisions WHERE slot=?', (slot,)).fetchone()
        if decision:
            for key in decision.keys():
                value = decision[key]
                if value:
                    attrs[key + '_sha256'] = _hash(value)
                    decoded = json.loads(value)
                    attrs[key + '_facts'] = _facts(decoded)
                    if key in ('context', 'proposal'):
                        refs_sources.append(decoded)
            # Recompute conflicts across immutable body and exact-slot context/proposal.
            for key in list(attrs):
                if key.endswith('_ref') or key.endswith('_ref_status'):
                    if key.removesuffix('_status').removesuffix('_ref') in {'event_id', 'scenario_id', 'intent_id', 'run_id', 'input_id', 'action_id'}:
                        del attrs[key]
            attrs.update(_references(*refs_sources))
            attrs['proposal_semantics'] = 'PROPOSED_NOT_INSTALLED_PROTECTION'
            attrs['context_semantics'] = 'DECISION_INPUT_OBSERVATION_NOT_LATER_EXECUTION'
            attrs['model_evidence_refs'] = dict(state.execute('SELECT kind,ref FROM model_refs WHERE slot=?', (slot,)).fetchall())
            metadata = {kind: json.loads(text) for kind, text in state.execute('SELECT kind,body FROM model_metadata WHERE slot=?', (slot,))}
            attrs['model_metadata'] = metadata
            if 'MODEL_REQUEST' in metadata and 'MODEL_RAW' in metadata:
                duration = metadata['MODEL_RAW']['source_event_time'] - metadata['MODEL_REQUEST']['source_event_time']
                if duration >= 0:
                    attrs['request_to_raw_observed_seconds'] = duration
                    attrs['latency_basis'] = 'JOURNAL_TIMESTAMPS_NOT_PROVIDER_LATENCY'
            state.execute('DELETE FROM model_refs WHERE slot=?', (slot,))
            state.execute('DELETE FROM model_metadata WHERE slot=?', (slot,))
        else:
            attrs['decision_evidence'] = 'MISSING_EXACT_SLOT'
    _queue(state, identity, stream + ':' + str(row['source_rowid']), 'source', attrs, stamp)


def _append(event, spool_path):
    # Leading newline seals any old short-write prefix without truncating shared data.
    # Persisted, already-sanitized event keeps every retry byte-identical.
    raw = ('\n' + _json(event) + '\n').encode()
    if len(raw) > 65536:
        raise ValueError('event_bound')
    path = Path(spool_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_RDWR, 0o600)
    try:
        written = os.write(fd, raw)
        if written != len(raw):
            raise OSError('partial_spool_write')
        os.fsync(fd)
        end = os.lseek(fd, 0, os.SEEK_END)
        if os.pread(fd, len(raw), end-len(raw)) != raw:
            raise OSError('concurrent_append_verification_gap')
    finally:
        os.close(fd)


def export_once(db_path, state_path, spool_path, limit=100):
    """Fail-open bounded append export. First call records cutover, not history."""
    source = state = None
    counts = {'status': 'UNAVAILABLE', 'captured': 0, 'emitted': 0, 'pending': 0,
              'source_bytes': 0, 'backlog': False, 'source_errors': []}
    try:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid_limit')
        db_path, state_path, spool_path = Path(db_path), Path(state_path), Path(spool_path)
        _guard_paths(db_path, state_path, spool_path)
        identity = _identity(db_path)
        source = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.05)
        source.row_factory = sqlite3.Row
        source.execute('PRAGMA query_only=ON')
        source.execute('BEGIN')
        tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(state_path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(state_path, 0o600)
        state = sqlite3.connect(state_path, timeout=.05)
        state.executescript('CREATE TABLE IF NOT EXISTS meta(key PRIMARY KEY,value); CREATE TABLE IF NOT EXISTS cursors(stream PRIMARY KEY,seq); CREATE TABLE IF NOT EXISTS anchors(stream PRIMARY KEY,ref); CREATE TABLE IF NOT EXISTS outbox(id PRIMARY KEY,body); CREATE TABLE IF NOT EXISTS notices(id PRIMARY KEY,status,checked); CREATE TABLE IF NOT EXISTS model_refs(slot,kind,ref,PRIMARY KEY(slot,kind)); CREATE TABLE IF NOT EXISTS model_metadata(slot,kind,body,PRIMARY KEY(slot,kind));')
        state.execute('BEGIN IMMEDIATE')
        previous = state.execute("SELECT value FROM meta WHERE key='source_identity'").fetchone()
        if previous and previous[0] != identity:
            raise ValueError('source_identity_changed')
        if not previous:
            state.execute("INSERT INTO meta VALUES('source_identity',?)", (identity,))
        for stream in STREAMS:
            if stream not in tables:
                continue
            cursor = state.execute('SELECT seq FROM cursors WHERE stream=?', (stream,)).fetchone()
            maximum = source.execute(f'SELECT MAX(rowid) FROM {stream}').fetchone()[0] or 0
            if cursor is None:
                state.execute('INSERT INTO cursors VALUES(?,?)', (stream, maximum))
                anchor = source.execute(f'SELECT body FROM {stream} WHERE rowid=?', (maximum,)).fetchone()
                state.execute('INSERT INTO anchors VALUES(?,?)', (stream, _hash(anchor[0]) if anchor else None))
                _queue(state, identity, 'coverage:' + stream, 'coverage',
                       {'stream': stream, 'coverage_start_after_rowid': maximum,
                        'historical_backfill': False}, time.time())
                continue
            if maximum < cursor[0]:
                raise ValueError('source_rowid_regressed')
            anchor = source.execute(f'SELECT body FROM {stream} WHERE rowid=?', (cursor[0],)).fetchone()
            expected = state.execute('SELECT ref FROM anchors WHERE stream=?', (stream,)).fetchone()
            if not expected or expected[0] != (_hash(anchor[0]) if anchor else None):
                raise ValueError('source_anchor_changed')
            if state.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] >= 1000:
                break
            for row in source.execute(f'SELECT rowid AS source_rowid,* FROM {stream} WHERE rowid>? ORDER BY rowid LIMIT ?', (cursor[0], max(0, limit-counts['captured']))):
                if state.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] >= 1000:
                    counts['backlog'] = True
                    break
                size = len(row['body'].encode())
                if counts['source_bytes'] + size > 4 * 1024 * 1024:
                    counts['backlog'] = True
                    counts['source_errors'].append({'stream': stream, 'error_type': 'SOURCE_BYTE_BUDGET'})
                    break
                state.execute('SAVEPOINT source_row')
                try:
                    _capture(source, state, identity, stream, row)
                    state.execute('UPDATE cursors SET seq=? WHERE stream=?', (row['source_rowid'], stream))
                    state.execute('UPDATE anchors SET ref=? WHERE stream=?', (_hash(row['body']), stream))
                    state.execute('RELEASE source_row')
                    counts['captured'] += 1
                    counts['source_bytes'] += size
                except Exception as exc:  # Isolate malformed source, not source-identity failure.
                    state.execute('ROLLBACK TO source_row')
                    state.execute('RELEASE source_row')
                    counts['source_errors'].append({'stream': stream, 'rowid': row['source_rowid'],
                                                    'error_type': type(exc).__name__})
                    counts['backlog'] = True
                    break
            current = state.execute('SELECT seq FROM cursors WHERE stream=?', (stream,)).fetchone()[0]
            counts['backlog'] |= current < maximum
        if 'llm_scenario_outbox' in tables:
            watches = state.execute("SELECT id FROM notices WHERE status IN ('QUEUED','SENDING') ORDER BY checked LIMIT ?", (limit,)).fetchall()
            for (event_id,) in watches:
                if state.execute('SELECT COUNT(*) FROM outbox').fetchone()[0] >= 1000:
                    counts['backlog'] = True
                    break
                row = source.execute('SELECT * FROM llm_scenario_outbox WHERE event_id=?', (event_id,)).fetchone()
                if row:
                    _notice(state, identity, row, time.time())
                    state.execute('UPDATE notices SET checked=? WHERE id=?', (time.time(), event_id))
        source.rollback()
        if _identity(db_path) != identity:
            raise ValueError('source_identity_changed')
        state.commit()
        for event_id, body in state.execute('SELECT id,body FROM outbox ORDER BY rowid LIMIT ?', (limit,)).fetchall():
            event = json.loads(body)
            _append(event, spool_path)
            state.execute('DELETE FROM outbox WHERE id=?', (event_id,))
            state.commit()
            counts['emitted'] += 1
        counts['pending'] = state.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]
        counts['status'] = 'DEGRADED' if counts['source_errors'] else 'OK'
    except Exception as exc:  # fail-open boundary; never expose raw source/exception text
        counts['error_type'] = type(exc).__name__
        if state:
            state.rollback()
    finally:
        if source:
            source.close()
        if state:
            state.close()
    return counts
