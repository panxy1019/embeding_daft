import sqlite3
from typing import List, Dict, Any, Optional, Union, Tuple
from contextlib import contextmanager
import logging
import os


class SQLiteDB:
    """SQLite数据库操作封装类"""

    """AI编写的SQLite数据库操作类，支持创建表、索引，插入、更新、删除记录工具"""

    def __init__(self, db_path: str, enable_foreign_keys: bool = True, wal_mode: bool = True, logger: Optional[logging.Logger] = None, log_acc: bool = False):
        """
        初始化数据库连接

        Args:
            db_path: 数据库文件路径，使用':memory:'创建内存数据库
            enable_foreign_keys: 是否启用外键约束
            wal_mode: 是否启用WAL模式（提高并发性能）
            logger: 日志记录器
        """
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self.enable_foreign_keys = enable_foreign_keys
        self.wal_mode = wal_mode
        self.logger = logger or self._setup_default_logger()
        self.log_acc = log_acc

        # 连接数据库
        self.connect()

    def _setup_default_logger(self) -> logging.Logger:
        """设置默认日志记录器"""
        logger = logging.getLogger("SQLiteDB")
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
        return logger

    def connect(self):
        """建立数据库连接并配置"""
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row  # 支持字典式访问
            self.cursor = self.conn.cursor()

            # 性能优化设置
            if self.wal_mode:
                self.conn.execute("PRAGMA journal_mode=WAL")

            self.conn.execute("PRAGMA synchronous=NORMAL")
            self.conn.execute("PRAGMA cache_size=-64000")  # 64MB缓存
            self.conn.execute("PRAGMA temp_store=MEMORY")

            # 启用外键约束
            if self.enable_foreign_keys:
                self.conn.execute("PRAGMA foreign_keys=ON")

            if self.log_acc:
                self.logger.info(f"数据库连接成功: {self.db_path}")
        except sqlite3.Error as e:
            self.logger.error(f"数据库连接失败: {e}")
            raise

    def close(self):
        """
        关闭数据库连接，确保完全释放资源

        处理步骤：
        1. 回滚未完成的事务
        2. 关闭cursor
        3. 关闭connection
        4. 清空引用
        """
        try:
            if self.cursor:
                # 确保没有未完成的事务
                try:
                    self.conn.rollback()
                except Exception:
                    pass
                self.cursor.close()
                self.cursor = None

            if self.conn:
                self.conn.close()
                self.conn = None

            if self.log_acc:
                self.logger.info("数据库连接已完全关闭并释放资源")
        except Exception as e:
            self.logger.error(f"关闭数据库连接时出错: {e}")

    @contextmanager
    def transaction(self):
        """事务上下文管理器"""
        try:
            self.cursor.execute("BEGIN TRANSACTION")
            yield
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            self.logger.error(f"事务失败，已回滚: {e}")
            raise

    def _validate_table_name(self, table_name: str):
        """验证表名，防止SQL注入"""
        if not table_name.isidentifier():
            raise ValueError(f"无效的表名: {table_name}")

    def _validate_column_name(self, column_name: str):
        """验证列名，防止SQL注入"""
        if not column_name.replace("_", "").isalnum():
            raise ValueError(f"无效的列名: {column_name}")

    # ==================== 创建操作 ====================

    def create_table(
        self,
        table_name: str,
        columns: Dict[str, str],
        if_not_exists: bool = True,
        primary_key: Optional[str] = None,
        foreign_keys: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        创建表

        Args:
            table_name: 表名
            columns: 列定义，格式 {'列名': '类型约束'}
            if_not_exists: 是否添加IF NOT EXISTS
            primary_key: 主键列名，多个列用逗号分隔
            foreign_keys: 外键定义，格式 {'列名': '引用表(引用列)'}

        Returns:
            是否创建成功

        Example:
            >>> db.create_table('users', {
            ...     'id': 'INTEGER PRIMARY KEY AUTOINCREMENT',
            ...     'name': 'TEXT NOT NULL',
            ...     'email': 'TEXT UNIQUE'
            ... })
        """
        self._validate_table_name(table_name)

        # 构建列定义
        columns_def = []
        for col_name, col_type in columns.items():
            self._validate_column_name(col_name)
            columns_def.append(f"{col_name} {col_type}")

        # 添加主键（如果没有在列中定义）
        if primary_key and not any("PRIMARY KEY" in col_def.upper() for col_def in columns_def):
            columns_def.append(f"PRIMARY KEY ({primary_key})")

        # 添加外键
        if foreign_keys:
            for col_name, reference in foreign_keys.items():
                self._validate_column_name(col_name)
                columns_def.append(f"FOREIGN KEY ({col_name}) REFERENCES {reference}")

        # 构建SQL
        if_exists_clause = "IF NOT EXISTS" if if_not_exists else ""
        columns_sql = ",\n    ".join(columns_def)
        sql = f"CREATE TABLE {if_exists_clause} {table_name} (\n    {columns_sql}\n)"

        try:
            self.cursor.execute(sql)
            self.conn.commit()
            if self.log_acc:
                self.logger.info(f"表创建成功: {table_name}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"表创建失败: {e}")
            return False

    def create_index(self, table_name: str, index_name: str, columns: Union[str, List[str]], unique: bool = False, if_not_exists: bool = True) -> bool:
        """
        创建索引

        Args:
            table_name: 表名
            index_name: 索引名
            columns: 索引列，可以是单个列名字符串或列名列表
            unique: 是否创建唯一索引
            if_not_exists: 是否添加IF NOT EXISTS

        Returns:
            是否创建成功

        Example:
            >>> db.create_index('users', 'idx_email', 'email', unique=True)
            >>> db.create_index('users', 'idx_name_age', ['name', 'age'])
        """
        self._validate_table_name(table_name)

        # 处理列名
        if isinstance(columns, str):
            columns = [columns]

        columns_str = ", ".join(columns)

        # 构建SQL
        unique_clause = "UNIQUE" if unique else ""
        if_exists_clause = "IF NOT EXISTS" if if_not_exists else ""

        sql = f"CREATE {unique_clause} INDEX {if_exists_clause} {index_name} ON {table_name} ({columns_str})"

        try:
            self.cursor.execute(sql)
            self.conn.commit()
            if self.log_acc:
                self.logger.info(f"索引创建成功: {index_name} ON {table_name}({columns_str})")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"索引创建失败: {e}")
            return False

    # ==================== 插入操作 ====================

    def insert(self, table_name: str, data: Dict[str, Any]) -> Optional[int]:
        """
        插入单条记录

        Args:
            table_name: 表名
            data: 要插入的数据字典

        Returns:
            插入行的ID，失败返回None

        Example:
            >>> user_id = db.insert('users', {'name': '张三', 'age': 25, 'email': 'zhangsan@example.com'})
        """
        self._validate_table_name(table_name)

        if not data:
            self.logger.warning("插入数据为空")
            return None

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        sql = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"

        try:
            self.cursor.execute(sql, tuple(data.values()))
            self.conn.commit()
            last_id = self.cursor.lastrowid
            if self.log_acc:
                self.logger.debug(f"插入成功: {table_name}, ID={last_id}")
            return last_id
        except sqlite3.Error as e:
            self.logger.error(f"插入失败: {e}")
            return None

    def insert_many(self, table_name: str, data_list: List[Dict[str, Any]], batch_size: int = 1000) -> int:
        """
        批量插入记录

        Args:
            table_name: 表名
            data_list: 数据字典列表
            batch_size: 批次大小

        Returns:
            成功插入的行数

        Example:
            >>> count = db.insert_many('users', [
            ...     {'name': '张三', 'age': 25},
            ...     {'name': '李四', 'age': 30}
            ... ])
        """
        self._validate_table_name(table_name)

        if not data_list:
            if self.log_acc:
                self.logger.warning("插入数据列表为空")
            return 0

        # 获取列名（使用第一条数据的键）
        columns = ", ".join(data_list[0].keys())
        placeholders = ", ".join(["?" for _ in data_list[0]])
        sql = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"

        total_inserted = 0

        try:
            with self.transaction():
                for i in range(0, len(data_list), batch_size):
                    batch = data_list[i : i + batch_size]
                    values = [tuple(item.values()) for item in batch]
                    self.cursor.executemany(sql, values)
                    total_inserted += len(batch)
                    self.logger.debug(f"批量插入进度: {total_inserted}/{len(data_list)}")

            if self.log_acc:
                self.logger.info(f"批量插入成功: {total_inserted} 行")
            return total_inserted
        except sqlite3.Error as e:
            self.logger.error(f"批量插入失败: {e}")
            exit(1)
            return total_inserted

    def upsert(self, table_name: str, data: Dict[str, Any], conflict_columns: Union[str, List[str]]) -> Optional[int]:
        """
        插入或更新（冲突时更新指定字段）

        Args:
            table_name: 表名
            data: 数据字典
            conflict_columns: 冲突检测列

        Returns:
            行的ID
        """
        self._validate_table_name(table_name)

        if isinstance(conflict_columns, str):
            conflict_columns = [conflict_columns]

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["?" for _ in data])
        conflict_cols = ", ".join(conflict_columns)

        # 构建UPDATE SET子句（排除冲突列）
        update_parts = [f"{k} = excluded.{k}" for k in data.keys() if k not in conflict_columns]
        update_clause = ", ".join(update_parts) if update_parts else f"{conflict_columns[0]} = excluded.{conflict_columns[0]}"

        sql = f"""
            INSERT INTO {table_name} ({columns})
            VALUES ({placeholders})
            ON CONFLICT({conflict_cols}) DO UPDATE SET {update_clause}
        """

        try:
            self.cursor.execute(sql, tuple(data.values()))
            self.conn.commit()
            return self.cursor.lastrowid
        except sqlite3.Error as e:
            self.logger.error(f"UPSERT失败: {e}")
            return None

    # ==================== 删除操作 ====================

    def delete_by_id(self, table_name: str, record_id: Any, id_column: str = "id") -> int:
        """
        按主键删除记录

        Args:
            table_name: 表名
            record_id: 主键值
            id_column: 主键列名

        Returns:
            删除的行数

        Example:
            >>> deleted = db.delete_by_id('users', 1)
        """
        self._validate_table_name(table_name)
        self._validate_column_name(id_column)

        sql = f"DELETE FROM {table_name} WHERE {id_column} = ?"

        try:
            self.cursor.execute(sql, (record_id,))
            self.conn.commit()
            deleted = self.cursor.rowcount
            if self.log_acc:
                self.logger.info(f"按主键删除: {deleted} 行")
            return deleted
        except sqlite3.Error as e:
            self.logger.error(f"按主键删除失败: {e}")
            return 0

    def delete_by_indexed(self, table_name: str, field: str, value: Any, operator: str = "=") -> int:
        """
        按索引字段删除记录

        Args:
            table_name: 表名
            field: 索引字段名
            value: 字段值
            operator: 比较运算符（=, >, <, >=, <=, !=, LIKE）

        Returns:
            删除的行数

        Example:
            >>> deleted = db.delete_by_indexed('users', 'age', 30, '>')
        """
        self._validate_table_name(table_name)
        self._validate_column_name(field)

        # 验证运算符
        allowed_operators = {"=", ">", "<", ">=", "<=", "!=", "LIKE", "IN", "BETWEEN"}
        if operator.upper() not in allowed_operators:
            raise ValueError(f"不支持的运算符: {operator}")

        sql = f"DELETE FROM {table_name} WHERE {field} {operator} ?"

        try:
            self.cursor.execute(sql, (value,))
            self.conn.commit()
            deleted = self.cursor.rowcount
            if self.log_acc:
                self.logger.info(f"按索引删除: {deleted} 行 (WHERE {field} {operator} ?)")
            return deleted
        except sqlite3.Error as e:
            self.logger.error(f"按索引删除失败: {e}")
            return 0

    def delete_by_condition(self, table_name: str, condition: str, params: Optional[Tuple] = None) -> int:
        """
        按条件删除记录

        Args:
            table_name: 表名
            condition: WHERE条件（使用?占位符）
            params: 条件参数

        Returns:
            删除的行数

        Example:
            >>> deleted = db.delete_by_condition('users', 'age > ? AND status = ?', (30, 'inactive'))
        """
        self._validate_table_name(table_name)

        sql = f"DELETE FROM {table_name} WHERE {condition}"

        try:
            if params:
                self.cursor.execute(sql, params)
            else:
                self.cursor.execute(sql)
            self.conn.commit()
            deleted = self.cursor.rowcount
            if self.log_acc:
                self.logger.info(f"按条件删除: {deleted} 行")
            return deleted
        except sqlite3.Error as e:
            self.logger.error(f"按条件删除失败: {e}")
            return 0

    def delete_all(self, table_name: str) -> int:
        """
        清空表（删除所有记录）

        Args:
            table_name: 表名

        Returns:
            删除的行数

        Example:
            >>> deleted = db.delete_all('users')
        """
        self._validate_table_name(table_name)

        sql = f"DELETE FROM {table_name}"

        try:
            self.cursor.execute(sql)
            self.conn.commit()
            deleted = self.cursor.rowcount
            if self.log_acc:
                self.logger.info(f"清空表 {table_name}: {deleted} 行")
            return deleted
        except sqlite3.Error as e:
            self.logger.error(f"清空表失败: {e}")
            return 0

    def truncate_table(self, table_name: str) -> bool:
        """
        截断表（使用DROP+CREATE更快，但会重置自增ID）

        Args:
            table_name: 表名

        Returns:
            是否成功
        """
        self._validate_table_name(table_name)

        try:
            # 获取表结构
            self.cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            result = self.cursor.fetchone()
            if not result:
                self.logger.error(f"表不存在: {table_name}")
                return False

            create_sql = result["sql"]

            # 删除并重建表
            with self.transaction():
                self.cursor.execute(f"DROP TABLE {table_name}")
                self.cursor.execute(create_sql)

            if self.log_acc:
                self.logger.info(f"表截断成功: {table_name}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"表截断失败: {e}")
            return False

    # ==================== 更新操作 ====================

    def update_by_id(self, table_name: str, record_id: Any, data: Dict[str, Any], id_column: str = "id") -> int:
        """
        按主键更新记录

        Args:
            table_name: 表名
            record_id: 主键值
            data: 要更新的字段字典
            id_column: 主键列名

        Returns:
            更新的行数

        Example:
            >>> updated = db.update_by_id('users', 1, {'name': '张三丰', 'age': 100})
        """
        self._validate_table_name(table_name)
        self._validate_column_name(id_column)

        if not data:
            self.logger.warning("更新数据为空")
            return 0

        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        values = list(data.values()) + [record_id]

        sql = f"UPDATE {table_name} SET {set_clause} WHERE {id_column} = ?"

        try:
            self.cursor.execute(sql, values)
            self.conn.commit()
            updated = self.cursor.rowcount
            if self.log_acc:
                self.logger.info(f"按主键更新: {updated} 行")
            return updated
        except sqlite3.Error as e:
            self.logger.error(f"按主键更新失败: {e}")
            return 0

    def update_by_indexed(self, table_name: str, where_field: str, where_value: Any, data: Dict[str, Any], operator: str = "=") -> int:
        """
        按索引字段更新记录

        Args:
            table_name: 表名
            where_field: WHERE条件字段（应为索引字段）
            where_value: WHERE条件值
            data: 要更新的字段字典
            operator: 比较运算符

        Returns:
            更新的行数

        Example:
            >>> updated = db.update_by_indexed('users', 'age', 30,
            ...                                       {'status': 'senior'}, '>')
        """
        self._validate_table_name(table_name)
        self._validate_column_name(where_field)

        if not data:
            return 0

        allowed_operators = {"=", ">", "<", ">=", "<=", "!=", "LIKE"}
        if operator.upper() not in allowed_operators:
            raise ValueError(f"不支持的运算符: {operator}")

        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        values = list(data.values()) + [where_value]

        sql = f"UPDATE {table_name} SET {set_clause} WHERE {where_field} {operator} ?"

        try:
            self.cursor.execute(sql, values)
            self.conn.commit()
            updated = self.cursor.rowcount
            if self.log_acc:
                self.logger.info(f"按索引更新: {updated} 行 (WHERE {where_field} {operator} ?)")
            return updated
        except sqlite3.Error as e:
            self.logger.error(f"按索引更新失败: {e}")
            return 0

    def update_by_condition(self, table_name: str, condition: str, data: Dict[str, Any], condition_params: Optional[Tuple] = None) -> int:
        """
        按条件更新记录

        Args:
            table_name: 表名
            condition: WHERE条件
            data: 要更新的字段字典
            condition_params: 条件参数

        Returns:
            更新的行数

        Example:
            >>> updated = db.update_by_condition('users',
            ...                                   'age > ? AND status = ?',
            ...                                   {'status': 'senior'},
            ...                                   (30, 'active'))
        """
        self._validate_table_name(table_name)

        if not data:
            return 0

        set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
        values = list(data.values()) + (list(condition_params) if condition_params else [])

        sql = f"UPDATE {table_name} SET {set_clause} WHERE {condition}"

        try:
            self.cursor.execute(sql, values)
            self.conn.commit()
            updated = self.cursor.rowcount
            if self.log_acc:
                self.logger.info(f"按条件更新: {updated} 行")
            return updated
        except sqlite3.Error as e:
            self.logger.error(f"按条件更新失败: {e}")
            return 0

    # ==================== 查询操作（辅助方法） ====================

    def select_all(self, table_name: str, columns: str = "*", limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """查询所有记录"""
        self._validate_table_name(table_name)

        sql = f"SELECT {columns} FROM {table_name}"
        if limit:
            sql += f" LIMIT {limit}"

        try:
            self.cursor.execute(sql)
            return [dict(row) for row in self.cursor.fetchall()]
        except sqlite3.Error as e:
            self.logger.error(f"查询失败: {e}")
            return []

    def select_by_id(self, table_name: str, record_id: Any, id_column: str = "id") -> Optional[Dict[str, Any]]:
        """按主键查询单条记录"""
        self._validate_table_name(table_name)

        sql = f"SELECT * FROM {table_name} WHERE {id_column} = ?"

        try:
            self.cursor.execute(sql, (record_id,))
            row = self.cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as e:
            self.logger.error(f"查询失败: {e}")
            return None

    # ==================== 索引管理 ====================

    def list_indexes(self, table_name: str) -> List[Dict[str, Any]]:
        """列出表的所有索引"""
        self._validate_table_name(table_name)

        self.cursor.execute(f"PRAGMA index_list({table_name})")
        return [dict(row) for row in self.cursor.fetchall()]

    def drop_index(self, index_name: str, if_exists: bool = True) -> bool:
        """删除索引"""
        if_exists_clause = "IF EXISTS" if if_exists else ""
        sql = f"DROP INDEX {if_exists_clause} {index_name}"

        try:
            self.cursor.execute(sql)
            self.conn.commit()
            if self.log_acc:
                self.logger.info(f"索引删除成功: {index_name}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"索引删除失败: {e}")
            return False

    # ==================== 表管理 ====================

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        self._validate_table_name(table_name)

        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
        return self.cursor.fetchone() is not None

    def get_table_info(self, table_name: str) -> List[Dict[str, Any]]:
        """获取表结构信息"""
        self._validate_table_name(table_name)

        self.cursor.execute(f"PRAGMA table_info({table_name})")
        return [dict(row) for row in self.cursor.fetchall()]

    def drop_table(self, table_name: str, if_exists: bool = True) -> bool:
        """删除表"""
        self._validate_table_name(table_name)

        if_exists_clause = "IF EXISTS" if if_exists else ""
        sql = f"DROP TABLE {if_exists_clause} {table_name}"

        try:
            self.cursor.execute(sql)
            self.conn.commit()
            if self.log_acc:
                self.logger.info(f"表删除成功: {table_name}")
            return True
        except sqlite3.Error as e:
            self.logger.error(f"表删除失败: {e}")
            return False

    def drop_all_table(self) -> int:
        """删除所有表"""
        self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in self.cursor.fetchall()]
        try:
            for table in tables:
                self.drop_table(table, if_exists=True)
        except sqlite3.Error as e:
            self.logger.error(f"删除所有表时出错: {e}")
        return len(tables)

    def count(self, table_name: str, condition: Optional[str] = None, params: Optional[Tuple] = None) -> int:
        """计数"""
        self._validate_table_name(table_name)

        sql = f"SELECT COUNT(*) FROM {table_name}"
        if condition:
            sql += f" WHERE {condition}"

        try:
            if params:
                self.cursor.execute(sql, params)
            else:
                self.cursor.execute(sql)
            return self.cursor.fetchone()[0]
        except sqlite3.Error as e:
            self.logger.error(f"计数失败: {e}")
            return 0

    def __enter__(self):
        """支持上下文管理器"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文管理器时关闭连接"""
        self.close()

    def __del__(self):
        """析构时关闭连接"""
        self.close()


# ==================== 使用示例 ====================


def demo(demo_db_path: str = "demo.db", dele: bool = True):
    """演示所有功能"""

    # 创建数据库实例（使用上下文管理器）
    with SQLiteDB(demo_db_path, logger=logging.getLogger("demo")) as db:

        # 1. 创建表
        print("\n=== 创建表 ===")
        db.create_table(
            "users",
            {
                "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
                "username": "TEXT NOT NULL",
                "email": "TEXT UNIQUE NOT NULL",
                "age": "INTEGER",
                "status": 'TEXT DEFAULT "active"',
                "score": "REAL DEFAULT 0.0",
            },
        )

        # 2. 创建索引
        print("\n=== 创建索引 ===")
        db.create_index("users", "idx_email", "email", unique=True)
        db.create_index("users", "idx_age", "age")
        db.create_index("users", "idx_status", "status")
        db.create_index("users", "idx_age_status", ["age", "status"])

        print("索引列表:", db.list_indexes("users"))

        # 3. 插入数据
        print("\n=== 插入数据 ===")

        # 单条插入
        user_id = db.insert("users", {"username": "张三", "email": "zhangsan@example.com", "age": 25, "score": 85.5})
        print(f"插入用户ID: {user_id}")

        # 批量插入
        users_data = [
            {"username": "李四", "email": "lisi@example.com", "age": 30, "score": 90.0},
            {"username": "王五", "email": "wangwu@example.com", "age": 28, "score": 78.5},
            {"username": "赵六", "email": "zhaoliu@example.com", "age": 35, "score": 92.0},
        ]
        count = db.insert_many("users", users_data)
        print(f"批量插入: {count} 条")

        # UPSERT
        db.upsert("users", {"username": "张三丰", "email": "zhangsan@example.com", "age": 100, "score": 99.0}, conflict_columns="email")
        print("UPSERT完成")

        # 4. 查询
        print("\n=== 当前数据 ===")
        for user in db.select_all("users"):
            print(f"  {user}")

        # 5. 按主键更新
        print("\n=== 按主键更新 ===")
        updated = db.update_by_id("users", 1, {"age": 26, "score": 88.0})
        print(f"更新了 {updated} 行")

        user = db.select_by_id("users", 1)
        print(f"更新后的用户1: {user}")

        # 6. 按索引字段更新
        print("\n=== 按索引字段更新 ===")
        updated = db.update_by_indexed("users", "age", 30, {"status": "senior", "score": 95.0}, operator=">=")
        print(f"更新了 {updated} 行")

        if dele:
            # 7. 按主键删除
            print("\n=== 按主键删除 ===")
            deleted = db.delete_by_id("users", 3)
            print(f"删除了 {deleted} 行")

            # 8. 按索引字段删除
            print("\n=== 按索引字段删除 ===")
            deleted = db.delete_by_indexed("users", "age", 30, ">")
            print(f"删除了 {deleted} 行")

        # 9. 查询最终结果
        print("\n=== 最终数据 ===")
        remaining = db.select_all("users")
        for user in remaining:
            print(f"  {user}")
        print(f"总记录数: {db.count('users')}")

        if dele:
            # 10. 清空表（演示前先查询）
            print("\n=== 清空表 ===")
            print(f"清空前: {db.count('users')} 条记录")
            deleted = db.delete_all("users")
            print(f"清空后: {db.count('users')} 条记录")

            # 11. 删除索引
            print("\n=== 删除索引 ===")
            db.drop_index("idx_age")
            print("剩余索引:", [idx["name"] for idx in db.list_indexes("users")])

            # 12. 删除表
            print("\n=== 删除表 ===")
            db.drop_table("users")
            print(f"表是否存在: {db.table_exists('users')}")
        db.close()
        os.remove(demo_db_path)


if __name__ == "__main__":
    demo()
