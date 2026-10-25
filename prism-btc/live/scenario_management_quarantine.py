"""Explicit administrative WAIT/EXIT authority, never exchange/settlement evidence."""
from copy import deepcopy
import hashlib
import json

PURPOSE = 'ADMIN_AUTHORIZATION_NOT_EXCHANGE_EVIDENCE'
KIND = 'management_quarantine_v1'
EXIT_KIND = 'management_quarantine_exit_v1'
ENDPOINT = 'https://api-demo.bybit.com'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')).encode()).hexdigest()


def _require(ok, reason):
    if not ok:
        raise ValueError('management_quarantine_' + reason)


def _events(conn, kind):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_scenario_broker_evidence'").fetchone():
        return []
    return [json.loads(r[0]) for r in conn.execute('SELECT body FROM llm_scenario_broker_evidence WHERE kind=? ORDER BY rowid', (kind,))]


def _append(conn, kind, body, at):
    conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                 (kind, at, json.dumps(body, sort_keys=True, allow_nan=False)))


def _latest(broker):
    events = _events(broker.conn, KIND)
    if not events:
        return None
    event = events[-1]
    _require(event.get('sha256') == digest(event.get('body')), 'approval_corrupt')
    return event


def _approval(broker):
    event = _latest(broker)
    if not event:
        return None
    body = event['body']
    if body.get('operation') == 'REVOKE':
        return None
    _require(body.get('operation') == 'AUTHORIZE' and body.get('version') == 1
             and body.get('purpose') == PURPOSE and body.get('allowed_actions') == ['WAIT', 'EXIT'], 'approval_shape')
    _require(body['issued_at'] < body['expires_at'] <= body['issued_at'] + 86400, 'approval_window')
    if not body['issued_at'] <= broker.clock() < body['expires_at']:
        return None
    active = broker._active()
    if not active or active['scenario_id'] != body['scenario_id']:
        return None
    return event


def _receipt(event):
    body = event['body']
    return dict(status='AUTHORIZED', approval_id=body['approval_id'], approval_sha256=event['sha256'],
        scenario_id=body['scenario_id'], contract_id=body['contract_id'], expires_at=body['expires_at'],
        allowed_actions=['WAIT', 'EXIT'], purpose=PURPOSE)


def _pending(conn):
    return [dict(id=i, scenario_id=s, payload=json.loads(p), status=t) for i,s,p,t in conn.execute(
        "SELECT id,scenario_id,payload,status FROM llm_scenario_intents WHERE status='PENDING' OR status IS NULL")]


def _pending_matches(conn, body, self_exit=None):
    expected = {r['id']: r for r in body['pending_intents']}
    rows = _pending(conn)
    if self_exit:
        records = [r for r in _events(conn, EXIT_KIND) if r.get('intent_id') == self_exit]
        _require(len(records) == 1, 'self_exit_receipt_missing')
        receipt = records[0]
        _require(receipt['approval_id'] == body['approval_id'] and receipt['approval_sha256'] == digest(body), 'self_exit_approval_changed')
        own = [r for r in rows if r['id'] == self_exit]
        _require(len(own) == 1 and own[0]['status'] == 'PENDING' and own[0]['payload']['action'] == 'EXIT'
                 and digest(own[0]['payload']) == receipt['payload_sha256'], 'self_exit_changed')
        rows = [r for r in rows if r['id'] != self_exit]
    _require(len(rows) == len(expected) and {r['id'] for r in rows} == set(expected), 'pending_set_changed')
    for row in rows:
        spec = expected[row['id']]
        _require(row['status'] == 'PENDING' and row['scenario_id'] == body['scenario_id']
                 and row['payload'].get('action') == spec['action']
                 and row['payload'].get('revision') == spec['revision']
                 and digest(row['payload']) == spec['payload_sha256'], 'pending_payload_changed')


def _inspect(broker, event, observed, active, children, self_exit=None):
    from live.scenario_control import read_control
    from live.scenario_entry_contract import valid_record
    from live.scenario_execution import decimal
    body = event['body']
    _require(broker.environment == 'demo' and broker.session.endpoint == ENDPOINT
             and body['endpoint'] == ENDPOINT and body['main_uid'] == broker.expected_main_uid, 'identity_changed')
    control = read_control(broker.conn)
    _require(control and control['main_uid'] == body['main_uid'], 'control_changed')
    _require(active and active['scenario_id'] == body['scenario_id'] and active['side'] == body['side'], 'scenario_changed')
    contract = active.get('entry_contract')
    registered = broker.conn.execute('SELECT body FROM llm_scenario_entry_contracts WHERE scenario_id=?', (body['scenario_id'],)).fetchone()
    _require(valid_record(contract, body['scenario_id']) and contract['contract_id'] == body['contract_id']
             and registered and json.loads(registered[0]) == contract, 'contract_changed')
    _pending_matches(broker.conn, body, self_exit)
    _require(not broker.conn.execute("SELECT 1 FROM llm_scenario_children WHERE (scenario_id IS NULL OR scenario_id!=?) AND (status IS NULL OR status!='TERMINAL') LIMIT 1",
                                    (body['scenario_id'],)).fetchone(), 'foreign_unresolved_child')
    _require(not broker.conn.execute("SELECT 1 FROM llm_scenario_intents WHERE (scenario_id IS NULL OR scenario_id!=?) AND (status IS NULL OR status!='TERMINAL') LIMIT 1",
                                    (body['scenario_id'],)).fetchone(), 'foreign_unresolved_intent')
    _require(not observed['legacy_fenced'] and 0 <= broker.clock() - observed['captured_at'] <= 10, 'observation_untrusted')
    size = decimal(observed['position']['size'])
    _require(0 < size <= decimal(body['maximum_quantity'])
             and observed['position']['side'] == ('Buy' if body['side'] == 'LONG' else 'Sell')
             and broker._verify_protection(observed, active['hard_stop'], active['side']), 'position_or_protection_changed')
    target = [c for c in children if c['link_id'] == body['unknown_link_id']]
    _require(len(target) == 1, 'unknown_child_missing')
    child = target[0]
    identity = {k: child[k] for k in ('intent_id', 'local_id', 'created_at')}
    reduce_ids = [r['id'] for r in body['pending_intents'] if r['action'] == 'REDUCE']
    _require(child['status'] == 'UNKNOWN' and child['order_id'] is None and child['evidence'] is None
             and child['scenario_id'] == body['scenario_id'] and child['kind'] == 'partial_sl'
             and identity == body['unknown_child_identity'] and reduce_ids == [child['intent_id']]
             and child['local_id'].startswith('reduce:')
             and digest(child['request']) == body['unknown_request_sha256'], 'unknown_child_changed')
    _require(child['request'].get('reduceOnly') is True and child['request'].get('closeOnTrigger') is True
             and child['request'].get('orderType') == 'Market' and child['request'].get('triggerBy') == 'MarkPrice'
             and child['request'].get('symbol') == 'BTCUSDT' and child['request'].get('positionIdx') == 0
             and child['request'].get('side') == ('Sell' if body['side'] == 'LONG' else 'Buy'), 'unknown_request_unsafe')
    natives = []
    for c in children:
        if c['link_id'] == child['link_id']:
            continue
        _require(c['scenario_id'] == body['scenario_id'], 'foreign_child')
        if self_exit and c['intent_id'] == self_exit and c['kind'] == 'exit':
            continue  # Existing self-EXIT is query-only; never authority for another POST.
        _require(c['evidence'] and c['status'] in {'TERMINAL', 'LIVE'}, 'other_child_unknown')
        if c['kind'] == 'native_sl' and c['status'] == 'LIVE':
            natives.append(c)
        else:
            _require(c['status'] == 'TERMINAL', 'other_live_child')
    _require(len(natives) == 1, 'native_full_stop_unconfirmed')
    return _receipt(event)


def refresh_known(broker, self_exit=None):
    """Reuse same-instance exact terminal observations; live protection stays fresh."""
    from live.scenario_execution import encoded
    event = _approval(broker)
    if not event:
        return
    for c in broker.children(event['body']['scenario_id']):
        if c['link_id'] != event['body']['unknown_link_id']:
            if c['status'] == 'TERMINAL' and c['kind'] != 'native_sl':
                fingerprint = hashlib.sha256(encoded(dict(main_uid=broker.expected_main_uid,
                    endpoint=broker.session.endpoint, child=c)).encode()).hexdigest()
                if getattr(broker, '_quarantine_terminal_observations', {}).get(c['link_id']) == fingerprint:
                    continue
            broker._query_child(c)


def project(broker, observed, active, children, self_exit=None):
    event = _approval(broker)
    return _inspect(broker, event, observed, active, children, self_exit) if event else None


def projection_failure(broker, observed=None):
    """Resolved source needs explicit revocation, never implicit normal authority."""
    event = _approval(broker)
    if not event:
        return None
    body = event['body']
    rows = [c for c in broker.children(body['scenario_id']) if c['link_id'] == body['unknown_link_id']]
    if len(rows) != 1:
        return 'INVALID'
    child = rows[0]
    if (digest(child['request']) != body['unknown_request_sha256']
            or {k: child[k] for k in ('intent_id', 'local_id', 'created_at')} != body['unknown_child_identity']):
        return 'INVALID'
    if child['status'] == 'TERMINAL' and child['order_id'] and child['evidence']:
        exact = broker._query_child(child)
        if exact['status'] == 'TERMINAL' and exact['order_id'] == child['order_id'] and exact['evidence']:
            return 'RELEASE_REQUIRED'
    if child['status'] == 'UNKNOWN' and child['order_id'] is None and child['evidence'] is None:
        from live.scenario_execution import decimal
        if (observed and observed.get('exchange_flat') is True and observed.get('legacy_fenced') is False
                and decimal(observed['position']['size']) == 0
                and observed['position'].get('side') in {'', 'Buy' if body['side'] == 'LONG' else 'Sell'}
                and 0 <= broker.clock() - observed['captured_at'] <= 10):
            return 'FLAT_UNSETTLED'  # No authority and no settlement assertion.
        exits = [r for r in _events(broker.conn, EXIT_KIND) if r.get('approval_id') == body['approval_id']]
        if len(exits) == 1:
            try:
                _pending_matches(broker.conn, body, self_exit=exits[0]['intent_id'])
                return None  # Preserve the ordinary pending-EXIT gate, never a new model call.
            except (ValueError, KeyError, TypeError):
                return 'INVALID'
    return 'INVALID'


def active_receipt(broker, self_exit=None):
    refresh_known(broker, self_exit)
    event = _approval(broker)
    if not event:
        return None
    observed = broker.capture_account()
    return project(broker, observed, broker._active(), broker.children(event['body']['scenario_id']), self_exit)


def pending_allowed(broker, context, self_exit=None):
    receipt = context.get('management_quarantine')
    if not receipt:
        return False
    event = _approval(broker)
    _require(event and receipt == _receipt(event), 'authority_changed')
    _pending_matches(broker.conn, event['body'], self_exit)
    return True


def validate_action(payload, context):
    if context.get('management_quarantine'):
        _require(payload.get('action') in {'WAIT', 'EXIT'} and not payload.get('pending_renewal')
                 and not payload.get('cancel_entry_ids') and not payload.get('entries')
                 and not payload.get('take_profits') and not payload.get('partial_stops')
                 and payload.get('reduce_fraction') is None, 'action_forbidden')


def bind_exit(broker, payload, receipt):
    """Append inside the caller's atomic intent transaction; no commit/order."""
    _require(payload['action'] == 'EXIT', 'not_exit')
    validate_action(payload, {'management_quarantine': receipt})
    event = _approval(broker)
    _require(event and receipt == _receipt(event), 'authority_changed')
    _require(not any(r.get('approval_id') == receipt['approval_id'] for r in _events(broker.conn, EXIT_KIND)), 'exit_already_bound')
    _append(broker.conn, EXIT_KIND, dict(intent_id=payload['action_id'], payload_sha256=digest(payload),
        approval_id=receipt['approval_id'], approval_sha256=receipt['approval_sha256']), broker.clock())
    _pending_matches(broker.conn, event['body'], self_exit=payload['action_id'])


def register(broker, *, approval_id, reason, expires_at, scenario_id, contract_id,
             unknown_link_id, unknown_request_sha256, pending_intents, maximum_quantity):
    from live.shared_entry_coordinator import mutation_lock
    with mutation_lock(broker.conn):
        now = broker.clock()
        _require(isinstance(approval_id, str) and approval_id and isinstance(reason, str) and reason, 'approval_reason_missing')
        _require(now < expires_at <= now + 86400 and len(pending_intents) == 3
                 and {r['action'] for r in pending_intents} == {'OPEN', 'ADJUST', 'REDUCE'}, 'approval_scope')
        _require(not any(e['body']['approval_id'] == approval_id for e in _events(broker.conn, KIND)), 'approval_id_reused')
        active = broker._active()
        for child in broker.children(scenario_id):
            if child['link_id'] != unknown_link_id:
                broker._query_child(child)
        observed = broker.capture_account()
        unknown = [c for c in broker.children(scenario_id) if c['link_id'] == unknown_link_id]
        _require(len(unknown) == 1, 'unknown_child_missing')
        body = dict(version=1, operation='AUTHORIZE', approval_id=approval_id, reason=reason, purpose=PURPOSE,
            issued_at=now, expires_at=expires_at, main_uid=broker.expected_main_uid, endpoint=ENDPOINT,
            scenario_id=scenario_id, contract_id=contract_id, side=active['side'], maximum_quantity=maximum_quantity,
            unknown_link_id=unknown_link_id, unknown_request_sha256=unknown_request_sha256,
            unknown_child_identity={k: unknown[0][k] for k in ('intent_id', 'local_id', 'created_at')},
            pending_intents=deepcopy(pending_intents), allowed_actions=['WAIT', 'EXIT'])
        event = dict(body=body, sha256=digest(body))
        receipt = _inspect(broker, event, observed, active, broker.children(scenario_id))
        _append(broker.conn, KIND, event, now)
        broker.conn.commit()
        return receipt


def revoke(broker, *, approval_id, reason):
    from live.shared_entry_coordinator import mutation_lock
    with mutation_lock(broker.conn):
        broker._identity()
        event = _latest(broker)
        _require(event and event['body'].get('operation') == 'AUTHORIZE'
                 and event['body']['approval_id'] == approval_id and reason
                 and event['body']['main_uid'] == broker.expected_main_uid
                 and event['body']['endpoint'] == broker.session.endpoint == ENDPOINT, 'revocation_scope')
        body = dict(operation='REVOKE', approval_id=approval_id, reason=reason,
                    at=broker.clock(), approval_sha256=event['sha256'])
        _append(broker.conn, KIND, dict(body=body, sha256=digest(body)), broker.clock())
        broker.conn.commit()


def bound_exit(broker, ident):
    rows = [r for r in _events(broker.conn, EXIT_KIND) if r.get('intent_id') == ident]
    _require(len(rows) <= 1, 'duplicate_exit_receipt')
    return rows[0] if rows else None


def execute_exit(broker, payload, ident):
    """One fixed reduce-only order, retaining the unresolved conditional request."""
    from live.scenario_execution import decimal, text_number
    receipt = bound_exit(broker, ident)
    _require(receipt and receipt['payload_sha256'] == digest(payload) and payload['action'] == 'EXIT', 'exit_binding_changed')
    existing = [c for c in broker.children(payload['scenario_id']) if c['intent_id'] == ident]
    if existing:
        _require(len(existing) == 1 and existing[0]['kind'] == 'exit' and existing[0]['local_id'] == 'full', 'exit_children_changed')
        return broker._query_child(existing[0])  # Even expiry/revocation permits exact reads, never another POST.
    authority = active_receipt(broker, self_exit=ident)
    _require(authority and authority['approval_id'] == receipt['approval_id']
             and authority['approval_sha256'] == receipt['approval_sha256'], 'exit_authority_changed')
    validate_action(payload, {'management_quarantine': authority})
    observed = broker.capture_account()
    active = broker._active()
    _require(project(broker, observed, active, broker.children(payload['scenario_id']), ident) == authority, 'exit_fresh_authority_changed')
    size = decimal(observed['position']['size'])
    _require(size <= broker._instrument()['market_maximum'], 'exit_quantity_requires_split')
    return broker._submit(payload, ident, 'exit', 'full', dict(
        side='Sell' if active['side'] == 'LONG' else 'Buy', orderType='Market',
        qty=text_number(size), reduceOnly=True, closeOnTrigger=True), single_attempt=True)
