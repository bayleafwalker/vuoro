"""Pure client value boundary only; actual generated SDK path is a later gate."""
from hashlib import sha256
from pathlib import Path
import pytest
from vuoro_contracts import ContractBundle, ContractError

FIXTURE=Path(__file__).resolve().parents[3]/"packages/vuoro-contracts/tests/fixtures/probe"

def test_client_refuses_fault_after_server_validation_and_preserves_counter():
    contract=ContractBundle.load(FIXTURE,integrity_sha256=sha256((FIXTURE/"integrity.json").read_bytes()).hexdigest())
    server_value={"state":"ready","revision":"9007199254740995"}
    assert contract.validate_response("submit",200,server_value) is server_value
    received=dict(server_value,state="future")
    with pytest.raises(ContractError):contract.validate_response("submit",200,received)
    assert server_value["revision"]=="9007199254740995" and server_value["state"]=="ready"
