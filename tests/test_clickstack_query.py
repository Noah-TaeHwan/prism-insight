"""tools/clickstack_query.py stays read-only and passes values as query parameters (2026-10-18)."""
import ast
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clickstack_query", ROOT / "tools/clickstack_query.py")
cq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cq)


def test_program_carries_values_as_parameters_not_sql():
    nasty = "x'); DROP TABLE otel_logs; --"
    program = cq.build_program("SELECT 1 WHERE TraceId = {trace:String}", {"trace": nasty})
    tail = program.split(cq.REMOTE, 1)[1].strip()
    assert tail.startswith("run(json.loads(") and tail.endswith("))")
    payload = json.loads(ast.literal_eval(tail[len("run(json.loads("):-2]))
    assert payload["sql"] == "SELECT 1 WHERE TraceId = {trace:String}"
    assert payload["params"] == {"trace": nasty}
    assert payload["limits"]["readonly"] == "1"


@pytest.mark.parametrize("query", ["DROP TABLE otel_logs", "INSERT INTO otel_logs VALUES (1)", "  alter table x", ""])
def test_sql_rejects_non_read_queries(query):
    args = type("A", (), {"query": query, "local": False})()
    with pytest.raises(SystemExit):
        cq.cmd_sql(args)


def test_summarize_pulls_decision_and_fill_fields():
    body = {"attributes": {
        "decision_context": {"buy_score": 7, "min_score": 5.0, "gate_reason": ""},
        "fill_provenance": {"status": "REJECTED", "reason_code": "ORDER_NOT_ACCEPTED"},
        "reason": "가" * 300,
        "triggers": [{"rank": 1}],
    }}
    text = cq.summarize(body, width=50)
    assert "buy_score=7" in text and "min_score=5.0" in text and "status=REJECTED" in text
    assert "gate_reason" not in text and "triggers" not in text
    assert "가" * 50 + "…" in text
    assert json.dumps(cq.summarize({}, 50)) == '""'
