# Ladybug Engine — Concrete Method Overrides

The base class `GraphEngineBase` provides concrete implementations for `create_nodes`, `merge_nodes`, `merge_relationships`, and several other methods. These implementations generate Cypher by string interpolation and then call `self.evaluate_query()` or `self.evaluate_query_single()`. Three of these methods generate Cypher that is incompatible with Ladybug. This file explains exactly what is wrong with each and provides complete replacement implementations.

All code in this file belongs inside the `LadybugEngine` class body.

---

## Why the Base Class Cypher Fails in Ladybug

### Problem 1: `SET n += dict` is not supported

The Neo4j `+=` map-merge operator bulk-assigns all properties from a map parameter. Ladybug does not support this. Every property must be assigned individually:

```cypher
-- Neo4j (works)
SET n += $props

-- Ladybug (required)
SET n.name = $name, n.age = $age
```

### Problem 2: Multi-label node syntax is not supported

Neo4j allows nodes to have multiple labels simultaneously. Ladybug enforces one label per node table. The multi-label pattern in CREATE/MERGE fails:

```cypher
-- Neo4j (works with multi-label)
CREATE (n:Person:Employee {name: $name})

-- Ladybug (required — one label only)
CREATE (n:Person {name: $name})
```

Neontology's base class joins all labels with `:` when there are secondary labels. Ladybug will reject this. The fix is to use only `labels[0]` (the primary label).

### Problem 3: Multi-label MERGE match is not supported

```cypher
-- Neo4j (works)
MERGE (n:Person:Employee {name: $name})

-- Ladybug (required)
MERGE (n:Person {name: $name})
```

---

## Helper: Building Explicit SET Clauses

Add this module-level helper function. It is used by all three override methods.

```python
def _expand_set_clause(alias: str, props_dict: dict, prefix: str) -> tuple[str, dict]:
    """Convert a dict of properties into an explicit SET clause and flat params dict.

    Args:
        alias: The Cypher variable alias to set properties on (e.g. "n" or "r").
        props_dict: Dict of property_name → value. May be empty.
        prefix: A string prefix to namespace the parameter names and avoid
                collisions when multiple SET clauses are generated for the
                same query (e.g. "match", "create", "always").

    Returns:
        A tuple of:
        - set_clause_str: A string like "n.name = $match_name, n.age = $match_age"
                          or an empty string if props_dict is empty.
        - flat_params: Dict mapping prefixed param names to their values,
                       e.g. {"match_name": "Alice", "match_age": 30}.

    Example:
        clause, params = _expand_set_clause("n", {"name": "Alice", "age": 30}, "match")
        # clause = "n.name = $match_name, n.age = $match_age"
        # params = {"match_name": "Alice", "match_age": 30}
    """
    if not props_dict:
        return "", {}

    assignments = []
    flat_params = {}
    for prop_name, value in props_dict.items():
        param_key = f"{prefix}_{prop_name}"
        assignments.append(f"{alias}.{prop_name} = ${param_key}")
        flat_params[param_key] = value

    return ", ".join(assignments), flat_params
```

---

## Override: `create_nodes`

**Original base class Cypher (DO NOT use):**
```cypher
UNWIND $node_list AS node
CREATE (n:Person:Employee {name: node.pp})
SET n += node.props
RETURN n
```

Problems: multi-label syntax, `SET +=` map assignment.

**The replacement strategy:** Because Ladybug's `UNWIND` does not support nested dict property access on parameters in the same way as Neo4j (`node.props.name`), we must issue one `CREATE` statement per node rather than using `UNWIND`. Each node's properties are flattened into individual named parameters.

```python
def create_nodes(self, labels: list, pp_key: str, properties: list, node_class: type[BaseNodeT]) -> list[BaseNodeT]:
    """Create nodes in Ladybug for the given node class.

    Overrides the base class implementation because:
    1. The base class uses multi-label syntax (n:Label1:Label2) which Ladybug rejects.
    2. The base class uses SET n += dict which Ladybug does not support.

    Args:
        labels: List of labels. Only labels[0] (the primary label) is used.
                Ladybug does not support multi-label nodes.
        pp_key: The primary property key name.
        properties: List of dicts, each with keys:
                    - 'pp': the primary property value
                    - 'props': dict of all other property name → value pairs
        node_class: The BaseNode subclass to create instances of.

    Returns:
        List of created BaseNode instances.
    """
    # Ensure the NODE TABLE exists before writing
    self._ensure_node_table(node_class)

    label = labels[0]  # Ladybug: one label per node
    node_classes = {node_class.__primarylabel__: node_class}
    created_nodes = []

    for i, node_props in enumerate(properties):
        # Build flat params for this individual node.
        # Prefix with index 'i' to avoid param name collisions if this loop
        # is ever batched differently in the future.
        params: dict[str, Any] = {f"pp_{i}": node_props["pp"]}
        prop_assignments = [f"{pp_key} = $pp_{i}"]

        for prop_name, prop_value in node_props.get("props", {}).items():
            param_key = f"p_{i}_{prop_name}"
            params[param_key] = prop_value
            prop_assignments.append(f"{prop_name} = ${param_key}")

        props_inline = ", ".join(f"{pp_key}: $pp_{i}" for _ in [None])  # just the primary key for identity
        set_clause = ", ".join(
            f"{prop_name} = ${f'p_{i}_{prop_name}'}"
            for prop_name in node_props.get("props", {}).keys()
        )

        if set_clause:
            cypher = (
                f"CREATE (n:{label} {{{pp_key}: $pp_{i}}})\n"
                f"SET {set_clause}\n"
                f"RETURN n"
            )
        else:
            cypher = f"CREATE (n:{label} {{{pp_key}: $pp_{i}}}) RETURN n"

        result = self.evaluate_query(cypher, params, node_classes)
        created_nodes.extend(result.nodes)

    return created_nodes
```

**Why one query per node instead of UNWIND:** Ladybug's `UNWIND` does support list parameters, but accessing nested dict properties from an `UNWIND` list item (e.g. `node.props.name`) requires the properties to be structured consistently. Flattening to individual named parameters is simpler, avoids issues with heterogeneous property sets across nodes, and produces straightforward Cypher that is easy to debug.

---

## Override: `merge_nodes`

**Original base class Cypher (DO NOT use):**
```cypher
UNWIND $node_list AS node
MERGE (n:Person:Employee {name: node.pp})
ON MATCH SET n += node.set_on_match
ON CREATE SET n += node.set_on_create
SET n += node.always_set
RETURN n
```

Problems: multi-label syntax, all three `SET +=` clauses.

**The `properties` list structure for `merge_nodes`:** Each element of the `properties` list is a dict with these keys:
- `pp`: the primary property value (used for MERGE identity)
- `set_on_match`: dict of properties to set only when the node already exists
- `set_on_create`: dict of properties to set only when the node is newly created
- `always_set`: dict of properties to set regardless of whether node was created or matched

```python
def merge_nodes(self, labels: list, pp_key: str, properties: list, node_class: type[BaseNodeT]) -> list[BaseNodeT]:
    """Merge (create or update) nodes in Ladybug for the given node class.

    Overrides the base class implementation because:
    1. The base class uses multi-label syntax which Ladybug rejects.
    2. The base class uses SET n += dict which Ladybug does not support.

    Args:
        labels: List of labels. Only labels[0] is used.
        pp_key: The primary property key name.
        properties: List of dicts, each with keys:
                    - 'pp': the primary property value
                    - 'set_on_match': dict of properties to set on existing nodes
                    - 'set_on_create': dict of properties to set on new nodes
                    - 'always_set': dict of properties to set regardless
        node_class: The BaseNode subclass to merge.

    Returns:
        List of merged BaseNode instances.
    """
    self._ensure_node_table(node_class)

    label = labels[0]
    node_classes = {node_class.__primarylabel__: node_class}
    merged_nodes = []

    for i, node_props in enumerate(properties):
        pp_value = node_props["pp"]
        set_on_match: dict = node_props.get("set_on_match", {}) or {}
        set_on_create: dict = node_props.get("set_on_create", {}) or {}
        always_set: dict = node_props.get("always_set", {}) or {}

        params: dict[str, Any] = {f"pp_{i}": pp_value}

        # Build ON MATCH SET clause
        match_clause, match_params = _expand_set_clause("n", set_on_match, f"m{i}")
        params.update(match_params)

        # Build ON CREATE SET clause
        create_clause, create_params = _expand_set_clause("n", set_on_create, f"c{i}")
        params.update(create_params)

        # Build always SET clause
        always_clause, always_params = _expand_set_clause("n", always_set, f"a{i}")
        params.update(always_params)

        cypher = f"MERGE (n:{label} {{{pp_key}: $pp_{i}}})\n"

        if match_clause:
            cypher += f"ON MATCH SET {match_clause}\n"
        if create_clause:
            cypher += f"ON CREATE SET {create_clause}\n"
        if always_clause:
            cypher += f"SET {always_clause}\n"

        cypher += "RETURN n"

        result = self.evaluate_query(cypher, params, node_classes)
        merged_nodes.extend(result.nodes)

    return merged_nodes
```

---

## Override: `merge_relationships`

**Original base class Cypher (DO NOT use):**
```cypher
UNWIND $rel_list AS rel
MATCH (source:SourceLabel)
WHERE source.source_prop = rel.source_prop
MATCH (target:TargetLabel)
WHERE target.target_prop = rel.target_prop
MERGE (source)-[r:REL_TYPE {merge_on_prop: rel.merge_on_prop}]->(target)
ON MATCH SET r += rel.set_on_match
ON CREATE SET r += rel.set_on_create
SET r += rel.always_set
```

Problems: `SET r += dict` map assignment. (The MATCH and MERGE patterns themselves are valid Ladybug Cypher.)

**The `rel_props` list structure:** Each element of `rel_props` is a dict with:
- `source_prop`: the source node's primary property value (for MATCH)
- `target_prop`: the target node's primary property value (for MATCH)
- plus any `merge_on_props` keys at the top level (used in the MERGE pattern)
- `set_on_match`: dict of relationship properties to set when relationship exists
- `set_on_create`: dict of relationship properties to set when relationship is new
- `always_set`: dict of relationship properties to always set

```python
def merge_relationships(
    self,
    source_label: str,
    target_label: str,
    source_prop: str,
    target_prop: str,
    rel_type: str,
    merge_on_props: list[str],
    rel_props: list[dict],
) -> None:
    """Merge relationships in Ladybug between existing nodes.

    Overrides the base class implementation because:
    1. The base class uses SET r += dict which Ladybug does not support.

    The REL TABLE is created lazily if it does not exist.

    Args:
        source_label: Primary label of the source node.
        target_label: Primary label of the target node.
        source_prop: Property name on the source node to match on.
        target_prop: Property name on the target node to match on.
        rel_type: The relationship type string (e.g. "KNOWS").
        merge_on_props: List of relationship property names to include in
                        the MERGE pattern (uniqueness identity for the edge).
        rel_props: List of dicts, one per relationship to merge.
    """
    if not rel_props:
        return

    # We need the relationship class to build the REL TABLE DDL.
    # Resolve it from GraphConnection.global_rels.
    from ..graphconnection import GraphConnection

    rel_type_data = GraphConnection.global_rels.get(rel_type)
    rel_class = rel_type_data.relationship_class if rel_type_data else None

    if rel_class is None:
        raise RuntimeError(
            f"Cannot merge relationships of type '{rel_type}': "
            "no matching BaseRelationship class found in GraphConnection.global_rels. "
            "Ensure the class is defined before calling init_neontology()."
        )

    self._ensure_rel_table(rel_type, source_label, target_label, rel_class)

    for i, rp in enumerate(rel_props):
        params: dict[str, Any] = {
            f"src_{i}": rp["source_prop"],
            f"tgt_{i}": rp["target_prop"],
        }

        # Build the MERGE pattern identity props (merge_on_props)
        merge_pattern_parts = []
        for prop_name in merge_on_props:
            param_key = f"merge_{i}_{prop_name}"
            params[param_key] = rp.get(prop_name)
            merge_pattern_parts.append(f"{prop_name}: ${param_key}")

        merge_props_str = "{" + ", ".join(merge_pattern_parts) + "}" if merge_pattern_parts else ""

        # Build SET clauses
        match_clause, match_params = _expand_set_clause("r", rp.get("set_on_match", {}) or {}, f"rm{i}")
        params.update(match_params)

        create_clause, create_params = _expand_set_clause("r", rp.get("set_on_create", {}) or {}, f"rc{i}")
        params.update(create_params)

        always_clause, always_params = _expand_set_clause("r", rp.get("always_set", {}) or {}, f"ra{i}")
        params.update(always_params)

        cypher = (
            f"MATCH (source:{source_label}) WHERE source.{source_prop} = $src_{i}\n"
            f"MATCH (target:{target_label}) WHERE target.{target_prop} = $tgt_{i}\n"
            f"MERGE (source)-[r:{rel_type} {merge_props_str}]->(target)\n"
        )

        if match_clause:
            cypher += f"ON MATCH SET {match_clause}\n"
        if create_clause:
            cypher += f"ON CREATE SET {create_clause}\n"
        if always_clause:
            cypher += f"SET {always_clause}\n"

        self.evaluate_query_single(cypher, params)
```

---

## Filter Overrides: `_filters_to_where_clause`

The base class generates case-insensitive filter clauses using `toLower()`:

```cypher
-- Base class generates (not supported in Ladybug)
WHERE toLower(n.name) = toLower($filter_name_iexact)
WHERE toLower(n.name) CONTAINS toLower($filter_name_icontains)
WHERE toLower(n.name) STARTS WITH toLower($filter_name_istartswith)
```

Ladybug does not support `toLower()`. The options are:

1. **Restrict unsupported filters** (recommended for initial implementation): override `_filters_to_where_clause` to raise `ValueError` if a case-insensitive filter is used, so users get a clear error message.
2. **Use regex**: Ladybug supports the `=~` regex operator. `iexact` could be rewritten as `n.name =~ '(?i)value'`. This is more complete but adds complexity.

**Recommended: restrict unsupported filters**

```python
def _filters_to_where_clause(self, filters: Optional[dict] = None) -> tuple[Optional[str], dict]:
    """Generate a WHERE clause from a filter dict for Ladybug queries.

    Overrides the base class to reject case-insensitive filter types that
    rely on toLower(), which Ladybug does not support.

    Supported filter types: exact, contains, startswith, gt, lt, gte, lte, in, isnull
    Unsupported filter types: iexact, icontains, istartswith

    Args:
        filters: Dict of field__lookuptype → value entries.

    Returns:
        Tuple of (where_clause_string_or_None, params_dict).

    Raises:
        ValueError: If an unsupported case-insensitive filter type is used.
    """
    _unsupported = {"iexact", "icontains", "istartswith"}

    if filters:
        for key in filters:
            if "__" in key:
                _, lookup_type = key.split("__", 1)
                if lookup_type in _unsupported:
                    raise ValueError(
                        f"Filter type '{lookup_type}' is not supported by the Ladybug engine. "
                        f"Ladybug does not support the toLower() function. "
                        f"Use case-sensitive alternatives: 'exact', 'contains', 'startswith'."
                    )

    # Delegate to the base class for all supported filter types
    return super()._filters_to_where_clause(filters)
```

---

## Methods That Do NOT Need Overriding

The following base class concrete methods generate Cypher that is valid in Ladybug:

### `delete_nodes`

Base class generates:
```cypher
UNWIND $pp_values AS pp
MATCH (n:Label)
WHERE n.pp_key = pp
DETACH DELETE n
```

This is valid Ladybug Cypher. `DETACH DELETE` cascades to connected relationships. No override needed.

### `match_nodes`

Base class generates:
```cypher
MATCH (n:Label) WHERE n.field = $value RETURN n SKIP $skip LIMIT $limit
```

This is valid Ladybug Cypher as long as the filter types are supported. The `_filters_to_where_clause` override above handles the unsupported filter types. No further override needed.

### `get_count`

Base class generates:
```cypher
MATCH (n:Label) WHERE ... RETURN COUNT(DISTINCT n)
```

Valid Ladybug Cypher. No override needed.

### `match_relationships`

Base class generates:
```cypher
MATCH (n)-[r:REL_TYPE]->(o)
RETURN n, r, o
SKIP $skip LIMIT $limit
```

Valid Ladybug Cypher. No override needed.

---

## Summary of All Overrides

| Method | Override required | Reason |
|--------|------------------|--------|
| `create_nodes` | Yes | Multi-label syntax + `SET +=` |
| `merge_nodes` | Yes | Multi-label syntax + `SET +=` |
| `merge_relationships` | Yes | `SET +=` |
| `_filters_to_where_clause` | Yes | `toLower()` not supported |
| `delete_nodes` | No | Valid Ladybug Cypher |
| `match_nodes` | No | Valid after filter override |
| `get_count` | No | Valid Ladybug Cypher |
| `match_relationships` | No | Valid Ladybug Cypher |
