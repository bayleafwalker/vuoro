"""Fail-closed consistency checks for source-only workflow packaging."""
import copy
import json
from pathlib import Path
import tomllib

import pytest

from scripts.validate_workflow_distribution import validate

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    contract = tomllib.loads((ROOT / 'deploy/reference-distribution.toml').read_text())
    composition = json.loads((ROOT / contract['composition_manifest']).read_text())
    return contract, composition


def test_candidate_matrix_matches_composition_and_workflow():
    validate(*inputs())


@pytest.mark.parametrize('mutation', [
    lambda c: c['support_matrix'][0].update(workflow_packages=['missing']),
    lambda c: c['workflow_package'][0].update(required_capabilities=['missing']),
    lambda c: c['support_matrix'][0].update(component_lock_ids=['missing']),
    lambda c: c['support_matrix'][0].update(qualification='qualified'),
    lambda c: c['support_matrix'][0].update(workflow_packages=[]),
    lambda c: c['workflow_package'].append(copy.deepcopy(c['workflow_package'][0])),
])
def test_rejects_inconsistent_or_premature_claims(mutation):
    contract, composition = inputs()
    mutation(contract)
    with pytest.raises(ValueError):
        validate(contract, composition)
