"""Selected-chain unknown-value qualification; failures are retained findings.

No generated decoder, IDL, frozen artifact or OwnerOracle policy is repaired here.
The matrix uses real generated HTTP clients and one unchanged persisted owner.
"""
from contextlib import closing
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import httpx

from artifact_semantics import (
    _TEST_PRIVATE_KEY, canonical_bytes, make_signed_artifact, transport_fields,
)
from candidate_owner_http import initialize_fixture, persisted_artifact
from owner_oracle import OwnerOracle, material_digest
from versioned_counter_http import VersionedCounterServer, _VersionedHandler

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "generated/typespec-v2/clients/python"))
from ec00_oa_v2_python_client.client import Client
from ec00_oa_v2_python_client.api.default import read_request, read_artifact

REQUEST_ID = "9007199254741199"
LARGE_REVISION = "9007199254740995"
ORIGINAL = b'\x00{"futureSchema":{"opaque":"preserve exact spaces"} }\xff'
VARIANTS = ("baseline", "additive-top", "additive-nested", "unknown-state")
LANGUAGES = ("python", "typescript", "go", "rust")


def snapshot(path):
    with closing(sqlite3.connect(path)) as database:
        return {table: database.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
                for table in ("request", "event", "idem", "dependent", "signed_artifact")}


def snapshot_digest(value):
    # Reporting representation only. This does not redefine signed meaning.
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        default=lambda item: {"bytes_hex": item.hex()}).encode()).hexdigest()


def seed(path, *, large_revision=False):
    initialize_fixture(path)
    body = {"objective": "", "note": None}
    signed = make_signed_artifact(workspace_id="workspace-a", request_id=REQUEST_ID,
        material_revision=1, material_digest=material_digest(body), original_bytes=ORIGINAL)
    owner = OwnerOracle.connect_as(path, "requester-a")
    try:
        owner.publish(REQUEST_ID, body, ["work-a"], "seed", signed_artifact=signed)
    finally:
        owner.close()
    if large_revision:
        with closing(sqlite3.connect(path)) as database:
            database.execute("UPDATE request SET revision=? WHERE request_id=?",
                             (int(LARGE_REVISION), REQUEST_ID))
            database.commit()
    return signed


class ResponseVariantHandler(_VersionedHandler):
    """Inject only HTTP read projection faults; never mutate owner state."""
    def _json(self, status, body):
        body = copy.deepcopy(body)
        if status == 200 and "state" in body and "body" in body:
            variant = self.server.unknown_variant
            if variant == "additive-top":
                body["futureMetadata"] = {"diagnostic": "safe additive fixture"}
            elif variant == "additive-nested":
                body["body"]["futureMetadata"] = {"diagnostic": "safe additive fixture"}
            elif variant == "unknown-state":
                body["state"] = "future-success"
        self.server.observed_wire.append({"path": self.path, "status": status,
                                          "body": copy.deepcopy(body)})
        super()._json(status, body)


class ResponseVariantServer(VersionedCounterServer):
    def __init__(self, path, variant):
        super().__init__(path)
        self.httpd.RequestHandlerClass = ResponseVariantHandler
        self.httpd.unknown_variant = variant
        self.httpd.observed_wire = []


def python_read(base_url):
    def observe(function):
        try:
            response = function()
            if response.status_code != 200 or response.parsed is None:
                return {"decoded": False, "error": f"HTTP {response.status_code}"}
            return {"decoded": True, "value": response.parsed.to_dict()}
        except (ValueError, KeyError, TypeError) as error:
            return {"decoded": False, "error": type(error).__name__}
    with Client(base_url=base_url, timeout=httpx.Timeout(5)) as client:
        return {"language": "python",
            "request": observe(lambda: read_request.sync_detailed(REQUEST_ID, client=client)),
            "artifact": observe(lambda: read_artifact.sync_detailed(REQUEST_ID, "1", client=client))}


def native_read(language, base_url):
    candidates = HERE / "candidates"
    env = {**os.environ, "EC00_BASE_URL": base_url, "EC00_UNKNOWN_VALUES": "1",
        "CARGO_NET_OFFLINE": "true", "GOPROXY": "off", "GOSUMDB": "off", "GOTOOLCHAIN": "local"}
    if language == "typescript":
        binary = env.get("EC00_NODE")
        if not binary:
            raise RuntimeError("EC00_NODE missing: pinned runtime not checked")
        env["PATH"] = str(Path(binary).parent) + os.pathsep + env["PATH"]
        env["NODE_PATH"] = str(candidates / "node_modules")
        command = [str(candidates / "node_modules/.bin/tsx"), str(candidates / "run_unknown_values.ts")]
    elif language == "go":
        if not env.get("EC00_GO"):
            raise RuntimeError("EC00_GO missing: pinned runtime not checked")
        command = [env["EC00_GO"], "run", "-mod=readonly", "./candidates/go-client-v2"]
    elif language == "rust":
        if not env.get("EC00_CARGO"):
            raise RuntimeError("EC00_CARGO missing: pinned runtime not checked")
        command = [env["EC00_CARGO"], "run", "--quiet", "--offline", "--locked",
            "--manifest-path", str(candidates / "rust-client/Cargo.toml"), "--bin", "counter_v2"]
    else:
        raise ValueError("unknown native language")
    process = subprocess.run(command, cwd=HERE, env=env, capture_output=True, text=True, timeout=300)
    if process.returncode:
        raise RuntimeError(f"{language} runtime failed, not a decoder result: {process.stderr[-3000:]}")
    value = json.loads(process.stdout)
    if value.get("language") != language:
        raise ValueError("runner language mismatch")
    return value


def read_findings(row, expected_artifact):
    """Guard is independent of decoder implementations and refuses bad controls."""
    findings = []
    prefix = f"{row['variant']}/{row['language']}"
    wire = row.get("wire", [])
    expected_paths = ["/v1/requests/" + REQUEST_ID,
                      "/v1/requests/" + REQUEST_ID + "/artifact/1"]
    if [entry.get("path") for entry in wire] != expected_paths or any(
            entry.get("status") != 200 for entry in wire):
        findings.append(prefix + ": both exact generated read requests must reach the fixture with HTTP 200")
    elif wire[0]["body"].get("state") != (
            "future-success" if row["variant"] == "unknown-state" else "open"):
        findings.append(prefix + ": response variant was not exercised")
    request = row["request"]
    if row["variant"] == "unknown-state":
        if request.get("decoded"):
            findings.append(prefix + ": unknown lifecycle state decoded as a successful typed result")
    elif not request.get("decoded"):
        findings.append(prefix + ": baseline/additive metadata refused by generated decoder")
    else:
        value = request["value"]
        required = {"requestId": REQUEST_ID, "revision": LARGE_REVISION,
            "materialRevision": "1", "state": "open", "reported": False, "verified": False}
        if any(value.get(key) != expected for key, expected in required.items()):
            findings.append(prefix + ": known response meaning changed")
        if value.get("body", {}).get("objective") != "" or value.get("body", {}).get("note", "absent") is not None:
            findings.append(prefix + ": empty/null body meaning changed")
    artifact = row["artifact"]
    if not artifact.get("decoded"):
        findings.append(prefix + ": original artifact was not decoded")
    else:
        for field in ("originalBytes", "envelopeBytes", "signature", "artifactDigest", "materialDigest", "keyId", "materialRevision"):
            if artifact["value"].get(field) != expected_artifact[field]:
                findings.append(prefix + ": original artifact field changed: " + field)
    if not row.get("owner_unchanged"):
        findings.append(prefix + ": read changed owner state")
    return findings


def command_findings(row):
    if row["case"] == "known-command-control":
        return [] if row["status"] == 200 and not row["owner_unchanged"] else ["known command control failed"]
    return [] if row["status"] in (400, 409, 422) and row["owner_unchanged"] else [
        f"{row['case']}: expected refusal without owner effect; HTTP {row['status']}, unchanged={row['owner_unchanged']}"]


def command_case(directory, case):
    path = directory / (case + ".sqlite")
    seed(path)
    before = snapshot(path)
    method, route = "PATCH", "/v2/requests/" + REQUEST_ID
    body = {"requestId": REQUEST_ID, "expectedRevision": "1", "objective": "changed", "note": ""}
    if case == "unknown-command-field":
        body["futureAuthority"] = {"grant": True}
    elif case == "missing-revision":
        del body["expectedRevision"]
    elif case == "null-revision":
        body["expectedRevision"] = None
    elif case == "zero-revision":
        body["expectedRevision"] = "0"
    elif case in ("unknown-nested-signed-field", "unknown-authority-intent"):
        method, route = "POST", "/v2/requests"
        new_id = "9007199254741299"
        semantic_body = {"objective": "signed command"}
        signed = make_signed_artifact(workspace_id="workspace-a", request_id=new_id,
            material_revision=1, material_digest=material_digest(semantic_body), original_bytes=ORIGINAL)
        envelope = json.loads(signed["envelope_bytes"])
        if case == "unknown-nested-signed-field":
            envelope["futureAuthority"] = {"grant": True}
        else:
            envelope["intent"] = "future-authority"
        signed["envelope_bytes"] = canonical_bytes(envelope)
        # A valid fixture signature isolates meaning/shape refusal from bad signatures.
        signed["signature"] = _TEST_PRIVATE_KEY.sign(signed["envelope_bytes"])
        body = {"requestId": new_id, **semantic_body, "dependants": ["work-a"], **transport_fields(signed)}
    elif case != "known-command-control":
        raise ValueError("unfrozen command case")
    with VersionedCounterServer(path) as server:
        response = httpx.request(method, server.base_url + route, json=body,
            headers={"Idempotency-Key": "q1-" + case}, timeout=5, trust_env=False)
    after = snapshot(path)
    return {"case": case, "transport": "raw HTTP to unchanged candidate handler/owner",
        "status": response.status_code, "wire_request": body, "wire_response": response.json(),
        "owner_unchanged": before == after, "before_sha256": snapshot_digest(before),
        "after_sha256": snapshot_digest(after),
        "owner_counts_before": {table: len(rows) for table, rows in before.items()},
        "owner_counts_after": {table: len(rows) for table, rows in after.items()}}


def collect_matrix(directory):
    path = directory / "reads.sqlite"
    signed = seed(path, large_revision=True)
    before = snapshot(path)
    expected = {
        "materialRevision": "1", "materialDigest": material_digest({"objective": "", "note": None}),
        "artifactDigest": signed["artifact_digest"], "keyId": signed["key_id"],
        **{field: base64.b64encode(signed[source]).decode() for field, source in (
            ("originalBytes", "original_bytes"), ("envelopeBytes", "envelope_bytes"), ("signature", "signature"))}}
    rows, infrastructure = [], []
    for variant in VARIANTS:
        with ResponseVariantServer(path, variant) as server:
            for language in LANGUAGES:
                server.httpd.observed_wire.clear()
                try:
                    row = python_read(server.base_url) if language == "python" else native_read(language, server.base_url)
                except Exception as error:
                    infrastructure.append({"variant": variant, "language": language,
                        "status": "could-not-check", "error": str(error)[-3500:]})
                    continue
                row.update(variant=variant, wire=copy.deepcopy(server.httpd.observed_wire),
                    owner_unchanged=before == snapshot(path))
                rows.append(row)
    cases = ("known-command-control", "unknown-command-field", "unknown-nested-signed-field",
             "unknown-authority-intent", "missing-revision", "null-revision", "zero-revision")
    commands = [command_case(directory, case) for case in cases]
    findings = [finding for row in rows for finding in read_findings(row, expected)]
    findings.extend(finding for row in commands for finding in command_findings(row))
    if persisted_artifact(path, REQUEST_ID, 1) != (
            signed["original_bytes"], signed["envelope_bytes"], signed["signature"], signed["artifact_digest"]):
        findings.append("persisted original signed artifact changed")
    return {"schema": "ec00-unknown-values-characterization/v1", "source_base": "1eae8ace228af39bd224565bac5be0a1fb7c4f25",
        "scope": "Disposable loopback generated response decoders and raw command/signed-envelope inputs; no production, ingress or performance claim",
        "limitations": ["Flat candidate command schema has no ordinary nested model; nested input coverage is the signed envelope only",
                         "Four-language response decoder coverage; raw command probes do not qualify four-language request serialization"],
        "expected_artifact": expected, "rows": rows, "commands": commands,
        "could_not_check": infrastructure, "findings": findings,
        "qualified": not findings and not infrastructure and len(rows) == len(VARIANTS) * len(LANGUAGES),
        "toolchain_environment": {key: os.environ.get(key) for key in ("EC00_NODE", "EC00_GO", "EC00_CARGO")}}


class UnknownValueQualificationTests(unittest.TestCase):
    def test_selected_chain_unknown_value_policy(self):
        with tempfile.TemporaryDirectory(prefix="ec00-unknown-values-") as work:
            report = collect_matrix(Path(work))
        destination = os.environ.get("EC00_UNKNOWN_REPORT")
        if destination:
            Path(destination).write_text(json.dumps(report, indent=2) + "\n")
        self.assertFalse(report["could_not_check"], "Runtime cases could not be checked: " + json.dumps(report["could_not_check"]))
        self.assertEqual(16, len(report["rows"]))
        self.assertFalse(report["findings"], "Selected policy is NOT qualified:\n" + "\n".join(report["findings"]))

    def test_command_guard_rejects_silent_effect_and_bad_positive_control(self):
        self.assertTrue(command_findings({"case": "unknown-command-field", "status": 200, "owner_unchanged": False}))
        self.assertTrue(command_findings({"case": "unknown-command-field", "status": 422, "owner_unchanged": False}))
        self.assertTrue(command_findings({"case": "known-command-control", "status": 422, "owner_unchanged": True}))
        self.assertEqual([], command_findings({"case": "unknown-command-field", "status": 422, "owner_unchanged": True}))

    def test_read_guard_rejects_unknown_success_changed_bytes_and_missing_control(self):
        expected = {field: "original" for field in ("originalBytes", "envelopeBytes", "signature", "artifactDigest", "materialDigest", "keyId", "materialRevision")}
        row = {"variant": "unknown-state", "language": "fixture", "request": {"decoded": False},
            "artifact": {"decoded": True, "value": copy.deepcopy(expected)}, "owner_unchanged": True,
            "wire": [{"path": "/v1/requests/" + REQUEST_ID, "status": 200,
                      "body": {"state": "future-success"}},
                     {"path": "/v1/requests/" + REQUEST_ID + "/artifact/1", "status": 200, "body": {}}]}
        self.assertEqual([], read_findings(row, expected))
        row["request"] = {"decoded": True, "value": {"state": "future-success"}}
        self.assertTrue(read_findings(row, expected))
        row["request"] = {"decoded": False}
        row["artifact"]["value"]["originalBytes"] = "rewritten"
        self.assertTrue(read_findings(row, expected))
        row["artifact"]["value"] = copy.deepcopy(expected)
        row["variant"] = "baseline"
        self.assertTrue(read_findings(row, expected))


if __name__ == "__main__":
    unittest.main()
