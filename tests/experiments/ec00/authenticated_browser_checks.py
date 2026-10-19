"""Actual Chromium carries only disposable assertions through candidate transports."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest

import connect_browser_checks as browser_fixture
from browser_fixture import CorsProxy
from authenticated_transport_checks import FixtureIdentity, endpoint, snapshot, ISSUER


class AuthenticatedBrowserTests(unittest.TestCase):
    setUp = browser_fixture.ConnectBrowserTests.setUp
    @classmethod
    def setUpClass(cls):
        browser_fixture.ConnectBrowserTests.setUpClass.__func__(cls)
    @classmethod
    def tearDownClass(cls):
        browser_fixture.ConnectBrowserTests.tearDownClass.__func__(cls)

    def invoke(self, arm, url, data, headers):
        return self.page.evaluate('''async a => a.arm === 'http-v2'
            ? await window.ec00Http(a.url, 'publish', a.data, 'browser-auth', 5000, 0, a.headers)
            : await window.ec00Rpc(a.url, a.arm.endsWith('binary'), 'publish', a.data, 'browser-auth', 5000, 0, a.headers)''',
            dict(arm=arm, url=url, data=data, headers=headers))

    def test_signed_identity_missing_replayed_and_fresh_assertion_retry(self):
        for arm in ('http-v2','python-json','python-binary','edge-json','edge-binary'):
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                path = Path(directory) / 'owner.sqlite'
                identity = FixtureIdentity(); identity.initialize(path)
                transport = stack.enter_context(endpoint(path, identity, arm))
                proxy = stack.enter_context(CorsProxy(transport.url, self.origin.base_url))
                data = {'requestId':'9007199254741401','objective':'browser identity',
                    'dependants' if arm=='http-v2' else 'dependentIds':['work-alice']}
                before = snapshot(path)
                absent = self.invoke(arm, proxy.base_url, data, {})
                self.assertFalse(absent['ok'], absent)
                self.assertEqual(401 if arm=='http-v2' else 16, absent.get('status', absent.get('code')))
                self.assertEqual(before, snapshot(path))
                headers = identity.headers()
                first = self.invoke(arm, proxy.base_url, data, headers)
                self.assertTrue(first['ok'], first)
                committed = snapshot(path)
                self.assertEqual((1,1,1), tuple(len(committed[t]) for t in ('request','event','idem')))
                self.assertEqual(f'{ISSUER}:alice:0', committed['idem'][0][1])
                replay = self.invoke(arm, proxy.base_url, data, headers)
                self.assertFalse(replay['ok'], replay)
                self.assertEqual(401 if arm=='http-v2' else 16, replay.get('status', replay.get('code')))
                fresh = self.invoke(arm, proxy.base_url, data, identity.headers())
                self.assertTrue(fresh['ok'], fresh)
                self.assertEqual(committed, snapshot(path))
                self.assertTrue(any(row['method']=='OPTIONS' and row['allowed'] for row in proxy.wire))
                # Wire receipts deliberately contain bodies/status, never assertions.
                self.assertNotIn(headers['x-vuoro-identity'], repr(proxy.wire))


if __name__ == '__main__':
    unittest.main()
