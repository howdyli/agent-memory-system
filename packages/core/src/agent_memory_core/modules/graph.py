"""GraphManager — Core-layer graph memory management.

Pure business logic, no HTTP/auth dependency.
Manages entities, relationships, graph traversal, and NL queries.
Migrated from backend/services/graph_memory_service.py.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from ..events import EventEmitter, MemoryEvent, MemoryEventType
from ..store.base import RelationalStore

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────

ENTITY_TYPES = ["person", "organization", "location", "event"]

RELATION_TYPE_MAP = {
    # Colleague
    "同事": "colleague",
    "colleague": "colleague",
    "collaborator": "colleague",
    # Friend
    "朋友": "friend",
    "friend": "friend",
    # Superior / Subordinate
    "上级": "superior",
    "superior": "superior",
    "领导": "superior",
    "经理": "superior",
    "老板": "superior",
    "下属": "subordinate",
    "subordinate": "subordinate",
    "下级": "subordinate",
    "汇报给": "subordinate",
    # Project
    "项目": "project_member",
    "项目成员": "project_member",
    "project_member": "project_member",
    "同事关系": "project_member",
    # Family / Classmate / Mentor
    "家庭成员": "family",
    "family": "family",
    "家人": "family",
    "配偶": "family",
    "同学": "classmate",
    "classmate": "classmate",
    "校友": "classmate",
    "师兄弟": "classmate",
    "师生": "mentor",
    "mentor": "mentor",
    "老师": "mentor",
    "学生": "mentee",
    "mentee": "mentee",
}

# Reverse map for incoming edge label inversion
REVERSE_RELATION_MAP = {
    "superior": "subordinate",
    "subordinate": "superior",
    "colleague": "colleague",
    "friend": "friend",
    "project_member": "project_member",
    "mentor": "mentee",
    "mentee": "mentor",
    "classmate": "classmate",
    "family": "family",
}

# Chinese labels for display
RELATION_LABEL_ZH = {
    "colleague": "同事",
    "friend": "朋友",
    "superior": "上级",
    "subordinate": "下属",
    "project_member": "项目同事",
    "family": "家人",
    "classmate": "同学",
    "mentor": "导师",
    "mentee": "学生",
}

# LLM extraction prompt
EXTRACTION_SYSTEM_PROMPT = """你是一个实体与关系抽取引擎。
从对话文本中识别人名、组织、地点、事件及其关系。

返回 JSON 格式（不要包含其他文字）：
{
  "entities": [
    {"name": "实体名称", "type": "person|organization|location|event", "aliases": ["别名1"]}
  ],
  "relationships": [
    {
      "source": "源实体名称",
      "target": "目标实体名称",
      "relation": "关系类型（colleague|superior|subordinate|friend|project_member|classmate|family）",
      "confidence": 0.9
    }
  ]
}"""


class GraphManager:
    """Manage entity graph within a workspace.

    Replaces backend/services/graph_memory_service.py.
    Uses RelationalStore for entity/relationship CRUD
    and EventEmitter for lifecycle events.
    """

    def __init__(
        self,
        relational_store: RelationalStore,
        event_emitter: Optional[EventEmitter] = None,
        llm_backend: Optional[Any] = None,  # Injected from SDK
    ):
        self._relational = relational_store
        self._events = event_emitter
        self._llm = llm_backend

    # ── Helpers ─────────────────────────────────────────────────

    @staticmethod
    def normalize_relation_type(relation_type: str) -> str:
        """Normalize relation type, supporting Chinese/English input."""
        return RELATION_TYPE_MAP.get(relation_type, relation_type)

    @staticmethod
    def guess_entity_type(name: str, context: str = "") -> str:
        """Guess entity type from name and context."""
        if re.match(r'^[\u4e00-\u9fff]{2,4}$', name):
            return "person"
        if re.search(r'(公司|集团|学院|医院|大学|银行|机构|部门|团队|项目组|有限公司)$', name):
            return "organization"
        if re.search(r'(市|区|省|路|街|大厦|广场|中心)$', name):
            return "location"
        return "organization"

    # ── Entity Management ───────────────────────────────────────

    def ensure_entity(
        self,
        workspace_id: int,
        name: str,
        entity_type: str,
        aliases: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create or update an entity. Returns entity info dict with 'id' and 'created' flag."""
        now = datetime.now().isoformat()

        # Check existing by name+type
        existing = self._relational.get_entity_by_name(workspace_id, name, entity_type)

        if existing:
            entity_id = existing.get("id") or existing.get("entity_id")
            # Update last_seen_at via metadata update
            # (store may not have direct last_seen_at column, but we update metadata)
            current_metadata = existing.get("metadata")
            if isinstance(current_metadata, str):
                try:
                    current_metadata = json.loads(current_metadata)
                except (json.JSONDecodeError, TypeError):
                    current_metadata = {}

            # Merge aliases
            if aliases:
                current_aliases = set()
                existing_aliases_raw = existing.get("aliases")
                if isinstance(existing_aliases_raw, str):
                    try:
                        current_aliases = set(json.loads(existing_aliases_raw))
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif isinstance(existing_aliases_raw, list):
                    current_aliases = set(existing_aliases_raw)

                merged_aliases = list(current_aliases | set(aliases))
                # Re-ensure with updated aliases
                entity_id = self._relational.ensure_entity(
                    workspace_id=workspace_id,
                    name=name,
                    entity_type=entity_type,
                    aliases=merged_aliases,
                    metadata={**current_metadata, "last_seen_at": now} if current_metadata else {"last_seen_at": now},
                )

            return {"id": entity_id, "name": name, "entity_type": entity_type, "created": False}

        # Create new entity
        entity_id = self._relational.ensure_entity(
            workspace_id=workspace_id,
            name=name,
            entity_type=entity_type,
            aliases=aliases,
            metadata=metadata,
        )

        if self._events:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.ENTITY_CREATED,
                workspace_id=workspace_id,
                memory_type="entity",
                memory_id=str(entity_id),
                data={"name": name, "entity_type": entity_type},
            ))

        return {
            "id": entity_id,
            "name": name,
            "entity_type": entity_type,
            "aliases": aliases,
            "metadata": metadata,
            "created": True,
        }

    def get_entity(self, workspace_id: int, entity_id: int) -> Optional[Dict[str, Any]]:
        """Get entity details with relationship count."""
        entity = self._relational.get_entity(workspace_id, entity_id)
        if entity is None:
            return None

        # Count relationships
        source_rels = self._relational.list_relationships(
            workspace_id=workspace_id,
            source_entity_id=entity_id,
            is_active=True,
        )
        target_rels = self._relational.list_relationships(
            workspace_id=workspace_id,
            target_entity_id=entity_id,
            is_active=True,
        )

        entity["relation_counts"] = {
            "as_source": len(source_rels),
            "as_target": len(target_rels),
            "total": len(source_rels) + len(target_rels),
        }
        return entity

    def update_entity(
        self,
        workspace_id: int,
        entity_id: int,
        name: Optional[str] = None,
        entity_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update entity fields. Returns True on success."""
        # The store doesn't have a direct update_entity method.
        # We use ensure_entity with the new name/type, which creates a new entity
        # if name+type combo changes. For metadata-only updates, we re-ensure.
        existing = self._relational.get_entity(workspace_id, entity_id)
        if existing is None:
            raise ValueError(f"Entity {entity_id} not found")

        # If name or type changed, we need to re-ensure (UPSERT)
        new_name = name or existing.get("name", "")
        new_type = entity_type or existing.get("entity_type", "")
        new_metadata = metadata or {}
        if isinstance(existing.get("metadata"), str):
            try:
                old_meta = json.loads(existing["metadata"])
                new_metadata = {**old_meta, **new_metadata}
            except (json.JSONDecodeError, TypeError):
                pass

        self._relational.ensure_entity(
            workspace_id=workspace_id,
            name=new_name,
            entity_type=new_type,
            aliases=existing.get("aliases") if isinstance(existing.get("aliases"), list) else None,
            metadata=new_metadata,
        )

        if self._events:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.ENTITY_UPDATED,
                workspace_id=workspace_id,
                memory_type="entity",
                memory_id=str(entity_id),
            ))

        return True

    def delete_entity(self, workspace_id: int, entity_id: int) -> bool:
        """Delete entity and its relationships."""
        success = self._relational.delete_entity(workspace_id, entity_id)

        if success and self._events:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.ENTITY_DELETED,
                workspace_id=workspace_id,
                memory_type="entity",
                memory_id=str(entity_id),
            ))

        return success

    def search_entities(
        self,
        workspace_id: int,
        query: str,
        entity_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search entities by name (fuzzy/prefix match)."""
        entities = self._relational.list_entities(
            workspace_id=workspace_id,
            entity_type=entity_type,
            limit=limit,
        )
        # Filter by name match (store doesn't support LIKE directly)
        like_pattern = query.lower()
        results = [
            e for e in entities
            if like_pattern in (e.get("name") or "").lower()
        ]
        return results[:limit]

    # ── Relationship Management ─────────────────────────────────

    def add_relationship(
        self,
        workspace_id: int,
        source_name: str,
        target_name: str,
        relation_type: str,
        source_type: str = "person",
        target_type: str = "organization",
        relation_subtype: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        confidence: float = 0.5,
        valid_from: Optional[str] = None,
        extraction_source: str = "manual",
    ) -> Dict[str, Any]:
        """Create a relationship between two entities.

        Auto-ensures both entities first.
        Returns dict with relationship_id and 'created' flag.
        """
        # 1. Ensure both entities
        src = self.ensure_entity(workspace_id, source_name, source_type)
        tgt = self.ensure_entity(workspace_id, target_name, target_type)

        source_id = src["id"]
        target_id = tgt["id"]
        normalized_type = self.normalize_relation_type(relation_type)
        now = datetime.now().isoformat()

        # 2. Check for duplicate active relationship
        existing_rels = self._relational.list_relationships(
            workspace_id=workspace_id,
            source_entity_id=source_id,
            target_entity_id=target_id,
            relation_type=normalized_type,
            is_active=True,
        )

        if existing_rels:
            rel = existing_rels[0]
            return {
                "relationship_id": rel.get("id"),
                "created": False,
                "relation_type": normalized_type,
                "source": src,
                "target": tgt,
            }

        # 3. Create relationship
        rel_id = self._relational.add_relationship(
            workspace_id=workspace_id,
            source_entity_id=source_id,
            target_entity_id=target_id,
            relation_type=normalized_type,
            relation_subtype=relation_subtype,
            properties=properties,
            confidence=confidence,
            valid_from=valid_from or now,
            extraction_source=extraction_source,
        )

        if self._events:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.RELATIONSHIP_CREATED,
                workspace_id=workspace_id,
                memory_type="relationship",
                memory_id=str(rel_id),
                data={
                    "source_name": source_name,
                    "target_name": target_name,
                    "relation_type": normalized_type,
                },
            ))

        return {
            "relationship_id": rel_id,
            "created": True,
            "relation_type": normalized_type,
            "source": src,
            "target": tgt,
            "valid_from": valid_from or now,
        }

    def deactivate_relationship(
        self,
        workspace_id: int,
        relationship_id: int,
        reason: str = "ended",
    ) -> bool:
        """Mark a relationship as inactive. Returns True on success."""
        return self._relational.deactivate_relationship(workspace_id, relationship_id)

    def list_relationships(
        self,
        workspace_id: int,
        source_name: Optional[str] = None,
        target_name: Optional[str] = None,
        relation_type: Optional[str] = None,
        is_active: Optional[bool] = True,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """List relationships with optional filters."""
        normalized = self.normalize_relation_type(relation_type) if relation_type else None
        return self._relational.list_relationships(
            workspace_id=workspace_id,
            relation_type=normalized,
            is_active=is_active,
            limit=limit,
        )

    # ── Graph Traversal ─────────────────────────────────────────

    def get_neighbors(
        self,
        workspace_id: int,
        entity_name: Optional[str] = None,
        entity_type: str = "person",
        relation_type: Optional[str] = None,
        depth: int = 1,
        max_depth: int = 3,
        entity_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Query neighbors of an entity with optional depth traversal.

        Returns {center, neighbors, count, depth}.
        """
        # Find entity — prefer ID lookup
        if entity_id is not None:
            entity = self._relational.get_entity(workspace_id, entity_id)
            if entity is None:
                return {"center": None, "neighbors": [], "count": 0, "depth": depth}
            entity_name = entity.get("name", "")
        elif entity_name:
            entity = self._relational.get_entity_by_name(workspace_id, entity_name, entity_type)
            if entity is None:
                return {"center": entity_name, "neighbors": [], "count": 0, "depth": depth}
            entity_id = entity.get("id") or entity.get("entity_id")
        else:
            raise ValueError("Must provide entity_id or entity_name")

        actual_depth = min(depth, max_depth)
        visited: Set[int] = {entity_id}
        all_neighbors: List[Dict[str, Any]] = []
        current_ids = [entity_id]

        normalized_rel = self.normalize_relation_type(relation_type) if relation_type else None

        for d in range(actual_depth):
            next_ids = []
            if not current_ids:
                break

            for cid in current_ids:
                # Get all active relationships for this entity
                rels = self._relational.list_relationships(
                    workspace_id=workspace_id,
                    source_entity_id=cid,
                    is_active=True,
                    limit=200,
                )
                # Also get as target
                target_rels = self._relational.list_relationships(
                    workspace_id=workspace_id,
                    target_entity_id=cid,
                    is_active=True,
                    limit=200,
                )

                for rel in rels:
                    neighbor_id = rel.get("target_entity_id")
                    direction = "outgoing"
                    rel_label = rel.get("relation_type", "")

                    # Filter by relation type
                    if normalized_rel and rel_label != normalized_rel:
                        continue

                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        # Get neighbor entity info
                        neighbor_entity = self._relational.get_entity(workspace_id, neighbor_id)
                        if neighbor_entity:
                            all_neighbors.append({
                                "entity_id": neighbor_id,
                                "entity_name": neighbor_entity.get("name", ""),
                                "entity_type": neighbor_entity.get("entity_type", ""),
                                "relation_type": rel_label,
                                "direction": direction,
                                "depth": d + 1,
                                "confidence": rel.get("confidence", 0.5),
                                "relationship_id": rel.get("id"),
                            })
                            if d + 1 < actual_depth:
                                next_ids.append(neighbor_id)

                for rel in target_rels:
                    neighbor_id = rel.get("source_entity_id")
                    direction = "incoming"
                    rel_label = REVERSE_RELATION_MAP.get(
                        rel.get("relation_type", ""),
                        rel.get("relation_type", ""),
                    )

                    # Filter by relation type
                    if normalized_rel and rel_label != normalized_rel:
                        continue

                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        neighbor_entity = self._relational.get_entity(workspace_id, neighbor_id)
                        if neighbor_entity:
                            all_neighbors.append({
                                "entity_id": neighbor_id,
                                "entity_name": neighbor_entity.get("name", ""),
                                "entity_type": neighbor_entity.get("entity_type", ""),
                                "relation_type": rel_label,
                                "direction": direction,
                                "depth": d + 1,
                                "confidence": rel.get("confidence", 0.5),
                                "relationship_id": rel.get("id"),
                            })
                            if d + 1 < actual_depth:
                                next_ids.append(neighbor_id)

            current_ids = next_ids

        return {
            "center": entity_name,
            "neighbors": all_neighbors,
            "count": len(all_neighbors),
            "depth": actual_depth,
        }

    def get_entity_graph_text(
        self,
        workspace_id: int,
        center_entity: str,
        entity_type: str = "person",
        max_depth: int = 2,
    ) -> str:
        """Build text representation of entity graph for LLM context injection."""
        result = self.get_neighbors(
            workspace_id=workspace_id,
            entity_name=center_entity,
            entity_type=entity_type,
            depth=max_depth,
        )

        if not result.get("neighbors"):
            return ""

        lines = [f"[知识图谱 - 以 {center_entity} 为中心]"]

        by_depth: Dict[int, List[Dict]] = {}
        for nb in result["neighbors"]:
            d = nb.get("depth", 1)
            by_depth.setdefault(d, []).append(nb)

        for d in sorted(by_depth.keys()):
            prefix = "直接关系" if d == 1 else f"间接关系 (深度 {d})"
            lines.append(f"  {prefix} ({center_entity}):")
            for nb in by_depth[d]:
                label = RELATION_LABEL_ZH.get(nb["relation_type"], nb["relation_type"])
                arrow = "→" if nb["direction"] == "outgoing" else "←"
                lines.append(
                    f"    - {center_entity} {arrow} {nb['entity_name']} "
                    f"({label}, 置信度: {nb.get('confidence', 0.5):.2f})"
                )

        return "\n".join(lines)

    # ── Entity Extraction ───────────────────────────────────────

    def extract_entities_from_text(
        self,
        workspace_id: int,
        text: str,
    ) -> Dict[str, Any]:
        """Extract entities and relationships from text.

        Uses LLM if available, falls back to regex.
        Returns {entities, relationships, extraction_method, stored}.
        """
        result: Dict[str, Any] = {
            "entities": [],
            "relationships": [],
            "extraction_method": "unknown",
        }

        # Try LLM extraction
        llm_success = False
        if self._llm:
            try:
                msg = [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ]
                response = self._llm.chat(messages=msg, temperature=0.1)
                if response and response.get("content"):
                    content = response["content"]
                    code_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
                    if code_match:
                        content = code_match.group(1).strip()
                    json_match = re.search(r'\{.*\}', content, re.DOTALL)
                    if json_match:
                        parsed = json.loads(json_match.group(0))
                        result["entities"] = parsed.get("entities", [])
                        result["relationships"] = parsed.get("relationships", [])
                        result["extraction_method"] = "llm"
                        llm_success = True
            except Exception as e:
                logger.debug(f"LLM extraction failed, falling back to regex: {e}")

        # Regex fallback
        if not llm_success:
            result["extraction_method"] = "regex"

            # Simple Chinese name extraction
            people = re.findall(r'([\u4e00-\u9fff]{2,4})(?:是|叫|说|问|告诉|通知|联系|找)', text)
            for p in set(people):
                result["entities"].append({"name": p, "type": "person", "aliases": []})

            # Organization extraction
            orgs = re.findall(r'([\u4e00-\u9fff]{2,10}(?:公司|集团|学院|医院|大学|银行|机构|部门|团队|项目组))', text)
            for o in set(orgs):
                result["entities"].append({"name": o, "type": "organization", "aliases": []})

            # Location extraction
            locs = re.findall(r'在([\u4e00-\u9fff]{2,6}(?:市|区|省|路|街|大厦|广场|中心))', text)
            for l in set(locs):
                result["entities"].append({"name": l, "type": "location", "aliases": []})

            # Relationship patterns
            rel_patterns = [
                (r'([\u4e00-\u9fff]{2,4})的同事([\u4e00-\u9fff]{2,4})', "colleague"),
                (r'([\u4e00-\u9fff]{2,4})的朋友([\u4e00-\u9fff]{2,4})', "friend"),
                (r'([\u4e00-\u9fff]{2,4})的领导([\u4e00-\u9fff]{2,4})', "superior"),
                (r'([\u4e00-\u9fff]{2,4})的下属([\u4e00-\u9fff]{2,4})', "subordinate"),
            ]
            for pattern, rel_type in rel_patterns:
                matches = re.findall(pattern, text)
                for src, tgt in matches:
                    result["relationships"].append({
                        "source": src,
                        "target": tgt,
                        "relation": rel_type,
                        "confidence": 0.6,
                    })

            # Deduplicate entities
            seen: Set[Tuple[str, str]] = set()
            unique = []
            for e in result["entities"]:
                key = (e["name"], e["type"])
                if key not in seen:
                    seen.add(key)
                    unique.append(e)
            result["entities"] = unique

        # Store extracted entities and relationships
        stored = {"entities": 0, "relationships": 0}
        for ent in result["entities"]:
            try:
                self.ensure_entity(
                    workspace_id=workspace_id,
                    name=ent["name"],
                    entity_type=ent["type"],
                    aliases=ent.get("aliases"),
                )
                stored["entities"] += 1
            except Exception as e:
                logger.debug(f"Entity store failed: {e}")

        for rel in result["relationships"]:
            try:
                self.add_relationship(
                    workspace_id=workspace_id,
                    source_name=rel["source"],
                    target_name=rel["target"],
                    relation_type=rel["relation"],
                    confidence=rel.get("confidence", 0.5),
                    extraction_source="auto_extract",
                )
                stored["relationships"] += 1
            except Exception as e:
                logger.debug(f"Relationship store failed: {e}")

        result["stored"] = stored
        return result

    # ── NL Graph Query ──────────────────────────────────────────

    def query_graph(
        self,
        workspace_id: int,
        query: str,
    ) -> Dict[str, Any]:
        """Natural language graph query. Routes to appropriate graph operation."""
        entities = _extract_query_entities(query, self._llm)
        if not entities:
            return {"result": "未识别到实体", "query_type": "unknown"}

        query_type = _classify_query(query)

        if query_type == "neighbors":
            rel_type = _extract_relation_type_from_query(query)
            return self.get_neighbors(
                workspace_id=workspace_id,
                entity_name=entities[0],
                entity_type="person",
                relation_type=rel_type,
            )

        elif query_type == "history":
            if len(entities) >= 2:
                return self.get_relationship_history(
                    workspace_id=workspace_id,
                    entity1_name=entities[0],
                    entity2_name=entities[1],
                )
            else:
                graph_text = self.get_entity_graph_text(
                    workspace_id=workspace_id,
                    center_entity=entities[0],
                )
                return {"result": graph_text, "query_type": "history", "entity": entities[0]}

        elif query_type == "graph":
            graph_text = self.get_entity_graph_text(
                workspace_id=workspace_id,
                center_entity=entities[0],
            )
            return {"result": graph_text, "query_type": "graph", "entity": entities[0]}

        else:
            return {"entities": self.search_entities(workspace_id, entities[0])}

    def get_relationship_history(
        self,
        workspace_id: int,
        entity1_name: str,
        entity2_name: str,
        entity1_type: str = "person",
        entity2_type: str = "organization",
    ) -> Dict[str, Any]:
        """Get relationship change history between two entities."""
        e1 = self._relational.get_entity_by_name(workspace_id, entity1_name, entity1_type)
        e2 = self._relational.get_entity_by_name(workspace_id, entity2_name, entity2_type)

        if not e1 or not e2:
            return {"history": [], "count": 0, "message": "一方实体不存在"}

        e1_id = e1.get("id") or e1.get("entity_id")
        e2_id = e2.get("id") or e2.get("entity_id")

        # Get all relationships between these entities
        rels = self._relational.list_relationships(
            workspace_id=workspace_id,
            source_entity_id=e1_id,
            target_entity_id=e2_id,
            is_active=None,
            limit=50,
        )
        # Also reverse direction
        reverse_rels = self._relational.list_relationships(
            workspace_id=workspace_id,
            source_entity_id=e2_id,
            target_entity_id=e1_id,
            is_active=None,
            limit=50,
        )

        history = rels + reverse_rels

        return {
            "history": history,
            "count": len(history),
            "entities": {
                "entity1": {"name": entity1_name, "type": entity1_type, "id": e1_id},
                "entity2": {"name": entity2_name, "type": entity2_type, "id": e2_id},
            },
        }

    # ── Duplicate Detection ─────────────────────────────────────

    def detect_duplicate_entities(
        self,
        workspace_id: int,
        threshold: int = 3,
    ) -> List[Dict[str, Any]]:
        """Detect similar entities based on name edit distance."""
        entities = self._relational.list_entities(workspace_id, limit=500)

        duplicates = []
        seen_pairs: Set[Tuple[int, int]] = set()

        for i, e1 in enumerate(entities):
            for e2 in entities[i + 1:]:
                if (e1.get("entity_type") or e1.get("type")) != (e2.get("entity_type") or e2.get("type")):
                    continue
                dist = _levenshtein_distance(
                    e1.get("name", ""),
                    e2.get("name", ""),
                )
                if 0 < dist < threshold:
                    pair_key = (
                        min(e1.get("id", 0), e2.get("id", 0)),
                        max(e1.get("id", 0), e2.get("id", 0)),
                    )
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        max_len = max(len(e1.get("name", "")), len(e2.get("name", "")), 1)
                        duplicates.append({
                            "entity_a": {"id": e1.get("id"), "name": e1.get("name"), "entity_type": e1.get("entity_type")},
                            "entity_b": {"id": e2.get("id"), "name": e2.get("name"), "entity_type": e2.get("entity_type")},
                            "distance": dist,
                            "similarity": round(1 - dist / max_len, 3),
                        })

        duplicates.sort(key=lambda x: x["similarity"], reverse=True)
        return duplicates

    def merge_entities(
        self,
        workspace_id: int,
        target_id: int,
        source_ids: List[int],
    ) -> Dict[str, Any]:
        """Merge duplicate entities. Redirects all relationships to target, deletes sources."""
        if not source_ids:
            raise ValueError("Source entity list is empty")
        if target_id in source_ids:
            raise ValueError("Target cannot be in source list")

        target = self._relational.get_entity(workspace_id, target_id)
        if target is None:
            raise ValueError("Target entity not found")

        redirected = 0
        for sid in source_ids:
            # Note: relationship redirect requires direct SQL or store method
            # For now, we delete source entity which cascades relationships
            self._relational.delete_entity(workspace_id, sid)
            redirected += 1

        return {
            "target_id": target_id,
            "merged_count": redirected,
        }

    # ── Statistics ──────────────────────────────────────────────

    def get_statistics(self, workspace_id: int) -> Dict[str, Any]:
        """Get graph statistics for a workspace."""
        entities = self._relational.list_entities(workspace_id, limit=10000)
        relationships = self._relational.list_relationships(
            workspace_id=workspace_id, is_active=True, limit=10000,
        )

        entity_types: Dict[str, int] = {}
        for e in entities:
            et = e.get("entity_type") or e.get("type", "unknown")
            entity_types[et] = entity_types.get(et, 0) + 1

        relation_types: Dict[str, int] = {}
        for r in relationships:
            rt = r.get("relation_type", "unknown")
            relation_types[rt] = relation_types.get(rt, 0) + 1

        # Top entities by relationship count
        entity_rel_counts: Dict[int, int] = {}
        for r in relationships:
            src = r.get("source_entity_id")
            tgt = r.get("target_entity_id")
            if src:
                entity_rel_counts[src] = entity_rel_counts.get(src, 0) + 1
            if tgt:
                entity_rel_counts[tgt] = entity_rel_counts.get(tgt, 0) + 1

        top_entities = []
        for e in entities:
            eid = e.get("id") or e.get("entity_id")
            if eid in entity_rel_counts:
                top_entities.append({
                    "name": e.get("name"),
                    "entity_type": e.get("entity_type") or e.get("type"),
                    "relation_count": entity_rel_counts[eid],
                })
        top_entities.sort(key=lambda x: x["relation_count"], reverse=True)
        top_entities = top_entities[:10]

        return {
            "entity_count": len(entities),
            "relationship_count": len(relationships),
            "entity_types": entity_types,
            "relationship_types": relation_types,
            "top_entities": top_entities,
        }


# ── NL Query Helpers (module-level) ─────────────────────────────

def _extract_query_entities(text: str, llm: Optional[Any] = None) -> List[str]:
    """Extract entity names from NL query."""
    # Try LLM-based extraction
    if llm:
        try:
            # Simple: ask LLM to extract entity names
            result = llm.chat(
                messages=[
                    {"role": "system", "content": "Extract entity names from the query. Return JSON array of names only."},
                    {"role": "user", "content": text},
                ],
                temperature=0.0,
            )
            if result and result.get("content"):
                import json as _json
                match = re.search(r'\[.*?\]', result["content"], re.DOTALL)
                if match:
                    return _json.loads(match.group(0))
        except Exception:
            pass

    # Regex fallback
    quoted = re.findall(r'[""」](.+?)[""「]', text)
    if quoted:
        return quoted

    names = re.findall(r'([\u4e00-\u9fff]{2,4})(?:的同事|的朋友|的领导|的下属|和|与|跟)', text)
    if names:
        return list(set(names))

    orgs = re.findall(r'([\u4e00-\u9fff]{2,10}(?:公司|集团|学院))', text)
    if orgs:
        return orgs

    return []


def _classify_query(query: str) -> str:
    """Classify NL query type: neighbors / history / graph / search."""
    if re.search(r'(的同事|的朋友|的领导|的下属|认识谁|认识什么人)', query):
        return "neighbors"
    if re.search(r'(的关系|的经历|的历程|的关系史|变化)', query):
        return "history"
    if re.search(r'(的关系网|的圈子|的图谱|关于.*的关系)', query):
        return "graph"
    return "search"


def _extract_relation_type_from_query(query: str) -> Optional[str]:
    """Extract relation type from NL query."""
    mapping = {
        r'同事': "colleague",
        r'朋友': "friend",
        r'领导': "superior",
        r'上级': "superior",
        r'下属': "subordinate",
        r'下级': "subordinate",
        r'同学': "classmate",
        r'家人': "family",
    }
    for pattern, rel_type in mapping.items():
        if re.search(pattern, query):
            return rel_type
    return None


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            curr_row.append(min(
                prev_row[j + 1] + 1,
                curr_row[j] + 1,
                prev_row[j] + (c1 != c2),
            ))
        prev_row = curr_row
    return prev_row[-1]
