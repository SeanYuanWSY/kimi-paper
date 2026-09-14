#!/usr/bin/env python3
"""Prune only old build backups; dry-run unless --apply is supplied."""
import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
import re
import shutil
import subprocess


def candidates(dist, include_installed=False):
    if dist.is_symlink() or not dist.is_dir():
        raise RuntimeError('dist must be a real directory')
    previous, installed = [], []
    for path in dist.iterdir():
        if re.fullmatch(r'previous-[A-Za-z0-9]{6}', path.name):
            previous.append(path)
        elif include_installed and re.fullmatch(r'installed-backup-[0-9-]+', path.name):
            installed.append(path)
    previous.sort(key=lambda p: (p.stat().st_birthtime, p.name), reverse=True)
    return previous[:2], previous[2:] + sorted(installed)


def validate(path):
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError(f'Not a real backup directory: {path}')
    if {p.name for p in path.iterdir()} - {'Kimi Paper.app', '.DS_Store'}:
        raise RuntimeError(f'Unexpected contents: {path}')
    metadata = path / '.DS_Store'
    if metadata.is_symlink() or (metadata.exists() and not metadata.is_file()):
        raise RuntimeError(f'Unexpected Finder metadata: {path}')
    app = path / 'Kimi Paper.app'
    if app.is_symlink() or not app.is_dir():
        raise RuntimeError(f'Invalid app: {path}')
    for name in ['Contents/Info.plist', 'Contents/MacOS/KimiPaper']:
        if not (app / name).is_file():
            raise RuntimeError(f'Incomplete app: {path}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--include-installed', action='store_true', help='Explicit one-time cleanup of installation backups')
    parser.add_argument('--build', choices=['build-only', 'run'])
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    dist = root / 'dist'
    with (root / '.build-backup-prune.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.build:
            env = dict(os.environ, KIMI_BUILD_LOCK_HELD='1')
            subprocess.run(['/bin/bash', str(root / 'scripts/build.sh'), '--build-only'], env=env, check=True)
            args.apply = True
        try:
            keep, remove = candidates(dist, args.include_installed)
            for path in keep + remove:
                validate(path)
            # Fail closed before deleting any files if lsof cannot establish safety.
            stamps = {p: (p.stat().st_dev, p.stat().st_ino, p.stat().st_mtime_ns) for p in remove}
            for path in remove:
                result = subprocess.run(['/usr/sbin/lsof', '-nP', '-t', '+D', str(path)], capture_output=True, text=True, timeout=30)
                if result.returncode != 1 or result.stdout.strip() or result.stderr.strip():
                    raise RuntimeError(f'Backup is open or could not be checked: {path}')
            print(json.dumps({'keep': [str(p) for p in keep], 'remove': [str(p) for p in remove], 'apply': args.apply}, ensure_ascii=False), flush=True)
            if args.apply:
                for path in remove:
                    validate(path)
                    if stamps[path] != (path.stat().st_dev, path.stat().st_ino, path.stat().st_mtime_ns):
                        raise RuntimeError(f'Backup changed: {path}')
                    check = subprocess.run(['/usr/sbin/lsof', '-nP', '-t', '+D', str(path)], capture_output=True, text=True, timeout=30)
                    if check.returncode != 1 or check.stdout.strip() or check.stderr.strip():
                        raise RuntimeError(f'Backup became open or could not be checked: {path}')
                    shutil.rmtree(path)
                    print('Removed ' + path.name, flush=True)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            if not args.build:
                raise
            print(f'Build succeeded, but backup retention was not completed: {error}', file=sys.stderr)
        if args.build == 'run':
            subprocess.run(['/usr/bin/open', str(dist / 'Kimi Paper.app')], check=True)


if __name__ == '__main__':
    main()
