from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest
from vuoro_reconciler.signing import SigningKey

GPG_MISSING = shutil.which("gpg") is None


def _run(*args: str, cwd: str | None = None, env: dict[str, str] | None = None) -> None:
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True)
    assert result.returncode == 0, f"{args}: {result.stderr}"


@pytest.fixture
def reconciler_signing_key(tmp_path: Path) -> SigningKey:
    """A throwaway, passphrase-less GPG key in an isolated keyring: the
    reconciler's own key, exactly as a real deployment would inject one
    (just not from a real, persistent keyring)."""

    if GPG_MISSING:
        pytest.skip("gpg is not installed in this environment")
    gnupghome = tmp_path / "gnupghome"
    gnupghome.mkdir(mode=0o700)
    env = {"GNUPGHOME": str(gnupghome)}
    param_file = tmp_path / "keyparam"
    param_file.write_text(
        "%no-protection\n"
        "Key-Type: EDDSA\n"
        "Key-Curve: ed25519\n"
        "Name-Real: Vuoro Reconciler\n"
        "Name-Email: reconciler@vuoro.test\n"
        "Expire-Date: 0\n"
        "%commit\n"
    )
    import os

    full_env = {**os.environ, **env}
    _run("gpg", "--batch", "--pinentry-mode", "loopback", "--gen-key", str(param_file), env=full_env)
    listing = subprocess.run(
        ["gpg", "--list-secret-keys", "--with-colons"],
        env=full_env,
        capture_output=True,
        text=True,
        check=True,
    )
    key_id = next(
        line.split(":")[4] for line in listing.stdout.splitlines() if line.startswith("sec:")
    )
    return SigningKey(
        key_format="openpgp",
        signing_key=key_id,
        committer_name="Vuoro Reconciler",
        committer_email="reconciler@vuoro.test",
        env=env,
    )


@pytest.fixture
def bare_remote(tmp_path: Path) -> Path:
    """A bare local git repository standing in for a forge: real git, no
    network. Seeded with one commit on `main` so intents have a base_commit
    to target."""

    bare = tmp_path / "remote.git"
    _run("git", "init", "--bare", "-q", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    seed.mkdir()
    _run("git", "init", "-q", "-b", "main", str(seed))
    _run("git", "-C", str(seed), "config", "user.name", "Seeder")
    _run("git", "-C", str(seed), "config", "user.email", "seeder@vuoro.test")
    (seed / "docs").mkdir()
    (seed / "docs" / "readme.md").write_text("old\n")
    _run("git", "-C", str(seed), "add", "-A")
    _run("git", "-C", str(seed), "commit", "-q", "-m", "seed")
    _run("git", "-C", str(seed), "push", str(bare), "main")
    return bare


def base_commit_of(bare_remote: Path) -> str:
    result = subprocess.run(
        ["git", "--git-dir", str(bare_remote), "rev-parse", "main"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()
