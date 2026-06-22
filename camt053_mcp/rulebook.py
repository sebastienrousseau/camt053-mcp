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

"""Curated payments-rulebook citation registry.

Backs the ``cite_rulebook`` MCP tool. The registry holds a small,
maintained set of well-known rules covering the rulebooks an
LLM-driven payments agent most often needs to reason about:

* **SEPA** — European Payments Council Credit Transfer (SCT) and SCT
  Instant rulebooks.
* **CBPR+** — SWIFT Cross-Border Payments and Reporting Plus market
  practice guidelines.
* **HVPS+** — SWIFT High Value Payments Systems Plus market practice
  guidelines.

Each entry returns a short summary together with the canonical source
URL so agents can quote the rule and the operator can verify it.
Entries are intentionally rule-level summaries, not verbatim
reproductions of copyrighted text; the source URL is the authoritative
reference.

The registry is *curated*, not exhaustive: contributions are
welcome via PR. The keys (``clause`` arguments to the tool) are
short, stable, kebab-case identifiers chosen to read well in agent
prompts (e.g. ``structured-address-mandate-nov-2026``).
"""

from __future__ import annotations

from typing import TypedDict


class RulebookEntry(TypedDict):
    """One curated rulebook clause."""

    scheme: str
    version: str
    clause: str
    title: str
    summary: str
    source_url: str
    as_of: str


_DISCLAIMER = (
    "Curated convenience summary. Always consult the official "
    "scheme document at source_url for authoritative text before "
    "relying on this for compliance or contractual decisions."
)


_ENTRIES: dict[tuple[str, str, str], RulebookEntry] = {}


def _register(entry: RulebookEntry) -> None:
    """Add an entry to the module-level registry.

    Raises ``ValueError`` on duplicate ``(scheme, version, clause)``
    keys to guard against accidental copy-paste collisions when the
    registry grows.
    """
    key = (entry["scheme"], entry["version"], entry["clause"])
    if key in _ENTRIES:
        raise ValueError(f"duplicate rulebook entry: {key}")
    _ENTRIES[key] = entry


# ---------------------------------------------------------------------------
# CBPR+ (SWIFT Cross-Border Payments and Reporting Plus)
# ---------------------------------------------------------------------------

_register(
    {
        "scheme": "CBPR+",
        "version": "2026",
        "clause": "structured-address-mandate-nov-2026",
        "title": "Structured postal addresses mandatory from 14-16 Nov 2026",
        "summary": (
            "From the Nov 14-16 2026 cutover, CBPR+ messages (pacs.008, "
            "pacs.009, camt.053, camt.054 and related) must carry "
            "structured postal addresses (TownName, Country, optionally "
            "PostCode) on debtor, creditor, and ultimate parties. "
            "Unstructured-only addresses (AdrLine without structured "
            "siblings) will be rejected at FINplus."
        ),
        "source_url": (
            "https://www.swift.com/standards/iso-20022-programme/cbpr-plus"
        ),
        "as_of": "2026-06-22",
    }
)

_register(
    {
        "scheme": "CBPR+",
        "version": "2026",
        "clause": "exceptions-investigations-camt-110-111",
        "title": "camt.110 / camt.111 mandated for exceptions & investigations",
        "summary": (
            "From the Nov 2026 cutover, the legacy MT 19x exception and "
            "investigation flows are retired on cross-border and become "
            "camt.110 / camt.111 messages with structured fields, replacing "
            "the free-form MT n92 / n95 / n96 / n99 narrative used previously."
        ),
        "source_url": (
            "https://www.swift.com/standards/iso-20022-programme/cbpr-plus"
        ),
        "as_of": "2026-06-22",
    }
)

_register(
    {
        "scheme": "CBPR+",
        "version": "2026",
        "clause": "uetr-mandatory",
        "title": "UETR mandatory on every payment instruction",
        "summary": (
            "The Unique End-to-end Transaction Reference (UETR, a v4 UUID) "
            "is mandatory on every CBPR+ payment, credit transfer, and "
            "return / cancellation. Banks must propagate the UETR "
            "unchanged through the entire chain so a payment can be "
            "tracked end-to-end via SWIFT gpi."
        ),
        "source_url": ("https://www.swift.com/our-solutions/swift-gpi"),
        "as_of": "2026-06-22",
    }
)


# ---------------------------------------------------------------------------
# SEPA (European Payments Council)
# ---------------------------------------------------------------------------

_register(
    {
        "scheme": "SEPA",
        "version": "2025",
        "clause": "iban-only",
        "title": "IBAN-only payment initiation (BIC no longer required)",
        "summary": (
            "From the 2025 SCT rulebook, a payer's PSP must accept SEPA "
            "Credit Transfer initiations without a BIC: the IBAN alone "
            "uniquely identifies the beneficiary's PSP. PSPs derive the "
            "BIC from the IBAN as needed."
        ),
        "source_url": (
            "https://www.europeanpaymentscouncil.eu/document-library/"
            "rulebooks/sepa-credit-transfer-rulebook"
        ),
        "as_of": "2026-06-22",
    }
)

_register(
    {
        "scheme": "SEPA",
        "version": "2025",
        "clause": "remittance-info-max-140",
        "title": "Remittance information max 140 characters (unstructured)",
        "summary": (
            "Unstructured remittance information (RmtInf/Ustrd) is capped "
            "at 140 characters in SEPA SCT and SCT Instant. Longer "
            "remittance must use structured remittance (RmtInf/Strd) or be "
            "split across multiple credit transfers."
        ),
        "source_url": (
            "https://www.europeanpaymentscouncil.eu/document-library/"
            "rulebooks/sepa-credit-transfer-rulebook"
        ),
        "as_of": "2026-06-22",
    }
)

_register(
    {
        "scheme": "SEPA",
        "version": "2025",
        "clause": "instant-10-seconds",
        "title": "SCT Instant 10-second target settlement",
        "summary": (
            "Under the SCT Instant rulebook the Beneficiary PSP must make "
            "the funds available to the beneficiary within 10 seconds of "
            "the payer's PSP timestamp on the credit transfer. Maximum "
            "amount per Instant transfer is set by the rulebook in force."
        ),
        "source_url": (
            "https://www.europeanpaymentscouncil.eu/document-library/"
            "rulebooks/sepa-instant-credit-transfer-rulebook"
        ),
        "as_of": "2026-06-22",
    }
)

_register(
    {
        "scheme": "SEPA",
        "version": "2025",
        "clause": "verification-of-payee",
        "title": "Verification of Payee mandatory from 9 Oct 2025",
        "summary": (
            "Under the EU Instant Payments Regulation, every Payment "
            "Service Provider offering SCT or SCT Inst must verify, "
            "before the payer authorises the transfer, that the supplied "
            "IBAN matches the supplied payee name. The payer must be "
            "warned of any mismatch."
        ),
        "source_url": (
            "https://www.europeanpaymentscouncil.eu/what-we-do/be-involved/"
            "verification-payee"
        ),
        "as_of": "2026-06-22",
    }
)


# ---------------------------------------------------------------------------
# HVPS+ (SWIFT High Value Payments Systems Plus)
# ---------------------------------------------------------------------------

_register(
    {
        "scheme": "HVPS+",
        "version": "2026",
        "clause": "t2-rtgs-uplift-mr2026",
        "title": "T2 / TARGET2 RTGS schema uplift to MR2026 in Nov 2026",
        "summary": (
            "On the same Nov 14-16 2026 weekend, the Eurosystem T2 RTGS "
            "and T2S systems uplift to maintenance release MR2026. "
            "camt.053 / camt.054 produced by T2 RTGS move to the MR2026 "
            "variant; older variants are no longer accepted."
        ),
        "source_url": (
            "https://www.ecb.europa.eu/paym/target/target2/html/index.en.html"
        ),
        "as_of": "2026-06-22",
    }
)

_register(
    {
        "scheme": "HVPS+",
        "version": "2026",
        "clause": "structured-address-alignment",
        "title": "HVPS+ aligns with CBPR+ structured-address rule",
        "summary": (
            "HVPS+ market practice guidelines align with CBPR+ on the "
            "structured postal address requirement so the same payment "
            "instruction routing across CBPR+ correspondents and HVPS+ "
            "high-value clearing rails works without translation."
        ),
        "source_url": (
            "https://www.swift.com/standards/iso-20022-programme/hvps-plus"
        ),
        "as_of": "2026-06-22",
    }
)


def list_clauses(
    scheme: str | None = None, version: str | None = None
) -> list[dict]:
    """Return the registry entries, optionally filtered.

    Args:
        scheme: Restrict to one scheme (e.g. ``"SEPA"``).
        version: Restrict to one version (e.g. ``"2025"``).
    """
    rows: list[dict] = []
    for entry in _ENTRIES.values():
        if scheme is not None and entry["scheme"] != scheme:
            continue
        if version is not None and entry["version"] != version:
            continue
        rows.append(dict(entry))
    rows.sort(key=lambda r: (r["scheme"], r["version"], r["clause"]))
    return rows


def cite(scheme: str, version: str, clause: str) -> dict:
    """Return one rulebook citation or ``{"error": ...}``.

    Args:
        scheme: ``"SEPA"`` / ``"CBPR+"`` / ``"HVPS+"`` (case sensitive).
        version: e.g. ``"2025"``, ``"2026"``.
        clause: kebab-case identifier from ``list_clauses``.
    """
    entry = _ENTRIES.get((scheme, version, clause))
    if entry is None:
        return {
            "error": (
                f"unknown rulebook citation: scheme={scheme!r} "
                f"version={version!r} clause={clause!r}. "
                "Use list_rulebook_clauses() to discover available entries."
            )
        }
    return {**entry, "disclaimer": _DISCLAIMER}
