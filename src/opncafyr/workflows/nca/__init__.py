"""The NCA workflow pack: import, clean, map columns, pick one analyte, run opnca-r.

This is the clin pharm layer on top of the tool-agnostic core. Planned ports from
makecoolplots (Rust -> Python/R):
- column-role aliases and value checks: crates/mcp-inspect/src/roles.rs
- canonical routes, unit normalisation, BLQ parsing: crates/mcp-inspect/src/nca_input.rs
- analyte choice and "ask, don't guess": crates/mcp-codegen/src/endpoint.rs,
  crates/mcp-core/src/error.rs (needs_choice)
"""
