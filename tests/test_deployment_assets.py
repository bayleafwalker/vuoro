from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
BASE = ROOT / "deploy" / "kustomize" / "base"


def test_public_packaging_includes_compose_and_neutral_kustomize_base() -> None:
    assert (ROOT / "deploy" / "compose" / "compose.yaml").is_file()
    assert (ROOT / "deploy" / "compose" / "postgres" / "init.sql").is_file()
    assert (BASE / "kustomization.yaml").is_file()
    assert (BASE / "deployment.yaml").is_file()
    assert (BASE / "migration-jobs.yaml").is_file()


def test_base_keeps_runtime_and_migration_credentials_separate() -> None:
    deployment = (BASE / "deployment.yaml").read_text(encoding="utf-8")
    jobs = (BASE / "migration-jobs.yaml").read_text(encoding="utf-8")
    assert "vuoro-runtime-dsns" in deployment
    assert "vuoro-migration-dsns" not in deployment
    assert "vuoro-migration-dsns" in jobs
    assert jobs.count("suspend: true") == 4
    assert "ACTIONQ_RUNTIME_ROLE" in jobs
    assert "--environment-name" in jobs
    assert "--environment-class" in jobs


def test_base_requires_an_immutable_image_replacement() -> None:
    deployment = (BASE / "deployment.yaml").read_text(encoding="utf-8")
    assert "@sha256:" in deployment
    assert "REPLACE_IN_OVERLAY" in (BASE / "runtime-config.yaml").read_text(encoding="utf-8")
