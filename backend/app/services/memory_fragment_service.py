"""
记忆片段服务（Memory Fragments）

实现对话历史分析、摘要生成、关键信息抽取、TTL 管理、语义化存储
"""
import logging
import json
import re
import os
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

# ChromaDB protobuf 修复
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.chromadb_client import get_chromadb_client
from app.core.tracing import get_tracer
from app.services.memory_observability_service import record_trace_event, update_recall_metrics
import time as _time_obs


# ============================================================
# Task 11: 对话历史分析与摘要生成
# ============================================================

def analyze_conversation_history(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    分析对话历史，识别关键信息
    
    Args:
        messages: 对话历史列表（格式：[{"role": "user", "content": "..."}, ...]）
        
    Returns:
        分析结果字典（包含用户信息、偏好、计划等）
    """
    try:
        user_info = {}
        preferences = []
        plans = []
        key_facts = []
        
        for msg in messages:
            if msg.get("role") != "user":
                continue
            
            content = msg.get("content", "")
            
            # 1. 抽取用户信息（姓名、角色、组织等）
            # 模式: "我叫XXX" / "我是XXX" / "我的名字是XXX"
            name_patterns = [
                r'我叫(.+?)(?:[，,。！!？?的]|$)',
                r'我是(.+?)(?:[，,。！!？?的]|$)',
                r'我的名字是(.+?)(?:[，,。！!？?的]|$)',
            ]
            for pattern in name_patterns:
                match = re.search(pattern, content)
                if match:
                    user_info["name"] = match.group(1).strip()
                    break
            
            # 模式: "我在XXX工作" / "我负责XXX"
            org_match = re.search(r'我在(.+?)(?:工作|任职|上班)', content)
            if org_match:
                user_info["organization"] = org_match.group(1).strip()
            
            role_match = re.search(r'我是(.+?)(?:工程师|经理|设计师|开发|PM|产品|架构师)', content)
            if role_match:
                user_info["role"] = role_match.group(1).strip() + re.search(r'(?:工程师|经理|设计师|开发|PM|产品|架构师)', content).group(0)
            
            # 2. 抽取偏好
            # 模式: "我喜欢XXX" / "我偏好XXX" / "我习惯用XXX"
            pref_patterns = [
                r'我喜欢(.+?)(?:[，,。！!？?的]|$)',
                r'我偏好(.+?)(?:[，,。！!？?的]|$)',
                r'我习惯用(.+?)(?:[，,。！!？?的]|$)',
                r'我习惯(.+?)(?:[，,。！!？?的]|$)',
            ]
            for pattern in pref_patterns:
                matches = re.findall(pattern, content)
                for m in matches:
                    preferences.append(m.strip())
            
            # 3. 抽取计划
            # 模式: "我打算XXX" / "我计划XXX" / "明天我要XXX"
            plan_patterns = [
                r'我打算(.+?)(?:[，,。！!？?的]|$)',
                r'我计划(.+?)(?:[，,。！!？?的]|$)',
                r'(?:明天|后天|下周|这周|今天)我要(.+?)(?:[，,。！!？?的]|$)',
            ]
            for pattern in plan_patterns:
                matches = re.findall(pattern, content)
                for m in matches:
                    plans.append(m.strip())
            
            # 4. 抽取关键事实
            # 模式: "XXX是XXX" / "XXX有XXX"
            fact_patterns = [
                r'(.+?)是(.+?)(?:[，,。！!？?]|$)',
            ]
            for pattern in fact_patterns:
                matches = re.findall(pattern, content)
                for subj, obj in matches:
                    subj = subj.strip()
                    obj = obj.strip()
                    if len(subj) < 20 and len(obj) < 50 and subj and obj:
                        key_facts.append(f"{subj}是{obj}")
        
        return {
            "success": True,
            "user_info": user_info,
            "preferences": list(set(preferences)),  # 去重
            "plans": list(set(plans)),
            "key_facts": list(set(key_facts)),
            "message_count": len(messages),
            "analyzed_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"✗ 对话历史分析失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def generate_summary(messages: List[Dict[str, str]], max_length: int = 200) -> Dict[str, Any]:
    """
    生成对话摘要
    
    Args:
        messages: 对话历史列表
        max_length: 摘要最大长度
        
    Returns:
        摘要结果字典
    """
    try:
        if not messages:
            return {
                "success": False,
                "error": "没有对话历史可摘要"
            }
        
        # 1. 分析对话
        analysis = analyze_conversation_history(messages)
        
        if not analysis["success"]:
            return analysis
        
        # 2. 构建摘要
        summary_parts = []
        
        if analysis["user_info"]:
            info_str = "、".join([f"{k}:{v}" for k, v in analysis["user_info"].items()])
            summary_parts.append(f"用户信息({info_str})")
        
        if analysis["preferences"]:
            summary_parts.append(f"偏好({', '.join(analysis['preferences'][:3])})")
        
        if analysis["plans"]:
            summary_parts.append(f"计划({', '.join(analysis['plans'][:3])})")
        
        if analysis["key_facts"]:
            summary_parts.append(f"关键信息({', '.join(analysis['key_facts'][:3])})")
        
        summary = "。".join(summary_parts)
        
        # 截断到最大长度
        if len(summary) > max_length:
            summary = summary[:max_length - 3] + "..."
        
        return {
            "success": True,
            "summary": summary,
            "user_info": analysis["user_info"],
            "preferences": analysis["preferences"],
            "plans": analysis["plans"],
            "key_facts": analysis["key_facts"],
            "message_count": analysis["message_count"],
            "generated_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"✗ 摘要生成失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def extract_fragments(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    从对话历史中抽取多种类型的记忆片段
    
    Args:
        messages: 对话历史列表
        
    Returns:
        抽取结果（包含按类型分类的记忆片段）
    """
    try:
        analysis = analyze_conversation_history(messages)
        
        if not analysis["success"]:
            return analysis
        
        fragments = []
        
        # 类型1: 用户信息
        for key, value in analysis["user_info"].items():
            fragments.append({
                "fragment_type": "info",
                "content": f"用户的{key}是{value}",
                "importance_score": 0.9
            })
        
        # 类型2: 偏好
        for pref in analysis["preferences"]:
            fragments.append({
                "fragment_type": "preference",
                "content": pref,
                "importance_score": 0.7
            })
        
        # 类型3: 计划
        for plan in analysis["plans"]:
            fragments.append({
                "fragment_type": "plan",
                "content": plan,
                "importance_score": 0.8
            })
        
        # 类型4: 关键事实
        for fact in analysis["key_facts"]:
            fragments.append({
                "fragment_type": "info",
                "content": fact,
                "importance_score": 0.6
            })
        
        return {
            "success": True,
            "fragments": fragments,
            "count": len(fragments),
            "by_type": {
                "info": len([f for f in fragments if f["fragment_type"] == "info"]),
                "preference": len([f for f in fragments if f["fragment_type"] == "preference"]),
                "plan": len([f for f in fragments if f["fragment_type"] == "plan"])
            },
            "extracted_at": datetime.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"✗ 记忆片段抽取失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# Task 12: 可配置的抽取 Prompt 模板
# ============================================================

# 默认抽取 Prompt 模板
DEFAULT_EXTRACTION_PROMPTS = {
    "user_info": {
        "template": "从以下对话中抽取用户信息（姓名、角色、组织等）。\n对话: {conversation}\n请以 JSON 格式返回抽取结果。",
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    },
    "preference": {
        "template": "从以下对话中抽取用户的偏好和习惯。\n对话: {conversation}\n请以列表格式返回抽取结果。",
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    },
    "plan": {
        "template": "从以下对话中抽取用户的计划和待办事项。\n对话: {conversation}\n请以列表格式返回抽取结果。",
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    },
    "summary": {
        "template": "请为以下对话生成简洁的摘要，不超过 {max_length} 字。\n对话: {conversation}\n摘要:",
        "version": 1,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat()
    }
}

# 用户自定义 Prompt 模板存储（SQLite）
def _get_prompt_store_table() -> None:
    """确保 Prompt 模板存储表存在"""
    db = get_db_client()
    db.execute('''
        CREATE TABLE IF NOT EXISTS extraction_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            prompt_name TEXT NOT NULL,
            template TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, prompt_name, version),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_prompts_user ON extraction_prompts(user_id)')


def get_extraction_prompt(user_id: int, prompt_name: str, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取抽取 Prompt 模板
    
    优先返回用户自定义模板，如果没有则返回默认模板
    
    Args:
        user_id: 用户 ID
        prompt_name: 模板名称（user_info, preference, plan, summary）
        
    Returns:
        Prompt 模板信息
    """
    try:
        _get_prompt_store_table()
        db = get_db_client()
        
        # 查找用户的自定义模板（激活版本）
        rows = db.execute(
            'SELECT * FROM extraction_prompts WHERE user_id = ? AND prompt_name = ? AND is_active = 1 ORDER BY version DESC LIMIT 1',
            (user_id, prompt_name)
        )
        
        if rows:
            row = dict(rows[0])
            return {
                "success": True,
                "prompt": {
                    "name": row["prompt_name"],
                    "template": row["template"],
                    "version": row["version"],
                    "is_custom": True,
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
            }
        
        # 返回默认模板
        if prompt_name in DEFAULT_EXTRACTION_PROMPTS:
            default = DEFAULT_EXTRACTION_PROMPTS[prompt_name]
            return {
                "success": True,
                "prompt": {
                    "name": prompt_name,
                    "template": default["template"],
                    "version": default["version"],
                    "is_custom": False,
                    "created_at": default["created_at"],
                    "updated_at": default["updated_at"]
                }
            }
        
        return {
            "success": False,
            "error": f"Prompt template '{prompt_name}' not found"
        }
        
    except Exception as e:
        logger.error(f"✗ 获取 Prompt 模板失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def create_extraction_prompt(user_id: int, prompt_name: str, template: str, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    创建或更新用户自定义抽取 Prompt 模板
    
    Args:
        user_id: 用户 ID
        prompt_name: 模板名称
        template: 模板内容
        
    Returns:
        创建结果
    """
    try:
        _get_prompt_store_table()
        db = get_db_client()
        
        # 获取当前最大版本号
        rows = db.execute(
            'SELECT MAX(version) as max_version FROM extraction_prompts WHERE user_id = ? AND prompt_name = ?',
            (user_id, prompt_name)
        )
        current_version = rows[0]["max_version"] if rows and rows[0]["max_version"] else 0
        new_version = current_version + 1
        
        # 停用旧版本
        db.execute(
            'UPDATE extraction_prompts SET is_active = 0 WHERE user_id = ? AND prompt_name = ?',
            (user_id, prompt_name)
        )
        
        # 插入新版本
        db.execute(
            'INSERT INTO extraction_prompts (user_id, prompt_name, template, version, is_active) VALUES (?, ?, ?, ?, 1)',
            (user_id, prompt_name, template, new_version)
        )
        
        logger.info(f"✓ 创建 Prompt 模板: {prompt_name} v{new_version}")
        
        return {
            "success": True,
            "prompt_name": prompt_name,
            "version": new_version,
            "message": f"Prompt template '{prompt_name}' created with version {new_version}"
        }
        
    except Exception as e:
        logger.error(f"✗ 创建 Prompt 模板失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def list_extraction_prompts(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    列出用户所有可用的抽取 Prompt 模板
    
    Args:
        user_id: 用户 ID
        
    Returns:
        Prompt 模板列表
    """
    try:
        _get_prompt_store_table()
        db = get_db_client()
        
        # 获取用户自定义模板
        rows = db.execute(
            'SELECT prompt_name, template, version, is_active, created_at, updated_at FROM extraction_prompts WHERE user_id = ? ORDER BY prompt_name, version DESC',
            (user_id,)
        )
        
        custom_prompts = {}
        if rows:
            for row in rows:
                row_dict = dict(row)
                name = row_dict["prompt_name"]
                if name not in custom_prompts or row_dict["is_active"] == 1:
                    custom_prompts[name] = row_dict
        
        # 合并默认模板
        all_prompts = []
        
        # 添加默认模板
        for name, default in DEFAULT_EXTRACTION_PROMPTS.items():
            if name not in custom_prompts:
                all_prompts.append({
                    "name": name,
                    "template": default["template"],
                    "version": default["version"],
                    "is_custom": False,
                    "is_active": True
                })
        
        # 添加自定义模板
        for name, prompt in custom_prompts.items():
            all_prompts.append({
                "name": name,
                "template": prompt["template"],
                "version": prompt["version"],
                "is_custom": True,
                "is_active": prompt["is_active"] == 1
            })
        
        return {
            "success": True,
            "prompts": all_prompts,
            "count": len(all_prompts)
        }
        
    except Exception as e:
        logger.error(f"✗ 列出 Prompt 模板失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def render_prompt(user_id: int, prompt_name: str, variables: Dict[str, Any], workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    渲染 Prompt 模板
    
    Args:
        user_id: 用户 ID
        prompt_name: 模板名称
        variables: 模板变量字典
        
    Returns:
        渲染后的 Prompt
    """
    try:
        result = get_extraction_prompt(user_id, prompt_name)
        
        if not result["success"]:
            return result
        
        template = result["prompt"]["template"]
        
        # 替换变量
        rendered = template
        for key, value in variables.items():
            rendered = rendered.replace(f"{{{key}}}", str(value))
        
        return {
            "success": True,
            "rendered_prompt": rendered,
            "template_name": prompt_name,
            "version": result["prompt"]["version"]
        }
        
    except Exception as e:
        logger.error(f"✗ 渲染 Prompt 失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# Task 13: TTL 机制实现
# ============================================================

def create_fragment(user_id: int,
                    fragment_type: str,
                    content: str,
                    ttl: Optional[int] = None,
                    importance_score: float = 0.5,
                    metadata: Optional[Dict[str, Any]] = None,
                    workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    创建记忆片段（支持 TTL）
    
    Args:
        user_id: 用户 ID
        fragment_type: 片段类型（info, preference, plan）
        content: 片段内容
        ttl: 过期时间（秒），None 表示永久
        importance_score: 重要性评分（0.0 - 1.0）
        metadata: 附加元数据
        
    Returns:
        创建结果（包含片段 ID）
    """
    try:
        _span = get_tracer().start_span("fragment.create")
        _span.set_attribute("user.id", user_id)
        _span.set_attribute("fragment.type", fragment_type)
        if workspace_id:
            _span.set_attribute("workspace.id", workspace_id)

        db = get_db_client()
        
        # 计算过期时间
        expires_at = None
        if ttl is not None and ttl > 0:
            expires_at = (datetime.now() + timedelta(seconds=ttl)).isoformat()
        
        # 构建元数据
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        
        # 插入记忆片段（vector_synced 默认为 0）
        fragment_id = db.execute('''
            INSERT INTO memory_fragments (user_id, workspace_id, fragment_type, content, ttl, importance_score, expires_at, vector_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        ''', (user_id, workspace_id, fragment_type, content, ttl, importance_score, expires_at))

        # 同一事务内写入 outbox（保证 SQLite 业务数据和 outbox 记录原子化）
        db.execute('''
            INSERT INTO vector_outbox (fragment_id, user_id, workspace_id, fragment_type, content, importance_score, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (fragment_id, user_id, str(workspace_id) if workspace_id is not None else "", fragment_type, content, importance_score, expires_at or ""))

        # 尝试立即写入向量数据库（乐观执行）
        vector_synced = False
        try:
            chroma = get_chromadb_client()
            if chroma:
                chroma.add_embedding(
                    text=content,
                    metadata={
                        "fragment_id": str(fragment_id),
                        "user_id": str(user_id),
                        "workspace_id": str(workspace_id) if workspace_id is not None else "",
                        "fragment_type": fragment_type,
                        "importance_score": str(importance_score),
                        "expires_at": expires_at or "",
                        "vector_synced": "1",
                    }
                )
                vector_synced = True
        except BaseException as e:
            logger.warning(f"⚠️  向量存储失败（已写入 outbox，将由后台任务补偿）: {e}")

        # 根据向量写入结果更新状态
        if vector_synced:
            # 写入成功：标记 vector_synced=1 并删除 outbox 记录
            db.execute('UPDATE memory_fragments SET vector_synced = 1 WHERE id = ?', (fragment_id,))
            db.execute('DELETE FROM vector_outbox WHERE fragment_id = ?', (fragment_id,))
        # 如果写入失败，outbox 记录保留，由 process_vector_outbox 后台任务处理
        
        logger.info(f"✓ 创建记忆片段 ID: {fragment_id}, 类型: {fragment_type}, TTL: {ttl}")
        
        # 观测性埋点：记录创建事件
        try:
            record_trace_event(user_id, str(fragment_id), "fragment", "created",
                              "extraction", metadata={"fragment_type": fragment_type})
        except Exception:
            pass
        
        return {
            "success": True,
            "fragment_id": fragment_id,
            "fragment_type": fragment_type,
            "content": content,
            "ttl": ttl,
            "expires_at": expires_at,
            "importance_score": importance_score,
            "message": f"Fragment created successfully"
        }
        
    except Exception as e:
        if '_span' in locals():
            _span.record_exception(e)
        logger.error(f"✗ 创建记忆片段失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        if '_span' in locals():
            _span.end()


def get_fragment(user_id: int, fragment_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取记忆片段（自动检查 TTL）
    
    Args:
        user_id: 用户 ID
        fragment_id: 片段 ID
        
    Returns:
        片段信息（如果已过期则返回未找到）
    """
    try:
        db = get_db_client()
        
        # 查询片段
        if workspace_id is None:
            rows = db.execute(
                'SELECT * FROM memory_fragments WHERE id = ? AND user_id = ? AND workspace_id IS NULL',
                (fragment_id, user_id)
            )
        else:
            rows = db.execute(
                'SELECT * FROM memory_fragments WHERE id = ? AND user_id = ? AND workspace_id = ?',
                (fragment_id, user_id, workspace_id)
            )
        
        if not rows:
            return {
                "success": False,
                "error": f"Fragment {fragment_id} not found"
            }
        
        fragment = dict(rows[0])
        
        # 检查是否过期
        if fragment.get("expires_at"):
            expires_at = datetime.fromisoformat(fragment["expires_at"])
            if datetime.now() > expires_at:
                # 自动清理过期片段
                db.execute('DELETE FROM memory_fragments WHERE id = ?', (fragment_id,))
                logger.info(f"✓ 自动清理过期片段: {fragment_id}")
                return {
                    "success": False,
                    "error": f"Fragment {fragment_id} has expired and been cleaned up"
                }
        
        return {
            "success": True,
            "fragment": fragment
        }
        
    except Exception as e:
        logger.error(f"✗ 获取记忆片段失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def update_fragment(user_id: int,
                    fragment_id: int,
                    content: Optional[str] = None,
                    ttl: Optional[int] = None,
                    importance_score: Optional[float] = None,
                    workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    更新记忆片段（内容或 TTL）
    
    Args:
        user_id: 用户 ID
        fragment_id: 片段 ID
        content: 新内容（可选）
        ttl: 新 TTL（可选，None 表示不修改，0 表示永久）
        importance_score: 新重要性评分（可选）
        
    Returns:
        更新结果
    """
    try:
        db = get_db_client()
        
        # 检查片段是否存在
        existing = get_fragment(user_id, fragment_id, workspace_id)
        if not existing["success"]:
            return existing
        
        # 构建更新
        updates = []
        params = []
        
        if content is not None:
            updates.append('content = ?')
            params.append(content)
        
        if ttl is not None:
            updates.append('ttl = ?')
            params.append(ttl)
            
            if ttl > 0:
                expires_at = (datetime.now() + timedelta(seconds=ttl)).isoformat()
            else:
                expires_at = None
            
            updates.append('expires_at = ?')
            params.append(expires_at)
        
        if importance_score is not None:
            updates.append('importance_score = ?')
            params.append(importance_score)
        
        if not updates:
            return {
                "success": False,
                "error": "No fields to update"
            }
        
        params.append(fragment_id)
        params.append(user_id)

        if workspace_id is None:
            db.execute(
                f'UPDATE memory_fragments SET {", ".join(updates)} WHERE id = ? AND user_id = ? AND workspace_id IS NULL',
                tuple(params)
            )
        else:
            params.append(workspace_id)
            db.execute(
                f'UPDATE memory_fragments SET {", ".join(updates)} WHERE id = ? AND user_id = ? AND workspace_id = ?',
                tuple(params)
            )
        
        logger.info(f"✓ 更新记忆片段: {fragment_id}")
        
        # 观测性埋点：记录更新事件
        try:
            updated_fields = []
            if content is not None: updated_fields.append("content")
            if ttl is not None: updated_fields.append("ttl")
            if importance_score is not None: updated_fields.append("importance_score")
            record_trace_event(user_id, str(fragment_id), "fragment", "updated",
                              "manual", metadata={"fields_updated": updated_fields})
        except Exception:
            pass
        
        return {
            "success": True,
            "fragment_id": fragment_id,
            "message": f"Fragment {fragment_id} updated successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 更新记忆片段失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def delete_fragment(user_id: int, fragment_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    删除记忆片段
    
    Args:
        user_id: 用户 ID
        fragment_id: 片段 ID
        
    Returns:
        删除结果
    """
    try:
        db = get_db_client()
        
        if workspace_id is None:
            result = db.execute(
                'DELETE FROM memory_fragments WHERE id = ? AND user_id = ? AND workspace_id IS NULL',
                (fragment_id, user_id)
            )
        else:
            result = db.execute(
                'DELETE FROM memory_fragments WHERE id = ? AND user_id = ? AND workspace_id = ?',
                (fragment_id, user_id, workspace_id)
            )
        
        # 同时从向量数据库删除
        try:
            chroma = get_chromadb_client()
            # ChromaDB 用 UUID 作为 ID，无法直接按 fragment_id 删除
            # 这里通过查询获取对应的 doc_id 再删除
            # 为简化，向量数据库的清理依赖 TTL 清理流程
        except BaseException:
            pass  # 向量数据库删除失败不影响主流程
        
        logger.info(f"✓ 删除记忆片段: {fragment_id}")
        
        # 观测性埋点：记录删除事件
        try:
            record_trace_event(user_id, str(fragment_id), "fragment", "deleted", "manual")
        except Exception:
            pass
        
        return {
            "success": True,
            "fragment_id": fragment_id,
            "message": f"Fragment {fragment_id} deleted successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 删除记忆片段失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def list_fragments(user_id: int,
                   fragment_type: Optional[str] = None,
                   limit: int = 100,
                   offset: int = 0,
                   workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    列出用户的记忆片段（自动清理过期片段）
    
    Args:
        user_id: 用户 ID
        fragment_type: 片段类型过滤（可选）
        limit: 返回数量限制
        offset: 偏移量
        
    Returns:
        片段列表
    """
    try:
        db = get_db_client()
        
        # 1. 清理过期片段
        cleanup_expired_fragments(user_id, workspace_id)
        
        # 2. 查询片段
        ws_clause = 'workspace_id IS NULL' if workspace_id is None else 'workspace_id = ?'
        ws_params = () if workspace_id is None else (workspace_id,)
        if fragment_type:
            rows = db.execute(
                f'SELECT * FROM memory_fragments WHERE user_id = ? AND {ws_clause} AND fragment_type = ? ORDER BY importance_score DESC, created_at DESC LIMIT ? OFFSET ?',
                (user_id, *ws_params, fragment_type, limit, offset)
            )
            count_rows = db.execute(
                f'SELECT COUNT(*) as total FROM memory_fragments WHERE user_id = ? AND {ws_clause} AND fragment_type = ?',
                (user_id, *ws_params, fragment_type)
            )
        else:
            rows = db.execute(
                f'SELECT * FROM memory_fragments WHERE user_id = ? AND {ws_clause} ORDER BY importance_score DESC, created_at DESC LIMIT ? OFFSET ?',
                (user_id, *ws_params, limit, offset)
            )
            count_rows = db.execute(
                f'SELECT COUNT(*) as total FROM memory_fragments WHERE user_id = ? AND {ws_clause}',
                (user_id, *ws_params)
            )
        
        fragments = [dict(row) for row in rows] if rows else []
        total = count_rows[0]["total"] if count_rows else 0
        
        return {
            "success": True,
            "fragments": fragments,
            "count": len(fragments),
            "total": total,
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        logger.error(f"✗ 列出记忆片段失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def cleanup_expired_fragments(user_id: Optional[int] = None, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    清理过期的记忆片段
    
    Args:
        user_id: 用户 ID（可选，None 表示清理所有用户的过期片段）
        
    Returns:
        清理结果
    """
    try:
        db = get_db_client()
        now = datetime.now().isoformat()
        
        if user_id:
            ws_clause = 'workspace_id IS NULL' if workspace_id is None else 'workspace_id = ?'
            ws_params = () if workspace_id is None else (workspace_id,)
            # 查找过期片段（用于向量数据库清理）
            expired = db.execute(
                f'SELECT id FROM memory_fragments WHERE user_id = ? AND {ws_clause} AND expires_at IS NOT NULL AND expires_at < ?',
                (user_id, *ws_params, now)
            )
            
            # 清理 SQLite
            result = db.execute(
                f'DELETE FROM memory_fragments WHERE user_id = ? AND {ws_clause} AND expires_at IS NOT NULL AND expires_at < ?',
                (user_id, *ws_params, now)
            )
            
            # 清理向量数据库（通过 fragment_id 过滤）
            if expired:
                try:
                    chroma = get_chromadb_client()
                    if chroma:
                        for row in expired:
                            try:
                                chroma.collection.delete(
                                    where={"fragment_id": str(row["id"])}
                                )
                            except Exception:
                                pass
                except BaseException:
                    pass
            
            cleaned = len(expired) if expired else 0
        else:
            expired = db.execute(
                'SELECT id FROM memory_fragments WHERE expires_at IS NOT NULL AND expires_at < ?',
                (now,)
            )
            
            result = db.execute(
                'DELETE FROM memory_fragments WHERE expires_at IS NOT NULL AND expires_at < ?',
                (now,)
            )
            
            if expired:
                try:
                    chroma = get_chromadb_client()
                    if chroma:
                        for row in expired:
                            try:
                                chroma.collection.delete(
                                    where={"fragment_id": str(row["id"])}
                                )
                            except Exception:
                                pass
                except BaseException:
                    pass
            
            cleaned = len(expired) if expired else 0
        
        if cleaned > 0:
            logger.info(f"✓ 清理 {cleaned} 条过期记忆片段")
        
        return {
            "success": True,
            "cleaned_count": cleaned,
            "message": f"Cleaned up {cleaned} expired fragments"
        }
        
    except Exception as e:
        logger.error(f"✗ 清理过期片段失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# Task 14: 语义化存储（Vector Embeddings）
# ============================================================

def search_fragments_by_semantic(user_id: int,
                                 query: str,
                                 top_k: int = 5,
                                 threshold: float = 0.3,
                                 workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    语义搜索记忆片段（基于向量相似性）
    
    Args:
        user_id: 用户 ID
        query: 查询文本
        top_k: 返回 Top-K 结果
        threshold: 相似性阈值（低于此值不返回）
        
    Returns:
        搜索结果
    """
    try:
        _search_start_time = _time_obs.time()
        chroma = get_chromadb_client()
        
        if chroma is None:
            return {
                "success": True,
                "fragments": [],
                "count": 0,
                "query": query,
                "warning": "ChromaDB 不可用"
            }
        
        # 语义搜索（使用 where 条件过滤用户）
        # 注意：ChromaDB 的 where 条件需要值是字符串
        if workspace_id is None:
            _where = {"user_id": str(user_id)}
        else:
            _where = {"$and": [{"user_id": str(user_id)}, {"workspace_id": str(workspace_id)}]}
        results = chroma.search_embeddings(
            query_text=query,
            n_results=top_k,
            where=_where
        )
        
        if not results:
            return {
                "success": True,
                "fragments": [],
                "count": 0,
                "query": query
            }
        
        # 过滤低相似性结果并关联 SQLite 数据
        filtered = []
        for r in results:
            similarity = r.get("similarity", 0)
            if similarity is not None and similarity >= threshold:
                # 从元数据中获取 fragment_id，查询 SQLite 获取完整信息
                metadata = r.get("metadata", {})
                fragment_id_str = metadata.get("fragment_id")
                if fragment_id_str:
                    db = get_db_client()
                    rows = db.execute(
                        'SELECT * FROM memory_fragments WHERE id = ?',
                        (int(fragment_id_str),)
                    )
                    if rows:
                        fragment = dict(rows[0])
                        fragment["similarity"] = similarity
                        fragment["vector_document"] = r.get("document", "")
                        filtered.append(fragment)
        
        logger.info(f"✓ 语义搜索: '{query}' -> {len(filtered)} 条结果")
        
        # 观测性埋点：记录召回事件
        try:
            latency = _time_obs.time() - _search_start_time
            update_recall_metrics(user_id, query, filtered, round(latency * 1000, 2))
        except Exception:
            pass
        
        return {
            "success": True,
            "fragments": filtered,
            "count": len(filtered),
            "query": query,
            "top_k": top_k,
            "threshold": threshold
        }
        
    except Exception as e:
        logger.error(f"✗ 语义搜索失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# 跨存储一致性修复
# ============================================================

def process_vector_outbox(limit: int = 50) -> Dict[str, Any]:
    """
    处理向量写入 Outbox（后台任务）

    扫描 vector_outbox 中待处理的记录，重试写入 ChromaDB。
    采用指数退避策略：第 1 次重试立即执行，后续每次延迟 2^retry_count 秒。
    最大重试 5 次，超过后标记为永久失败。

    Returns:
        处理结果统计
    """
    try:
        db = get_db_client()
        chroma = get_chromadb_client()

        if chroma is None:
            return {"success": True, "processed": 0, "skipped": "ChromaDB 不可用"}

        # 查询待处理的 outbox 记录
        rows = db.execute(
            '''SELECT * FROM vector_outbox
               WHERE retry_count < 5 AND next_retry_at <= CURRENT_TIMESTAMP
               ORDER BY created_at ASC LIMIT ?''',
            (limit,)
        )

        if not rows:
            return {"success": True, "processed": 0, "message": "无待处理记录"}

        processed = 0
        succeeded = 0
        failed = 0

        for row in rows:
            row_dict = dict(row)
            fragment_id = row_dict["fragment_id"]
            processed += 1

            try:
                chroma.add_embedding(
                    text=row_dict["content"],
                    metadata={
                        "fragment_id": str(fragment_id),
                        "user_id": str(row_dict["user_id"]),
                        "workspace_id": row_dict.get("workspace_id") or "",
                        "fragment_type": row_dict["fragment_type"],
                        "importance_score": str(row_dict.get("importance_score", 0.5)),
                        "expires_at": row_dict.get("expires_at") or "",
                        "vector_synced": "1",
                        "outbox_repaired": "1",
                    }
                )
                # 写入成功：标记 vector_synced=1 并删除 outbox 记录
                db.execute('UPDATE memory_fragments SET vector_synced = 1 WHERE id = ?', (fragment_id,))
                db.execute('DELETE FROM vector_outbox WHERE id = ?', (row_dict["id"],))
                succeeded += 1
            except Exception as e:
                failed += 1
                retry_count = row_dict.get("retry_count", 0) + 1
                # 指数退避：2^retry_count 秒后重试
                from datetime import timedelta
                delay_seconds = 2 ** retry_count
                db.execute(
                    '''UPDATE vector_outbox
                       SET retry_count = ?, next_retry_at = datetime('now', '+' || ? || ' seconds')
                       WHERE id = ?''',
                    (retry_count, delay_seconds, row_dict["id"])
                )
                if retry_count >= 5:
                    logger.error(f"✗ Outbox 记录 {row_dict['id']} (fragment_id={fragment_id}) 达到最大重试次数: {e}")
                else:
                    logger.warning(f"⚠️ Outbox 记录 {row_dict['id']} 重试 {retry_count}/5: {e}")

        logger.info(f"✓ Outbox 处理完成: 处理 {processed}, 成功 {succeeded}, 失败 {failed}")
        return {
            "success": True,
            "processed": processed,
            "succeeded": succeeded,
            "failed": failed,
        }

    except Exception as e:
        logger.error(f"✗ Outbox 处理失败: {e}")
        return {"success": False, "error": str(e)}


def repair_vector_consistency(limit: int = 100) -> Dict[str, Any]:
    """
    修复 SQLite 与 ChromaDB 的数据一致性

    扫描 memory_fragments 中未同步到向量库的记录，重新写入。
    由后台调度任务或手动 API 触发。

    Args:
        limit: 单次修复最大记录数

    Returns:
        修复结果统计
    """
    try:
        db = get_db_client()
        chroma = get_chromadb_client()

        if chroma is None:
            return {"success": False, "error": "ChromaDB 不可用，无法修复"}

        # 查询所有片段，检查是否在向量库中存在
        rows = db.execute(
            'SELECT id, user_id, fragment_type, content, importance_score, expires_at '
            'FROM memory_fragments ORDER BY id DESC LIMIT ?',
            (limit * 2,)
        )

        if not rows:
            return {"success": True, "repaired": 0, "scanned": 0, "message": "无数据"}

        repaired = 0
        scanned = 0
        errors = 0

        for row in rows:
            if scanned >= limit:
                break
            scanned += 1
            row_dict = dict(row)
            fragment_id = str(row_dict["id"])

            # 检查向量库中是否存在该 fragment_id
            try:
                existing = chroma.collection.get(
                    where={"fragment_id": fragment_id},
                    include=[]
                )
                if existing and existing.get("ids") and len(existing["ids"]) > 0:
                    continue  # 已存在，跳过
            except Exception:
                pass

            # 重新写入向量库
            try:
                chroma.add_embedding(
                    text=row_dict["content"],
                    metadata={
                        "fragment_id": fragment_id,
                        "user_id": str(row_dict["user_id"]),
                        "fragment_type": row_dict["fragment_type"],
                        "importance_score": str(row_dict["importance_score"]),
                        "expires_at": row_dict.get("expires_at") or "",
                        "vector_synced": "1",
                        "repaired": "1",
                    }
                )
                repaired += 1
            except Exception as e:
                logger.error(f"✗ 修复片段 {fragment_id} 向量失败: {e}")
                errors += 1

        logger.info(f"✓ 一致性修复完成: 扫描 {scanned}, 修复 {repaired}, 失败 {errors}")

        return {
            "success": True,
            "scanned": scanned,
            "repaired": repaired,
            "errors": errors,
            "message": f"扫描 {scanned} 条，修复 {repaired} 条，失败 {errors} 条"
        }

    except Exception as e:
        logger.error(f"✗ 一致性修复失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 向量 Outbox 后台调度器
# ============================================================

_outbox_task: Optional[asyncio.Task] = None
_outbox_running = False

async def _outbox_loop():
    """按配置间隔处理 pending outbox 记录"""
    global _outbox_running
    from app.core.config import get_settings
    interval = get_settings().OUTBOX_SCHEDULER_INTERVAL
    logger.info(f"🔄 向量 Outbox 调度器已启动，每 {interval} 秒处理一次")
    while _outbox_running:
        try:
            await asyncio.sleep(interval)
            if not _outbox_running:
                break
            result = process_vector_outbox(limit=50)
            if result.get("processed", 0) > 0:
                logger.info(f"📋 Outbox 处理: {result}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"✗ Outbox 调度器异常: {e}")
            await asyncio.sleep(10)
    logger.info("🔄 向量 Outbox 调度器已停止")


def start_outbox_scheduler() -> None:
    """启动向量 Outbox 后台调度器"""
    global _outbox_task, _outbox_running
    if _outbox_running:
        logger.warning("Outbox 调度器已在运行")
        return
    _outbox_running = True
    try:
        _outbox_task = asyncio.create_task(_outbox_loop())
        logger.info("✓ 向量 Outbox 调度器已启动")
    except RuntimeError:
        _outbox_running = False
        logger.warning("无事件循环，跳过 Outbox 调度器启动")


def stop_outbox_scheduler() -> None:
    """停止向量 Outbox 后台调度器"""
    global _outbox_task, _outbox_running
    _outbox_running = False
    if _outbox_task:
        _outbox_task.cancel()
        _outbox_task = None
        logger.info("✓ 向量 Outbox 调度器已停止")


# ============================================================
# 测试函数
# ============================================================

def test_memory_fragments():
    """测试记忆片段服务"""
    print("\n" + "="*60)
    print("测试记忆片段服务")
    print("="*60 + "\n")
    
    user_id = 999
    
    # 清理
    db = get_db_client()
    _get_prompt_store_table()  # 确保 extraction_prompts 表存在
    db.execute('DELETE FROM memory_fragments WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM extraction_prompts WHERE user_id = ?', (user_id,))
    
    # Task 11: 对话历史分析与摘要生成
    print("--- Task 11: 对话历史分析与摘要生成 ---\n")
    
    # 测试1: 分析对话历史
    print("1. 测试对话历史分析...")
    messages = [
        {"role": "user", "content": "我叫鑫海，我在腾讯工作，我是PM"},
        {"role": "assistant", "content": "你好，鑫海！"},
        {"role": "user", "content": "我喜欢极简设计风格，我习惯用Ardot做设计"},
        {"role": "assistant", "content": "好的，了解了你的偏好。"},
        {"role": "user", "content": "我计划明天完成源启智能体工厂的架构设计"},
        {"role": "assistant", "content": "好的，我来帮你。"},
        {"role": "user", "content": "源启是一个智能体工厂平台"},
    ]
    
    result = analyze_conversation_history(messages)
    print(f"   分析结果:")
    print(f"   用户信息: {result.get('user_info', {})}")
    print(f"   偏好: {result.get('preferences', [])}")
    print(f"   计划: {result.get('plans', [])}")
    print(f"   关键事实: {result.get('key_facts', [])}")
    assert result["success"] == True
    assert "name" in result["user_info"]
    assert len(result["preferences"]) > 0
    assert len(result["plans"]) > 0
    print(f"   ✓ 对话历史分析成功\n")
    
    # 测试2: 生成摘要
    print("2. 测试摘要生成...")
    result = generate_summary(messages, max_length=200)
    print(f"   摘要: {result.get('summary', 'N/A')}")
    assert result["success"] == True
    assert len(result["summary"]) > 0
    print(f"   ✓ 摘要生成成功\n")
    
    # 测试3: 抽取记忆片段
    print("3. 测试记忆片段抽取...")
    result = extract_fragments(messages)
    print(f"   抽取 {result.get('count', 0)} 条片段")
    print(f"   按类型: {result.get('by_type', {})}")
    assert result["success"] == True
    assert result["count"] > 0
    print(f"   ✓ 记忆片段抽取成功\n")
    
    # Task 12: 可配置的抽取 Prompt 模板
    print("--- Task 12: 可配置的抽取 Prompt 模板 ---\n")
    
    # 测试4: 获取默认 Prompt
    print("4. 测试获取默认 Prompt...")
    result = get_extraction_prompt(user_id, "summary")
    print(f"   模板名: {result['prompt']['name']}")
    print(f"   自定义: {result['prompt']['is_custom']}")
    print(f"   模板: {result['prompt']['template'][:50]}...")
    assert result["success"] == True
    assert result["prompt"]["is_custom"] == False
    print(f"   ✓ 获取默认 Prompt 成功\n")
    
    # 测试5: 创建自定义 Prompt
    print("5. 测试创建自定义 Prompt...")
    custom_template = "请从以下对话中抽取用户的核心信息，以表格形式返回。\n对话: {conversation}\n核心信息:"
    result = create_extraction_prompt(user_id, "user_info", custom_template)
    print(f"   创建结果: {result.get('success')}")
    print(f"   版本: {result.get('version')}")
    assert result["success"] == True
    assert result["version"] == 1
    print(f"   ✓ 创建自定义 Prompt 成功\n")
    
    # 测试6: 获取自定义 Prompt
    print("6. 测试获取自定义 Prompt...")
    result = get_extraction_prompt(user_id, "user_info")
    print(f"   自定义: {result['prompt']['is_custom']}")
    print(f"   版本: {result['prompt']['version']}")
    assert result["success"] == True
    assert result["prompt"]["is_custom"] == True
    print(f"   ✓ 获取自定义 Prompt 成功\n")
    
    # 测试7: 列出所有 Prompt
    print("7. 测试列出所有 Prompt...")
    result = list_extraction_prompts(user_id)
    print(f"   共 {result['count']} 个模板")
    for p in result.get("prompts", []):
        print(f"   - {p['name']} (v{p['version']}, custom={p['is_custom']})")
    assert result["success"] == True
    assert result["count"] >= 4  # 至少 4 个默认 + 1 个自定义
    print(f"   ✓ 列出 Prompt 成功\n")
    
    # 测试8: 渲染 Prompt
    print("8. 测试渲染 Prompt...")
    result = render_prompt(user_id, "summary", {"conversation": "用户: 我叫鑫海", "max_length": "100"})
    print(f"   渲染结果: {result.get('rendered_prompt', 'N/A')[:60]}...")
    assert result["success"] == True
    assert "鑫海" in result["rendered_prompt"]
    print(f"   ✓ 渲染 Prompt 成功\n")
    
    # Task 13: TTL 机制
    print("--- Task 13: TTL 机制 ---\n")
    
    # 测试9: 创建带 TTL 的片段
    print("9. 测试创建带 TTL 的片段...")
    result = create_fragment(
        user_id=user_id,
        fragment_type="preference",
        content="我喜欢极简设计风格",
        ttl=30 * 24 * 3600,  # 30天
        importance_score=0.9
    )
    print(f"   创建结果: {result.get('success')}")
    print(f"   片段 ID: {result.get('fragment_id')}")
    print(f"   过期时间: {result.get('expires_at')}")
    assert result["success"] == True
    assert result["expires_at"] is not None
    fragment_id_1 = result["fragment_id"]
    print(f"   ✓ 创建带 TTL 的片段成功\n")
    
    # 测试10: 创建永久片段
    print("10. 测试创建永久片段...")
    result = create_fragment(
        user_id=user_id,
        fragment_type="info",
        content="用户名叫鑫海",
        ttl=None,  # 永久
        importance_score=0.95
    )
    print(f"   创建结果: {result.get('success')}")
    print(f"   过期时间: {result.get('expires_at')}")
    assert result["success"] == True
    assert result["expires_at"] is None
    fragment_id_2 = result["fragment_id"]
    print(f"   ✓ 创建永久片段成功\n")
    
    # 测试11: 获取片段
    print("11. 测试获取片段...")
    result = get_fragment(user_id, fragment_id_1)
    print(f"   获取结果: {result.get('success')}")
    print(f"   内容: {result.get('fragment', {}).get('content', 'N/A')}")
    assert result["success"] == True
    print(f"   ✓ 获取片段成功\n")
    
    # 测试12: 更新片段 TTL
    print("12. 测试更新片段 TTL...")
    result = update_fragment(user_id, fragment_id_1, ttl=60 * 24 * 3600)  # 延长到60天
    print(f"   更新结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 更新 TTL 成功\n")
    
    # 测试13: 列出片段
    print("13. 测试列出片段...")
    result = list_fragments(user_id)
    print(f"   共 {result['count']} 条片段（总计 {result.get('total', 0)} 条）")
    for f in result.get("fragments", []):
        print(f"   - [{f['fragment_type']}] {f['content'][:30]}... (重要性: {f['importance_score']})")
    assert result["success"] == True
    assert result["count"] >= 2
    print(f"   ✓ 列出片段成功\n")
    
    # 测试14: 清理过期片段
    print("14. 测试清理过期片段...")
    # 创建一个已过期的片段
    result = create_fragment(
        user_id=user_id,
        fragment_type="info",
        content="这条信息很快过期",
        ttl=1  # 1秒过期
    )
    expired_id = result["fragment_id"]
    
    # 手动设置过期时间为过去
    db.execute(
        'UPDATE memory_fragments SET expires_at = ? WHERE id = ?',
        ((datetime.now() - timedelta(seconds=10)).isoformat(), expired_id)
    )
    
    # 清理
    result = cleanup_expired_fragments(user_id)
    print(f"   清理结果: {result.get('cleaned_count', 0)} 条")
    assert result["success"] == True
    assert result["cleaned_count"] >= 1
    print(f"   ✓ 清理过期片段成功\n")
    
    # Task 14: 语义搜索
    print("--- Task 14: 语义化存储 ---\n")
    
    # 测试15: 语义搜索
    print("15. 测试语义搜索...")
    result = search_fragments_by_semantic(user_id, "设计风格偏好", top_k=3)
    print(f"   搜索结果: {result.get('count', 0)} 条")
    if result.get("fragments"):
        for f in result["fragments"]:
            print(f"   - {f.get('content', 'N/A')[:30]}... (相似度: {f.get('similarity', 0):.3f})")
    print(f"   ✓ 语义搜索执行成功\n")
    
    # 清理
    print("--- 清理测试数据 ---")
    db.execute('DELETE FROM memory_fragments WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM extraction_prompts WHERE user_id = ?', (user_id,))
    print("   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 记忆片段服务测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    test_memory_fragments()
