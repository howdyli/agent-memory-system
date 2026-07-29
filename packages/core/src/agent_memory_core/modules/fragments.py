"""FragmentManager — Core-layer memory fragment management.

Pure business logic, no HTTP/auth dependency.
Uses RelationalStore for structured data, VectorStore for semantic search.
Replaces backend/services/memory_fragment_service.py.
"""

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..events import EventEmitter, MemoryEvent, MemoryEventType
from ..store.base import RelationalStore, VectorStore

logger = logging.getLogger(__name__)

DEFAULT_EXTRACTION_PROMPTS: Dict[str, Dict[str, Any]] = {
    "user_info": {
        "template": "从以下对话中抽取用户信息（姓名、角色、组织等）。\n对话: {conversation}\n请以 JSON 格式返回抽取结果。",
        "version": 1,
    },
    "preference": {
        "template": "从以下对话中抽取用户的偏好和习惯。\n对话: {conversation}\n请以列表格式返回抽取结果。",
        "version": 1,
    },
    "plan": {
        "template": "从以下对话中抽取用户的计划和待办事项。\n对话: {conversation}\n请以列表格式返回抽取结果。",
        "version": 1,
    },
    "summary": {
        "template": "请为以下对话生成简洁的摘要，不超过 {max_length} 字。\n对话: {conversation}\n摘要:",
        "version": 1,
    },
}

VECTOR_COLLECTION = "memory_fragments"


class FragmentManager:
    """Manage memory fragments: CRUD, TTL, semantic search, analysis, prompts."""

    def __init__(
        self,
        relational_store: RelationalStore,
        vector_store: VectorStore,
        event_emitter: Optional[EventEmitter] = None,
    ):
        self._relational = relational_store
        self._vector = vector_store
        self._events = event_emitter or EventEmitter()

    # ── Conversation Analysis (pure logic, no store) ─────────────

    @staticmethod
    def analyze_conversation(messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Analyze conversation history, extract user info, preferences, plans, facts."""
        user_info: Dict[str, str] = {}
        preferences: List[str] = []
        plans: List[str] = []
        key_facts: List[str] = []

        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")

            # User info: name
            for pattern in [
                r"我叫(.+?)(?:[，,。！!？?的]|$)",
                r"我是(.+?)(?:[，,。！!？?的]|$)",
                r"我的名字是(.+?)(?:[，,。！!？?的]|$)",
            ]:
                match = re.search(pattern, content)
                if match:
                    user_info["name"] = match.group(1).strip()
                    break

            # User info: organization
            org_match = re.search(r"我在(.+?)(?:工作|任职|上班)", content)
            if org_match:
                user_info["organization"] = org_match.group(1).strip()

            # User info: role
            role_match = re.search(r"我是(.+?)(?:工程师|经理|设计师|开发|PM|产品|架构师)", content)
            if role_match:
                role_suffix = re.search(r"(?:工程师|经理|设计师|开发|PM|产品|架构师)", content)
                if role_suffix:
                    user_info["role"] = role_match.group(1).strip() + role_suffix.group(0)

            # Preferences
            for pattern in [
                r"我喜欢(.+?)(?:[，,。！!？?的]|$)",
                r"我偏好(.+?)(?:[，,。！!？?的]|$)",
                r"我习惯用(.+?)(?:[，,。！!？?的]|$)",
                r"我习惯(.+?)(?:[，,。！!？?的]|$)",
            ]:
                for m in re.findall(pattern, content):
                    preferences.append(m.strip())

            # Plans
            for pattern in [
                r"我打算(.+?)(?:[，,。！!？?的]|$)",
                r"我计划(.+?)(?:[，,。！!？?的]|$)",
                r"(?:明天|后天|下周|这周|今天)我要(.+?)(?:[，,。！!？?的]|$)",
            ]:
                for m in re.findall(pattern, content):
                    plans.append(m.strip())

            # Key facts
            for subj, obj in re.findall(r"(.+?)是(.+?)(?:[，,。！!？?]|$)", content):
                subj, obj = subj.strip(), obj.strip()
                if len(subj) < 20 and len(obj) < 50 and subj and obj:
                    key_facts.append(f"{subj}是{obj}")

        return {
            "user_info": user_info,
            "preferences": list(set(preferences)),
            "plans": list(set(plans)),
            "key_facts": list(set(key_facts)),
            "message_count": len(messages),
            "analyzed_at": datetime.now().isoformat(),
        }

    @staticmethod
    def generate_summary(messages: List[Dict[str, str]], max_length: int = 200) -> Dict[str, Any]:
        """Generate a concise summary from conversation history."""
        if not messages:
            raise ValueError("没有对话历史可摘要")

        analysis = FragmentManager.analyze_conversation(messages)
        parts: List[str] = []

        if analysis["user_info"]:
            info_str = "、".join(f"{k}:{v}" for k, v in analysis["user_info"].items())
            parts.append(f"用户信息({info_str})")
        if analysis["preferences"]:
            parts.append(f"偏好({', '.join(analysis['preferences'][:3])})")
        if analysis["plans"]:
            parts.append(f"计划({', '.join(analysis['plans'][:3])})")
        if analysis["key_facts"]:
            parts.append(f"关键信息({', '.join(analysis['key_facts'][:3])})")

        summary = "。".join(parts)
        if len(summary) > max_length:
            summary = summary[:max_length - 3] + "..."

        return {
            "summary": summary,
            "user_info": analysis["user_info"],
            "preferences": analysis["preferences"],
            "plans": analysis["plans"],
            "key_facts": analysis["key_facts"],
            "message_count": analysis["message_count"],
            "generated_at": datetime.now().isoformat(),
        }

    @staticmethod
    def extract_fragments(messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Extract typed memory fragments from conversation history."""
        analysis = FragmentManager.analyze_conversation(messages)
        fragments: List[Dict[str, Any]] = []

        for key, value in analysis["user_info"].items():
            fragments.append({"fragment_type": "info", "content": f"用户的{key}是{value}", "importance_score": 0.9})
        for pref in analysis["preferences"]:
            fragments.append({"fragment_type": "preference", "content": pref, "importance_score": 0.7})
        for plan in analysis["plans"]:
            fragments.append({"fragment_type": "plan", "content": plan, "importance_score": 0.8})
        for fact in analysis["key_facts"]:
            fragments.append({"fragment_type": "info", "content": fact, "importance_score": 0.6})

        by_type: Dict[str, int] = {}
        for f in fragments:
            by_type[f["fragment_type"]] = by_type.get(f["fragment_type"], 0) + 1

        return {
            "fragments": fragments,
            "count": len(fragments),
            "by_type": by_type,
            "extracted_at": datetime.now().isoformat(),
        }

    # ── Fragment CRUD + TTL ──────────────────────────────────────

    def create(
        self,
        workspace_id: int,
        fragment_type: str,
        content: str,
        ttl: Optional[int] = None,
        importance_score: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Create a memory fragment. Returns fragment ID."""
        fragment_id = self._relational.create_fragment(
            workspace_id=workspace_id,
            fragment_type=fragment_type,
            content=content,
            ttl=ttl,
            importance_score=importance_score,
            user_id=user_id,
        )

        # Vector write — failure leaves embedding_id as None (repair job will catch it)
        embedding_id: Optional[str] = None
        try:
            embedding_id = self._vector.add(
                collection=VECTOR_COLLECTION,
                doc_id=str(fragment_id),
                text=content,
                metadata={
                    "fragment_id": str(fragment_id),
                    "user_id": str(workspace_id),
                    "fragment_type": fragment_type,
                    "importance_score": str(importance_score),
                },
            )
        except Exception as e:
            logger.warning(f"Vector write failed for fragment {fragment_id}: {e}")

        # Link embedding_id back to relational record
        if embedding_id is not None:
            self._relational.update_fragment(workspace_id, fragment_id, embedding_id=embedding_id)

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.FRAGMENT_CREATED,
            workspace_id=workspace_id,
            memory_type="fragment",
            memory_id=str(fragment_id),
            data={
                "fragment_type": fragment_type,
                "content_preview": content[:100],
                "ttl": ttl,
                "importance_score": importance_score,
                "embedding_id": embedding_id,
            },
        ))

        logger.debug(f"Created fragment: workspace={workspace_id} id={fragment_id} type={fragment_type}")
        return fragment_id

    def get(self, workspace_id: int, fragment_id: int) -> Optional[Dict]:
        """Get fragment by ID. Returns None if not found or expired (auto-deletes expired)."""
        fragment = self._relational.get_fragment(workspace_id, fragment_id)
        if fragment is None:
            return None

        # TTL expiry check
        expires_at_str = fragment.get("expires_at")
        if expires_at_str:
            try:
                if datetime.now() > datetime.fromisoformat(expires_at_str):
                    self.delete(workspace_id, fragment_id)
                    self._events.emit(MemoryEvent(
                        event_type=MemoryEventType.FRAGMENT_EXPIRED,
                        workspace_id=workspace_id,
                        memory_type="fragment",
                        memory_id=str(fragment_id),
                        data={"reason": "ttl_expired"},
                    ))
                    logger.info(f"Auto-cleaned expired fragment: {fragment_id}")
                    return None
            except (ValueError, TypeError):
                pass

        return fragment

    def update(
        self,
        workspace_id: int,
        fragment_id: int,
        content: Optional[str] = None,
        importance_score: Optional[float] = None,
        ttl: Optional[int] = None,
    ) -> bool:
        """Update fragment fields. Syncs content to VectorStore. Returns False if not found."""
        existing = self._relational.get_fragment(workspace_id, fragment_id)
        if existing is None:
            return False

        updated = self._relational.update_fragment(
            workspace_id, fragment_id, content=content, importance_score=importance_score,
        )
        if not updated:
            return False

        # Sync vector when content changes
        if content is not None:
            embedding_id = existing.get("embedding_id")
            frag_type = existing.get("fragment_type", "info")
            imp = str(importance_score or existing.get("importance_score", 0.5))
            vec_meta = {
                "fragment_id": str(fragment_id),
                "user_id": str(workspace_id),
                "fragment_type": frag_type,
                "importance_score": imp,
            }
            try:
                if embedding_id:
                    self._vector.update(VECTOR_COLLECTION, doc_id=embedding_id, text=content, metadata=vec_meta)
                else:
                    new_eid = self._vector.add(VECTOR_COLLECTION, doc_id=str(fragment_id), text=content, metadata=vec_meta)
                    self._relational.update_fragment(workspace_id, fragment_id, embedding_id=new_eid)
            except Exception as e:
                logger.warning(f"Vector sync failed on update for fragment {fragment_id}: {e}")

        updated_fields = []
        if content is not None: updated_fields.append("content")
        if importance_score is not None: updated_fields.append("importance_score")
        if ttl is not None: updated_fields.append("ttl")

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.FRAGMENT_UPDATED,
            workspace_id=workspace_id,
            memory_type="fragment",
            memory_id=str(fragment_id),
            data={"fields_updated": updated_fields},
        ))

        logger.debug(f"Updated fragment: workspace={workspace_id} id={fragment_id}")
        return True

    def delete(self, workspace_id: int, fragment_id: int) -> bool:
        """Delete fragment from both stores. Returns False if not found."""
        fragment = self._relational.get_fragment(workspace_id, fragment_id)
        embedding_id = fragment.get("embedding_id") if fragment else None

        deleted = self._relational.delete_fragment(workspace_id, fragment_id)
        if not deleted:
            return False

        # Vector cleanup — try both embedding_id and fragment_id as doc_id
        if embedding_id:
            try:
                self._vector.delete(VECTOR_COLLECTION, doc_id=embedding_id)
            except Exception as e:
                logger.warning(f"Vector delete failed for embedding_id={embedding_id}: {e}")
        try:
            self._vector.delete(VECTOR_COLLECTION, doc_id=str(fragment_id))
        except Exception:
            pass

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.FRAGMENT_DELETED,
            workspace_id=workspace_id,
            memory_type="fragment",
            memory_id=str(fragment_id),
            data={"embedding_id": embedding_id},
        ))

        logger.debug(f"Deleted fragment: workspace={workspace_id} id={fragment_id}")
        return True

    def list(
        self,
        workspace_id: int,
        fragment_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        lifecycle_status: str = "active",
    ) -> List[Dict]:
        """List fragments, auto-cleaning expired ones first."""
        self.cleanup_expired(workspace_id)
        return self._relational.list_fragments(
            workspace_id, fragment_type=fragment_type, lifecycle_status=lifecycle_status, limit=limit, offset=offset,
        )

    def cleanup_expired(self, workspace_id: Optional[int] = None) -> int:
        """Remove expired fragments from both stores. Returns count cleaned."""
        now = datetime.now()

        # Gather expired embedding_ids before relational deletion
        if workspace_id is not None:
            fragments = self._relational.list_fragments(workspace_id, lifecycle_status="active", limit=10000)
        else:
            fragments = self._relational.list_fragments(0, lifecycle_status="active", limit=10000)

        expired_eids: List[str] = []
        for f in fragments:
            expires_at_str = f.get("expires_at")
            if expires_at_str:
                try:
                    if now > datetime.fromisoformat(expires_at_str):
                        eid = f.get("embedding_id")
                        if eid:
                            expired_eids.append(eid)
                except (ValueError, TypeError):
                    pass

        # Relational bulk delete
        if workspace_id is not None:
            count = self._relational.delete_expired_fragments(workspace_id)
        else:
            count = self._relational.delete_expired_fragments()

        # Vector cleanup
        for eid in expired_eids:
            try:
                self._vector.delete(VECTOR_COLLECTION, doc_id=eid)
            except Exception:
                pass

        if count > 0:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.FRAGMENT_EXPIRED,
                workspace_id=workspace_id or 0,
                memory_type="fragment",
                memory_id="bulk",
                data={"cleaned_count": count},
            ))
            logger.info(f"Cleaned {count} expired fragments")

        return count

    # ── Semantic Search ──────────────────────────────────────────

    def search_by_semantic(
        self,
        workspace_id: int,
        query: str,
        top_k: int = 5,
        threshold: float = 0.3,
    ) -> List[Dict]:
        """Semantic search via VectorStore, enriched with RelationalStore data."""
        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.SEARCH_TRIGGERED,
            workspace_id=workspace_id,
            memory_type="fragment",
            data={"query": query, "top_k": top_k, "threshold": threshold},
        ))

        vector_results = self._vector.search(
            collection=VECTOR_COLLECTION,
            query_text=query,
            n_results=top_k,
            where={"user_id": str(workspace_id)},
        )

        enriched: List[Dict] = []
        for r in vector_results:
            similarity = r.get("similarity") or 0
            if similarity < threshold:
                continue

            metadata = r.get("metadata", {})
            fragment_id_str = metadata.get("fragment_id")

            if fragment_id_str:
                try:
                    fragment_id = int(fragment_id_str)
                    fragment = self._relational.get_fragment(workspace_id, fragment_id)
                    if fragment is not None:
                        fragment["similarity"] = similarity
                        fragment["vector_document"] = r.get("document", "")
                        enriched.append(fragment)
                        continue
                except (ValueError, TypeError):
                    pass

            # Fallback: vector result only
            enriched.append({
                "content": r.get("document", ""),
                "fragment_type": metadata.get("fragment_type", ""),
                "similarity": similarity,
                "source": "vector_only",
            })

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.SEARCH_COMPLETED,
            workspace_id=workspace_id,
            memory_type="fragment",
            data={"query": query, "results_count": len(enriched)},
        ))

        logger.debug(f"Semantic search: workspace={workspace_id} query='{query}' results={len(enriched)}")
        return enriched

    # ── Vector Consistency Repair ─────────────────────────────────

    def repair_vector_consistency(self, limit: int = 100) -> Dict[str, int]:
        """Scan fragments, re-add missing ones to VectorStore. Returns {scanned, repaired, errors}."""
        scanned = 0
        repaired = 0
        errors = 0

        all_fragments = self._relational.list_fragments(0, lifecycle_status="active", limit=limit * 2)

        for fragment in all_fragments:
            if scanned >= limit:
                break
            scanned += 1

            fragment_id = str(fragment.get("id", ""))
            embedding_id = fragment.get("embedding_id")
            content = fragment.get("content", "")
            fragment_type = fragment.get("fragment_type", "info")
            importance_score = fragment.get("importance_score", 0.5)

            # Check vector existence by embedding_id
            if embedding_id:
                existing = self._vector.get(VECTOR_COLLECTION, doc_id=embedding_id)
                if existing is not None:
                    continue

            # Also check by fragment_id as doc_id
            if self._vector.get(VECTOR_COLLECTION, doc_id=fragment_id) is not None:
                continue

            # Re-add missing vector entry
            workspace_id = fragment.get("workspace_id", 0)
            try:
                new_embedding_id = self._vector.add(
                    VECTOR_COLLECTION,
                    doc_id=fragment_id,
                    text=content,
                    metadata={
                        "fragment_id": fragment_id,
                        "user_id": str(workspace_id),
                        "fragment_type": fragment_type,
                        "importance_score": str(importance_score),
                        "repaired": "1",
                    },
                )
                self._relational.update_fragment(workspace_id, int(fragment_id), embedding_id=new_embedding_id)
                repaired += 1
            except Exception as e:
                logger.error(f"Repair failed for fragment {fragment_id}: {e}")
                errors += 1

        logger.info(f"Consistency repair: scanned={scanned} repaired={repaired} errors={errors}")

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.LIFECYCLE_MAINTENANCE,
            workspace_id=0,
            memory_type="fragment",
            data={"action": "repair_vector_consistency", "scanned": scanned, "repaired": repaired, "errors": errors},
        ))

        return {"scanned": scanned, "repaired": repaired, "errors": errors}

    # ── Extraction Prompt Templates ──────────────────────────────

    def get_prompt_template(self, workspace_id: int, prompt_name: str) -> Optional[Dict[str, Any]]:
        """Get prompt template: custom overrides default, returns None if not found."""
        custom = self._relational.get_extraction_prompt_template(workspace_id, prompt_name)
        if custom is not None:
            return {
                "name": custom.get("name", prompt_name),
                "template": custom.get("content", ""),
                "version": custom.get("version", 1),
                "is_custom": True,
                "is_active": custom.get("is_active", True),
                "created_at": custom.get("created_at"),
                "updated_at": custom.get("updated_at"),
            }

        default = DEFAULT_EXTRACTION_PROMPTS.get(prompt_name)
        if default is not None:
            return {
                "name": prompt_name,
                "template": default["template"],
                "version": default["version"],
                "is_custom": False,
                "is_active": True,
            }

        return None

    def create_prompt_template(
        self,
        workspace_id: int,
        prompt_name: str,
        template: str,
        user_id: Optional[int] = None,
    ) -> int:
        """Create/update a custom prompt template. Returns record ID."""
        record_id = self._relational.set_extraction_prompt_template(
            workspace_id=workspace_id,
            name=prompt_name,
            content=template,
            is_active=True,
            user_id=user_id,
        )

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.EXTRACTION_TRIGGERED,
            workspace_id=workspace_id,
            memory_type="fragment",
            data={"action": "create_prompt_template", "prompt_name": prompt_name, "record_id": record_id},
        ))

        logger.debug(f"Created prompt template: workspace={workspace_id} name={prompt_name}")
        return record_id

    def list_prompt_templates(self, workspace_id: int) -> List[Dict[str, Any]]:
        """List all prompt templates (defaults merged with workspace custom overrides)."""
        # Discover custom overrides for known default names
        custom_map: Dict[str, Dict[str, Any]] = {}
        for name in DEFAULT_EXTRACTION_PROMPTS:
            custom = self._relational.get_extraction_prompt_template(workspace_id, name)
            if custom is not None:
                custom_map[name] = custom

        result: List[Dict[str, Any]] = []

        for name, default in DEFAULT_EXTRACTION_PROMPTS.items():
            if name in custom_map:
                c = custom_map[name]
                result.append({
                    "name": name,
                    "template": c.get("content", default["template"]),
                    "version": c.get("version", default["version"]),
                    "is_custom": True,
                    "is_active": c.get("is_active", True),
                })
            else:
                result.append({
                    "name": name,
                    "template": default["template"],
                    "version": default["version"],
                    "is_custom": False,
                    "is_active": True,
                })

        # Extra custom templates not in defaults
        for name, c in custom_map.items():
            if name not in DEFAULT_EXTRACTION_PROMPTS:
                result.append({
                    "name": name,
                    "template": c.get("content", ""),
                    "version": c.get("version", 1),
                    "is_custom": True,
                    "is_active": c.get("is_active", True),
                })

        return result

    def render_prompt(self, workspace_id: int, prompt_name: str, variables: Dict[str, Any]) -> str:
        """Render a prompt template with variable substitution. Raises ValueError if not found."""
        template_info = self.get_prompt_template(workspace_id, prompt_name)
        if template_info is None:
            raise ValueError(f"Prompt template '{prompt_name}' not found")

        rendered = template_info["template"]
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            if placeholder in rendered:
                rendered = rendered.replace(placeholder, str(value))
        return rendered
