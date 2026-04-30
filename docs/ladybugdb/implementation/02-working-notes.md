# Working Notes: 02 Schema Management

## Implementation Details

- Added the `_python_type_to_ladybug` function to handle translating Python type annotations to Ladybug DDL type equivalents. We included `json` as a fallback serialization mapping to JSON format via `STRING` as instructed for `list` elements or unhandled types.
- Implemented `_get_node_table_ddl` to handle generating a `CREATE NODE TABLE` string using fields resolved from Pydantic `model_fields`. The PK constraints are generated gracefully on `pp_key`.
- Implemented `_get_rel_table_ddl` which behaves similarly to Node generation, ignoring metadata fields (`source`, `target`, `_merge_on`) and correctly mapping relations with `FROM` and `TO` targets.
- Brought in `_get_existing_tables`, `_ensure_node_table`, and `_ensure_rel_table` methods onto the `LadybugEngine` object to seamlessly query local metadata cache `_known_tables` before generating lazy relations or fetching constraints globally.
- Re-appropriated the constraints abstract interface methods (`apply_constraint`, `drop_constraint`, `get_constraints`) by routing them into DDL equivalents for node setup via table structures vs label-based limitations typical of Neo4j.

## Observations
- Importing `GraphConnection` locally within `apply_constraint` behaves perfectly to bypass cyclical imports while maintaining required global variable scope (`GraphConnection.global_nodes`).
- Ladybug handles constraint mappings uniquely per engine design as structural components versus explicit `ON CREATE` commands; applying a primary key as the default index constraints solves index-related dependencies natively at object instantiations.