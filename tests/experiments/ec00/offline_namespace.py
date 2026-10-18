"""Launch an isolated offline restore; supplied Nix runtime store is a prerequisite."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):result.update(block)
    return result.hexdigest()


def extract(archive, target):
    with tarfile.open(archive) as stream:stream.extractall(target, filter='data')


def restore_and_run(archive, expected, report):
    if digest(archive) != expected:raise ValueError('offline archive differs from externally pinned SHA256')
    with tempfile.TemporaryDirectory(prefix='ec00-offline-restore-') as directory:
        temporary = Path(directory); extract(archive, temporary)
        payload = temporary / 'payload'; manifest = json.loads((payload / 'manifest.json').read_text())
        actual = {p.relative_to(payload).as_posix() for p in payload.rglob('*') if p.is_file() or p.is_symlink()}
        if actual != set(manifest['files']) | {'manifest.json'}:raise ValueError('unmanifested offline payload file')
        for name, entry in manifest['files'].items():
            path = payload / name
            if 'symlink' in entry:
                if not path.is_symlink() or os.readlink(path) != entry['symlink']:raise ValueError('offline symlink drift')
            elif not path.is_file() or path.is_symlink() or digest(path) != entry['sha256']:
                raise ValueError('offline payload file hash mismatch: ' + name)
        root = temporary / 'root'
        for name in ('nix/store','opt','dev/shm','proc','etc/ssl/certs','bin','usr/bin','work','out','tmp/root','cache'):
            (root / name).mkdir(parents=True, exist_ok=True)
        shutil.move(payload, root / 'bundle'); payload = root / 'bundle'
        shutil.move(payload / 'python', root / 'opt/python'); shutil.move(payload / 'uv', root / 'opt/uv')
        shutil.move(payload / 'fhs-libraries', root / 'lib64')
        (root / 'lib').symlink_to('lib64')
        shutil.copyfile(payload / 'ca-certificates.crt', root / 'etc/ssl/certs/ca-certificates.crt')
        (root / 'etc/ld.so.conf').write_text('/lib64\n')
        (root / 'etc/passwd').write_text('root:x:0:0:offline fixture:/tmp/root:/bin/sh\n')
        (root / 'etc/group').write_text('root:x:0:\n')
        (root / 'etc/hosts').write_text('127.0.0.1 localhost\n::1 localhost\n')
        (root / 'etc/nsswitch.conf').write_text('hosts: files\npasswd: files\ngroup: files\n')
        repository = root / 'work/repository'; repository.mkdir();extract(payload / 'repository.tar', repository)
        ec00 = repository / 'tests/experiments/ec00'
        shutil.move(payload / 'go-vendor', ec00 / 'vendor')
        shutil.copyfile(payload / 'offline_inside.py', root / 'work/offline_inside.py')
        runtime = manifest['runtimes']
        bash = Path(runtime['EC00_BASH'])
        env_tool = Path(runtime['EC00_ENV'])
        if not str(bash).startswith('/nix/store/') or not str(env_tool).startswith('/nix/store/'):
            raise ValueError('run inside the pinned Nix toolchain')
        (root / 'bin/sh').symlink_to(bash);(root / 'bin/bash').symlink_to(bash)
        (root / 'usr/bin/env').symlink_to(env_tool)
        for name in ('null','urandom','random'):(root / 'dev' / name).touch()
        runtime = manifest['runtimes']
        if any(not value.startswith('/nix/store/') for value in runtime.values()):
            raise ValueError('qualified runtime prerequisites must be immutable Nix paths')
        path = runtime['EC00_OFFLINE_PATH']
        if any(not p.startswith('/nix/store/') for p in path.split(':')):
            raise ValueError('offline PATH contains an unpinned executable directory')
        environment = {'PATH': '/work/venv/bin:/opt/python/bin:' + path,
            **runtime, 'LD_LIBRARY_PATH':'/lib64', 'SSL_CERT_FILE':'/etc/ssl/certs/ca-certificates.crt',
            'UV_CACHE_DIR':'/cache/uv', 'UV_NO_CONFIG':'1', 'UV_PYTHON_DOWNLOADS':'never',
            'XDG_CACHE_HOME':'/cache/xdg', 'TSX_DISABLE_CACHE':'1',
            'GOTOOLCHAIN':'local','GOPROXY':'off','GOSUMDB':'off','GOFLAGS':'-mod=vendor',
            'GOMODCACHE':'/cache/go-modules','GOCACHE':'/cache/go-build','CGO_ENABLED':'0',
            'CARGO_HOME':'/cache/cargo','CARGO_TARGET_DIR':'/work/cargo-target',
            'CARGO_NET_OFFLINE':'true','CARGO_BUILD_JOBS':'2','LANG':'C.UTF-8'}
        assignments = [key + '=' + value for key, value in environment.items()]
        # PID 1 enters the filesystem root; no outside-root process is visible through /proc.
        script = '\n'.join([
            shlex.join([runtime['EC00_MOUNT'], '--make-rprivate', '/']),
            shlex.join([runtime['EC00_IP'], 'link', 'set', 'lo', 'up']),
            shlex.quote(runtime['EC00_MOUNT']) + ' --bind /nix/store ' + shlex.quote(str(root / 'nix/store')),
            shlex.quote(runtime['EC00_MOUNT']) + ' -o remount,bind,ro ' + shlex.quote(str(root / 'nix/store')),
            shlex.quote(runtime['EC00_MOUNT']) + ' -t proc proc ' + shlex.quote(str(root / 'proc')),
            shlex.quote(runtime['EC00_MOUNT']) + ' -t tmpfs -o size=256m,mode=1777 tmpfs ' + shlex.quote(str(root / 'dev/shm')),
            *[shlex.quote(runtime['EC00_MOUNT']) + ' --bind /dev/' + name + ' ' + shlex.quote(str(root / 'dev' / name)) for name in ('null','urandom','random')],
            'exec ' + shlex.join([runtime['EC00_CHROOT'], str(root), str(env_tool), '-i', *assignments,
                '/opt/python/bin/python3.13', '-I', '/work/offline_inside.py'])])
        result = subprocess.run([runtime['EC00_UNSHARE'],'--user','--map-root-user','--mount','--pid','--fork','--net',
                                 str(bash),'-ec',script], text=True, capture_output=True, timeout=1200)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.with_suffix('.stdout.log').write_text(result.stdout)
        report.with_suffix('.stderr.log').write_text(result.stderr)
        if result.returncode:
            raise RuntimeError('offline restore failed; see captured logs, returncode=' + str(result.returncode))
        shutil.copyfile(root / 'out/result.json', report)
        print(json.dumps({'archive_sha256':expected,'report':str(report),'status':'PASS'}))
