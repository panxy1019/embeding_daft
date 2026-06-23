"""Shared SQLGlot runtime helpers for DB SQL utilities."""

from __future__ import annotations

__all__ = [
    "SA_TO_SQLGLOT",
    "SQLGLOT_TO_SA",
    "sa_dialect_to_sqlglot",
    "sqlglot_dialect_to_sa",
    "get_sqlglot",
    "dialect_prefers_backticks",
    "dialect_supports_backticks",
    "resolve_render_dialect",
    "rewrite_cte_column_aliases",
]

from functools import lru_cache
from typing import Any, Dict, Union

from ..deps import deps

SA_TO_SQLGLOT: Dict[str, str] = {
    "postgresql": "postgres",
    "mssql": "tsql",
}
"""SQLAlchemy dialect name -> SQLGlot dialect name."""

SQLGLOT_TO_SA: Dict[str, str] = {v: k for k, v in SA_TO_SQLGLOT.items()}
"""SQLGlot dialect name -> SQLAlchemy dialect name."""

_SQLGLOT = None
_BACKTICK = "`"


@lru_cache(maxsize=128)
def _get_dialect_class(dialect: str):
    sg = get_sqlglot()
    instance = sg.Dialect.get_or_raise(sa_dialect_to_sqlglot(dialect))
    return instance.__class__


def _identifier_tokens_prefer_backtick(tokens: list[Any]) -> list[Any]:
    preferred = [_BACKTICK]
    seen = {_BACKTICK, (_BACKTICK, _BACKTICK)}
    for token in tokens:
        key = token
        if isinstance(token, list):
            key = tuple(token)
        if key in seen:
            continue
        seen.add(key)
        preferred.append(token)
    return preferred


def sa_dialect_to_sqlglot(dialect: str) -> str:
    """Convert a SQLAlchemy dialect identifier to its SQLGlot equivalent."""
    return SA_TO_SQLGLOT.get(dialect, dialect)


def sqlglot_dialect_to_sa(dialect: str) -> str:
    """Convert a SQLGlot dialect identifier to its SQLAlchemy equivalent."""
    return SQLGLOT_TO_SA.get(dialect, dialect)


def get_sqlglot():
    """Load SQLGlot lazily via optional dependency manager."""
    global _SQLGLOT
    if _SQLGLOT is None:
        _SQLGLOT = deps.load("sqlglot")
    return _SQLGLOT


def dialect_prefers_backticks(dialect: str) -> bool:
    """Return True when the SQLGlot dialect emits backticks for identifiers."""
    try:
        dcls = _get_dialect_class(dialect)
        return dcls.IDENTIFIER_START == _BACKTICK and dcls.IDENTIFIER_END == _BACKTICK
    except Exception:
        return sa_dialect_to_sqlglot(dialect) in {"mysql", "hive"}


def dialect_supports_backticks(dialect: str) -> bool:
    """Return True if the SQLGlot dialect accepts backtick-delimited identifiers."""
    try:
        dcls = _get_dialect_class(dialect)
        identifiers = list(getattr(dcls.Tokenizer, "IDENTIFIERS", []) or [])
        for ident in identifiers:
            if ident == _BACKTICK:
                return True
            if isinstance(ident, (tuple, list)) and len(ident) >= 2:
                if ident[0] == _BACKTICK and ident[1] == _BACKTICK:
                    return True
        return dialect_prefers_backticks(dialect)
    except Exception:
        return False


@lru_cache(maxsize=128)
def resolve_render_dialect(dialect: str, prefer_backticks: bool = False) -> Union[str, type]:
    """Resolve SQLGlot render dialect (optionally forcing backticks via subclass)."""
    target = sa_dialect_to_sqlglot(dialect)
    if not prefer_backticks:
        return target
    if not dialect_supports_backticks(dialect):
        return target

    dcls = _get_dialect_class(dialect)
    if dcls.IDENTIFIER_START == _BACKTICK and dcls.IDENTIFIER_END == _BACKTICK:
        return target

    tokenizer_tokens = list(getattr(dcls.Tokenizer, "IDENTIFIERS", []) or [])
    tokenizer_cls = type(
        f"{dcls.__name__}BacktickTokenizer",
        (dcls.Tokenizer,),
        {"IDENTIFIERS": _identifier_tokens_prefer_backtick(tokenizer_tokens)},
    )
    dialect_cls = type(
        f"{dcls.__name__}BacktickDialect",
        (dcls,),
        {
            "Tokenizer": tokenizer_cls,
            "IDENTIFIER_START": _BACKTICK,
            "IDENTIFIER_END": _BACKTICK,
        },
    )
    return dialect_cls


def _dialect_supports_cte_columns(dialect: str) -> bool:
    """Return True if the dialect's generator can emit CTE column aliases."""
    try:
        gen_cls = _get_dialect_class(dialect).Generator
        return getattr(gen_cls, "SUPPORTS_TABLE_ALIAS_COLUMNS", True)
    except Exception:
        return True


def rewrite_cte_column_aliases(tree):
    """Rewrite ``WITH cte(a,b) AS (SELECT 1,2)`` -> ``WITH cte AS (SELECT 1 AS a, 2 AS b)``.

    SQLGlot silently drops CTE column aliases for dialects where
    ``SUPPORTS_TABLE_ALIAS_COLUMNS`` is False (e.g. sqlite, bigquery).
    This pre-pass inlines the aliases into the first SELECT so the
    generated SQL stays semantically equivalent.
    """
    sg = get_sqlglot()
    Alias, CTE, Select, Union = sg.exp.Alias, sg.exp.CTE, sg.exp.Select, sg.exp.Union

    for cte in tree.find_all(CTE):
        alias_node = cte.args.get("alias")
        if not alias_node:
            continue
        columns = alias_node.args.get("columns") or []
        if not columns:
            continue

        # Resolve the first SELECT (may be wrapped in a Union).
        body = cte.this
        first_select = body.this if isinstance(body, Union) else body
        if not isinstance(first_select, Select):
            continue

        exprs = first_select.expressions
        if len(exprs) != len(columns):
            continue  # mismatch - leave untouched

        # Inject AS aliases; existing aliases are overwritten to match CTE header.
        first_select.set(
            "expressions",
            [Alias(this=e.this if isinstance(e, Alias) else e, alias=c) for e, c in zip(exprs, columns)],
        )
        alias_node.set("columns", [])  # clear to prevent the "unsupported" warning

    return tree
