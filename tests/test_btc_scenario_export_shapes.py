"""Production-shaped source evidence without trading imports or network calls."""
import hashlib
import json
import sqlite3

import pytest

from observability import btc_scenario_export as export


SID = "private-scenario-identifier-429"
INTENT = "private-intent-identifier-517"
INPUT = "private-input-identifier-913"
ACTION = "private-action-identifier-621"
SECRET = "CREDENTIAL_NEVER_EXPORT_942"
PROMPT = "PRIVATE_PROMPT_NEVER_EXPORT_793"
RAW = '{"action":"WAIT","rationale":"PRIVATE_RAW_RESPONSE_125"}'
STAMP = 1791690000.0
SLOT = 5972300


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def hashed(value):
    return hashlib.sha256(value.encode()).hexdigest()


def values(value):
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in values(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in values(item)]
    return [value]


@pytest.fixture
def production_lane(tmp_path):
    db, state, spool = (tmp_path / name for name in ("source.sqlite", "state.sqlite", "events.jsonl"))
    with sqlite3.connect(db) as source:
        source.executescript("""
        CREATE TABLE llm_scenario_audit_manifests(manifest_id TEXT PRIMARY KEY,body TEXT);
        CREATE TABLE llm_scenario_audit_events(event_id TEXT PRIMARY KEY,run_id TEXT,kind TEXT,
            observed_at REAL,scenario_id TEXT,intent_id TEXT,decision_slot INTEGER,manifest_id TEXT,body TEXT);
        CREATE TABLE llm_scenario_recovery_events(event_id TEXT PRIMARY KEY,slot INTEGER,scenario_id TEXT,
            kind TEXT NOT NULL,observed_at REAL NOT NULL,body TEXT NOT NULL);
        CREATE TABLE llm_scenario_broker_evidence(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,
            captured_at REAL NOT NULL,body TEXT NOT NULL);
        CREATE TABLE llm_scenario_outbox(event_id TEXT PRIMARY KEY,kind TEXT NOT NULL,body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'QUEUED',message_id INTEGER);
        CREATE TABLE llm_scenario_decisions(slot INTEGER PRIMARY KEY,snapshot TEXT,context TEXT,proposal TEXT,outcome TEXT);
        """)
        source.execute("INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?,?)",
                       (1, "historical_before_cutover", STAMP - 300, encoded({"price": 77777})))
        source.execute("INSERT INTO llm_scenario_audit_manifests VALUES(?,?)",
                       ("private-manifest-id", encoded(dict(git_revision="a" * 40, policy_hash="b" * 64,
                            execution_hash="c" * 64, loaded_code_status="VERIFIED"))))
    result = export.export_once(db, state, spool)
    assert result["captured"] == 0
    return db, state, spool


def populate(lane):
    account = dict(equity=9518.82, mark_price=83051.2, protection_confirmed=False,
        captured_at=STAMP, api_key=SECRET,
        position=dict(symbol="BTCUSDT", side="Buy", size="0.043", avgPrice="83023.90",
                      stopLoss="82940.00", leverage="10", positionIdx=0))
    plan = dict(action="ADJUST", side="LONG", scenario_id=SID, action_id=ACTION,
        input_id=INPUT, hard_stop=82940.0, rationale=PROMPT,
        take_profits=[dict(price=83442.7, fraction=0.6), dict(price=83592.7, fraction=0.4)],
        partial_stops=[dict(price=82970.3, fraction=0.25)],
        risk=dict(budget=47.6, total_risk=23.07))
    context = dict(scenario_id=SID, input_id=INPUT, initial_equity=9518.82,
        scenario_risk_fraction=0.005, positions=[dict(side="LONG", quantity=0.043, price=83023.9)],
        current_plan=plan, authorization=SECRET)
    snapshot = dict(timeframes={"30m": {"confirmed": dict(ma10=83012.5, ma35=82921.7)},
                                 "1h": {"forming": dict(close=83051.2, progress=0.75)}})
    outcome = dict(status="wait", scenario_id=SID, input_id=INPUT)
    request = dict(model="gpt-6-luna", effort="high", fast=True, system_prompt=PROMPT,
        user_prompt=PROMPT + SECRET, request_hash="d" * 64, response_schema={"type": "object"})
    raw = dict(model="gpt-6-luna", effort="high", fast=True, raw=RAW, sha256=hashed(RAW))
    revision = dict(scenario_id=SID, intent_id=INTENT, input_id=INPUT,
        old_stop=82940.0, new_stop=82910.0, quantity="0.043", captured_at=STAMP,
        risk_before=23.07, risk_after=24.36, stop_revision=dict(reason=PROMPT))
    with sqlite3.connect(lane[0]) as source:
        source.execute("INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?,?)", (2, "account", STAMP, encoded(account)))
        source.execute("INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?,?)", (3, "stop_revision_attempt", STAMP, encoded(revision)))
        source.execute("INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)",
            (SLOT, encoded(snapshot), encoded(context), encoded(plan), encoded(outcome)))
        # A neighboring slot must never contaminate the exact-slot OUTCOME join.
        source.execute("INSERT INTO llm_scenario_decisions VALUES(?,?,?,?,?)",
            (SLOT + 1, "{}", encoded(dict(initial_equity=999999.0)), encoded(dict(action="EXIT")), "{}"))
        for index, (kind, body) in enumerate((("MODEL_REQUEST", request), ("MODEL_RAW", raw), ("OUTCOME", outcome))):
            source.execute("INSERT INTO llm_scenario_recovery_events VALUES(?,?,?,?,?,?)",
                ("private-recovery-event-" + str(index), SLOT, SID, kind, STAMP, encoded(body)))
        for index, status in enumerate(("confirmed", "ACK")):
            source.execute("INSERT INTO llm_scenario_audit_events VALUES(?,?,?,?,?,?,?,?,?)",
                ("private-audit-event-" + str(index), "private-run-id", "accounting_observation", STAMP,
                 SID, INTENT, SLOT, "private-manifest-id",
                 encoded(dict(result=dict(status=status, net_pnl=3.39,
                     execution_ids=["private-execution-" + status])))))
        source.execute("INSERT INTO llm_scenario_outbox VALUES(?,?,?,?,?)",
            ("private-notice-id", "FILL", PROMPT + SECRET, "QUEUED", None))
    return dict(account=account, plan=plan, context=context, snapshot=snapshot, outcome=outcome,
                request=request, raw=raw, revision=revision)


def capture(lane, monkeypatch):
    # Fail the test if the exporter introduces any new network behavior.
    import socket
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: pytest.fail("network forbidden"))
    before = lane[0].read_bytes()
    result = export.export_once(*lane)
    assert result["status"] != "UNAVAILABLE", result
    assert lane[0].read_bytes() == before
    return [json.loads(line) for line in lane[2].read_text().splitlines() if line.strip()]


def test_production_shapes_preserve_safe_scalars_and_exact_slot_without_source_writes(production_lane, monkeypatch):
    source = populate(production_lane)
    events = capture(production_lane, monkeypatch)
    attrs = [event["attributes"] for event in events if event["event_type"] == "btc.research.source"]
    assert {row["source_stream"] for row in attrs} == set(export.STREAMS) - {"llm_scenario_outbox"}
    assert "historical_before_cutover" not in {row["source_kind"] for row in attrs}
    account, = [row for row in attrs if row["source_kind"] == "account"]
    leaves = values(account["facts"])
    for value in (9518.82, 83051.2, "0.043", "83023.90", "82940.00"):
        assert value in leaves
    assert account["body_sha256"] == hashed(encoded(source["account"]))
    outcome, = [row for row in attrs if row["source_kind"] == "OUTCOME"]
    assert outcome["decision_slot"] == SLOT
    for name in ("snapshot", "context", "proposal", "outcome"):
        original = source["plan" if name == "proposal" else name]
        assert outcome[name + "_sha256"] == hashed(encoded(original))
    for value in (83442.7, 83592.7, 82970.3, 0.6, 0.4, 0.25):
        assert value in values(outcome["proposal_facts"])
    assert 999999.0 not in values(outcome)
    assert outcome["proposal_facts"]["action"] == "ADJUST"
    assert outcome["scenario_id_ref"] == hashed(SID)
    assert outcome["input_id_ref"] == hashed(INPUT)
    revision, = [row for row in attrs if row["source_kind"] == "stop_revision_attempt"]
    assert revision["scenario_id_ref"] == outcome["scenario_id_ref"]
    assert revision["intent_id_ref"] == hashed(INTENT)
    assert revision["input_id_ref"] == outcome["input_id_ref"]
    for value in (82940.0, 82910.0, "0.043"):
        assert value in values(revision["facts"])


def test_model_metadata_retains_hashes_and_effort_but_no_wire_or_raw_identifiers(production_lane, monkeypatch):
    source = populate(production_lane)
    events = capture(production_lane, monkeypatch)
    attrs = [event["attributes"] for event in events if event["event_type"] == "btc.research.source"]
    request, = [row for row in attrs if row["source_kind"] == "MODEL_REQUEST"]
    raw, = [row for row in attrs if row["source_kind"] == "MODEL_RAW"]
    for row in (request, raw):
        assert "gpt-6-luna" in values(row["model_metadata"])
        assert "high" in values(row["model_metadata"])
    assert hashed(RAW) in values(raw["model_metadata"])
    assert hashed(PROMPT) in values(request["model_metadata"])
    outcome, = [row for row in attrs if row["source_kind"] == "OUTCOME"]
    assert outcome["model_evidence_refs"] == {
        "MODEL_REQUEST": hashed(encoded(source["request"])), "MODEL_RAW": hashed(encoded(source["raw"]))}
    serialized = production_lane[2].read_text()
    for forbidden in (SID, INTENT, INPUT, ACTION, SECRET, PROMPT, "PRIVATE_RAW_RESPONSE_125",
                      "private-execution-confirmed", "private-execution-ACK", "private-notice-id",
                      "private-run-id", "private-manifest-id", "private-recovery-event-", "private-audit-event-"):
        assert forbidden not in serialized


def test_ack_is_not_confirmed_fill_and_notice_is_only_observed_status(production_lane, monkeypatch):
    populate(production_lane)
    events = capture(production_lane, monkeypatch)
    rows = [event["attributes"] for event in events
            if event["attributes"].get("source_kind") == "accounting_observation"]
    confirmed = [row for row in rows if "confirmed_ledger_execution_refs" in row]
    assert len(confirmed) == 1
    assert confirmed[0]["confirmed_ledger_execution_refs"] == [hashed("private-execution-confirmed")]
    ack, = [row for row in rows if row not in confirmed]
    assert ack["ledger_execution_evidence"] == "UNKNOWN_OR_UNCONFIRMED"
    assert "confirmed_ledger_execution_refs" not in ack
    with sqlite3.connect(production_lane[0]) as source:
        source.execute("UPDATE llm_scenario_outbox SET status='SENT',message_id=170001 WHERE event_id='private-notice-id'")
    events = capture(production_lane, monkeypatch)
    notice = [event["attributes"] for event in events if event["event_type"] == "btc.research.notice"][-1]
    assert notice["notice_status"] == "SENT"
    assert notice["actual_delivery_time"] == "UNKNOWN"
    assert notice["delivery_evidence"] == "SOURCE_STATUS_ONLY"
    assert notice["receipt_ref"] == hashed("170001")
