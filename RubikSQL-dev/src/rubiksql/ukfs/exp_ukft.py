__all__ = [
    "RubikSQLExpUKFT",
]

from ahvn.ukf import register_ukft
from ahvn.ukf.templates.basic.knowledge import KnowledgeUKFT
from ahvn.ukf.templates.basic.prompt import PromptUKFT
from ahvn.ukf.templates.basic.experience import ExperienceUKFT
from ahvn.ukf.ukf_utils import ptags
from ahvn.utils.db.base import Database
from ahvn.utils.db.db_utils import prettify_sql, compare_sqls
from ahvn.utils.exts.autotask import autotask, AutoFuncError
from ahvn.utils.basic.log_utils import get_logger
from ahvn.utils.basic.config_utils import dget, dsetdef
from ahvn.cache.base import CacheEntry

logger = get_logger(__name__)

from ..utils.config_utils import rpj

from ..resources.rubik_kb import RUBIK_CM

from typing import ClassVar, List, Dict, Any, Optional, Union


@register_ukft
class RubikSQLExpUKFT(ExperienceUKFT):
    """\
    RubikSQL Database NL2SQL Query Experience UKF Type.

    UKF Type: experience >> nl2sql-query
    Recommended Tags:
        - DATABASE
        - DIALECT

    Recommended Relations:
        - RubikSQLExpUKFT -- involves -> DatabaseUKFT
        - RubikSQLExpUKFT -- involves -> TableUKFT
        - RubikSQLExpUKFT -- involves -> TaxonomyUKFT
        - RubikSQLExpUKFT -- involves -> ColumnUKFT
        - RubikSQLExpUKFT -- involves -> PredicateUKFT
        - RubikSQLExpUKFT -- involves -> EnumUKFT

    Recommended Components of `content_resources`:
        - func (str): The name of the generator of this experience instance.
        - inputs (Dict): The inputs.
            - question (str): NL query question
            - context (Dict[str, Any]): Query context, e.g., user profile, time, location, etc.
            - schema (Optional[List[str]]): The desired SQL execution result schema
            - hints (List[str]): Optional hints or notes about the experience instance
        - output (Any): The output.
            - (sql) (str): Final SQL query string, formatted and commented
        - expected (Any): The ground-truth output.
            - (sql) (str): Ground Truth SQL query string, formatted and commented
        - metatdata (Dict): Any extra information related to the experience instance.
            - db_id (str): Database identifier
            - schema (List[Dict]): The actual SQL execution result schema
            - dialect (str): SQL dialect used
            - kls (List[str]): List of involved knowledge strings
            - prompt (str): The prompt used for LLM generation
            - raw_question ...
            - raw_sql ...
            - ...
    """

    type_default: ClassVar[str] = "nl2sql-query"

    def format_sql(self, sql: str):
        """\
        Transpile the given SQL string with standard format.

        Args:
            sql (str): The SQL string to be formatted.

        Returns:
            str: Formatted SQL string.
        """
        if not sql.strip():
            return sql
        return prettify_sql(
            query=sql.strip(),
            dialect=self.metadata.get("dialect", "sqlite"),
            comments=True,
        ).strip()

    def format_output_sql(self) -> str:
        """\
        Transpile the output SQL strings with standard format.

        Returns:
            str: Formatted SQL string, which is also set to the `output` field.
        """
        self.set("output", self.format_sql(self.get("output", "")))
        return self.get("output")

    def format_expected_sql(self) -> str:
        """\
        Transpile the expected SQL strings with standard format.

        Returns:
            str: Formatted SQL string, which is also set to the `expected` field.
        """
        self.set("expected", self.format_sql(self.get("expected", "")))
        return self.get("expected")

    def comment_sql(self, sql: str, verify_db: Optional[Database] = None, **kwargs) -> str:
        """\
        Add comments to the given SQL string.

        Args:
            sql (str): The SQL string to be commented.
            verify_db (Optional[Database]): If provided, use this database to verify the SQL.
                The commented SQL should have exactly the same execution result as the original SQL on this database.
                If not provided, no verification is performed.
            **kwargs: Additional keyword arguments to be fed as information to the commenting prompt.
                For example, a typical use case is to provide `**self.get("inputs", dict())` to include input information.

        Returns:
            str: Commented SQL string.
        """
        if not sql.strip():
            return sql
        try:
            pd_sql = autotask(
                prompt=RUBIK_CM.get_prompt(
                    "comment_sql",
                    tags=ptags(
                        DIALECT=self.metadata.get("dialect", "sqlite"),
                    ),
                )
            )(
                sql=sql.strip(),
                **kwargs,
            )
            if not isinstance(pd_sql, str):
                raise AutoFuncError(f"Generated description is not a string, but {type(pd_sql)}: {pd_sql}.")
        except AutoFuncError as e:
            logger.warning(f"Failed to generate commented SQL for {self.name}. {e}")
        if (verify_db is not None) and (not compare_sqls(sql, pd_sql, verify_db)):
            logger.warning(
                f"Commented SQL does not match the execution result of the original SQL on the verification database for {self.name}.\n"
                + f"Original SQL: ```sql\n{sql}\n```\n"
                + f"Commented SQL: ```sql\n{pd_sql}\n```"
            )
        return pd_sql.strip()

    def comment_output_sql(self, verify_db: Optional[Database] = None, **kwargs) -> str:
        """\
        Add comments to the output SQL string.

        Args:
            verify_db (Optional[Database]): If provided, use this database to verify the SQL.
                The commented SQL should have exactly the same execution result as the original SQL on this database.
                If not provided, no verification is performed.
            **kwargs: Additional keyword arguments to be fed as information to the commenting prompt.
                For example, a typical use case is to provide `**self.get("inputs", dict())` to include input information.

        Returns:
            str: Commented SQL string, which is also set to the `output` field.
        """
        self.set("output", self.comment_sql(self.get("output", ""), verify_db=verify_db, **kwargs))
        return self.get("output")

    def comment_expected_sql(self, verify_db: Optional[Database] = None, **kwargs) -> str:
        """\
        Add comments to the expected SQL string.

        Args:
            verify_db (Optional[Database]): If provided, use this database to verify the SQL.
                The commented SQL should have exactly the same execution result as the original SQL on this database.
                If not provided, no verification is performed.
            **kwargs: Additional keyword arguments to be fed as information to the commenting prompt.
                For example, a typical use case is to provide `**self.get("inputs", dict())` to include input information.

        Returns:
            str: Commented SQL string, which is also set to the `expected` field.
        """
        self.set("expected", self.comment_sql(self.get("expected", ""), verify_db=verify_db, **kwargs))
        return self.get("expected")

    @classmethod
    def from_cache_entry(
        cls,
        db_id: str,
        entry: Union[CacheEntry, Dict[str, Any]],
        format_sql: bool = True,
        comment_sql: bool = True,
        verify_db: Optional[Database] = None,
        **updates,
    ):
        if not isinstance(entry, CacheEntry):
            full_entry = CacheEntry.from_dict(entry)
        else:
            full_entry = entry.clone()
        # dsetdef(full_entry.inputs, "question", "")
        dsetdef(full_entry.inputs, "context", dict())
        dsetdef(full_entry.inputs, "schema", None)
        dsetdef(full_entry.inputs, "hints", list())
        dsetdef(full_entry.metadata, "db_id", db_id)
        kl = cls.from_ukf(
            ExperienceUKFT.from_cache_entry(
                name=full_entry.inputs.get("question"),
                entry=full_entry,
                **updates,
            ),
            polymorphic=False,
            override_type=True,
        )
        output_sql = full_entry.output
        expected_sql = full_entry.expected
        if format_sql and (output_sql is not ...):
            kl.format_output_sql()
        if format_sql and (expected_sql is not ...):
            kl.format_expected_sql()
        if comment_sql and (output_sql is not ...):
            kl.comment_output_sql(verify_db=verify_db)
        if comment_sql and (expected_sql is not ...):
            kl.comment_expected_sql(verify_db=verify_db)
        return kl

    @classmethod
    def from_nl2sql(
        cls,
        db_id: str,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        schema: Optional[Any] = None,
        hints: Optional[List[str]] = None,
        output_sql: Optional[str] = ...,
        expected_sql: Optional[str] = ...,
        metadata: Optional[Dict[str, Any]] = None,
        format_sql: bool = True,
        comment_sql: bool = False,
        verify_db: Optional[Database] = None,
        **updates,
    ):
        return cls.from_cache_entry(
            db_id=db_id,
            entry={
                "func": "nl2sql",
                "inputs": {
                    "question": question,
                    "context": context if context is not None else dict(),
                    "schema": schema if schema is not None else None,
                    "hints": hints if hints is not None else list(),
                },
                "output": output_sql,
                "expected": expected_sql,
                "metadata": {
                    "db_id": db_id,
                }
                | (metadata if metadata is not None else dict()),
            },
            format_sql=format_sql,
            comment_sql=comment_sql,
            verify_db=verify_db,
            **updates,
        )
