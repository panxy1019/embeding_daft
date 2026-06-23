"""\
Chat CLI for AgentHeaven.

Provides a modular :class:`ChatCLI` class with backend-agnostic ``do_*``
methods and ``register_click`` / ``register_typer`` / ``register`` dispatchers
following the same pattern as :class:`~ahvn.utils.basic.cli_utils.ConfigCLI`.
"""

__all__ = ["ChatCLI"]

import html
from typing import List, Literal, Optional

from ..utils.basic.color_utils import color_error, color_grey, color_warning
from ..utils.basic.config_utils import CM_AHVN
from ..utils.basic.debug_utils import error_str
from ..utils.basic.log_utils import get_logger
from ..utils.basic.serialize_utils import load_txt

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# prompt_toolkit session helpers (module-level, UI only)
# ---------------------------------------------------------------------------

from prompt_toolkit import PromptSession, prompt as pt_prompt
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import HTML as HTML_print
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.shortcuts import print_formatted_text
from prompt_toolkit.styles import Style

_CUSTOM_STYLE = Style.from_dict(
    {
        "ansigreen": "ansigreen",
        "ansiblue": "ansiblue",
        "ansigrey": "ansibrightblack",
    }
)

_SESSION_COMMANDS = ["/exit", "/quit", "/bye", "/help", "/save", "/load", "/clear", "/regen", "/back"]


def create_chat_session() -> PromptSession:
    """\
    Create a PromptSession for interactive chat with key bindings and history.
    """
    bindings = KeyBindings()

    @bindings.add(Keys.ControlC)
    def _(event):
        event.app.exit(result="/quit")

    @bindings.add(Keys.ControlD)
    def _(event):
        event.app.exit(result="/quit")

    return PromptSession(
        history=InMemoryHistory(),
        key_bindings=bindings,
        multiline=False,
        wrap_lines=True,
        complete_style="column",
        mouse_support=True,
        enable_history_search=True,
        completer=WordCompleter(_SESSION_COMMANDS, ignore_case=True),
        complete_while_typing=True,
    )


def _get_user_input(session: Optional[PromptSession] = None) -> str:
    """\
    Get one line of user input (prompt_toolkit or fallback).
    """
    placeholder = "Type your message... (/bye or /exit to exit, /help for more commands)"
    prompt_html = HTML_print(f"<ansigrey>{html.escape(placeholder)}</ansigrey>")
    try:
        if session:
            return session.prompt(
                HTML_print("<ansiblue>>>> </ansiblue>"),
                placeholder=prompt_html,
                complete_style="column",
                style=_CUSTOM_STYLE,
            ).strip()
        return pt_prompt(">>> ", placeholder=prompt_html, style=_CUSTOM_STYLE).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _show_session_help():
    """\
    Print available session slash commands.
    """
    help_text = """\
<ansiblue><b>Available Commands:</b></ansiblue>
    <ansigreen>/exit, /quit, /bye, /e, /q</ansigreen>       - Exit the session
    <ansigreen>/help, /h, /?, /commands</ansigreen>         - Show this help message
    <ansigreen>/save [path], /s [path]</ansigreen>          - Save session to a file (default: session.json)
    <ansigreen>/load [path], /l [path]</ansigreen>          - Load session from a file
    <ansigreen>/clear, /c</ansigreen>                       - Clear session context
    <ansigreen>/regen [seed], /r [seed]</ansigreen>         - Regenerate last response
    <ansigreen>/back, /b</ansigreen>                        - Remove last interaction
    <ansigreen>Ctrl+C or Ctrl+D</ansigreen>                 - Exit the session
"""
    print_formatted_text(HTML_print(help_text), style=_CUSTOM_STYLE)


def _user_input_loop(messages: list, session: Optional[PromptSession] = None):
    """\
    Loop for user input until a non-command message is received.

    Returns:
        (user_content, should_exit, gen_kwargs) tuple.
    """
    import click

    while True:
        raw = _get_user_input(session)
        cmd = raw.lower()

        if cmd in ("/exit", "/quit", "/bye", "/e", "/q"):
            return "", True, {}
        if cmd in ("/help", "/h", "/?", "/commands"):
            _show_session_help()
            continue
        if cmd in ("/s", "/save") or cmd.startswith("/s ") or cmd.startswith("/save "):
            path = (raw[6:] if cmd.startswith("/save ") else raw[3:] if cmd.startswith("/s ") else "").strip() or "session.json"
            from ..utils.basic.serialize_utils import save_json

            save_json(messages, path)
            continue
        if cmd.startswith("/l ") or cmd.startswith("/load "):
            path = (raw[6:] if cmd.startswith("/load ") else raw[3:]).strip() or "session.json"
            from ..utils.basic.serialize_utils import load_json

            try:
                messages.clear()
                messages.extend(load_json(path))
            except FileNotFoundError:
                click.echo(color_error(f"File not found: {path}"), err=True)
            continue
        if cmd in ("/c", "/clear"):
            messages.clear()
            click.echo(color_grey("Session context cleared."))
            continue
        if cmd.split()[0] in ("/r", "/regen"):
            parts = raw.split()
            if len(messages) > 1 and messages[-1]["role"] == "assistant":
                last = messages.pop()
                seed = None
                if len(parts) > 1:
                    try:
                        seed = int(parts[1])
                    except ValueError:
                        click.echo(color_warning(f"Invalid seed: {parts[1]}. Using default."))
                if seed is None:
                    from ..utils.basic.hash_utils import md5hash

                    seed = md5hash(last["content"]) % 1_000_000
                click.echo(color_grey(f"Regenerating... (seed: {seed})"))
                return None, False, {"seed": seed}
            click.echo(color_warning("Nothing to regenerate."))
            continue
        if cmd in ("/b", "/back"):
            if len(messages) >= 2:
                messages.pop()
                messages.pop()
                click.echo(color_grey("Back one step."))
            else:
                click.echo(color_warning("Cannot go back further."))
            continue
        if raw.startswith("/"):
            click.echo(color_warning(f"Unrecognized command: {raw}. Type /help for help."))
            continue
        if not raw:
            continue
        return raw, False, {}


# ---------------------------------------------------------------------------
# ChatCLI – backend-agnostic operations
# ---------------------------------------------------------------------------


class ChatCLI:
    """\
    Modular chat CLI following the same pattern as
    :class:`~ahvn.utils.basic.cli_utils.ConfigCLI`.

    Core operations are in the ``do_*`` methods; ``register_click``,
    ``register_typer``, and ``register`` attach them to a CLI framework.
    """

    # =====================================================================
    # Core operations (backend-agnostic)
    # =====================================================================

    def do_chat(
        self,
        prompt: Optional[str] = None,
        system: Optional[str] = None,
        input_files: Optional[List[str]] = None,
        cache: bool = True,
        stream: bool = True,
        preset: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        verbose: bool = False,
    ):
        """\
        One-shot chat with an LLM.
        """
        import click
        from ..utils.llm import LLM
        from ..cache import DiskCache

        try:
            llm = LLM(
                cache=(None if not cache else DiskCache(CM_AHVN.pj(CM_AHVN.get("core.cache_path", "~/.ahvn/cache/"), "session_cli", abs=True))),
                preset="chat" if preset is None else preset,
                model=model,
                provider=provider,
            )
        except Exception as e:
            click.echo(color_error(f"Error initializing LLM: {error_str(e)}"), err=True)
            raise SystemExit(1)

        user_contents = []
        for file_path in input_files or []:
            try:
                content = load_txt(file_path)
                user_contents.append(f"=== Content from {file_path} Start ===\n{content.strip()}\n=== Content from {file_path} End ===")
                if verbose:
                    click.echo(color_grey(f"Read {len(content)} characters from {file_path}"))
            except Exception as e:
                click.echo(f"Error reading file {file_path}: {e}", err=True)
                raise SystemExit(1)
        user_contents.append("" if prompt is None else prompt.strip())
        user_content = "\n\n".join(user_contents)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content})

        try:
            if stream:
                in_think = False
                for delta in llm.stream(messages, include=["text", "think"], reduce=False, verbose=verbose):
                    think_chunk = delta.get("think", "")
                    text_chunk = delta.get("text", "")
                    if think_chunk:
                        if not in_think:
                            # click.echo(color_grey("<think>"), nl=False)
                            in_think = True
                        click.echo(color_grey(think_chunk), nl=False)
                    if text_chunk:
                        if in_think:
                            # click.echo(color_grey("\n</think>\n"))
                            click.echo(color_grey("\n"))
                            in_think = False
                        click.echo(text_chunk, nl=False)
                if in_think:
                    click.echo(color_grey("\n</think>\n"))
                click.echo()
            else:
                response = llm.oracle(messages, include=["content"], verbose=verbose)
                if response:
                    click.echo(response)
        except KeyboardInterrupt:
            click.echo("\nChat interrupted.", err=True)
            raise SystemExit(1)
        except Exception as e:
            click.echo(f"Error during chat: {e}", err=True)
            raise SystemExit(1)

    def do_embed(
        self,
        prompt: Optional[str] = None,
        input_file: Optional[str] = None,
        cache: bool = True,
        preset: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        verbose: bool = False,
    ):
        """\
        Embed text or a file.
        """
        import click
        from ..utils.llm import LLM
        from ..cache import DiskCache

        if input_file and prompt:
            click.echo("Error: --input-file/-i and prompt are mutually exclusive.", err=True)
            raise SystemExit(1)

        llm = LLM(
            cache=(None if not cache else DiskCache(CM_AHVN.pj(CM_AHVN.get("core.cache_path", "~/.ahvn/cache/"), "embed_cli", abs=True))),
            preset="embedder" if preset is None else preset,
            model=model,
            provider=provider,
        )

        user_content = "" if prompt is None else prompt.strip()
        if input_file:
            try:
                user_content = load_txt(input_file).strip()
                if verbose:
                    click.echo(color_grey(f"Read {len(user_content)} characters from {input_file}"))
            except Exception as e:
                click.echo(f"Error reading file {input_file}: {e}", err=True)
                raise SystemExit(1)

        try:
            click.echo(llm.embed(user_content, verbose=verbose))
        except Exception as e:
            click.echo(f"Error during embedding: {e}", err=True)
            raise SystemExit(1)

    def do_session(
        self,
        prompt: Optional[str] = None,
        system: Optional[str] = None,
        input_files: Optional[List[str]] = None,
        cache: bool = True,
        stream: bool = True,
        preset: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        verbose: bool = False,
    ):
        """\
        Start an interactive chat session.
        """
        import click
        from ..utils.llm import LLM, gather_assistant_message
        from ..cache import DiskCache

        chat_session = create_chat_session()
        click.echo(color_grey("Session started. Type /help for commands, /bye or /exit to quit."))

        try:
            llm = LLM(
                cache=(None if not cache else DiskCache(CM_AHVN.pj(CM_AHVN.get("core.cache_path", "~/.ahvn/cache/"), "session_cli", abs=True))),
                preset="chat" if preset is None else preset,
                model=model,
                provider=provider,
            )
        except Exception as e:
            click.echo(color_error(f"Error initializing LLM: {e}"), err=True)
            raise SystemExit(1)

        user_contents = []
        for file_path in input_files or []:
            try:
                content = load_txt(file_path)
                user_contents.append(f"=== Content from {file_path} Start ===\n{content.strip()}\n=== Content from {file_path} End ===")
                if verbose:
                    click.echo(color_grey(f"Read {len(content)} characters from {file_path}"))
            except Exception as e:
                click.echo(f"Error reading file {file_path}: {e}", err=True)
                raise SystemExit(1)
        user_contents.append("" if prompt is None else prompt.strip())

        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        gen_kwargs = {}
        if prompt is None:
            user_input, user_exit, user_kwargs = _user_input_loop(messages=messages, session=chat_session)
            user_contents.append(user_input)
            gen_kwargs = user_kwargs
        else:
            user_exit = False

        user_content = "\n\n".join(c for c in user_contents if c)

        while not user_exit:
            try:
                if user_content is not None:
                    messages.append({"role": "user", "content": user_content})
                if stream:
                    responses = []
                    in_think = False
                    for delta in llm.stream(messages, include=["text", "think", "message"], reduce=False, verbose=verbose, **gen_kwargs):
                        think_chunk = delta.get("think", "")
                        text_chunk = delta.get("text", "")
                        msg = delta.get("message", {})
                        if think_chunk:
                            if not in_think:
                                # click.echo(color_grey("<think>"), nl=False)
                                in_think = True
                            click.echo(color_grey(think_chunk), nl=False)
                        if text_chunk:
                            if in_think:
                                # click.echo(color_grey("\n</think>\n"))
                                click.echo(color_grey("\n"))
                                in_think = False
                            click.echo(text_chunk, nl=False)
                        if msg:
                            responses.append(msg)
                    if in_think:
                        click.echo(color_grey("\n</think>\n"))
                    assistant_message = gather_assistant_message(responses)
                    click.echo()
                else:
                    assistant_message = llm.oracle(messages, include=["message"], verbose=verbose, **gen_kwargs)
                    click.echo(assistant_message.get("content", ""))
                messages.append(assistant_message)
                user_content, user_exit, user_kwargs = _user_input_loop(messages=messages, session=chat_session)
                gen_kwargs = user_kwargs
            except KeyboardInterrupt:
                break
            except Exception as e:
                if messages and messages[-1]["role"] == "user":
                    messages.pop()
                click.echo(color_error(f"\nError: {e}"), err=True)
                user_content, user_exit, gen_kwargs = _user_input_loop(messages=messages, session=chat_session)

    # =====================================================================
    # Click backend
    # =====================================================================

    def register_click(self, parent):
        """\
        Register chat, embed, and session commands on a **Click** group.
        """
        import click
        from ..utils.basic.cli_utils import apply_cli_aliases

        ref = self

        @parent.command(
            "chat",
            help="Chat with an LLM.\n\nExamples:\n  ahvn chat 'Hello!'\n  ahvn chat -s 'You are helpful' 'What is Python?'\n  ahvn chat -i file.txt 'Summarize'",
        )
        @click.argument("prompt", required=False)
        @click.option("--system", "-s", help="System prompt.")
        @click.option("--input-files", "-i", multiple=True, type=click.Path(exists=True, readable=True), help="Input files to include.")
        @click.option("--cache/--no-cache", default=True, help="Enable/disable caching. Default: enabled.")
        @click.option("--stream/--no-stream", default=True, help="Enable/disable streaming. Default: enabled.")
        @click.option("--preset", "-p", help="LLM preset (default: 'chat').")
        @click.option("--model", "-m", help="LLM model to use.")
        @click.option("--provider", "-b", help="LLM provider to use.")
        @click.option("--verbose", "-v", is_flag=True, help="Show debug information.")
        def chat_cmd(prompt, system, input_files, cache, stream, preset, model, provider, verbose):
            ref.do_chat(prompt, system, list(input_files), cache, stream, preset, model, provider, verbose)

        @parent.command(
            "embed",
            help="Embed text or a file.\n\nExamples:\n  ahvn embed 'Embed this sentence.'\n  ahvn embed -i file.txt",
        )
        @click.argument("prompt", required=False)
        @click.option("--input-file", "-i", type=click.Path(exists=True, readable=True), help="Input file to embed.")
        @click.option("--cache/--no-cache", default=True, help="Enable/disable caching. Default: enabled.")
        @click.option("--preset", "-p", help="LLM preset (default: 'embedder').")
        @click.option("--model", "-m", help="LLM model to use.")
        @click.option("--provider", "-b", help="LLM provider to use.")
        @click.option("--verbose", "-v", is_flag=True, help="Show debug information.")
        def embed_cmd(prompt, input_file, cache, preset, model, provider, verbose):
            ref.do_embed(prompt, input_file, cache, preset, model, provider, verbose)

        @parent.command(
            "session",
            help="Start an interactive chat session.\n\nExamples:\n  ahvn session\n  ahvn session -s 'You are a helpful assistant'\n  ahvn session -i context.txt",
        )
        @click.argument("prompt", required=False)
        @click.option("--system", "-s", help="System prompt.")
        @click.option("--input-files", "-i", multiple=True, type=click.Path(exists=True, readable=True), help="Input files to include.")
        @click.option("--cache/--no-cache", default=True, help="Enable/disable caching. Default: enabled.")
        @click.option("--stream/--no-stream", default=True, help="Enable/disable streaming. Default: enabled.")
        @click.option("--preset", "-p", help="LLM preset (default: 'chat').")
        @click.option("--model", "-m", help="LLM model to use.")
        @click.option("--provider", "-b", help="LLM provider to use.")
        @click.option("--verbose", "-v", is_flag=True, help="Show debug information.")
        def session_cmd(prompt, system, input_files, cache, stream, preset, model, provider, verbose):
            ref.do_session(prompt, system, list(input_files), cache, stream, preset, model, provider, verbose)

        apply_cli_aliases(parent, ())

    # =====================================================================
    # Typer backend
    # =====================================================================

    def register_typer(self, parent):
        """\
        Register chat, embed, and session commands on a **Typer** app.
        """
        import typer
        from typing import Annotated
        from ..utils.basic.cli_utils import apply_cli_aliases

        ref = self

        @parent.command("chat", help="Chat with an LLM.")
        def chat_cmd(
            prompt: Annotated[Optional[str], typer.Argument(help="The message to send.")] = None,
            system: Annotated[Optional[str], typer.Option("-s", "--system", help="System prompt.")] = None,
            input_files: Annotated[Optional[List[str]], typer.Option("-i", "--input-files", help="Input files to include.")] = None,
            cache: Annotated[bool, typer.Option("--cache/--no-cache", help="Enable/disable caching.")] = True,
            stream: Annotated[bool, typer.Option("--stream/--no-stream", help="Enable/disable streaming.")] = True,
            preset: Annotated[Optional[str], typer.Option("-p", "--preset", help="LLM preset.")] = None,
            model: Annotated[Optional[str], typer.Option("-m", "--model", help="LLM model.")] = None,
            provider: Annotated[Optional[str], typer.Option("-b", "--provider", help="LLM provider.")] = None,
            verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Show debug information.")] = False,
        ):
            ref.do_chat(prompt, system, input_files or [], cache, stream, preset, model, provider, verbose)

        @parent.command("embed", help="Embed text or a file.")
        def embed_cmd(
            prompt: Annotated[Optional[str], typer.Argument(help="Text to embed.")] = None,
            input_file: Annotated[Optional[str], typer.Option("-i", "--input-file", help="Input file to embed.")] = None,
            cache: Annotated[bool, typer.Option("--cache/--no-cache", help="Enable/disable caching.")] = True,
            preset: Annotated[Optional[str], typer.Option("-p", "--preset", help="LLM preset.")] = None,
            model: Annotated[Optional[str], typer.Option("-m", "--model", help="LLM model.")] = None,
            provider: Annotated[Optional[str], typer.Option("-b", "--provider", help="LLM provider.")] = None,
            verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Show debug information.")] = False,
        ):
            ref.do_embed(prompt, input_file, cache, preset, model, provider, verbose)

        @parent.command("session", help="Start an interactive chat session.")
        def session_cmd(
            prompt: Annotated[Optional[str], typer.Argument(help="Initial message (optional).")] = None,
            system: Annotated[Optional[str], typer.Option("-s", "--system", help="System prompt.")] = None,
            input_files: Annotated[Optional[List[str]], typer.Option("-i", "--input-files", help="Input files to include.")] = None,
            cache: Annotated[bool, typer.Option("--cache/--no-cache", help="Enable/disable caching.")] = True,
            stream: Annotated[bool, typer.Option("--stream/--no-stream", help="Enable/disable streaming.")] = True,
            preset: Annotated[Optional[str], typer.Option("-p", "--preset", help="LLM preset.")] = None,
            model: Annotated[Optional[str], typer.Option("-m", "--model", help="LLM model.")] = None,
            provider: Annotated[Optional[str], typer.Option("-b", "--provider", help="LLM provider.")] = None,
            verbose: Annotated[bool, typer.Option("-v", "--verbose", help="Show debug information.")] = False,
        ):
            ref.do_session(prompt, system, input_files or [], cache, stream, preset, model, provider, verbose)

        apply_cli_aliases(parent, ())

    # =====================================================================
    # Unified dispatcher
    # =====================================================================

    def register(self, parent, backend: Literal["click", "typer", "argparse"] = "click"):
        """\
        Register chat commands on a CLI framework determined by *backend*.

        Args:
            parent: The parent CLI object (Click group or Typer app).
            backend: Which CLI framework to target ("click" or "typer").
        """
        if backend == "click":
            return self.register_click(parent)
        elif backend == "typer":
            return self.register_typer(parent)
        else:
            raise ValueError(f"Unsupported backend: {backend!r}")
