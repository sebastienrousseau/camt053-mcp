# Copyright (C) 2023-2026 Sebastien Rousseau.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Model Context Protocol (MCP) server for Camt053.

This server exposes the Camt053 library's ISO 20022 ``camt.05x`` capabilities
as MCP tools so that any MCP-compatible client (Claude Desktop, IDEs, agents)
can discover message types and return reasons, inspect input schemas, validate
records and financial identifiers, parse incoming bank statements, and generate
validated reversing-entry XML.

The headline workflow is one-shot reversing-entry generation: read an incoming
camt.053 statement, find the entries carrying a return reason code (e.g. AC04
Closed Account), and emit a validated camt.053.001.14 reversal document.

Every tool is a thin, typed wrapper over :mod:`camt053.services` -- the single
shared facade also used by the CLI, REST API, and LSP server -- so all
interfaces behave identically. Tools return JSON-serializable data (dicts,
lists, or strings); on a :class:`ValueError` or :class:`camt053.exceptions.\
Camt053Error` they return an ``{"error": ...}`` payload rather than raising.

Launching the server:
    * As a console script (installed with the ``servers`` extra)::

        camt053-mcp

    * Programmatically::

        from camt053_mcp.server import main
        main()

    * In an MCP client config (e.g. Claude Desktop ``claude_desktop_config.json``)::

        {
          "mcpServers": {
            "camt053": {
              "command": "camt053-mcp"
            }
          }
        }

The server communicates over stdio (FastMCP's default transport).
"""

import json
from typing import Annotated, Any

from camt053 import services
from camt053.compliance import (
    CBPR_CUTOVER_DATE,
)
from camt053.compliance import (
    check_cbpr_readiness as _check_cbpr_readiness,
)
from camt053.exceptions import Camt053Error
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.prompts.base import AssistantMessage, UserMessage
from mcp.types import ToolAnnotations
from pydantic import Field

from camt053_mcp import __version__, classify, rulebook
from camt053_mcp import export_journal as _export_journal

server = FastMCP("camt053")
# FastMCP does not expose a version kwarg; without this override the
# MCP SDK's own version leaks into serverInfo.version, breaking
# manifest/runtime coherence checks (e.g. Glama scoring).
server._mcp_server.version = __version__

# Shared MCP tool annotations. These hints let MCP clients (and the Glama
# quality grader) reason about a tool's safety, cacheability, and
# auto-approval eligibility *without* executing it. Every tool is
# classified by its ACTUAL behaviour, not blanket-applied.
#
# Almost every tool here is a pure, side-effect-free reader: it computes
# solely over its arguments (an XML string, a message-type name, a record
# list) or over data bundled with the server, writes nothing, and touches
# neither the filesystem nor the network. Those are ``_PURE_READ`` --
# read-only, idempotent, non-destructive, closed-world. No tool in this
# server reads a caller-supplied filesystem path or mutates persistent
# state, so no ``_FS_READ`` / ``_WRITE`` variants are needed.
_PURE_READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

# ``classify_entry`` is the sole exception: it performs an MCP *Sampling*
# call, delegating an LLM completion to the connecting client. It changes
# no state (read-only, non-destructive) but reaches an external system and
# is non-deterministic, so it is open-world and NOT idempotent -- the same
# entry may classify differently across calls.
_SAMPLING = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)


def _paginate(
    entries: list[dict],
    offset: int,
    limit: int | None,
) -> list[dict] | dict[str, Any]:
    """Apply optional pagination to a list of entry dicts.

    When ``limit`` is ``None`` the full ``entries`` list is returned unchanged,
    preserving the behaviour expected by existing callers. Otherwise a
    paginated envelope ``{"total", "offset", "limit", "entries"}`` is returned,
    where ``entries`` is the ``offset:offset + limit`` slice.

    A negative ``offset`` or a negative ``limit`` yields an ``{"error": ...}``
    payload, consistent with the module's error convention.

    Args:
        entries: The full list of entry dicts to paginate.
        offset: The zero-based index of the first entry to return.
        limit: The maximum number of entries to return, or ``None`` for all.
    """
    if offset < 0:
        return {"error": "offset must be non-negative"}
    if limit is not None and limit < 0:
        return {"error": "limit must be non-negative"}
    if limit is None:
        return entries
    return {
        "total": len(entries),
        "offset": offset,
        "limit": limit,
        "entries": entries[offset : offset + limit],
    }


@server.tool(title="List camt.05x message types", annotations=_PURE_READ)
def list_message_types() -> list[dict]:
    """List every supported ISO 20022 camt.05x message type and its name.

    Use this first, before any validation or generation call, to discover the
    exact ``message_type`` strings this server accepts. For the return-reason
    codes rather than message types, call ``list_return_reasons`` instead.

    Returns a list of ``{"message_type": ..., "name": ...}`` dictionaries, one
    per supported message type (e.g. ``camt.053.001.14``).
    """
    try:
        return services.list_message_types()
    except (ValueError, Camt053Error) as exc:
        return [{"error": str(exc)}]


@server.tool(title="List ISO return reason codes", annotations=_PURE_READ)
def list_return_reasons() -> list[dict]:
    """List every known ISO external return reason code with its name.

    Use this to discover the ``reason_code`` values that ``filter_entries`` and
    ``generate_reversal`` accept (e.g. ``AC04`` Closed Account). For the
    supported message types rather than reason codes, use ``list_message_types``.

    Returns a list of ``{"code": ..., "name": ...}`` dictionaries (e.g.
    ``{"code": "AC04", "name": "Closed Account Number"}``).
    """
    try:
        return services.list_return_reasons()
    except (ValueError, Camt053Error) as exc:
        return [{"error": str(exc)}]


@server.tool(title="Get required input fields", annotations=_PURE_READ)
def get_required_fields(
    message_type: Annotated[
        str,
        Field(
            description=(
                "A supported ISO 20022 camt.05x message type string, e.g. "
                "'camt.053.001.14'. Call list_message_types first to discover "
                "the exact accepted values."
            )
        ),
    ],
) -> list[str]:
    """List only the required input field names for a camt message type.

    Use this for a quick checklist of the mandatory columns before building
    reversing-entry records. When you need full type/format constraints (not
    just which fields are required), call ``get_input_schema`` instead.

    Args:
        message_type: A supported ISO 20022 camt.05x message type.
    """
    try:
        return services.get_required_fields(message_type)
    except (ValueError, Camt053Error) as exc:
        return [f"error: {exc}"]


@server.tool(title="Get input JSON Schema", annotations=_PURE_READ)
def get_input_schema(
    message_type: Annotated[
        str,
        Field(
            description=(
                "A supported ISO 20022 camt.05x message type string, e.g. "
                "'camt.053.001.14'. Call list_message_types first to discover "
                "the exact accepted values."
            )
        ),
    ],
) -> dict:
    """Return the full JSON Schema for a message type's flat input record.

    Use this to learn every field, its type, and its constraints before
    assembling records, or to drive a form/UI. For just the required-field
    names use ``get_required_fields``; to actually check records against this
    schema use ``validate_records``.

    Args:
        message_type: A supported ISO 20022 camt.05x message type.
    """
    try:
        return services.get_input_schema(message_type)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool(title="Validate records against schema", annotations=_PURE_READ)
def validate_records(
    message_type: Annotated[
        str,
        Field(
            description=(
                "A supported ISO 20022 camt.05x message type string, e.g. "
                "'camt.053.001.14', whose input JSON Schema the records are "
                "checked against. Call list_message_types to discover accepted "
                "values and get_input_schema to see the constraints."
            )
        ),
    ],
    records: Annotated[
        list[dict],
        Field(
            description=(
                "One or more flat reversing-entry records (each a dict of "
                "field name to value) to validate row-by-row against the "
                "message type's input JSON Schema."
            )
        ),
    ],
) -> dict:
    """Validate flat records against a message type's input JSON Schema.

    Use this on in-memory reversing-entry records to catch structural/type
    errors per row before generation. To validate a whole camt.05x *document*
    (XML) against its XSD instead, use ``validate_statement``.

    Returns a report ``{"valid": bool, "total": int, "valid_count": int,
    "errors": [...]}``.

    Args:
        message_type: A supported ISO 20022 camt.05x message type.
        records: One or more flat reversing-entry records to validate.
    """
    try:
        return services.validate_records(message_type, records)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool(title="Validate IBAN, BIC or LEI", annotations=_PURE_READ)
def validate_identifier(
    kind: Annotated[
        str,
        Field(
            description=(
                "The identifier type to validate: one of 'iban', 'bic', or "
                "'lei' (case-insensitive)."
            )
        ),
    ],
    value: Annotated[
        str,
        Field(
            description=(
                "The identifier value to check, matching the chosen kind "
                "(e.g. an IBAN, an 8- or 11-character BIC, or a 20-character "
                "LEI). Whitespace/case handling follows the underlying "
                "validator."
            )
        ),
    ],
) -> dict:
    """Validate a single financial identifier (IBAN, BIC, or LEI).

    Use this for a one-off identifier check with a clear pass/fail. To validate
    identifiers embedded across a whole batch of records, prefer
    ``validate_records`` rather than calling this per field.

    Returns ``{"kind": str, "value": str, "valid": bool}``.

    Args:
        kind: One of ``"iban"``, ``"bic"``, or ``"lei"`` (case-insensitive).
        value: The identifier value to check.
    """
    try:
        return services.validate_identifier(kind, value)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool(title="Parse camt.05x statement XML", annotations=_PURE_READ)
def parse_statement(
    xml: Annotated[
        str,
        Field(
            description=(
                "The raw camt.05x statement XML document as a string, with its "
                "root camt <Document> element. Returned verbatim from the bank; "
                "no file path is accepted."
            )
        ),
    ],
) -> dict:
    """Parse an incoming camt.05x statement XML string into structured data.

    Use this to turn a raw statement into a navigable dict (header, statements,
    accounts, balances, entries). To pull just the flat entry list use
    ``list_entries``; to only check the document is schema-valid use
    ``validate_statement``.

    Returns the parsed document as a JSON-serialisable dict (group header plus
    statements, each with its account, balances, and entries), or an
    ``{"error": ...}`` payload if the XML cannot be parsed.

    Args:
        xml: The raw statement XML as a string.
    """
    try:
        return services.parse_statement(xml)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool(title="Validate statement against XSD", annotations=_PURE_READ)
def validate_statement(
    xml: Annotated[
        str,
        Field(
            description=(
                "The raw camt.05x statement XML document as a string, with its "
                "root camt <Document> element. Validated against the matching "
                "ISO 20022 XSD; no file path is accepted."
            )
        ),
    ],
) -> dict:
    """Validate an incoming camt.05x statement XML against its XSD schema.

    Use this to confirm a document is well-formed and schema-valid before
    processing it. This checks XSD conformance only; for the Nov 2026 CBPR+
    business rules use ``check_cbpr_readiness``, and to extract the data use
    ``parse_statement``.

    Detects the document's message type, validates it against the matching
    ISO 20022 schema, and returns a report ``{"valid": bool, "message_type":
    str, "errors": [...]}``. A well-formed but schema-invalid document yields
    ``valid=False`` with a populated ``errors`` list (and the detected
    ``message_type``); a valid one yields ``valid=True`` with no errors.

    Returns an ``{"error": ...}`` payload instead if the XML cannot be parsed
    (e.g. it is malformed or is not a camt ``Document``).

    Args:
        xml: The raw statement XML as a string.
    """
    try:
        return services.validate_statement(xml)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool(title="Check CBPR+ Nov 2026 readiness", annotations=_PURE_READ)
def check_cbpr_readiness(
    xml: Annotated[
        str,
        Field(
            description=(
                "The raw camt.05x statement XML document as a string, audited "
                "against the CBPR+ Nov 2026 acceptance rules (schema version "
                "and structured postal addresses). Rejected by the hardened "
                "pre-flight if it carries a DOCTYPE/ENTITY or is oversized."
            )
        ),
    ],
) -> dict:
    """Check a camt.053 statement against the CBPR+ Nov 2026 acceptance rules.

    Use this to audit a statement for the business-rule changes (schema
    version, structured postal addresses) enforced from the Nov 2026 cutover.
    For plain XSD schema validity use ``validate_statement`` instead; for just
    the cutover date use ``get_cbpr_cutover_date``.

    A coordinated CBPR+ / Fedwire / CHAPS / T2 cutover lands on
    **14-16 November 2026**: unstructured-only postal addresses get rejected,
    ``camt.110/111`` exceptions and investigations become mandatory, and T2S
    R2026.NOV upgrades camt.053 / 054 to schema revision MR2026.

    This tool walks the supplied payload and reports every issue that will
    fail the Nov 2026 acceptance rules:

    * **Schema version** vs the CBPR+ current set (``camt.053.001.08`` /
      ``camt.053.001.13``); ``.02``-``.07`` are flagged as deprecated
      warnings; unknown / non-camt.053 namespaces as errors.
    * **Postal addresses**: every ``<PstlAdr>`` is classified as fully
      structured, hybrid, or **unstructured-only** (``<AdrLine>`` without
      ``<TwnNm>`` + ``<Ctry>`` siblings, the Nov 2026 reject case).

    Returns a dictionary ``{"cbpr_ready": bool, "schema_version": str | None,
    "checked_at": ISO-8601 UTC, "cutover_date": "2026-11-16",
    "issues": [...], "summary": {...}}``. ``cbpr_ready`` is ``True`` iff no
    ``severity="error"`` issue was raised. An ``{"error": ...}`` envelope
    is returned instead if the XML is malformed or refused by the
    hardened pre-flight (DOCTYPE / ENTITY / oversized payload).

    Args:
        xml: The raw camt.05x statement XML as a string.
    """
    try:
        return _check_cbpr_readiness(xml)
    except (ValueError, Camt053Error) as exc:
        return {"error": str(exc)}


@server.tool(title="Get CBPR+ cutover date", annotations=_PURE_READ)
def get_cbpr_cutover_date() -> dict:
    """Return the official CBPR+ / Nov 2026 cutover date as ISO 8601.

    Use this to quote the enforcement date directly, without parsing a
    document. To actually audit a statement against the rules that take effect
    on that date, call ``check_cbpr_readiness`` instead.

    The cutover (``2026-11-16``) is the date after which the rules checked
    by ``check_cbpr_readiness`` are enforced by the major clearing systems;
    payments that fail will be rejected at receive-time. Surfaced as a
    discrete tool so agents can quote it directly without having to call
    a readiness check first.
    """
    return {"cutover_date": CBPR_CUTOVER_DATE}


@server.tool(title="Cite payments rulebook clause", annotations=_PURE_READ)
def cite_rulebook(
    scheme: Annotated[
        str,
        Field(
            description=(
                "The rulebook scheme to cite: one of 'SEPA', 'CBPR+', or "
                "'HVPS+' (case-sensitive)."
            )
        ),
    ],
    version: Annotated[
        str,
        Field(
            description=(
                "The rulebook version, e.g. '2025' or '2026'. Use "
                "list_rulebook_clauses to see which versions exist per scheme."
            )
        ),
    ],
    clause: Annotated[
        str,
        Field(
            description=(
                "A kebab-case clause identifier (e.g. 'iban-only') as returned "
                "by list_rulebook_clauses for the chosen scheme and version."
            )
        ),
    ],
) -> dict:
    """Return a curated payments-rulebook citation for a single clause.

    Use this to quote one specific rule (with its canonical source URL) once
    you know the ``scheme``/``version``/``clause``. To discover which clauses
    exist first, call ``list_rulebook_clauses``.

    Looks up one well-known rule across the SEPA, CBPR+, and HVPS+
    rulebooks and returns a short summary together with the canonical
    source URL so an agent can quote the rule and the operator can
    verify it against the official document.

    The registry is a curated convenience layer, not a verbatim
    reproduction of copyrighted text. Always defer to ``source_url``
    for authoritative wording before relying on a citation for
    compliance or contractual decisions; the returned ``disclaimer``
    field repeats this for the calling agent.

    Args:
        scheme: One of ``"SEPA"``, ``"CBPR+"``, or ``"HVPS+"`` (case
            sensitive).
        version: The rulebook version (e.g. ``"2025"`` or ``"2026"``).
        clause: A kebab-case clause identifier from
            ``list_rulebook_clauses``.

    Returns:
        A citation dict ``{"scheme", "version", "clause", "title",
        "summary", "source_url", "as_of", "disclaimer"}`` or an
        ``{"error": ...}`` payload if the citation is not in the
        registry.
    """
    return rulebook.cite(scheme, version, clause)


@server.tool(title="List rulebook clauses", annotations=_PURE_READ)
def list_rulebook_clauses(
    scheme: Annotated[
        str | None,
        Field(
            description=(
                "Restrict the listing to one scheme ('SEPA', 'CBPR+', or "
                "'HVPS+'). None (the default) returns clauses for all schemes."
            )
        ),
    ] = None,
    version: Annotated[
        str | None,
        Field(
            description=(
                "Restrict the listing to one rulebook version, e.g. '2026'. "
                "None (the default) returns clauses for all versions."
            )
        ),
    ] = None,
) -> list[dict]:
    """List the curated rulebook clauses the server can cite, optionally filtered.

    Use this to browse the citation registry and pick a ``clause`` id; then pass
    that id to ``cite_rulebook`` to fetch the full summary and source URL.

    Returns the full registry, optionally filtered by ``scheme`` and /
    or ``version``. Use the resulting ``clause`` values as input to
    ``cite_rulebook``.

    Args:
        scheme: Restrict to one scheme (e.g. ``"SEPA"``). ``None``
            returns all schemes.
        version: Restrict to one version (e.g. ``"2026"``). ``None``
            returns all versions.
    """
    return rulebook.list_clauses(scheme=scheme, version=version)


@server.tool(
    title="Export statement to journal entries", annotations=_PURE_READ
)
def export_journal(
    xml: Annotated[
        str,
        Field(
            description=(
                "The raw camt.053 statement XML document as a string; its "
                "booked entries are reshaped into journal-entry payloads."
            )
        ),
    ],
    target: Annotated[
        str,
        Field(
            description=(
                "The accounting platform to shape payloads for: 'xero' "
                "(BankTransactions) or 'qbo' (QuickBooks Online JournalEntry). "
                "Defaults to 'xero'; call list_export_journal_targets for the "
                "current valid values."
            )
        ),
    ] = "xero",
) -> dict:
    """Export a camt.053 statement as accounting-platform journal-entry payloads.

    Use this to reshape a statement's booked entries into ready-to-POST Xero or
    QuickBooks payloads (the tool builds the payloads only; it does not call any
    external API or write files). To discover the valid ``target`` values first,
    call ``list_export_journal_targets``.

    Parses the supplied statement and re-shapes every booked entry
    into a target-specific journal-entry payload ready for direct
    POST to the accounting platform's REST API.

    Supported targets (see ``camt053_mcp.export_journal.SUPPORTED_TARGETS``):

    * ``"xero"`` - returns a list of Xero ``BankTransactions``
      payloads. Each entry maps to ``{Type, Reference, Date,
      BankAccount, Contact, LineAmountTypes, CurrencyCode,
      LineItems}``; CRDT entries become ``Type=RECEIVE`` and DBIT
      entries ``Type=SPEND``.
    * ``"qbo"`` - returns a list of QuickBooks Online
      ``JournalEntry`` payloads. Each entry produces a balanced
      two-line journal (one to the bank account, one to a clearing
      account; sign flipped on debit entries).

    Operator-specific values (account codes, contact identifiers,
    realm IDs) appear as ``"OPERATOR_FILL"`` placeholders so the
    operator knows exactly what still needs wiring. The response's
    ``placeholder_count`` field reports the total.

    NetSuite + SAP S/4HANA targets are tracked as a follow-up in #17.

    Args:
        xml: The raw camt.053 statement XML as a string.
        target: One of ``"xero"`` or ``"qbo"`` (default ``"xero"``).

    Returns:
        ``{"target", "entries", "placeholder_count", "placeholder_field"}``
        on success, or ``{"error": ...}`` on failure (unsupported
        target / malformed XML / parse refusal).
    """
    return _export_journal.export(xml, target)


@server.tool(title="List journal export targets", annotations=_PURE_READ)
def list_export_journal_targets() -> list[str]:
    """List the accounting-platform targets the ``export_journal`` tool supports.

    Use this to tell a user which ``target`` values ``export_journal`` accepts
    before invoking it. This lists export destinations only; for the LLM
    classifier's category vocabulary use ``list_classify_entry_categories``.

    Returns the sorted list of valid ``target`` arguments accepted by
    ``export_journal`` (``["qbo", "xero"]`` today). NetSuite and SAP
    S/4HANA support is a tracked follow-up.
    """
    return sorted(_export_journal.SUPPORTED_TARGETS)


@server.tool(title="Classify entry via LLM sampling", annotations=_SAMPLING)
async def classify_entry(
    ctx: Context,
    entry: Annotated[
        dict,
        Field(
            description=(
                "A single statement entry dict, in the shape returned by "
                "parse_statement / list_entries, to classify into one category."
            )
        ),
    ],
    categories: Annotated[
        list[str] | None,
        Field(
            description=(
                "The candidate categories the model must choose exactly one "
                "from. None (the default) uses the built-in default list "
                "exposed by list_classify_entry_categories."
            )
        ),
    ] = None,
) -> dict:
    """Classify one statement entry into a category via MCP LLM Sampling.

    Use this when you want a semantic, model-driven label for an entry (payroll,
    fee, refund, …) rather than a deterministic rule match. Because it delegates
    an LLM completion to the client it is open-world and non-idempotent; for the
    fixed candidate categories it chooses from, call
    ``list_classify_entry_categories`` first.

    Uses the **MCP Sampling** protocol primitive: the server (this
    process) asks the *client* (the agent's host application) to
    perform an LLM completion on the server's behalf, then receives
    the model's structured response. Keeps every LLM call in the
    operator's existing model contract (privacy, billing, audit).

    The model is asked to choose exactly one category from
    ``categories`` (or :data:`camt053_mcp.classify.DEFAULT_CATEGORIES`
    if ``None`` is passed) and return a structured
    ``{category, confidence, explanation}`` payload.

    Clients that do not support Sampling will get an
    ``{"error": "..."}`` envelope and can fall back to a rules-only
    classifier.

    Args:
        ctx: The FastMCP Context (auto-injected; provides
            ``session.create_message``).
        entry: A statement entry dict (the shape returned by
            ``parse_statement`` / ``list_entries``).
        categories: The candidate categories. ``None`` uses the
            built-in default list (12 common payment buckets).

    Returns:
        ``{"category", "confidence", "explanation"}`` on success or
        ``{"error": "..."}`` on Sampling failure / malformed model
        response / out-of-vocabulary category.
    """
    return await classify.classify_entry(ctx, entry, categories)


@server.tool(title="List classifier categories", annotations=_PURE_READ)
def list_classify_entry_categories() -> list[str]:
    """List the default candidate categories the ``classify_entry`` tool uses.

    Use this to quote the built-in category vocabulary to a user before running
    the LLM classifier. This is a static list lookup (no model call); to
    actually classify an entry, call ``classify_entry``.

    Operators can override the list per call; this tool exposes the
    default the prompt template ships with so an agent can quote them
    to the user before invoking the classifier.
    """
    return list(classify.DEFAULT_CATEGORIES)


@server.tool(title="List all statement entries", annotations=_PURE_READ)
def list_entries(
    xml: Annotated[
        str,
        Field(
            description=(
                "The raw camt.05x statement XML document as a string; every "
                "booked entry across all its statements is returned."
            )
        ),
    ],
    offset: Annotated[
        int,
        Field(
            description=(
                "Zero-based index of the first entry to return. Applies only "
                "when limit is given; must be non-negative. Defaults to 0."
            )
        ),
    ] = 0,
    limit: Annotated[
        int | None,
        Field(
            description=(
                "Maximum number of entries to return, starting at offset. None "
                "(the default) returns the full unpaginated list; a non-None "
                "value returns a {total, offset, limit, entries} envelope. Must "
                "be non-negative."
            )
        ),
    ] = None,
) -> list[dict] | dict[str, Any]:
    """List every booked entry across all statements in a camt.05x document.

    Use this to get the flat, paginable entry list from a statement. To keep
    only the entries carrying a given return-reason code use ``filter_entries``;
    for the full nested document structure use ``parse_statement``.

    When ``limit`` is ``None`` (the default) the full list of entries is
    returned. When ``limit`` is given, a paginated envelope ``{"total",
    "offset", "limit", "entries"}`` is returned instead, exposing the
    ``offset:offset + limit`` slice. A negative ``offset`` or ``limit`` yields
    an ``{"error": ...}`` payload.

    Args:
        xml: The raw statement XML as a string.
        offset: The zero-based index of the first entry to return (paginated
            mode only; default ``0``).
        limit: The maximum number of entries to return, or ``None`` for the
            full list (default ``None``).
    """
    try:
        entries = services.list_entries(xml)
    except (ValueError, Camt053Error) as exc:
        return [{"error": str(exc)}]
    return _paginate(entries, offset, limit)


@server.tool(title="Filter entries by reason code", annotations=_PURE_READ)
def filter_entries(
    xml: Annotated[
        str,
        Field(
            description=(
                "The raw camt.05x statement XML document as a string; only its "
                "entries carrying the given return reason code are returned."
            )
        ),
    ],
    reason_code: Annotated[
        str,
        Field(
            description=(
                "The ISO external return reason code to match, e.g. 'AC04' "
                "Closed Account (the default). Call list_return_reasons for the "
                "full set of accepted codes."
            )
        ),
    ] = "AC04",
    offset: Annotated[
        int,
        Field(
            description=(
                "Zero-based index of the first matching entry to return. "
                "Applies only when limit is given; must be non-negative. "
                "Defaults to 0."
            )
        ),
    ] = 0,
    limit: Annotated[
        int | None,
        Field(
            description=(
                "Maximum number of matching entries to return, starting at "
                "offset. None (the default) returns the full unpaginated list; "
                "a non-None value returns a {total, offset, limit, entries} "
                "envelope. Must be non-negative."
            )
        ),
    ] = None,
) -> list[dict] | dict[str, Any]:
    """List only the statement entries carrying a given return reason code.

    Use this to preview exactly which entries a reversal would touch before
    calling ``generate_reversal`` with the same ``reason_code``. For every entry
    regardless of reason code use ``list_entries`` instead.

    When ``limit`` is ``None`` (the default) the full list of matching entries
    is returned, preserving the behaviour expected by existing callers. When
    ``limit`` is given, a paginated envelope ``{"total", "offset", "limit",
    "entries"}`` is returned instead, exposing the ``offset:offset + limit``
    slice. A negative ``offset`` or ``limit`` yields an ``{"error": ...}``
    payload.

    Args:
        xml: The raw statement XML as a string.
        reason_code: The ISO external return reason to match (default
            ``"AC04"`` Closed Account).
        offset: The zero-based index of the first entry to return (paginated
            mode only; default ``0``).
        limit: The maximum number of entries to return, or ``None`` for the
            full list (default ``None``).
    """
    try:
        entries = services.filter_entries(xml, reason_code)
    except (ValueError, Camt053Error) as exc:
        return [{"error": str(exc)}]
    return _paginate(entries, offset, limit)


@server.tool(title="Generate reversal document", annotations=_PURE_READ)
def generate_reversal(
    xml: Annotated[
        str,
        Field(
            description=(
                "The raw incoming camt.053 statement XML document as a string; "
                "the entries carrying reason_code are reversed into a new "
                "camt.053.001.14 document."
            )
        ),
    ],
    reason_code: Annotated[
        str,
        Field(
            description=(
                "The ISO external return reason code whose entries are "
                "reversed, e.g. 'AC04' Closed Account (the default). Preview "
                "the matches with filter_entries using the same code; call "
                "list_return_reasons for all accepted codes."
            )
        ),
    ] = "AC04",
) -> str:
    """Generate a validated camt.053.001.14 reversal document from a statement.

    This is the headline one-shot workflow: pass an incoming statement and a
    return-reason code and get back the reversal XML (nothing is written to
    disk). Preview which entries will be reversed first with ``filter_entries``
    using the same ``reason_code``.

    This is the headline one-shot workflow: parse the incoming camt.053, pick
    the entries with the requested return reason (e.g. AC04 Closed Account),
    and emit a validated camt.053.001.14 reversal statement.

    Returns the validated XML document as a string, or an ``{"error": ...}``
    payload (serialized) if generation fails.

    Args:
        xml: The raw incoming statement XML as a string.
        reason_code: The ISO external return reason to reverse (default
            ``"AC04"``).
    """
    try:
        return services.generate_reversal(xml, reason_code)
    except (ValueError, Camt053Error) as exc:
        return json.dumps({"error": str(exc)})


@server.resource("camt053://return-reasons", title="Return reason catalog")
def return_reason_catalog() -> str:
    """Expose the ISO external return-reason catalog as a JSON resource.

    Returns the full list of ``{"code", "name"}`` return-reason dictionaries
    (from :func:`camt053.services.list_return_reasons`) serialised as JSON, so
    an agent can load the catalog as reference context without calling a tool.
    On a :class:`ValueError` or
    :class:`camt053.exceptions.Camt053Error` an ``{"error": ...}`` payload is
    returned instead (serialised), consistent with the server's tools.
    """
    try:
        return json.dumps(services.list_return_reasons())
    except (ValueError, Camt053Error) as exc:
        return json.dumps({"error": str(exc)})


@server.resource("camt053://message-types", title="Message type catalog")
def message_type_catalog() -> str:
    """Expose the supported camt.05x message types as a JSON resource.

    Returns the list of ``{"message_type", "name"}`` dictionaries (from
    :func:`camt053.services.list_message_types`) serialised as JSON, so an agent
    can load the supported message types as reference context without calling a
    tool. On a :class:`ValueError` or
    :class:`camt053.exceptions.Camt053Error` an ``{"error": ...}`` payload is
    returned instead (serialised), consistent with the server's tools.
    """
    try:
        return json.dumps(services.list_message_types())
    except (ValueError, Camt053Error) as exc:
        return json.dumps({"error": str(exc)})


@server.resource(
    "camt053://session/{session_id}/bank/{bic}",
    title="Bank session context",
)
def bank_session_context(
    session_id: Annotated[
        str,
        Field(
            description=(
                "An opaque, agent-chosen session tag (chat id, workflow id, or "
                "operator label). The server is stateless and echoes it back; "
                "it only namespaces the (session, bank) context."
            )
        ),
    ],
    bic: Annotated[
        str,
        Field(
            description=(
                "The bank's BIC8 (head office) or BIC11 (branch) code. Its "
                "characters 5-6 are read as the ISO 3166-1 country used to "
                "recommend rulebook clauses."
            )
        ),
    ],
) -> str:
    """Stable per-session, per-bank context for multi-bank workflows.

    A templated MCP Resource that gives an agent a stable URI namespace
    for the (session, bank) pair it is reasoning about. The server is
    stateless: the URI's ``session_id`` is opaque to the server and
    treated as an agent-chosen tag (a chat id, a workflow id, an
    operator-set label). The ``bic`` is the bank's BIC8 or BIC11.

    The resource returns a JSON dict with everything the agent
    typically needs to "anchor" itself when working with one bank's
    statements:

    * ``session_id`` / ``bic`` echoed back so the agent can confirm
      its URI was parsed correctly.
    * ``bic_country`` — the ISO-3166-1 country code embedded in the
      BIC (characters 5-6); ``None`` if the BIC is malformed.
    * ``bic_kind`` — ``"BIC8"`` (8-char head office) or
      ``"BIC11"`` (full 11-char branch); ``None`` if malformed.
    * ``recommended_rulebook_clauses`` — the curated rulebook clause
      identifiers most relevant to the bank's likely jurisdiction
      (SEPA for EU/UK BICs; CBPR+ + HVPS+ for everyone).
    * ``cbpr_cutover_date`` — the well-known Nov 2026 cutover date.

    Multiple agents can share one server: agent A's
    ``camt053://session/A/bank/NWBKGB2L`` and agent B's
    ``camt053://session/B/bank/NWBKGB2L`` are distinct namespaces
    even though the underlying bank context is identical.
    """
    payload = _bank_session_payload(session_id, bic)
    return json.dumps(payload)


def _bank_session_payload(session_id: str, bic: str) -> dict[str, Any]:
    """Build the JSON payload for the ``bank_session_context`` resource."""
    bic_upper = bic.upper()
    bic_country: str | None = None
    bic_kind: str | None = None
    if len(bic_upper) in (8, 11) and bic_upper.isalnum():
        bic_country = bic_upper[4:6]
        bic_kind = "BIC8" if len(bic_upper) == 8 else "BIC11"

    eu_uk_countries = {
        "AT",
        "BE",
        "BG",
        "CY",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "FR",
        "GB",
        "GR",
        "HR",
        "HU",
        "IE",
        "IT",
        "LT",
        "LU",
        "LV",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SE",
        "SI",
        "SK",
    }
    recommended: list[str] = []
    if bic_country in eu_uk_countries:
        recommended.extend(
            [
                "SEPA/2025/iban-only",
                "SEPA/2025/remittance-info-max-140",
                "SEPA/2025/verification-of-payee",
            ]
        )
    recommended.extend(
        [
            "CBPR+/2026/structured-address-mandate-nov-2026",
            "CBPR+/2026/uetr-mandatory",
            "HVPS+/2026/t2-rtgs-uplift-mr2026",
        ]
    )

    return {
        "session_id": session_id,
        "bic": bic_upper,
        "bic_country": bic_country,
        "bic_kind": bic_kind,
        "recommended_rulebook_clauses": recommended,
        "cbpr_cutover_date": CBPR_CUTOVER_DATE,
    }


@server.prompt(title="Preview and confirm a reversal")
def reversal_preview(
    reason_code: Annotated[
        str,
        Field(
            description=(
                "The ISO external return reason code the generated guidance "
                "previews and reverses, e.g. 'AC04' Closed Account (the "
                "default). Call list_return_reasons for all accepted codes."
            )
        ),
    ] = "AC04",
) -> list[UserMessage | AssistantMessage]:
    """Guide an agent through previewing and confirming a reversal.

    Returns a multi-step message template that walks an agent through the
    headline reversal workflow for a given return reason code: parse the
    statement, preview the entries that would be reversed via
    ``filter_entries``, confirm with the operator, then call
    ``generate_reversal``. The flow is parameterised by ``reason_code`` so the
    same guidance can target any ISO external return reason.

    Args:
        reason_code: The ISO external return reason to preview (default
            ``"AC04"`` Closed Account).
    """
    return [
        UserMessage(
            "I want to reverse the entries in a camt.053 statement that "
            f"carry the return reason code {reason_code}. Walk me through it "
            "safely, one step at a time, and wait for my confirmation before "
            "generating anything."
        ),
        AssistantMessage(
            "We'll do this in four steps:\n"
            "1. Parse the statement: call `parse_statement` with the raw "
            "statement XML so we can see its structure.\n"
            f"2. Preview the reversals: call `filter_entries` with that XML "
            f'and reason_code="{reason_code}" to list exactly which entries '
            "would be reversed. For a large statement, pass `limit` (and "
            "`offset`) to page through the matches.\n"
            "3. Confirm: review the previewed entries together and explicitly "
            "confirm the reversal is correct before proceeding.\n"
            f"4. Generate: once confirmed, call `generate_reversal` with the "
            f'same XML and reason_code="{reason_code}" to emit the validated '
            "camt.053.001.14 reversal document.\n"
            "Please share the statement XML and we'll start at step 1."
        ),
    ]


@server.prompt(title="Reconcile against pain.001 batch")
def reconcile_against_pain001() -> list[UserMessage | AssistantMessage]:
    """Guide an agent through reconciling a camt.053 statement against a pain.001 batch.

    Returns a multi-step message template that walks an agent through
    matching booked entries on a bank statement back to the original
    payment-initiation batch that produced them: parse both sides,
    pair on ``EndToEndId``, surface unmatched entries on either side
    and propose explanations.

    No parameters - the workflow is symmetric, the user supplies both
    payloads at the first step.
    """
    return [
        UserMessage(
            "I have a camt.053 statement from my bank and the original "
            "pain.001 credit-transfer batch we sent earlier. I need to "
            "reconcile: which payments cleared, which are missing, and "
            "which entries on the statement don't match anything we "
            "initiated. Walk me through it carefully."
        ),
        AssistantMessage(
            "We'll do this in five steps:\n"
            "1. Parse the statement: call `parse_statement` with the "
            "camt.053 XML to extract every booked entry.\n"
            "2. Read the pain.001: load the original batch and extract "
            "the list of (EndToEndId, amount, currency, counterparty) "
            "triples that were initiated.\n"
            "3. Pair on EndToEndId: every statement entry has an "
            "`end_to_end_id` field (under transaction details). Match "
            "the two lists on this key.\n"
            "4. Report exceptions: list (a) initiated payments missing "
            "from the statement (in flight or rejected), (b) statement "
            "entries we did not initiate (cross-postings, fees, errors), "
            "and (c) matches where amount or counterparty differ "
            "(currency conversion or partial settlement).\n"
            "5. Propose next actions for each exception bucket "
            "(re-send, query the bank, post to suspense).\n"
            "Please share the camt.053 statement first; we'll start at "
            "step 1."
        ),
    ]


@server.prompt(title="Find duplicate entries")
def find_duplicate_entries() -> list[UserMessage | AssistantMessage]:
    """Guide an agent through finding duplicate entries within a statement.

    Returns a multi-step message template that walks an agent through
    flagging suspected duplicates on a camt.053 statement: parse the
    statement, group entries on the dedupe key
    ``(GrpHdr/MsgId, Stmt/Id, Stmt/ElctrncSeqNb)``, then surface any
    pair of entries with the same amount / counterparty / value date
    that the dedupe key did not already catch.
    """
    return [
        UserMessage(
            "Find any duplicate entries on this camt.053 statement. I "
            "want both exact duplicates (same entry posted twice) and "
            "suspected duplicates (same amount, counterparty, and value "
            "date but different references) flagged separately, with a "
            "confidence note on each."
        ),
        AssistantMessage(
            "We'll do this in four steps:\n"
            "1. Parse the statement: call `parse_statement` with the "
            "raw XML.\n"
            "2. Exact duplicates: group every entry on its "
            "(reference, amount, currency, value_date) tuple. Any group "
            "of size 2+ is an exact duplicate; report each with its "
            "entry references.\n"
            "3. Suspected duplicates: within each statement, group the "
            "remaining entries on (amount, currency, value_date, "
            "counterparty_account). Pairs in the same group with "
            "different references and posting times within 5 minutes "
            "are suspected duplicates. Report each pair with the "
            "matching fields and the diverging ones.\n"
            "4. For each flagged group, suggest the most likely cause "
            "(true duplicate from a retry; legitimate split payment; "
            "reversal pair where one half is missing the reversal "
            "indicator) and the recommended next action.\n"
            "Please share the camt.053 statement and we'll start at "
            "step 1."
        ),
    ]


@server.prompt(title="Match entries to invoices")
def match_to_invoice_set() -> list[UserMessage | AssistantMessage]:
    """Guide an agent through matching statement entries to a set of invoices.

    Returns a multi-step message template that walks an agent through
    matching incoming credits on a camt.053 statement against an
    accounts-receivable invoice ledger: parse the statement, narrow
    to credits, then pair on (amount, currency) first and remittance
    information second, surfacing under- / over-payments and unmatched
    invoices.
    """
    return [
        UserMessage(
            "Match every incoming credit on this camt.053 statement to "
            "an invoice from my open AR ledger. Tell me which invoices "
            "are paid, which are partly paid, which are over-paid, "
            "which statement credits don't match any invoice, and "
            "which open invoices remain unmatched."
        ),
        AssistantMessage(
            "We'll do this in five steps:\n"
            "1. Parse the statement: call `parse_statement` with the "
            "raw XML; we want every entry where credit_debit is CRDT.\n"
            "2. Tier 1 (exact match): for each credit, look for an "
            "open invoice whose amount, currency, and customer/IBAN "
            "(if known) match exactly.\n"
            "3. Tier 2 (remittance match): for the remaining credits, "
            "scan unstructured remittance text and structured "
            "remittance references for invoice numbers; match on those.\n"
            "4. Tier 3 (partial / over / multi-invoice): for credits "
            "still unmatched, look for combinations of open invoices "
            "that sum to the credit amount (down-payments, batched "
            "payments). Flag confidence levels.\n"
            "5. Report four buckets: paid-in-full, partly-paid (with "
            "outstanding amount), over-paid (with surplus), unmatched "
            "credits (with the suggested next action: query customer, "
            "post to suspense, refund). End with the list of invoices "
            "still open.\n"
            "Please share the camt.053 statement and a JSON array of "
            "open invoices to start step 1."
        ),
    ]


def main() -> None:
    """Run the Camt053 MCP server over stdio (the ``camt053-mcp`` entry point)."""
    server.run()


if __name__ == "__main__":  # pragma: no cover
    main()
