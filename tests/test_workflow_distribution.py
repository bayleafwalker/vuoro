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
    lambda c: c['support_matrix'][0].update(native_runtimes='unknown'),
    lambda c: c['support_matrix'][0]['component_lock_ids'].append('work-adapter'),
    lambda c: c['support_matrix'][0].update(qualification_references=['FABRICATED']),
    lambda c: c['support_matrix'][0].update(qualification_references=['']),
    lambda c: c['workflow_package'][0].update(unsupported_requirements=['']),
    lambda c: c['workflow_package'][0].update(evidence=['']),
    lambda c: c['workflow_package'][0].update(environment_profiles=['']),
    lambda c: c['workflow_package'][0].update(id=4),
    lambda c: c['support_matrix'][0].update(profile='made-up'),
    lambda c: c.update(workflow_package='repository-delivery'),
    lambda c: c['capability'][0].update(pinned_version='99.99.99'),
    lambda c: c['capability'][0].update(distribution='fabricated-package'),
    lambda c: c.update(supported_distribution=True),
    lambda c: c.update(qualification_status='qualified'),
    lambda c: c.update(deployment_status='deployed'),
    lambda c: c['capability'][0].update(user_qualification='qualified'),
])
def test_rejects_inconsistent_or_premature_claims(mutation):
    contract, composition = inputs()
    mutation(contract)
    with pytest.raises(ValueError):
        validate(contract, composition)


@pytest.mark.parametrize('mutation', [
    lambda c: c['release_locks'][0].update(artifact_sha256='not-a-digest'),
    lambda c: c['release_locks'][0].update(source_revision='branch-main'),
    lambda c: c['release_locks'][0].update(distribution_version='99.99.99'),
    lambda c: c['runtime_descriptors'][0].update(api_version='invented/v1'),
])
def test_rejects_corrupted_composition_tuple(mutation):
    contract, composition = inputs()
    mutation(composition)
    with pytest.raises(ValueError):
        validate(contract, composition)
