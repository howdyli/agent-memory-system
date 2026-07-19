#!/usr/bin/env python3
"""
任务 4.1：生产部署配置预检脚本

在部署前逐项检查运行环境是否就绪：
  1. 关键环境变量 / 配置项（JWT 密钥、加密密钥、CORS、数据库 URL 等）
  2. 数据库连接（SQLite / Postgres）
  3. Redis 连接（若启用 redis 事件总线）
  4. ChromaDB 向量库可用性
  5. OTel collector 可达性（若启用 tracing）
  6. 关键依赖版本

用法：
    python scripts/pre_deploy_check.py            # 严格模式：任一 FAIL 退出码 1
    python scripts/pre_deploy_check.py --force    # 强制模式：仅告警，退出码始终 0
    python scripts/pre_deploy_check.py --json      # 以 JSON 输出结果

退出码：0 = 全部通过（或 --force）；1 = 存在失败项。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional

# 允许从 backend 根目录导入 app.*
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 检查级别
LEVEL_OK = "OK"
LEVEL_WARN = "WARN"
LEVEL_FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    level: str
    message: str


@dataclass
class Report:
    results: List[CheckResult] = field(default_factory=list)

    def add(self, name: str, level: str, message: str) -> None:
        self.results.append(CheckResult(name=name, level=level, message=message))

    @property
    def failed(self) -> List[CheckResult]:
        return [r for r in self.results if r.level == LEVEL_FAIL]

    @property
    def warned(self) -> List[CheckResult]:
        return [r for r in self.results if r.level == LEVEL_WARN]


# ============================================================
# 是否处于容器环境（用于放宽 localhost 类检查）
# ============================================================

def _in_docker() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "rt") as f:
            return "docker" in f.read() or "kubepods" in f.read()
    except OSError:
        return False


# ============================================================
# 各检查项
# ============================================================

def check_env_vars(report: Report) -> None:
    """检查关键配置项是否已设置为安全的生产值。"""
    try:
        from app.core.config import get_settings
        settings = get_settings()
    except Exception as e:  # pragma: no cover - 配置加载失败视为致命
        report.add("config", LEVEL_FAIL, f"无法加载 Settings: {e}")
        return

    # JWT 密钥必须设置且非空
    if not settings.JWT_SECRET_KEY:
        report.add("JWT_SECRET_KEY", LEVEL_FAIL, "未设置 JWT_SECRET_KEY，生产环境必须提供")
    elif len(settings.JWT_SECRET_KEY) < 32:
        report.add("JWT_SECRET_KEY", LEVEL_WARN, "JWT_SECRET_KEY 长度 < 32，建议使用更强的密钥")
    else:
        report.add("JWT_SECRET_KEY", LEVEL_OK, "已设置")

    # 加密密钥
    if not settings.ENCRYPTION_PASSWORD:
        report.add("ENCRYPTION_PASSWORD", LEVEL_WARN, "未设置加密密码，敏感字段将无法加密")
    else:
        report.add("ENCRYPTION_PASSWORD", LEVEL_OK, "已设置")

    # CORS 生产环境不应为空或通配
    if not settings.CORS_ORIGINS:
        report.add("CORS_ORIGINS", LEVEL_WARN, "未设置 CORS_ORIGINS，跨域请求可能被拒绝")
    elif settings.CORS_ORIGINS.strip() == "*":
        report.add("CORS_ORIGINS", LEVEL_WARN, "CORS_ORIGINS=* 存在安全风险，建议限定来源")
    else:
        report.add("CORS_ORIGINS", LEVEL_OK, settings.CORS_ORIGINS)

    # 数据库 URL：生产环境不建议使用 SQLite
    if settings.DATABASE_URL.startswith("sqlite"):
        report.add("DATABASE_URL", LEVEL_WARN, "使用 SQLite，生产环境建议切换到 Postgres")
    else:
        report.add("DATABASE_URL", LEVEL_OK, settings.DATABASE_URL.split("@")[-1])

    # 生产日志建议 JSON
    if not settings.LOG_JSON:
        report.add("LOG_JSON", LEVEL_WARN, "LOG_JSON=false，生产环境建议启用结构化日志")
    else:
        report.add("LOG_JSON", LEVEL_OK, "结构化日志已启用")


def check_database(report: Report) -> None:
    try:
        from app.core.db_client import get_db_client
        db = get_db_client()
        rows = db.execute("SELECT 1")
        if rows is not None:
            report.add("database", LEVEL_OK, "数据库连接正常")
        else:
            report.add("database", LEVEL_FAIL, "数据库查询返回空")
    except Exception as e:
        report.add("database", LEVEL_FAIL, f"数据库连接失败: {e}")


def check_redis(report: Report) -> None:
    try:
        from app.core.config import get_settings
        settings = get_settings()
    except Exception as e:  # pragma: no cover
        report.add("redis", LEVEL_FAIL, f"无法加载配置: {e}")
        return

    # 仅当使用 redis 事件总线时视为必需
    required = settings.EVENT_BUS_BACKEND == "redis"
    try:
        from app.core.redis_client import get_redis_client
        client = get_redis_client()
        conn = client.get_connection()
        conn.ping()
        # 检测是否为 fakeredis（降级）
        cls_name = type(conn).__name__.lower()
        if "fake" in cls_name:
            level = LEVEL_FAIL if required else LEVEL_WARN
            report.add("redis", level, "当前使用 fakeredis（内存降级），非真实 Redis")
        else:
            report.add("redis", LEVEL_OK, "Redis 连接正常")
    except Exception as e:
        level = LEVEL_FAIL if required else LEVEL_WARN
        report.add("redis", level, f"Redis 连接失败: {e}")


def check_chromadb(report: Report) -> None:
    try:
        from app.core.config import get_settings
        settings = get_settings()
        if settings.VECTOR_BACKEND != "chroma":
            report.add("chromadb", LEVEL_OK, f"向量后端为 {settings.VECTOR_BACKEND}，跳过 ChromaDB 检查")
            return
        from app.core.chromadb_client import get_chromadb_client
        client = get_chromadb_client()
        if client is None:
            report.add("chromadb", LEVEL_FAIL, "ChromaDB 客户端不可用（数据损坏或未安装）")
        else:
            report.add("chromadb", LEVEL_OK, "ChromaDB 可用")
    except Exception as e:
        report.add("chromadb", LEVEL_FAIL, f"ChromaDB 检查失败: {e}")


def check_otel(report: Report) -> None:
    try:
        from app.core.config import get_settings
        settings = get_settings()
    except Exception as e:  # pragma: no cover
        report.add("otel", LEVEL_WARN, f"无法加载配置: {e}")
        return

    if not settings.ENABLE_TRACING:
        report.add("otel", LEVEL_OK, "Tracing 未启用，跳过")
        return

    endpoint = settings.OTLP_ENDPOINT or "http://localhost:4317"
    # 解析 host:port 做 TCP 连通性探测
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(endpoint if "://" in endpoint else f"http://{endpoint}")
    host = parsed.hostname or "localhost"
    port = parsed.port or 4317
    try:
        with socket.create_connection((host, port), timeout=3):
            report.add("otel", LEVEL_OK, f"OTLP collector 可达 ({host}:{port})")
    except OSError as e:
        report.add("otel", LEVEL_WARN, f"OTLP collector 不可达 ({host}:{port}): {e}")


def check_dependencies(report: Report) -> None:
    """检查关键依赖是否已安装。"""
    required = ["fastapi", "pydantic", "sqlalchemy", "httpx", "uvicorn"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        report.add("dependencies", LEVEL_FAIL, f"缺失关键依赖: {', '.join(missing)}")
    else:
        report.add("dependencies", LEVEL_OK, f"关键依赖齐备 ({len(required)} 项)")


CHECKS: List[Callable[[Report], None]] = [
    check_env_vars,
    check_database,
    check_redis,
    check_chromadb,
    check_otel,
    check_dependencies,
]


# ============================================================
# 主流程
# ============================================================

def run_checks() -> Report:
    report = Report()
    for check in CHECKS:
        try:
            check(report)
        except Exception as e:  # pragma: no cover - 单项检查崩溃不应中断全局
            report.add(check.__name__, LEVEL_FAIL, f"检查异常: {e}")
    return report


_ICON = {LEVEL_OK: "✓", LEVEL_WARN: "⚠", LEVEL_FAIL: "✗"}


def print_report(report: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(
            {"results": [r.__dict__ for r in report.results]},
            ensure_ascii=False, indent=2,
        ))
        return

    print("\n" + "=" * 60)
    print("生产部署预检 (pre_deploy_check)")
    print("=" * 60)
    if _in_docker():
        print("环境: 容器 (Docker/K8s)")
    else:
        print("环境: 本地/主机")
    print("-" * 60)
    for r in report.results:
        icon = _ICON.get(r.level, "?")
        print(f"  {icon} [{r.level:<4}] {r.name}: {r.message}")
    print("-" * 60)
    print(f"合计: {len(report.results)} 项 | "
          f"FAIL {len(report.failed)} | WARN {len(report.warned)}")
    print("=" * 60 + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="生产部署配置预检")
    parser.add_argument("--force", action="store_true",
                        help="强制模式：即使存在 FAIL 也返回退出码 0（仅告警）")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="以 JSON 格式输出结果")
    args = parser.parse_args(argv)

    report = run_checks()
    print_report(report, args.as_json)

    if report.failed and not args.force:
        if not args.as_json:
            print(f"预检失败：{len(report.failed)} 个致命问题。使用 --force 可强制继续。")
        return 1
    if report.failed and args.force:
        if not args.as_json:
            print("--force 已启用：忽略致命问题，继续部署（风险自负）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
