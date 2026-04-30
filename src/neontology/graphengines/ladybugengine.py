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

    def verify_connection(self) -> bool:
        """Verify the connection to the database."""
        raise NotImplementedError

    def close_connection(self) -> None:
        """Close the database connection."""
        raise NotImplementedError

    def evaluate_query(
        self,
        cypher: str,
        params: dict[str, Any] = {},
        node_classes: dict = {},
        relationship_classes: dict = {},
    ) -> NeontologyResult:
        """Evaluate a cypher query."""
        raise NotImplementedError

    def evaluate_query_single(self, cypher: str, params: dict[str, Any] = {}) -> Any:
        """Evaluate a cypher query that returns a single result."""
        raise NotImplementedError

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
