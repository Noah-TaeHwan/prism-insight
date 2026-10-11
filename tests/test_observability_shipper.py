from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from observability.events import emit_event
from observability.shipper import (
    build_otlp_payload,
    load_checkpoint,
    run_once,
)


class _CollectorHandler(BaseHTTPRequestHandler):
    payloads: ClassVar[list] = []
    status = 200
    body = b"{}"
    content_type = "application/json"

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        self.__class__.payloads.append(
            (
                self.path,
                json.loads(self.rfile.read(length)),
                self.headers.get("Authorization"),
            )
        )
        self.send_response(self.__class__.status)
        self.send_header("Content-Type", self.__class__.content_type)
        self.end_headers()
        self.wfile.write(self.__class__.body)

    def log_message(self, _format, *_args):
        return


@pytest.fixture
def collector():
    _CollectorHandler.payloads = []
    _CollectorHandler.status = 200
    _CollectorHandler.body = b"{}"
    _CollectorHandler.content_type = "application/json"
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CollectorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/logs"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_otlp_payload_exposes_queryable_prism_attributes(tmp_path):
    event = emit_event(
        "trigger.performance_feedback",
        service="prism-us-trading",
        market="US",
        ticker="AAPL",
        attributes={
            "trigger_type": "Gap Up Momentum Top",
            "mode": "shadow",
            "applied_adjust": 0,
        },
        spool_path=tmp_path / "events.jsonl",
    )
    payload = build_otlp_payload([event])
    record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    attributes = {item["key"]: next(iter(item["value"].values())) for item in record["attributes"]}

    assert record["traceId"] == event["trace_id"]
    assert attributes["event.name"] == "trigger.performance_feedback"
    assert attributes["prism.market"] == "US"
    assert attributes["prism.trigger_type"] == "Gap Up Momentum Top"
    assert attributes["prism.applied_adjust"] == "0"


def test_mixed_services_keep_their_own_resource_identity(tmp_path):
    events = [emit_event("btc.exit.snapshot", service=service,
                         spool_path=tmp_path / "events.jsonl")
              for service in ("prism-btc", "prism-us-trading", "prism-btc")]
    payload = build_otlp_payload(events)
    assert len(payload["resourceLogs"]) == 2
    for group in payload["resourceLogs"]:
        resource = {a["key"]: next(iter(a["value"].values()))
                    for a in group["resource"]["attributes"]}
        for record in group["scopeLogs"][0]["logRecords"]:
            assert json.loads(record["body"]["stringValue"])["service"] == resource["service.name"]


def test_success_advances_checkpoint_and_does_not_resend(tmp_path, collector):
    spool = tmp_path / "events.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    emit_event("pipeline.run", service="prism-test", spool_path=spool)
    emit_event("pipeline.done", service="prism-test", spool_path=spool)

    assert run_once(
        spool_path=spool,
        checkpoint_path=checkpoint,
        endpoint=collector,
        batch_size=100,
        timeout=2,
        auth_token="collector-secret",
    ) == 2
    saved = load_checkpoint(checkpoint)
    assert saved is not None
    assert saved.offset == spool.stat().st_size
    assert len(_CollectorHandler.payloads) == 1
    assert _CollectorHandler.payloads[0][0] == "/v1/logs"
    assert _CollectorHandler.payloads[0][2] == "collector-secret"

    assert run_once(
        spool_path=spool,
        checkpoint_path=checkpoint,
        endpoint=collector,
        batch_size=100,
        timeout=2,
    ) == 0
    assert len(_CollectorHandler.payloads) == 1


def test_failed_delivery_does_not_advance_checkpoint(tmp_path, collector):
    spool = tmp_path / "events.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    emit_event("pipeline.run", service="prism-test", spool_path=spool)
    _CollectorHandler.status = 503

    with pytest.raises(RuntimeError, match="HTTP 503"):
        run_once(
            spool_path=spool,
            checkpoint_path=checkpoint,
            endpoint=collector,
            batch_size=100,
            timeout=2,
        )
    assert load_checkpoint(checkpoint) is None


def test_corrupt_line_is_skipped_without_blocking_later_events(tmp_path, collector):
    spool = tmp_path / "events.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    spool.write_text("not-json\n", encoding="utf-8")
    emit_event("pipeline.done", service="prism-test", spool_path=spool)

    assert run_once(
        spool_path=spool,
        checkpoint_path=checkpoint,
        endpoint=collector,
        batch_size=100,
        timeout=2,
    ) == 1
    assert load_checkpoint(checkpoint).offset == spool.stat().st_size


@pytest.mark.parametrize("body", [b'{}', b'{"partialSuccess":{}}',
    b'{"partialSuccess":{"rejectedLogRecords":"0","errorMessage":"warning"}}',
    b'{"partialSuccess":{"rejectedLogRecords":0}}',
    b'{"futureField":{"opaque":[1,2,3]}}',
    b'{"partialSuccess":{"rejectedLogRecords":"0","futureField":{"x":1}}}',
    b'{"futureField":{"rejectedLogRecords":1,"rejectedLogRecords":0},"futureField":true}',
    b'{"partialSuccess":{"rejectedLogRecords":0,"futureField":1,"futureField":2}}'])
def test_valid_success_response_advances(tmp_path, collector, body):
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    emit_event("pipeline.run", service="prism-test", spool_path=spool)
    _CollectorHandler.body = body
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 1
    assert load_checkpoint(checkpoint).offset == spool.stat().st_size


@pytest.mark.parametrize("body", [b'not-json-secret', b'[]', b'null',
    b'{"partialSuccess":[]}', b'{"partialSuccess":{"rejectedLogRecords":true}}',
    b'{"partialSuccess":{"rejectedLogRecords":-1}}',
    b'{"partialSuccess":{"rejectedLogRecords":"bad"}}',
    b'{"partialSuccess":{"rejectedLogRecords":0.5}}',
    b'{"partialSuccess":{"rejectedLogRecords":0,"errorMessage":{}}}',
    b'{"partialSuccess":{"rejectedLogRecords":1,"rejectedLogRecords":0}}',
    b'{"partialSuccess":{"rejectedLogRecords":1},"partialSuccess":{}}', b' '])
def test_malformed_body_freezes_checkpoint_and_hides_response(tmp_path, collector, body):
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    emit_event("pipeline.first", service="prism-test", spool_path=spool)
    run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    before = checkpoint.read_bytes()
    emit_event("pipeline.second", service="prism-test", spool_path=spool)
    _CollectorHandler.body = body
    with pytest.raises(RuntimeError, match="invalid OTLP response") as caught:
        run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    assert "secret" not in str(caught.value)
    assert checkpoint.read_bytes() == before


def test_partial_rejection_quarantines_ids_without_retrying_accepted_subset(tmp_path, collector, capsys):
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    events = [emit_event("pipeline.run", service="prism-test", spool_path=spool) for _ in range(2)]
    _CollectorHandler.body = b'{"partialSuccess":{"rejectedLogRecords":"1","errorMessage":"token-secret\\nunsafe"}}'
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 0
    receipt_path, = checkpoint.with_suffix(".quarantine").glob("*.json")
    raw = receipt_path.read_text()
    receipt = json.loads(raw)
    assert "token-secret" not in raw
    assert receipt["event_ids"] == [e["event_id"] for e in events]
    assert receipt["events"] == events
    assert receipt["rejected_log_records"] == 1
    assert receipt["delivery_status"] == "PARTIAL_UNKNOWN"
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    assert len(receipt["request_sha256"]) == 64
    assert load_checkpoint(checkpoint).offset == spool.stat().st_size
    assert "PARTIAL_UNKNOWN" in capsys.readouterr().err
    _CollectorHandler.body = b'{}'
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 0
    assert len(_CollectorHandler.payloads) == 1
    emit_event("pipeline.later", service="prism-test", spool_path=spool)
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 1


def test_quarantine_failure_keeps_checkpoint_and_holds_across_restart(tmp_path, collector, monkeypatch):
    import observability.shipper as shipper
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    emit_event("pipeline.run", service="prism-test", spool_path=spool)
    _CollectorHandler.body = b'{"partialSuccess":{"rejectedLogRecords":1}}'
    def failed(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(shipper, "_quarantine", failed)
    with pytest.raises(OSError):
        run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    assert load_checkpoint(checkpoint) is None
    attempt, = checkpoint.with_suffix(".quarantine").glob("*.attempt.json")
    assert json.loads(attempt.read_text())["status"] == "SENDING"
    assert attempt.stat().st_mode & 0o777 == 0o600
    emit_event("pipeline.appended", service="prism-test", spool_path=spool)
    with pytest.raises(RuntimeError, match="operator_review_required"):
        run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    # A new interpreter has no in-memory exception/state from the first send.
    subprocess.run([sys.executable, "-c", """
from pathlib import Path
import sys
from observability.shipper import run_once
try:
    run_once(spool_path=Path(sys.argv[1]),checkpoint_path=Path(sys.argv[2]),endpoint=sys.argv[3],batch_size=100,timeout=2)
except RuntimeError as error:
    assert 'operator_review_required' in str(error)
else:
    raise AssertionError('pending submission was retried or acknowledged')
""", str(spool), str(checkpoint), collector], check=True, capture_output=True, text=True)
    assert len(_CollectorHandler.payloads) == 1
    assert load_checkpoint(checkpoint) is None


def test_transient_failure_retry_preserves_original_batch_before_appended_events(tmp_path, collector):
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    first = emit_event("pipeline.first", service="prism-test", spool_path=spool)
    first_end = spool.stat().st_size
    _CollectorHandler.status = 503
    with pytest.raises(RuntimeError, match="HTTP 503"):
        run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    attempt, = checkpoint.with_suffix(".quarantine").glob("*.attempt.json")
    assert json.loads(attempt.read_text())["status"] == "RETRY_ALLOWED"
    emit_event("pipeline.later", service="prism-test", spool_path=spool)
    _CollectorHandler.status = 200
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 1
    assert load_checkpoint(checkpoint).offset == first_end
    assert not attempt.exists()
    for _, payload, _ in _CollectorHandler.payloads:
        records = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        assert [json.loads(record["body"]["stringValue"])["event_id"] for record in records] == [first["event_id"]]
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 1


def test_full_success_checkpoint_failure_resumes_without_resend(tmp_path, collector, monkeypatch):
    import observability.shipper as shipper
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    emit_event("pipeline.first", service="prism-test", spool_path=spool)
    original_save = shipper.save_checkpoint
    def failed(*args):
        raise OSError("checkpoint unavailable")
    monkeypatch.setattr(shipper, "save_checkpoint", failed)
    with pytest.raises(OSError):
        run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    attempt, = checkpoint.with_suffix(".quarantine").glob("*.attempt.json")
    assert json.loads(attempt.read_text())["status"] == "ACCEPTED"
    monkeypatch.setattr(shipper, "save_checkpoint", original_save)
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 1
    assert len(_CollectorHandler.payloads) == 1
    assert not attempt.exists()


def test_attempt_receipt_failure_never_submits(tmp_path, collector, monkeypatch):
    import observability.shipper as shipper
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    emit_event("pipeline.first", service="prism-test", spool_path=spool)
    def failed(*args):
        raise OSError("attempt unavailable")
    monkeypatch.setattr(shipper, "_attempt_receipt", failed)
    with pytest.raises(OSError):
        run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    assert _CollectorHandler.payloads == []
    assert load_checkpoint(checkpoint) is None


def test_oversized_response_quarantines_unknown_delivery_without_retry(tmp_path, collector, capsys):
    from observability.shipper import MAX_RESPONSE_BYTES
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    event = emit_event("pipeline.run", service="prism-test", spool_path=spool)
    _CollectorHandler.body = b'{"futureField":"' + b'x' * MAX_RESPONSE_BYTES + b'"}'
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 0
    receipt_path, = checkpoint.with_suffix(".quarantine").glob("*.json")
    receipt = json.loads(receipt_path.read_text())
    assert receipt["delivery_status"] == "DELIVERY_UNKNOWN"
    assert receipt["terminal_reason"] == "response_size_limit"
    assert receipt["rejected_log_records"] is None
    assert receipt["collector_error_sha256"] is None
    assert receipt["event_ids"] == [event["event_id"]]
    assert receipt["automatic_retry"] is False
    assert load_checkpoint(checkpoint).offset == spool.stat().st_size
    assert "delivery=DELIVERY_UNKNOWN" in capsys.readouterr().err
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 0
    assert len(_CollectorHandler.payloads) == 1


def test_checkpoint_crash_after_quarantine_does_not_resend_or_swallow_new_events(tmp_path, collector, monkeypatch):
    import observability.shipper as shipper
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    first = emit_event("pipeline.run", service="prism-test", spool_path=spool)
    first_end = spool.stat().st_size
    _CollectorHandler.body = b'{"partialSuccess":{"rejectedLogRecords":1}}'
    original_save = shipper.save_checkpoint
    def failed(*args):
        raise OSError("checkpoint unavailable")
    monkeypatch.setattr(shipper, "save_checkpoint", failed)
    with pytest.raises(OSError):
        run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    assert load_checkpoint(checkpoint) is None
    later = emit_event("pipeline.later", service="prism-test", spool_path=spool)
    monkeypatch.setattr(shipper, "save_checkpoint", original_save)
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 0
    assert len(_CollectorHandler.payloads) == 1
    assert load_checkpoint(checkpoint).offset == first_end
    _CollectorHandler.body = b'{}'
    assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 1
    records = _CollectorHandler.payloads[-1][1]["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
    assert json.loads(records[0]["body"]["stringValue"])["event_id"] == later["event_id"] != first["event_id"]


@pytest.mark.parametrize("content_type,accepted", [("", True), ("application/x-protobuf", True), ("application/json", False)])
def test_empty_body_protocol_compatibility(tmp_path, collector, content_type, accepted):
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    emit_event("pipeline.run", service="prism-test", spool_path=spool)
    _CollectorHandler.body = b""
    _CollectorHandler.content_type = content_type
    if accepted:
        assert run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2) == 1
    else:
        with pytest.raises(RuntimeError, match="invalid OTLP response"):
            run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
        assert load_checkpoint(checkpoint) is None


@pytest.mark.parametrize("status", [400, 429, 503])
def test_http_error_preserves_existing_checkpoint(tmp_path, collector, status):
    spool, checkpoint = tmp_path / "events.jsonl", tmp_path / "checkpoint.json"
    emit_event("pipeline.first", service="prism-test", spool_path=spool)
    run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    before = checkpoint.read_bytes()
    emit_event("pipeline.second", service="prism-test", spool_path=spool)
    _CollectorHandler.status = status
    _CollectorHandler.body = b'{"message":"token-secret"}'
    with pytest.raises(RuntimeError) as caught:
        run_once(spool_path=spool, checkpoint_path=checkpoint, endpoint=collector, batch_size=100, timeout=2)
    assert str(caught.value) == f"OTLP endpoint returned HTTP {status}"
    assert checkpoint.read_bytes() == before


def test_full_resource_identity_roundtrip_no_fanout_loss(tmp_path):
    events = []
    identities = [("btc", "v1", "demo", "h1"), ("btc", "v2", "demo", "h1"),
                  ("btc", "v1", "live", "h1"), ("btc", "v1", "demo", "h2"),
                  ("us", "v1", "demo", "h1"), ("btc", "v1", "demo", "h1")]
    for identity in identities:
        event = emit_event("pipeline.run", service="prism-test", spool_path=tmp_path / "events.jsonl")
        event.update(zip(("service", "git_sha", "environment", "host"), identity))
        events.append(event)
    payload = build_otlp_payload(events)
    assert len(payload["resourceLogs"]) == 5
    restored = []
    for group in payload["resourceLogs"]:
        resource = {a["key"]: next(iter(a["value"].values())) for a in group["resource"]["attributes"]}
        for record in group["scopeLogs"][0]["logRecords"]:
            event = json.loads(record["body"]["stringValue"])
            assert tuple(resource[key] for key in ("service.name", "service.version", "deployment.environment", "host.name")) == tuple(event[key] for key in ("service", "git_sha", "environment", "host"))
            assert record["traceId"] == event["trace_id"]
            assert record["spanId"] == event["span_id"]
            restored.append(event)
    assert sorted(restored, key=lambda e:e["event_id"]) == sorted(events, key=lambda e:e["event_id"])
