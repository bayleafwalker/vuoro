"""Fixed private credential material delivery over an inherited local socket.

No credential value is emitted on stdout, stderr, an argument or a named file.
The containing operation cgroup owns every descendant before private reads.
"""

import array
import os
import socket
import sys

from .protected_copy_codec import _canonical, _closed, _need, _read
from .protected_runtime_credentials import _sealed, _session
from .protected_runtime_registry import load_runtime_profile, _owned_file

_FIELDS = {
    "broker_read": (
        ("server_ca_path", 262144, False),
        ("session_path", 65536, True),
        ("client_certificate_path", 262144, False),
        ("private_key_path", 262144, True),
    ),
    "native_read": (
        ("server_ca_path", 262144, False),
        ("credential_ref", 16384, True),
    ),
}


def main():
    _need(len(sys.argv) == 3)
    group_fd, socket_fd = (int(value) for value in sys.argv[1:])
    _need(group_fd >= 3 and socket_fd >= 3 and group_fd != socket_fd)
    request = _read(sys.stdin.buffer.read(2049), 2048)
    _closed(
        request,
        "schema profile_id kind runtime_profile_sha256 provider_registry_sha256 provider_profile_sha256",
    )
    _need(request["schema"] == "vuoro-private-reader-request/v1")
    _need(request["kind"] in _FIELDS)
    from .protected_provider_registry import (
        load_installed_profile,
        _join_cgroup,
        _validate_active,
    )

    provider = load_installed_profile(request["profile_id"])
    _need(provider.registry_sha256 == request["provider_registry_sha256"])
    _need(provider.profile_sha256 == request["provider_profile_sha256"])
    _join_cgroup(group_fd, provider.data)
    _validate_active(provider)
    channel = socket.socket(fileno=socket_fd)
    descriptors = []
    try:
        _need(
            channel.family == socket.AF_UNIX and channel.type == socket.SOCK_SEQPACKET
        )
        runtime = load_runtime_profile(request["profile_id"])
        _need(runtime.profile_sha256 == request["runtime_profile_sha256"])
        values = runtime.data[request["kind"]]
        for field, maximum, private in _FIELDS[request["kind"]]:
            path = values[field]
            if field == "credential_ref":
                _need(path.startswith("file:"))
                path = path[5:]
            raw = _owned_file(path, maximum, private=private)
            if field == "session_path":
                _session(raw)
            descriptors.append(_sealed(raw))
        _need(load_runtime_profile(runtime.profile_id) == runtime)
        _need(load_installed_profile(runtime.profile_id) == provider)
        receipt = _canonical(
            {
                "schema": "vuoro-private-reader-materials/v1",
                "profile_id": runtime.profile_id,
                "runtime_profile_sha256": runtime.profile_sha256,
                "provider_registry_sha256": provider.registry_sha256,
                "provider_profile_sha256": provider.profile_sha256,
                "kind": request["kind"],
                "count": len(descriptors),
            }
        )
        rights = array.array("i", descriptors)
        _need(
            channel.sendmsg([receipt], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)])
            == len(receipt)
        )
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
        channel.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        # Fixed private helper: no exception rendering or upstream content.
        os._exit(1)
