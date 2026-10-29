from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "publish-service-image.yaml"
PYTHON_WORKFLOW = ROOT / ".github" / "workflows" / "publish-python-packages.yaml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def _assert_python_release_order(workflow: str) -> None:
    preparation = workflow.index("name: Prepare the pinned contract compiler and decoder")
    sync = workflow.index("uv sync --all-packages --all-extras --locked")
    full_suite = workflow.index(
        'name: Run the complete repository test suite\n        run: uv run --locked --all-packages --all-extras --python "$qualified_python" pytest\n'
    )
    for command in (line for line in workflow.splitlines() if "uv run" in line):
        assert "--locked" in command and "--all-packages" in command and "--all-extras" in command
        assert '--python "$qualified_python"' in command
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

    assert preparation < sync < build < full_suite < release_gate < served_gate
    assert served_gate < selection < tag_gate < attestation < release_create < release_finalize
    assert workflow.count("uv build --package") == 6
    assert "uv build --package vuoro-contracts --wheel --out-dir dist/vuoro-contracts --no-create-gitignore" in workflow
    assert 'dist/vuoro-contracts/*.whl' in workflow
    assert 'wheels=(dist/vuoro-client/*.whl dist/vuoro-contracts/*.whl dist/vuoro-service/*.whl dist/vuoro-schema-runtime/*.whl dist/vuoro-adapter-kit/*.whl)' in workflow
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


def test_service_image_installs_both_locked_shared_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    # Owner adapters declare both shared wheels as immutable direct URLs.
    # Resolve owners first, then install the two locally verified shared wheels.
    first_install = dockerfile.split("&& python -m pip install", 1)[0]
    assert "./packages/vuoro-service" in first_install
    assert "/opt/vuoro/adapters/*.whl" not in first_install
    assert "python -m pip install --no-cache-dir --no-deps" in dockerfile
    assert "/opt/vuoro/adapters/vuoro_adapter_kit-*.whl" in dockerfile
    assert "/opt/vuoro/adapters/vuoro_schema_runtime-*.whl" in dockerfile
    # S2 item 4 unbound the execution (ActionQ) and knowledge (kctl) domains.
    for retired in ("actionq-", "actionq_contracts-", "kctl-"):
        assert f"/opt/vuoro/adapters/{retired}" not in dockerfile


def test_service_image_carries_the_mcp_server_beside_the_service() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    # `vuoro-service mcp-serve` needs vuoro-mcp-edge; installing both in one
    # resolution satisfies its vuoro-service dependency from the local tree.
    assert "COPY packages/vuoro-mcp-edge ./packages/vuoro-mcp-edge" in dockerfile
    first_install = dockerfile.split("&& python -m pip install", 1)[0]
    assert "./packages/vuoro-mcp-edge" in first_install
    assert "./packages/vuoro-service" in first_install


def test_ci_runs_the_mcp_edge_suite() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert (
        "uv run --package vuoro-mcp-edge --extra test pytest packages/vuoro-mcp-edge/tests"
        in workflow
    )


def test_ci_exercises_the_released_audit_composition() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "name: Exercise pinned released audit adapter" in workflow
    assert "dist/adapters/auditctl-*.whl" in workflow
    assert "dist/adapters/vuoro_adapter_kit-*.whl" in workflow
    assert "dist/adapters/vuoro_schema_runtime-*.whl" in workflow
    assert "scripts/validate_released_audit_adapter.py" in workflow


def test_ci_exercises_the_complete_released_three_domain_catalog() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "name: Exercise complete released three-domain catalog" in workflow
    assert "dist/adapters/vuoro_workspace-*.whl" in workflow
    assert "validate_knowledge_catalog_composition.py" not in workflow
    for retired in (
        "validate_released_execution_adapter.py",
        "validate_released_knowledge_adapter.py",
        "dist/adapters/actionq",
        "dist/adapters/kctl-",
    ):
        assert retired not in workflow
    assert "scripts/validate_released_catalog_composition.py" in workflow
    assert "dist/adapters/auditctl-*.whl" in workflow
    assert "dist/adapters/vuoro_schema_runtime-*.whl" in workflow


def test_python_release_workflow_uses_independent_immutable_package_tags() -> None:
    workflow = PYTHON_WORKFLOW.read_text()
    for tag in (
        "vuoro-client-v*", "vuoro-service-v*",
        "vuoro-schema-runtime-v*", "vuoro-adapter-kit-v*",
        "vuoro-workspace-v*",
    ):
        assert tag in workflow
    assert "vuoro-bootstrap" not in workflow
    _assert_python_release_order(workflow)


def test_python_release_pins_contract_toolchain_and_exports_decoder_environment() -> None:
    workflow = PYTHON_WORKFLOW.read_text()
    preparation = workflow.split(
        "      - name: Prepare the pinned contract compiler and decoder\n", 1
    )[1].split("      - name:", 1)[0]
    for token in (
        "0.13.1",
        "contracts-python-20261001.tar.gz",
        "ffcb50e716789d1a6e1db5e745d4d194ac8ed9b015ccf5eabcaccb179a25e4a8",
        "f8188f8dec8f4ae9865dc6789323327c50bb275a5a301373d675464fdbbf933f",
        "v24.21.0",
        "11.19.0",
        "npm ci --prefix tools/contracts --ignore-scripts --no-audit --no-fund",
        "tools/contracts/toolchain/tool-artifacts.json",
        "VUORO_CONTRACT_NODE",
        "VUORO_CONTRACT_PYTHON",
        "VUORO_CONTRACT_DECODER",
        "PYTHONPATH",
        "PYTHONDONTWRITEBYTECODE",
        "UV_PYTHON=%s",
    ):
        assert token in preparation
    assert "runs-on: ubuntu-24.04" in workflow
    assert "VUORO_OPS_REQUIRE_PG16: \"1\"" in workflow
    assert "VUORO_PG_BIN: /usr/lib/postgresql/16/bin" in workflow
    assert "VUORO_WORKSPACE_REQUIRE_PG: \"1\"" in workflow
    assert "VUORO_WORKSPACE_TEST_PG_BIN: /usr/lib/postgresql/16/bin" in workflow
    pg_setup = workflow.index("name: Install disposable PostgreSQL 16 tools for owner release tests")
    sync = workflow.index("name: Synchronize the complete locked workspace")
    full_suite = workflow.index("name: Run the complete repository test suite")
    assert pg_setup < sync < full_suite


def test_client_release_candidate_uses_fresh_version_after_failed_immutable_tag() -> None:
    import tomllib

    project = tomllib.loads((ROOT / "packages/vuoro-client/pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = [package for package in lock["package"] if package["name"] == "vuoro-client"]
    assert project["version"] == "0.1.6"
    assert len(locked) == 1 and locked[0]["version"] == "0.1.6"
    assert '__version__ = "0.1.6"' in (ROOT / "packages/vuoro-client/src/vuoro_client/__init__.py").read_text()
    recovery = (ROOT / "docs/plans/2026-10-25-vuoro-client-0.1.5-release-recovery.md").read_text()
    assert "Neither immutable tag is retagged" in recovery
    assert "0.1.4" in recovery and "40209690209" in recovery


def test_publisher_uv_runs_preserve_the_qualified_python_and_workspace() -> None:
    workflow = PYTHON_WORKFLOW.read_text()
    assert 'UV_PYTHON=%s\\n' in workflow
    runs = [line.strip() for line in workflow.splitlines() if "uv run" in line]
    assert runs
    for command in runs:
        assert "--locked" in command
        assert "--all-packages" in command and "--all-extras" in command
        assert '--python "$qualified_python"' in command


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
            'run: uv run --locked --all-packages --all-extras --python "$qualified_python" pytest', 'run: uv run --locked --python "$qualified_python" pytest tests/test_release_workflow.py'
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


def _canonical(name: str) -> str:
    import re

    return re.sub(r"[-_.]+", "-", name).lower()


def _workspace_requirements() -> dict[str, dict[str, set[str]]]:
    """Workspace dependencies per package: key "" is the base set, others extras."""
    import re
    import tomllib

    def names(requirements: list[str]) -> set[str]:
        return {
            _canonical(re.split(r"[\s\[<>=!~;@(]", requirement, maxsplit=1)[0])
            for requirement in requirements
        }

    projects = {}
    for pyproject in (ROOT / "packages").glob("*/pyproject.toml"):
        with pyproject.open("rb") as handle:
            project = tomllib.load(handle)["project"]
        groups = {"": names(project.get("dependencies", []))}
        for extra, requirements in project.get("optional-dependencies", {}).items():
            groups[_canonical(extra)] = names(requirements)
        projects[_canonical(project["name"])] = groups
    return {
        name: {group: deps & projects.keys() for group, deps in groups.items()}
        for name, groups in projects.items()
    }


def test_service_image_never_resolves_a_workspace_name_from_an_index() -> None:
    """Every workspace package the image's local installs need is supplied.

    pip ignores `[tool.uv.sources] workspace = true`, so a workspace dependency
    that is not itself supplied is looked up on the index, where the name is
    unregistered (0.1.76: vuoro-evidence) and could be claimed by anyone.
    The local install is --no-deps, so every workspace dependency must come
    from one of three places:
    - a local path in that install;
    - the verified adapter wheelhouse;
    - a hash-pinned URL requirement in the hash-locked image requirements.
    """
    import re

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    installs = re.split(r"\bpip install\b", dockerfile)[1:]
    assert installs, "the Dockerfile has no pip install to check"
    with_local = [
        segment for segment in installs if re.search(r"\./packages/", segment)
    ]
    assert len(with_local) == 1, (
        "pass every ./packages/ path to one pip install; found them in "
        f"{len(with_local)} installs"
    )
    resolving_install = with_local[0]
    # Nothing resolves: third-party distributions come only from the
    # hash-locked requirements, and every install is --no-deps.
    for segment in installs:
        assert "--no-deps" in segment.split("&&", 1)[0], segment
    locked = [segment for segment in installs if "-r /opt/vuoro/image-requirements.txt" in segment]
    assert len(locked) == 1 and "--require-hashes" in locked[0].split("&&", 1)[0]
    assert "COPY deploy/image/requirements.txt /opt/vuoro/image-requirements.txt" in dockerfile
    pinned_urls = {
        _canonical(name)
        for name in re.findall(
            r"^([A-Za-z0-9_.-]+) @ https://\S+#sha256=[0-9a-f]{64}",
            (ROOT / "deploy" / "image" / "requirements.txt").read_text(encoding="utf-8"),
            flags=re.MULTILINE,
        )
    }
    local: dict[str, set[str]] = {}
    for name, extras in re.findall(
        r"\./packages/([A-Za-z0-9_.-]+)(?:\[([^\]]*)\])?", resolving_install
    ):
        local.setdefault(_canonical(name), set()).update(
            _canonical(extra) for extra in extras.split(",") if extra.strip()
        )
    wheelhouse = {
        _canonical(name)
        for name in re.findall(r"/opt/vuoro/adapters/([A-Za-z0-9_]+)-\*\.whl", dockerfile)
    }
    requirements = _workspace_requirements()
    assert local, "the Dockerfile installs no local workspace package"
    assert local.keys() <= requirements.keys(), local.keys() - requirements.keys()

    needed: set[str] = set()
    frontier = [(name, extras) for name, extras in local.items()]
    while frontier:
        name, extras = frontier.pop()
        for group in {"", *extras}:
            assert group in requirements[name], f"{name} has no extra {group!r}"
            for dependency in requirements[name][group] - needed:
                needed.add(dependency)
                frontier.append((dependency, set()))
    from_index = needed - local.keys() - wheelhouse - pinned_urls
    assert not from_index, (
        f"workspace packages {sorted(from_index)} would resolve from an index; "
        "COPY them and pass their ./packages/ path in the same pip install"
    )
    for name in local:
        assert f"COPY packages/{name} ./packages/{name}" in dockerfile


def test_ci_builds_and_inspects_the_service_image() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    job = workflow.split("\n  service-image:\n", 1)[1]
    assert "uses: docker/build-push-action@v7" in job
    assert "load: true" in job and "push: false" in job
    assert "cache-from: type=gha" in job
    assert "scripts/validate_service_image.py workspace-names" in job
    assert "- check \"${names[@]}\" < scripts/validate_service_image.py" in job
    assert "-m pip check" in job
    assert "mcp-serve --help" in job


def test_image_requirements_are_the_current_export_of_uv_lock() -> None:
    """The hash-locked image requirements cannot drift from uv.lock."""
    import shutil
    import subprocess
    import sys

    assert shutil.which("uv"), "uv is required to verify the image requirements export"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_image_requirements.py"), "--check"],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    requirements = (ROOT / "deploy" / "image" / "requirements.txt").read_text(encoding="utf-8")
    for name in ("boto3==", "botocore==", "s3transfer==", "click==", "psycopg==", "psycopg-binary==",
                 "fastapi==", "vuoro-client @ "):
        assert name in requirements
