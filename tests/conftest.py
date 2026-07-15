"""Shared fixtures for the camt053-mcp test suite."""

import threading
from dataclasses import dataclass

import pytest

#: The bearer token the test HTTP server (fixture ``http_server``) requires.
HTTP_TEST_TOKEN = "test-token-c53"  # noqa: S105


@dataclass(frozen=True)
class HttpServerInfo:
    """Connection details for the in-process test HTTP server.

    Attributes:
        url: The MCP endpoint URL (``http://127.0.0.1:<port>/mcp``).
        token: The bearer token the server requires.
    """

    url: str
    token: str


@pytest.fixture(scope="session")
def http_server():
    """Serve the MCP server over authenticated streamable HTTP.

    Boots the real stack -- FastMCP streamable-HTTP app wrapped in
    ``BearerTokenMiddleware``, served by uvicorn -- in a daemon thread
    on an ephemeral loopback port, and yields an :class:`HttpServerInfo`
    with the endpoint URL and required bearer token. Shared
    session-wide: FastMCP's streamable-HTTP session manager can only be
    run once per server instance, so every HTTP test (auth, tenant
    round-trip, stress) talks to this one server.
    """
    import uvicorn

    import camt053_mcp.server as server_mod
    from camt053_mcp import transport

    # A fresh session manager per pytest session (it is single-run).
    server_mod.server._session_manager = None
    app = transport.build_http_app(server_mod.server, HTTP_TEST_TOKEN)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"
    )
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    deadline = threading.Event()
    for _ in range(200):  # up to ~10 s for slow CI runners
        if uv_server.started:
            break
        deadline.wait(0.05)
    assert uv_server.started, "test HTTP server failed to start"
    port = uv_server.servers[0].sockets[0].getsockname()[1]
    yield HttpServerInfo(
        url=f"http://127.0.0.1:{port}/mcp", token=HTTP_TEST_TOKEN
    )
    uv_server.should_exit = True
    thread.join(timeout=10)


# A camt.053 Bank-to-Customer Statement with three booked entries:
#   * NTRY-0001 - a EUR 1500.00 credit returned AC04 (Closed Account)
#   * NTRY-0002 - a EUR 980.50 credit returned AC06 (Blocked Account)
#   * NTRY-0003 - a EUR 42.00 debit with no return reason
SAMPLE_STATEMENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.14">
  <BkToCstmrStmt>
    <GrpHdr>
      <MsgId>STMT-MSG-0001</MsgId>
      <CreDtTm>2026-06-15T08:00:00</CreDtTm>
    </GrpHdr>
    <Stmt>
      <Id>STMT-0001</Id>
      <ElctrncSeqNb>1</ElctrncSeqNb>
      <CreDtTm>2026-06-15T08:00:00</CreDtTm>
      <Acct>
        <Id><IBAN>GB29NWBK60161331926819</IBAN></Id>
        <Ccy>EUR</Ccy>
        <Ownr><Nm>Acme Treasury Ltd</Nm></Ownr>
        <Svcr><FinInstnId><BICFI>NWBKGB2LXXX</BICFI></FinInstnId></Svcr>
      </Acct>
      <Bal>
        <Tp><CdOrPrtry><Cd>CLBD</Cd></CdOrPrtry></Tp>
        <Amt Ccy="EUR">10000.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Dt><Dt>2026-06-15</Dt></Dt>
      </Bal>
      <Ntry>
        <NtryRef>NTRY-0001</NtryRef>
        <Amt Ccy="EUR">1500.00</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><Dt>2026-06-15</Dt></BookgDt>
        <ValDt><Dt>2026-06-15</Dt></ValDt>
        <NtryDtls><TxDtls>
          <Refs><EndToEndId>E2E-0001</EndToEndId><TxId>TX-0001</TxId></Refs>
          <Amt Ccy="EUR">1500.00</Amt>
          <CdtDbtInd>CRDT</CdtDbtInd>
          <RtrInf><Rsn><Cd>AC04</Cd></Rsn><AddtlInf>Beneficiary account closed</AddtlInf></RtrInf>
          <RltdPties>
            <Dbtr><Nm>Globex SA</Nm></Dbtr>
            <DbtrAcct><Id><IBAN>DE89370400440532013000</IBAN></Id></DbtrAcct>
          </RltdPties>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <NtryRef>NTRY-0002</NtryRef>
        <Amt Ccy="EUR">980.50</Amt>
        <CdtDbtInd>CRDT</CdtDbtInd>
        <Sts><Cd>BOOK</Cd></Sts>
        <BookgDt><Dt>2026-06-15</Dt></BookgDt>
        <NtryDtls><TxDtls>
          <Refs><EndToEndId>E2E-0002</EndToEndId></Refs>
          <RtrInf><Rsn><Cd>AC06</Cd></Rsn></RtrInf>
        </TxDtls></NtryDtls>
      </Ntry>
      <Ntry>
        <NtryRef>NTRY-0003</NtryRef>
        <Amt Ccy="EUR">42.00</Amt>
        <CdtDbtInd>DBIT</CdtDbtInd>
        <Sts>BOOK</Sts>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>"""


@pytest.fixture
def statement_xml() -> str:
    """A camt.053 statement with AC04, AC06, and a plain debit entry."""
    return SAMPLE_STATEMENT_XML
