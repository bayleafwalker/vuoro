"""Fresh private delivery decoded as immutable DATA; never an execution grant."""

from dataclasses import dataclass

from .protected_copy_codec import decode_accepted_page, decode_protected_copy, _need
from .protected_effects import ProtectedEffectData
from .protected_read_transport import (
    PrivateReadTransport,
    ReadoutProfile,
    _async_fixed,
    _fixed,
)


@dataclass(frozen=True, slots=True, repr=False)
class ProtectedCopyRead:
    effect: ProtectedEffectData
    wire_utf8: bytes


class ProtectedLedgerSource:
    @_fixed
    def __init__(self, profile, transport):
        _need(
            type(profile) is ReadoutProfile
            and isinstance(transport, PrivateReadTransport)
            and transport.profile == profile
        )
        profile.__post_init__()
        self._profile, self._transport = profile, transport

    @property
    def profile(self):
        return self._profile

    @_async_fixed
    async def list_accepted(self, cursor=None, limit=64):
        wire = await self._transport.list_accepted(cursor, limit)
        return decode_accepted_page(
            wire,
            namespace_utf8=self.profile.namespace_utf8,
            requested_limit=limit,
            previous_cursor=cursor,
        )

    @_async_fixed
    async def read_copy(self, acceptance_id, listed_metadata=None):
        wire = await self._transport.read_copy(acceptance_id)
        effect = decode_protected_copy(
            wire,
            namespace_utf8=self.profile.namespace_utf8,
            acceptance_id=acceptance_id,
            repository=self.profile.repository,
            listed_metadata=listed_metadata,
        )
        return ProtectedCopyRead(effect, wire)
