# Ladybug Engine — Overview

This guide walks through implementing a `LadybugEngine` for neontology. Ladybug is the embedded graph database formerly known as Kuzu. It runs in-process (no server required) and implements an openCypher dialect with several important differences from Neo4j Cypher. This document explains the architectural context you must understand before writing any code.

---

## Neontology's Engine System

Neontology uses a pluggable engine architecture. Every database backend is represented by two classes:

1. **`GraphEngineBase`** — the abstract base class that all engines inherit from. Located at `src/neontology/graphengines/graphengine.py`. Defines 6 abstract methods (must be overridden) and several concrete methods that generate Cypher and call the abstract methods.
2. **`GraphEngineConfig`** — a Pydantic `BaseModel` that holds connection parameters. Located in the same file. Has a `ClassVar engine` that points to the engine class. Has a `model_validator` that auto-populates fields from environment variables.

These two classes always come in a pair. Neo4j has `Neo4jEngine` + `Neo4jConfig`. NetworkX has `NetworkxEngine` + `NetworkxConfig`. You will create `LadybugEngine` + `LadybugConfig`.

### How neontology selects an engine

The user calls `init_neontology()` in `src/neontology/graphconnection.py`. That function:

1. Accepts an optional `config: GraphEngineConfig` argument.
2. If no config is given, reads the `NEONTOLOGY_ENGINE` environment variable and looks it up in a `graph_engines` dict (`"NEO4J"`, `"MEMGRAPH"`, `"NETWORKX"`). You will add `"LADYBUG"` to this dict.
3. Calls `GraphConnection(config)`, which is a singleton that stores the engine instance.

After `init_neontology()` runs, `BaseNode.merge()`, `BaseNode.match_nodes()`, `BaseRelationship.merge()`, and all other high-level operations delegate to the active engine via `GraphConnection`.

### The singleton

`GraphConnection` is a singleton (one instance per Python process). Its `__new__` method:

1. Creates `self.engine = config.engine(config)` — instantiates your engine class.
2. Calls `get_node_types()` and `get_rels_by_type()` to capture all currently defined `BaseNode` and `BaseRelationship` subclasses and stores them as `GraphConnection.global_nodes` and `GraphConnection.global_rels`.

Its `__init__` method immediately calls `self.engine.verify_connection()` and raises `RuntimeError` if it returns `False`. **Your `verify_connection()` must return `True` after `__init__` completes.**

---

## Where to Put the New Files

Create one new file:

```
src/neontology/graphengines/ladybugengine.py
```

This file will contain both `LadybugConfig` and `LadybugEngine`. Look at `src/neontology/graphengines/neo4jengine.py` as the primary reference for structure and style.

---

## Why Ladybug Needs Its Own Engine

Ladybug cannot share the Neo4j or Memgraph engine because:

1. **No network driver.** Ladybug runs embedded in-process. The `ladybug` Python package provides a direct `lb.Database` / `lb.Connection` API. There is no Bolt protocol.
2. **Schema must be defined before data can be written.** Ladybug is a *structured property graph* database. Before you can `CREATE` or `MERGE` a node with label `Person`, a `NODE TABLE Person (...)` must already exist. Neo4j does not require this.
3. **One label per node table.** Each Ladybug node belongs to exactly one node table, which has exactly one label. Neo4j nodes can have multiple labels simultaneously. The base class Cypher uses multi-label syntax like `(n:Person:Employee)` which Ladybug rejects.
4. **`SET n += dict` is not supported.** Neo4j supports bulk property assignment with the `+=` map operator. Ladybug does not. Every property must be set individually: `SET n.name = $name, n.age = $age`.
5. **Different function names.** `labels()` → `label()`, `date()` → `current_date()`, `timestamp()` → `current_timestamp()`, list functions use `list_` prefix, etc.

The full Cypher dialect reference is in `docs/ladybugdb/cypher/`. Read `docs/ladybugdb/cypher/difference.md` and `docs/ladybugdb/cypher/data-manipulation-clauses/` before writing any Cypher in the engine.

---

## Python Package Dependency

Ladybug's Python package is `ladybug`. It must be declared as an **optional dependency** in `pyproject.toml`, exactly like `grandcypher` is optional for the NetworkX engine.

```bash
uv add ladybug
```

```python
import ladybug as lb
```

In `pyproject.toml`, add `ladybug` to an optional extras group:

```toml
[project.optional-dependencies]
ladybug = ["ladybug"]
```

In `ladybugengine.py`, import `ladybug` at the top level — the optional-import guard goes in `src/neontology/graphengines/__init__.py` and `src/neontology/graphconnection.py` (see `05-registration-and-testing.md` for the exact lines).

---

## Reading Order for This Guide

Read the files in this order. Each file builds on the previous one.

| File | What it covers |
|------|---------------|
| `00-overview.md` (this file) | Architecture, engine system, why a new engine is needed |
| `01-config-and-skeleton.md` | `LadybugConfig` class and `LadybugEngine` skeleton with all method stubs |
| `02-schema-management.md` | How to auto-create `NODE TABLE` and `REL TABLE` DDL before DML |
| `03-abstract-methods.md` | Full implementation of all 6 abstract methods |
| `04-concrete-overrides.md` | Which inherited concrete methods generate incompatible Cypher and how to fix them |
| `05-registration-and-testing.md` | Wiring the engine into `init_neontology()` and verifying the implementation works |
