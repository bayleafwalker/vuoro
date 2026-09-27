from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from edge_support import assertion, identity_headers, key_pair


@pytest.fixture
def keys(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey]:
    return key_pair(tmp_path)


@pytest.fixture
def auth(keys) -> dict[str, str]:
    """Headers carrying a valid gateway assertion for the default caller."""

    return identity_headers(assertion(keys[1]))


@pytest.fixture
def fresh_auth(keys):
    """Mint headers carrying a new assertion per request, as the gateway does."""

    return lambda **claims: identity_headers(assertion(keys[1], **claims))


@pytest.fixture
def oauth_auth(keys) -> dict[str, str]:
    """Headers carrying an assertion minted on the gateway's OAuth /mcp path.

    A composed edge whose audience is still the shell's requires client_id
    and grant_id (agentops#2519), exactly as the gateway mints them for /mcp.
    """

    return identity_headers(
        assertion(keys[1], client_id="claude-connector", grant_id="grant-1")
    )
