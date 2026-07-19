"""
结构化日志模块（structlog）

提供 JSON 结构化日志（生产）或彩色控制台日志（开发）。
在 main.py 启动时调用 setup_logging() 初始化。
"""
import logging
import sys
from typing import Optional


def setup_logging(
    log_level: str = "INFO",
    json_logs: bool = False,
    service_name: str = "agent-memory",
) -> None:
    """
    初始化 structlog 结构化日志。

    Args:
        log_level: 日志级别（DEBUG/INFO/WARNING/ERROR）
        json_logs: True 输出 JSON 格式（生产），False 输出彩色控制台格式（开发）
        service_name: 服务名称，注入到每条日志的 context 中
    """
    try:
        import structlog
    except ImportError:
        # structlog 未安装时回退到标准 logging
        logging.basicConfig(
            level=getattr(logging, log_level.upper(), logging.INFO),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        return

    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        structlog.processors.StackInfoRenderer(),
    ]

    if json_logs:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 降低第三方库日志级别，减少噪音
    for noisy in ("uvicorn.access", "uvicorn.error", "chromadb", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None):
    """获取 structlog logger（兼容 stdlib logger 接口）"""
    try:
        import structlog
        return structlog.get_logger(name)
    except ImportError:
        return logging.getLogger(name)
