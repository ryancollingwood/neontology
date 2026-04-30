from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Optional, TypeVar, Union, get_args, get_origin

import ladybug as lb
from dotenv import load_dotenv
from pydantic import model_validator

from ..result import NeontologyResult
from .graphengine import GraphEngineBase, GraphEngineConfig

if TYPE_CHECKING:
    from ..basenode import BaseNode
    from ..baserelationship import BaseRelationship

BaseNodeT = TypeVar("BaseNodeT", bound="BaseNode")
BaseRelationshipT = TypeVar("BaseRelationshipT", bound="BaseRelationship")


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

    # ------------------------------------------------------------------
    # Abstract methods — must be implemented (see 03-abstract-methods.md)
    # ------------------------------------------------------------------

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

        rel_class = rel_type_data.relationship_class
        src_class = rel_type_data.source_class if hasattr(rel_type_data, "source_class") else None
        tgt_class = rel_type_data.target_class if hasattr(rel_type_data, "target_class") else None

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

    def evaluate_query(
        self,
        cypher: str,
        params: dict[str, Any] = None,
        node_classes: dict = None,
        relationship_classes: dict = None,
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
        if params is None:
            params = {}
        if node_classes is None:
            node_classes = {}
        if relationship_classes is None:
            relationship_classes = {}

        query_result = self.conn.execute(cypher, parameters=params if params else None)

        raw_records = []
        hydrated_nodes_by_label_pp: dict[str, "BaseNode"] = {}
        all_rels: list["BaseRelationship"] = []

        column_names = query_result.get_column_names()

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

        unique_nodes = list(hydrated_nodes_by_label_pp.values())

        return NeontologyResult(
            records_raw=raw_records,
            records=raw_records,
            nodes=unique_nodes,
            relationships=all_rels,
            paths=[],  # TODO: implement path hydration
        )

    def evaluate_query_single(self, cypher: str, params: dict[str, Any] = None) -> Any:
        """Execute a query and return the first value of the first row.

        Used for queries that return a single scalar value, such as COUNT queries
        or DML statements that return nothing meaningful.

        Args:
            cypher: The Cypher query string.
            params: Parameter dict. Keys are plain strings (no '$' prefix).

        Returns:
            The first column value of the first row, or None if no rows returned.
        """
        if params is None:
            params = {}

        query_result = self.conn.execute(cypher, parameters=params if params else None)

        if query_result.has_next():
            row = query_result.get_next()
            if row:
                return row[0]

        return None

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

    def get_constraints(self) -> list:
        """Return the names of all currently defined tables in the database.

        Repurposed for Ladybug: returns all table names (both NODE and REL tables)
        rather than constraint names, since Ladybug enforces uniqueness via
        PRIMARY KEY in the CREATE NODE TABLE DDL rather than separate constraints.

        Returns:
            A list of table name strings.
        """
        return list(self._get_existing_tables())

    # ------------------------------------------------------------------
    # Concrete method overrides — base implementations generate
    # incompatible Cypher (see 04-concrete-overrides.md)
    # ------------------------------------------------------------------

    def create_nodes(self, labels: list, pp_key: str, properties: list, node_class: type[BaseNodeT]) -> list[BaseNodeT]:
        """Create nodes."""
        raise NotImplementedError

    def merge_nodes(self, labels: list, pp_key: str, properties: list, node_class: type[BaseNodeT]) -> list[BaseNodeT]:
        """Merge nodes."""
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
        """Merge relationships."""
        raise NotImplementedError


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
