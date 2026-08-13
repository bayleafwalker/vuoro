from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-service-image.yaml"
PYTHON_WORKFLOW = ROOT / ".github" / "workflows" / "publish-python-packages.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _assert_python_release_order(workflow: str) -> None:
    sync = workflow.index("uv sync --all-packages --all-extras --locked")
    full_suite = workflow.index(
        "name: Run the complete repository test suite\n        run: uv run pytest\n"
    )
    build = workflow.index("uv build --package vuoro-client")
    release_gate = workflow.index(
        'scripts/validate_release_contract.py "${wheels[@]}" --release'
    )
    served_gate = workflow.index("scripts/validate_served_conformance.py")
    selection = workflow.index("name: Select the gated wheel for publication")
    tag_gate = workflow.index("name: Validate the selected gated wheel against its tag")
    attestation = workflow.index("name: Attest the selected gated wheel")
    release_create = workflow.index("name: Create the draft GitHub release with the selected wheel")
    release_finalize = workflow.index(
        "name: Publish the GitHub release after attestation and draft creation"
    )

    assert sync < build < full_suite < release_gate < served_gate
    assert served_gate < selection < tag_gate < attestation < release_create < release_finalize
    assert workflow.count("uv build --package") == 5
    assert 'wheel_stem="${package//-/_}"' in workflow
    assert 'wheels=(dist/"${package}"/"${wheel_stem}"-*.whl)' in workflow
    assert 'cp -- "${wheels[0]}" "$wheel"' in workflow
    assert 'sha256="$(sha256sum "$wheel" | awk \'{print $1}\')"' in workflow
    assert 'echo "sha256=$sha256" >> "$GITHUB_OUTPUT"' in workflow
    assert workflow.count("${{ steps.publication.outputs.wheel }}") == 3
    assert "subject-path: \"${{ steps.publication.outputs.wheel }}\"" in workflow
    assert workflow.count("uses: actions/attest@v4") == 1
    assert "if: ${{ false }}" not in workflow
    assert 'contents: write' in workflow
    assert 'GH_TOKEN: ${{ github.token }}' in workflow
    assert 'gh release create "$tag"' in workflow
    assert '--verify-tag' in workflow
    assert '--repo "$GITHUB_REPOSITORY"' in workflow
    assert '--title "$tag — $package — $wheel_name — sha256:$sha256"' in workflow
    assert 'distribution: %s\\nwheel: %s\\nsha256: %s\\n' in workflow
    assert '"$wheel"' in workflow
    assert '--clobber' not in workflow
    assert 'gh release upload' not in workflow
    assert "pypa/gh-action-pypi-publish" not in workflow
    assert "pypi" not in workflow.lower()
    assert "packages-dir:" not in workflow
    assert "id-token: write" in workflow  # required by actions/attest
    assert 'gh release edit "${GITHUB_REF_NAME}" --repo "${GITHUB_REPOSITORY}" --draft=false' in workflow


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


def test_ci_exercises_the_released_knowledge_composition() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "name: Exercise pinned released knowledge adapter" in workflow
    assert "dist/adapters/kctl-*.whl" in workflow
    assert "dist/adapters/vuoro_adapter_kit-*.whl" in workflow
    assert "scripts/validate_released_knowledge_adapter.py" in workflow


def test_python_release_workflow_uses_independent_immutable_package_tags() -> None:
    workflow = PYTHON_WORKFLOW.read_text()
    for tag in (
        "vuoro-client-v*", "vuoro-bootstrap-v*", "vuoro-service-v*",
        "vuoro-schema-runtime-v*", "vuoro-adapter-kit-v*",
    ):
        assert tag in workflow
    _assert_python_release_order(workflow)


def test_python_release_is_github_only_and_publishes_the_created_release() -> None:
    workflow = PYTHON_WORKFLOW.read_text()

    assert "pypa/gh-action-pypi-publish" not in workflow
    assert "pypi" not in workflow.lower()
    assert "packages-dir:" not in workflow
    assert "skip-existing:" not in workflow
    assert "id-token: write" in workflow  # required by actions/attest

    attestation = workflow.index("name: Attest the selected gated wheel")
    release_create = workflow.index("name: Create the draft GitHub release with the selected wheel")
    release_publish = workflow.index(
        "name: Publish the GitHub release after attestation and draft creation"
    )
    assert attestation < release_create < release_publish


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
            "      - name: Publish the GitHub release after attestation and draft creation\n",
            "      - uses: pypa/gh-action-pypi-publish@release/v1\n"
            "        with:\n"
            "          packages-dir: dist/publish/\n"
            "      - name: Publish the GitHub release after attestation and draft creation\n",
        ),
        lambda workflow: workflow.replace(
            "      - name: Select the gated wheel for publication\n",
            "      - run: uv build --package vuoro-client --wheel --out-dir dist/publish\n"
            "      - name: Select the gated wheel for publication\n",
        ),
        lambda workflow: workflow.replace(
            'wheel_stem="${package//-/_}"', 'wheel_stem="$package"'
        ),
        lambda workflow: workflow.replace(
            "      - name: Attest the selected gated wheel\n",
            "      - name: Attest the selected gated wheel\n"
            "        if: ${{ false }}\n",
        ),
        lambda workflow: workflow.replace("            --verify-tag \\\n", ""),
        lambda workflow: workflow.replace(
            "            \"$wheel\"\n",
            "            --clobber \"$wheel\"\n",
        ),
        lambda workflow: workflow.replace("  contents: write", "  contents: read"),
        lambda workflow: workflow.replace("  id-token: write", "  id-token: read"),
    ],
    ids=(
        "partial-sync",
        "partial-suite",
        "early-publisher",
        "post-gate-rebuild",
        "hyphenated-wheel-stem",
        "disabled-attestation",
        "missing-verify-tag",
        "clobber-release-asset",
        "read-only-release-permission",
        "read-only-attestation-oidc-permission",
    ),
)
def test_python_release_order_contract_rejects_regressions(broken) -> None:
    workflow = PYTHON_WORKFLOW.read_text()
    with pytest.raises((AssertionError, ValueError)):
        _assert_python_release_order(broken(workflow))
