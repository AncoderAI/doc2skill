"""In-process network isolation for offline PDF processing.

Patches ``socket.socket.connect`` so non-loopback destinations raise
``NetworkBlocked``. This is an inner guard; OS-level isolation is separate.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Optional, Tuple, Union

_original_connect = None
_active = False

Address = Union[Tuple[Any, ...], str]


class NetworkBlocked(RuntimeError):
    """Raised when code attempts a non-loopback network connection."""


def _host_from_address(address: Address) -> Optional[str]:
    if isinstance(address, tuple) and address:
        return str(address[0])
    if isinstance(address, str):
        # AF_UNIX path or opaque string — treat as non-IP unless loopback-like.
        return address
    return None


def _is_loopback_host(host: Optional[str]) -> bool:
    if host is None:
        return False
    h = host.strip().lower()
    if h in {"localhost", "::1"}:
        return True
    # Strip IPv6 zone / brackets if present: "[::1]", "127.0.0.1"
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if "%" in h:
        h = h.split("%", 1)[0]
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def install_guard(allow_loopback: bool = True) -> None:
    """Patch ``socket.socket.connect``: non-loopback addresses raise NetworkBlocked."""
    global _original_connect, _active

    if _active:
        return

    if _original_connect is None:
        _original_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: Address) -> None:  # type: ignore[override]
        host = _host_from_address(address)
        if allow_loopback and _is_loopback_host(host):
            return _original_connect(self, address)  # type: ignore[misc]
        if not allow_loopback and _is_loopback_host(host):
            raise NetworkBlocked(
                f"loopback connection blocked by net_guard: {address!r}"
            )
        raise NetworkBlocked(
            f"outbound network connection blocked by net_guard: {address!r}"
        )

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    _active = True


def uninstall_guard() -> None:
    """Restore the original ``socket.socket.connect`` if a guard is active."""
    global _active
    if not _active:
        return
    if _original_connect is not None:
        socket.socket.connect = _original_connect  # type: ignore[method-assign]
    _active = False


def is_active() -> bool:
    """Return whether the network guard is currently installed."""
    return _active
