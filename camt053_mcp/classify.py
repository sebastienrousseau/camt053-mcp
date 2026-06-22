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

"""LLM-driven entry classification via MCP Sampling.

Backs the ``classify_entry`` MCP tool. Uses the **MCP Sampling**
protocol primitive: the server (this process) asks the *client* (the
agent's host application — Claude Desktop, an IDE, a custom shell)
to perform an LLM completion on the server's behalf, then receives
the model's response.

This is the canonical "the server is the camt.053 expert, the
client owns the model" pattern. It keeps every LLM call in the
operator's existing model contract (privacy, billing, audit) and
lets the server stay a pure stateless data layer.

Clients that don't support Sampling will get an
``{"error": "...sampling unsupported..."}`` envelope and can fall
back to a rules-only classifier.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.types import SamplingMessage, TextContent

#: The default category list the prompt asks the model to choose from.
#: Mirrors common bank-statement reconciliation buckets; operators
#: typically supply their own list.
DEFAULT_CATEGORIES: tuple[str, ...] = (
    "payroll",
    "vendor_payment",
    "customer_payment",
    "refund",
    "fee",
    "interest",
    "tax",
    "internal_transfer",
    "subscription",
    "fx_settlement",
    "loan_payment",
    "other",
)


def _classification_prompt(
    entry: dict[str, Any], categories: list[str]
) -> str:
    """Build the user-role prompt for the sampling request."""
    cats_repr = ", ".join(sorted(set(categories)))
    return (
        "You are a payments analyst. Classify the following ISO 20022 "
        "camt.053 booked entry into exactly one of these categories: "
        f"{cats_repr}.\n\n"
        "Return ONLY a JSON object on a single line, no prose, no code "
        "fence:\n"
        '{"category": "<one of the above>", '
        '"confidence": <float 0.0-1.0>, '
        '"explanation": "<one short sentence>"}\n\n'
        f"Entry: {json.dumps(entry, default=str)}"
    )


async def classify_entry(
    ctx,
    entry: dict[str, Any],
    categories: list[str] | None = None,
    *,
    max_tokens: int = 256,
) -> dict[str, Any]:
    """Classify one entry into a category via the client's LLM.

    Args:
        ctx: The FastMCP Context (provides ``session.create_message``).
        entry: A statement entry dict (the shape returned by
            ``parse_statement`` / ``list_entries``).
        categories: The candidate categories. Defaults to
            :data:`DEFAULT_CATEGORIES`.
        max_tokens: The Sampling ``maxTokens`` value.

    Returns:
        ``{"category", "confidence", "explanation"}`` on success or
        ``{"error": "..."}`` if the client refuses Sampling, the
        model returns non-JSON, or a network error fires.
    """
    cats = list(categories) if categories else list(DEFAULT_CATEGORIES)
    prompt = _classification_prompt(entry, cats)

    try:
        result = await ctx.session.create_message(
            messages=[
                SamplingMessage(
                    role="user",
                    content=TextContent(type="text", text=prompt),
                )
            ],
            max_tokens=max_tokens,
        )
    except Exception as exc:  # noqa: BLE001 - we re-package every failure
        return {
            "error": (
                "MCP Sampling failed (the client may not support it). "
                f"Detail: {type(exc).__name__}: {exc}"
            )
        }

    content = getattr(result, "content", None)
    text = getattr(content, "text", None) if content is not None else None
    if not text:
        return {"error": "Sampling returned an empty payload."}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {
            "error": (
                f"Sampling response was not parseable JSON: {exc}. "
                f"Raw text: {text[:200]}"
            )
        }

    return _normalise(parsed, cats)


def _normalise(parsed: Any, cats: list[str]) -> dict[str, Any]:
    """Normalise the LLM response: validate category + confidence."""
    if not isinstance(parsed, dict):
        return {
            "error": (
                f"Sampling response was not a JSON object. Got: {type(parsed).__name__}"
            )
        }

    category = parsed.get("category")
    confidence = parsed.get("confidence")
    explanation = parsed.get("explanation") or ""

    if category not in cats:
        return {
            "error": (
                f"Sampling returned an out-of-vocabulary category: {category!r}. "
                f"Allowed: {cats}"
            )
        }
    if not isinstance(confidence, int | float):
        return {
            "error": (
                f"Sampling returned a non-numeric confidence: {confidence!r}"
            )
        }
    confidence = max(0.0, min(1.0, float(confidence)))
    return {
        "category": category,
        "confidence": confidence,
        "explanation": str(explanation),
    }
