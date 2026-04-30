# Ladybug Engine — Config Class and Engine Skeleton

This file covers the `LadybugConfig` Pydantic model and the initial `LadybugEngine` class skeleton. After following this file you will have a module that imports without errors and contains all required method stubs. Subsequent files fill in the method bodies.

---

## File Header and Imports

Create `src/neontology/graphengines/ladybugengine.py` with the following imports:

```python
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, ClassVar, Optional, TypeVar

import ladybug as lb
from dotenv import load_dotenv
from pydantic import model_validator

from ..result import NeontologyResult
from .graphengine import GraphEngineBase, GraphEngineConfig

if TYPE_CHECKING:
    from ..basenode import BaseNode
    from ..baserelationship import BaseRelationship, RelationshipTypeData

BaseNodeT = TypeVar("BaseNodeT", bound="BaseNode")
BaseRelationshipT = TypeVar("BaseRelationshipT", bound="BaseRelationship")
```

**Why `from __future__ import annotations`:** This enables PEP 563 postponed evaluation of annotations, which is required so that forward references to `LadybugEngine` inside `LadybugConfig` work without the class being defined yet.

**Why `TYPE_CHECKING` guard:** `BaseNode` and `BaseRelationship` import from `graphengines` indirectly. Importing them at runtime creates a circular import. The `TYPE_CHECKING` guard means these imports only apply to static type checkers, not at runtime. This is the same pattern used in `neo4jengine.py`.

**Why `import ladybug as lb` at the top level here:** The optional-import guard (the `try/except ImportError`) lives in `graphengines/__init__.py` and `graphconnection.py`, not in this file. This file is only imported when `ladybug` is installed. See `05-registration-and-testing.md` for the guard placement.


---

## `LadybugConfig`

```python
class LadybugConfig(GraphEngineConfig):
    """Configuration for the Ladybug embedded graph engine."""

    engine: ClassVar[type[GraphEngineBase]] = LadybugEngine  # forward reference resolved by __future__ annotations

    database_path: Optional[str] = None

    env_fields: ClassVar[dict[str, str]] = {}  # do not use the base class env_fields validator behaviour

    @model_validator(mode="before")
    @classmethod
    def populate_defaults(cls, data: Any) -> Any:
        """Populate database_path from environment variable if not explicitly provided.

        Unlike other engines, database_path is truly optional. If it is absent and
        LADYBUG_DATABASE_PATH is not set, the engine will use an in-memory database.
        """
        load_dotenv()
        if not data.get("database_path"):
            env_value = os.getenv("LADYBUG_DATABASE_PATH")
            if env_value:
                data["database_path"] = env_value
            # If still None after env check, in-memory mode will be used. Do not raise.
        return data
```

### Why `env_fields = {}` and a custom `populate_defaults`

The base class `GraphEngineConfig` has a `model_validator` called `populate_defaults` that iterates `env_fields` and **raises `ValueError`** if the env var is not set. For Neo4j this is correct: you always need a URI and credentials. For Ladybug, `database_path` is optional — if absent, `lb.Database()` opens an in-memory database (no file system path needed). If we put `database_path` in `env_fields`, the base validator would raise when no path was provided. Setting `env_fields = {}` disables that behaviour entirely, and our override handles the optional env var load without raising.

### `database_path` semantics

| Value | Effect |
|-------|--------|
| `"/path/to/mydb"` | Opens (or creates) a persistent on-disk Ladybug database at that path |
| `None` (default) | Opens an in-memory Ladybug database; data is lost when the process exits |

---

## `LadybugEngine` Skeleton

Write the engine class directly below `LadybugConfig` (or above it — but `LadybugConfig` references `LadybugEngine` as a `ClassVar`, so `LadybugEngine` must be defined first, or you rely on the `from __future__ import annotations` postponed evaluation to avoid a `NameError` at class body evaluation time). The safest order is: define `LadybugEngine` first, then `LadybugConfig`.

```python
class LadybugEngine(GraphEngineBase):
    """Graph engine for Ladybug embedded graph databases."""

    def __init__(self, config: LadybugConfig) -> None:
        """Initialise the Ladybug database and connection.

        Args:
            config: LadybugConfig instance. If config.database_path is None, an
                    in-memory database is used (lb.Database() defaults to ':memory:').
        """
        if config.database_path:
            self.db = lb.Database(config.database_path)
        else:
            self.db = lb.Database()  # in-memory mode — defaults to ':memory:'

        # lb.Connection is the object used to execute all queries.
        # IMPORTANT: lb.Connection is NOT thread-safe. Do not share a single
        # connection across threads. If you need multi-threaded access, create a
        # separate LadybugEngine (and therefore a separate Connection) per thread.
        # For concurrent use, see lb.AsyncConnection instead.
        self.conn = lb.Connection(self.db)

        # Registry of existing table names, populated lazily on first use.
        # Used to avoid redundant CALL show_tables() round-trips.
        self._known_tables: set[str] = set()

    # ------------------------------------------------------------------
    # Abstract methods — must be implemented (see 03-abstract-methods.md)
    # ------------------------------------------------------------------

    def verify_connection(self) -> bool:
        raise NotImplementedError

    def close_connection(self) -> None:
        raise NotImplementedError

    def evaluate_query(
        self,
        cypher: str,
        params: dict[str, Any] = {},
        node_classes: dict = {},
        relationship_classes: dict = {},
    ) -> NeontologyResult:
        raise NotImplementedError

    def evaluate_query_single(self, cypher: str, params: dict[str, Any] = {}) -> Any:
        raise NotImplementedError

    def apply_constraint(self, label: str, property: str) -> None:
        raise NotImplementedError

    def drop_constraint(self, constraint_name: str) -> None:
        raise NotImplementedError

    def get_constraints(self) -> list:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Concrete method overrides — base implementations generate
    # incompatible Cypher (see 04-concrete-overrides.md)
    # ------------------------------------------------------------------

    def create_nodes(self, labels: list, pp_key: str, properties: list, node_class: type[BaseNodeT]) -> list[BaseNodeT]:
        raise NotImplementedError

    def merge_nodes(self, labels: list, pp_key: str, properties: list, node_class: type[BaseNodeT]) -> list[BaseNodeT]:
        raise NotImplementedError

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
        raise NotImplementedError
```

### About `self._known_tables`

This is a local cache of table names that exist in the Ladybug database. It starts empty. When the engine needs to create a node or relationship, it checks whether the table already exists before issuing `CREATE NODE TABLE` DDL. After successfully creating a table, it adds the name to this set. This avoids issuing `CALL show_tables()` before every single DML operation. The cache is per-engine-instance, so it resets if the engine is re-initialised.

### Which methods must be overridden and why

The base class `GraphEngineBase` provides concrete implementations for `create_nodes`, `merge_nodes`, `merge_relationships`, `delete_nodes`, `match_nodes`, `get_count`, and `match_relationships`. Most of these generate Cypher by string interpolation and then call `self.evaluate_query()` or `self.evaluate_query_single()`. The problem is that the generated Cypher uses patterns Ladybug does not support:

| Base class method | Problem with generated Cypher |
|-------------------|-------------------------------|
| `create_nodes` | Uses `(n:Label1:Label2 {...})` multi-label syntax; uses `SET n += node.props` map assignment |
| `merge_nodes` | Same multi-label syntax; uses `ON MATCH SET n += ...` and `SET n += ...` map assignment |
| `merge_relationships` | Uses `ON MATCH SET r += ...` and `SET r += ...` map assignment |
| `delete_nodes` | Generates `UNWIND ... MATCH (n:Label) WHERE n.pp = pp DETACH DELETE n` — this syntax is valid in Ladybug; **no override needed** |
| `match_nodes` | Generates `MATCH (n:Label) WHERE ... RETURN n` — valid in Ladybug; **no override needed** |
| `get_count` | Generates `MATCH (n:Label) WHERE ... RETURN COUNT(DISTINCT n)` — valid in Ladybug; **no override needed** |
| `match_relationships` | Generates `MATCH (n)-[r:TYPE]->(o) RETURN n, r, o` — valid in Ladybug; **no override needed** |

The `_filters_to_where_clause` concrete method generates WHERE clauses using `toLower()` for case-insensitive filters. Ladybug does not support `toLower()` in the same way. See `04-concrete-overrides.md` for how to handle this.

---

## Complete File Structure After This Step

```
src/neontology/graphengines/ladybugengine.py
├── imports
├── class LadybugEngine(GraphEngineBase)
│   ├── __init__
│   ├── [6 abstract method stubs]
│   └── [3 concrete override stubs]
└── class LadybugConfig(GraphEngineConfig)
    ├── engine: ClassVar = LadybugEngine
    ├── database_path: Optional[str] = None
    └── populate_defaults validator
```

The file will import cleanly once `ladybug` is installed (`uv add ladybug`). It will fail at runtime if any stub is called, which is expected at this stage.
