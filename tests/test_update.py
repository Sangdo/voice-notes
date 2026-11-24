"""In-app updates: version comparison, release parsing, verified installer download."""
import hashlib
import http.server
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from voice_notes import update

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize('latest, current, expected', [
    ('1.0.12', '1.0.9', True),     # numeric, not text, comparison
    ('v1.0.12', '1.0.12', False),
    ('1.0.11', '1.0.12', False),
    ('1.1.0', '1.0.99', True),
    ('1.0.3', None, True),         # running from source
    ('', '1.0.1', False),
])
def test_newer(latest, current, expected):
    assert update.newer(latest, current) is expected


def test_version_comes_from_build_stamp(monkeypatch, tmp_path):
    assert update.parse('1.0.42 abc1234 2026-11-23'.split()[0]) == (1, 0, 42)


def test_pick_finds_installer_and_digest():
    release = {'tag_name': 'v1.0.7', 'body': 'Fixes', 'assets': [
        {'name': 'other.zip', 'browser_download_url': 'x'},
        {'name': update.ASSET, 'browser_download_url': 'https://example/setup.exe', 'size': 10,
         'digest': 'sha256:abc'}]}
    assert update.pick(release) == ('1.0.7', 'Fixes', {'url': 'https://example/setup.exe', 'size': 10,
                                                      'sha256': 'abc'})
    assert update.pick({'tag_name': 'v1.0.8', 'assets': []})[2] is None


@pytest.fixture
def server(tmp_path):
    (tmp_path/'setup.exe').write_bytes(b'installer' * 1000)
    handler = lambda *a: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path))
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{httpd.server_port}/setup.exe', hashlib.sha256(b'installer' * 1000).hexdigest()
    httpd.shutdown()


def run_worker(tmp_path, job):
    path = tmp_path/'job.json'
    path.write_text(json.dumps(job))
    out = subprocess.run([sys.executable, '-m', 'voice_notes.worker', str(path)], cwd=ROOT, capture_output=True,
                         text=True, env={'PATH': '/usr/bin:/bin', 'VOICE_NOTES_LOG_DIR': str(tmp_path)})
    return out.returncode, [json.loads(line) for line in out.stdout.splitlines()]


def test_get_update_downloads_and_verifies(tmp_path, server):
    url, sha = server
    code, events = run_worker(tmp_path, {'mode': 'get_update', 'folder': str(tmp_path/'u'),
                                         'asset': {'url': url, 'size': 9000, 'sha256': sha}})
    assert code == 0 and events[-1] == {'type': 'done', 'path': str(tmp_path/'u'/update.ASSET)}
    assert (tmp_path/'u'/update.ASSET).stat().st_size == 9000


def test_get_update_rejects_a_corrupt_download(tmp_path, server):
    url, _ = server
    code, events = run_worker(tmp_path, {'mode': 'get_update', 'folder': str(tmp_path/'u'),
                                         'asset': {'url': url, 'size': 9000, 'sha256': '0' * 64}})
    assert code == 1 and 'did not download correctly' in events[-1]['message']
    assert not list((tmp_path/'u').iterdir())
