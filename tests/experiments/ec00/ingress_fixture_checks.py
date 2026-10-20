"""The ingress workload and its remote-mode client, exercised over loopback.

This is the local half of the ingress qualification: the same serve_fixture.py
the container runs, started as a subprocess with a disposable public key, and
remote_checks.py run against it with --allow-plaintext-loopback. TLS and the
real Gateway are exercised only by the operator run in ingress/README.md.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'ingress'))
import remote_checks


def free_ports(count):
    sockets = [socket.socket() for _ in range(count)]
    try:
        for s in sockets:
            s.bind(('127.0.0.1', 0))
        return [s.getsockname()[1] for s in sockets]
    finally:
        for s in sockets:
            s.close()


@contextmanager
def fixture(public_key: Path, state: Path):
    http, connect, grpc_port = free_ports(3)
    env = {**os.environ, 'EC00_BIND': '127.0.0.1', 'EC00_HTTP_PORT': str(http),
           'EC00_CONNECT_PORT': str(connect), 'EC00_GRPC_PORT': str(grpc_port),
           'EC00_STATE_DIR': str(state), 'EC00_PUBLIC_KEY_FILE': str(public_key)}
    process = subprocess.Popen([sys.executable, str(HERE / 'ingress/serve_fixture.py')], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        if not select.select([process.stdout], [], [], 20)[0]:
            raise AssertionError('fixture readiness timed out')
        line = process.stdout.readline()
        if not line:
            raise AssertionError('fixture exited: ' + process.stderr.read()[-2000:])
        assert json.loads(line)['ready']
        yield f'http://127.0.0.1:{http}', f'http://127.0.0.1:{connect}', f'127.0.0.1:{grpc_port}'
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class IngressFixtureTests(unittest.TestCase):
    def test_remote_mode_checks_pass_against_the_workload_over_loopback(self):
        with tempfile.TemporaryDirectory() as directory:
            keys, state = Path(directory) / 'keys', Path(directory) / 'state'
            state.mkdir()
            remote_checks.main(['keygen', '--directory', str(keys)])
            report = Path(directory) / 'report.json'
            with fixture(keys / 'fixture-public.pem', state) as (http, connect, grpc_target):
                status = remote_checks.main(['run', '--http-url', http, '--connect-url', connect,
                    '--grpc-target', grpc_target, '--private-key', str(keys / 'fixture-private.pem'),
                    '--allow-plaintext-loopback', '--samples', '3', '--report', str(report)])
            result = json.loads(report.read_text())
            self.assertEqual([], result['invariant_failures'])
            self.assertEqual(0, status)
            cases = {(c['arm'], c['case']): c for c in result['cases']}
            for arm in remote_checks.ARMS:
                self.assertTrue(cases[(arm, 'signed-publication')]['correlation']['preserved'])
                self.assertEqual('repeated', cases[(arm, 'repeated-x-vuoro-identity-different')]['arrived_as'])
                self.assertTrue(cases[(arm, 'envelope')]['passed'])
                self.assertTrue(cases[(arm, 'concurrent-16x8-no-identity-carry-over')]['passed'])
            # No assertion value is ever echoed into the report.
            self.assertNotIn('eyJ', report.read_text())

    def test_workload_refuses_private_key_and_existing_store(self):
        with tempfile.TemporaryDirectory() as directory:
            keys, state = Path(directory) / 'keys', Path(directory) / 'state'
            state.mkdir()
            remote_checks.main(['keygen', '--directory', str(keys)])
            env = {**os.environ, 'EC00_STATE_DIR': str(state), 'EC00_BIND': '127.0.0.1',
                   'EC00_PUBLIC_KEY_FILE': str(keys / 'fixture-private.pem')}
            refused = subprocess.run([sys.executable, str(HERE / 'ingress/serve_fixture.py')], env=env,
                                     capture_output=True, text=True, timeout=30)
            self.assertNotEqual(0, refused.returncode)
            self.assertIn('refusing a private key', refused.stderr)
            (state / 'owner.sqlite').write_bytes(b'')
            env['EC00_PUBLIC_KEY_FILE'] = str(keys / 'fixture-public.pem')
            refused = subprocess.run([sys.executable, str(HERE / 'ingress/serve_fixture.py')], env=env,
                                     capture_output=True, text=True, timeout=30)
            self.assertIn('fresh disposable owner database required', refused.stderr)

    def test_remote_mode_refuses_plaintext_to_non_loopback(self):
        with self.assertRaises(SystemExit):
            remote_checks.main(['run', '--http-url', 'http://ec00-qualification.apps.kotona.app',
                '--connect-url', 'https://x.example', '--grpc-target', 'x.example:443',
                '--private-key', '/nonexistent', '--allow-plaintext-loopback'])


if __name__ == '__main__':
    unittest.main()
