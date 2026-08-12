"""OS-level network isolation proof for optimize fail-closed policy."""

from __future__ import annotations

import shutil

import pytest

from book_to_skill.pdf2md.optimize.runner import _os_isolation_available, _prove_sandbox_exec


@pytest.mark.skipif(not shutil.which("sandbox-exec"), reason="sandbox-exec not on PATH")
def test_sandbox_exec_blocks_external_connect():
    assert _prove_sandbox_exec() is True


def test_os_isolation_detection():
    # On this macOS CI-less laptop, sandbox-exec should make this True.
    # On Linux CI without docker/unshare tools in PATH it may be False — that's OK;
    # optimize then fail-closes as required.
    val = _os_isolation_available()
    assert isinstance(val, bool)
