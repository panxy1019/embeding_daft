sqlite:     SELECT name AS col_name FROM pragma_table_info('{tab_name}');
duckdb:     SELECT column_name AS col_name FROM information_schema.columns WHERE table_name = '{tab_name}' AND table_schema = 'main';
postgresql: SELECT column_name AS col_name FROM information_schema.columns WHERE table_name = '{tab_name}';
mysql:      SELECT COLUMN_NAME AS col_name FROM information_schema.COLUMNS WHERE TABLE_NAME = '{tab_name}';
mssql:      SELECT COLUMN_NAME AS col_name FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{tab_name}';
oracle:     SELECT COLUMN_NAME AS col_name FROM USER_TAB_COLUMNS WHERE TABLE_NAME = UPPER('{tab_name}') ORDER BY COLUMN_ID;
starrocks:  SELECT COLUMN_NAME AS col_name FROM information_schema.COLUMNS WHERE TABLE_NAME = '{tab_name}' AND TABLE_SCHEMA = DATABASE();
hive:       SELECT column_name AS col_name FROM information_schema.columns WHERE table_name = '{tab_name}' AND table_schema = current_database();
trino:      SELECT column_name AS col_name FROM information_schema.columns WHERE table_name = '{tab_name}' AND table_schema = current_schema;