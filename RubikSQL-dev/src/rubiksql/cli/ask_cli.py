"""\
NL2SQL query CLI command for RubikSQL.
"""

import click
import json
from typing import Dict, Any, List, Optional
from ahvn.cli.utils import AliasedGroup


def _format_tool_call(tc: dict) -> str:
    """Format a tool call for display with pretty colored header."""
    from ahvn.utils.basic.color_utils import color_info

    func_info = tc.get("function", tc)
    name = func_info.get("name", "unknown")
    args_str = func_info.get("arguments", "{}")
    try:
        args = json.loads(args_str) if isinstance(args_str, str) else args_str
        # Format args as key=value pairs
        args_parts = [f"{k} = {repr(v)}" for k, v in args.items()]
        args_display = "\n".join(args_parts)
    except Exception:
        args_display = str(args_str)

    return "\n".join(
        [
            color_info(f"===== [TOOL `{name}`] ====="),
            color_info(args_display),
        ]
    )


def _format_tool_result(msg: dict) -> str:
    """Format a tool result message for display with colored content and footer."""
    from ahvn.utils.basic.color_utils import color_info

    name = msg.get("name", "unknown")
    content = msg.get("content", "")
    # Color the content as cyan, footer as magenta
    return f"{content}\n{color_info(f'===== [TOOL {name} END] =====')}"


def register_ask_commands(cli):
    """\
    Register the ask (NL2SQL) command to the CLI.
    """

    @cli.command(
        "ask",
        help="""\
Ask a question in natural language to the database.

Examples:
  rubiksql ask -n superhero "How many superheroes are there?"
  rubiksql ask -n mydb "Show top 10 users" --hints "users table has user_id"
  rubiksql ask -n mydb "..." --agent flash --verbose
""",
    )
    @click.option("--name", "-n", required=True, help="Registered database identifier.")
    @click.argument("question", required=True)
    @click.option("--agent", "-a", default=None, help="Agent to use (default: auto).")
    @click.option("--hints", "-H", default=None, help="Additional hints for the agent.")
    @click.option("--verbose", "-v", is_flag=True, help="Show detailed agent logs.")
    @click.option("--stream", "-S", is_flag=True, help="Stream agent output.")
    def ask_cmd(name, question, agent, hints, verbose, stream):
        """\
        Ask a question in natural language.
        """
        import asyncio
        from ahvn.utils.basic.color_utils import color_success, color_error, color_grey, color_info, color_warning
        from ahvn.utils.basic.log_utils import set_log_level

        from rubiksql.api import db_exists, get_kb_path, load_db
        from rubiksql.services.agent_service import ask_agent, get_default_agent
        from rubiksql.klbase.kb_status import kb_is_built

        if not db_exists(name):
            click.echo(color_error(f"Database '{name}' not found."), err=True)
            raise SystemExit(1)

        if not kb_is_built(name):
            click.echo(color_warning(f"KB for '{name}' is not built."))
            click.echo(color_grey(f"Run 'rubiksql build -n {name}' first."))
            click.echo(color_grey(f"Run 'rubiksql build -n {name}' to complete the build."))
            # We allow continuing, but warn strongly
            if not click.confirm("Continue without KB? (Results may be poor)"):
                return

        try:
            db = load_db(name)

            if verbose:
                set_log_level("DEBUG", loggers=["rubiksql", "ahvn"])
                click.echo(color_grey(f"Using agent: {agent or 'default'}"))
                click.echo(color_grey(f"Question: {question}"))
                if hints:
                    click.echo(color_grey(f"Hints: {hints}"))
                click.echo()

            # Run async function
            msg_gen = ask_agent(db, question, agent_name=agent, hints=hints, stream=True)

            final_answer = ""
            # current_tool_call = None

            with click.progressbar(length=None, label="Thinking", show_percent=False, show_pos=True) as bar:
                for msg in msg_gen:
                    if msg.get("type") == "tool_call":
                        bar.finish()  # Clear progress bar
                        click.echo(_format_tool_call(msg))
                        # current_tool_call = msg

                    elif msg.get("type") == "tool_result":
                        click.echo(_format_tool_result(msg))
                        # Restart "Thinking" unless we're done
                        bar.start()

                    elif msg.get("type") == "answer_chunk":
                        chunk = msg.get("content", "")
                        final_answer += chunk
                        if stream:
                            click.echo(chunk, nl=False)

                    elif msg.get("type") == "answer":
                        final_answer = msg.get("content", "")

                bar.finish()

            if not stream:
                click.echo()
                click.echo(color_success("Answer:"))
                click.echo(final_answer)

            db.close_conn()

        except Exception as e:
            click.echo(color_error(f"Error: {e}"), err=True)
            if verbose:
                import traceback

                traceback.print_exc()
            raise SystemExit(1)
