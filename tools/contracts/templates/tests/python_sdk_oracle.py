"""F1b oracle for actual openapi-python-client output; never a replacement SDK.

Run in a fresh interpreter with GENERATED_ROOT SCENARIO. All transports are in
process. The generated attachment ABI is specified in QUALIFICATION.md.
"""
from copy import deepcopy
from hashlib import sha256
import importlib
import json
from pathlib import Path
import socket
import sys
import traceback

import httpx
from vuoro_contracts import ABSENT, ContractBundle, ContractError


def _forbid_network(*args, **kwargs):
    raise AssertionError("oracle_socket_forbidden")


def exercise(root: Path, scenario: str) -> dict:
    socket.socket = _forbid_network
    socket.create_connection = _forbid_network
    sys.path.insert(0, str(root / "clients/python"))
    package = "vuoro_contract_" + sha256(json.loads((root / "operation-manifest.json").read_bytes())["contractId"].encode("utf-8")).hexdigest() + "_client"
    native_client = importlib.import_module(package + ".client")
    submit = importlib.import_module(package + ".api.default.submit")
    digest = sha256((root / "integrity.json").read_bytes()).hexdigest()
    package_digest = sha256((root / f"clients/python/{package}/data/integrity.json").read_bytes()).hexdigest()
    contract = ContractBundle.load(root, integrity_sha256=digest)
    counts = {"native": 0, "model": 0, "serialize": 0, "parse": 0, "wire": 0, "effect": 0, "server_result": 0}
    wires = []
    stream_state = {"reads": 0, "closed": False}
    mode = {"fault": None, "serialize_fault": None}
    native_call, serialize_call, parse_call = submit.sync_detailed, submit._get_kwargs, submit._parse_response
    input_type = importlib.import_module(package + ".models.input_").Input
    original_model_init = input_type.__init__
    def model_observer(self, *args, **kwargs):
        counts["model"] += 1
        return original_model_init(self, *args, **kwargs)
    input_type.__init__ = model_observer
    def forbid_response_reconstruction(self):
        raise AssertionError("oracle_generated_response_model_rewrite")
    importlib.import_module(package + ".models.result").Result.to_dict = forbid_response_reconstruction

    def native_observer(*args, **kwargs):
        counts["native"] += 1
        return native_call(*args, **kwargs)

    def serialize_observer(*args, **kwargs):
        counts["serialize"] += 1
        assert isinstance(kwargs.get("body"), input_type)
        result = serialize_call(*args, **kwargs)
        if mode["serialize_fault"]:
            result = deepcopy(result)
            mutation = mode["serialize_fault"]
            if mutation == "unknown": result["json"]["unexpected"] = "PRIVATE_WIRE_MARKER"
            elif mutation == "counter": result["json"]["revision"] = 9007199254740995
            else:
                value = result.pop("json")
                raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
                if mutation == "duplicate": raw = raw[:-1] + b',"revision":"0"}'
                if mutation == "oversize": raw += b" " * (1025 - len(raw))
                if mutation == "exact": raw += b" " * (1024 - len(raw))
                result["content"] = raw
        return result

    def parse_observer(*args, **kwargs):
        counts["parse"] += 1
        return parse_call(*args, **kwargs)

    submit.sync_detailed, submit._get_kwargs, submit._parse_response = native_observer, serialize_observer, parse_observer
    attachment = importlib.import_module(package + ".validated_client")
    raw_body = {"revision": "9007199254740995", "enabled": True, "nested": {"label": "é"}}
    success = {"state": "ready", "revision": "9007199254740995", "metadata": {"safe": {"text": "é"}}}

    def server(request):
        counts["wire"] += 1
        assert request.url.host == "contracts.invalid"
        assert not request.url.query
        operation = {"/probe/k": "submit" if request.method == "POST" else "inspect",
                     "/probe/k/empty": "empty", "/probe/k/null": "nullBody"}[request.url.path]
        wire = request.read()
        wires.append(wire)
        value = json.loads(wire) if wire else ABSENT
        contract.validate_request(operation, value)
        if operation == "submit":
            assert value == raw_body and isinstance(value["revision"], str)
            counts["effect"] += 1
        status, body = (204, ABSENT) if operation == "empty" else (200, None if operation == "nullBody" else deepcopy(success))
        fault = mode["fault"]
        if fault in ("valid400", "valid503"):
            status, body = (400, {"code": "invalid"}) if fault == "valid400" else (503, {"code": "unavailable"})
        contract.validate_response(operation, status, body)
        counts["server_result"] += 1
        raw = b"" if body is ABSENT else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        # Every fault is introduced AFTER the server's valid result/effect boundary.
        if fault == "future": raw = b'{"state":"future","revision":"0"}'
        if fault == "duplicate": raw = b'{"state":"ready","revision":"0","revision":"9007199254740995"}'
        if fault == "nan": raw = b'{"state":"ready","revision":"0","metadata":{"x":NaN}}'
        if fault == "overflow": raw = b'{"state":"ready","revision":"0","metadata":{"x":1e999}}'
        if fault == "invalid_utf8": raw = b'{"state":"ready","revision":"0","metadata":{"x":"\xff"}}'
        if fault == "surrogate": raw = b'{"state":"ready","revision":"0","metadata":{"x":"\\ud800"}}'
        if fault == "unknown_status": status = 201
        if fault == "wrong400": status, raw = 400, b'{"code":"unavailable"}'
        if fault == "wrong503": status, raw = 503, b'{"code":"invalid"}'
        if fault == "empty_json": raw = b""
        if fault == "null_json": raw = b"null"
        if fault == "empty_wrong_body": raw = b"null"
        if fault == "null_missing": raw = b""
        if fault == "oversize": raw += b" " * 4096
        if fault == "bound_exact": raw += b" " * (contract.manifest["operations"][operation]["responseMaxBytes"][str(status)] - len(raw))
        if fault == "stream":
            class BoundedProbeStream(httpx.SyncByteStream):
                def __iter__(self):
                    stream_state["reads"] += 1
                    yield raw + b" " * 4096
                    raise AssertionError("unbounded_response_read_continued")
                def close(self): stream_state["closed"] = True
            return httpx.Response(status, stream=BoundedProbeStream(), headers={"Content-Type": "application/json"}, request=request)
        return httpx.Response(status, content=raw, headers={"Content-Type": "application/json"}, request=request)

    transport = httpx.Client(base_url="https://contracts.invalid", transport=httpx.MockTransport(server), trust_env=False, follow_redirects=False)
    native = native_client.Client(base_url="https://contracts.invalid", raise_on_unexpected_status=True).set_httpx_client(transport)
    guarded = attachment.create_validated_client(client=native, integrity_sha256=package_digest)

    def refuse(call):
        try: call()
        except ContractError as error:
            rendered = str(error) + repr(vars(error)) + "".join(traceback.format_exception(error))
            assert "PRIVATE_" not in rendered
            return error
        raise AssertionError("unsafe_sdk_result_was_accepted")

    def positive():
        before = deepcopy(raw_body)
        result = guarded.submit(key="k", body=raw_body)
        assert result.status == 200 and type(result.body) is dict and result.body == success
        assert result.content == json.dumps(success, ensure_ascii=False, separators=(",", ":")).encode()
        assert raw_body == before and result.body["revision"] == "9007199254740995"
        assert counts == {"native": 1, "model": 1, "serialize": 1, "parse": 1, "wire": 1, "effect": 1, "server_result": 1}
        assert json.loads(wires[0]) == raw_body

    if scenario == "positive": positive()
    elif scenario == "raw_request":
        invalids = []
        invalids.append(dict(raw_body, unexpected="PRIVATE_RAW_MARKER"))
        invalids.append(dict(raw_body, revision=9007199254740995))
        invalids.append(dict(raw_body, revision="9223372036854775808"))
        invalids.append(dict(raw_body, enabled=1))
        invalids.append(dict(raw_body, nested={"label": "é", "unexpected": True}))
        invalids.append(dict(raw_body, note="\ud800"))
        for value in invalids: refuse(lambda value=value: guarded.submit(key="k", body=value))
        assert counts["model"] == counts["serialize"] == counts["wire"] == counts["effect"] == 0
        for note in (ABSENT, None, ""):
            raw_body.pop("note", None)
            if note is not ABSENT: raw_body["note"] = note
            result = guarded.submit(key="k", body=raw_body)
            assert result.status == 200 and ("note" in json.loads(wires[-1])) is (note is not ABSENT)
        assert counts["effect"] == 3
    elif scenario.startswith("serialized_request:"):
        mode["serialize_fault"] = scenario.split(":", 1)[1]
        refuse(lambda: guarded.submit(key="k", body=raw_body))
        assert counts["native"] == counts["serialize"] == 1
        assert counts["wire"] == counts["effect"] == 0
    elif scenario == "serialized_exact":
        mode["serialize_fault"] = "exact"
        result = guarded.submit(key="k", body=raw_body)
        assert result.status == 200 and len(wires[0]) == 1024 and counts["effect"] == 1
    elif scenario.startswith("response:"):
        mode["fault"] = scenario.split(":", 1)[1]
        refuse(lambda: guarded.submit(key="k", body=raw_body))
        assert counts["native"] == counts["serialize"] == counts["wire"] == counts["effect"] == counts["server_result"] == 1
        assert counts["parse"] == 0
        if mode["fault"] == "stream": assert stream_state == {"reads": 1, "closed": True}
    elif scenario == "status":
        for fault, status, body in (("valid400", 400, {"code": "invalid"}), ("valid503", 503, {"code": "unavailable"})):
            mode["fault"] = fault
            result = guarded.submit(key="k", body=raw_body)
            assert result.status == status and type(result.body) is dict and result.body == body
            assert json.loads(result.content) == body
        assert counts["wire"] == counts["effect"] == counts["parse"] == 2
    elif scenario == "absence":
        empty = guarded.empty(key="k")
        assert empty.status == 204 and empty.body is ABSENT and empty.content == b""
        null = guarded.null_body(key="k")
        assert null.status == 200 and null.body is None and null.content == b"null"
        mode["fault"] = "empty_wrong_body"
        refuse(lambda: guarded.empty(key="k"))
        mode["fault"] = "null_missing"
        refuse(lambda: guarded.null_body(key="k"))
    elif scenario == "bound_exact":
        mode["fault"] = "bound_exact"
        result = guarded.submit(key="k", body=raw_body)
        assert len(result.content) == contract.manifest["operations"]["submit"]["responseMaxBytes"]["200"]
        assert result.body == success
    elif scenario == "attachment_removal":
        # A separately constructed native client has neither attachment. Its real
        # generated parser accepts a duplicate key after server validation/commit.
        bare = native_client.Client(base_url="https://contracts.invalid").set_httpx_client(
            httpx.Client(base_url="https://contracts.invalid", transport=httpx.MockTransport(server), trust_env=False))
        mode["fault"] = "duplicate"
        model = importlib.import_module(package + ".models.input_").Input.from_dict(raw_body)
        unsafe = submit.sync_detailed(client=bare, key="k", body=model)
        assert unsafe.status_code == 200 and unsafe.parsed.revision == "9007199254740995"
        assert counts["effect"] == 1
        refuse(lambda: guarded.submit(key="k", body=raw_body))
        assert counts["effect"] == 2
    else: raise AssertionError("unknown_oracle_scenario")
    transport.close()
    return {"scenario": scenario, **counts}


if __name__ == "__main__":
    print(json.dumps(exercise(Path(sys.argv[1]).resolve(), sys.argv[2]), sort_keys=True))
