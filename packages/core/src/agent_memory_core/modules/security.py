"""
Security — Input validation, SQL injection prevention, XSS protection, rate limiting.

Pure validation logic extracted from backend sql_safety.py and security_service.py.
No DB-backed audit logging (belongs to Server layer with RelationalStore).

Usage:
    mgr = SecurityManager()
    is_safe, reason = mgr.validate_sql("SELECT * FROM users WHERE id = 1")
    check = mgr.security_check("user input text")
    limiter = mgr.rate_limiter
    limiter.check("api_call", max_requests=100)
"""

import hashlib
import hmac
import logging
import math
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# SQL Safety Constants
# ─────────────────────────────────────────────────────────────────

ALLOWED_STATEMENTS: Set[str] = {"SELECT", "INSERT", "UPDATE"}

FORBIDDEN_KEYWORDS: Set[str] = {
    "DROP", "DELETE", "ALTER", "TRUNCATE", "EXEC", "EXECUTE",
    "CREATE", "GRANT", "REVOKE", "MERGE", "COMMIT", "ROLLBACK",
    "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX",
}

FORBIDDEN_PATTERNS: List[str] = [
    r";",              # multi-statement separator
    r"--",             # line comment
    r"/\*.*?\*/",      # block comment
    r"xp_",            # SQL Server extended stored procedure
    r"sp_",            # SQL Server stored procedure
    r"\bINTO\b.*\bOUTFILE\b",  # SELECT INTO OUTFILE
    r"\bLOAD\b",       # LOAD DATA
    r"\bOUTFILE\b",    # OUTFILE
]

_STATEMENT_RE = re.compile(r"^\s*(\w+)", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────
# SQL Injection Detection Constants
# ─────────────────────────────────────────────────────────────────

SQL_INJECTION_PATTERNS: List[str] = [
    r"(\b(union)\b.*\b(select|all)\b)",
    r"(\b(drop|truncate|alter|create|exec|execute)\b)",
    r"(--|/\*|\*/)",
    r"(;\s*(drop|delete|update|insert|alter))",
    r"(\b(or|and)\b\s+\d+\s*=\s*\d+)",
    r"(\b(or|and)\b\s+'[^']*'\s*=\s*'[^']*')",
    r"(\bxp_cmdshell\b)",
    r"(\bwaitfor\s+delay\b)",
    r"(\bbenchmark\s*\()",
    r"(\bsleep\s*\()",
]

SQL_INJECTION_REGEX = [re.compile(p, re.IGNORECASE) for p in SQL_INJECTION_PATTERNS]

SQL_FORBIDDEN_KEYWORDS_INJECTION: Set[str] = {
    "exec", "execute", "xp_cmdshell", "sp_", "master", "sysobjects",
    "syscolumns", "information_schema", "load_file", "outfile", "dumpfile",
    "benchmark", "sleep", "waitfor", "shutdown",
}


# ─────────────────────────────────────────────────────────────────
# XSS Detection Constants
# ─────────────────────────────────────────────────────────────────

XSS_PATTERNS: List[re.Pattern] = [
    re.compile(r'<script[^>]*>.*?</script>', re.IGNORECASE | re.DOTALL),
    re.compile(r'javascript:', re.IGNORECASE),
    re.compile(r'on\w+\s*=', re.IGNORECASE),
    re.compile(r'<iframe[^>]*>', re.IGNORECASE),
    re.compile(r'<object[^>]*>', re.IGNORECASE),
    re.compile(r'<embed[^>]*>', re.IGNORECASE),
    re.compile(r'eval\s*\(', re.IGNORECASE),
    re.compile(r'expression\s*\(', re.IGNORECASE),
]


# ─────────────────────────────────────────────────────────────────
# Threat Detection Result
# ─────────────────────────────────────────────────────────────────

@dataclass
class ThreatInfo:
    """Single threat found in input."""
    type: str  # "pattern_match", "forbidden_keyword", "char_frequency", "xss_pattern"
    description: str
    pattern_index: Optional[int] = None
    keyword: Optional[str] = None


@dataclass
class SafetyCheckResult:
    """Result of a comprehensive safety check."""
    is_safe: bool
    threats: List[ThreatInfo] = field(default_factory=list)
    sanitized_input: str = ""

    @property
    def threat_count(self) -> int:
        return len(self.threats)


# ─────────────────────────────────────────────────────────────────
# Rate Limiter (in-memory sliding window)
# ─────────────────────────────────────────────────────────────────

class RateLimiter:
    """Sliding window rate limiter — no Redis dependency.

    Uses in-memory deque with maxlen for auto-eviction.
    Suitable for single-process Core layer. Server layer may
    replace with Redis-backed rate limiting for multi-process.
    """

    def __init__(self):
        self._requests: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

    def check(
        self,
        key: str,
        max_requests: int = 100,
        window_seconds: int = 60,
    ) -> Dict[str, Any]:
        """Check rate limit for a key.

        Args:
            key: Rate limit key (e.g. workspace_id, IP address).
            max_requests: Max requests in window.
            window_seconds: Window duration in seconds.

        Returns:
            Dict with "allowed", "current_count", "remaining", etc.
        """
        now = time.time()
        window_start = now - window_seconds

        # Evict expired entries
        while self._requests[key] and self._requests[key][0] < window_start:
            self._requests[key].popleft()

        current_count = len(self._requests[key])

        if current_count >= max_requests:
            return {
                "allowed": False,
                "current_count": current_count,
                "max_requests": max_requests,
                "window_seconds": window_seconds,
                "retry_after": int(window_seconds - (now - self._requests[key][0])),
            }

        self._requests[key].append(now)

        return {
            "allowed": True,
            "current_count": current_count + 1,
            "max_requests": max_requests,
            "window_seconds": window_seconds,
            "remaining": max_requests - current_count - 1,
        }

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        if key in self._requests:
            self._requests[key].clear()


# ─────────────────────────────────────────────────────────────────
# CSRF Token
# ─────────────────────────────────────────────────────────────────

def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token."""
    return secrets.token_urlsafe(32)


def validate_csrf_token(token: str, expected_token: str) -> bool:
    """Validate CSRF token using HMAC comparison (timing-safe)."""
    if not token or not expected_token:
        return False
    return hmac.compare_digest(token, expected_token)


# ─────────────────────────────────────────────────────────────────
# SecurityManager — unified security API
# ─────────────────────────────────────────────────────────────────

class SecurityManager:
    """Core-layer security manager.

    Provides:
    - SQL safety validation (whitelist/blacklist/pattern/quote matching)
    - SQL injection detection (pattern + keyword + frequency)
    - XSS detection and HTML sanitization
    - Input sanitization (control chars, length limits)
    - Rate limiting (in-memory sliding window)
    - Comprehensive safety check (combines all detectors)
    """

    def __init__(
        self,
        allowed_statements: Optional[Set[str]] = None,
        readonly: bool = False,
    ):
        self._allowed_statements = allowed_statements or ALLOWED_STATEMENTS
        self._readonly = readonly
        self._rate_limiter = RateLimiter()

    @property
    def rate_limiter(self) -> RateLimiter:
        return self._rate_limiter

    # ── SQL Safety ───────────────────────────────────────────────

    def validate_sql(
        self,
        sql: str,
        allowed_statements: Optional[Set[str]] = None,
        readonly: bool = False,
    ) -> Tuple[bool, str]:
        """Validate SQL statement safety.

        Checks:
        1. Non-empty
        2. Statement type in whitelist
        3. readonly → only SELECT allowed
        4. No forbidden keywords
        5. No forbidden patterns (multi-statement, comments, stored proc)
        6. Balanced quotes

        Args:
            sql: SQL statement to validate.
            allowed_statements: Override default whitelist.
            readonly: Force SELECT-only mode.

        Returns:
            (is_safe, reason) tuple.
        """
        if not sql or not sql.strip():
            return False, "SQL statement is empty"

        sql_clean = sql.strip()
        sql_upper = sql_clean.upper()

        allowed = allowed_statements or self._allowed_statements
        stmt_type = self._extract_statement_type(sql_clean)
        if stmt_type is None:
            return False, "Cannot identify SQL statement type"

        if stmt_type not in allowed:
            return False, f"Disallowed SQL operation type: {stmt_type}"

        if readonly and stmt_type != "SELECT":
            return False, "Read-only mode only allows SELECT queries"

        # Forbidden keywords
        sql_words = re.findall(r"\b[A-Z_]+\b", sql_upper)
        for word in sql_words:
            if word in FORBIDDEN_KEYWORDS:
                return False, f"SQL contains forbidden keyword: {word}"

        # Forbidden patterns
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, sql_upper):
                return False, f"SQL contains forbidden pattern: {pattern}"

        # Quote balance
        if sql_clean.count("'") % 2 != 0:
            return False, "Unbalanced single quotes"
        if sql_clean.count('"') % 2 != 0:
            return False, "Unbalanced double quotes"

        return True, "SQL safety check passed"

    def extract_literal_params(self, sql: str) -> Tuple[str, Tuple]:
        """Replace single-quoted literals with ? placeholders.

        Lightweight parameterization helper — only handles
        top-level single-quoted string literals.

        Args:
            sql: Raw SQL with string literals.

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

    def is_select_only(self, sql: str) -> bool:
        """Check if SQL is a SELECT-only query."""
        return self._extract_statement_type(sql) == "SELECT"

    # ── SQL Injection Detection ──────────────────────────────────

    def detect_sql_injection(self, input_string: str) -> SafetyCheckResult:
        """Detect SQL injection patterns in input.

        Args:
            input_string: Input to check.

        Returns:
            SafetyCheckResult with threats list.
        """
        if not input_string:
            return SafetyCheckResult(is_safe=True)

        threats: List[ThreatInfo] = []

        # Pattern matching
        for i, pattern in enumerate(SQL_INJECTION_REGEX):
            if pattern.search(input_string):
                threats.append(ThreatInfo(
                    type="pattern_match",
                    description=f"Matched SQL injection pattern {i+1}",
                    pattern_index=i,
                ))

        # Forbidden keywords
        lower_input = input_string.lower()
        for keyword in SQL_FORBIDDEN_KEYWORDS_INJECTION:
            if keyword in lower_input:
                threats.append(ThreatInfo(
                    type="forbidden_keyword",
                    description=f"Contains forbidden SQL keyword: {keyword}",
                    keyword=keyword,
                ))

        # Character frequency anomaly
        if input_string.count("'") > 5 or input_string.count('"') > 5:
            threats.append(ThreatInfo(
                type="char_frequency",
                description="Abnormal quote character frequency",
            ))
        if input_string.count(";") > 3:
            threats.append(ThreatInfo(
                type="char_frequency",
                description="Abnormal semicolon frequency",
            ))

        return SafetyCheckResult(
            is_safe=len(threats) == 0,
            threats=threats,
        )

    # ── XSS Detection ────────────────────────────────────────────

    def detect_xss(self, input_string: str) -> SafetyCheckResult:
        """Detect XSS attack patterns in input.

        Args:
            input_string: Input to check.

        Returns:
            SafetyCheckResult with threats list.
        """
        if not input_string:
            return SafetyCheckResult(is_safe=True)

        threats: List[ThreatInfo] = []
        for i, pattern in enumerate(XSS_PATTERNS):
            if pattern.search(input_string):
                threats.append(ThreatInfo(
                    type="xss_pattern",
                    description=f"Matched XSS pattern {i+1}",
                    pattern_index=i,
                ))

        return SafetyCheckResult(
            is_safe=len(threats) == 0,
            threats=threats,
        )

    # ── Input Sanitization ───────────────────────────────────────

    def sanitize_input(self, input_string: str, max_length: int = 10000) -> str:
        """Sanitize input string.

        - Truncates to max_length
        - Removes control characters (except common whitespace)

        Args:
            input_string: Raw input.
            max_length: Maximum allowed length.

        Returns:
            Sanitized string.
        """
        if not input_string:
            return ""
        result = input_string[:max_length]
        result = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', result)
        return result

    def sanitize_html(self, input_string: str) -> str:
        """Sanitize HTML content — remove dangerous tags and attributes.

        Args:
            input_string: HTML content.

        Returns:
            Cleaned HTML with dangerous elements removed.
        """
        if not input_string:
            return ""

        result = re.sub(r'<script[^>]*>.*?</script>', '', input_string,
                        flags=re.IGNORECASE | re.DOTALL)
        result = re.sub(r'on\w+\s*=\s*["\'][^"\']*["\']', '', result,
                        flags=re.IGNORECASE)
        result = re.sub(r'javascript:', '', result, flags=re.IGNORECASE)
        result = result.replace('<', '&lt;').replace('>', '&gt;')
        return result

    # ── Comprehensive Safety Check ───────────────────────────────

    def security_check(self, input_string: str) -> SafetyCheckResult:
        """Comprehensive safety check combining all detectors.

        1. SQL injection detection
        2. XSS detection
        3. Input sanitization

        Args:
            input_string: Input to check.

        Returns:
            SafetyCheckResult with all threats and sanitized input.
        """
        threats: List[ThreatInfo] = []

        # SQL injection
        sql_result = self.detect_sql_injection(input_string)
        for t in sql_result.threats:
            t.type = f"sql_{t.type}"
            threats.append(t)

        # XSS
        xss_result = self.detect_xss(input_string)
        for t in xss_result.threats:
            t.type = f"xss_{t.type}"
            threats.append(t)

        # Sanitize
        sanitized = self.sanitize_input(input_string)

        return SafetyCheckResult(
            is_safe=len(threats) == 0,
            threats=threats,
            sanitized_input=sanitized,
        )

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _extract_statement_type(sql: str) -> Optional[str]:
        """Extract the first keyword (SELECT/INSERT/UPDATE/...) from SQL."""
        match = _STATEMENT_RE.match(sql)
        if match:
            return match.group(1).upper()
        return None
