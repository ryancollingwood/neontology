# 04-working-notes

- Implemented `create_nodes`, `merge_nodes`, `merge_relationships`, and `_filters_to_where_clause` for Ladybug engine as required.
- Implemented `_expand_set_clause` helper to build explicit SET clauses.
- The `create_nodes` and `merge_nodes` methods loop through properties and generate single `CREATE` / `MERGE` queries instead of `UNWIND`, and dynamically unpack variables since Ladybug does not support nested property dict access in `UNWIND`.
- Handled rejection of case-insensitive filters in `_filters_to_where_clause`.
- Ensured dependencies like `networkx` and `grandcypher` are available locally for testing (`uv sync --all-extras`).
- Some graph tests still fail in `ladybug-engine` which is expected as we haven't completed `05-registration-and-testing.md`.
