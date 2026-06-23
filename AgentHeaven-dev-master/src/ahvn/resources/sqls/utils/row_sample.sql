sqlite:     SELECT * FROM "{tab_name}" ORDER BY ABS((rowid * {seed}) % {modulus}) LIMIT {n_sample};
duckdb:     SELECT * FROM "{tab_name}" USING SAMPLE reservoir({n_sample} ROWS) REPEATABLE ({seed});
postgresql: SELECT * FROM "{tab_name}" ORDER BY ABS(hashtext(ctid::text || '{seed}'::text)) % {modulus} LIMIT {n_sample};
mysql:      SELECT * FROM `{tab_name}` ORDER BY RAND({seed}) LIMIT {n_sample};
mssql:      SELECT TOP ({n_sample}) * FROM [{tab_name}] ORDER BY (ABS(CHECKSUM(*)) + {seed}) % {modulus};
oracle:     SELECT * FROM (SELECT * FROM "{tab_name}" ORDER BY ORA_HASH(ROWID, {modulus} - 1, {seed})) WHERE ROWNUM <= {n_sample};
starrocks:  SELECT * FROM `{tab_name}` ORDER BY RAND() LIMIT {n_sample};
hive:       SELECT * FROM `{tab_name}` ORDER BY RAND({seed}) LIMIT {n_sample};
trino:      SELECT * FROM "{tab_name}" ORDER BY RAND() LIMIT {n_sample};
