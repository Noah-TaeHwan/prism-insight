import gzip
import fcntl
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

from utils import btc_offsite_archive as archive


def git(repo, *args):
    return archive.run(['git', *args], cwd=repo).strip()


@pytest.fixture
def payload(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init')
    git(repo, 'config', 'user.name', 'test')
    git(repo, 'config', 'user.email', 'test@example.invalid')
    (repo / 'source.py').write_text('print(1)\n')
    git(repo, 'add', 'source.py')
    git(repo, 'commit', '-m', 'initial')
    first = git(repo, 'rev-parse', 'HEAD')
    # Keep an actual commit that no source ref reaches; the bundle must include it.
    tree = git(repo, 'rev-parse', 'HEAD^{tree}')
    orphan = git(repo, 'commit-tree', tree, '-m', 'historical detached revision')
    git(repo, 'update-ref', 'refs/notes/archive-test', first)
    raw = tmp_path / 'source.sqlite'
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_audit_manifests(body TEXT)')
        db.execute('INSERT INTO llm_scenario_audit_manifests VALUES(?)',
                   (json.dumps({'git_revision': orphan}),))
    zipped = tmp_path / archive.FILES[0]
    with gzip.open(zipped, 'wb') as f:
        f.write(raw.read_bytes())
    manifest = dict(sqlite_quick_check='ok', decompression_verified=True, source_writes=0,
                    raw_bytes=raw.stat().st_size, raw_sha256=archive.digest(raw),
                    compressed_bytes=zipped.stat().st_size, compressed_sha256=archive.digest(zipped))
    mp = tmp_path / archive.FILES[1]
    mp.write_text(json.dumps(manifest))
    refs_before = git(repo, 'show-ref')
    bundle = tmp_path / archive.FILES[2]
    archive.make_bundle(repo, bundle, [orphan], tmp_path / 'temporary.git')
    assert git(repo, 'show-ref') == refs_before
    assert 'refs/notes/archive-test' in git(repo, 'bundle', 'list-heads', str(bundle))
    needed = raw.stat().st_size + zipped.stat().st_size + 4 * bundle.stat().st_size + 1024**2
    _, coverage = archive.referenced_commits(raw)
    metadata = dict(required_space=needed, source_head=first, referenced_commits=[orphan], code_reference_coverage=coverage,
                    sha256={name: archive.digest(tmp_path / name) for name in archive.FILES[:-1]})
    (tmp_path / archive.FILES[3]).write_text(json.dumps(metadata))
    return tmp_path, needed, manifest, repo


def transfer(path, names=archive.FILES):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        for name in names:
            tar.add(path / name, arcname=name, recursive=False)
    stream.seek(0)
    return stream


def test_verified_restore_and_exact_commit_no_settings_upload(payload, monkeypatch):
    path, needed, manifest, _ = payload
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    (path / '.env').write_text('private-do-not-copy')
    root = path / 'remote'
    ack = archive.receive(transfer(path), root, needed)
    assert ack['status'] == 'VERIFIED' and ack['raw_sha256'] == manifest['raw_sha256']
    saved = root / ack['archive']
    assert {p.name for p in saved.iterdir()} == set(archive.FILES) | {'receipt.json'}
    assert all(p.stat().st_mode & 0o077 == 0 for p in saved.iterdir())
    assert not list(root.glob('.incoming-*'))
    assert (root / '.receive.lock').stat().st_mode & 0o077 == 0


@pytest.mark.parametrize('fault', ['checksum', 'partial', 'env', 'raw_hash', 'understated_space', 'missing_ref'])
def test_failed_receive_preserves_old_archive_and_removes_only_staging(payload, monkeypatch, fault):
    path, needed, _, _ = payload
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    root = path / 'remote'
    root.mkdir(mode=0o700)
    old = root / 'older-verified'
    old.write_bytes(b'old proof')
    names = archive.FILES
    if fault == 'checksum':
        (path / archive.FILES[0]).write_bytes(b'corrupt')
    if fault == 'partial':
        names = archive.FILES[:-1]
    if fault == 'env':
        (path / '.env').write_text('not allowed')
        names = (*archive.FILES, '.env')
    if fault in {'raw_hash', 'missing_ref', 'understated_space'}:
        mp = path / archive.FILES[3]
        meta = json.loads(mp.read_text())
        if fault == 'raw_hash':
            sp = path / archive.FILES[1]
            snapshot = json.loads(sp.read_text())
            snapshot['raw_sha256'] = '0' * 64
            sp.write_text(json.dumps(snapshot))
            meta['sha256'][archive.FILES[1]] = archive.digest(sp)
        elif fault == 'missing_ref':
            meta['referenced_commits'] = []
        else:
            needed = sum((path / name).stat().st_size for name in archive.FILES) + 4096
            meta['required_space'] = needed
        mp.write_text(json.dumps(meta))
    with pytest.raises((ValueError, tarfile.TarError, EOFError)):
        archive.receive(transfer(path, names), root, needed)
    assert old.read_bytes() == b'old proof'
    assert sorted(p.name for p in root.iterdir()) == ['.receive.lock', 'older-verified']


def test_space_reserve_and_capacity_are_fail_closed(tmp_path, monkeypatch):
    root = tmp_path / 'remote'
    archive.private_root(root)
    old = root / 'old'
    old.write_bytes(b'x')
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=archive.RESERVE + 9))
    with pytest.raises(OSError, match='space'):
        archive.receive(io.BytesIO(), root, 10)
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    with pytest.raises(OSError, match='space'):
        archive.space(root, archive.CAP)
    assert old.read_bytes() == b'x'


def test_root_and_host_safety(tmp_path):
    with pytest.raises(ValueError):
        archive.private_root(Path('relative'))
    link = tmp_path / 'linked'
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        archive.private_root(link / 'archive')
    config = tmp_path / 'tunnel.env'
    assert archive.backend_host(config) is None
    for value in ['-oProxyCommand=bad', 'host; touch /tmp/bad', 'a..b', 'a.-b', '999.1.2.3', '$(whoami)', '']:
        config.write_text('PRISM_BACKEND_HOST=' + value)
        with pytest.raises(ValueError):
            archive.backend_host(config)
    config.write_text('SECRET=not-read-as-host\nPRISM_BACKEND_HOST=158.247.205.250\n')
    assert archive.backend_host(config) == '158.247.205.250'


def test_missing_source_commit_cannot_create_valid_bundle(payload):
    path, _, _, repo = payload
    with pytest.raises(ValueError, match='referenced_commit_unavailable'):
        archive.make_bundle(repo, path / 'missing.bundle', ['0' * 40], path / 'missing.git')


def test_expansion_limit_and_concurrent_receiver_preserve_existing(payload, monkeypatch):
    path, needed, manifest, _ = payload
    bad = dict(manifest, raw_bytes=1)
    with pytest.raises(ValueError, match='expansion_bound'):
        archive.restore_snapshot(path / archive.FILES[0], bad, path / 'oversize.sqlite')
    root = path / 'remote'
    root.mkdir(mode=0o700)
    lock = root / '.receive.lock'
    lock.write_text('another receiver')
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    with lock.open('r+') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            archive.receive(transfer(path), root, needed)
    assert lock.read_text() == 'another receiver'


def test_killed_lock_holder_does_not_strand_receiver(payload, monkeypatch):
    path, needed, _, _ = payload
    root = path / 'remote'
    root.mkdir(mode=0o700)
    lock = root / '.receive.lock'
    script = ('import fcntl,os,sys,time; '
              'fd=os.open(sys.argv[1],os.O_CREAT|os.O_RDWR,0o600); '
              'fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); print("held",flush=True); time.sleep(60)')
    process = subprocess.Popen([os.sys.executable, '-c', script, str(lock)], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'held'
        inode = lock.stat().st_ino
        with pytest.raises(BlockingIOError):
            archive.receive(io.BytesIO(), root, needed)
        process.kill()
        process.wait(timeout=10)
        monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
        assert archive.receive(transfer(path), root, needed)['status'] == 'VERIFIED'
        assert lock.stat().st_ino == inode
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        process.stdout.close()


def test_unknown_missing_and_malformed_revision_evidence_is_counted(tmp_path):
    raw = tmp_path / 'evidence.sqlite'
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_audit_manifests(body TEXT)')
        db.executemany('INSERT INTO llm_scenario_audit_manifests VALUES(?)', [
            (json.dumps({'git_revision': 'a' * 40}),),
            (json.dumps({'git_revision': 'UNKNOWN'}),),
            (json.dumps({'git_revision': None}),), ('{}',), ('broken JSON',),
        ])
    commits, coverage = archive.referenced_commits(raw)
    assert commits == ['a' * 40]
    assert coverage['unique_known_revision_count'] == 1
    assert coverage['invalid_or_unknown_revision_fields'] == 2
    assert coverage['missing_manifest_git_revision'] == 2
    assert coverage['malformed_evidence_rows'] == 1
    assert 'ONLY' in coverage['scope']


def test_uncollected_null_inputs_are_missing_not_corrupt(tmp_path):
    raw = tmp_path / 'uncollected.sqlite'
    with sqlite3.connect(raw) as db:
        db.execute('CREATE TABLE llm_scenario_audit_manifests(body TEXT)')
        db.execute('INSERT INTO llm_scenario_audit_manifests VALUES(NULL)')
        db.execute('CREATE TABLE llm_scenario_decisions(context TEXT,snapshot TEXT)')
        db.execute("INSERT INTO llm_scenario_decisions VALUES(NULL,'')")
    commits, coverage = archive.referenced_commits(raw)
    assert commits == []
    assert coverage['missing_evidence_values'] == 3
    assert coverage['malformed_evidence_rows'] == 0
    assert coverage['missing_manifest_git_revision'] == 1


def test_receiver_recomputes_coverage_and_rejects_symlink_lock(payload, monkeypatch):
    path, needed, _, _ = payload
    root = path / 'remote'
    root.mkdir(mode=0o700)
    target = path / 'untouched'
    target.write_text('keep')
    (root / '.receive.lock').symlink_to(target)
    with pytest.raises(ValueError, match='lock_symlink'):
        archive.receive(io.BytesIO(), root, needed)
    assert target.read_text() == 'keep'
    (root / '.receive.lock').unlink()
    meta_path = path / 'archive.json'
    metadata = json.loads(meta_path.read_text())
    metadata['code_reference_coverage']['missing_manifest_git_revision'] = 100
    meta_path.write_text(json.dumps(metadata))
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    with pytest.raises(ValueError, match='reference_coverage_mismatch'):
        archive.receive(transfer(path), root, needed)


def test_cli_disabled_is_not_success_and_retention_follows_ack(tmp_path):
    result = subprocess.run([os.sys.executable, str(Path(archive.__file__)), '--repo', str(tmp_path),
                             '--snapshot', str(tmp_path / 'missing.gz'), '--config', str(tmp_path / 'absent')],
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 2
    assert json.loads(result.stdout)['status'] == 'DISABLED'
    script = Path(archive.__file__).with_name('backup_configs.sh').read_text()
    assert script.index('btc_offsite_archive.py') < script.index('-mtime +7')
    assert '기존 백업 보존, 보존기간 삭제 중단' in script


def test_local_preparation_upload_and_ack_binding_without_network(payload, monkeypatch):
    path, _, manifest, repo = payload
    snapshot = path / archive.FILES[0]
    Path(str(snapshot) + '.manifest.json').write_text(json.dumps(manifest))
    config = path / 'tunnel.env'
    config.write_text('PRISM_BACKEND_HOST=archive.example.invalid\nSECRET=not-sent\n')
    (path / '.env').write_text('not-sent')
    monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * archive.CAP))
    original = subprocess.run
    calls = []
    def transport(args, **kwargs):
        if args[0] != 'ssh':
            return original(args, **kwargs)
        calls.append(args)
        assert 'StrictHostKeyChecking=yes' in args and 'BatchMode=yes' in args
        assert 'root@archive.example.invalid' in args
        if 'stdin' not in kwargs:
            return subprocess.CompletedProcess(args, 0, stdout='READY\n', stderr='')
        stream = kwargs['stdin']
        with tarfile.open(fileobj=stream, mode='r:') as tar:
            assert set(tar.getnames()) == set(archive.FILES)
            meta = json.load(tar.extractfile('archive.json'))
        stream.seek(0)
        result = archive.receive(stream, path / 'remote', meta['required_space'])
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(result).encode(), stderr=b'')
    monkeypatch.setattr(archive.subprocess, 'run', transport)
    ack = archive.archive(repo, snapshot, config)
    assert ack['status'] == 'VERIFIED' and len(calls) == 2
    assert not list(path.glob('.btc-offsite-*'))
