"""Actual temporary Git/OpenPGP and durable original-artifact falsifiers."""

import asyncio
import base64
from dataclasses import dataclass, replace
import gc
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from vuoro_reconciler import protected_git as module
from vuoro_reconciler.execution_journal import ExecutionJournal, install_journal
from vuoro_reconciler.protected_git import (
    GitArtifactProfile,
    ProtectedGit,
    ProtectedGitRefused,
)
from test_protected_copy_codec import (
    canonical,
    decode,
    docs,
    repack,
    sha,
    stamp_evidence,
    wire,
)

GIT = shutil.which("git")
GPG = shutil.which("gpg")
REGISTRY = canonical({"schema": "protected-git-test-registry/v1"})
HOST = "protected-git-test-host"


def run(executable, *args, cwd=None, data=None, home=None):
    env = {
        "PATH": os.defpath,
        "LANG": "C",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    if home is not None:
        env["GNUPGHOME"] = str(home)
    result = subprocess.run(
        [executable, *map(str, args)],
        cwd=cwd,
        input=data,
        capture_output=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.fixture(scope="session")
def keys(tmp_path_factory):
    assert GIT and GPG, "actual qualification requires Git and GnuPG"
    result = []
    for name in ("expected", "foreign"):
        home = tmp_path_factory.mktemp("protected-git-" + name)
        home.chmod(0o700)
        run(
            GPG,
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            name + " <" + name + "@example.invalid>",
            "ed25519",
            "cert,sign",
            "0",
            home=home,
        )
        listing = run(GPG, "--batch", "--with-colons", "--list-secret-keys", home=home)
        primary = next(
            line.split(b":")[9].decode().lower()
            for line in listing.splitlines()
            if line.startswith(b"fpr:")
        )
        run(
            GPG,
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-add-key",
            primary,
            "ed25519",
            "sign",
            "0",
            home=home,
        )
        listing = run(GPG, "--batch", "--with-colons", "--list-secret-keys", home=home)
        fingerprints = [
            line.split(b":")[9].decode().lower()
            for line in listing.splitlines()
            if line.startswith(b"fpr:")
        ]
        result.append(
            {
                "home": home,
                "primary": fingerprints[0],
                "signing": fingerprints[1],
                "public": run(GPG, "--batch", "--export", primary, home=home),
            }
        )
    yield result
    executable = shutil.which("gpgconf")
    if executable:
        for key in result:
            subprocess.run(
                [executable, "--homedir", str(key["home"]), "--kill", "all"],
                capture_output=True,
            )


class Base:
    def __init__(self, repo, *, poison=None, delay=0):
        self.repo, self.poison, self.delay = repo, poison, delay
        self.calls = []

    async def acquire_base(self, profile, oid, git_dir):
        self.calls.append((profile, oid))
        await asyncio.sleep(self.delay)
        shutil.copytree(
            self.repo / ".git" / "objects", git_dir / "objects", dirs_exist_ok=True
        )
        if self.poison == "config":
            with (git_dir / "config").open("ab") as handle:
                handle.write(b'\n[filter "unsafe"]\n\tclean = false\n')
        elif self.poison == "alternates":
            (git_dir / "objects" / "info" / "alternates").write_text(
                str(self.repo / ".git" / "objects") + "\n"
            )
        elif self.poison == "missing":
            shutil.rmtree(git_dir / "objects")
            (git_dir / "objects").mkdir()


class Signer:
    def __init__(self, key, *, fingerprint=None, delay=0, poison=None):
        self.key, self.fingerprint = key, fingerprint or key["signing"]
        self.delay, self.poison = delay, poison
        self.calls = []

    async def sign(self, unsigned):
        self.calls.append(unsigned)
        await asyncio.sleep(self.delay)
        if self.poison == "changed-message":
            unsigned += b"changed\n"
        elif self.poison == "unsigned":
            return b"not a signature"
        process = await asyncio.create_subprocess_exec(
            GPG,
            "--batch",
            "--no-options",
            "--pinentry-mode",
            "loopback",
            "--armor",
            "--detach-sign",
            "--local-user",
            self.fingerprint + "!",
            "--output",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"GNUPGHOME": str(self.key["home"]), "PATH": os.defpath, "LANG": "C"},
        )
        try:
            stdout, stderr = await process.communicate(unsigned)
            assert process.returncode == 0, stderr
            return stdout
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()


def proposal(
    base,
    *,
    patch=None,
    title="Approved exact candidate",
    rationale="Exact rationale",
    source_run=None,
):
    value = wire()
    co = json.loads(base64.b64decode(value["canonical_utf8_b64"]))
    co["parameters"].update(
        base_commit=base,
        title=title,
        rationale=rationale,
        unified_diff=patch
        or "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-old\n+new\n",
    )
    legacy = sha(
        canonical({"schema": "sprintctl-effect-intent/v1", **co["parameters"]}), False
    )
    value["legacy_content_digest"] = legacy
    value["acceptance"]["challenge"]["legacy_content_digest"] = legacy
    entries = docs(value)
    if source_run is not None:
        co["source_run"] = source_run
        for _, document in entries:
            if "proposal_source_run" in document:
                document["proposal_source_run"] = source_run
            if "native_run" in document:
                document["native_run"]["run_id"] = source_run
    artifact = sha(co["parameters"]["unified_diff"].encode())
    for envelope, document in entries:
        if envelope["kind"] == "protected-artifact-verification":
            document["intent_content_digest"] = legacy
            document["artifact_digest"] = artifact
            receipt = document["owner_verification_projection"]["receipt"]
            receipt["canonical_intent_digest"] = legacy
            receipt["artifact"]["digest"] = artifact
            stamp_evidence(document)
    value["canonical_utf8_b64"] = base64.b64encode(canonical(co)).decode()
    repack(value, entries)
    return decode(value), canonical(value)


def profile(effect, key, **changes):
    value = {
        "schema": "vuoro-protected-git-signing-profile/v1",
        "repository": effect.repository,
        "signing_fingerprint": key["signing"],
        "public_key_sha256": sha(key["public"]),
        "author_name": "Protected Author",
        "author_email": "author@example.invalid",
        "committer_name": "Protected Committer",
        "committer_email": "committer@example.invalid",
        "path_allowlist": [],
        "protected_path_patterns": [],
    }
    value.update(changes)
    return GitArtifactProfile(canonical(value), key["public"])


@dataclass
class Case:
    directory: Path
    repo: Path
    base: str
    effect: object
    wire: bytes
    profile: object
    journal: object
    record: object
    base_provider: object
    signer: object
    git: object


def case(
    tmp_path,
    keys,
    *,
    object_format="sha1",
    patch=None,
    title="Approved exact candidate",
    rationale="Exact rationale",
    source_run=None,
    attributes=False,
):
    repo = tmp_path / "base"
    run(GIT, "init", "--quiet", "--object-format=" + object_format, repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "readme.md").write_bytes(b"old\n")
    if attributes:
        (repo / ".gitattributes").write_bytes(b"* text eol=crlf ident filter=unsafe\n")
    run(GIT, "-C", repo, "add", "-A")
    run(
        GIT,
        "-C",
        repo,
        "-c",
        "user.name=Seeder",
        "-c",
        "user.email=seed@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "base",
    )
    base = run(GIT, "-C", repo, "rev-parse", "HEAD").strip().decode()
    effect, raw = proposal(
        base, patch=patch, title=title, rationale=rationale, source_run=source_run
    )
    registered = profile(effect, keys[0])
    directory = tmp_path / "journal"
    install_journal(directory, host_id=HOST, registry_utf8=REGISTRY)
    journal = ExecutionJournal(directory, host_id=HOST, registry_utf8=REGISTRY)
    with journal.cycle():
        record = journal.bind(
            effect,
            protected_copy_utf8=raw,
            provider_profile_sha256="sha256:" + "2" * 64,
            signing_profile_sha256=registered.signing_profile_sha256,
            publication_target_utf8=canonical(
                {
                    "schema": "vuoro-protected-publication-target/v1",
                    "repository": effect.repository,
                    "head_repository": effect.repository,
                    "base_branch": "main",
                }
            ),
            expected_signer_fingerprint=keys[0]["signing"],
            expected_public_key_sha256=sha(keys[0]["public"]),
        )
    base_provider, signer = Base(repo), Signer(keys[0])
    git = ProtectedGit(registered, directory, base_provider, signer)
    return Case(
        directory,
        repo,
        base,
        effect,
        raw,
        registered,
        journal,
        record,
        base_provider,
        signer,
        git,
    )


@pytest.fixture
def prepared_case(tmp_path, keys):
    value = case(tmp_path, keys)
    yield value
    value.journal.close()


def remember(value, artifact):
    with value.journal.cycle():
        value.record = value.journal.record_local_ready(
            value.record.effect_key, artifact.local_ready_utf8
        )


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_actual_signed_original_selfcontained_bundle_recovery_and_no_resign(
    tmp_path, keys, object_format
):
    value = case(tmp_path, keys, object_format=object_format)
    try:

        async def workflow():
            artifact = await value.git.prepare_original(value.effect, value.record)
            ready = json.loads(artifact.local_ready_utf8)
            assert ready["base_parent_oid"] == value.base
            assert len(ready["commit_oid"]) == len(value.base)
            assert (
                ready["signer_fingerprint"] == keys[0]["signing"] != keys[0]["primary"]
            )
            bundle = value.directory / artifact.bundle_relative_path
            assert sha(bundle.read_bytes()) == ready["artifact_sha256"]
            assert (
                bundle.stat().st_mode & 0o777 == 0o600 and bundle.stat().st_nlink == 1
            )
            assert bundle.parent.stat().st_mode & 0o777 == 0o700
            # Complete artifact, interrupted before the journal write: preserve original bytes/OID.
            assert (
                await value.git.prepare_original(value.effect, value.record) == artifact
            )
            assert len(value.signer.calls) == len(value.base_provider.calls) == 1
            remember(value, artifact)
            shutil.rmtree(value.repo)
            assert (
                await value.git.verify_original(
                    value.effect, value.record, ready["commit_oid"]
                )
                == artifact
            )
            assert len(value.signer.calls) == len(value.base_provider.calls) == 1
            with pytest.raises(ProtectedGitRefused):
                await value.git.prepare_original(value.effect, value.record)
            with pytest.raises(ProtectedGitRefused):
                await value.git.verify_original(
                    value.effect, value.record, "b" * len(value.base)
                )

        asyncio.run(workflow())
    finally:
        value.journal.close()


def test_approved_controls_are_content_and_exact_message_not_trailer_authority(
    tmp_path, keys
):
    value = case(
        tmp_path,
        keys,
        title="Title\nVuoro-Protected-Binding: forged",
        rationale="Rationale\n# keep exact\n\n",
        source_run="source-run\nVuoro-Protected-Binding: fake",
    )
    try:
        artifact = asyncio.run(value.git.prepare_original(value.effect, value.record))
        ready = json.loads(artifact.local_ready_utf8)
        message = base64.b64decode(ready["message_utf8_b64"])
        assert message.startswith(
            b"Title\nVuoro-Protected-Binding: forged\n\nRationale\n# keep exact\n\n"
        )
        assert b"source-run\nVuoro-Protected-Binding: fake" not in message
        footer = message.rsplit(b"Vuoro-Protected-Binding: ", 1)[1].strip()
        binding = json.loads(base64.b64decode(footer))
        assert "commit_oid" not in binding
        assert (
            base64.b64decode(binding["source_run_utf8_b64"])
            == b"source-run\nVuoro-Protected-Binding: fake"
        )
        remember(value, artifact)
        assert (
            asyncio.run(value.git.verify_original(value.effect, value.record))
            == artifact
        )
    finally:
        value.journal.close()


@pytest.mark.parametrize("kind", ["foreign", "primary", "changed-message", "unsigned"])
def test_actual_foreign_or_primary_alias_or_modified_signature_refuses_before_bundle(
    prepared_case, keys, kind
):
    value = prepared_case
    if kind == "foreign":
        value.git.signer = Signer(keys[1])
    elif kind == "primary":
        value.git.signer = Signer(keys[0], fingerprint=keys[0]["primary"])
    else:
        value.git.signer = Signer(keys[0], poison=kind)
    with pytest.raises(ProtectedGitRefused, match="^protected-git-refused$") as error:
        asyncio.run(value.git.prepare_original(value.effect, value.record))
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert not (
        value.directory / "artifacts" / value.record.effect_key / "original.bundle"
    ).exists()


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("signing_fingerprint", "a" * 16),
        ("signing_fingerprint", "F" * 40),
        ("author_name", "bad\nname"),
        ("committer_email", "bad@example.invalid\n"),
        ("public_key_sha256", "sha256:" + "0" * 64),
        ("path_allowlist", ["z", "a"]),
        ("repository", ""),
    ],
)
def test_profile_exact_full_fingerprint_key_bytes_identity_and_policy_closure(
    keys, field, replacement
):
    effect = decode()
    with pytest.raises(ProtectedGitRefused):
        profile(effect, keys[0], **{field: replacement})


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("receipt_id", "iar_" + "e" * 32),
        ("canonical_hash", "sha256:" + "e" * 64),
        ("signer_fingerprint", "a" * 40),
        ("public_key_sha256", "sha256:" + "e" * 64),
        ("signing_profile_sha256", "sha256:" + "e" * 64),
        ("frozen_reference_set_sha256", "sha256:" + "e" * 64),
        ("acceptance_revision", True),
        ("intent_revision", True),
    ],
)
def test_redigested_fabricated_journal_binding_never_calls_signer(
    prepared_case, field, replacement
):
    value = prepared_case
    binding = json.loads(value.record.binding_utf8)
    binding[field] = replacement
    raw = canonical(binding)
    record = replace(
        value.record,
        binding_utf8=raw,
        events=(replace(value.record.events[0], payload_utf8=raw),),
    )
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.prepare_original(value.effect, record))
    assert value.signer.calls == [] and value.base_provider.calls == []


@pytest.mark.parametrize("kind", ["config", "alternates", "missing"])
def test_registered_base_callback_cannot_supply_ambient_git_configuration_or_wrong_objects(
    prepared_case, kind
):
    value = prepared_case
    value.git.base_provider = Base(value.repo, poison=kind)
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.prepare_original(value.effect, value.record))
    assert value.signer.calls == []


def test_base_attributes_do_not_transform_approved_bytes_or_execute_filters(
    tmp_path, keys, monkeypatch
):
    trap = tmp_path / "ambient-filter-ran"
    global_config = tmp_path / "ambient.gitconfig"
    global_config.write_text(
        '[filter "unsafe"]\n clean = touch '
        + str(trap)
        + "\n smudge = touch "
        + str(trap)
        + "\n[core]\n hooksPath = "
        + str(tmp_path)
        + "\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GNUPGHOME", "/unusable/private/path")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Ambient Wrong")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "filter.unsafe.clean")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "touch " + str(trap))
    value = case(tmp_path, keys, attributes=True)
    try:
        artifact = asyncio.run(value.git.prepare_original(value.effect, value.record))
        remember(value, artifact)
        assert (
            asyncio.run(value.git.verify_original(value.effect, value.record))
            == artifact
        )
        assert not trap.exists()
    finally:
        value.journal.close()


@pytest.mark.parametrize(
    "patch",
    [
        "diff --git a/docs/readme.md b/docs/readme.md\n--- a/docs/readme.md\n+++ b/docs/readme.md\n@@ -1 +1 @@\n-old\n+new\x00\n",
        "diff --git a/.gitattributes b/.gitattributes\nnew file mode 100644\n--- /dev/null\n+++ b/.gitattributes\n@@ -0,0 +1 @@\n+* filter=unsafe\n",
        "diff --git a/link b/link\nnew file mode 120000\n--- /dev/null\n+++ b/link\n@@ -0,0 +1 @@\n+docs/readme.md\n",
        "diff --git a/script.sh b/script.sh\nnew file mode 100755\n--- /dev/null\n+++ b/script.sh\n@@ -0,0 +1 @@\n+echo bad\n",
        "diff --git a/.forgejo/workflows/ci.yml b/.forgejo/workflows/ci.yml\nnew file mode 100644\n--- /dev/null\n+++ b/.forgejo/workflows/ci.yml\n@@ -0,0 +1 @@\n+malicious: true\n",
    ],
)
def test_actual_git_policy_refuses_before_any_private_signer(tmp_path, keys, patch):
    value = case(tmp_path, keys, patch=patch)
    try:
        with pytest.raises(ProtectedGitRefused):
            asyncio.run(value.git.prepare_original(value.effect, value.record))
        assert not value.signer.calls
    finally:
        value.journal.close()


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("commit_oid", "b" * 40),
        ("tree_oid", "b" * 40),
        ("base_parent_oid", "b" * 40),
        ("message_sha256", "sha256:" + "b" * 64),
        ("signer_fingerprint", "b" * 40),
        ("public_key_sha256", "sha256:" + "b" * 64),
        ("artifact_relative_path", "../original.bundle"),
        ("artifact_sha256", "sha256:" + "b" * 64),
    ],
)
def test_original_local_ready_tuple_each_field_off_cannot_adopt_equal_shape(
    prepared_case, field, replacement
):
    value = prepared_case
    artifact = asyncio.run(value.git.prepare_original(value.effect, value.record))
    remember(value, artifact)
    local = json.loads(artifact.local_ready_utf8)
    local[field] = replacement
    record = replace(
        value.record,
        events=(
            value.record.events[0],
            replace(value.record.events[1], payload_utf8=canonical(local)),
        ),
    )
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.verify_original(value.effect, record))
    assert len(value.signer.calls) == len(value.base_provider.calls) == 1


@pytest.mark.parametrize(
    "kind", ["partial", "symlink", "hardlink", "mode", "hash", "root-replaced"]
)
def test_actual_owned_artifact_and_directory_mutations_fail_closed(
    prepared_case, tmp_path, kind
):
    value = prepared_case
    if kind == "partial":
        directory = value.directory / "artifacts" / value.record.effect_key
        directory.mkdir(parents=True, mode=0o700)
        directory.parent.chmod(0o700)
        with pytest.raises(ProtectedGitRefused):
            asyncio.run(value.git.prepare_original(value.effect, value.record))
        assert value.signer.calls == [] and value.base_provider.calls == []
        return
    artifact = asyncio.run(value.git.prepare_original(value.effect, value.record))
    remember(value, artifact)
    bundle = value.directory / artifact.bundle_relative_path
    if kind == "symlink":
        bundle.rename(bundle.with_name("saved.bundle"))
        bundle.symlink_to(bundle.with_name("saved.bundle"))
    elif kind == "hardlink":
        os.link(bundle, tmp_path / "linked.bundle")
    elif kind == "mode":
        bundle.chmod(0o644)
    elif kind == "hash":
        data = bundle.read_bytes()
        bundle.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    else:
        value.directory.rename(tmp_path / "moved")
        value.directory.mkdir(mode=0o700)
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.verify_original(value.effect, value.record))
    assert len(value.signer.calls) == 1


def test_completed_artifact_is_durable_before_local_ready_and_fsync_uncertainty_never_resigns(
    prepared_case, monkeypatch
):
    value = prepared_case
    real = module.os.fsync
    targets = []

    def observe(fd):
        targets.append(os.readlink("/proc/self/fd/" + str(fd)))
        return real(fd)

    monkeypatch.setattr(module.os, "fsync", observe)
    artifact = asyncio.run(value.git.prepare_original(value.effect, value.record))
    assert (
        str(
            value.directory
            / artifact.bundle_relative_path.replace("original.bundle", "pending.bundle")
        )
        in targets
    )
    assert (
        str(value.directory) in targets
        and str(value.directory / "artifacts") in targets
    )
    assert str((value.directory / artifact.bundle_relative_path).parent) in targets
    assert value.record.events[-1].event_type == "BOUND"
    assert (
        asyncio.run(value.git.prepare_original(value.effect, value.record)) == artifact
    )
    assert len(value.signer.calls) == 1


@pytest.mark.parametrize("stage", ["base", "signer"])
def test_base_and_signer_delays_share_one_total_deadline(
    prepared_case, monkeypatch, stage
):
    value = prepared_case
    monkeypatch.setattr(module, "_TOTAL_SECONDS", 0.20)
    if stage == "base":
        value.base_provider.delay = 0.40
    else:
        value.signer.delay = 0.40
    start = time.monotonic()
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.prepare_original(value.effect, value.record))
    assert time.monotonic() - start < 0.8
    assert not (
        value.directory / "artifacts" / value.record.effect_key / "original.bundle"
    ).exists()


def test_profile_drift_conflicts_before_private_signing(prepared_case):
    value = prepared_case
    binding = json.loads(value.record.binding_utf8)
    # A changed registered identity conflicts with the permanently bound signing profile.
    altered = json.loads(value.profile.signing_profile_utf8)
    altered["committer_name"] = "Different"
    other = ProtectedGit(
        GitArtifactProfile(canonical(altered), value.profile.public_key_utf8),
        value.directory,
        value.base_provider,
        value.signer,
    )
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(other.prepare_original(value.effect, value.record))
    assert (
        binding["signing_profile_sha256"] == value.profile.signing_profile_sha256
        and not value.signer.calls
    )


def test_local_ready_wire_limit_before_base_or_private_signing(tmp_path, keys):
    value = case(tmp_path, keys, rationale="λ" * 8000)
    try:
        with pytest.raises(ProtectedGitRefused):
            asyncio.run(value.git.prepare_original(value.effect, value.record))
        assert value.base_provider.calls == [] and value.signer.calls == []
        assert not (value.directory / "artifacts").exists()
    finally:
        value.journal.close()


def test_same_tree_parent_message_signed_by_same_key_but_different_original_oid_refuses(
    prepared_case, monkeypatch, tmp_path
):
    value = prepared_case
    bound = value.record
    original = asyncio.run(value.git.prepare_original(value.effect, bound))
    remember(value, original)
    first = json.loads(original.local_ready_utf8)
    other_root = tmp_path / "other-original"
    other_root.mkdir(mode=0o700)
    other_git = ProtectedGit(
        value.profile, other_root, value.base_provider, value.signer
    )
    previous_time = module.time.time
    monkeypatch.setattr(module.time, "time", lambda: previous_time() + 10)
    alternative = asyncio.run(other_git.prepare_original(value.effect, bound))
    second = json.loads(alternative.local_ready_utf8)
    assert first["commit_oid"] != second["commit_oid"]
    for field in (
        "tree_oid",
        "base_parent_oid",
        "message_utf8_b64",
        "signer_fingerprint",
        "public_key_sha256",
    ):
        assert first[field] == second[field]
    # Repair the outer bundle checksum to exercise the original OID join itself.
    modified = {**first, "artifact_sha256": second["artifact_sha256"]}
    record = replace(
        value.record,
        events=(
            value.record.events[0],
            replace(value.record.events[1], payload_utf8=canonical(modified)),
        ),
    )
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(other_git.verify_original(value.effect, record))
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(
            value.git.verify_original(value.effect, value.record, second["commit_oid"])
        )
    assert (
        asyncio.run(value.git.verify_original(value.effect, value.record)) == original
    )


@pytest.mark.parametrize("kind", ["inode-replacement", "same-inode-write"])
def test_artifact_changed_during_actual_signature_verification_refuses(
    prepared_case, monkeypatch, kind
):
    value = prepared_case
    artifact = asyncio.run(value.git.prepare_original(value.effect, value.record))
    remember(value, artifact)
    bundle = value.directory / artifact.bundle_relative_path
    real = module._verify_commit

    async def raced(*args, **kwargs):
        result = await real(*args, **kwargs)
        if kind == "inode-replacement":
            copy = bundle.with_name("changed.bundle")
            shutil.copyfile(bundle, copy)
            copy.chmod(0o600)
            os.replace(copy, bundle)
        else:
            data = bundle.read_bytes()
            bundle.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        return result

    monkeypatch.setattr(module, "_verify_commit", raced)
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.verify_original(value.effect, value.record))
    assert len(value.signer.calls) == 1


@pytest.mark.parametrize("stage", ["file", "directory"])
def test_atomic_install_fsync_uncertainty_has_no_ready_signal_and_complete_recovery_never_resigns(
    prepared_case, monkeypatch, stage
):
    value = prepared_case
    real = module.os.fsync
    failed = []
    destination = value.directory / "artifacts" / value.record.effect_key

    def uncertain(fd):
        path = os.readlink("/proc/self/fd/" + str(fd))
        target = (
            str(destination / "pending.bundle") if stage == "file" else str(destination)
        )
        if (
            path == target
            and (destination / "pending.bundle").exists() == (stage == "file")
            and not failed
        ):
            failed.append(path)
            raise OSError("synthetic private fsync uncertainty")
        real(fd)

    monkeypatch.setattr(module.os, "fsync", uncertain)
    with pytest.raises(ProtectedGitRefused) as error:
        asyncio.run(value.git.prepare_original(value.effect, value.record))
    assert failed and error.value.__context__ is None and error.value.__cause__ is None
    assert value.record.events[-1].event_type == "BOUND"
    monkeypatch.setattr(module.os, "fsync", real)
    if stage == "file":
        assert not (destination / "original.bundle").exists()
        with pytest.raises(ProtectedGitRefused):
            asyncio.run(value.git.prepare_original(value.effect, value.record))
    else:
        assert (destination / "original.bundle").exists()
        hardened = []

        def observe(fd):
            hardened.append(os.readlink("/proc/self/fd/" + str(fd)))
            real(fd)

        monkeypatch.setattr(module.os, "fsync", observe)
        artifact = asyncio.run(value.git.prepare_original(value.effect, value.record))
        assert (
            str(destination / "original.bundle") in hardened
            and str(destination) in hardened
        )
        remember(value, artifact)
    assert len(value.signer.calls) == 1


def test_bundle_bound_is_monitored_and_complete_oversized_artifact_is_not_installed(
    prepared_case, monkeypatch
):
    value = prepared_case
    monkeypatch.setattr(module, "_BUNDLE_MAX", 128)
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.prepare_original(value.effect, value.record))
    assert not (
        value.directory / "artifacts" / value.record.effect_key / "original.bundle"
    ).exists()


@pytest.mark.parametrize("kind", ["timeout", "output"])
def test_bounded_runner_kills_actual_subprocess_group_and_diagnostics_do_not_retain_output(
    prepared_case, monkeypatch, tmp_path, kind
):
    value = prepared_case
    child_file = tmp_path / "child.pid"
    shim = tmp_path / "fake-private-git"
    script = f"#!{sys.executable}\nimport os,time\npid=os.fork()\nif pid==0:\n time.sleep(30)\nelse:\n open({str(child_file)!r},'w').write(str(pid))\n"
    script += (
        " os.write(2,b'secret-looking-private-output'*(100000))\n"
        if kind == "output"
        else " time.sleep(30)\n"
    )
    shim.write_text(script)
    shim.chmod(0o700)
    monkeypatch.setattr(module, "_GIT", str(shim))
    monkeypatch.setattr(module, "_TOTAL_SECONDS", 0.20 if kind == "timeout" else 2.0)
    start = time.monotonic()
    with pytest.raises(ProtectedGitRefused, match="^protected-git-refused$") as error:
        asyncio.run(value.git.prepare_original(value.effect, value.record))
    gc.collect()
    assert time.monotonic() - start < 2.5
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert not error.value.__dict__ and str(tmp_path) not in str(error.value)
    assert child_file.exists()
    child = int(child_file.read_text())
    for _ in range(100):
        try:
            state = Path(f"/proc/{child}/stat").read_text().split()[2]
        except FileNotFoundError:
            break
        if state == "Z":
            break
        time.sleep(0.005)
    else:
        pytest.fail("subprocess group child still running after refusal")
    assert not value.signer.calls


@pytest.mark.parametrize(
    "race", ["replace-pending", "write-pending", "replace-final", "write-final"]
)
def test_install_joins_staged_descriptor_and_final_content_before_local_ready(
    prepared_case, monkeypatch, race
):
    value = prepared_case
    real_link, real_fsync = module.os.link, module.os.fsync
    raced = []

    def poison(directory, name):
        path = directory / name
        if race.startswith("replace"):
            path.rename(directory / "displaced.bundle")
        path.write_bytes(b"owner-replaced-non-bundle")
        path.chmod(0o600)
        raced.append(True)

    def link(src, dst, **kwargs):
        directory = Path(os.readlink("/proc/self/fd/" + str(kwargs["src_dir_fd"])))
        if race.endswith("pending") and not raced:
            poison(directory, src)
        return real_link(src, dst, **kwargs)

    def fsync(fd):
        result = real_fsync(fd)
        path = Path(os.readlink("/proc/self/fd/" + str(fd)))
        if (
            race.endswith("final")
            and not raced
            and path.name == value.record.effect_key
        ):
            poison(path, "original.bundle")
        return result

    monkeypatch.setattr(module.os, "link", link)
    monkeypatch.setattr(module.os, "fsync", fsync)
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.prepare_original(value.effect, value.record))
    assert raced and len(value.signer.calls) == 1


def test_multiple_local_phases_share_one_deadline(prepared_case, monkeypatch):
    value = prepared_case
    value.base_provider.delay = 0.6
    value.signer.delay = 0.6
    monkeypatch.setattr(module, "_TOTAL_SECONDS", 1.0)
    start = time.monotonic()
    with pytest.raises(ProtectedGitRefused):
        asyncio.run(value.git.prepare_original(value.effect, value.record))
    assert 0.9 <= time.monotonic() - start < 1.8
    assert len(value.base_provider.calls) == len(value.signer.calls) == 1


def test_direct_caller_cancellation_reaps_actual_group_before_loop_closes(tmp_path):
    child_file = tmp_path / "child.pid"
    shim = tmp_path / "forker"
    shim.write_text(
        f"#!{sys.executable}\nimport os,time\npid=os.fork()\n"
        "if pid==0:\n time.sleep(30)\nelse:\n"
        f" tmp={str(child_file)!r}+'.tmp'\n"
        " with open(tmp,'w') as handle: handle.write(str(pid))\n"
        f" os.replace(tmp,{str(child_file)!r})\n time.sleep(30)\n"
    )
    shim.chmod(0o700)

    def alive(pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
        except (FileNotFoundError, ProcessLookupError):
            return False

    async def workflow():
        runner = module._Runner(tmp_path, asyncio.get_running_loop().time() + 5)
        task = asyncio.create_task(runner.run(str(shim), ()))
        for _ in range(200):
            if child_file.exists():
                break
            await asyncio.sleep(0.005)
        assert child_file.exists()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        for _ in range(100):
            if not alive(int(child_file.read_text())):
                break
            await asyncio.sleep(0.005)
        assert not alive(int(child_file.read_text()))

    asyncio.run(workflow())
    gc.collect()
