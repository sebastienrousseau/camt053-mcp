"""The one command line every server in the suite shares.

``_transports`` and ``_cli`` are copied verbatim into each server, so these
tests pin their contract: the flags, the defaults, and what ``run`` asks of the SDK on
either major. The listeners themselves are the SDK's; a fake server
records the call instead of opening a port.
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from camt053_mcp import _cli, _transports, server


class _Modern:
    """A server shaped like mcp 2.x: ``run(transport, **kwargs)``."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def run(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))


class _Legacy(_Modern):
    """A server shaped like mcp 1.x: host and port live on ``settings``."""

    def __init__(self) -> None:
        super().__init__()
        self.settings = argparse.Namespace(host="0.0.0.0", port=1)


def test_flags_and_defaults() -> None:
    parser = argparse.ArgumentParser()
    _cli.add_arguments(parser)
    args = parser.parse_args([])
    assert (args.transport, args.host, args.port) == (
        "stdio",
        "127.0.0.1",
        8000,
    )
    args = parser.parse_args(
        ["--transport", "sse", "--host", "0.0.0.0", "--port", "9"]
    )
    assert (args.transport, args.host, args.port) == ("sse", "0.0.0.0", 9)


def test_unknown_transport_is_rejected_by_the_parser() -> None:
    parser = argparse.ArgumentParser()
    _cli.add_arguments(parser)
    with pytest.raises(SystemExit):
        parser.parse_args(["--transport", "carrier-pigeon"])


def test_stdio_runs_with_no_arguments() -> None:
    srv = _Modern()
    _transports.run(srv)
    assert srv.calls == [((), {})]


def test_streamable_http_on_modern_sdk_passes_host_port_and_path() -> None:
    srv = _Modern()
    _transports.run(srv, "streamable-http", "0.0.0.0", 8080)
    assert srv.calls == [
        (
            ("streamable-http",),
            {"host": "0.0.0.0", "port": 8080, "streamable_http_path": "/mcp"},
        )
    ]


def test_sse_on_modern_sdk_passes_both_paths() -> None:
    srv = _Modern()
    _transports.run(srv, "sse", "127.0.0.1", 8001)
    assert srv.calls == [
        (
            ("sse",),
            {
                "host": "127.0.0.1",
                "port": 8001,
                "sse_path": "/sse",
                "message_path": "/messages/",
            },
        )
    ]


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_legacy_sdk_is_configured_through_settings(transport: str) -> None:
    srv = _Legacy()
    _transports.run(srv, transport, "10.0.0.1", 7000)
    assert (srv.settings.host, srv.settings.port) == ("10.0.0.1", 7000)
    assert srv.calls == [((transport,), {})]


def test_run_rejects_a_bad_transport_and_port() -> None:
    with pytest.raises(ValueError, match="unknown transport"):
        _transports.run(_Modern(), "carrier-pigeon")
    with pytest.raises(ValueError, match="port must be"):
        _transports.run(_Modern(), "sse", port=70000)


def test_serve_wires_the_flags_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        _cli, "run", lambda s, t, h, p: seen.append((s, t, h, p))
    )
    _cli.serve("srv", ["--transport", "sse", "--port", "8002"], "x", "1")
    assert seen == [("srv", "sse", "127.0.0.1", 8002)]


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        _cli.serve("srv", ["--version"], "camt053-mcp", "9.9.9")
    assert info.value.code == 0
    assert "camt053-mcp 9.9.9" in capsys.readouterr().out


def test_main_defaults_to_stdio(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    monkeypatch.setattr(
        server.server, "run", lambda *a, **k: calls.append((a, k))
    )
    server.main([])
    assert calls == [((), {})]
