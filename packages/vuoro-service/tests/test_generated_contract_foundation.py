"""Pure server-side boundary order oracle, without a deployed HTTP adapter.

The later real Sovite adapter must repeat these observations against its real owner.
"""
from hashlib import sha256
from pathlib import Path
import pytest
from vuoro_contracts import ContractBundle, ContractError, parse_json

FIXTURE=Path(__file__).resolve().parents[3]/"packages/vuoro-contracts/tests/fixtures/probe"

def load():
    return ContractBundle.load(FIXTURE,integrity_sha256=sha256((FIXTURE/"integrity.json").read_bytes()).hexdigest())

def test_invalid_raw_command_is_refused_before_handler_observation():
    contract=load();calls=[]
    def invoke(raw):
        value=contract.validate_request("submit",parse_json(raw,max_bytes=1024))
        calls.append(value)
        return contract.validate_response("submit",200,{"state":"ready","revision":"0"})
    valid=b'{"revision":"0","enabled":true,"nested":{"label":"x"}}'
    assert invoke(valid)["state"]=="ready" and len(calls)==1
    for raw in (valid[:-1]+b',"unknown":true}',b'{"revision":"0","revision":"1"}',valid.replace(b'"0"',b'0')):
        with pytest.raises(ContractError):invoke(raw)
    assert len(calls)==1

def test_invalid_projected_result_cannot_be_typed_success():
    contract=load()
    for result in ({"state":"future","revision":"0"},{}, {"code":"unavailable"}):
        with pytest.raises(ContractError):contract.validate_response("submit",200,result)
    assert contract.validate_response("submit",503,{"code":"unavailable"})=={"code":"unavailable"}
