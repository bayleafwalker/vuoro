from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-service-image.yaml"
PYTHON_WORKFLOW = ROOT / ".github" / "workflows" / "publish-python-packages.yaml"


def _assert_python_release_order(workflow: str) -> None:
    sync = workflow.index("uv sync --all-packages --all-extras --locked")
    full_suite = workflow.index(
        "name: Run the complete repository test suite\n        run: uv run pytest\n"
    )
    build = workflow.index("uv build --package vuoro-client")
    release_gate = workflow.index(
        "scripts/validate_release_contract.py dist/gate/*.whl --release"
    )
    served_gate = workflow.index("scripts/validate_served_conformance.py")
    selection = workflow.index("name: Select the gated wheel for publication")
    tag_gate = workflow.index("name: Validate the selected gated wheel against its tag")
    publisher = workflow.index("uses: pypa/gh-action-pypi-publish@release/v1")
    attestation = workflow.index("uses: actions/attest@v4")

    assert sync < full_suite < build < release_gate < served_gate
    assert served_gate < selection < tag_gate < publisher < attestation
    assert workflow.count("uv build --package") == 3
    assert 'wheel_stem="${package//-/_}"' in workflow
    assert 'wheels=(dist/gate/"${wheel_stem}"-*.whl)' in workflow
    assert 'cp -- "${wheels[0]}" "$wheel"' in workflow
    assert "packages-dir: dist/publish/" in workflow
    assert workflow.count("${{ steps.publication.outputs.wheel }}") == 2
    assert "subject-path: \"${{ steps.publication.outputs.wheel }}\"" in workflow


def test_service_image_publication_emits_verifiable_supply_chain_evidence() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "attestations: write" in workflow
    assert "id-token: write" in workflow
    assert "uses: docker/setup-buildx-action@v4" in workflow
    assert "uses: docker/build-push-action@v7" in workflow
    assert "provenance: mode=max" in workflow
    assert "sbom: true" in workflow
    assert "uses: actions/attest@v4" in workflow
    assert "subject-digest: ${{ steps.push.outputs.digest }}" in workflow
    assert "push-to-registry: true" in workflow


def test_service_image_publication_keeps_tag_and_source_aliases() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "vuoro-service:${{ github.ref_name }}" in workflow
    assert "vuoro-service:sha-${{ github.sha }}" in workflow


def test_python_release_workflow_uses_independent_immutable_package_tags() -> None:
    workflow = PYTHON_WORKFLOW.read_text()
    for tag in ("vuoro-client-v*", "vuoro-bootstrap-v*", "vuoro-service-v*"):
        assert tag in workflow
    _assert_python_release_order(workflow)


@pytest.mark.parametrize(
    "broken",
    [
        lambda workflow: workflow.replace(
            "uv sync --all-packages --all-extras --locked", "uv sync --all-packages"
        ),
        lambda workflow: workflow.replace(
            "run: uv run pytest", "run: uv run pytest tests/test_release_workflow.py"
        ),
        lambda workflow: workflow.replace(
            "      - uses: pypa/gh-action-pypi-publish@release/v1\n"
            "        with:\n"
            "          packages-dir: dist/publish/\n"
            "          skip-existing: false\n",
            "",
            1,
        ).replace(
            "      - name: Build and exercise the complete release candidate\n",
            "      - uses: pypa/gh-action-pypi-publish@release/v1\n"
            "        with:\n"
            "          packages-dir: dist/publish/\n"
            "      - name: Build and exercise the complete release candidate\n",
            1,
        ),
        lambda workflow: workflow.replace(
            "      - name: Select the gated wheel for publication\n",
            "      - run: uv build --package vuoro-client --wheel --out-dir dist/publish\n"
            "      - name: Select the gated wheel for publication\n",
        ),
        lambda workflow: workflow.replace(
            'wheel_stem="${package//-/_}"', 'wheel_stem="$package"'
        ),
    ],
    ids=(
        "partial-sync",
        "partial-suite",
        "early-publisher",
        "post-gate-rebuild",
        "hyphenated-wheel-stem",
    ),
)
def test_python_release_order_contract_rejects_regressions(broken) -> None:
    workflow = PYTHON_WORKFLOW.read_text()
    with pytest.raises((AssertionError, ValueError)):
        _assert_python_release_order(broken(workflow))
