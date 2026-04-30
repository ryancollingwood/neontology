# Ladybug Engine — Schema Management

Ladybug is a *structured property graph* database. Unlike Neo4j, it requires every node label and relationship type to be declared as a table with an explicit schema **before** any data can be written. This file explains how to auto-generate that schema from neontology's Python model classes, when to create it, and how to implement the constraint-related abstract methods.

---

## The Core Problem

When a user calls `Person(name="Alice", age=30).merge()`, neontology calls `LadybugEngine.merge_nodes(...)`. At that point, Ladybug needs a `NODE TABLE Person (name STRING PRIMARY KEY, age INT64)` to already exist. If it does not, Ladybug will raise an error.

Neontology has no DDL lifecycle hook that fires at startup. The closest hooks are:

- **`apply_constraint(label, property)`** — called explicitly by the user via `auto_constrain_neo4j()` or `apply_neo4j_constraints()` in `src/neontology/utils.py`. These iterate all known `BaseNode` subclasses and call `engine.apply_constraint(node.__primarylabel__, node.__primaryproperty__)`. For Ladybug, repurpose this hook to create NODE TABLEs.
- **Lazy table creation inside DML methods** — `create_nodes`, `merge_nodes`, and `merge_relationships` all receive the node/relationship class. Check whether the table exists before executing the DML and create it if not.

**Recommended approach: use both.** Implement `apply_constraint` so that users who call `auto_constrain_neo4j()` get early schema creation. Also implement lazy creation inside `create_nodes`, `merge_nodes`, and `merge_relationships` as a defensive fallback, because the user may not call `auto_constrain_neo4j()` explicitly.

---

## Python Type → Ladybug DDL Type Mapping

Ladybug has a strongly-typed schema. Every column in a NODE TABLE must have a declared type. The following table maps Python annotation types (as used in neontology Pydantic models) to their Ladybug DDL equivalents.

| Python type | Ladybug DDL type | Notes |
|-------------|-----------------|-------|
| `str` | `STRING` | |
| `int` | `INT64` | Ladybug also has INT32, INT16, INT8 — use INT64 as the safe default |
| `float` | `DOUBLE` | Ladybug also has FLOAT — use DOUBLE as the safe default |
| `bool` | `BOOLEAN` | |
| `datetime` | `TIMESTAMP` | Python `datetime.datetime` |
| `date` | `DATE` | Python `datetime.date` |
| `time` | `STRING` | Ladybug has no TIME type; serialise to ISO string |
| `timedelta` | `STRING` | Ladybug has no duration type; serialise to string |
| `bytes` | `BLOB` | Python `bytes` |
| `bytearray` | `BLOB` | |
| `list` | `STRING` | Ladybug LIST requires all elements to be the same type, which neontology cannot guarantee at the DDL level. Serialise to JSON string and deserialise on read. |
| `Optional[X]` | Same as `X` | Ladybug columns are nullable by default; `Optional` does not change the DDL type |

### Implementing the type mapper

Add this helper function at the module level in `ladybugengine.py`, above the class definitions:

```python
import json
from datetime import date, datetime, time, timedelta
from typing import get_args, get_origin, Union


def _python_type_to_ladybug(annotation: Any) -> str:
    """Map a Python type annotation to a Ladybug DDL type string.

    Handles Optional[X] (which is Union[X, None]) by unwrapping to X.
    Falls back to STRING for any unrecognised type.

    Args:
        annotation: A Python type annotation (e.g. str, int, Optional[str]).

    Returns:
        A Ladybug DDL type string (e.g. "STRING", "INT64").
    """
    # Unwrap Optional[X] — Optional[X] is Union[X, None] in Python's type system
    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _python_type_to_ladybug(args[0])
        # Multiple non-None args = complex union; fall back to STRING
        return "STRING"

    type_map = {
        str: "STRING",
        int: "INT64",
        float: "DOUBLE",
        bool: "BOOLEAN",
        datetime: "TIMESTAMP",
        date: "DATE",
        time: "STRING",
        timedelta: "STRING",
        bytes: "BLOB",
        bytearray: "BLOB",
        list: "STRING",  # serialised to JSON
    }

    return type_map.get(annotation, "STRING")
```

**Why fall back to `STRING` for unknown types:** Neontology's `_export_type_converter` converts any unsupported type to its string representation before writing to the graph. The schema must therefore also use `STRING` for any type that would be stringified.

---

## Building NODE TABLE DDL

Add this helper function at the module level:

```python
def _get_node_table_ddl(node_class: type) -> str:
    """Generate a CREATE NODE TABLE statement for a BaseNode subclass.

    Args:
        node_class: A BaseNode subclass. Must have __primarylabel__ and
                    __primaryproperty__ defined, and model_fields populated
                    by Pydantic.

    Returns:
        A complete CREATE NODE TABLE Cypher string.

    Example output:
        CREATE NODE TABLE IF NOT EXISTS Person (name STRING PRIMARY KEY, age INT64)
    """
    label = node_class.__primarylabel__
    pp_key = node_class.__primaryproperty__

    columns = []
    for field_name, field_info in node_class.model_fields.items():
        # model_fields is a Pydantic v2 dict: {field_name: FieldInfo}
        # FieldInfo.annotation holds the Python type
        annotation = field_info.annotation
        ladybug_type = _python_type_to_ladybug(annotation)

        if field_name == pp_key:
            columns.append(f"{field_name} {ladybug_type} PRIMARY KEY")
        else:
            columns.append(f"{field_name} {ladybug_type}")

    # PRIMARY KEY column must be listed; ensure it appears exactly once.
    # If pp_key is not in model_fields (shouldn't happen for valid BaseNode),
    # append it as STRING PRIMARY KEY as a safe fallback.
    pp_in_columns = any(pp_key in col for col in columns)
    if not pp_in_columns:
        columns.insert(0, f"{pp_key} STRING PRIMARY KEY")

    columns_str = ", ".join(columns)
    return f"CREATE NODE TABLE IF NOT EXISTS {label} ({columns_str})"
```

**Why `IF NOT EXISTS`:** Ladybug supports this guard. It makes the DDL idempotent — safe to call multiple times without error.

**About `model_fields`:** This is a Pydantic v2 attribute. It is a `dict[str, FieldInfo]` where each key is the field name and `FieldInfo.annotation` holds the type. Do not use `__annotations__` directly — Pydantic may store annotations differently after model resolution.

**About field ordering:** Ladybug requires the PRIMARY KEY column to be present. The loop above marks it with `PRIMARY KEY` when encountered. The order of the remaining columns in the DDL does not matter for correctness.

---

## Building REL TABLE DDL

Relationship tables have a different DDL structure. They reference the source and target node table labels explicitly.

```python
def _get_rel_table_ddl(
    rel_type: str,
    source_label: str,
    target_label: str,
    rel_class: type,
) -> str:
    """Generate a CREATE REL TABLE statement for a BaseRelationship subclass.

    Args:
        rel_type: The relationship type string (e.g. "KNOWS").
        source_label: The __primarylabel__ of the source node class.
        target_label: The __primarylabel__ of the target node class.
        rel_class: The BaseRelationship subclass. Its model_fields are
                   inspected to build the property column list.

    Returns:
        A complete CREATE REL TABLE Cypher string.

    Example output:
        CREATE REL TABLE IF NOT EXISTS KNOWS (FROM Person TO Person, since DATE)
    """
    # Fields to EXCLUDE from the relationship property columns.
    # 'source' and 'target' are neontology-internal fields that map to the
    # FROM/TO node references — they are not properties stored on the edge.
    # '_merge_on' is also a neontology-internal field.
    excluded_fields = {"source", "target", "_merge_on"}

    columns = []
    for field_name, field_info in rel_class.model_fields.items():
        if field_name in excluded_fields:
            continue
        annotation = field_info.annotation
        ladybug_type = _python_type_to_ladybug(annotation)
        columns.append(f"{field_name} {ladybug_type}")

    if columns:
        columns_str = ", ".join(columns)
        return f"CREATE REL TABLE IF NOT EXISTS {rel_type} (FROM {source_label} TO {target_label}, {columns_str})"
    else:
        return f"CREATE REL TABLE IF NOT EXISTS {rel_type} (FROM {source_label} TO {target_label})"
```

---

## Checking Whether a Table Exists

Use the `_known_tables` cache introduced in the skeleton (see `01-config-and-skeleton.md`). Add a helper method to `LadybugEngine`:

```python
def _get_existing_tables(self) -> set[str]:
    """Query Ladybug for all currently defined table names.

    Ladybug does not support SHOW CONSTRAINTS (a Neo4j-only syntax).
    Instead, use the built-in CALL show_tables() procedure.

    Returns:
        A set of table name strings (both node and relationship tables).
    """
    # conn.execute() returns a QueryResult object (from ladybug).
    # Calling .get_as_df() on it returns a pandas DataFrame.
    # The table names are in the "name" column.
    result = self.conn.execute("CALL show_tables() RETURN *")
    df = result.get_as_df()
    if df.empty:
        return set()
    return set(df["name"].tolist())

def _ensure_node_table(self, node_class: type) -> None:
    """Create the NODE TABLE for a node class if it does not already exist.

    Uses the _known_tables cache to avoid querying Ladybug on every call.
    If the table is not in the cache, queries Ladybug directly to confirm.

    Args:
        node_class: The BaseNode subclass to ensure a table for.
    """
    label = node_class.__primarylabel__
    if label in self._known_tables:
        return

    # Cache miss: check the database directly
    existing = self._get_existing_tables()
    self._known_tables.update(existing)

    if label not in self._known_tables:
        ddl = _get_node_table_ddl(node_class)
        self.conn.execute(ddl)
        self._known_tables.add(label)

def _ensure_rel_table(
    self,
    rel_type: str,
    source_label: str,
    target_label: str,
    rel_class: type,
) -> None:
    """Create the REL TABLE for a relationship class if it does not already exist.

    Args:
        rel_type: The relationship type string.
        source_label: The primary label of the source node.
        target_label: The primary label of the target node.
        rel_class: The BaseRelationship subclass.
    """
    if rel_type in self._known_tables:
        return

    existing = self._get_existing_tables()
    self._known_tables.update(existing)

    if rel_type not in self._known_tables:
        # The source and target NODE TABLEs must exist before the REL TABLE.
        # By the time merge_relationships is called, create_nodes/merge_nodes
        # will have already run for those nodes, so their tables should exist.
        # If not, raise a clear error rather than silently failing.
        if source_label not in self._known_tables:
            raise RuntimeError(
                f"Cannot create REL TABLE {rel_type}: source node table '{source_label}' "
                "does not exist. Ensure the source node class has been merged or created first."
            )
        if target_label not in self._known_tables:
            raise RuntimeError(
                f"Cannot create REL TABLE {rel_type}: target node table '{target_label}' "
                "does not exist. Ensure the target node class has been merged or created first."
            )
        ddl = _get_rel_table_ddl(rel_type, source_label, target_label, rel_class)
        self.conn.execute(ddl)
        self._known_tables.add(rel_type)
```

---

## Implementing the Constraint Abstract Methods

### `apply_constraint(label, property)`

In neontology, `apply_constraint` is called by `apply_neo4j_constraints()` in `src/neontology/utils.py`. That function loops over all known node classes and calls:

```python
graph.engine.apply_constraint(node_type.__primarylabel__, node_type.__primaryproperty__)
```

Notice: it passes only the **label string** and the **property string**, not the class itself. To generate the DDL we need the full class. Resolve it from `GraphConnection.global_nodes`, which is a `ClassVar` dict keyed by label:

```python
def apply_constraint(self, label: str, property: str) -> None:
    """Create the NODE TABLE for the given label if it does not exist.

    For Ladybug, constraints are enforced at the DDL level via PRIMARY KEY
    declarations in CREATE NODE TABLE. This method creates the table if it
    has not already been created.

    Args:
        label: The node label (matches __primarylabel__ of a BaseNode subclass).
        property: The primary property key (used for PRIMARY KEY in DDL).
    """
    from ..graphconnection import GraphConnection

    node_classes = GraphConnection.global_nodes
    node_class = node_classes.get(label)

    if node_class is None:
        # Label not found in global registry. This can happen if apply_constraint
        # is called before the class is defined, or if the label doesn't match.
        # Skip silently — lazy table creation in create_nodes/merge_nodes will
        # handle it when the class is actually used.
        return

    self._ensure_node_table(node_class)
```

**Important:** `GraphConnection` is imported inside the method body (not at the top of the file) to avoid a circular import. The import chain is:
`ladybugengine.py` → `graphengine.py` → (nothing circular)
`graphconnection.py` → `graphengines/__init__.py` → `ladybugengine.py`

Importing `GraphConnection` at the top of `ladybugengine.py` would create a circular import at module load time. The deferred import inside the method body is the standard Python pattern for breaking these cycles.

### `get_constraints()`

```python
def get_constraints(self) -> list:
    """Return the names of all currently defined tables in the database.

    Repurposed for Ladybug: returns all table names (both NODE and REL tables)
    rather than constraint names, since Ladybug enforces uniqueness via
    PRIMARY KEY in the CREATE NODE TABLE DDL rather than separate constraints.

    Returns:
        A list of table name strings.
    """
    return list(self._get_existing_tables())
```

### `drop_constraint(constraint_name)`

```python
def drop_constraint(self, constraint_name: str) -> None:
    """Drop a table by name.

    WARNING: This is destructive. Dropping a table permanently deletes all
    data in it. This should only be called during test teardown when you
    intend to wipe the database.

    Args:
        constraint_name: The table name to drop (NODE TABLE or REL TABLE).
    """
    self.conn.execute(f"DROP TABLE {constraint_name}")
    self._known_tables.discard(constraint_name)
```

---

## Summary: When Each DDL Helper Is Called

| Trigger | Method called | DDL issued |
|---------|--------------|-----------|
| User calls `auto_constrain_neo4j()` | `apply_constraint(label, property)` | `CREATE NODE TABLE IF NOT EXISTS ...` |
| `create_nodes(...)` is called | `_ensure_node_table(node_class)` | `CREATE NODE TABLE IF NOT EXISTS ...` (if not cached) |
| `merge_nodes(...)` is called | `_ensure_node_table(node_class)` | `CREATE NODE TABLE IF NOT EXISTS ...` (if not cached) |
| `merge_relationships(...)` is called | `_ensure_rel_table(...)` | `CREATE REL TABLE IF NOT EXISTS ...` (if not cached) |
| User calls `get_constraints()` | `get_constraints()` | `CALL show_tables() RETURN *` |
| User calls `drop_constraint(name)` | `drop_constraint(name)` | `DROP TABLE name` |
