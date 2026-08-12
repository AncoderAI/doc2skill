"""Real (non-mocked) proof that net_guard blocks outbound connects."""

from __future__ import annotations

import socket
import threading

import pytest

from book_to_skill.pdf2md.optimize.net_guard import (
    NetworkBlocked,
    install_guard,
    is_active,
    uninstall_guard,
)


@pytest.fixture(autouse=True)
def _clean_guard():
    uninstall_guard()
    yield
    uninstall_guard()


def test_install_guard_sets_active():
    assert is_active() is False
    install_guard()
    assert is_active() is True


def test_blocks_external_connect_real_socket():
    """Positive proof: a real connect() to an external IP must raise NetworkBlocked.

    Uses 1.1.1.1:443 (Cloudflare DNS). The guard must raise before any bytes
    leave the host — we do not mock socket.connect.
    """
    install_guard(allow_loopback=True)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlocked) as exc_info:
            sock.connect(("1.1.1.1", 443))
        assert "1.1.1.1" in str(exc_info.value)
    finally:
        sock.close()


def test_allows_loopback_connect():
    """Loopback must still work when allow_loopback=True."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    accepted = []

    def _accept():
        conn, _addr = server.accept()
        accepted.append(conn)

    thread = threading.Thread(target=_accept, daemon=True)
    thread.start()

    install_guard(allow_loopback=True)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect(("127.0.0.1", port))
        thread.join(timeout=2.0)
        assert accepted, "loopback connect should succeed under net_guard"
    finally:
        client.close()
        for conn in accepted:
            conn.close()
        server.close()


def test_blocks_external_hostname_via_ip():
    """Connecting by resolved external IP is blocked (no DNS required)."""
    install_guard()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlocked):
            sock.connect(("8.8.8.8", 53))
    finally:
        sock.close()
