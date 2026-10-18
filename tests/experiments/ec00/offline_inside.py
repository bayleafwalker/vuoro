"""Runs only inside the isolated restore; no credentials or host source are present."""
from __future__ import annotations
import errno
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

ROOT = Path('/work/repository')
EC00 = ROOT / 'tests/experiments/ec00'
CANDIDATES = EC00 / 'candidates'
PYTHON = Path('/work/venv/bin/python')
MANIFEST = json.loads(Path('/bundle/manifest.json').read_text())
RESULT = {'source_revision':MANIFEST['source_revision'],'scope':'offline client package restore/build on supplied pinned Nix runtimes; not OS/bootstrap/production restore','checks':[]}


def run(args, *, cwd=ROOT, good=True):
    start=time.monotonic()
    result=subprocess.run([str(x) for x in args], cwd=cwd, text=True, capture_output=True, timeout=600)
    print(result.stdout, end='');print(result.stderr, end='')
    if good and result.returncode:raise AssertionError('offline command failed: '+str(args[0]))
    RESULT['checks'].append({'command':[str(x) for x in args],'returncode':result.returncode,
                             'elapsed_seconds':round(time.monotonic()-start,3)})
    return result


def venv(path):
    run(['/opt/python/bin/python3.13','-I','-m','venv','--without-pip',path])
    assert 'include-system-site-packages = false' in (Path(path)/'pyvenv.cfg').read_text()


def install(python):
    return run(['/opt/uv','--no-config','--offline','--no-cache','pip','install','--python',python,
                '--no-index','--find-links','/bundle/wheels','--require-hashes','-r','/bundle/requirements.txt'],good=False)


def main():
    assert os.getpid()==1
    assert not Path('/projects').exists() and not Path('/home').exists()
    assert not (CANDIDATES/'node_modules').exists() and not Path('/work/venv').exists()
    assert not Path('/cache/cargo').exists() and not Path('/cache/go-build').exists()
    # Prove loopback is usable while IP egress has no route, rather than assuming an outage.
    server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1)
    def echo():
        connection,_=server.accept()
        with connection:connection.sendall(connection.recv(32))
    thread=threading.Thread(target=echo);thread.start()
    with socket.create_connection(server.getsockname(),timeout=2) as connection:
        connection.sendall(b'offline-positive-control');assert connection.recv(32)==b'offline-positive-control'
    thread.join(2);server.close()
    try:
        socket.create_connection(('1.1.1.1',443),timeout=2)
        raise AssertionError('external egress unexpectedly available')
    except OSError as exc:assert exc.errno==errno.ENETUNREACH
    RESULT['isolation']={'loopback_echo':'PASS','external_egress':'ENETUNREACH',
        'route_table':Path('/proc/net/route').read_text(),'host_source_home_absent':True,
        'fresh_venv_node_modules_build_caches':True}
    # Missing artifacts must fail despite all other artifacts being available.
    wheel=next(Path('/bundle/wheels').glob('connectrpc-*.whl'));hidden=wheel.with_suffix('.missing')
    wheel.rename(hidden)
    try:
        venv('/work/missing-wheel')
        failure=install('/work/missing-wheel/bin/python')
        assert failure.returncode and 'connectrpc' in (failure.stdout+failure.stderr).lower()
    finally: hidden.rename(wheel)
    negative=Path('/work/missing-npm');negative.mkdir()
    import shutil
    for name in ('package.json','package-lock.json'):shutil.copyfile(CANDIDATES/name,negative/name)
    flags=['--ignore-scripts','--no-audit','--no-fund','--userconfig','/bundle/empty-user.npmrc','--globalconfig','/bundle/empty-global.npmrc']
    failure=run(['npm','ci','--prefix',negative,'--offline','--cache','/cache/npm-empty',*flags],good=False)
    assert failure.returncode and 'ENOTCACHED' in failure.stdout+failure.stderr
    RESULT['falsifiers']={'missing_wheel':'refused','empty_npm_cache':'ENOTCACHED'}
    venv('/work/venv');installed=install(PYTHON);assert installed.returncode==0
    run(['npm','ci','--prefix',CANDIDATES,'--offline','--cache','/bundle/npm-cache',*flags])
    os.environ['NODE_PATH']=str(CANDIDATES/'node_modules')
    # No prior Go/Rust artifact or registry cache is inherited. Vendored sources are hash-bound.
    cargo=Path('/cache/cargo');cargo.mkdir(parents=True)
    (cargo/'config.toml').write_text('[source.crates-io]\nreplace-with="offline-vendor"\n[source.offline-vendor]\ndirectory="/bundle/cargo-vendor"\n[net]\noffline=true\n')
    assert not Path('/work/cargo-target').exists()
    run([CANDIDATES/'node_modules/.bin/tsc','--project',CANDIDATES/'tsconfig.json'])
    for pattern in ('read_presence_checks.py','read_presence_native_checks.py','versioned_native_checks.py',
                    'connect_python_checks.py','connect_browser_checks.py'):
        run([PYTHON,'-I','-c',
             'import sys,unittest; sys.path.insert(0,'+repr(str(EC00))+'); '+
             'suite=unittest.defaultTestLoader.discover('+repr(str(EC00))+',pattern='+repr(pattern)+'); '+
             'result=unittest.TextTestRunner(verbosity=2).run(suite); sys.exit(not result.wasSuccessful())'])
    RESULT['status']='PASS'
    RESULT['timing_limit']='command elapsed times are diagnostic only, not representative performance comparison'
    Path('/out/result.json').write_text(json.dumps(RESULT,indent=2)+'\n')


if __name__=='__main__':main()
