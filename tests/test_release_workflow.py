from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-service-image.yaml"


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
