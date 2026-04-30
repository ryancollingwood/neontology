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
    from ..baserelationship import BaseRelationship

BaseNodeT = TypeVar("BaseNodeT", bound="BaseNode")
BaseRelationshipT = TypeVar("BaseRelationshipT", bound="BaseRelationship")


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
        """Apply a constraint."""
        raise NotImplementedError

    def drop_constraint(self, constraint_name: str) -> None:
        """Drop a constraint."""
        raise NotImplementedError

    def get_constraints(self) -> list:
        """Get constraints."""
        raise NotImplementedError

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
