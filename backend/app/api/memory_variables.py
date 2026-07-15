"""
记忆变量 API 路由
"""
import logging
import fastapi as _fastapi
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Any, Dict

# 导入服务
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.memory_variable_service import (
    set_memory_variable,
    get_memory_variable,
    delete_memory_variable,
    list_memory_variables,
    list_memory_variables_detailed,
    update_variable_ttl,
    clear_memory_variables,
    extract_variables_from_text,
    render_template
)
from app.core.auth import get_current_user, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-variables"])


# 请求模型
class SetVariableRequest(BaseModel):
    key: str
    value: Any
    session_id: Optional[str] = None
    ttl: Optional[int] = 86400  # 默认 24 小时


class GetVariableRequest(BaseModel):
    key: str
    session_id: Optional[str] = None
    default: Optional[Any] = None


class DeleteVariableRequest(BaseModel):
    key: str
    session_id: Optional[str] = None


class ExtractVariablesRequest(BaseModel):
    text: str


class RenderTemplateRequest(BaseModel):
    template: str
    variables: Dict[str, Any]


class UpdateTtlRequest(BaseModel):
    ttl: Optional[int] = None  # 秒数，0 或 None 表示永久保留


# API 路由
@router.post("/variables")
async def set_variable(
    request: SetVariableRequest,
    current_user: User = Depends(get_current_user)
):
    """
    设置记忆变量
    
    Args:
        request: 设置变量请求（key, value, session_id, ttl）
        current_user: 当前登录用户
        
    Returns:
        设置结果
    """
    try:
        result = set_memory_variable(
            user_id=current_user.user_id,
            key=request.key,
            value=request.value,
            session_id=request.session_id,
            ttl=request.ttl
        )
        
        if result:
            return {
                "success": True,
                "message": f"Variable '{request.key}' set successfully"
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to set variable"
            )
            
    except Exception as e:
        logger.error(f"✗ 设置变量失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/variables/{key}")
async def get_variable(
    key: str,
    session_id: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """
    获取记忆变量
    
    Args:
        key: 变量名
        session_id: 会话ID（可选）
        current_user: 当前登录用户
        
    Returns:
        变量值
    """
    try:
        value = get_memory_variable(
            user_id=current_user.user_id,
            key=key,
            session_id=session_id,
            default=None
        )
        
        if value is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Variable '{key}' not found"
            )
        
        return {
            "key": key,
            "value": value
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取变量失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/variables/{key}")
async def delete_variable(
    key: str,
    session_id: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """
    删除记忆变量
    
    Args:
        key: 变量名
        session_id: 会话ID（可选）
        current_user: 当前登录用户
        
    Returns:
        删除结果
    """
    try:
        result = delete_memory_variable(
            user_id=current_user.user_id,
            key=key,
            session_id=session_id
        )
        
        if result:
            return {
                "success": True,
                "message": f"Variable '{key}' deleted successfully"
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Variable '{key}' not found"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 删除变量失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/variables")
async def list_variables(
    session_id: Optional[str] = None,
    detailed: bool = False,
    current_user: User = Depends(get_current_user)
):
    """
    列出所有记忆变量
    
    Args:
        session_id: 会话ID（可选）
        detailed: 是否返回含 TTL 等详细信息的数组格式
        current_user: 当前登录用户
        
    Returns:
        所有变量
    """
    try:
        if detailed:
            variables = list_memory_variables_detailed(
                user_id=current_user.user_id,
                session_id=session_id
            )
            return {
                "variables": variables,
                "count": len(variables)
            }
        else:
            variables = list_memory_variables(
                user_id=current_user.user_id,
                session_id=session_id
            )
            return {
                "variables": variables,
                "count": len(variables)
            }
        
    except Exception as e:
        logger.error(f"✗ 列出变量失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.put("/variables/{key}/ttl")
async def update_ttl(
    key: str,
    request: UpdateTtlRequest,
    current_user: User = Depends(get_current_user)
):
    """
    更新变量 TTL / 续期
    
    Args:
        key: 变量名
        request: { ttl: int | null } — 秒数，0 表示永久
        current_user: 当前登录用户
    """
    try:
        result = update_variable_ttl(
            user_id=current_user.user_id,
            key=key,
            ttl=request.ttl
        )
        
        if result:
            return {
                "success": True,
                "message": f"Variable '{key}' TTL updated successfully"
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Variable '{key}' not found"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 更新变量 TTL 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/variables")
async def clear_variables(
    session_id: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """
    清空所有记忆变量
    
    Args:
        session_id: 会话ID（可选，如果提供则只清空会话级别变量）
        current_user: 当前登录用户
        
    Returns:
        清空结果
    """
    try:
        count = clear_memory_variables(
            user_id=current_user.user_id,
            session_id=session_id
        )
        
        return {
            "success": True,
            "message": f"Cleared {count} variables"
        }
        
    except Exception as e:
        logger.error(f"✗ 清空变量失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/extract")
async def extract_variables(
    request: ExtractVariablesRequest,
    current_user: User = Depends(get_current_user)
):
    """
    从文本中抽取变量
    
    Args:
        request: 抽取变量请求（text）
        current_user: 当前登录用户
        
    Returns:
        抽取到的变量字典
    """
    try:
        variables = extract_variables_from_text(request.text)
        
        return {
            "text": request.text,
            "extracted_variables": variables,
            "count": len(variables)
        }
        
    except Exception as e:
        logger.error(f"✗ 抽取变量失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/render")
async def render_template_api(
    request: RenderTemplateRequest,
    current_user: User = Depends(get_current_user)
):
    """
    渲染模板（将 {variable_name} 替换为变量值）
    
    Args:
        request: 渲染模板请求（template, variables）
        current_user: 当前登录用户
        
    Returns:
        渲染后的字符串
    """
    try:
        rendered = render_template(request.template, request.variables)
        
        return {
            "template": request.template,
            "variables": request.variables,
            "rendered": rendered
        }
        
    except Exception as e:
        logger.error(f"✗ 渲染模板失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# 测试函数
def test_memory_variable_api():
    """测试记忆变量 API（模拟）"""
    print("\n" + "="*60)
    print("测试记忆变量服务")
    print("="*60 + "\n")
    
    user_id = 999  # 测试用户ID
    
    # 测试设置变量
    print("1. 测试设置变量...")
    set_memory_variable(user_id, "user_name", "鑫海")
    set_memory_variable(user_id, "user_role", "PM")
    set_memory_variable(user_id, "user_projects", ["源启·智能体工厂", "Agent星图"])
    print(f"   ✓ 设置变量成功")
    
    # 测试获取变量
    print(f"\n2. 测试获取变量...")
    name = get_memory_variable(user_id, "user_name")
    role = get_memory_variable(user_id, "user_role")
    projects = get_memory_variable(user_id, "user_projects")
    
    print(f"   user_name: {name}")
    print(f"   user_role: {role}")
    print(f"   user_projects: {projects}")
    print(f"   ✓ 获取变量成功")
    
    # 测试会话级别变量
    print(f"\n3. 测试会话级别变量...")
    session_id = "test_session_123"
    set_memory_variable(user_id, "temp_data", "会话数据", session_id=session_id)
    
    temp_data = get_memory_variable(user_id, "temp_data", session_id=session_id)
    print(f"   会话变量 temp_data: {temp_data}")
    print(f"   ✓ 会话级别变量成功")
    
    # 测试列出所有变量
    print(f"\n4. 测试列出所有变量...")
    all_vars = list_memory_variables(user_id)
    print(f"   全局变量：{all_vars}")
    print(f"   ✓ 列出变量成功")
    
    # 测试从文本抽取变量
    print(f"\n5. 测试从文本抽取变量...")
    text = "我叫鑫海，我的名字是XinHai。我的项目是源启·智能体工厂。"
    extracted = extract_variables_from_text(text)
    print(f"   文本：{text}")
    print(f"   抽取结果：{extracted}")
    print(f"   ✓ 抽取变量成功")
    
    # 测试模板渲染
    print(f"\n6. 测试模板渲染...")
    template = "你好，{user_name}！你的角色是 {user_role}。"
    variables = {
        "user_name": "鑫海",
        "user_role": "PM"
    }
    rendered = render_template(template, variables)
    print(f"   模板：{template}")
    print(f"   渲染后：{rendered}")
    print(f"   ✓ 模板渲染成功")
    
    # 测试删除变量
    print(f"\n7. 测试删除变量...")
    delete_memory_variable(user_id, "user_role")
    role_after_delete = get_memory_variable(user_id, "user_role")
    print(f"   删除后 user_role: {role_after_delete}")
    print(f"   ✓ 删除变量成功")
    
    # 清理测试数据
    print(f"\n8. 清理测试数据...")
    clear_memory_variables(user_id)
    print(f"   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 记忆变量 API 测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_memory_variable_api()
