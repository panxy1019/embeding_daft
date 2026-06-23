"""
MCP Toolkit Tutorial — Programmatic Usage
==========================================

This tutorial demonstrates how to use the AgentHeaven MCP Toolkit system
programmatically (without the CLI).

Topics:
    1. Creating toolkits via factories
    2. Managing toolkits with ToolkitManager
    3. Executing tools
    4. Exporting as a Skills package
    5. Loading Skills via SkillUKFT
"""

# %% [1] Factory & Toolkit basics
# ------------------------------------------------------------------
# A ToolkitFactory creates a Toolkit from arguments.
# Built-in factories: "db" (database), with more to come.

from ahvn.tool import get_factory, list_factories, Toolkit

# List registered factories
print("Available factories:", list_factories())

# Get the database factory
db_factory = get_factory("db")
print("Factory description:")
print(db_factory.description)

# %% [2] Create a toolkit
# ------------------------------------------------------------------
# The factory's .create() method builds a Toolkit with pre-configured tools.

toolkit = db_factory.create(
    "demo-db",
    provider="sqlite",
    database="./demo.db",
)
print(toolkit)
print("Tools:", toolkit.list_tools())
print("Info:", toolkit.info())

# %% [3] Run a tool
# ------------------------------------------------------------------
# toolkit.run(tool_name, **kwargs) executes a tool directly.

result = toolkit.run("exec_sql", query="SELECT 1 AS hello, 2 AS world")
print(result)

# Create a table and insert data
toolkit.run("exec_sql", query="CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT)")
toolkit.run("exec_sql", query="INSERT INTO users (name) VALUES ('Alice')")
toolkit.run("exec_sql", query="INSERT INTO users (name) VALUES ('Bob')")
result = toolkit.run("exec_sql", query="SELECT * FROM users")
print(result)

# %% [4] ToolkitManager — CRUD + persistence
# ------------------------------------------------------------------
# ToolkitManager provides CRUD, persistence, MCP serving, and execution.

from ahvn.tool import ToolkitManager, TK_AHVN

# TK_AHVN is a module-level singleton wrapping ToolkitManager.
# You can use it directly instead of creating a new ToolkitManager().
mgr = TK_AHVN

# Create via manager (persisted to ~/.ahvn/toolkits.json)
mgr.create("db", "tutorial-db", provider="sqlite", database="./tutorial.db")

# List all toolkits
print("All toolkits:", mgr.list())

# Execute a tool via qualified name
result = mgr.run("tutorial-db.exec_sql", query="SELECT 42 AS answer")
print(result)

# Rename
mgr.rename("tutorial-db", "my-tutorial-db")

# Remove
mgr.remove("my-tutorial-db")

# %% [5] Export as Skills package
# ------------------------------------------------------------------
# Toolkits can be exported as Skills packages (directories with SKILL.md,
# metadata.json, etc.) compatible with SkillUKFT.from_path().

import os
import shutil

export_tk = db_factory.create(
    "export-demo",
    provider="sqlite",
    database="./export_demo.db",
)

# Export to a directory
export_path = export_tk.export("./export-demo-skill/")
print(f"Exported to: {export_path}")

# Show contents
for root, dirs, files in os.walk(export_path):
    for f in files:
        rel = os.path.relpath(os.path.join(root, f), export_path)
        print(f"  {rel}")

# Read SKILL.md
with open(os.path.join(export_path, "SKILL.md")) as f:
    print("\n--- SKILL.md ---")
    print(f.read())

# %% [6] Load as SkillUKFT
# ------------------------------------------------------------------
# The exported directory is directly loadable by SkillUKFT.from_path().

from ahvn.ukf.templates.basic.skill import SkillUKFT

skill = SkillUKFT.from_path(export_path)
print(f"Skill name: {skill.name}")
print(f"Skill tools: {skill.tools}")
print(f"Skill description:\n{skill.text('desc')}")

# %% [7] MCP serving
# ------------------------------------------------------------------
# A toolkit can be served directly as an MCP server.

# Generate MCP client config for copy-paste
import json

print("MCP client config:")
print(export_tk.to_mcp_json())

# Serve as MCP server (blocking — uncomment to start):
# export_tk.serve()                                        # stdio
# export_tk.serve(transport="http", port=8000)             # HTTP

# %% Cleanup
# ------------------------------------------------------------------
# for path in ["./demo.db", "./tutorial.db", "./export_demo.db"]:
#     if os.path.exists(path):
#         os.remove(path)
# shutil.rmtree("./export-demo-skill/", ignore_errors=True)
# print("Cleanup done.")
#
