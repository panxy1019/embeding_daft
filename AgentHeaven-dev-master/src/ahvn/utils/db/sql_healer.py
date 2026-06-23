"""SQL healing utilities for schema-aware SQL recovery."""

from __future__ import annotations

__all__ = [
    "SchemaIndex",
    "SQLHealer",
    "create_sql_healer",
    "resolve_sql_healing_options",
]

import json
import re
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple, Mapping

from ..basic.log_utils import get_logger
from ..deps import deps
from .sqlglot_runtime import (
    get_sqlglot,
    sa_dialect_to_sqlglot,
    resolve_render_dialect,
)

logger = get_logger(__name__)

SchemaIndex = Dict[str, List[str]]
SchemaLoader = Callable[[], Mapping[str, Sequence[str]] | Dict[str, Any]]

_DEFAULT_SQL_HEALING_OPTIONS: Dict[str, Any] = {
    "aggressiveness": "balanced",
    "prefer_backticks": True,
}

_AGGRESSIVENESS_PROFILES: Dict[str, Dict[str, Any]] = {
    "off": {
        "enable_fuzzy": False,
        "min_score": 101.0,
        "min_gap": 100.0,
        "max_repairs": 0,
    },
    "conservative": {
        "enable_fuzzy": True,
        "min_score": 90.0,
        "min_gap": 8.0,
        "max_repairs": 3,
    },
    "balanced": {
        "enable_fuzzy": True,
        "min_score": 72.0,
        "min_gap": 2.0,
        "max_repairs": 6,
    },
    "aggressive": {
        "enable_fuzzy": True,
        "min_score": 72.0,
        "min_gap": 0.0,
        "max_repairs": 12,
    },
}


def resolve_sql_healing_options(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize SQL healing options from config."""
    cfg = dict(_DEFAULT_SQL_HEALING_OPTIONS)
    if isinstance(config, dict):
        cfg.update(config)

    aggressiveness = str(cfg.get("aggressiveness", "balanced")).strip().lower()
    if aggressiveness not in _AGGRESSIVENESS_PROFILES:
        aggressiveness = "balanced"

    return {
        "aggressiveness": aggressiveness,
        "prefer_backticks": bool(cfg.get("prefer_backticks", True)),
    }


class SQLHealer:
    """Heal malformed SQL strings with schema-grounded typo correction."""

    def __init__(
        self,
        target_dialect: str,
        *,
        schema_loader: Optional[SchemaLoader] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self.target_dialect = target_dialect
        self.schema_loader = schema_loader
        self.options = resolve_sql_healing_options(config)

    def heal(
        self,
        query: Any,
        *,
        dialect: Optional[str] = None,
        schema_index: Optional[Mapping[str, Sequence[str]] | Dict[str, Any]] = None,
        prefer_backticks: Optional[bool] = None,
    ) -> str:
        """Heal SQL text and return prettified SQL when SQLGlot is available.

        Args:
            query: Raw SQL text.
            dialect: Optional source parse dialect.
            schema_index: Optional schema snapshot for identifier grounding.
            prefer_backticks: Optional render override. If None, uses healer
                config; if True, backticks are preferred only for dialects that
                support them.
        """
        text = self._normalize_query_text(query)
        if not text:
            return ""

        if not deps.check("sqlglot"):
            return text

        sg = get_sqlglot()
        source_dialect = sa_dialect_to_sqlglot(dialect or self.target_dialect)
        expr = self._parse_sql_candidates(text, source_dialect, sg)
        if expr is None:
            return text

        profile = _AGGRESSIVENESS_PROFILES[self.options["aggressiveness"]]
        if profile["enable_fuzzy"]:
            tables, columns_by_table = self._load_schema_snapshot(schema_index=schema_index)
            if tables:
                self._repair_identifiers(expr, tables, columns_by_table, profile, sg)

        return self._render(expr, sg, prefer_backticks=prefer_backticks)

    @staticmethod
    def _normalize_query_text(query: Any) -> str:
        if query is None:
            return ""
        text = query.decode("utf-8", errors="ignore") if isinstance(query, bytes) else str(query)
        text = text.strip()
        if not text:
            return ""

        # Unwrap JSON-wrapped SQL payloads.
        for _ in range(2):
            try:
                loaded = json.loads(text)
            except Exception:
                break
            if isinstance(loaded, str):
                text = loaded.strip()
                continue
            break

        # Unwrap single wrapped SQL string payloads: 'SELECT ...'
        if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
            inner = text[1:-1].strip()
            if re.search(r"\b(select|insert|update|delete|create|drop|alter|with)\b", inner, re.IGNORECASE):
                text = inner

        return text.strip()

    @staticmethod
    def _candidate_sql_variants(text: str) -> List[str]:
        variants = [text]
        unescaped = text.replace('\\"', '"').replace("\\'", "'")
        unescaped = re.sub(r'\\{2,}"', '"', unescaped)
        unescaped = re.sub(r"\\{2,}'", "'", unescaped)
        if unescaped != text:
            variants.append(unescaped)
        return variants

    def _parse_sql_candidates(self, text: str, source_dialect: str, sg) -> Optional[Any]:
        for candidate in self._candidate_sql_variants(text):
            try:
                return sg.parse_one(candidate, dialect=source_dialect)
            except Exception:
                continue
        return None

    @staticmethod
    def _normalize_schema_index(
        schema_index: Optional[Mapping[str, Sequence[str]] | Dict[str, Any]],
    ) -> Dict[str, Set[str]]:
        if not schema_index:
            return {}

        table_map: Any = schema_index
        if isinstance(schema_index, dict) and "tables" in schema_index and isinstance(schema_index.get("tables"), dict):
            table_map = schema_index["tables"]
        if not isinstance(table_map, dict):
            raise ValueError("schema_index must be a mapping of table -> columns, or {'tables': {...}}")

        normalized: Dict[str, Set[str]] = {}
        for raw_table, raw_columns in table_map.items():
            table_name = str(raw_table).strip()
            if not table_name:
                continue
            if raw_columns is None:
                normalized[table_name] = set()
                continue
            if isinstance(raw_columns, str):
                items = [raw_columns]
            elif isinstance(raw_columns, dict):
                items = list(raw_columns.keys())
            else:
                try:
                    items = list(raw_columns)
                except TypeError as e:
                    raise ValueError(f"schema_index[{table_name!r}] must be iterable of column names") from e
            normalized[table_name] = {str(item).strip() for item in items if str(item).strip()}
        return normalized

    def _load_schema_snapshot(
        self,
        *,
        schema_index: Optional[Mapping[str, Sequence[str]] | Dict[str, Any]] = None,
    ) -> Tuple[Set[str], Dict[str, Set[str]]]:
        if schema_index is None and self.schema_loader is None:
            return set(), {}
        try:
            raw_index = schema_index if schema_index is not None else self.schema_loader()
            columns_by_table = self._normalize_schema_index(raw_index)
        except Exception as e:
            logger.warning(f"SQL schema snapshot failed during healing: {e}")
            return set(), {}

        normalized_tables: Set[str] = set(columns_by_table.keys())
        return normalized_tables, columns_by_table

    def _repair_identifiers(
        self,
        expr: Any,
        tables: Set[str],
        columns_by_table: Dict[str, Set[str]],
        profile: Dict[str, Any],
        sg,
    ) -> None:
        exp = sg.exp
        repairs = 0
        max_repairs = int(profile["max_repairs"])

        alias_to_table: Dict[str, str] = {}
        referenced_tables: Set[str] = set()

        for table_node in list(expr.find_all(exp.Table)):
            table_name = table_node.name
            if not table_name:
                continue
            fixed = self._ground_identifier(table_name, tables, profile)
            if fixed is not None and fixed != table_name and repairs < max_repairs:
                table_node.set("this", exp.to_identifier(fixed))
                table_name = fixed
                repairs += 1
            referenced_tables.add(table_name)
            alias = table_node.alias_or_name
            if alias:
                alias_to_table[str(alias)] = table_name

        if repairs >= max_repairs:
            return

        if referenced_tables:
            column_universe: Set[str] = set()
            for t in referenced_tables:
                column_universe.update(columns_by_table.get(t, set()))
        else:
            column_universe = set()
            for cols in columns_by_table.values():
                column_universe.update(cols)

        for col_node in list(expr.find_all(exp.Column)):
            col_name = col_node.name
            if not col_name or col_name == "*":
                continue

            table_qualifier = col_node.table
            candidates = column_universe
            if table_qualifier:
                real_table = alias_to_table.get(table_qualifier, table_qualifier)
                if real_table not in columns_by_table:
                    grounded_table = self._ground_identifier(real_table, tables, profile)
                    if grounded_table:
                        real_table = grounded_table
                candidates = columns_by_table.get(real_table, set())

            fixed_col = self._ground_identifier(col_name, candidates, profile)
            if fixed_col is not None and fixed_col != col_name:
                col_node.set("this", exp.to_identifier(fixed_col))
                repairs += 1
                if repairs >= max_repairs:
                    break

    def _ground_identifier(self, raw_name: str, candidates: Sequence[str], profile: Dict[str, Any]) -> Optional[str]:
        if not raw_name or not candidates:
            return None

        candidate_list = [str(c) for c in candidates]
        candidate_set = set(candidate_list)
        if raw_name in candidate_set:
            return raw_name

        lowered = [c for c in candidate_list if c.lower() == raw_name.lower()]
        if len(lowered) == 1:
            return lowered[0]

        if not profile["enable_fuzzy"]:
            return None

        scored = self._score_candidates(raw_name, candidate_list)
        if not scored:
            return None
        (best_name, best_score), second = scored[0], scored[1] if len(scored) > 1 else (None, 0.0)

        if best_score < float(profile["min_score"]):
            return None
        if second[0] is not None and (best_score - second[1]) < float(profile["min_gap"]):
            return None
        return best_name

    @staticmethod
    def _score_candidates(name: str, candidates: List[str]) -> List[Tuple[str, float]]:
        if not candidates:
            return []

        if deps.check("rapidfuzz"):
            try:
                rf = deps.load("rapidfuzz")
                extracted = rf.process.extract(name, candidates, scorer=rf.fuzz.WRatio, limit=2)
                return [(choice, float(score)) for choice, score, _ in extracted]
            except Exception:
                pass

        ranked = sorted(
            ((c, SequenceMatcher(None, name, c).ratio() * 100.0) for c in candidates),
            key=lambda item: item[1],
            reverse=True,
        )
        return ranked[:2]

    def _render(self, expr: Any, sg, *, prefer_backticks: Optional[bool] = None) -> str:
        use_backticks = bool(self.options.get("prefer_backticks", True)) if prefer_backticks is None else bool(prefer_backticks)
        target = resolve_render_dialect(
            self.target_dialect,
            prefer_backticks=use_backticks,
        )

        try:
            rendered = expr.sql(
                dialect=target,
                identify=True,
                pretty=True,
                comments=True,
            )
            return rendered.strip()
        except Exception as e:
            logger.warning(f"Failed to render healed SQL via SQLGlot: {e}")
            try:
                return expr.sql(dialect=target).strip()
            except Exception:
                return str(expr).strip()


def create_sql_healer(
    target_dialect: str,
    *,
    schema_loader: Optional[SchemaLoader] = None,
    config: Optional[Dict[str, Any]] = None,
) -> SQLHealer:
    """Create a SQLHealer with normalized configuration."""
    return SQLHealer(target_dialect, schema_loader=schema_loader, config=config)
