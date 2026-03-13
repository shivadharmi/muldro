"""World Model — maintains entities and relationships.

The structured representation of everything Jarvis knows about:
people, projects, tasks, meetings, organizations, goals.

Responsibilities:
- Upsert entities from events
- Maintain relationships between entities
- Provide lookup APIs for planner and presenter
- Merge duplicates and resolve aliases
"""


class WorldModel:
    """Manage the entity graph."""

    async def upsert_entity(
        self,
        user_id: str,
        entity_type: str,
        canonical_name: str,
        attributes: dict | None = None,
        aliases: list[str] | None = None,
        source_refs: list[dict] | None = None,
    ) -> str:
        """Create or update an entity. Returns entity_id."""
        # TODO: Implement
        return ""

    async def add_relationship(
        self,
        user_id: str,
        from_entity_id: str,
        relation_type: str,
        to_entity_id: str,
        attributes: dict | None = None,
    ) -> str:
        """Add a relationship between entities. Returns relation_id."""
        # TODO: Implement
        return ""

    async def find_entity(self, user_id: str, query: str) -> list[dict]:
        """Search entities by name, alias, or type."""
        # TODO: Implement
        return []
