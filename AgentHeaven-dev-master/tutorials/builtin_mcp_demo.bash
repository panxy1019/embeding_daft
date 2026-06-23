# Creating the built-in MCPs for the AHVN
ahvn mcp rm ahvn -y
ahvn mcp create config ahvn -p ahvn -cm CM_AHVN
ahvn mcp rm llm -y
ahvn mcp create llm llm -d chat -a "chat*" -a sys -a "reason*"


# Creating the basic database MCP
ahvn mcp rm unicom -y
ahvn mcp create db unicom --provider sqlite --database ./unicom.db


# Serving the MCPs
## Stdio (do nothing, only presents the MCP config json for stdio connection)
ahvn mcp serve ahvn llm unicom --stdio
## HTTP
ahvn mcp serve ahvn llm unicom --host 127.0.0.1 --port 7001