"""Public-source release graph oracles; no network or Actions execution.

These check wiring and ordering. The independent candidate subprocess corpus
checks actual byte/receipt refusal; hosted qualification remains mandatory.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ".github/workflows/contracts-production-toolchain.yml"
PUBLISHER = ".github/workflows/publish-contracts.yaml"
FIELDS = ("artifact_name", "artifact_id", "wheel_basename", "wheel_sha256", "source_sha")


def load(path: str) -> dict:
    target = ROOT / path
    assert target.is_file(), f"missing required release workflow {path}"
    return yaml.load(target.read_text(), Loader=yaml.BaseLoader)


def script(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job.get("steps", []))


def assert_publisher(flow: dict) -> None:
    assert flow["on"] == {"push": {"tags": ["vuoro-contracts-v*"]}}
    assert flow["permissions"] == {"contents": "read"}
    assert flow["concurrency"]["group"] == "contracts-release-${{ github.ref }}"
    assert flow["concurrency"]["cancel-in-progress"] == "false"
    assert set(flow["jobs"]) == {"qualified-candidate", "publish"}
    gate = flow["jobs"]["qualified-candidate"]
    assert gate["uses"] == "./" + REUSABLE
    assert gate["with"] == {"run-repository-suite": "true"}
    assert "secrets" not in gate and "if" not in gate
    publish = flow["jobs"]["publish"]
    assert publish["needs"] == "qualified-candidate"
    assert "if" not in publish and "continue-on-error" not in publish
    assert publish["permissions"] == {"contents": "write", "attestations": "write", "id-token": "write"}
    steps = publish["steps"]
    assert all("if" not in step and "continue-on-error" not in step for step in steps)
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout.get("with", {}).get("ref", "${{ github.sha }}") == "${{ github.sha }}"
    downloads = [s for s in steps if s.get("uses", "").startswith("actions/download-artifact@")]
    assert len(downloads) == 1
    assert downloads[0]["with"] == {"artifact-ids": "${{ needs.qualified-candidate.outputs.artifact_id }}", "path": "dist/contracts-candidate", "merge-multiple": "true"}
    verify = next(s for s in steps if s.get("id") == "verify")
    for name in ("wheel_basename", "wheel_sha256", "source_sha", "artifact_name"):
        assert verify["env"]["EXPECTED_" + name.upper()] == "${{ needs.qualified-candidate.outputs." + name + " }}"
    run = verify["run"]
    assert "set -euo pipefail" in run
    assert "scripts/contracts/release_candidate.py verify" in run
    for arg in ('--candidate-dir dist/contracts-candidate', '--expected-source-sha "$EXPECTED_SOURCE_SHA"', '--expected-wheel-basename "$EXPECTED_WHEEL_BASENAME"', '--expected-wheel-sha256 "$EXPECTED_WHEEL_SHA256"', '--expected-artifact-name "$EXPECTED_ARTIFACT_NAME"', '--expected-run-id "$GITHUB_RUN_ID"', '--expected-run-attempt "$GITHUB_RUN_ATTEMPT"', '--tag "$GITHUB_REF_NAME"'):
        assert arg in run
    assert 'test "$EXPECTED_SOURCE_SHA" = "$GITHUB_SHA"' in run
    assert "GITHUB_OUTPUT" in run and "wheel=" in run and "sha256=" in run
    attest = [s for s in steps if s.get("uses") == "actions/attest@v4"]
    assert len(attest) == 1
    assert attest[0]["with"]["subject-path"] == "${{ steps.verify.outputs.wheel }}"
    creates = [s for s in steps if "gh release create" in s.get("run", "")]
    finalizes = [s for s in steps if "gh release edit" in s.get("run", "")]
    assert len(creates) == len(finalizes) == 1
    create, finalize = creates[0], finalizes[0]
    assert steps.index(downloads[0]) < steps.index(verify) < steps.index(attest[0]) < steps.index(create) < steps.index(finalize)
    assert create["env"]["WHEEL"] == "${{ steps.verify.outputs.wheel }}"
    assert create["env"]["SHA256"] == "${{ steps.verify.outputs.sha256 }}"
    assert create["env"]["GH_TOKEN"] == finalize["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert all(token in create["run"] for token in ('--verify-tag', '--draft', '--repo "$GITHUB_REPOSITORY"', '"$WHEEL"', '$SHA256', 'sha256:', 'vuoro-contracts'))
    assert '--draft=false' in finalize["run"] and '--repo "$GITHUB_REPOSITORY"' in finalize["run"]
    assert "set -euo pipefail" in create["run"] and "set -euo pipefail" in finalize["run"]
    text = script(publish).lower()
    for forbidden in ("uv build", "uv sync", "--clobber", "release upload", "skip-existing", "pypi", "curl ", "wget "):
        assert forbidden not in text


def assert_reusable(flow: dict) -> None:
    call = flow["on"]["workflow_call"]
    assert call["inputs"] == {"run-repository-suite": {"type": "boolean", "default": "false", "required": "false"}}
    assert set(call["outputs"]) == set(FIELDS)
    assert "secrets" not in call
    assert flow["permissions"] == {"contents": "read"}
    assert set(flow["jobs"]) == {"qualify"}
    job = flow["jobs"]["qualify"]
    assert "if" not in job and "continue-on-error" not in job
    for name in FIELDS:
        assert call["outputs"][name]["value"] == "${{ jobs.qualify.outputs." + name + " }}"
        expected = "${{ steps.upload.outputs.artifact-id }}" if name == "artifact_id" else "${{ steps.candidate.outputs." + name + " }}"
        assert job["outputs"][name] == expected
    steps = job["steps"]
    assert all("continue-on-error" not in s for s in steps)
    restore = next(s for s in steps if s.get("name") == "Restore locked contract toolchain and test environments")
    production = next(s for s in steps if s.get("name") == "Run exact production compiler and SDK contract gates")
    mcp_projection = next(s for s in steps if s.get("name") == "Run MCP definition-only projection gate")
    build = next(s for s in steps if "uv build --package vuoro-contracts " in s.get("run", ""))
    consumer = next(s for s in steps if s.get("name") == "Exercise wheel resources in an isolated consumer")
    ordinary = next(s for s in steps if s.get("name") == "Run repository release gates")
    candidate = next(s for s in steps if s.get("id") == "candidate")
    upload = next(s for s in steps if s.get("id") == "upload")
    assert steps.index(restore) < steps.index(production) < steps.index(mcp_projection) < steps.index(build) < steps.index(consumer) < steps.index(ordinary) < steps.index(candidate) < steps.index(upload)
    for step in (restore, production, mcp_projection, build, consumer, candidate, upload):
        assert "if" not in step
    assert ordinary["if"] == "${{ inputs.run-repository-suite }}"
    full = script(job)
    assert full.count("uv build --package vuoro-contracts ") == 1
    for token in ("20261001/cpython-3.13.16%2B20261001-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz", "ffcb50e716789d1a6e1db5e745d4d194ac8ed9b015ccf5eabcaccb179a25e4a8", "f8188f8dec8f4ae9865dc6789323327c50bb275a5a301373d675464fdbbf933f", 'sha256sum --check', 'node --version', 'v24.21.0', '11.19.0', '--locked', '--ignore-scripts --no-audit --no-fund', '--python "$qualified_python"'):
        assert token in restore["run"]
    for token in ("VUORO_CONTRACT_NODE", "VUORO_CONTRACT_PYTHON", "VUORO_CONTRACT_DECODER", "PYTHONDONTWRITEBYTECODE", "tool-artifacts.json", "assert actual == expected", "packages/vuoro-contracts/tests", "packages/vuoro-client/tests/test_generated_contract_boundary.py", "packages/vuoro-service/tests/test_generated_contract_foundation.py", '(279, 0, 0, 0)', '--junitxml="$RUNNER_TEMP/contracts-pytest.xml"'):
        assert token in production["run"]
    for token in ("VUORO_CONTRACT_NODE", "VUORO_CONTRACT_PYTHON", "VUORO_CONTRACT_DECODER", "test_mcp_projection.py", '(10, 0, 0, 0)', '--junitxml="$RUNNER_TEMP/contracts-mcp-projection.xml"'):
        assert token in mcp_projection["run"]
    assert "uv venv --python 3.13.16" not in consumer["run"]
    assert 'uv venv --python "$qualified_python"' in consumer["run"]
    assert "Draft202012Validator.check_schema" in consumer["run"] and "-I -" in consumer["run"]
    for token in ("postgresql-16", "VUORO_OPS_REQUIRE_PG16", "/usr/lib/postgresql/16/bin", "uv run --no-sync pytest", "--ignore=packages/vuoro-contracts/tests/test_production_generation.py", "--ignore=packages/vuoro-contracts/tests/test_production_generation_regressions.py"):
        assert token in ordinary["run"] or token in str(ordinary.get("env", {}))
    assert ordinary["run"].count("--ignore=") == 2
    for package in ("vuoro-client", "vuoro-service", "vuoro-schema-runtime", "vuoro-adapter-kit", "vuoro-workspace"):
        assert f"uv build --package {package} --wheel --out-dir dist/{package}" in ordinary["run"]
    assert "uv build --project packages/vuoro-demo --wheel --out-dir dist/vuoro-demo" in ordinary["run"]
    assert "set -euo pipefail" in ordinary["run"]
    assert "scripts/contracts/release_candidate.py create" in candidate["run"]
    for token in ('--wheel-dir dist/vuoro-contracts', '--source-sha "$GITHUB_SHA"', '--run-id "$GITHUB_RUN_ID"', '--run-attempt "$GITHUB_RUN_ATTEMPT"', '--junit "$RUNNER_TEMP/contracts-pytest.xml"', 'dist/vuoro-contracts/candidate.json', 'GITHUB_OUTPUT'):
        assert token in candidate["run"]
    assert "set -euo pipefail" in candidate["run"]
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"] == {"name": "${{ steps.candidate.outputs.artifact_name }}", "path": "dist/vuoro-contracts/*.whl\ndist/vuoro-contracts/candidate.json\n", "if-no-files-found": "error"}
    assert "always()" not in full and "|| true" not in full


def test_dedicated_publisher_has_no_qualification_bypass() -> None:
    assert_publisher(load(PUBLISHER))


def test_single_reusable_toolchain_qualifies_candidate_before_upload() -> None:
    assert_reusable(load(REUSABLE))


def test_ci_calls_same_source_reusable_without_duplicate_producer() -> None:
    flow = load(".github/workflows/ci.yml")
    assert flow["jobs"]["contracts-production-toolchain"] == {"uses": "./" + REUSABLE}
    text = (ROOT / ".github/workflows/ci.yml").read_text()
    assert "contracts-python-20261001.tar.gz" not in text
    assert "Run exact production compiler and SDK contract gates" not in text
    ordinary = flow["jobs"]["test"]
    pytest_step = next(step for step in ordinary["steps"] if "uv run pytest" in step.get("run", ""))
    assert "--ignore=packages/vuoro-contracts/tests/test_production_generation.py" in pytest_step["run"]
    assert "--ignore=packages/vuoro-contracts/tests/test_production_generation_regressions.py" in pytest_step["run"]
    assert "--ignore=packages/vuoro-contracts/tests/test_mcp_projection.py" in pytest_step["run"]


def test_reviewed_contract_aware_publisher_successor_and_its_oracle_are_pinned() -> None:
    expected = {".github/workflows/publish-python-packages.yaml": "4af72c872e613ff0039cdb399e86cef40a0df3d6209d1696197b3e9ba3ddee4a", "tests/test_release_workflow.py": "c7318d4e3a85cc8e16a80f13f815c2d2d7e28fc57bf45a5bafb5849954334159"}
    for path, digest in expected.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
    recovery = (ROOT / "docs/plans/2026-10-25-vuoro-client-0.1.5-release-recovery.md").read_text()
    assert "8364554fb84763e6436c118db0a030fd655635660dfe19ff85bd0b8e4e90d919" in recovery
    assert "vuoro-client-v0.1.3" in recovery and "40203510472" in recovery


@pytest.mark.parametrize("path", [REUSABLE, PUBLISHER])
def test_all_new_run_blocks_are_valid_bash(path: str) -> None:
    flow = load(path)
    for job in flow["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step:
                checked = subprocess.run(["bash", "-n"], input=step["run"], text=True, capture_output=True, timeout=5)
                assert checked.returncode == 0, (step.get("name"), checked.stderr)


@pytest.mark.parametrize("case", ["no-needs", "always", "partial-suite", "cross-run", "no-hash", "wrong-source", "rebuild", "no-attest", "no-verify-tag", "clobber", "early-finalize"])
def test_publisher_graph_oracle_rejects_authority_mutations(case: str) -> None:
    flow = load(PUBLISHER)
    assert_publisher(flow)  # require actual positive first, never count missing files as mutant kills
    bad = copy.deepcopy(flow)
    job = bad["jobs"]["publish"]
    steps = job["steps"]
    verify = next(s for s in steps if s.get("id") == "verify")
    if case == "no-needs":
        del job["needs"]
    elif case == "always":
        job["if"] = "${{ always() }}"
    elif case == "partial-suite":
        bad["jobs"]["qualified-candidate"]["with"]["run-repository-suite"] = "false"
    elif case == "cross-run":
        next(s for s in steps if s.get("uses", "").startswith("actions/download-artifact@"))["with"]["run-id"] = "9999"
    elif case == "no-hash":
        verify["run"] = verify["run"].replace('--expected-wheel-sha256 "$EXPECTED_WHEEL_SHA256"', "")
    elif case == "wrong-source":
        verify["env"]["EXPECTED_SOURCE_SHA"] = "${{ github.sha }}"
    elif case == "rebuild":
        steps.append({"run": "uv build --package vuoro-contracts"})
    elif case == "no-attest":
        steps[:] = [s for s in steps if s.get("uses") != "actions/attest@v4"]
    elif case in {"no-verify-tag", "clobber"}:
        create = next(s for s in steps if "gh release create" in s.get("run", ""))
        create["run"] = create["run"].replace("--verify-tag", "" if case == "no-verify-tag" else "--clobber")
    elif case == "early-finalize":
        finish = next(s for s in steps if "gh release edit" in s.get("run", ""))
        steps.remove(finish)
        steps.insert(0, finish)
    with pytest.raises((AssertionError, KeyError, StopIteration)):
        assert_publisher(bad)


@pytest.mark.parametrize("case", ["conditional-production", "short-corpus", "missing-decoder", "unqualified-python", "early-candidate", "no-repository-suite", "extra-ignore", "mutable-wheel", "missing-artifact-id"])
def test_producer_oracle_rejects_incomplete_qualification(case: str) -> None:
    flow = load(REUSABLE)
    assert_reusable(flow)
    bad = copy.deepcopy(flow)
    job = bad["jobs"]["qualify"]
    steps = job["steps"]
    production = next(s for s in steps if s.get("name") == "Run exact production compiler and SDK contract gates")
    ordinary = next(s for s in steps if s.get("name") == "Run repository release gates")
    if case == "conditional-production":
        production["if"] = "${{ inputs.run-repository-suite }}"
    elif case == "short-corpus":
        production["run"] = production["run"].replace("(279, 0, 0, 0)", "(1, 0, 0, 0)")
    elif case == "missing-decoder":
        production["run"] = production["run"].replace("VUORO_CONTRACT_DECODER", "IGNORED_DECODER")
    elif case == "unqualified-python":
        consumer = next(s for s in steps if s.get("name") == "Exercise wheel resources in an isolated consumer")
        consumer["run"] = consumer["run"].replace('uv venv --python "$qualified_python"', 'uv venv --python 3.13.16')
    elif case == "early-candidate":
        candidate = next(s for s in steps if s.get("id") == "candidate")
        steps.remove(candidate)
        steps.insert(0, candidate)
    elif case == "no-repository-suite":
        ordinary["if"] = "${{ false }}"
    elif case == "extra-ignore":
        ordinary["run"] += "\nuv run pytest --ignore=tests/test_contracts_release_metadata.py\n"
    elif case == "mutable-wheel":
        steps.append({"run": "uv build --package vuoro-contracts --wheel --out-dir dist/vuoro-contracts"})
    elif case == "missing-artifact-id":
        del job["outputs"]["artifact_id"]
    with pytest.raises((AssertionError, KeyError, StopIteration)):
        assert_reusable(bad)
