sqlite:     ALTER TABLE {table_name} ADD COLUMN {column_sql};
postgresql: ALTER TABLE {table_name} ADD COLUMN {column_sql};
mysql:      ALTER TABLE {table_name} ADD COLUMN {column_sql};
mssql:      ALTER TABLE {table_name} ADD {column_sql};
oracle:     ALTER TABLE {table_name} ADD ({column_sql});
duckdb:     ALTER TABLE {table_name} ADD COLUMN {column_sql};
starrocks:  ALTER TABLE {table_name} ADD COLUMN {column_sql};
hive:       ALTER TABLE {table_name} ADD COLUMNS ({column_sql});
trino:      ALTER TABLE {table_name} ADD COLUMN {column_sql};
