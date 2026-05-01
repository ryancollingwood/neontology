# 05-working-notes

- Updated `pyproject.toml` to list `ladybug` as an optional dependency rather than a required one, and added `ladybug` to the `all` extras group.
- Updated `init_neontology` in `src/neontology/graphconnection.py` to optionally import and initialize `LadybugConfig`.
- Exposed `LadybugConfig` in `src/neontology/__init__.py`.
- Wrote and added `test_ladybug_engine.py` to cover all 10 checks listed in `05-registration-and-testing.md`.
- Updated Pytest markers in `pyproject.toml` to declare engine-specific test markers (`neo4j_engine`, `memgraph_engine`, `networkx_engine`, `ladybug_engine`).
- Solved a `Binder exception` occurring when assigning properties via `MERGE` by explicitly ensuring that the primary key property (`pp_key`) is removed from `set_on_match` and `set_on_create` inside `LadybugEngine.merge_nodes`.
- Handled Ladybug returning nodes nested in a list structure inside result records.
- Enhanced hydration to properly resolve node `_ID` to node models within `_ladybug_rel_to_neontology_rel` utilizing a `hydrated_nodes_by_id` state dictionary. Handled upper-case internal properties `_LABEL`, `_ID`, `_SRC`, and `_DST` that Ladybug occasionally returns.
- Fixed `get_count` by ensuring the return of `[count]` handles list-wrapped primitives returned by Ladybug correctly.
