"""
插件化架构服务

支持第三方记忆存储后端插件，提供注册、发现、配置管理
"""
import logging
import json
import importlib
import importlib.util
import os
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client


# ============================================================
# 插件接口定义
# ============================================================

class MemoryStoragePlugin(ABC):
    """记忆存储插件抽象接口"""

    @abstractmethod
    def initialize(self, config: Dict[str, Any]) -> bool:
        """初始化插件"""
        pass

    @abstractmethod
    def store(self, key: str, value: Any, metadata: Optional[Dict] = None) -> bool:
        """存储数据"""
        pass

    @abstractmethod
    def retrieve(self, key: str) -> Optional[Any]:
        """读取数据"""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """删除数据"""
        pass

    @abstractmethod
    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索数据"""
        pass

    @abstractmethod
    def get_info(self) -> Dict[str, Any]:
        """获取插件信息"""
        pass


# ============================================================
# 内置示例插件
# ============================================================

class FileStoragePlugin(MemoryStoragePlugin):
    """文件系统存储插件（示例）"""

    def initialize(self, config: Dict[str, Any]) -> bool:
        self.base_dir = config.get("base_dir", "/tmp/agent_memory_plugins")
        os.makedirs(self.base_dir, exist_ok=True)
        return True

    def store(self, key: str, value: Any, metadata: Optional[Dict] = None) -> bool:
        try:
            filepath = os.path.join(self.base_dir, f"{key}.json")
            data = {"value": value, "metadata": metadata or {}}
            with open(filepath, "w") as f:
                json.dump(data, f, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"✗ 文件存储失败: {e}")
            return False

    def retrieve(self, key: str) -> Optional[Any]:
        try:
            filepath = os.path.join(self.base_dir, f"{key}.json")
            if not os.path.exists(filepath):
                return None
            with open(filepath, "r") as f:
                data = json.load(f)
            return data.get("value")
        except Exception:
            return None

    def delete(self, key: str) -> bool:
        try:
            filepath = os.path.join(self.base_dir, f"{key}.json")
            if os.path.exists(filepath):
                os.remove(filepath)
            return True
        except Exception:
            return False

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        results = []
        try:
            for filename in os.listdir(self.base_dir):
                if not filename.endswith(".json"):
                    continue
                filepath = os.path.join(self.base_dir, filename)
                with open(filepath, "r") as f:
                    data = json.load(f)
                content = json.dumps(data.get("value", ""), ensure_ascii=False)
                if query.lower() in content.lower():
                    results.append({
                        "key": filename.replace(".json", ""),
                        "value": data.get("value"),
                        "metadata": data.get("metadata", {})
                    })
                    if len(results) >= limit:
                        break
        except Exception:
            pass
        return results

    def get_info(self) -> Dict[str, Any]:
        file_count = 0
        try:
            file_count = len([f for f in os.listdir(self.base_dir) if f.endswith(".json")])
        except Exception:
            pass
        return {
            "name": "file-storage",
            "type": "file",
            "base_dir": self.base_dir,
            "file_count": file_count
        }


class InMemoryStoragePlugin(MemoryStoragePlugin):
    """内存存储插件（示例/测试用）"""

    _store: Dict[str, Any] = {}

    def initialize(self, config: Dict[str, Any]) -> bool:
        self._store.clear()
        return True

    def store(self, key: str, value: Any, metadata: Optional[Dict] = None) -> bool:
        self._store[key] = {"value": value, "metadata": metadata or {}}
        return True

    def retrieve(self, key: str) -> Optional[Any]:
        data = self._store.get(key)
        return data.get("value") if data else None

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
        return True

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        results = []
        for key, data in self._store.items():
            content = json.dumps(data.get("value", ""), ensure_ascii=False)
            if query.lower() in content.lower():
                results.append({"key": key, "value": data["value"], "metadata": data.get("metadata", {})})
                if len(results) >= limit:
                    break
        return results

    def get_info(self) -> Dict[str, Any]:
        return {
            "name": "in-memory",
            "type": "memory",
            "item_count": len(self._store)
        }


# ============================================================
# 插件注册表
# ============================================================

# 内置插件注册
BUILTIN_PLUGINS = {
    "file-storage": {"class": FileStoragePlugin, "type": "file"},
    "in-memory": {"class": InMemoryStoragePlugin, "type": "memory"},
}

# 运行时插件实例
_plugin_instances: Dict[str, MemoryStoragePlugin] = {}


def _ensure_plugin_tables():
    """确保插件表存在"""
    db = get_db_client()
    db.execute('''
        CREATE TABLE IF NOT EXISTS plugins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plugin_name TEXT NOT NULL,
            plugin_type TEXT NOT NULL,
            config TEXT,
            is_enabled INTEGER DEFAULT 1,
            is_builtin INTEGER DEFAULT 1,
            module_path TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, plugin_name),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS plugin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plugin_name TEXT,
            action TEXT,
            detail TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_plugins_user ON plugins(user_id)')


def register_plugin(user_id: int,
                    plugin_name: str,
                    plugin_type: str,
                    config: Optional[Dict[str, Any]] = None,
                    module_path: Optional[str] = None,
                    enabled: bool = True) -> Dict[str, Any]:
    """
    注册插件

    Args:
        user_id: 用户 ID
        plugin_name: 插件名称
        plugin_type: 插件类型（file, memory, custom）
        config: 插件配置
        module_path: 自定义插件的模块路径
        enabled: 是否启用

    Returns:
        注册结果
    """
    try:
        _ensure_plugin_tables()
        db = get_db_client()

        is_builtin = 1 if plugin_name in BUILTIN_PLUGINS else 0
        config_str = json.dumps(config or {}, ensure_ascii=False)

        existing = db.execute(
            'SELECT id FROM plugins WHERE user_id = ? AND plugin_name = ?',
            (user_id, plugin_name)
        )

        if existing:
            db.execute(
                'UPDATE plugins SET plugin_type = ?, config = ?, is_enabled = ?, module_path = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND plugin_name = ?',
                (plugin_type, config_str, 1 if enabled else 0, module_path, user_id, plugin_name)
            )
        else:
            db.execute(
                'INSERT INTO plugins (user_id, plugin_name, plugin_type, config, is_enabled, is_builtin, module_path) VALUES (?, ?, ?, ?, ?, ?, ?)',
                (user_id, plugin_name, plugin_type, config_str, 1 if enabled else 0, is_builtin, module_path)
            )

        # 初始化插件实例
        _init_plugin_instance(user_id, plugin_name, plugin_type, config or {}, module_path)

        # 记录日志
        db.execute(
            'INSERT INTO plugin_logs (user_id, plugin_name, action, detail) VALUES (?, ?, ?, ?)',
            (user_id, plugin_name, "register", f"Plugin registered with type={plugin_type}")
        )

        logger.info(f"✓ 注册插件: {plugin_name} ({plugin_type})")

        return {
            "success": True,
            "plugin_name": plugin_name,
            "plugin_type": plugin_type,
            "is_enabled": enabled,
            "message": f"Plugin '{plugin_name}' registered successfully"
        }

    except Exception as e:
        logger.error(f"✗ 注册插件失败: {e}")
        return {"success": False, "error": str(e)}


def _init_plugin_instance(user_id: int, plugin_name: str, plugin_type: str,
                          config: Dict[str, Any], module_path: Optional[str] = None):
    """初始化插件实例"""
    instance_key = f"{user_id}:{plugin_name}"

    # 尝试内置插件
    if plugin_name in BUILTIN_PLUGINS:
        plugin_class = BUILTIN_PLUGINS[plugin_name]["class"]
        instance = plugin_class()
        instance.initialize(config)
        _plugin_instances[instance_key] = instance
        return instance

    # 尝试加载自定义插件
    if module_path:
        try:
            spec = importlib.util.spec_from_file_location(plugin_name, module_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                # 查找插件类
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and issubclass(attr, MemoryStoragePlugin) and attr != MemoryStoragePlugin:
                        instance = attr()
                        instance.initialize(config)
                        _plugin_instances[instance_key] = instance
                        return instance
        except Exception as e:
            logger.error(f"✗ 加载自定义插件失败: {e}")

    return None


def discover_plugins() -> Dict[str, Any]:
    """
    发现可用插件

    Returns:
        可用插件列表
    """
    builtin = []
    for name, info in BUILTIN_PLUGINS.items():
        builtin.append({
            "name": name,
            "type": info["type"],
            "is_builtin": True,
            "description": f"Built-in {name} storage plugin"
        })

    return {
        "success": True,
        "builtin_plugins": builtin,
        "custom_plugin_support": True,
        "custom_plugin_instructions": "Create a Python file with a class inheriting from MemoryStoragePlugin, then register with module_path"
    }


def list_plugins(user_id: int) -> Dict[str, Any]:
    """列出用户的所有插件"""
    try:
        _ensure_plugin_tables()
        db = get_db_client()

        rows = db.execute(
            'SELECT * FROM plugins WHERE user_id = ? ORDER BY is_enabled DESC, updated_at DESC',
            (user_id,)
        )

        plugins = []
        if rows:
            for row in rows:
                r = dict(row)
                plugins.append({
                    "name": r["plugin_name"],
                    "type": r["plugin_type"],
                    "is_enabled": bool(r["is_enabled"]),
                    "is_builtin": bool(r["is_builtin"]),
                    "config": json.loads(r["config"]) if r["config"] else {},
                    "module_path": r["module_path"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"]
                })

        return {
            "success": True,
            "plugins": plugins,
            "count": len(plugins)
        }

    except Exception as e:
        logger.error(f"✗ 列出插件失败: {e}")
        return {"success": False, "error": str(e)}


def enable_plugin(user_id: int, plugin_name: str) -> Dict[str, Any]:
    """启用插件"""
    try:
        _ensure_plugin_tables()
        db = get_db_client()

        db.execute(
            'UPDATE plugins SET is_enabled = 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND plugin_name = ?',
            (user_id, plugin_name)
        )

        return {"success": True, "message": f"Plugin '{plugin_name}' enabled"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def disable_plugin(user_id: int, plugin_name: str) -> Dict[str, Any]:
    """禁用插件"""
    try:
        _ensure_plugin_tables()
        db = get_db_client()

        db.execute(
            'UPDATE plugins SET is_enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND plugin_name = ?',
            (user_id, plugin_name)
        )

        # 从运行时移除实例
        instance_key = f"{user_id}:{plugin_name}"
        if instance_key in _plugin_instances:
            del _plugin_instances[instance_key]

        return {"success": True, "message": f"Plugin '{plugin_name}' disabled"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def delete_plugin(user_id: int, plugin_name: str) -> Dict[str, Any]:
    """删除插件"""
    try:
        _ensure_plugin_tables()
        db = get_db_client()

        db.execute(
            'DELETE FROM plugins WHERE user_id = ? AND plugin_name = ?',
            (user_id, plugin_name)
        )

        instance_key = f"{user_id}:{plugin_name}"
        if instance_key in _plugin_instances:
            del _plugin_instances[instance_key]

        return {"success": True, "message": f"Plugin '{plugin_name}' deleted"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_plugin_instance(user_id: int, plugin_name: str) -> Optional[MemoryStoragePlugin]:
    """获取插件实例"""
    instance_key = f"{user_id}:{plugin_name}"
    return _plugin_instances.get(instance_key)


def plugin_store(user_id: int, plugin_name: str, key: str, value: Any,
                 metadata: Optional[Dict] = None) -> Dict[str, Any]:
    """通过插件存储数据"""
    instance = get_plugin_instance(user_id, plugin_name)
    if not instance:
        return {"success": False, "error": f"Plugin '{plugin_name}' not initialized"}

    try:
        result = instance.store(key, value, metadata)
        db = get_db_client()
        _ensure_plugin_tables()
        db.execute(
            'INSERT INTO plugin_logs (user_id, plugin_name, action, detail) VALUES (?, ?, ?, ?)',
            (user_id, plugin_name, "store", f"key={key}")
        )
        return {"success": result, "key": key}
    except Exception as e:
        return {"success": False, "error": str(e)}


def plugin_retrieve(user_id: int, plugin_name: str, key: str) -> Dict[str, Any]:
    """通过插件读取数据"""
    instance = get_plugin_instance(user_id, plugin_name)
    if not instance:
        return {"success": False, "error": f"Plugin '{plugin_name}' not initialized"}

    try:
        value = instance.retrieve(key)
        return {"success": True, "key": key, "value": value, "found": value is not None}
    except Exception as e:
        return {"success": False, "error": str(e)}


def plugin_search(user_id: int, plugin_name: str, query: str, limit: int = 10) -> Dict[str, Any]:
    """通过插件搜索数据"""
    instance = get_plugin_instance(user_id, plugin_name)
    if not instance:
        return {"success": False, "error": f"Plugin '{plugin_name}' not initialized"}

    try:
        results = instance.search(query, limit)
        return {"success": True, "results": results, "count": len(results)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_plugin_info(user_id: int, plugin_name: str) -> Dict[str, Any]:
    """获取插件信息"""
    instance = get_plugin_instance(user_id, plugin_name)
    if not instance:
        return {"success": False, "error": f"Plugin '{plugin_name}' not initialized"}

    return {"success": True, "info": instance.get_info()}


# ============================================================
# 测试
# ============================================================

def test_plugin_service():
    """测试插件化架构服务"""
    print("\n" + "="*60)
    print("测试插件化架构服务")
    print("="*60 + "\n")

    user_id = 999

    # 清理
    db = get_db_client()
    _ensure_plugin_tables()
    db.execute('DELETE FROM plugins WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM plugin_logs WHERE user_id = ?', (user_id,))
    _plugin_instances.clear()

    print("1. 测试发现可用插件...")
    result = discover_plugins()
    print(f"   内置插件数: {len(result.get('builtin_plugins', []))}")
    for p in result.get("builtin_plugins", []):
        print(f"   - {p['name']} ({p['type']})")
    assert result["success"] == True
    print(f"   ✓ 插件发现成功\n")

    print("2. 测试注册文件存储插件...")
    result = register_plugin(user_id, "file-storage", "file", {"base_dir": "/tmp/test_plugin_memory"})
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 文件存储插件注册成功\n")

    print("3. 测试注册内存存储插件...")
    result = register_plugin(user_id, "in-memory", "memory", {})
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 内存存储插件注册成功\n")

    print("4. 测试列出所有插件...")
    result = list_plugins(user_id)
    print(f"   插件数: {result.get('count', 0)}")
    for p in result.get("plugins", []):
        enabled = "✓" if p["is_enabled"] else "✗"
        print(f"   [{enabled}] {p['name']} ({p['type']}) builtin={p['is_builtin']}")
    assert result["count"] == 2
    print(f"   ✓ 列出插件成功\n")

    print("5. 测试通过文件插件存储数据...")
    result = plugin_store(user_id, "file-storage", "test_key", {"name": "鑫海", "role": "PM"})
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 存储成功\n")

    print("6. 测试通过文件插件读取数据...")
    result = plugin_retrieve(user_id, "file-storage", "test_key")
    print(f"   值: {result.get('value')}")
    assert result["success"] == True
    assert result["found"] == True
    print(f"   ✓ 读取成功\n")

    print("7. 测试通过内存插件存储数据...")
    result = plugin_store(user_id, "in-memory", "mem_key", "hello world")
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 存储成功\n")

    print("8. 测试通过内存插件搜索数据...")
    result = plugin_search(user_id, "in-memory", "hello", limit=5)
    print(f"   搜索结果数: {result.get('count', 0)}")
    assert result["count"] >= 1
    print(f"   ✓ 搜索成功\n")

    print("9. 测试获取插件信息...")
    result = get_plugin_info(user_id, "in-memory")
    print(f"   信息: {result.get('info')}")
    assert result["success"] == True
    print(f"   ✓ 获取信息成功\n")

    print("10. 测试禁用/启用插件...")
    result = disable_plugin(user_id, "in-memory")
    print(f"   禁用: {result.get('success')}")
    assert result["success"] == True
    result = enable_plugin(user_id, "in-memory")
    print(f"   启用: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 禁用/启用成功\n")

    print("11. 测试删除插件...")
    result = delete_plugin(user_id, "in-memory")
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    result = list_plugins(user_id)
    assert result["count"] == 1
    print(f"   ✓ 删除成功\n")

    # 清理
    db.execute('DELETE FROM plugins WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM plugin_logs WHERE user_id = ?', (user_id,))
    _plugin_instances.clear()

    print("="*60)
    print("✅ 插件化架构服务测试完成！")
    print("="*60 + "\n")

    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    test_plugin_service()
