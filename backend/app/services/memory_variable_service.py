"""
记忆变量服务（基于 Redis）

提供轻量级的 Key-Value 存储机制
实现会话级别的变量作用域管理
"""
import logging
import json
import re
from typing import Optional, Any, Dict, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# 全局 Redis 客户端
from app.core.redis_client import get_redis_client
from app.core.tracing import get_tracer


def _build_key(user_id: int, key: str, session_id: Optional[str] = None) -> str:
    """
    构建 Redis Key
    
    格式：
    - 全局变量：memory:var:{user_id}:{key}
    - 会话变量：memory:var:{user_id}:{session_id}:{key}
    """
    if session_id:
        return f"memory:var:{user_id}:{session_id}:{key}"
    else:
        return f"memory:var:{user_id}:{key}"


def set_memory_variable(user_id: int,
                       key: str,
                       value: Any,
                       session_id: Optional[str] = None,
                       ttl: Optional[int] = 86400,
                       workspace_id: Optional[int] = None) -> bool:
    """
    设置记忆变量
    
    Args:
        user_id: 用户ID
        key: 变量名
        value: 变量值（可以是任意可JSON序列化的类型）
        session_id: 会话ID（如果提供，则为会话级别变量）
        ttl: 过期时间（秒），默认 86400（24小时），None 表示永久
        
    Returns:
        是否设置成功
    """
    try:
        _span = get_tracer().start_span("variable.set")
        _span.set_attribute("user.id", user_id)
        _span.set_attribute("variable.key", key)

        redis_client = get_redis_client()
        
        # 构建 Key
        redis_key = _build_key(user_id, key, session_id)
        
        # 序列化值
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value, ensure_ascii=False)
        else:
            value_str = str(value)
        
        # 存储到 Redis
        redis_client.set(redis_key, value_str)
        
        # 如果提供了 TTL，设置过期时间
        if ttl is not None:
            redis_client.expire(redis_key, ttl)
        else:
            # 如果之前有 TTL 现在去掉了，取消过期
            redis_client.persist(redis_key)
        
        # 记录到变量索引（用于列出所有变量）
        _update_variable_index(user_id, key, session_id)
        
        logger.debug(f"✓ 设置记忆变量：{redis_key} = {value_str[:50]}...")
        return True
        
    except Exception as e:
        if '_span' in locals():
            _span.record_exception(e)
        logger.error(f"✗ 设置记忆变量失败：{e}")
        return False
    finally:
        if '_span' in locals():
            _span.end()


def get_memory_variable(user_id: int, 
                       key: str, 
                       session_id: Optional[str] = None,
                       default: Any = None,
                       workspace_id: Optional[int] = None) -> Any:
    """
    获取记忆变量
    
    Args:
        user_id: 用户ID
        key: 变量名
        session_id: 会话ID（如果提供，则查找会话级别变量）
        default: 默认值（如果变量不存在）
        
    Returns:
        变量值（自动反序列化）
    """
    try:
        redis_client = get_redis_client()
        
        # 构建 Key
        redis_key = _build_key(user_id, key, session_id)
        
        # 从 Redis 读取
        value_str = redis_client.get(redis_key)
        
        if value_str is None:
            # 如果会话级别变量不存在，尝试查找全局变量
            if session_id:
                global_key = _build_key(user_id, key, None)
                value_str = redis_client.get(global_key)
                if value_str is None:
                    return default
            else:
                return default
        
        # 处理 bytes 类型（FakeRedis 返回 bytes）
        if isinstance(value_str, bytes):
            value_str = value_str.decode('utf-8')
        
        # 尝试反序列化
        try:
            return json.loads(value_str)
        except (json.JSONDecodeError, TypeError):
            return value_str
        
    except Exception as e:
        logger.error(f"✗ 获取记忆变量失败：{e}")
        return default


def delete_memory_variable(user_id: int, 
                          key: str, 
                          session_id: Optional[str] = None,
                          workspace_id: Optional[int] = None) -> bool:
    """
    删除记忆变量
    
    Args:
        user_id: 用户ID
        key: 变量名
        session_id: 会话ID（如果提供，则删除会话级别变量）
        
    Returns:
        是否删除成功
    """
    try:
        redis_client = get_redis_client()
        
        # 构建 Key
        redis_key = _build_key(user_id, key, session_id)
        
        # 从 Redis 删除
        result = redis_client.delete(redis_key)
        
        # 从变量索引中移除
        if result:
            _remove_from_variable_index(user_id, key, session_id)
            logger.debug(f"✓ 删除记忆变量：{redis_key}")
        
        return result > 0
        
    except Exception as e:
        logger.error(f"✗ 删除记忆变量失败：{e}")
        return False


def list_memory_variables(user_id: int, 
                         session_id: Optional[str] = None,
                         workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    列出所有记忆变量（简单 key-value 字典）
    
    Args:
        user_id: 用户ID
        session_id: 会话ID（如果提供，则只列出会话级别变量）
        
    Returns:
        变量名到值的字典
    """
    try:
        redis_client = get_redis_client()
        
        # 索引 Key
        if session_id:
            index_key = f"memory:var:index:{user_id}:{session_id}"
        else:
            index_key = f"memory:var:index:{user_id}"
        
        # 从索引中读取所有变量名
        index_str = redis_client.get(index_key)
        if not index_str:
            return {}
        
        # 处理已经是 list 的情况（FakeRedis 自动反序列化）
        if isinstance(index_str, list):
            var_names = index_str
        elif isinstance(index_str, bytes):
            var_names = json.loads(index_str.decode('utf-8'))
        elif isinstance(index_str, str):
            var_names = json.loads(index_str)
        else:
            return {}
        
        # 读取所有变量值
        result = {}
        for var_name in var_names:
            value = get_memory_variable(user_id, var_name, session_id)
            if value is not None:
                result[var_name] = value
        
        return result
        
    except Exception as e:
        logger.error(f"✗ 列出记忆变量失败：{e}")
        return {}


def list_memory_variables_detailed(user_id: int,
                                   session_id: Optional[str] = None,
                                   workspace_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    列出所有记忆变量（含 TTL / expires_at 详细信息）
    
    Returns:
        [{ key, value, ttl, expires_at }, ...]
    """
    try:
        redis_client = get_redis_client()
        
        if session_id:
            index_key = f"memory:var:index:{user_id}:{session_id}"
        else:
            index_key = f"memory:var:index:{user_id}"
        
        index_str = redis_client.get(index_key)
        if not index_str:
            return []
        
        if isinstance(index_str, list):
            var_names = index_str
        elif isinstance(index_str, bytes):
            var_names = json.loads(index_str.decode('utf-8'))
        elif isinstance(index_str, str):
            var_names = json.loads(index_str)
        else:
            return []
        
        result = []
        now = datetime.utcnow()
        for var_name in var_names:
            value = get_memory_variable(user_id, var_name, session_id)
            if value is None:
                continue
            
            redis_key = _build_key(user_id, var_name, session_id)
            remaining = redis_client.ttl(redis_key)
            
            ttl_val = None
            expires_at = None
            if remaining > 0:
                ttl_val = remaining
                expires_at = (now + timedelta(seconds=remaining)).isoformat() + "Z"
            # remaining == -1 → 无过期，ttl_val/expires_at 保持 None
            # remaining == -2 → key 不存在，跳过
            
            result.append({
                "key": var_name,
                "value": value,
                "ttl": ttl_val,
                "expires_at": expires_at,
            })
        
        return result
    except Exception as e:
        logger.error(f"✗ 列出记忆变量(详细)失败：{e}")
        return []


def update_variable_ttl(user_id: int,
                        key: str,
                        ttl: Optional[int],
                        session_id: Optional[str] = None,
                        workspace_id: Optional[int] = None) -> bool:
    """
    更新变量 TTL / 续期
    
    Args:
        ttl: 新 TTL（秒），0 或 None 表示永久保留
    """
    try:
        redis_client = get_redis_client()
        redis_key = _build_key(user_id, key, session_id)
        
        if not redis_client.exists(redis_key):
            return False
        
        if ttl and ttl > 0:
            redis_client.expire(redis_key, ttl)
        else:
            redis_client.persist(redis_key)
        
        logger.info(f"✓ 更新变量 TTL: {redis_key} -> {ttl}")
        return True
    except Exception as e:
        logger.error(f"✗ 更新变量 TTL 失败：{e}")
        return False


def clear_memory_variables(user_id: int, 
                         session_id: Optional[str] = None,
                         workspace_id: Optional[int] = None) -> int:
    """
    清空所有记忆变量
    
    Args:
        user_id: 用户ID
        session_id: 会话ID（如果提供，则只清空会话级别变量）
        
    Returns:
        清空的变量数量
    """
    try:
        variables = list_memory_variables(user_id, session_id)
        count = 0
        
        for key in variables.keys():
            if delete_memory_variable(user_id, key, session_id):
                count += 1
        
        logger.info(f"✓ 清空记忆变量：{count} 个")
        return count
        
    except Exception as e:
        logger.error(f"✗ 清空记忆变量失败：{e}")
        return 0


def extract_variables_from_text(text: str) -> Dict[str, str]:
    """
    从文本中抽取变量
    
    支持的模式：
    - "我叫{name}"
    - "我的名字是{name}"
    - "{key}是{value}"
    - "设置{key}为{value}"
    
    Args:
        text: 输入文本
        
    Returns:
        抽取到的变量字典
    """
    variables = {}
    
    # 模式1：我叫{name}
    match = re.search(r'我叫(.+?)[\s。，]', text)
    if match:
        variables['user_name'] = match.group(1).strip()
    
    # 模式2：我的名字是{name}
    match = re.search(r'我的名字是(.+?)[\s。，]', text)
    if match:
        variables['user_name'] = match.group(1).strip()
    
    # 模式3：{key}是{value}
    matches = re.findall(r'(\w+)是(.+?)[\s。，]', text)
    for key, value in matches:
        variables[key.strip()] = value.strip()
    
    # 模式4：设置{key}为{value}
    matches = re.findall(r'设置(\w+)为(.+?)[\s。，]', text)
    for key, value in matches:
        variables[key.strip()] = value.strip()
    
    return variables


def render_template(template: str, variables: Dict[str, Any]) -> str:
    """
    渲染模板（将 {variable_name} 替换为变量值）
    
    Args:
        template: 模板字符串（如 "你好，{user_name}！"）
        variables: 变量字典
        
    Returns:
        渲染后的字符串
    """
    result = template
    for key, value in variables.items():
        placeholder = '{' + key + '}'
        if placeholder in result:
            result = result.replace(placeholder, str(value))
    return result


# 辅助函数
def _update_variable_index(user_id: int, key: str, session_id: Optional[str] = None):
    """更新变量索引（用于 list_memory_variables）"""
    try:
        redis_client = get_redis_client()
        
        # 索引 Key
        if session_id:
            index_key = f"memory:var:index:{user_id}:{session_id}"
        else:
            index_key = f"memory:var:index:{user_id}"
        
        # 读取现有索引
        index_str = redis_client.get(index_key)
        
        # 处理已经是 list 的情况（FakeRedis 自动反序列化）
        if isinstance(index_str, list):
            index_list = index_str
        elif index_str:
            if isinstance(index_str, bytes):
                index_str = index_str.decode('utf-8')
            index_list = json.loads(index_str)
        else:
            index_list = []
        
        # 添加新变量名（如果不存在）
        if key not in index_list:
            index_list.append(key)
            
        # 保存回 Redis
        redis_client.set(index_key, json.dumps(index_list, ensure_ascii=False))
        
    except Exception as e:
        logger.error(f"✗ 更新变量索引失败：{e}")


def _remove_from_variable_index(user_id: int, key: str, session_id: Optional[str] = None):
    """从变量索引中移除"""
    try:
        redis_client = get_redis_client()
        
        # 索引 Key
        if session_id:
            index_key = f"memory:var:index:{user_id}:{session_id}"
        else:
            index_key = f"memory:var:index:{user_id}"
        
        # 读取现有索引
        index_str = redis_client.get(index_key)
        
        # 处理已经是 list 的情况（FakeRedis 自动反序列化）
        if isinstance(index_str, list):
            index_list = index_str
        elif index_str:
            if isinstance(index_str, bytes):
                index_str = index_str.decode('utf-8')
            index_list = json.loads(index_str)
        else:
            index_list = []
        
        # 移除变量名
        if key in index_list:
            index_list.remove(key)
                
        # 保存回 Redis
        redis_client.set(index_key, json.dumps(index_list, ensure_ascii=False))
        
    except Exception as e:
        logger.error(f"✗ 从变量索引中移除失败：{e}")


# 测试函数
def test_memory_variable_service():
    """测试记忆变量服务"""
    print("\n" + "="*60)
    print("测试记忆变量服务（基于 Redis）")
    print("="*60 + "\n")
    
    user_id = 999  # 测试用户ID
    
    # 测试设置变量
    print("1. 测试设置变量...")
    assert set_memory_variable(user_id, "user_name", "鑫海") == True
    assert set_memory_variable(user_id, "user_role", "PM") == True
    assert set_memory_variable(user_id, "user_projects", ["源启·智能体工厂", "Agent星图"]) == True
    print(f"   ✓ 设置变量成功")
    
    # 测试获取变量
    print(f"\n2. 测试获取变量...")
    name = get_memory_variable(user_id, "user_name")
    role = get_memory_variable(user_id, "user_role")
    projects = get_memory_variable(user_id, "user_projects")
    
    print(f"   user_name: {name}")
    print(f"   user_role: {role}")
    print(f"   user_projects: {projects}")
    
    assert name == "鑫海"
    assert role == "PM"
    assert "源启·智能体工厂" in projects
    print(f"   ✓ 获取变量成功")
    
    # 测试会话级别变量
    print(f"\n3. 测试会话级别变量...")
    session_id = "session_123"
    assert set_memory_variable(user_id, "temp_data", "会话数据", session_id=session_id) == True
    
    # 获取会话级别变量
    temp_data = get_memory_variable(user_id, "temp_data", session_id=session_id)
    print(f"   会话变量 temp_data: {temp_data}")
    assert temp_data == "会话数据"
    
    # 全局变量应该可以被会话访问（如果会话级别不存在）
    global_name = get_memory_variable(user_id, "user_name", session_id=session_id)
    print(f"   会话中访问全局变量 user_name: {global_name}")
    assert global_name == "鑫海"
    print(f"   ✓ 会话级别变量成功")
    
    # 测试列出所有变量
    print(f"\n4. 测试列出所有变量...")
    all_vars = list_memory_variables(user_id)
    print(f"   全局变量：{all_vars}")
    assert "user_name" in all_vars
    assert "user_role" in all_vars
    assert "user_projects" in all_vars
    print(f"   ✓ 列出变量成功")
    
    # 测试删除变量
    print(f"\n5. 测试删除变量...")
    assert delete_memory_variable(user_id, "user_role") == True
    role_after_delete = get_memory_variable(user_id, "user_role")
    print(f"   删除后 user_role: {role_after_delete}")
    assert role_after_delete is None
    print(f"   ✓ 删除变量成功")
    
    # 测试从文本抽取变量
    print(f"\n6. 测试从文本抽取变量...")
    text = "我叫鑫海，我的名字是XinHai。我的项目是源启·智能体工厂。"
    extracted = extract_variables_from_text(text)
    print(f"   文本：{text}")
    print(f"   抽取结果：{extracted}")
    assert "user_name" in extracted
    print(f"   ✓ 抽取变量成功")
    
    # 测试模板渲染
    print(f"\n7. 测试模板渲染...")
    template = "你好，{user_name}！你的角色是 {user_role}。"
    variables = {
        "user_name": "鑫海",
        "user_role": "PM"
    }
    rendered = render_template(template, variables)
    print(f"   模板：{template}")
    print(f"   渲染后：{rendered}")
    assert "{user_name}" not in rendered
    assert "鑫海" in rendered
    print(f"   ✓ 模板渲染成功")
    
    # 清理测试数据
    print(f"\n8. 清理测试数据...")
    clear_memory_variables(user_id)
    print(f"   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 记忆变量服务测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_memory_variable_service()
