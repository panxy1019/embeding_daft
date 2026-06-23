"""\
Main CLI module for AgentHeaven.

Built entirely on Typer.  All subcommands (config, chat, embed, session)
are registered via their respective ``*CLI`` classes using the Typer backend.
"""

import typer

from ..version import __version__
from ..utils.basic.config_utils import CM_AHVN
from ..utils.basic.cli_utils import AliasedTyper, apply_cli_aliases

app = AliasedTyper(
    name="ahvn",
    help="AgentHeaven CLI",
    no_args_is_help=True,
    context_settings={
        "help_option_names": ["-h", "--help"],
        "max_content_width": 120,
    },
    invoke_without_command=True,
    add_completion=True,
)

# Register config subcommand group
from .config_cli import ConfigCLI

_config_cli = ConfigCLI(CM_AHVN)
_config_cli.register_root(app, version=__version__, backend="typer")
_config_cli.register(app, group_name="config", backend="typer")


from .chat_cli import ChatCLI

_chat_cli = ChatCLI()
_chat_cli.register(app, backend="typer")


from .mcp_cli import McpCLI

_mcp_cli = McpCLI()
_mcp_cli.register(app, group_name="mcp", backend="typer")


from .capsule_cli import CapsuleCLI

_capsule_cli = CapsuleCLI()
_capsule_cli.register(app, group_name="capsule", backend="typer")


from .tr_cli import TrCLI

_tr_cli = TrCLI()
_tr_cli.register(app, group_name="tr", backend="typer")

apply_cli_aliases(app, ())


import typer.main as _typer_main

cli = _typer_main.get_command(app)


def main():
    """Entry point for the AgentHeaven CLI."""
    app()
