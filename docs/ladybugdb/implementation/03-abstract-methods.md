# Ladybug Engine — Abstract Method Implementations

This file provides complete implementations for all 6 abstract methods defined in `GraphEngineBase`. The schema-related methods (`apply_constraint`, `drop_constraint`, `get_constraints`) were covered in `02-schema-management.md`. This file focuses on the connection and query methods.

All code in this file belongs inside the `LadybugEngine` class body.

---

## `verify_connection()`

```python
def verify_connection(self) -> bool:
    """Verify that the Ladybug connection is working.

    Executes a trivial query. Returns True if it succeeds, False on any error.

    Returns:
        bool: True if the connection is usable, False otherwise.
    """
    try:
        self.conn.execute("RETURN 1")
        return True
    except Exception:
        return False
```

**About `conn.execute()`:** `lb.Connection.execute(query_str, parameters=None)` sends a Cypher query to the Ladybug database and returns a `QueryResult` object. It raises an exception if the query is syntactically invalid or the connection is broken. Returning the result value does not matter here — we only care whether the call succeeded.

**Why catch all exceptions:** `ladybug` does not expose a stable base exception class. Catching `Exception` broadly mirrors the pattern in `Neo4jEngine.verify_connection()`, which uses a bare `except:`.

---

## `close_connection()`

```python
def close_connection(self) -> None:
    """Close the Ladybug connection and database handle.

    Safe to call even if the connection or database is already closed.
    """
    try:
        self.conn.close()
    except Exception:
        pass
    try:
        self.db.close()
    except Exception:
        pass
```

**Why close both `conn` and `db`:** `lb.Connection` holds a query context. `lb.Database` holds the file handle and write-ahead log. Both must be closed to release file locks on the database directory. Always close `conn` before `db`. Both support auto-cleanup on garbage collection, but explicit close is safer for deterministic resource release.

---

## `evaluate_query()`

This is the most complex method. It executes a Cypher query and converts the raw Ladybug results into a `NeontologyResult` containing hydrated `BaseNode` and `BaseRelationship` instances.

### How Ladybug returns query results

`conn.execute(cypher, parameters=params_dict)` returns a `QueryResult`. To iterate rows:

```python
result = conn.execute("MATCH (n:Person) RETURN n")
while result.has_next():
    row = result.get_next()   # returns a list, one element per SELECT column
    value = row[0]            # the first column's value
```

Additional `QueryResult` methods (all confirmed in the official API docs at `https://api-docs.ladybugdb.com/python/ladybug`):
- `get_column_names()` → `list[str]` — column names in SELECT order
- `get_column_data_types()` → `list` — Ladybug type constants per column
- `get_as_df()` → `pandas.DataFrame`
- `get_n(n)` → next `n` rows as a list
- `get_all()` → all remaining rows as a list
- `reset_iterator()` → restart iteration from the beginning
- `get_num_tuples()` → total number of result rows
- `close()` → release result resources

**Column types returned by `get_next()`:**

| Ladybug column type | Python value returned |
|--------------------|-----------------------|
| `STRING`, `INT64`, `DOUBLE`, `BOOLEAN` | Native Python `str`, `int`, `float`, `bool` |
| `DATE` | Python `datetime.date` |
| `TIMESTAMP` | Python `datetime.datetime` |
| `BLOB` | Python `bytes` |
| Node (returned by `RETURN n`) | Python `dict` — see node dict structure below |
| Relationship (returned by `RETURN r`) | Python `dict` — see relationship dict structure below |
| `NULL` | Python `None` |

**Node dict structure** (when a whole node variable is returned, e.g. `RETURN n`):

```python
{
    "_id": {"offset": <int>, "table": <int>},   # internal Ladybug identity — do not persist
    "_label": "Person",                          # the node table name / label
    "name": "Alice",                             # property values
    "age": 30,
    # ... all other properties declared in the NODE TABLE schema
}
```

**Relationship dict structure** (when a whole relationship variable is returned, e.g. `RETURN r`):

```python
{
    "_id": {"offset": <int>, "table": <int>},
    "_src": {"offset": <int>, "table": <int>},  # internal ID of source node
    "_dst": {"offset": <int>, "table": <int>},  # internal ID of target node
    "_label": "KNOWS",                           # the relationship type
    "since": datetime.date(2023, 1, 1),          # property values
    # ... all other properties declared in the REL TABLE schema
}
```

**Important:** These internal keys (`_id`, `_src`, `_dst`, `_label`) must be filtered out before constructing Pydantic model instances. The helper methods below do this with `if not k.startswith("_")`.

### Helper: converting a Ladybug node dict to a `BaseNode`

```python
def _ladybug_node_to_neontology_node(
    self,
    node_dict: dict,
    node_classes: dict,
) -> Optional["BaseNode"]:
    """Convert a Ladybug node dict to a neontology BaseNode instance.

    Args:
        node_dict: Dict returned by ladybug for a node value. Must contain
                   '_label' and all property keys.
        node_classes: Mapping of label strings to BaseNode subclasses.
                      Passed in from evaluate_query.

    Returns:
        A BaseNode instance, or None if the label is not in node_classes
        (with a warning emitted).
    """
    import warnings

    label = node_dict.get("_label")
    if label not in node_classes:
        warnings.warn(f"Received node with label '{label}' but no matching class found in node_classes.")
        return None

    node_class = node_classes[label]

    # Build the properties dict by filtering out Ladybug internal keys
    # that start with '_' — these are not model fields.
    props = {k: v for k, v in node_dict.items() if not k.startswith("_")}

    return node_class(**props)
```

### Helper: converting a Ladybug relationship dict to a `BaseRelationship`

Relationship conversion requires access to already-hydrated node instances so that `source` and `target` can be set. The nodes are passed in as a pre-built lookup dict.

```python
def _ladybug_rel_to_neontology_rel(
    self,
    rel_dict: dict,
    rel_classes: dict,
    node_classes: dict,
    hydrated_nodes_by_label_pp: dict,
) -> Optional["BaseRelationship"]:
    """Convert a Ladybug relationship dict to a neontology BaseRelationship instance.

    Args:
        rel_dict: Dict returned by ladybug for a relationship value. Must contain
                  '_label', '_src', '_dst', and property keys.
        rel_classes: Mapping of relationship type strings to RelationshipTypeData.
        node_classes: Mapping of label strings to BaseNode subclasses.
        hydrated_nodes_by_label_pp: Dict keyed by '{label}:{primary_property_value}'
                                     to already-hydrated BaseNode instances. Used to
                                     attach source and target nodes to the relationship.
                                     This dict is built from all nodes returned in the
                                     same query result.

    Returns:
        A BaseRelationship instance, or None on failure (with a warning).
    """
    import warnings

    rel_type = rel_dict.get("_label")
    rel_type_data = rel_classes.get(rel_type)

    if rel_type_data is None:
        warnings.warn(
            f"Received relationship of type '{rel_type}' but no matching class found in relationship_classes. "
            "Did you define the class before initializing Neontology?"
        )
        return None

    # Filter out internal Ladybug keys (_id, _src, _dst, _label)
    props = {k: v for k, v in rel_dict.items() if not k.startswith("_")}

    # Attach source and target nodes.
    # Ladybug does not directly embed node data inside relationship dicts.
    # When a query returns both nodes and relationships (e.g. RETURN n, r, o),
    # the nodes are returned as separate columns. The caller (evaluate_query)
    # builds hydrated_nodes_by_label_pp from those node columns, then passes it here.
    #
    # If the query only returned the relationship (not the surrounding nodes),
    # source and target will be None. BaseRelationship validation may fail in that case.
    # Users should always return surrounding nodes when relationship hydration is needed:
    #   MATCH (n)-[r:KNOWS]->(o) RETURN n, r, o
    rel_class = rel_type_data.relationship_class
    src_class = rel_type_data.source_class if hasattr(rel_type_data, "source_class") else None
    tgt_class = rel_type_data.target_class if hasattr(rel_type_data, "target_class") else None

    # Find source and target nodes in the hydrated node dict.
    # Since we don't have a direct node reference in the relationship dict,
    # look for nodes that match the relationship's source/target node classes.
    source_node = None
    target_node = None

    if src_class:
        src_label = src_class.__primarylabel__
        for key, node in hydrated_nodes_by_label_pp.items():
            if key.startswith(f"{src_label}:"):
                source_node = node
                break

    if tgt_class:
        tgt_label = tgt_class.__primarylabel__
        for key, node in hydrated_nodes_by_label_pp.items():
            if key.startswith(f"{tgt_label}:"):
                target_node = node
                break

    props["source"] = source_node
    props["target"] = target_node

    try:
        return rel_class(**props)
    except Exception as exc:
        warnings.warn(f"Failed to hydrate relationship '{rel_type}': {exc}")
        return None
```

**Note on `RelationshipTypeData`:** This is a named tuple or dataclass defined in `src/neontology/baserelationship.py`. Check its actual field names before referencing `source_class` and `target_class`. Use the `TYPE_CHECKING` import for the type hint. The `relationship_class` attribute is confirmed in `neo4jengine.py` line 135.

### The `evaluate_query` method

```python
def evaluate_query(
    self,
    cypher: str,
    params: dict[str, Any] = {},
    node_classes: dict = {},
    relationship_classes: dict = {},
) -> NeontologyResult:
    """Execute a Cypher query and return hydrated neontology records.

    Args:
        cypher: The Cypher query string. Use $param_name placeholders for
                parameters — ladybug uses the same $param syntax as Neo4j.
        params: Dict of parameter name → value. Passed directly to
                conn.execute(parameters=...). Do not include the $ prefix in dict keys.
        node_classes: Dict mapping label strings to BaseNode subclasses.
                      Used to hydrate returned node values.
        relationship_classes: Dict mapping relationship type strings to
                              RelationshipTypeData. Used to hydrate returned
                              relationship values.

    Returns:
        NeontologyResult with records, nodes, relationships, and paths populated.
    """
    # Execute the query.
    # lb.Connection.execute() signature:
    #   execute(query: str, parameters: dict | None = None) -> QueryResult
    # The parameters dict keys are plain strings (without '$').
    query_result = self.conn.execute(cypher, parameters=params if params else None)

    raw_records = []
    hydrated_nodes_by_label_pp: dict[str, "BaseNode"] = {}
    all_rels: list["BaseRelationship"] = []

    # Get column names so we know which columns contain nodes vs relationships
    # vs scalar values.
    column_names = query_result.get_column_names()

    # Iterate all rows.
    while query_result.has_next():
        row = query_result.get_next()
        record: dict[str, dict] = {"nodes": {}, "relationships": {}, "paths": {}}

        for col_name, value in zip(column_names, row):
            if value is None:
                continue

            if isinstance(value, dict) and "_label" in value:
                if "_src" in value:
                    # This is a relationship dict
                    neontology_rel = self._ladybug_rel_to_neontology_rel(
                        value, relationship_classes, node_classes, hydrated_nodes_by_label_pp
                    )
                    if neontology_rel:
                        record["relationships"][col_name] = neontology_rel
                        all_rels.append(neontology_rel)
                else:
                    # This is a node dict
                    neontology_node = self._ladybug_node_to_neontology_node(value, node_classes)
                    if neontology_node:
                        record["nodes"][col_name] = neontology_node
                        key = f"{neontology_node.__primarylabel__}:{neontology_node.get_pp()}"
                        hydrated_nodes_by_label_pp[key] = neontology_node

        raw_records.append(record)

    # Deduplicate nodes: if the same node appears in multiple rows, keep only one.
    unique_nodes = list(hydrated_nodes_by_label_pp.values())

    return NeontologyResult(
        records_raw=raw_records,
        records=raw_records,
        nodes=unique_nodes,
        relationships=all_rels,
        paths=[],  # Path hydration is not implemented in this initial version
    )
```

**About path hydration:** Ladybug supports named paths and returns them as `RECURSIVE_REL` values (a `STRUCT` of node and relationship lists). Path hydration is complex and not required for the core neontology API (`create`, `merge`, `match`). Leave it as an empty list for the initial implementation. Add a `# TODO: implement path hydration` comment if desired.

**About `parameters=None` vs `parameters={}`:** The Ladybug API accepts `None` to mean "no parameters". Pass `None` when `params` is empty rather than an empty dict, to match the expected API contract.

---

## `evaluate_query_single()`

```python
def evaluate_query_single(self, cypher: str, params: dict[str, Any] = {}) -> Any:
    """Execute a query and return the first value of the first row.

    Used for queries that return a single scalar value, such as COUNT queries
    or DML statements that return nothing meaningful.

    Args:
        cypher: The Cypher query string.
        params: Parameter dict. Keys are plain strings (no '$' prefix).

    Returns:
        The first column value of the first row, or None if no rows returned.
    """
    query_result = self.conn.execute(cypher, parameters=params if params else None)

    if query_result.has_next():
        row = query_result.get_next()
        if row:
            return row[0]

    return None
```

**When this is called:** `evaluate_query_single` is used by base class concrete methods like `delete_nodes` (which calls `UNWIND ... DETACH DELETE n` and doesn't return data), `get_count` (which calls `MATCH ... RETURN COUNT(DISTINCT n)`), and `apply_constraint` / `drop_constraint`. For DML-only queries that return no rows, the method will return `None`, which is acceptable.

---

## Parameter Passing to Ladybug

Ladybug uses `$param_name` syntax in Cypher, the same as Neo4j. The Python API differs from the Neo4j driver:

| | Neo4j | Ladybug (`ladybug`) |
|--|-------|--------------------------|
| Execute call | `driver.execute_query(cypher, parameters_=params)` | `conn.execute(cypher, parameters=params)` |
| Parameter dict key | `"param_name"` (no `$`) | `"param_name"` (no `$`) |
| Passing no params | `parameters_={}` | `parameters=None` (preferred) |

No parameter transformation is needed — the same `params` dict passed into `evaluate_query` can be forwarded directly to `conn.execute`.

---

## Type Conversion on Read

Unlike Neo4j (which returns `Neo4jDateTime` objects that require `.to_native()`), `ladybug` returns Python-native types directly:

- `DATE` columns → `datetime.date`
- `TIMESTAMP` columns → `datetime.datetime`
- `BLOB` columns → `bytes`

No post-processing of property values is needed. Pydantic will handle coercion when constructing the model instances in `_ladybug_node_to_neontology_node`.

The only values requiring special handling are the internal Ladybug identity dicts (`_id`, `_src`, `_dst`). These are filtered out by the `if not k.startswith("_")` check in the helper methods.
