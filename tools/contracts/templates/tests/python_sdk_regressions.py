"""Root-owned real Python SDK regressions; bounded in-process transport only."""
from copy import deepcopy
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
import socket
import sys
import traceback

import httpx
from vuoro_contracts import ContractError


def exercise(root, scenario):
    def no_socket(*args, **kwargs):
        raise AssertionError("regression_network_forbidden")
    socket.socket = socket.create_connection = no_socket
    sys.path.insert(0, str(root / "clients/python"))
    package = "vuoro_contract_" + sha256(json.loads((root / "operation-manifest.json").read_bytes())["contractId"].encode("utf-8")).hexdigest() + "_client"
    Client = import_module(package + ".client").Client
    Input = import_module(package + ".models.input_").Input
    module = import_module(package + (".api.owner.submit" if scenario == "tagged" else ".api.default.submit"))
    attachment = import_module(package + ".validated_client")
    body = {"revision": "12", "enabled": True, "nested": {"label": "x"}}
    success = {"state": "ready", "revision": "12"}
    wires = []
    def server(request):
        wires.append((request.method, str(request.url), request.headers.get("x-token")))
        return httpx.Response(200, json=deepcopy(success), request=request)
    transport = httpx.Client(base_url="https://contracts.invalid", transport=httpx.MockTransport(server), trust_env=False)
    native = Client(base_url="https://contracts.invalid").set_httpx_client(transport)
    native_counts = {"entry":0, "serialize":0, "parse":0}
    def observe(key, original):
        def wrapped(*args, **kwargs):
            native_counts[key] += 1
            return original(*args, **kwargs)
        return wrapped
    for key, attribute in (("entry", "sync_detailed"), ("serialize", "_get_kwargs"), ("parse", "_parse_response")):
        setattr(module, attribute, observe(key, getattr(module, attribute)))
    extra = {"x_token": "k"} if scenario in ("header", "serialized_header") else {}
    # Direct emitted model/entrypoint/serializer/parser positive proves the variant is real.
    positive = module.sync_detailed(key="k", body=Input.from_dict(deepcopy(body)), client=native, **extra)
    assert positive.status_code == 200 and positive.parsed.to_dict() == success
    assert wires[-1][0] == "POST"
    guarded = attachment.create_validated_client(client=native, integrity_sha256=sha256((root / "integrity.json").read_bytes()).hexdigest())
    kwargs = {"key": "k", "body": deepcopy(body)}
    if extra: kwargs["x-token"] = "k"
    assert guarded.submit(**kwargs).body == success
    assert len(wires) == 2 and native_counts == {"entry":2, "serialize":2, "parse":2}, "guard_did_not_execute_actual_native_sdk"
    if extra: assert wires[-1][2] == "k"
    effects = len(wires)
    model_calls = []
    original_model = Input.from_dict
    def observe_model(*args, **kwargs):
        model_calls.append(True)
        return original_model(*args, **kwargs)
    Input.from_dict = observe_model
    def refuse(call):
        before = len(wires)
        try:
            call()
        except ContractError as error:
            rendered = str(error) + repr(vars(error)) + "".join(traceback.format_exception(error))
            assert "PRIVATE_" not in rendered, "caller_data_leaked_from_error"
        else:
            raise AssertionError("unsafe_native_sdk_call_accepted")
        assert len(wires) == before, "invalid_request_reached_transport"
    if scenario == "routing_override":
        refuse(lambda: guarded.inspect(_name="submit", key="k", body=body))
        assert not model_calls, "override_reached_model_conversion"
    elif scenario == "unknown_kwargs":
        invalid_kwargs = dict(kwargs, PRIVATE_CALLER_KEY="PRIVATE_CALLER_VALUE")
        refuse(lambda: guarded.submit(**invalid_kwargs))
        assert not model_calls, "unsupported_option_reached_model_conversion"
    elif scenario.startswith("serialized_"):
        original = module._get_kwargs
        def mutate(*args, **kw):
            out = deepcopy(original(*args, **kw))
            if scenario == "serialized_path": out["url"] = "/probe/" + "x" * 65
            if scenario == "serialized_query": out["params"]["selector"] = "x" * 65
            if scenario == "serialized_header": out["headers"]["x-token"] = "x" * 65
            return out
        module._get_kwargs = mutate
        if scenario == "serialized_query": kwargs["selector"] = "k"
        refuse(lambda: guarded.submit(**kwargs))
    elif scenario == "percent_path":
        # Schema-valid percent text must be encoded once by the actual native SDK.
        direct = module.sync_detailed(key="%41", body=Input.from_dict(deepcopy(body)), client=native)
        assert direct.status_code == 200 and direct.parsed.to_dict() == success
        encoded = wires[-1][1]
        assert encoded == "https://contracts.invalid/probe/%2541"
        before = len(wires)
        result = guarded.submit(key="%41", body=deepcopy(body))
        assert result.body == success and result.status == 200
        assert len(wires) == before + 1 and wires[-1][1] == encoded
        assert native_counts == {"entry":4, "serialize":4, "parse":4}, "percent_path_bypassed_actual_native_sdk"
    elif scenario in ("tagged", "header"):
        assert len(wires) == effects
    else:
        raise AssertionError("unknown_regression_scenario")
    return {"scenario": scenario, "result": "pass"}


if __name__ == "__main__":
    print(json.dumps(exercise(Path(sys.argv[1]), sys.argv[2])))


def exercise_final_parameter_regression(root, scenario):
    """Independent exact-native positives, then actual serialization corruption."""
    def no_socket(*args, **kwargs):
        raise AssertionError("regression_network_forbidden")
    socket.socket = socket.create_connection = no_socket
    sys.path.insert(0, str(root / "clients/python"))
    package = "vuoro_contract_" + sha256(json.loads((root / "operation-manifest.json").read_bytes())["contractId"].encode("utf-8")).hexdigest() + "_client"
    Client = import_module(package + ".client").Client
    Input = import_module(package + ".models.input_").Input
    module = import_module(package + ".api.default.submit")
    attachment = import_module(package + ".validated_client")
    body = {"revision":"12", "enabled":True, "nested":{"label":"x"}}
    success = {"state":"ready", "revision":"12"}
    wires = []
    counts = {"entry":0, "serialize":0, "parse":0}
    def server(request):
        wires.append({"method":request.method, "url":str(request.url), "token":request.headers.get("x-token")})
        return httpx.Response(200, json=deepcopy(success), request=request)
    def observe(key, original):
        def wrapped(*args, **kwargs):
            counts[key] += 1
            return original(*args, **kwargs)
        return wrapped
    for key, attribute in (("entry","sync_detailed"), ("serialize","_get_kwargs"), ("parse","_parse_response")):
        setattr(module, attribute, observe(key, getattr(module, attribute)))
    transport = httpx.Client(base_url="https://contracts.invalid", transport=httpx.MockTransport(server), trust_env=False)
    native = Client(base_url="https://contracts.invalid").set_httpx_client(transport)
    native_extra = {"selector":"k"} if scenario == "omitted_query" else {"x_token":"k"} if scenario == "omitted_header" else {"count":7} if scenario == "numeric_query" else {}
    public_extra = {"x-token":"k"} if scenario == "omitted_header" else dict(native_extra)
    direct = module.sync_detailed(key="k", body=Input.from_dict(deepcopy(body)), client=native, **native_extra)
    assert direct.status_code == 200 and direct.parsed.to_dict() == success
    if scenario == "omitted_query": assert wires[-1]["url"] == "https://contracts.invalid/probe/k?selector=k"
    if scenario == "omitted_header": assert wires[-1]["token"] == "k"
    if scenario == "numeric_query": assert wires[-1]["url"] == "https://contracts.invalid/probe/k?count=7"
    guarded = attachment.create_validated_client(client=native, integrity_sha256=sha256((root / "integrity.json").read_bytes()).hexdigest())
    # Omission by the caller is valid and must not become blanket-required behavior.
    if scenario in ("omitted_query", "omitted_header", "numeric_query"):
        assert guarded.submit(key="k", body=deepcopy(body)).body == success
        assert wires[-1]["url"] == "https://contracts.invalid/probe/k" and wires[-1]["token"] is None
    supplied = dict(key="k", body=deepcopy(body), **public_extra)
    assert guarded.submit(**supplied).body == success
    expected_positives = 3 if scenario != "wrong_origin" else 2
    assert len(wires) == expected_positives and counts == dict.fromkeys(counts, expected_positives), "positive_bypassed_actual_native_sdk"
    if scenario == "omitted_query": assert wires[-1]["url"].endswith("?selector=k")
    if scenario == "omitted_header": assert wires[-1]["token"] == "k"
    if scenario == "numeric_query": assert wires[-1]["url"].endswith("?count=7")
    original = module._get_kwargs
    mutation_calls = []
    def mutate(*args, **kwargs):
        result = deepcopy(original(*args, **kwargs))
        if scenario == "omitted_query":
            assert result["params"].pop("selector") == "k"
        elif scenario == "omitted_header":
            assert result["headers"].pop("x-token") == "k"
        elif scenario == "wrong_origin":
            assert result["url"] == "/probe/k"
            result["url"] = "https://outside.invalid/probe/k"
        elif scenario == "numeric_query":
            assert result["params"]["count"] == 7
            result["params"]["count"] = 8
        else:
            raise AssertionError("unknown_final_regression_scenario")
        mutation_calls.append(True)
        return result
    module._get_kwargs = mutate
    before = len(wires)
    try:
        guarded.submit(**supplied)
    except ContractError as error:
        rendered = str(error) + repr(vars(error)) + "".join(traceback.format_exception(error))
        assert "PRIVATE_" not in rendered
    else:
        raise AssertionError("serialized_parameter_or_origin_corruption_reached_transport")
    assert mutation_calls == [True], "actual_native_serializer_mutation_not_exercised"
    assert len(wires) == before, "invalid_request_reached_transport"
    assert counts == {"entry":expected_positives + 1, "serialize":expected_positives + 1, "parse":expected_positives}
    return {"scenario":scenario, "result":"pass"}
