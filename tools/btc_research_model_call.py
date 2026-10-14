"""One-attempt localhost OAuth research calls with durable, exact-wire receipts.

``record(event)`` must synchronously persist the local research event. It must
never send raw inputs/responses to public telemetry. Request headers are omitted.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit

from tools.btc_research_call_guard import CallGuardError


MAX_BYTES = 1_048_576
_KWARGS = {"system_prompt", "user_prompt", "model", "reasoning_effort", "fast_tier",
           "timeout", "response_schema", "mcp_profile", "retry_transport"}


class GuardedModelCall:
    def __init__(self, guard, arm, record, *, open_url=None, monotonic=time.monotonic):
        from live import scenario_oauth
        self.guard, self.arm, self.record = guard, arm, record
        self.open_url = scenario_oauth._local_open if open_url is None else open_url
        self.monotonic = monotonic
        self.actual_http_calls = 0

    def on_failure(self, *args, **kwargs):
        """External schema, clock, accounting, or other technical failure latch."""
        self.guard.stop("technical_failure")

    def _now(self):
        value = self.monotonic()
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid_research_clock")
        return value

    def __call__(self, **kwargs):
        from live import scenario_oauth
        attempt_id, request_sha256, actual_calls, succeeded = None, None, 0, False
        opener_entries, http_status = 0, None
        phase = "SINGLE"

        def event(kind, **fields):
            self.record(dict(kind=kind, arm=self.arm, phase=phase,
                             attempt_id=attempt_id, request_sha256=request_sha256, **fields))

        def capture(raw, status):
            if not isinstance(raw, bytes):
                raise ValueError("invalid_http_response_bytes")
            bounded = raw[:MAX_BYTES + 1]
            event("HTTP_RESPONSE", http_status=status, raw_response=bounded.decode("utf-8", errors="replace"),
                  raw_sha256=hashlib.sha256(bounded).hexdigest(), truncated=len(raw) > MAX_BYTES)
            try:
                body = json.loads(bounded)
            except (ValueError, UnicodeDecodeError):
                return
            if isinstance(body, dict):
                detail = body.get("error")
                code = detail.get("code") if isinstance(detail, dict) else None
                if (body.get("status") in ("failed", "cancelled")
                        or code in ("terminal_error", "authentication_error", "invalid_token", "invalid_grant")):
                    raise ValueError("explicit_terminal_response")

        def guarded_open(request, timeout):
            nonlocal attempt_id, request_sha256, actual_calls, opener_entries, http_status
            opener_entries += 1
            if opener_entries != 1:
                raise CallGuardError("research retry forbidden")
            started = self._now()
            url = urlsplit(request.full_url)
            if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"}
                    or url.path != "/v1/responses" or url.username or url.password or url.query or url.fragment
                    or request.get_method() != "POST"):
                raise ValueError("local_oauth_proxy_required")
            raw = request.data
            if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
                raise ValueError("invalid_http_request_bytes")
            request_sha256 = hashlib.sha256(raw).hexdigest()
            attempt_id = self.guard.reserve(self.arm, phase, request_sha256)
            event("HTTP_REQUEST", request_body=raw.decode("utf-8"), generate_kwargs=deepcopy(kwargs))
            if self.guard.snapshot()["stopped"]:
                raise CallGuardError("research stopped before HTTP")
            elapsed = self._now() - started
            if elapsed < 0 or not math.isfinite(timeout) or timeout - elapsed <= 0:
                raise ValueError("research_pre_http_deadline")
            # Counts entry to the real opener, including ambiguous transport errors.
            actual_calls += 1
            self.actual_http_calls += 1
            try:
                response = self.open_url(request, timeout=timeout - elapsed)
            except HTTPError as error:
                http_status = error.code
                if error.fp is not None:
                    try:
                        error_raw = error.read(MAX_BYTES + 1)
                    finally:
                        error.close()
                    capture(error_raw, error.code)
                    raise HTTPError(error.geturl(), error.code, error.msg, error.hdrs,
                                    BytesIO(error_raw)) from error
                raise

            class Response:
                def __enter__(self):
                    response.__enter__()
                    return self

                def __exit__(self, *args):
                    return response.__exit__(*args)

                @property
                def status(self):
                    nonlocal http_status
                    http_status = response.status
                    return http_status

                def geturl(self):
                    return response.geturl()

                def read(self, n):
                    raw_response = response.read(min(n, MAX_BYTES + 1))
                    capture(raw_response, response.status)
                    return raw_response

            return Response()

        try:
            if set(kwargs) - _KWARGS or kwargs.get("retry_transport", False) is not False:
                raise ValueError("research_retry_or_kwargs_policy")
            kwargs = deepcopy(kwargs)
            kwargs["retry_transport"] = False
            properties = kwargs.get("response_schema", {}).get("properties", {})
            phase = "SELECT" if "selection_token" in properties else "DRAFT" if "wait_proposal" in properties else "SINGLE"
            if self.guard.snapshot()["stopped"]:
                raise CallGuardError("research already stopped")
            result = scenario_oauth.generate_scenario(**kwargs, open_url=guarded_open)
            if opener_entries != 1 or actual_calls != 1:
                raise CallGuardError("research HTTP count mismatch")
            event("MODEL_RESULT", text=result.text, actual_http_calls=actual_calls)
            succeeded = True
            return result
        except BaseException as error:
            self.on_failure()
            event("HTTP_ERROR", error_type=type(error).__name__, http_status=http_status,
                  authentication_error=http_status in (401, 403), actual_http_calls=actual_calls)
            raise
        finally:
            if attempt_id is not None:
                self.guard.finish(attempt_id, "ok" if succeeded else "error",
                                  actual_http_calls=actual_calls if actual_calls in (0, 1) else None)
