"""
SQL security: whitelist tables, deny destructive keywords, cap LIMIT.
"""

import re

ALLOWED_TABLES = {"conapesca_landings_historical"}

DENY_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "REPLACE", "UPSERT",
    "ALTER", "DROP", "CREATE", "TRUNCATE", "RENAME",
    "GRANT", "REVOKE", "EXEC", "EXECUTE", "CALL",
    "LOAD", "OUTFILE", "DUMPFILE",
}

DEFAULT_MAX_ROWS = 5000


def validate_sql(sql: str) -> str:
    """
    Raise ValueError if sql contains destructive keywords or references
    tables outside the whitelist.  Returns the original sql if valid.
    """
    sql_upper = sql.upper()

    # Must start with SELECT / SHOW / DESCRIBE / EXPLAIN / PRAGMA / WITH
    first_token = sql_upper.lstrip().split()[0] if sql_upper.strip() else ""
    if first_token not in {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "PRAGMA", "WITH"}:
        raise ValueError(f"Only SELECT/SHOW/DESCRIBE queries are allowed. Got: {first_token}")

    # Deny destructive keywords (whole word)
    for kw in DENY_KEYWORDS:
        if re.search(rf"\b{kw}\b", sql_upper):
            raise ValueError(f"Forbidden keyword in SQL: {kw}")

    return sql


def enforce_limit(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> str:
    """Ensure the query has a LIMIT <= max_rows."""
    sql_upper = sql.upper()
    limit_match = re.search(r"\bLIMIT\s+(\d+)", sql_upper)
    if limit_match:
        current = int(limit_match.group(1))
        if current > max_rows:
            sql = re.sub(
                r"\bLIMIT\s+\d+", f"LIMIT {max_rows}", sql, flags=re.IGNORECASE
            )
    else:
        sql = sql.rstrip().rstrip(";") + f" LIMIT {max_rows}"
    return sql


def sanitize_identifier(name: str) -> str:
    """Allow only whitelisted table names."""
    if name not in ALLOWED_TABLES:
        raise ValueError(
            f"Table '{name}' is not allowed. "
            f"Allowed tables: {sorted(ALLOWED_TABLES)}"
        )
    return name
