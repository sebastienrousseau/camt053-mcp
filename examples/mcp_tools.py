#!/usr/bin/env python3
"""Example: call the camt053-mcp server's tools in-process.

Usage:
    pip install camt053-mcp     # requires Python 3.10+
    python examples/mcp_tools.py

The camt053 MCP server (launched as ``camt053-mcp`` over stdio) exposes the
camt053 library to AI agents. This example invokes the same tools directly
through the FastMCP instance, without a transport, to show what an agent would
receive.
"""

import asyncio

from camt053_mcp.server import server

# A camt.053 statement with one entry returned AC04 (Closed Account).
STATEMENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
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
          <RtrInf><Rsn><Cd>AC04</Cd></Rsn><AddtlInf>Closed account</AddtlInf></RtrInf>
        </TxDtls></NtryDtls>
      </Ntry>
    </Stmt>
  </BkToCstmrStmt>
</Document>"""


async def main() -> None:
    tools = await server.list_tools()
    print("Registered MCP tools:", [t.name for t in tools])

    async def call(name, args):
        result = await server.call_tool(name, args)
        # FastMCP returns a (content, structured) tuple or content blocks;
        # pull the first text payload for display.
        content = result[0] if isinstance(result, tuple) else result
        text = content[0].text if content else ""
        return text

    print(
        "list_message_types ->",
        (await call("list_message_types", {}))[:60],
        "…",
    )
    print(
        "filter_entries     ->",
        (await call(
            "filter_entries",
            {"xml": STATEMENT_XML, "reason_code": "AC04"},
        ))[:60],
        "…",
    )
    xml = await call(
        "generate_reversal",
        {"xml": STATEMENT_XML, "reason_code": "AC04"},
    )
    print("generate_reversal  ->", xml[:46], "…")


if __name__ == "__main__":
    asyncio.run(main())
