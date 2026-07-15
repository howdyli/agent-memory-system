"""
用户认证和授权模块

提供用户注册、登录、Token验证和多租户数据隔离
"""
import logging
import hashlib
import secrets
import os
import warnings
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import Depends, HTTPException, status
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
    def __init__(self, user_id: int, username: str, email: Optional[str] = None):
        self.user_id = user_id
        self.id = user_id
        self.username = username
        self.email = email
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "email": self.email
        }


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
    expiration = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
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
    
    用法：
    ```python
    @app.get("/api/v1/memory")
    async def get_memory(current_user: User = Depends(get_current_user)):
        # current_user.user_id 是当前登录用户的ID
        ...
    ```
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
    
    return User(user_id=user_id, username=username)


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
