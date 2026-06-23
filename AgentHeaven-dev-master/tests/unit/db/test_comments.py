"""
Tests for Database comment features: tab_comment, col_comment, tab_comments,
set_tab_comment, set_col_comment, drop_tab_comment, drop_col_comment.

Covers both unsupported dialects (SQLite) and supported dialects (PostgreSQL etc.).
"""

import pytest
import sqlalchemy as sa
from ahvn.utils.db.base import Database
from ahvn.utils.db.base import DatabaseError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TABLE_NAME = "cmt_test"
_COL_A = "id"
_COL_B = "name"


def _create_test_table(db: Database, tab_name: str = _TABLE_NAME) -> None:
    """Create a simple test table for comment testing."""
    meta = sa.MetaData()
    sa.Table(
        tab_name,
        meta,
        sa.Column(_COL_A, sa.Integer, primary_key=True),
        sa.Column(_COL_B, sa.String(100)),
    )
    meta.create_all(db.engine)


# ---------------------------------------------------------------------------
# Tests on minimal_database (SQLite) — comments NOT supported
# ---------------------------------------------------------------------------


class TestCommentReadSQLite:
    """Reading comments on SQLite should gracefully return None / empty."""

    def test_tab_comment_returns_none(self, minimal_database):
        _create_test_table(minimal_database)
        assert minimal_database.tab_comment(_TABLE_NAME) is None

    def test_col_comment_returns_none(self, minimal_database):
        _create_test_table(minimal_database)
        assert minimal_database.col_comment(_TABLE_NAME, _COL_B) is None

    def test_tab_comments_returns_dict(self, minimal_database):
        """tab_comments should return a dict, even if comments are unavailable."""
        _create_test_table(minimal_database)
        result = minimal_database.tab_comments(_TABLE_NAME)
        assert isinstance(result, dict)
        # SQLite get_columns works but won't include comment keys
        # Either empty dict or dict with None values are acceptable
        if result:
            for v in result.values():
                assert v is None

    def test_supports_comments_false(self, minimal_database):
        assert minimal_database._supports_comments() is False


class TestCommentWriteSQLite:
    """Writing comments on SQLite should raise."""

    def test_set_tab_comment_raises(self, minimal_database):
        _create_test_table(minimal_database)
        with pytest.raises((DatabaseError, Exception)):
            minimal_database.set_tab_comment(_TABLE_NAME, "table comment")

    def test_set_col_comment_raises(self, minimal_database):
        _create_test_table(minimal_database)
        with pytest.raises((DatabaseError, Exception)):
            minimal_database.set_col_comment(_TABLE_NAME, _COL_B, "col comment")

    def test_drop_tab_comment_raises(self, minimal_database):
        _create_test_table(minimal_database)
        with pytest.raises((DatabaseError, Exception)):
            minimal_database.drop_tab_comment(_TABLE_NAME)

    def test_drop_col_comment_raises(self, minimal_database):
        _create_test_table(minimal_database)
        with pytest.raises((DatabaseError, Exception)):
            minimal_database.drop_col_comment(_TABLE_NAME, _COL_B)


# ---------------------------------------------------------------------------
# Tests on representative_database — covers SQLite + PostgreSQL
# These tests skip automatically if the database does not support comments.
# ---------------------------------------------------------------------------


class TestCommentLifecycle:
    """Full lifecycle: set → read → update → drop for table and column comments."""

    def test_set_and_get_tab_comment(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_lifecycle_tab")
        representative_database.set_tab_comment("cmt_lifecycle_tab", "initial table comment")
        assert representative_database.tab_comment("cmt_lifecycle_tab") == "initial table comment"

    def test_update_tab_comment(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_upd_tab")
        representative_database.set_tab_comment("cmt_upd_tab", "v1")
        assert representative_database.tab_comment("cmt_upd_tab") == "v1"

        representative_database.set_tab_comment("cmt_upd_tab", "v2")
        assert representative_database.tab_comment("cmt_upd_tab") == "v2"

    def test_drop_tab_comment(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_drop_tab")
        representative_database.set_tab_comment("cmt_drop_tab", "to be removed")
        assert representative_database.tab_comment("cmt_drop_tab") == "to be removed"

        representative_database.drop_tab_comment("cmt_drop_tab")
        assert representative_database.tab_comment("cmt_drop_tab") is None

    def test_set_and_get_col_comment(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_lifecycle_col")
        representative_database.set_col_comment("cmt_lifecycle_col", _COL_B, "user name col")
        assert representative_database.col_comment("cmt_lifecycle_col", _COL_B) == "user name col"

    def test_update_col_comment(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_upd_col")
        representative_database.set_col_comment("cmt_upd_col", _COL_B, "v1")
        assert representative_database.col_comment("cmt_upd_col", _COL_B) == "v1"

        representative_database.set_col_comment("cmt_upd_col", _COL_B, "v2")
        assert representative_database.col_comment("cmt_upd_col", _COL_B) == "v2"

    def test_drop_col_comment(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_drop_col")
        representative_database.set_col_comment("cmt_drop_col", _COL_B, "to be removed")
        assert representative_database.col_comment("cmt_drop_col", _COL_B) == "to be removed"

        representative_database.drop_col_comment("cmt_drop_col", _COL_B)
        assert representative_database.col_comment("cmt_drop_col", _COL_B) is None

    def test_tab_comments_all_columns(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_all_cols")
        representative_database.set_col_comment("cmt_all_cols", _COL_A, "primary key")
        representative_database.set_col_comment("cmt_all_cols", _COL_B, "display name")

        comments = representative_database.tab_comments("cmt_all_cols")
        assert comments[_COL_A] == "primary key"
        assert comments[_COL_B] == "display name"

    def test_unicode_comment(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_unicode")
        text = "用户表 — contains user data 🧑‍💻"
        representative_database.set_tab_comment("cmt_unicode", text)
        assert representative_database.tab_comment("cmt_unicode") == text

    def test_empty_string_comment(self, representative_database):
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_empty")
        representative_database.set_tab_comment("cmt_empty", "")
        # Empty string comment: result should be "" or None depending on dialect
        result = representative_database.tab_comment("cmt_empty")
        assert result is None or result == ""


class TestCommentEdgeCases:
    """Edge cases and error handling for comment methods."""

    def test_col_comment_nonexistent_column(self, representative_database):
        """col_comment on a non-existent column returns None."""
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_edge_1")
        assert representative_database.col_comment("cmt_edge_1", "nonexistent_col") is None

    def test_set_col_comment_nonexistent_column_raises(self, representative_database):
        """set_col_comment on a non-existent column raises."""
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_edge_2")
        with pytest.raises(DatabaseError):
            representative_database.set_col_comment("cmt_edge_2", "nonexistent_col", "bad")

    def test_drop_col_comment_nonexistent_column_raises(self, representative_database):
        """drop_col_comment on a non-existent column raises."""
        if not representative_database._supports_comments():
            pytest.skip("Dialect does not support comments")

        _create_test_table(representative_database, "cmt_edge_3")
        with pytest.raises(DatabaseError):
            representative_database.drop_col_comment("cmt_edge_3", "nonexistent_col")

    def test_tab_comment_nonexistent_table(self, representative_database):
        """tab_comment on a non-existent table returns None (no crash)."""
        assert representative_database.tab_comment("totally_nonexistent_table_xyz") is None

    def test_col_comment_nonexistent_table(self, representative_database):
        """col_comment on a non-existent table returns None (no crash)."""
        assert representative_database.col_comment("totally_nonexistent_table_xyz", "col") is None

    def test_supports_comments_property(self, representative_database):
        """_supports_comments should return a bool."""
        result = representative_database._supports_comments()
        assert isinstance(result, bool)
