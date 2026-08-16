"""
Helpers for extracting and logging OpenAI API error metadata.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable
from typing import Any

_STATUS_PATTERN = re.compile(r"Error code:\s*(\d{3})")
_CODE_PATTERN = re.compile(r"""['"]code['"]\s*:\s*['"]([^'"]+)['"]""")
_TYPE_PATTERN = re.compile(r"""['"]type['"]\s*:\s*['"]([^'"]+)['"]""")
_REQUEST_ID_KEYS = ("x-request-id", "x-client-request-id")
_SAFE_METADATA_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:/-"
)
_MAX_CODE_OR_TYPE_CHARS = 64
_MAX_REQUEST_ID_CHARS = 128
_MAX_CONTEXT_CHARS = 160
MAX_OPENAI_LOG_RECORD_CHARS = 512
_OPENAI_CLASS_MARKERS = (
    "OpenAIError",
    "APIError",
    "APIStatusError",
    "BadRequestError",
    "RateLimitError",
    "AuthenticationError",
    "PermissionDeniedError",
)


def _sanitize_metadata_token(value: Any, max_length: int) -> str:
    """Return a bounded safe-ASCII metadata token without coercing callers."""
    if type(value) is not str or type(max_length) is not int or max_length < 1:
        return "invalid"

    trimmed = value.strip(" ")
    if not trimmed or any(
        character not in _SAFE_METADATA_CHARACTERS for character in trimmed
    ):
        return "invalid"

    return trimmed[:max_length]


def _sanitize_context(value: Any, max_length: int = _MAX_CONTEXT_CHARS) -> str:
    """Return bounded printable single-line context without coercing callers."""
    if type(value) is not str or type(max_length) is not int or max_length < 1:
        return "invalid_context"
    if "," in value or "=" in value:
        return "invalid_context"
    if value.splitlines() != [value]:
        return "invalid_context"
    if any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in value
    ):
        return "invalid_context"

    trimmed = value.strip()
    if not trimmed:
        return "invalid_context"
    return trimmed[:max_length]


def _sanitize_status_code(value: Any) -> int | None:
    """Accept only exact built-in integers in the HTTP status-code range."""
    if type(value) is int and 100 <= value <= 599:
        return value
    return None


def _iter_exception_chain(error: BaseException) -> Iterable[BaseException]:
    queue = [error]
    seen: set[int] = set()

    while queue:
        current = queue.pop(0)
        current_id = id(current)
        if current_id in seen:
            continue

        seen.add(current_id)
        yield current

        for linked in (getattr(current, "__cause__", None), getattr(current, "__context__", None)):
            if isinstance(linked, BaseException):
                queue.append(linked)

        for arg in getattr(current, "args", ()):
            if isinstance(arg, BaseException):
                queue.append(arg)


def _get_header_value(response: Any, *header_names: str) -> str | None:
    headers = getattr(response, "headers", None)
    if not headers:
        return None

    for name in header_names:
        try:
            value = headers.get(name)
        except Exception:
            value = None
        if value:
            return str(value)

    return None


def extract_openai_error_details(error: BaseException) -> dict[str, Any]:
    details: dict[str, Any] = {
        "is_openai_error": False,
        "status_code": None,
        "code": None,
        "type": None,
        "request_id": None,
        "message": None,
    }

    for exc in _iter_exception_chain(error):
        class_name = type(exc).__name__
        if class_name in _OPENAI_CLASS_MARKERS or "openai" in type(exc).__module__.lower():
            details["is_openai_error"] = True

        if details["status_code"] is None:
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int):
                details["status_code"] = status_code

        if details["request_id"] is None:
            request_id = getattr(exc, "request_id", None)
            if request_id:
                details["request_id"] = str(request_id)

        response = getattr(exc, "response", None)
        if details["request_id"] is None and response is not None:
            details["request_id"] = _get_header_value(response, *_REQUEST_ID_KEYS)
            details["is_openai_error"] = True

        body = getattr(exc, "body", None)
        error_payload = body.get("error", body) if isinstance(body, dict) else None

        if details["code"] is None:
            code = getattr(exc, "code", None)
            if code:
                details["code"] = str(code)
            elif isinstance(error_payload, dict) and error_payload.get("code"):
                details["code"] = str(error_payload["code"])

        if details["type"] is None:
            error_type = getattr(exc, "type", None)
            if error_type:
                details["type"] = str(error_type)
            elif isinstance(error_payload, dict) and error_payload.get("type"):
                details["type"] = str(error_payload["type"])

        if details["message"] is None:
            if isinstance(error_payload, dict) and error_payload.get("message"):
                details["message"] = str(error_payload["message"])
            else:
                message = str(exc).strip()
                if message:
                    details["message"] = message

        raw_text = str(exc)

        if details["status_code"] is None:
            match = _STATUS_PATTERN.search(raw_text)
            if match:
                details["status_code"] = int(match.group(1))

        if details["code"] is None:
            match = _CODE_PATTERN.search(raw_text)
            if match:
                details["code"] = match.group(1)

        if details["type"] is None:
            match = _TYPE_PATTERN.search(raw_text)
            if match:
                details["type"] = match.group(1)

        if details["status_code"] is not None or details["code"] or details["request_id"]:
            details["is_openai_error"] = True

    return details


def log_openai_error(logger: logging.Logger, error: BaseException, context: str) -> bool:
    details = extract_openai_error_details(error)
    raw_code = details["code"]
    raw_type = details["type"]
    raw_request_id = details["request_id"]
    status_code = _sanitize_status_code(details["status_code"])
    code = _sanitize_metadata_token(raw_code, _MAX_CODE_OR_TYPE_CHARS)

    should_log = details["is_openai_error"] or status_code in {400, 429} or code == "insufficient_quota"
    if not should_log:
        return False

    parts = ["context=" + _sanitize_context(context)]
    if status_code is not None:
        parts.append(f"status={status_code}")
    if raw_code is not None:
        parts.append("code=" + code)
    if raw_type is not None:
        parts.append(
            "type=" + _sanitize_metadata_token(raw_type, _MAX_CODE_OR_TYPE_CHARS)
        )
    request_id = (
        "unavailable"
        if raw_request_id is None
        else _sanitize_metadata_token(raw_request_id, _MAX_REQUEST_ID_CHARS)
    )
    parts.append("request_id=" + request_id)

    logger.error("OpenAI API error metadata: %s", ", ".join(parts))
    return True
