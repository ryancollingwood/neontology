# Neontology

A Python object-graph mapper for Neo4j (and compatible openCypher databases) using Pydantic and pandas. Nodes inherit `BaseNode`, relationships inherit `BaseRelationship`. Core operations: `create()`, `merge()`, `merge_df()`.

## Tech Stack

- **Python** ≥3.9, **Pydantic** v2, **pandas** v2, **neo4j** driver v5
- **Package manager**: `uv`
- **Linting/formatting**: `ruff` (line length 128, Google docstring convention)
- **Testing**: `pytest` with `pytest-cov` and `pytest-benchmark`
- **Docs**: `mkdocs`

## Project Layout

```
src/neontology/
  __init__.py           # public API exports
  basenode.py           # BaseNode
  baserelationship.py   # BaseRelationship
  commonmodel.py        # shared model logic
  graphconnection.py    # GraphConnection singleton
  graphengines/         # neo4j, memgraph, networkx engine adapters
  gql.py                # GQL/Cypher query support
  result.py             # query result types
  schema_utils.py
  tools/                # import_files, import_records helpers
  utils.py
tests/
  conftest.py
  test_basenode.py
  test_baserelationship.py
  test_commonmodel.py
  test_graph_connection.py
  test_tools/
  test_utils.py
```

## Development Setup

```bash
uv sync
uv build
```

## Testing

```bash
uv run pytest -x                          # stop on first failure
uv run pytest -m "not uses_graph"         # skip tests needing a live DB
uv run pytest -k 'networkx-engine'        # run against a specific engine only
uv run pytest --cov=src/neontology        # coverage report
uv run pytest --benchmark-disable         # run benchmarked tests without stats
```

Tests that require a live graph database are marked `uses_graph`. Engines: `neo4j-engine`, `memgraph-engine`, `networkx-engine`.

## Linting & Formatting

```bash
uv run ruff check src
uv run ruff check --fix src
uv run ruff format src
```

## Docs

```bash
uv run mkdocs serve    # live preview
uv run mkdocs build    # static build
```

## Active Branch: `ladybugdb`

Work in progress — new `ladybugdb` graph engine for an overview of the implementation docs see `docs/ladybugdb/implementation/00-overview.md`.
Ladybugs Cypher dialect docs are available under `docs/ladybugdb/`.
