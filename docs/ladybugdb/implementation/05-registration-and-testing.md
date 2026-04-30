# Ladybug Engine — Registration and Testing

This file covers the three changes needed to wire `LadybugEngine` into the neontology package, and a verification checklist to confirm the implementation is working correctly.

---

## Change 1: `src/neontology/graphengines/__init__.py`

The `graphengines` package `__init__.py` exports all engine classes. NetworkX uses a `try/except ImportError` guard because `grandcypher` is optional. Apply the same pattern for `ladybug`.

**Current file (`src/neontology/graphengines/__init__.py`):**
```python
from .memgraphengine import MemgraphConfig, MemgraphEngine
from .neo4jengine import Neo4jConfig, Neo4jEngine

__all__ = ["MemgraphConfig", "MemgraphEngine", "Neo4jConfig", "Neo4jEngine"]

try:
    from .networkxengine import NetworkxConfig, NetworkxEngine  # noqa: F401

    __all__.extend(["NetworkxConfig", "NetworkxEngine"])
except ImportError:
    pass
```

**Required addition — append after the NetworkX block:**
```python
try:
    from .ladybugengine import LadybugConfig, LadybugEngine  # noqa: F401

    __all__.extend(["LadybugConfig", "LadybugEngine"])
except ImportError:
    pass
```

The `try/except ImportError` means that if `ladybug` is not installed (`uv add ladybug`), importing `neontology` will not raise an error. Users who have not installed it simply won't have `LadybugConfig` available.

---

## Change 2: `src/neontology/graphconnection.py`

The `init_neontology()` function has a `graph_engines` dict that maps `NEONTOLOGY_ENGINE` environment variable values to config classes. Add `"LADYBUG"` to this dict.

**Current relevant section in `init_neontology()` (lines ~297–308):**
```python
def init_neontology(config: Optional[GraphEngineConfig] = None, **kwargs) -> None:
    """Initialise neontology."""
    graph_engines = {
        "NEO4J": Neo4jConfig,
        "MEMGRAPH": MemgraphConfig,
    }

    try:
        from .graphengines import NetworkxConfig

        graph_engines["NETWORKX"] = NetworkxConfig

    except ImportError:
        pass
```

**Required addition — append after the NetworkX block:**
```python
    try:
        from .graphengines import LadybugConfig

        graph_engines["LADYBUG"] = LadybugConfig

    except ImportError:
        pass
```

After this change, users can select the Ladybug engine via environment variable:

```bash
export NEONTOLOGY_ENGINE=LADYBUG
export LADYBUG_DATABASE_PATH=/path/to/mydb  # optional; omit for in-memory
```

Or omit the env var and pass the config explicitly:

```python
init_neontology(LadybugConfig(database_path="/path/to/mydb"))
```

---

## Change 3: `src/neontology/__init__.py`

The top-level `__init__.py` exports engine config classes for user convenience. Add `LadybugConfig` using the same optional-import pattern.

**Current file (`src/neontology/__init__.py`):**
```python
from .graphengines.memgraphengine import MemgraphConfig
from .graphengines.neo4jengine import Neo4jConfig

__all__ = [
    ...
    "Neo4jConfig",
    "MemgraphConfig",
]
```

**Required additions:**

At the end of the imports section, add:
```python
try:
    from .graphengines.ladybugengine import LadybugConfig  # noqa: F401
except ImportError:
    pass
```

Add `"LadybugConfig"` to the `__all__` list conditionally, or simply leave `__all__` as-is and let the try/except control availability. The simplest approach is to add it unconditionally to `__all__` — if the import failed, the name won't exist at runtime but `__all__` is only advisory:

```python
__all__ = [
    ...
    "Neo4jConfig",
    "MemgraphConfig",
    "LadybugConfig",  # only available if ladybug is installed (uv add ladybug)
]
```

---

## Adding `ladybug` to `pyproject.toml`

In `pyproject.toml`, add `ladybug` as an optional dependency in the same style as other optional extras. Locate the `[project.optional-dependencies]` section (or create it if absent) and add:

```toml
[project.optional-dependencies]
ladybug = ["ladybug"]
```

Users install it with:
```bash
uv add ladybug
# or
pip install neontology[ladybug]
```


---

## Verification Checklist

After completing all implementation files, run through each of these checks in order. If any check fails, the corresponding section of the implementation guide explains what to fix.

### Check 1: Import without error

```python
from neontology import init_neontology, BaseNode, BaseRelationship
from neontology.graphengines.ladybugengine import LadybugConfig, LadybugEngine
```

Expected: no `ImportError`, no `ModuleNotFoundError`. If `ladybug` is not installed, this import will fail — install it first with `uv add ladybug`.

### Check 2: Engine initialises

```python
from neontology import init_neontology
from neontology.graphengines.ladybugengine import LadybugConfig

init_neontology(LadybugConfig())  # in-memory database
```

Expected: no exception. `GraphConnection` singleton is created, `verify_connection()` returns `True`.

### Check 3: Node table is created via `apply_constraint`

```python
from neontology import init_neontology, GraphConnection
from neontology.graphengines.ladybugengine import LadybugConfig
from neontology.utils import auto_constrain_neo4j

class Person(BaseNode):
    __primarylabel__ = "Person"
    __primaryproperty__ = "name"
    name: str
    age: int

init_neontology(LadybugConfig())
auto_constrain_neo4j()

gc = GraphConnection()
tables = gc.engine.get_constraints()
assert "Person" in tables, f"Person table not found, got: {tables}"
```

### Check 4: Node merge and match round-trip

```python
from neontology import init_neontology, BaseNode
from neontology.graphengines.ladybugengine import LadybugConfig

class Person(BaseNode):
    __primarylabel__ = "Person"
    __primaryproperty__ = "name"
    name: str
    age: int

init_neontology(LadybugConfig())

alice = Person(name="Alice", age=30)
alice.merge()

results = Person.match_nodes()
assert len(results) == 1, f"Expected 1 result, got {len(results)}"
assert results[0].name == "Alice"
assert results[0].age == 30
```

### Check 5: Merge is idempotent

```python
alice.merge()
alice.merge()

results = Person.match_nodes()
assert len(results) == 1, "Duplicate nodes created by repeated merge"
```

### Check 6: Relationship merge

```python
from neontology import init_neontology, BaseNode, BaseRelationship
from neontology.graphengines.ladybugengine import LadybugConfig

class Person(BaseNode):
    __primarylabel__ = "Person"
    __primaryproperty__ = "name"
    name: str

class Knows(BaseRelationship):
    __relationshiptype__ = "KNOWS"
    source: Person
    target: Person

init_neontology(LadybugConfig())

alice = Person(name="Alice")
bob = Person(name="Bob")
alice.merge()
bob.merge()

rel = Knows(source=alice, target=bob)
rel.merge()

rels = Knows.match_relationships()
assert len(rels) == 1
assert rels[0].source.name == "Alice"
assert rels[0].target.name == "Bob"
```

### Check 7: `evaluate_query` with hand-written Cypher

```python
from neontology import init_neontology, GraphConnection
from neontology.graphengines.ladybugengine import LadybugConfig

# (after Check 4 setup, Alice exists in the DB)

gc = GraphConnection()
result = gc.evaluate_query(
    "MATCH (n:Person) WHERE n.name = $name RETURN n",
    params={"name": "Alice"},
)
assert len(result.nodes) == 1
assert result.nodes[0].name == "Alice"
```

### Check 8: `close_connection` does not raise

```python
from neontology import GraphConnection

gc = GraphConnection()
gc.close()  # should not raise
gc.close()  # calling twice should also not raise
```

### Check 9: Node count

```python
count = Person.get_count()
assert count == 1
```

### Check 10: `match_nodes` with filters

```python
people = [Person(name=f"Person{i}", age=i * 10) for i in range(5)]
for p in people:
    p.merge()

results = Person.match_nodes(filters={"age__gt": 20})
ages = [r.age for r in results]
assert all(a > 20 for a in ages)

results = Person.match_nodes(limit=2)
assert len(results) == 2
```

---

## Test File Location and Markers

Place integration tests in `tests/test_ladybug_engine.py`. Mark tests that require Ladybug with two markers:

```python
import pytest

pytestmark = [
    pytest.mark.uses_graph,
    pytest.mark.ladybug_engine,
]
```

This mirrors the convention used for other engines (`neo4j-engine`, `networkx-engine`). The `uses_graph` marker allows the existing test runner command `uv run pytest -m "not uses_graph"` to skip Ladybug tests when no database is available.

Register the new marker in `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
[tool.pytest.ini_options]
markers = [
    "uses_graph: marks tests that require a running graph database",
    "neo4j_engine: marks tests that target the Neo4j engine",
    "memgraph_engine: marks tests that target the Memgraph engine",
    "networkx_engine: marks tests that target the NetworkX engine",
    "ladybug_engine: marks tests that target the Ladybug engine",
]
```

Run Ladybug tests with:
```bash
uv run pytest -k "ladybug_engine"
```

Run all tests except live-DB tests:
```bash
uv run pytest -m "not uses_graph"
```
