sqlite:     SELECT "{col_name}" AS col_enums, COUNT(*) as freq FROM "{tab_name}" GROUP BY "{col_name}" ORDER BY freq ASC LIMIT {topk};
duckdb:     SELECT "{col_name}" AS col_enums, COUNT(*) as freq FROM "{tab_name}" GROUP BY "{col_name}" ORDER BY freq ASC LIMIT {topk};
postgresql: SELECT "{col_name}" AS col_enums, COUNT(*) as freq FROM "{tab_name}" GROUP BY "{col_name}" ORDER BY freq ASC LIMIT {topk};
mysql:      SELECT `{col_name}` AS col_enums, COUNT(*) as freq FROM `{tab_name}` GROUP BY `{col_name}` ORDER BY freq ASC LIMIT {topk};
mssql:      SELECT [{col_name}] AS col_enums, COUNT(*) as freq FROM [{tab_name}] GROUP BY [{col_name}] ORDER BY freq ASC OFFSET 0 ROWS FETCH NEXT {topk} ROWS ONLY;
oracle:     SELECT "{col_name}" AS col_enums, COUNT(*) as freq FROM "{tab_name}" GROUP BY "{col_name}" ORDER BY freq ASC FETCH FIRST {topk} ROWS ONLY;
starrocks:  SELECT `{col_name}` AS col_enums, COUNT(*) as freq FROM `{tab_name}` GROUP BY `{col_name}` ORDER BY freq ASC LIMIT {topk};
hive:       SELECT `{col_name}` AS col_enums, COUNT(*) as freq FROM `{tab_name}` GROUP BY `{col_name}` ORDER BY freq ASC LIMIT {topk};
trino:      SELECT "{col_name}" AS col_enums, COUNT(*) as freq FROM "{tab_name}" GROUP BY "{col_name}" ORDER BY freq ASC LIMIT {topk};