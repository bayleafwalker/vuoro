#!/usr/bin/env python3
"""Prepare a hash-bound experimental package bundle; networking occurs only here."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[3]
EC00 = ROOT / 'tests/experiments/ec00'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def run(args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def output(args, **kwargs):
    return subprocess.check_output([str(x) for x in args], text=True, **kwargs).strip()


def unpack(archive, target):
    with tarfile.open(archive) as stream:
        stream.extractall(target, filter='data')


def verify(archive, expected):
    if not re.fullmatch('[0-9a-f]{64}', expected) or sha(archive) != expected:
        raise ValueError('offline archive differs from externally pinned SHA256')


def prepare(destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    work = destination / 'preparation'; work.mkdir()
    payload = destination / 'payload'; payload.mkdir()
    if output(['git', 'status', '--porcelain', '--untracked-files=all', '--',
               'tests/experiments/ec00', 'scripts/experiments/ec00'], cwd=ROOT):
        raise ValueError('commit experiment inputs before preparing a revision-bound bundle')
    revision = output(['git', 'rev-parse', 'HEAD'], cwd=ROOT)
    archive = payload / 'repository.tar'
    with archive.open('wb') as stream:
        run(['git', 'archive', revision, 'tests/experiments/ec00', 'scripts/experiments/ec00'], cwd=ROOT, stdout=stream)
    repository = work / 'repository'; repository.mkdir(); unpack(archive, repository)
    ec00 = repository / 'tests/experiments/ec00'
    candidates = ec00 / 'candidates'
    requirements = payload / 'requirements.txt'
    run(['uv', 'export', '--project', candidates, '--locked', '--no-emit-project',
         '--format', 'requirements-txt', '--output-file', requirements], stdout=subprocess.DEVNULL)
    wheels = payload / 'wheels'; wheels.mkdir()
    run(['uv', 'run', '--no-project', '--python', '3.13.16', '--with', 'pip==26.2.1',
         'python', '-m', 'pip', '--isolated', 'download', '--no-cache-dir', '--require-hashes',
         '--only-binary=:all:', '--index-url', 'https://pypi.org/simple', '--dest', wheels,
         '-r', requirements])
    # A portable interpreter is restored, without any inherited global packages.
    python = Path(output(['uv', 'python', 'find', '3.13.16'])).resolve()
    assert output([python, '-c', 'import sys; print(sys.version.split()[0])']) == '3.13.16'
    shutil.copytree(python.parent.parent, payload / 'python', symlinks=True,
                    ignore=shutil.ignore_patterns('site-packages', '__pycache__'))
    (payload / 'python/lib/python3.13/site-packages').mkdir(parents=True, exist_ok=True)
    uv = Path(shutil.which('uv')).resolve()
    assert output([uv, '--version']).startswith('uv 0.13.0')
    shutil.copy2(uv, payload / 'uv')
    # Resolve only public runtime libraries, not host configuration or credentials.
    run(['uv', 'sync', '--project', candidates, '--locked', '--python', '3.13.16'])
    playwright_node = candidates / '.venv/lib/python3.13/site-packages/playwright/driver/node'
    if not playwright_node.is_file(): raise ValueError('install the locked candidate runtime first')
    libraries = payload / 'fhs-libraries'; libraries.mkdir()
    library_provenance = {}
    loader = Path(os.environ['EC00_FHS_LOADER'])
    library_path = os.environ['EC00_FHS_LIBRARY_PATH']
    # Resolve with the locked loader and compiler runtime, never host ldd's search path.
    for executable in (python, playwright_node):
        listing = output([loader, '--library-path', library_path, '--list', executable])
        if 'not found' in listing: raise ValueError('unresolved pinned runtime library')
        for line in listing.splitlines():
            match = re.match(r'\s*(\S+)\s+=>\s+(/\S+)\s+\(', line)
            if match:
                name, path = Path(match[1]).name, Path(match[2]).resolve()
            else:
                match = re.match(r'\s*(/\S+)\s+\(', line)
                if not match: continue
                name, path = Path(match[1]).name, Path(match[1]).resolve()
            if not path.is_file(): raise ValueError('unresolved runtime library')
            target = libraries / name
            if target.exists():
                if sha(target) != sha(path): raise ValueError('conflicting pinned runtime SONAME: ' + name)
            else:
                shutil.copy2(path, target)
                library_provenance[name] = {'source': str(path), 'sha256': sha(path)}
    ca = Path('/etc/ssl/certs/ca-certificates.crt').read_bytes()
    if b'PRIVATE KEY' in ca: raise ValueError('CA bundle contains private material')
    (payload / 'ca-certificates.crt').write_bytes(ca)
    for name in ('empty-user.npmrc', 'empty-global.npmrc'):(payload / name).write_bytes(b'')
    npm_cache = payload / 'npm-cache'
    run(['npm', 'ci', '--prefix', candidates, '--ignore-scripts', '--no-audit', '--no-fund',
         '--cache', npm_cache, '--userconfig', payload / 'empty-user.npmrc',
         '--globalconfig', payload / 'empty-global.npmrc'])
    shutil.rmtree(npm_cache / '_logs', ignore_errors=True)
    (npm_cache / '_update-notifier-last-checked').unlink(missing_ok=True)
    env = {**os.environ, 'GOTOOLCHAIN': 'local', 'GOMODCACHE': str(work / 'go-mod-cache'),
           'GOCACHE': str(work / 'go-build-cache'), 'CARGO_HOME': str(work / 'cargo-home')}
    go_inputs = {name: (ec00 / name).read_bytes() for name in ('go.mod', 'go.sum')}
    run([os.environ['EC00_GO'], 'mod', 'vendor'], cwd=ec00, env=env)
    assert all((ec00 / name).read_bytes() == raw for name, raw in go_inputs.items())
    shutil.move(ec00 / 'vendor', payload / 'go-vendor')
    lock = ec00 / 'candidates/rust-client/Cargo.lock'; old_lock = lock.read_bytes()
    run([os.environ['EC00_CARGO'], 'vendor', '--locked', '--manifest-path',
         ec00 / 'candidates/rust-client/Cargo.toml', payload / 'cargo-vendor'], cwd=repository, env=env)
    assert lock.read_bytes() == old_lock
    for name in ('offline_inside.py', 'offline_namespace.py'):
        shutil.copy2(ec00 / name, payload / name)
    inputs = {'schema_version': 'ec00-offline-bundle/v1', 'source_revision': revision,
              'platform': 'x86_64-linux; prepared pinned Nix runtime store is a prerequisite',
              'python': '3.13.16', 'uv': output([uv, '--version']), 'pip_preparation': '26.2.1',
              'library_provenance': library_provenance,
              'playwright_node_sha256': sha(playwright_node),
              'runtimes': {k: os.environ[k] for k in ('EC00_OFFLINE_PATH','EC00_BASH','EC00_ENV','EC00_CHROOT','EC00_UNSHARE','EC00_MOUNT','EC00_IP','EC00_FHS_LOADER','EC00_FHS_LIBRARY_PATH','EC00_NODE','EC00_GO','EC00_CARGO','EC00_CHROMIUM','EC00_PROTOC','PROTOC','PROTOC_INCLUDE')},
              'source_archive_sha256': sha(archive), 'files': {}}
    for path in sorted(payload.rglob('*')):
        relative = path.relative_to(payload).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            if target.startswith('/') or not path.resolve().is_relative_to(payload.resolve()):
                raise ValueError('payload symlink escapes the bundle')
            inputs['files'][relative] = {'symlink': target}
        elif path.is_file(): inputs['files'][relative] = {'sha256': sha(path)}
        elif not path.is_dir(): raise ValueError('unsupported payload file type')
    (payload / 'manifest.json').write_text(json.dumps(inputs, indent=2) + '\n')
    bundle = destination / 'offline-packages.tar'
    with tarfile.open(bundle, 'w') as stream: stream.add(payload, arcname='payload')
    receipt = {'bundle': str(bundle), 'sha256': sha(bundle), 'bytes': bundle.stat().st_size,
               'source_revision': revision, 'manifest_sha256': sha(payload / 'manifest.json')}
    (destination / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


def run_bundle(archive, expected, report):
    verify(archive, expected)
    # The external archive pin authenticates the launcher before any of its code executes.
    with tarfile.open(archive) as stream:
        launcher = stream.extractfile('payload/offline_namespace.py').read()
    namespace = {'__name__': 'ec00_offline_namespace'}
    exec(compile(launcher, 'verified-bundle/offline_namespace.py', 'exec'), namespace)
    namespace['restore_and_run'](archive, expected, report)


def main():
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('prepare'); command.add_argument('--destination', type=Path, required=True)
    command = sub.add_parser('run'); command.add_argument('--archive',type=Path,required=True)
    command.add_argument('--sha256',required=True);command.add_argument('--report',type=Path,required=True)
    args = parser.parse_args()
    if args.command=='prepare':prepare(args.destination)
    else:run_bundle(args.archive,args.sha256,args.report)


if __name__ == '__main__': main()
