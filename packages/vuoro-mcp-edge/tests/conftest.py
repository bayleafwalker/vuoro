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
