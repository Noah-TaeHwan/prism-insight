"""Ship PRISM JSONL events to an OTLP/HTTP logs endpoint.

The checkpoint advances after full acceptance or durable terminal-response
quarantine. OTLP forbids retrying partial success or oversized responses: the
rejected subset (or even acceptance for oversized responses) is unknown.
Quarantine is NOT delivery success and requires operator review. Invalid local
lines are skipped so one corrupt record cannot stop later trading telemetry.
An unresolved durable SENDING receipt holds its batch for manual review rather
than guessing whether a request was sent. This is not exactly-once delivery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import DEFAULT_SPOOL_PATH, _sanitize

DEFAULT_ENDPOINT = "http://127.0.0.1:14318/v1/logs"
DEFAULT_CHECKPOINT = DEFAULT_SPOOL_PATH.with_suffix(".checkpoint.json")
MAX_LINE_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024


class TerminalDeliveryError(RuntimeError):
    """A response that must not be retried, without assuming delivery success."""

    delivery_status = "DELIVERY_UNKNOWN"
    rejected = None
    error_hash = None

    def __init__(self, reason: str, request_hash: str):
        super().__init__(reason)
        self.reason = reason
        self.request_hash = request_hash


class PartialSuccessError(TerminalDeliveryError):
    """Validated partial acceptance, retaining only safe diagnostic metadata."""

    delivery_status = "PARTIAL_UNKNOWN"

    def __init__(self, rejected: int, request_hash: str, error_hash: str):
        super().__init__("partial_success", request_hash)
        self.rejected = rejected
        self.error_hash = error_hash


class _ResponseObject(dict):
    """Retain duplicate names so only known message fields are validated."""

    def __init__(self, pairs):
        super().__init__(pairs)
        seen = set()
        self.duplicates = set()
        for key, _value in pairs:
            if key in seen:
                self.duplicates.add(key)
            seen.add(key)


def _validate_response(raw: bytes, content_type: str, request_hash: str, count: int) -> None:
    # Empty binary protobuf is a valid empty ExportLogsServiceResponse. For
    # legacy endpoints only, an untyped empty body remains compatible; empty
    # application/json is NOT a valid JSON response (the valid form is {}).
    if raw == b"" and content_type in ("", "application/x-protobuf"):
        return
    if len(raw) > MAX_RESPONSE_BYTES:
        # OTLP response-size limits are non-retryable. No accepted/rejected
        # count can be inferred from a truncated response.
        raise TerminalDeliveryError("response_size_limit", request_hash)
    try:
        body = json.loads(raw, object_pairs_hook=_ResponseObject)
        if not isinstance(body, dict):
            raise ValueError("response object required")
        if "partialSuccess" in body.duplicates:
            raise ValueError("duplicate partial success")
        # OTLP requires ignoring unknown JSON message fields. Only lowerCamel
        # known names participate; future fields do not cause retry loops.
        partial = body.get("partialSuccess", _ResponseObject([]))
        if not isinstance(partial, dict):
            raise ValueError("partial success object required")
        if partial.duplicates & {"rejectedLogRecords", "errorMessage"}:
            raise ValueError("duplicate partial success field")
        rejected = partial.get("rejectedLogRecords", 0)
        if isinstance(rejected, str) and rejected.isascii() and rejected.isdecimal():
            rejected = int(rejected)
        if type(rejected) is not int or not 0 <= rejected <= count:
            raise ValueError("invalid rejected count")
        message = partial.get("errorMessage", "")
        if not isinstance(message, str):
            raise ValueError("invalid error message")
        error_hash = hashlib.sha256(message.encode()).hexdigest()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise RuntimeError("invalid OTLP response") from None
    if rejected:
        raise PartialSuccessError(rejected, request_hash, error_hash)


def _batch_hash(events: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(events, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _quarantine_path(checkpoint_path: Path, start: Checkpoint) -> Path:
    return checkpoint_path.with_suffix(".quarantine") / f"{start.inode}-{start.offset}.json"


def _already_quarantined(path: Path, events: list[dict[str, Any]]) -> tuple[Checkpoint, str] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError):
        raise RuntimeError("invalid OTLP quarantine receipt") from None
    retained = value.get("events", []) if isinstance(value, dict) else []
    original = events[:len(retained)] if isinstance(retained, list) else []
    if (not isinstance(value, dict) or not original or value.get("batch_sha256") != _batch_hash(original)
            or value.get("delivery_status") not in ("PARTIAL_UNKNOWN", "DELIVERY_UNKNOWN")
            or retained != [_sanitize(event) for event in original]):
        raise RuntimeError("invalid OTLP quarantine receipt")
    try:
        checkpoint = Checkpoint(**value["checkpoint"])
        if type(checkpoint.inode) is not int or type(checkpoint.offset) is not int or checkpoint.offset < 0:
            raise ValueError("invalid checkpoint")
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("invalid OTLP quarantine receipt") from None
    # A prior fsync or checkpoint write may have failed after rename. Reconfirm
    # durability before acknowledging; new appended events are not swallowed.
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return checkpoint, value["delivery_status"]


def _quarantine(path: Path, events: list[dict[str, Any]], error: TerminalDeliveryError, checkpoint: Checkpoint) -> None:
    """Atomic fsynced receipt; replay of this batch must never resend it."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    value = dict(delivery_status=error.delivery_status, terminal_reason=error.reason, automatic_retry=False,
        batch_sha256=_batch_hash(events), request_sha256=error.request_hash,
        rejected_log_records=error.rejected, collector_error_sha256=error.error_hash,
        checkpoint=dict(inode=checkpoint.inode, offset=checkpoint.offset),
        event_ids=[event.get("event_id") for event in events],
        events=[_sanitize(event) for event in events])
    _write_receipt(path, value)


def _write_receipt(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _attempt_receipt(path: Path, events: list[dict[str, Any]], checkpoint: Checkpoint, status: str) -> None:
    _write_receipt(path, dict(status=status, batch_sha256=_batch_hash(events),
        checkpoint=dict(inode=checkpoint.inode, offset=checkpoint.offset),
        events=[_sanitize(event) for event in events], event_ids=[event.get("event_id") for event in events]))


def _load_attempt(path: Path, events: list[dict[str, Any]], start_offset: int,
                  next_checkpoint: Checkpoint) -> tuple[str, list[dict[str, Any]], Checkpoint] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError):
        raise RuntimeError("invalid OTLP attempt receipt") from None
    try:
        retained = value["events"]
        original = events[:len(retained)]
        checkpoint = Checkpoint(**value["checkpoint"])
        valid = (isinstance(retained, list) and bool(original)
            and value["batch_sha256"] == _batch_hash(original)
            and retained == [_sanitize(event) for event in original]
            and value["status"] in ("SENDING", "ACCEPTED", "RETRY_ALLOWED")
            and type(checkpoint.inode) is int and type(checkpoint.offset) is int
            and checkpoint.inode == next_checkpoint.inode
            and start_offset < checkpoint.offset <= next_checkpoint.offset)
        if not valid:
            raise ValueError("invalid attempt")
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("invalid OTLP attempt receipt") from None
    if value["status"] == "SENDING":
        # This may mean never submitted, accepted, or partially accepted. No
        # automatic inference/retry is safe; preserve source for operator review.
        raise RuntimeError("delivery=UNKNOWN pending_attempt=1 automatic_retry=0 operator_review_required=1")
    return value["status"], original, checkpoint


@dataclass(frozen=True)
class Checkpoint:
    inode: int
    offset: int


def load_checkpoint(path: Path) -> Checkpoint | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(inode=int(value["inode"]), offset=int(value["offset"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def save_checkpoint(path: Path, checkpoint: Checkpoint) -> None:
    _write_receipt(path, {"inode": checkpoint.inode, "offset": checkpoint.offset})


def read_batch(
    spool_path: Path,
    checkpoint: Checkpoint | None,
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], Checkpoint | None]:
    try:
        stat = spool_path.stat()
    except FileNotFoundError:
        return [], checkpoint

    offset = checkpoint.offset if checkpoint and checkpoint.inode == stat.st_ino else 0
    if offset > stat.st_size:
        offset = 0

    events: list[dict[str, Any]] = []
    final_offset = offset
    with spool_path.open("rb") as stream:
        stream.seek(offset)
        while len(events) < limit:
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            final_offset = stream.tell()
            if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                continue
            try:
                value = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                events.append(value)

    return events, Checkpoint(inode=stat.st_ino, offset=final_offset)


def _otel_value(value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    return {"stringValue": "" if value is None else str(value)}


def _attributes(values: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"key": key, "value": _otel_value(value)}
        for key, value in values.items()
        if value is not None
    ]


def build_otlp_payload(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    events = list(events)
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for event in events:
        identity = tuple(event.get(key) for key in ("service", "git_sha", "environment", "host"))
        groups.setdefault(identity, []).append(event)
    if len(groups) > 1:
        return {"resourceLogs": [
            resource for group in groups.values()
            for resource in build_otlp_payload(group)["resourceLogs"]
        ]}
    records = []
    first: dict[str, Any] | None = None
    for event in events:
        if first is None:
            first = event
        payload_attributes = event.get("attributes") or {}
        records.append(
            {
                "timeUnixNano": str(event.get("time_unix_nano") or "0"),
                "observedTimeUnixNano": str(time.time_ns()),
                "severityText": str(event.get("severity") or "INFO"),
                "body": {
                    "stringValue": json.dumps(
                        event,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                },
                "attributes": _attributes(
                    {
                        "event.name": event.get("event_type"),
                        "event.id": event.get("event_id"),
                        "prism.market": event.get("market"),
                        "prism.ticker": event.get("ticker"),
                        "prism.git_sha": event.get("git_sha"),
                        "prism.policy_version": event.get("policy_version"),
                        "prism.config_hash": event.get("config_hash"),
                        "prism.decision_id": event.get("decision_id"),
                        "prism.position_id": event.get("position_id"),
                        "prism.scenario_ref": payload_attributes.get("scenario_ref", payload_attributes.get("scenario_id_ref")),
                        "prism.intent_ref": payload_attributes.get("intent_ref", payload_attributes.get("intent_id_ref")),
                        "prism.decision_slot": payload_attributes.get("decision_slot"),
                        "prism.source_kind": payload_attributes.get("source_kind"),
                        "prism.ledger_status": payload_attributes.get("ledger_status"),
                        "prism.notice_status": payload_attributes.get("notice_status"),
                        "prism.trigger_type": payload_attributes.get("trigger_type"),
                        "prism.regime": payload_attributes.get("regime"),
                        "prism.primary_trend_regime": payload_attributes.get("primary_trend_regime"),
                        "prism.effective_entry_regime": payload_attributes.get("effective_entry_regime"),
                        "prism.swing_state": payload_attributes.get("swing_state"),
                        "prism.context_schema_version": payload_attributes.get("context_schema_version"),
                        "prism.feedback_mode": payload_attributes.get("mode"),
                        "prism.applied_adjust": payload_attributes.get("applied_adjust"),
                        "prism.slot_allocation": payload_attributes.get("slot_allocation"),
                    }
                ),
                "traceId": event.get("trace_id"),
                "spanId": event.get("span_id"),
            }
        )

    resource = first or {}
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": _attributes(
                        {
                            "service.name": resource.get("service", "prism-trading"),
                            "service.version": resource.get("git_sha"),
                            "deployment.environment": resource.get("environment"),
                            "host.name": resource.get("host"),
                        }
                    )
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "prism.observability", "version": "1"},
                        "logRecords": records,
                    }
                ],
            }
        ]
    }


def send_batch(
    events: list[dict[str, Any]],
    *,
    endpoint: str,
    timeout: float,
    auth_token: str | None = None,
) -> None:
    encoded = json.dumps(build_otlp_payload(events), ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if auth_token:
        # ClickStack's static bearertokenauth extension compares the complete
        # Authorization value to OTLP_AUTH_TOKEN (without a "Bearer " prefix).
        headers["Authorization"] = auth_token
    request = urllib.request.Request(endpoint, data=encoded, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"OTLP endpoint returned HTTP {response.status}")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            _validate_response(raw, content_type, hashlib.sha256(encoded).hexdigest(), len(events))
    except urllib.error.HTTPError as error:
        # Never print untrusted reason text, response bodies, URL or credentials.
        error.close()
        raise RuntimeError(f"OTLP endpoint returned HTTP {error.code}") from None


def run_once(
    *,
    spool_path: Path,
    checkpoint_path: Path,
    endpoint: str,
    batch_size: int,
    timeout: float,
    auth_token: str | None = None,
) -> int:
    checkpoint = load_checkpoint(checkpoint_path)
    events, next_checkpoint = read_batch(spool_path, checkpoint, limit=batch_size)
    if next_checkpoint is None:
        return 0
    if events:
        start_offset = checkpoint.offset if checkpoint and checkpoint.inode == next_checkpoint.inode else 0
        if start_offset > spool_path.stat().st_size:
            start_offset = 0
        quarantine = _quarantine_path(checkpoint_path, Checkpoint(next_checkpoint.inode, start_offset))
        attempt = quarantine.with_suffix(".attempt.json")
        partial = _already_quarantined(quarantine, events)
        if not partial:
            prior = _load_attempt(attempt, events, start_offset, next_checkpoint)
            if prior:
                status, events, next_checkpoint = prior
            else:
                status = "NEW"
            if status != "ACCEPTED":
                # This durable pre-submit record survives a terminal response
                # followed by disk/quarantine failure, or a process crash.
                _attempt_receipt(attempt, events, next_checkpoint, "SENDING")
                try:
                    send_batch(events, endpoint=endpoint, timeout=timeout, auth_token=auth_token)
                except TerminalDeliveryError as error:
                    _quarantine(quarantine, events, error, next_checkpoint)
                    partial = next_checkpoint, error.delivery_status
                except (OSError, RuntimeError, urllib.error.URLError):
                    _attempt_receipt(attempt, events, next_checkpoint, "RETRY_ALLOWED")
                    raise
                else:
                    _attempt_receipt(attempt, events, next_checkpoint, "ACCEPTED")
        if partial:
            terminal_checkpoint, delivery_status = partial
            if terminal_checkpoint.inode != next_checkpoint.inode or not start_offset < terminal_checkpoint.offset <= next_checkpoint.offset:
                raise RuntimeError("invalid OTLP quarantine checkpoint")
            save_checkpoint(checkpoint_path, terminal_checkpoint)
            attempt.unlink(missing_ok=True)
            print(f"delivery={delivery_status} quarantined=1 automatic_retry=0", file=sys.stderr, flush=True)
            return 0
    save_checkpoint(checkpoint_path, next_checkpoint)
    if events:
        attempt.unlink(missing_ok=True)
    return len(events)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--spool",
        type=Path,
        default=Path(os.getenv("PRISM_OBSERVABILITY_SPOOL", str(DEFAULT_SPOOL_PATH))),
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=Path(
            os.getenv("PRISM_OBSERVABILITY_CHECKPOINT", str(DEFAULT_CHECKPOINT))
        ),
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("PRISM_OBSERVABILITY_OTLP_ENDPOINT", DEFAULT_ENDPOINT),
    )
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--btc-source", type=Path,
                        default=Path(os.getenv("PRISM_BTC_OBSERVABILITY_DB",
                                     str(DEFAULT_SPOOL_PATH.parent.parent / "stock_tracking_db.sqlite"))))
    parser.add_argument("--btc-state", type=Path)
    parser.add_argument("--no-btc-export", action="store_true")
    return parser


def _export_btc(args):
    """Independent read-only source capture; failure must not stop other shipping."""
    if args.no_btc_export or not args.btc_source.is_file():
        return {"status": "DISABLED"}
    try:
        from .btc_scenario_export import export_once
        state = args.btc_state or args.checkpoint.with_name("btc-research-export.sqlite")
        result = export_once(args.btc_source, state, args.spool, limit=32)
        if result.get("emitted") or result.get("status") != "OK" or result.get("backlog"):
            print("btc_export=" + json.dumps(result, sort_keys=True), flush=True)
        return result
    except Exception as error:  # no raw source data or arbitrary exception message
        print(f"btc_export_failed={type(error).__name__}", file=sys.stderr, flush=True)
        return {"status": "UNAVAILABLE", "error_type": type(error).__name__}


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    auth_token = os.getenv("PRISM_OBSERVABILITY_OTLP_TOKEN")
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        _export_btc(args)
        try:
            shipped = run_once(
                spool_path=args.spool,
                checkpoint_path=args.checkpoint,
                endpoint=args.endpoint,
                batch_size=max(1, args.batch_size),
                timeout=max(0.1, args.timeout),
                auth_token=auth_token,
            )
            if shipped:
                print(f"shipped={shipped}", flush=True)
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            print(f"ship_failed={type(error).__name__}: {error}", file=sys.stderr, flush=True)
            if args.once:
                return 1

        if args.once or stopping:
            return 0
        time.sleep(max(0.5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
