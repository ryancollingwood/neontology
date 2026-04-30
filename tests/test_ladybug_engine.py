import pytest

from neontology import init_neontology, GraphConnection, BaseNode, BaseRelationship
from neontology.graphengines.ladybugengine import LadybugConfig, LadybugEngine
from neontology.utils import auto_constrain_neo4j

pytestmark = [
    pytest.mark.uses_graph,
    pytest.mark.ladybug_engine,
]

class Person(BaseNode):
    __primarylabel__ = "Person"
    __primaryproperty__ = "name"
    name: str
    age: int

class Knows(BaseRelationship):
    __relationshiptype__ = "KNOWS"
    source: Person
    target: Person


def test_import_without_error():
    # Check 1: Should import correctly (already happens via top level imports)
    assert LadybugConfig is not None
    assert LadybugEngine is not None


def test_engine_initialises():
    # Check 2: Engine initializes without error
    init_neontology(LadybugConfig())
    gc = GraphConnection()
    assert gc.engine.verify_connection() is True


def test_node_table_created():
    # Check 3: Node table is created via apply_constraint
    init_neontology(LadybugConfig())
    auto_constrain_neo4j()

    gc = GraphConnection()
    tables = gc.engine.get_constraints()
    assert "Person" in tables, f"Person table not found, got: {tables}"


def test_node_merge_and_match_round_trip(use_graph):
    # Check 4: Node merge and match round-trip
    # use_graph fixture initializes with config in pytest params,
    # but since this runs under ladybug_engine, it will be LadybugConfig.
    alice = Person(name="Alice", age=30)
    alice.merge()

    results = Person.match_nodes()
    assert len(results) == 1, f"Expected 1 result, got {len(results)}"
    assert results[0].name == "Alice"
    assert results[0].age == 30


def test_merge_is_idempotent(use_graph):
    # Check 5: Merge is idempotent
    alice = Person(name="Alice", age=30)
    alice.merge()
    alice.merge()

    results = Person.match_nodes()
    assert len(results) == 1, "Duplicate nodes created by repeated merge"


def test_relationship_merge(use_graph):
    # Check 6: Relationship merge
    alice = Person(name="Alice", age=30)
    bob = Person(name="Bob", age=35)
    alice.merge()
    bob.merge()

    rel = Knows(source=alice, target=bob)
    rel.merge()

    rels = Knows.match_relationships()
    assert len(rels) == 1
    assert rels[0].source.name == "Alice"
    assert rels[0].target.name == "Bob"


def test_evaluate_query_with_hand_written_cypher(use_graph):
    # Check 7: evaluate_query with hand-written Cypher
    alice = Person(name="Alice", age=30)
    alice.merge()

    gc = GraphConnection()
    result = gc.evaluate_query(
        "MATCH (n:Person) WHERE n.name = $name RETURN n",
        params={"name": "Alice"},
    )
    assert len(result.nodes) == 1
    assert result.nodes[0].name == "Alice"


def test_close_connection_does_not_raise(use_graph):
    # Check 8: close_connection does not raise
    gc = GraphConnection()
    gc.close()  # should not raise
    gc.close()  # calling twice should also not raise


def test_node_count(use_graph):
    # Check 9: Node count
    alice = Person(name="Alice", age=30)
    alice.merge()

    count = Person.get_count()
    assert count == 1


def test_match_nodes_with_filters(use_graph):
    # Check 10: match_nodes with filters
    people = [Person(name=f"Person{i}", age=i * 10) for i in range(5)]
    for p in people:
        p.merge()

    results = Person.match_nodes(filters={"age__gt": 20})
    ages = [r.age for r in results]
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    assert all(a > 20 for a in ages)

    results = Person.match_nodes(limit=2)
    assert len(results) == 2
