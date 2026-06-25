<!-- SPDX-License-Identifier: Apache-2.0 OR MIT -->

# `camt053-mcp` style guide

`camt053-mcp` follows the cross-suite
[`STYLEGUIDE.md`](https://github.com/sebastienrousseau/camt053/blob/main/STYLEGUIDE.md)
maintained in the core repository. That document is the single source of
truth for:

- Voice + spelling conventions (British prose, American code, no em-dashes,
  no emojis outside the standard checkmark/cross in supported-versions
  tables).
- README structure (18-section template + badge order).
- CHANGELOG structure (Keep-a-Changelog + suite Quality gates + Suite
  alignment tables).
- SECURITY.md structure (6-section template including the NIST SSDF
  practice mapping).
- SUPPORT.md / CONTRIBUTING.md structure.
- CI floor (8 gates + release-only gates).
- PR style (conventional commits + signed commits + branch policy).
- Branch naming, issue filing, naming conventions.

## Local additions

`camt053-mcp` adds one suite convention: **MCP tool names use the
`verbNoun` snake_case pattern** (matching the Stripe MCP precedent):

```
list_message_types        # not get_message_types or messageTypes()
check_cbpr_readiness      # not is_cbpr_ready or cbpr_check
cite_rulebook             # not get_rulebook_citation
export_journal            # not journal_export or export_to_accounting
```

This makes tool names read naturally as English imperatives in agent
prompts.

## Updating

If you find divergence between this repo's practice and the core
STYLEGUIDE, the core wins; open a PR to align this repo (and/or fix
the deviation).
