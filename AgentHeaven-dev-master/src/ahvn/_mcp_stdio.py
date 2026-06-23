"""\
MCP stdio bootstrap — fixes conda DLL PATH before any heavy imports.

Can be invoked in three ways (shortest to most explicit)::

    python /path/to/ahvn/_mcp_stdio.py <toolkit_name>        # direct file
    python -c "..." <toolkit_name>                            # inline bootstrap
    ahvn mcp stdio <toolkit_name>                             # CLI (activated env)

This module is intentionally light: only stdlib imports at the top level.
The ``ahvn`` package (which may trigger native DLL loads) is imported
*after* PATH has been fixed.
"""

import os
import sys


def _fix_conda_path():
    """Prepend conda ``Library\\bin`` to PATH on Windows if needed."""
    if sys.platform != "win32":
        return
    env_root = os.path.dirname(sys.executable)
    lib_bin = os.path.join(env_root, "Library", "bin")
    if os.path.isdir(os.path.join(env_root, "conda-meta")) and os.path.isdir(lib_bin):
        os.environ["PATH"] = lib_bin + os.pathsep + os.environ.get("PATH", "")


def main(name: str = ""):
    """Bootstrap entry point for MCP stdio transport."""
    _fix_conda_path()

    name = name or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not name:
        print("Usage: python _mcp_stdio.py <toolkit_name>", file=sys.stderr)
        sys.exit(1)

    # Now safe to import ahvn (DLLs are findable)
    from ahvn.tool import TK_AHVN

    TK_AHVN.get(name).serve(transport="stdio")


if __name__ == "__main__":
    main()
