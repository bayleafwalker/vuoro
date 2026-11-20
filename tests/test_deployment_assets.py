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
    assert jobs.count("suspend: true") == 2
    # S2 item 4: the execution (ActionQ) and knowledge (kctl) domains are unbound.
    assert "ACTIONQ_" not in jobs and "kctl" not in jobs
    assert "VUORO_EXECUTION_" not in deployment and "VUORO_KNOWLEDGE_" not in deployment


def test_base_requires_an_immutable_image_replacement() -> None:
    deployment = (BASE / "deployment.yaml").read_text(encoding="utf-8")
    assert "@sha256:" in deployment
    assert "REPLACE_IN_OVERLAY" in (BASE / "runtime-config.yaml").read_text(encoding="utf-8")


def test_compose_backup_preflight_accepts_only_complete_verified_files(tmp_path) -> None:
    import hashlib
    import subprocess
    import sys
    import tarfile

    dump = tmp_path / "workspace.dump"
    dump.write_bytes(b"PGDMP fixture")
    archive = tmp_path / "knowledge.tar"
    manifest = tmp_path / "SHA256SUMS"
    verifier = ROOT / "deploy" / "compose" / "verify-backup.py"

    def write_archive(name: str) -> None:
        with tarfile.open(archive, "w") as tar:
            entry = tarfile.TarInfo(name)
            entry.type = tarfile.DIRTYPE
            tar.addfile(entry)

    def write_checksums() -> None:
        manifest.write_text("".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in (dump, archive)
        ), encoding="ascii")

    def run():
        return subprocess.run([sys.executable, str(verifier), str(tmp_path)],
                              capture_output=True, text=True)

    write_archive("knowledge-objects")
    write_checksums()
    assert run().returncode == 0
    dump.write_bytes(b"PGDMP modified")
    assert "checksum mismatch" in run().stderr
    write_archive("unrelated-directory")
    write_checksums()
    assert "unexpected entry" in run().stderr
    manifest.write_text(manifest.read_text() * 2)
    assert "duplicate checksum entry" in run().stderr
