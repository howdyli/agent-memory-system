"""
SQL 安全校验工具模块

为 NL2SQL 及任意用户提供的 SQL 提供白名单/黑名单/多语句/EXPLAIN 预检查等安全层。
"""
import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 允许的 SQL 语句类型（白名单）
ALLOWED_STATEMENTS = {"SELECT", "INSERT", "UPDATE"}

# 禁止的 SQL 关键字/操作（黑名单）
FORBIDDEN_KEYWORDS = {
    "DROP", "DELETE", "ALTER", "TRUNCATE", "EXEC", "EXECUTE",
    "CREATE", "GRANT", "REVOKE", "MERGE", "COMMIT", "ROLLBACK",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
}

# 禁止的特殊模式（大小写不敏感）
FORBIDDEN_PATTERNS = [
    r";",              # 多语句分隔
    r"--",             # 行注释
    r"/\*.*?\*/",      # 块注释
    r"xp_",            # SQL Server 扩展存储过程
    r"sp_",            # SQL Server 存储过程
    r"\bINTO\b.*\bOUTFILE\b",  # SELECT INTO OUTFILE
    r"\bLOAD\b",       # LOAD DATA
    r"\bOUTFILE\b",    # OUTFILE
]

# 用于提取首个 SQL 语句类型的正则
_STATEMENT_RE = re.compile(r"^\s*(\w+)", re.IGNORECASE)


def _extract_statement_type(sql: str) -> Optional[str]:
    """提取 SQL 语句的首个关键字（SELECT/INSERT/UPDATE/...）。"""
    match = _STATEMENT_RE.match(sql)
    if match:
        return match.group(1).upper()
    return None


def validate_sql_safety(
    sql: str,
    allowed_statements: Optional[set] = None,
    readonly: bool = False,
) -> Tuple[bool, str]:
    """
    校验 SQL 语句的安全性。

    校验逻辑：
    1. 非空检查
    2. 解析语句类型，必须在白名单内
    3. readonly=True 时只允许 SELECT
    4. 检查禁止关键字
    5. 检查禁止的特殊模式（多语句、注释、存储过程等）
    6. 检查引号匹配

    Args:
        sql: 待校验的 SQL 语句
        allowed_statements: 允许的操作类型集合，默认 ALLOWED_STATEMENTS
        readonly: 是否只读（强制 SELECT）

    Returns:
        (is_safe, reason)
    """
    if not sql or not sql.strip():
        return False, "SQL 语句为空"

    sql_clean = sql.strip()
    sql_upper = sql_clean.upper()

    # 1. 语句类型白名单检查
    allowed = allowed_statements or ALLOWED_STATEMENTS
    stmt_type = _extract_statement_type(sql_clean)
    if stmt_type is None:
        return False, "无法识别 SQL 语句类型"

    if stmt_type not in allowed:
        return False, f"不允许的 SQL 操作类型: {stmt_type}"

    # 2. 只读模式强制 SELECT
    if readonly and stmt_type != "SELECT":
        return False, "只读模式下只允许 SELECT 查询"

    # 3. 禁止关键字检查（按单词边界）
    sql_words = re.findall(r"\b[A-Z_]+\b", sql_upper)
    for word in sql_words:
        if word in FORBIDDEN_KEYWORDS:
            return False, f"SQL 包含禁止关键字: {word}"

    # 4. 禁止的特殊模式检查
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, sql_upper):
            return False, f"SQL 包含禁止模式: {pattern}"

    # 5. 引号匹配检查（防止字符串截断注入）
    single_quotes = sql_clean.count("'")
    double_quotes = sql_clean.count('"')
    if single_quotes % 2 != 0:
        return False, "单引号不匹配"
    if double_quotes % 2 != 0:
        return False, "双引号不匹配"

    return True, "SQL 安全校验通过"


def pre_check_sql(sql: str, db) -> Tuple[bool, str]:
    """
    使用 SQLite EXPLAIN QUERY PLAN 对 SQL 进行执行前预检查。

    Args:
        sql: 已通过安全校验的 SQL
        db: 数据库客户端（需实现 execute 方法）

    Returns:
        (success, message)
    """
    try:
        # SQLite 支持 EXPLAIN QUERY PLAN，不会真正执行数据变更
        explain_sql = f"EXPLAIN QUERY PLAN {sql}"
        db.execute(explain_sql)
        return True, "SQL 执行计划预检查通过"
    except Exception as e:
        logger.warning(f"SQL EXPLAIN 预检查失败: {sql[:100]}... 错误: {e}")
        return False, f"SQL 执行计划预检查失败: {str(e)}"


def extract_literal_params(sql: str) -> Tuple[str, Tuple]:
    """
    尝试将 SQL 中的单引号字符串字面值替换为 ? 占位符并返回参数。

    注意：这是轻量级参数化辅助，仅处理顶层的单引号字符串字面值，
    不处理标识符、数值、NULL、布尔值等。

    Args:
        sql: 原始 SQL

    Returns:
        (parameterized_sql, params_tuple)
    """
    params = []
    result = []
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]
        if ch == "'":
            # 读取字符串字面值，处理连续两个单引号转义
            literal = []
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        literal.append("'")
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    literal.append(sql[i])
                    i += 1
            params.append("".join(literal))
            result.append("?")
        else:
            result.append(ch)
            i += 1

    return "".join(result), tuple(params)


def is_select_only(sql: str) -> bool:
    """判断 SQL 是否仅为 SELECT 查询。"""
    stmt_type = _extract_statement_type(sql)
    return stmt_type == "SELECT"
