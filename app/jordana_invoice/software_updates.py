"""Opt-in installation of a release explicitly promoted by the maintainer.

The feed contains public release metadata only. No calendar or billing data is
sent. This file doubles as a copied, detached worker so replacing the installed
runtime cannot interrupt the updater itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

FEED_URL = 'https://raw.githubusercontent.com/BrokenFL/JordanaBilling/main/updates/jordana.json'
RELEASE_PREFIX = 'https://github.com/BrokenFL/JordanaBilling/releases/download/'
_lock = threading.Lock()
_cache = {}


def update_dir():
    return Path(os.environ.get('JORDANA_APP_SUPPORT_DIR', str(Path.home() / 'Library/Application Support/Jordana Billing'))) / 'updates'


def version_key(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:\.post(\d+))?', str(value))
    if not match:
        raise ValueError('Invalid update version.')
    return tuple(int(n or 0) for n in match.groups())


def validate_offer(data, current_version):
    if data.get('enabled') is not True:
        return None
    version = data.get('version', '')
    if version_key(version) <= version_key(current_version):
        return None
    tag = data.get('release_label', '')
    if not re.fullmatch(r'v\d+\.\d+\.\d+-test\.\d+', tag):
        raise ValueError('Invalid update release.')
    if tag != 'v' + version.replace('.post', '-test.'):
        raise ValueError('Update version and release disagree.')
    digest = data.get('sha256', '')
    commit = data.get('commit', '')
    if not re.fullmatch('[a-f0-9]{64}', digest) or not re.fullmatch('[a-f0-9]{40}', commit):
        raise ValueError('Update identity is missing.')
    name = f'JordanaBilling-{tag}-{commit[:12]}-macos-arm64.dmg'
    if data.get('url') != RELEASE_PREFIX + tag + '/' + name:
        raise ValueError('Update must use the promoted release asset.')
    return dict(version=version, release_label=tag, sha256=digest, commit=commit,
                url=data['url'], notes=str(data.get('notes', ''))[:4000])


def fetch_offer(current_version):
    with urlopen(Request(FEED_URL, headers={'User-Agent': 'JordanaBilling-Updater'}), timeout=10) as response:
        if not response.geturl().startswith('https://raw.githubusercontent.com/'):
            raise ValueError('Invalid update feed redirect.')
        body = response.read(65537)
    if len(body) > 65536:
        raise ValueError('Update feed is too large.')
    return validate_offer(json.loads(body), current_version)


def install_status():
    try:
        result = json.loads((update_dir() / 'status.json').read_text())
        if result.get('state') == 'installing' and time.time() - result.get('started_at', 0) > 1800:
            return {'state': 'failed', 'message': 'Update was interrupted. Restart the app and try again.'}
        return result
    except (OSError, ValueError):
        return {'state': 'idle'}


def check_updates(current_version, *, force=False):
    with _lock:
        if not force and _cache.get('version') == current_version and time.time() - _cache.get('at', 0) < 86400:
            return {**_cache['result'], 'installation': install_status()}
        try:
            offer = fetch_offer(current_version)
            result = {'ok': True, 'available': bool(offer), 'offer': offer, 'current_version': current_version}
        except Exception:
            result = {'ok': True, 'available': False, 'unavailable': True,
                      'message': 'Could not check for updates. Try again later.', 'current_version': current_version}
        _cache.update(version=current_version, at=time.time(), result=result)
        return {**result, 'installation': install_status()}


def write_status(directory, **values):
    target = directory / 'status.json'
    temp = directory / 'status.tmp'
    temp.write_text(json.dumps(values))
    temp.replace(target)


def start_update(database_path, current_version, requested_version):
    # No URL, command, or path from the browser is ever executed.
    with _lock:
        if install_status().get('state') == 'installing':
            raise ValueError('An update is already running.')
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise ValueError('Updates require the installed Apple Silicon Mac app.')
        directory = update_dir()
        support = directory.parent
        if Path(database_path).resolve() != (support / 'data/jordana_invoice.sqlite3').resolve():
            raise ValueError('Use the installed app to update this database.')
        try:
            offer = fetch_offer(current_version)
        except Exception:
            raise ValueError('Could not verify the update. Try again later.') from None
        if not offer or offer['version'] != requested_version:
            raise ValueError('The available update changed. Check for updates again.')
        from .backups import create_verified_backup
        create_verified_backup(database_path, reason='software_update', protected=True)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        job = Path(tempfile.mkdtemp(prefix='release-', dir=directory))
        shutil.copy2(__file__, job / 'worker.py')
        (job / 'offer.json').write_text(json.dumps(offer))
        write_status(directory, state='installing', version=offer['version'], started_at=time.time(), message='Downloading and verifying update…')
        try:
            with (job / 'install.log').open('wb') as log:
                subprocess.Popen([sys._base_executable, str(job / 'worker.py'), str(job)],
                                 stdout=log, stderr=log, start_new_session=True, close_fds=True)
        except Exception:
            write_status(directory, state='failed', message='Could not start the update. Try again.')
            raise ValueError('Could not start the update. Try again.') from None
        return {'ok': True, 'installation': install_status()}


def verify_payload(payload, offer):
    manifest = json.loads((payload / 'release_manifest.json').read_text())
    if (manifest.get('version') != offer['version'] or manifest.get('release_label') != offer['release_label']
            or manifest.get('git_commit') != offer['commit'] or manifest.get('source_tree_dirty') is not False
            or manifest.get('artifact', {}).get('contains_private_data') is not False):
        raise ValueError('Release manifest does not match the promoted update.')
    for name, expected in manifest['checksums'].items():
        path = (payload / name).resolve()
        if not path.is_relative_to(payload.resolve()) or not path.is_file():
            raise ValueError('Invalid release payload path.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Release payload checksum failed.')
    if 'scripts/install_release.sh' not in manifest['checksums']:
        raise ValueError('Installer checksum is missing.')


def worker(job):
    directory = job.parent
    mount = job / 'mount'
    attached = False
    try:
        offer = json.loads((job / 'offer.json').read_text())
        image = job / 'release.dmg'
        digest = hashlib.sha256()
        download_started = time.monotonic()
        with urlopen(offer['url'], timeout=60) as response, image.open('wb') as output:
            if not response.geturl().startswith('https://'):
                raise ValueError('Insecure download redirect.')
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 512 * 1024 * 1024 or time.monotonic() - download_started > 300:
                    raise ValueError('Update download is too large.')
                digest.update(chunk); output.write(chunk)
        if digest.hexdigest() != offer['sha256']:
            raise ValueError('Update checksum failed.')
        subprocess.run(['hdiutil', 'verify', str(image)], check=True, timeout=120)
        subprocess.run(['hdiutil', 'attach', '-readonly', '-nobrowse', '-mountpoint', str(mount), str(image)], check=True, timeout=120)
        attached = True
        app = mount / 'Install Jordana Billing.app'
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True, timeout=120)
        payload = app / 'Contents/Resources/ReleasePayload'
        verify_payload(payload, offer)
        env = {**os.environ, 'JORDANA_APP_SUPPORT_DIR': str(directory.parent), 'JORDANA_INSTALL_PYTHON': sys.executable}
        # Existing installer coordinates shutdown, verifies installed identity and
        # restarts the app, rolling back its runtime if installation fails.
        subprocess.run(['bash', str(payload / 'scripts/install_release.sh'), '--yes'], env=env, check=True, timeout=900)
        write_status(directory, state='complete', version=offer['version'], message='Update installed successfully.')
    except Exception:
        write_status(directory, state='failed', message='Update could not finish. Your data is preserved. Restart the app and try again.')
        raise
    finally:
        if attached:
            subprocess.run(['hdiutil', 'detach', str(mount)], timeout=60, check=False)


if __name__ == '__main__':
    worker(Path(sys.argv[1]))
