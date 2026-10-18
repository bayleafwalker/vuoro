"""Test-only v2 JSON projection; one unchanged OwnerOracle remains authoritative."""
import json
import re

from candidate_owner_http import CandidateOwnerServer, _Handler


def decode_revision(value):
    if not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise ValueError("v2 expectedRevision requires canonical nonnegative decimal text")
    number = int(value)
    if number > 9223372036854775807:
        raise ValueError("v2 expectedRevision exceeds signed int64")
    return number


class _VersionedHandler(_Handler):
    def _select_version(self):
        self._counter_v2 = self.path.startswith("/v2/")
        if self._counter_v2:
            self.path = "/v1/" + self.path[len("/v2/"):]

    def do_POST(self):
        self._select_version()
        super().do_POST()

    def do_PATCH(self):
        self._select_version()
        super().do_PATCH()

    def do_GET(self):
        self._select_version()
        super().do_GET()

    def _read(self):
        raw = super()._read()
        if self._counter_v2:
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("v2 request body must be an object")
            if "expectedRevision" in body and body["expectedRevision"] is not None:
                body["expectedRevision"] = decode_revision(body["expectedRevision"])
            return json.dumps(body, separators=(",", ":")).encode()
        return raw

    def _json(self, status, body):
        if self._counter_v2 and isinstance(body.get("revision"), int):
            body = dict(body, revision=str(body["revision"]))
        super()._json(status, body)


class VersionedCounterServer(CandidateOwnerServer):
    """v1 wire behavior plus explicit /v2 routes; no production API or fallback."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.httpd.RequestHandlerClass = _VersionedHandler
