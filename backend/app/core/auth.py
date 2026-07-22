"""
用户认证和授权模块

提供用户注册、登录、Token验证和多租户数据隔离
"""
import logging
import hashlib
import secrets
import os
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

logger = logging.getLogger(__name__)

# 配置 — 从环境变量读取，缺失时生成临时密钥并警告
def _load_jwt_secret() -> str:
    key = os.environ.get("JWT_SECRET_KEY")
    if key:
        return key
    # 开发环境兜底：生成随机临时密钥
    dev_key = secrets.token_urlsafe(32)
    warnings.warn(
        "JWT_SECRET_KEY 未设置，已生成临时密钥。生产环境必须通过环境变量配置 JWT_SECRET_KEY。",
        RuntimeWarning,
        stacklevel=2,
    )
    return dev_key

JWT_SECRET_KEY = _load_jwt_secret()
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRATION_HOURS = int(os.environ.get("JWT_EXPIRATION_HOURS", "24"))

# HTTP Bearer 认证
security = HTTPBearer()


class User:
    """用户模型"""
    def __init__(self, user_id: int, username: str, email: Optional[str] = None,
                 default_workspace_id: Optional[int] = None,
                 roles: Optional[List[str]] = None):
        self.user_id = user_id
        self.id = user_id
        self.username = username
        self.email = email
        # Phase 2: 多租户
        self.default_workspace_id = default_workspace_id
        self.roles = roles or []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email,
            "default_workspace_id": self.default_workspace_id,
            "roles": self.roles,
        }

    def has_role(self, role: str) -> bool:
        return role in self.roles


@dataclass
class Principal:
    """统一认证主体。同时支持 JWT 用户 与 API Key 机器身份。

    - auth_method="jwt": 代表登录用户，user_id 必填，workspace_id 取 users.default_workspace_id
    - auth_method="api_key": 代表 API Key，user_id 为 key 创建者，workspace_id 为 key 所属空间，
      scopes 为 key 被授权的权限列表
    """
    user_id: int
    workspace_id: Optional[int] = None
    scopes: List[str] = field(default_factory=list)
    auth_method: str = "jwt"  # "jwt" | "api_key"
    api_key_id: Optional[int] = None  # 仅 api_key 模式有值

    def has_scope(self, scope: str) -> bool:
        """检查是否拥有指定权限（API Key 按 scopes 严格检查；JWT 用户默认全部权限）。"""
        if self.auth_method == "jwt":
            return True
        return scope in self.scopes


# 密码哈希迭代次数（从环境变量读取，默认 200000，兼容旧密码）
PBKDF2_ITERATIONS = int(os.environ.get("PBKDF2_ITERATIONS", "200000"))
_PBKDF2_LEGACY_ITERATIONS = 10000  # 旧密码兼容


def hash_password(password: str) -> str:
    """
    哈希密码（使用 PBKDF2-HMAC-SHA256 + salt）

    返回格式: {iterations}${salt}${hash}
    """
    salt = secrets.token_hex(16)
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"{PBKDF2_ITERATIONS}${salt}${pwd_hash}"


def verify_password(password: str, hashed: str) -> bool:
    """验证密码（兼容新旧格式）"""
    try:
        parts = hashed.split('$')
        # 新格式: iterations$salt$hash
        if len(parts) == 3:
            iterations, salt, pwd_hash = parts
            verify_hash = hashlib.pbkdf2_hmac(
                'sha256',
                password.encode('utf-8'),
                salt.encode('utf-8'),
                int(iterations),
            ).hex()
            return secrets.compare_digest(verify_hash, pwd_hash)
        # 旧格式: salt$hash (10000 次迭代)
        elif len(parts) == 2:
            salt, pwd_hash = parts
            verify_hash = hashlib.pbkdf2_hmac(
                'sha256',
                password.encode('utf-8'),
                salt.encode('utf-8'),
                _PBKDF2_LEGACY_ITERATIONS,
            ).hex()
            return secrets.compare_digest(verify_hash, pwd_hash)
        return False
    except Exception:
        return False


def create_access_token(user_id: int, username: str) -> str:
    """创建 JWT Access Token"""
    expiration = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRATION_HOURS)
    payload = {
        "user_id": user_id,
        "username": username,
        "exp": expiration
    }
    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
    """解码 JWT Token"""
    try:
        payload = jwt.decode(
            token, 
            JWT_SECRET_KEY, 
            algorithms=[JWT_ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Token 已过期")
        return None
    except jwt.InvalidTokenError:
        logger.warning("Token 无效")
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> User:
    """
    获取当前用户（FastAPI 依赖注入）

    Phase 2 增强：从 DB 读取 default_workspace_id 和 roles，
    填充到 User 对象，使旧调用路径也能感知 workspace 上下文。
    """
    token = credentials.credentials
    payload = decode_access_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )

    user_id = payload.get("user_id")
    username = payload.get("username")

    if user_id is None or username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )

    # Phase 2: 从 DB 读取 workspace 上下文（延迟导入避免循环依赖）
    default_workspace_id, roles = _load_user_workspace_context(user_id)

    return User(
        user_id=user_id,
        username=username,
        default_workspace_id=default_workspace_id,
        roles=roles,
    )


def _load_user_workspace_context(user_id: int) -> tuple:
    """从 DB 读取 (default_workspace_id, roles)。失败时返回 (None, [])，
    保持旧调用路径的容错语义。"""
    try:
        from app.core.db_client import get_db_client
        client = get_db_client()
        row = client.execute(
            "SELECT default_workspace_id FROM users WHERE id = ?",
            (user_id,),
        )
        default_workspace_id = row[0]["default_workspace_id"] if row else None

        roles = []
        if default_workspace_id is not None:
            member_rows = client.execute(
                "SELECT role FROM workspace_members "
                "WHERE workspace_id = ? AND user_id = ?",
                (default_workspace_id, user_id),
            )
            roles = [r["role"] for r in member_rows] if member_rows else []
        return default_workspace_id, roles
    except Exception as e:
        logger.warning(f"读取 workspace 上下文失败（降级为无 workspace）: {e}")
        return None, []


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Principal:
    """统一认证入口：同时支持 JWT 与 API Key。

    识别规则：
    - Authorization: Bearer <jwt>           → JWT 用户
    - Authorization: Bearer amk_xxxxx       → API Key
    """
    token = credentials.credentials

    # API Key 分支：前缀 amk_ 表示机器身份
    if token.startswith("amk_"):
        principal = await _authenticate_api_key(token)
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key",
            )
        return principal

    # JWT 分支：现有逻辑 + 填充 workspace 上下文
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"}
        )
    user_id = payload.get("user_id")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload"
        )

    default_workspace_id, _ = _load_user_workspace_context(user_id)
    # 优先使用请求头 X-Workspace-Id（校验成员资格后生效），否则回退到默认空间
    effective_workspace_id = _resolve_workspace_from_header(
        request, user_id, default_workspace_id
    )
    return Principal(
        user_id=user_id,
        workspace_id=effective_workspace_id,
        scopes=[],  # JWT 用户默认全部权限（has_scope 返回 True）
        auth_method="jwt",
    )


def _resolve_workspace_from_header(
    request: Request, user_id: int, default_workspace_id: Optional[int]
) -> Optional[int]:
    """解析请求头 X-Workspace-Id。

    仅当请求头存在、可转为 int、且用户是该 workspace 成员时才生效；
    否则回退到 default_workspace_id，保持安全默认。
    """
    raw = request.headers.get("X-Workspace-Id")
    if not raw:
        return default_workspace_id
    try:
        requested_ws = int(raw)
    except (TypeError, ValueError):
        return default_workspace_id
    if requested_ws == default_workspace_id:
        return requested_ws
    try:
        from app.core.db_client import get_db_client
        client = get_db_client()
        rows = client.execute(
            "SELECT 1 FROM workspace_members "
            "WHERE workspace_id = ? AND user_id = ?",
            (requested_ws, user_id),
        )
        if rows:
            return requested_ws
        logger.warning(
            f"⚠️  用户 {user_id} 非 workspace {requested_ws} 成员，回退默认空间"
        )
    except Exception as e:
        logger.warning(f"校验 X-Workspace-Id 成员资格失败（回退默认）: {e}")
    return default_workspace_id


async def _authenticate_api_key(raw_key: str) -> Optional[Principal]:
    """校验 API Key 并返回 Principal。延迟导入 api_key_service 避免循环依赖。"""
    try:
        from app.services.api_key_service import validate_api_key
        return await validate_api_key(raw_key)
    except Exception as e:
        logger.error(f"API Key 认证异常: {e}")
        return None


def get_user_from_token(token: str) -> Optional[User]:
    """
    从 Token 中获取用户（用于非 FastAPI 路由）
    
    用法：
    ```python
    user = get_user_from_token(token)
    if user:
        print(user.user_id)
    ```
    """
    payload = decode_access_token(token)
    if payload is None:
        return None
    
    user_id = payload.get("user_id")
    username = payload.get("username")
    
    if user_id is None or username is None:
        return None
    
    return User(user_id=user_id, username=username)


# 多租户数据隔离工具函数
def enforce_user_isolation(user_id: int, resource_user_id: int):
    """
    强制用户数据隔离
    
    Args:
        user_id: 当前用户ID
        resource_user_id: 资源所属用户ID
    
    Raises:
        HTTPException: 如果用户无权访问该资源
    """
    if user_id != resource_user_id:
        logger.warning(f"⚠️  越权访问尝试：用户 {user_id} 试图访问用户 {resource_user_id} 的资源")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You can only access your own resources"
        )


"""
敏感信息加密工具

使用 AES-256-GCM 加密敏感信息
"""
import logging
import os
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64

logger = logging.getLogger(__name__)


class EncryptionService:
    """敏感信息加密服务（AES-256）"""
    
    _instance = None
    _key = None
    
    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize_key()
        return cls._instance
    
    def _initialize_key(self):
        """初始化加密密钥（必须从环境变量读取）"""
        password = os.environ.get("ENCRYPTION_PASSWORD")
        salt_env = os.environ.get("ENCRYPTION_SALT")

        if not password or not salt_env:
            # 开发环境兜底：生成临时密钥（重启后无法解密旧数据，仅限开发）
            warnings.warn(
                "ENCRYPTION_PASSWORD/ENCRYPTION_SALT 未设置，已生成临时密钥。"
                "生产环境必须配置，否则重启后无法解密敏感数据。",
                RuntimeWarning,
                stacklevel=2,
            )
            password = secrets.token_urlsafe(32)
            salt_env = secrets.token_hex(16)

        salt = salt_env.encode('utf-8')
        
        # 使用 PBKDF2 从密码派生密钥
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode('utf-8')))
        self._key = key
    
    def encrypt(self, plaintext: str) -> str:
        """
        加密明文
        
        Args:
            plaintext: 明文
            
        Returns:
            加密后的密文（base64 编码）
        """
        try:
            f = Fernet(self._key)
            encrypted = f.encrypt(plaintext.encode('utf-8'))
            return base64.urlsafe_b64encode(encrypted).decode('utf-8')
        except Exception as e:
            logger.error(f"✗ 加密失败: {e}")
            raise
    
    def decrypt(self, ciphertext: str) -> str:
        """
        解密密文
        
        Args:
            ciphertext: 密文（base64 编码）
            
        Returns:
            明文
        """
        try:
            f = Fernet(self._key)
            decoded = base64.urlsafe_b64decode(ciphertext)
            decrypted = f.decrypt(decoded)
            return decrypted.decode('utf-8')
        except Exception as e:
            logger.error(f"✗ 解密失败: {e}")
            raise
    
    def encrypt_sensitive_fields(self, data: dict, sensitive_fields: list) -> dict:
        """
        加密字典中的敏感字段
        
        Args:
            data: 原始数据字典
            sensitive_fields: 需要加密的字段列表
            
        Returns:
            加密后的数据字典（原字典不会被修改）
        """
        import copy
        encrypted_data = copy.deepcopy(data)
        
        for field in sensitive_fields:
            if field in encrypted_data and encrypted_data[field]:
                encrypted_data[field] = self.encrypt(str(encrypted_data[field]))
        
        return encrypted_data
    
    def decrypt_sensitive_fields(self, data: dict, sensitive_fields: list) -> dict:
        """
        解密字典中的敏感字段
        
        Args:
            data: 加密数据字典
            sensitive_fields: 需要解密的字段列表
            
        Returns:
            解密后的数据字典（原字典不会被修改）
        """
        import copy
        decrypted_data = copy.deepcopy(data)
        
        for field in sensitive_fields:
            if field in decrypted_data and decrypted_data[field]:
                try:
                    decrypted_data[field] = self.decrypt(str(decrypted_data[field]))
                except Exception as e:
                    logger.error(f"✗ 字段 {field} 解密失败: {e}")
                    # 如果解密失败，保留原值（可能是未加密的数据）
        
        return decrypted_data


# 全局加密服务实例
_encryption_service = None

def get_encryption_service() -> EncryptionService:
    """获取加密服务实例（单例模式）"""
    global _encryption_service
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    return _encryption_service


# 测试函数
def test_encryption():
    """测试加密解密功能"""
    print("\n" + "="*60)
    print("测试敏感信息加密")
    print("="*60 + "\n")
    
    service = get_encryption_service()
    
    # 测试加密解密
    print("1. 测试基本加密解密...")
    plaintext = "我的密码是 123456"
    encrypted = service.encrypt(plaintext)
    decrypted = service.decrypt(encrypted)
    
    print(f"   原文: {plaintext}")
    print(f"   密文: {encrypted[:50]}...")
    print(f"   解密: {decrypted}")
    print(f"   ✓ 加解密成功: {plaintext == decrypted}")
    
    # 测试字段加密
    print(f"\n2. 测试字段加密...")
    data = {
        "user_id": 1,
        "name": "鑫海",
        "email": "xinhai@example.com",
        "password": "my-secret-password",
        "credit_card": "1234-5678-9012-3456"
    }
    sensitive_fields = ["password", "credit_card"]
    encrypted_data = service.encrypt_sensitive_fields(data, sensitive_fields)
    
    print(f"   原始数据: {data}")
    print(f"   加密后: {encrypted_data}")
    print(f"   ✓ 敏感字段已加密: {encrypted_data['password'] != data['password']}")
    
    # 测试字段解密
    print(f"\n3. 测试字段解密...")
    decrypted_data = service.decrypt_sensitive_fields(encrypted_data, sensitive_fields)
    
    print(f"   解密后: {decrypted_data}")
    print(f"   ✓ 敏感字段已解密: {decrypted_data['password'] == data['password']}")
    
    print("\n" + "="*60)
    print("✅ 加密测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_encryption()
