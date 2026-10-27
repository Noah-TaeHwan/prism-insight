import gzip
import sqlite3

import pytest

from analysis.scenario_replay import _runtime_event_hash
from live.scenario_review_storage import MAGIC, MAX_BYTES, review_text
from observability.review_storage import review_text as export_text


def test_legacy_and_compressed_have_same_replay_hash():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE llm_scenario_review_events(slot,sequence,body)')
    conn.execute('CREATE TABLE llm_scenario_review_tapes(slot,body)')
    value = '{"kind":"MODEL_ERROR","reason":"hello"}'
    conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)', (1, 0, value))
    conn.execute('INSERT INTO llm_scenario_review_tapes VALUES(?,?)', (1, value))
    original = _runtime_event_hash(conn)
    packed = MAGIC + gzip.compress(value.encode(), mtime=0)
    conn.execute('UPDATE llm_scenario_review_events SET body=?', (packed,))
    conn.execute('UPDATE llm_scenario_review_tapes SET body=?', (packed,))
    assert _runtime_event_hash(conn) == original
    assert review_text(packed) == export_text(packed) == value


@pytest.mark.parametrize('data', [MAGIC+b'bad', MAGIC+gzip.compress(b'{}')[:-1],
                                  MAGIC+gzip.compress(b'{}')+b'junk',
                                  MAGIC+gzip.compress(b'x'*(MAX_BYTES+1))])
def test_bad_compressed_evidence_never_becomes_empty(data):
    with pytest.raises(ValueError):
        review_text(data)
    with pytest.raises(ValueError):
        export_text(data)


def test_comparator_reads_compressed_event_without_changing_hash(tmp_path):
    import json
    from tools.compare_btc_scenario_replays import digest, verify_economic_rejections
    event = {'kind': 'CALCULATED_CANDIDATES', 'body': {'candidates': []}}
    text = json.dumps(event)
    with sqlite3.connect(tmp_path / 'replay.sqlite') as conn:
        conn.execute('CREATE TABLE llm_scenario_review_events(slot,sequence,body)')
        conn.execute('CREATE TABLE llm_scenario_decisions(slot,proposal,context)')
        conn.execute('CREATE TABLE llm_scenario_intents(payload)')
        conn.execute('CREATE TABLE llm_scenario_recovery_events(slot,kind)')
        conn.execute('INSERT INTO llm_scenario_review_events VALUES(?,?,?)',
                     (10, 0, MAGIC + gzip.compress(text.encode(), mtime=0)))
    report = {'contract': {'policy_profile': 'economic-reject-goal-v2'},
              'decision_outcomes': {}, 'economic_rejections': {
                  'policy': 'preauthority-economic-reject-v1', 'rejected_ticks': 0,
                  'rejected_candidates': 0, 'codes': {}, 'durable_events_sha256': digest([(10, event)])}}
    verify_economic_rejections(tmp_path, report)
