sqlite:     SELECT name AS col_name FROM pragma_table_info('{tab_name}') WHERE pk > 0;
duckdb:     SELECT name AS col_name FROM pragma_table_info('{tab_name}') WHERE pk > 0;
postgresql: SELECT column_name AS col_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name WHERE tc.table_name = '{tab_name}' AND tc.constraint_type = 'PRIMARY KEY';
mysql:      SELECT COLUMN_NAME AS col_name FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_NAME = '{tab_name}' AND CONSTRAINT_NAME = 'PRIMARY';
mssql:      SELECT COLUMN_NAME AS col_name FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE WHERE TABLE_NAME = '{tab_name}' AND OBJECTPROPERTY(OBJECT_ID(CONSTRAINT_NAME), 'IsPrimaryKey') = 1;
oracle:     SELECT cc.COLUMN_NAME AS col_name FROM USER_CONSTRAINTS c JOIN USER_CONS_COLUMNS cc ON c.CONSTRAINT_NAME = cc.CONSTRAINT_NAME WHERE c.TABLE_NAME = UPPER('{tab_name}') AND c.CONSTRAINT_TYPE = 'P' ORDER BY cc.POSITION;
starrocks:  SELECT COLUMN_NAME AS col_name FROM information_schema.COLUMNS WHERE TABLE_NAME = '{tab_name}' AND COLUMN_KEY = 'PRI';
hive:       SELECT NULL AS col_name FROM (SELECT 1 AS x) t WHERE 1=0;
trino:      SELECT column_name AS col_name FROM information_schema.table_constraints tc JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name WHERE tc.table_name = '{tab_name}' AND tc.constraint_type = 'PRIMARY KEY';