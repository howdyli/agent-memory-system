"""
认证相关 API 路由

Stability: STABLE — 向后兼容，破坏性变更仅随主版本号发布。
"""
import logging
import fastapi as _fastapi
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

# 导入认证模块
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.auth import (
    User, 
    hash_password, 
    verify_password,
    create_access_token,
    get_current_user,
    Principal,
    get_current_principal,
)
from app.core.db_client import get_db_client
from app.core.errors import AppException, AuthError, ConflictError, NotFoundError, ValidationError
from app.core.rbac import Perm, require_permission
from app.services import api_key_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["authentication"])


# 请求模型
class UserRegister(BaseModel):
    username: str
    password: str
    email: Optional[str] = None


class UserLogin(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    username: str


# API 路由
@router.post("/register", response_model=TokenResponse)
async def register(user_data: UserRegister):
    """
    用户注册
    
    Args:
        user_data: 用户注册信息（用户名、密码、邮箱）
        
    Returns:
        TokenResponse: 包含 access_token 和用户信息
    """
    db = get_db_client()
    
    # 检查用户是否已存在
    existing = db.execute(
        'SELECT id FROM users WHERE username = ? OR email = ?',
        (user_data.username, user_data.email)
    )
    
    if existing:
        raise ConflictError(
            "Username or email already registered",
            status_code=400,
        )
    
    # 哈希密码
    password_hash = hash_password(user_data.password)
    
    # 创建用户
    try:
        with db.get_cursor() as cursor:
            cursor.execute(
                'INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)',
                (user_data.username, user_data.email, password_hash)
            )
            user_id = cursor.lastrowid
    except Exception as e:
        logger.error(f"✗ 用户注册失败: {e}")
        raise AppException("Registration failed")
    
    # 创建 Token
    access_token = create_access_token(user_id, user_data.username)
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user_id,
        "username": user_data.username
    }


@router.post("/login", response_model=TokenResponse)
async def login(login_data: UserLogin):
    """
    用户登录
    
    Args:
        login_data: 用户登录信息（用户名、密码）
        
    Returns:
        TokenResponse: 包含 access_token 和用户信息
    """
    db = get_db_client()
    
    # 查找用户
    result = db.execute(
        'SELECT id, username, email, password_hash FROM users WHERE username = ?',
        (login_data.username,)
    )
    
    if not result:
        raise AuthError("Incorrect username or password")
    
    user_data = dict(result[0])
    
    # 验证密码
    if not verify_password(login_data.password, user_data['password_hash']):
        raise AuthError("Incorrect username or password")
    
    # 创建 Token
    access_token = create_access_token(user_data['id'], user_data['username'])
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user_data['id'],
        "username": user_data['username']
    }


@router.get("/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """
    获取当前登录用户信息
    
    Args:
        current_user: 当前用户（通过 Token 认证）
        
    Returns:
        用户基本信息
    """
    return {
        "user_id": current_user.user_id,
        "username": current_user.username,
        "email": current_user.email
    }


@router.post("/logout")
async def logout():
    """
    用户登出
    
    注意：JWT Token 是无状态的，登出需要客户端删除 Token
    生产环境可使用 Token 黑名单机制
    """
    return {"message": "Logout successful. Please delete your token on client side."}


# ============================================================
# API Key 管理（Phase 2）
# ============================================================
class ApiKeyCreateRequest(BaseModel):
    name: str
    scopes: Optional[List[str]] = None
    expires_at: Optional[datetime] = None


class ApiKeyOut(BaseModel):
    id: int
    name: str
    workspace_id: int
    scopes: List[str]
    last_used_at: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None


class ApiKeyCreatedOut(BaseModel):
    id: int
    key: str  # 明文 key，仅创建时返回一次
    name: str
    workspace_id: int
    scopes: List[str]
    expires_at: Optional[str] = None
    created_at: Optional[str] = None


@router.get("/api-keys", response_model=List[ApiKeyOut])
async def list_api_keys(
    principal: Principal = Depends(require_permission(Perm.API_KEY_MANAGE)),
):
    """列出当前 workspace 的 API Key。"""
    if principal.workspace_id is None:
        raise ValidationError("No active workspace")
    return api_key_service.list_api_keys(principal.workspace_id)


@router.post("/api-keys", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreateRequest,
    principal: Principal = Depends(require_permission(Perm.API_KEY_MANAGE)),
):
    """创建 API Key（明文 key 仅返回一次）。"""
    if principal.workspace_id is None:
        raise ValidationError("No active workspace")
    return api_key_service.create_api_key(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        name=body.name,
        scopes=body.scopes,
        expires_at=body.expires_at,
    )


@router.delete("/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: int,
    principal: Principal = Depends(require_permission(Perm.API_KEY_MANAGE)),
):
    """撤销 API Key。"""
    if principal.workspace_id is None:
        raise ValidationError("No active workspace")
    ok = api_key_service.revoke_api_key(key_id, principal.workspace_id)
    if not ok:
        raise NotFoundError("API key not found")


# 测试函数
def test_auth_api():
    """测试认证 API（模拟）"""
    print("\n" + "="*60)
    print("测试用户认证和授权")
    print("="*60 + "\n")
    
    # 测试密码哈希
    print("1. 测试密码哈希和验证...")
    password = "my-secret-password-123"
    hashed = hash_password(password)
    print(f"   原文: {password}")
    print(f"   哈希: {hashed[:50]}...")
    print(f"   ✓ 验证正确密码: {verify_password(password, hashed)}")
    print(f"   ✓ 验证错误密码: {verify_password('wrong-password', hashed)}")
    
    # 测试 JWT Token
    print(f"\n2. 测试 JWT Token 生成和验证...")
    user_id = 123
    username = "xinhai"
    token = create_access_token(user_id, username)
    print(f"   生成 Token: {token[:50]}...")
    
    from app.core.auth import decode_access_token
    payload = decode_access_token(token)
    print(f"   解码 Payload: {payload}")
    print(f"   ✓ Token 验证成功: {payload['user_id'] == user_id and payload['username'] == username}")
    
    # 测试多租户隔离
    print(f"\n3. 测试多租户数据隔离...")
    from app.core.auth import enforce_user_isolation
    
    try:
        enforce_user_isolation(1, 1)  # 相同用户，应该通过
        print(f"   ✓ 相同用户访问：允许")
    except HTTPException as e:
        print(f"   ✗ 相同用户访问：拒绝（错误）")
    
    try:
        enforce_user_isolation(1, 2)  # 不同用户，应该拒绝
        print(f"   ✗ 不同用户访问：允许（错误）")
    except HTTPException as e:
        print(f"   ✓ 不同用户访问：拒绝（正确）")
    
    # 测试加密服务
    print(f"\n4. 测试敏感信息加密...")
    from app.core.auth import get_encryption_service
    
    encryption_service = get_encryption_service()
    plaintext = "我的银行卡号是 6225-1234-5678-9012"
    encrypted = encryption_service.encrypt(plaintext)
    decrypted = encryption_service.decrypt(encrypted)
    
    print(f"   原文: {plaintext}")
    print(f"   密文: {encrypted[:50]}...")
    print(f"   解密: {decrypted}")
    print(f"   ✓ 加解密成功: {plaintext == decrypted}")
    
    print("\n" + "="*60)
    print("✅ 认证和授权测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_auth_api()
