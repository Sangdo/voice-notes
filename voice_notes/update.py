"""Updates for the packaged Windows app, checked only when asked (Help > Check for Updates).

Installers are published as GitHub releases of this (public) repository. The check reads the latest release
from GitHub's API; nothing about the user, recordings or notes is sent.
"""
from pathlib import Path
import re
import sys

RELEASES_REPO = 'Sangdo/voice-notes'
LATEST = f'https://api.github.com/repos/{RELEASES_REPO}/releases/latest'
ASSET = 'VoiceNotes-Setup.exe'


def supported():
    """Only the installed Windows app can update itself (a source checkout updates with git)."""
    return sys.platform == 'win32' and getattr(sys, 'frozen', False)


def version():
    """This build's version, e.g. '1.0.12' (written by packaging/build.py), or None when run from source."""
    try:
        text = (Path(__file__).parent/'build.txt').read_text(encoding='utf-8')
    except OSError:
        return None
    match = re.match(r'v?(\d+(?:\.\d+)+)\b', text.strip())
    return match.group(1) if match else None


def parse(tag):
    match = re.match(r'v?(\d+(?:\.\d+)*)', tag or '')
    return tuple(int(x) for x in match.group(1).split('.')) if match else ()


def newer(latest, current):
    return bool(parse(latest)) and parse(latest) > parse(current or '0')


def pick(release):
    """(version, notes, asset) of a GitHub release, asset = {url, size, sha256} or None."""
    asset = next((a for a in release.get('assets', []) if a.get('name') == ASSET), None)
    if asset:
        digest = asset.get('digest') or ''
        asset = {'url': asset['browser_download_url'], 'size': asset.get('size') or 0,
                 'sha256': digest.split(':', 1)[1] if digest.startswith('sha256:') else None}
    return release.get('tag_name', '').lstrip('v'), (release.get('body') or '').strip(), asset
