"""US buy score excludes the journal adjustment, like KR (user decision 2026-10-19).

KR: stock_tracking_enhanced_agent._kr_effective_score = buy_score + macro_adjustment.
US: prism-us/us_stock_tracking_agent._effective_buy_score used to add the journal adjustment to the
gate score while the BUY prompt defines effective_score = buy_score + macro_adjustment.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _functions(path, names):
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in body} == set(names)
    namespace = {"Dict": dict, "Any": object}
    exec(compile(ast.Module(body=body, type_ignores=[]), path, "exec"), namespace)
    return namespace


def test_us_effective_score_excludes_journal_adjustment():
    ns = _functions("prism-us/us_stock_tracking_agent.py", ["_safe_number", "_effective_buy_score"])
    parts = ns["_effective_buy_score"]({"buy_score": 5, "macro_adjustment": 1}, journal_adjustment=-2)
    assert parts["effective_score"] == 6
    assert parts["journal_adjustment"] == -2  # still recorded for observability
    assert ns["_effective_buy_score"]({"buy_score": 5}, journal_adjustment=3)["effective_score"] == 5


def test_kr_effective_score_unchanged():
    ns = _functions("stock_tracking_enhanced_agent.py", ["_kr_effective_score"])
    assert ns["_kr_effective_score"]({"buy_score": 5, "macro_adjustment": 1}) == 6
    assert ns["_kr_effective_score"]({"buy_score": 5, "macro_adjustment": -1, "score_adjustment": 3}) == 4


def test_us_records_journal_as_prompt_only_and_prompt_says_reference():
    source = (ROOT / "prism-us/us_stock_tracking_agent.py").read_text(encoding="utf-8")
    assert 'application_mode="PROMPT_AND_DETERMINISTIC_SCORE"' not in source
    assert "do not add it to buy_score or effective_score" in source
