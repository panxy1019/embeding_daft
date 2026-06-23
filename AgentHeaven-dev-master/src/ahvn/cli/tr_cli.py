"""\
Translation CLI for AgentHeaven.

:class:`TrCLI` with ``do_*`` backend-agnostic methods and Click/Typer registration.

Commands::

    ahvn tr list (ls)             List namespaces or entries
    ahvn tr show     -n NS                        Show namespace metadata
    ahvn tr render   -n NS [-l LANG] [--version V] [--args K=V ...]
                                             Render prompt text with translations
    ahvn tr get      -n NS -l LANG KEY          Get a single translation
    ahvn tr set      -n NS -l LANG KEY VALUE    Set a translation
    ahvn tr remove (rm, del) -n NS -l LANG KEY  Delete a translation entry
    ahvn tr clear     [-n NS]                    Clear one namespace or all
    ahvn tr stale                                List stale namespaces
    ahvn tr missing  -n NS -l LANG [-r REF]     Show keys missing for LANG
    ahvn tr export   -n NS [-o FILE]            Export namespace to JSON
    ahvn tr import   FILE [-n NS]               Import translations from JSON
    ahvn tr fill     -n NS [-l LANG ...] [KEY]  Interactively translate missing entries
"""

__all__ = ["TrCLI"]

import json
import sys
from typing import List, Literal, Optional

from .tool_cli_utils import parse_kv_args


class TrCLI:
    """Translation CLI with Click and Typer registration."""

    def __init__(self):
        from ..utils.basic.cli_utils import CLIOutput

        self.out = CLIOutput()

    # -- helpers ------------------------------------------------------ #

    @staticmethod
    def _get_store():
        from ahvn.utils.prompt.translate import get_translation_store

        return get_translation_store()

    def _echo(self, msg: str, err: bool = False):
        self.out.echo(msg, err=err)

    # -- do_* operations ---------------------------------------------- #

    def do_list(self, namespace: Optional[str] = None, lang: Optional[str] = None):
        store = self._get_store()
        if namespace is None:
            for ns in store.list():
                self._echo(
                    f"  {ns['id']}  main_lang={ns['main_lang']}  langs={ns['languages']}  entries={ns['entry_count']}  " f"updated_at={ns.get('updated_at')}"
                )
        else:
            for e in store.get_entries(namespace, lang=lang):
                self._echo(f"  [{e['lang']}] {e['key']}  →  {e['value']}")

    def do_get(self, namespace: str, key: str, lang: str):
        store = self._get_store()
        val = store.get_value(namespace, lang, key)
        if val is None:
            self._echo(f"Not found: namespace='{namespace}' key='{key}' lang='{lang}'", err=True)
            sys.exit(1)
        self._echo(val)

    def do_show(self, namespace: str):
        """Show translation namespace metadata in a unified table format."""
        store = self._get_store()
        info = store.info(namespace)
        if info is None:
            self._echo(f"Namespace not found: '{namespace}'", err=True)
            sys.exit(1)

        languages = info.get("languages") or []
        if isinstance(languages, list):
            languages = ", ".join(str(x) for x in languages)
        rows = [
            ["Name", namespace],
            ["ID", str(info.get("id", namespace))],
            ["Main Lang", str(info.get("main_lang", "en"))],
            ["Languages", str(languages)],
            ["Entries", str(info.get("entry_count", 0))],
            ["Created", str(info.get("created_at", ""))[:19]],
            ["Updated", str(info.get("updated_at", ""))[:19]],
        ]
        self.out.table(f"Translation Namespace: {namespace}", ["Field", "Value"], rows)

    def do_render(
        self,
        namespace: str,
        lang: Optional[str] = None,
        version: Optional[str] = None,
        args: Optional[List[str]] = None,
        render_raw: bool = False,
    ):
        """Render translated prompt text for a prompt namespace."""
        from ahvn.utils.prompt.prompt_spec import TR_AHVN
        from ahvn.utils.basic.serialize_utils import dumps_json

        try:
            parsed_args = parse_kv_args(args or [])
            rendered = TR_AHVN.render(
                namespace,
                args=parsed_args,
                lang=lang,
                version=version,
                as_text=not render_raw,
            )
        except Exception as exc:
            self._echo(str(exc), err=True)
            sys.exit(1)
        if rendered is None:
            self._echo(f"Prompt not found for namespace '{namespace}'.", err=True)
            sys.exit(1)
        if render_raw:
            self._echo(dumps_json(rendered, ensure_ascii=False, indent=2))
        else:
            self._echo(str(rendered))

    def do_set(self, namespace: str, key: str, lang: str, value: str, main_lang: str = "en"):
        from ahvn.utils.prompt.translate import TranslationDict

        store = self._get_store()
        td = TranslationDict(namespace=namespace, main_lang=main_lang, store=store)
        td.set(key, lang, value)
        self._echo(f"Set: [{lang}] {key}  →  {value}")

    def do_remove(self, namespace: str, key: str, lang: str):
        store = self._get_store()
        store.delete_entry(namespace, key, lang)
        self._echo(f"Deleted: [{lang}] {key}")

    def do_clear(self, namespace: Optional[str] = None):
        store = self._get_store()
        if namespace:
            if not store.exists(namespace):
                self._echo(f"Namespace not found: '{namespace}'", err=True)
                sys.exit(1)
            store.remove(namespace)
            self._echo(f"Cleared namespace '{namespace}'.")
            return
        count = store.clear()
        self._echo(f"Cleared all translations ({count} value row(s) removed).")

    def do_stale(self):
        store = self._get_store()
        stale_items = store.stale()
        if not stale_items:
            self._echo("No stale translation namespaces.")
            return
        self._echo(f"Stale namespaces ({len(stale_items)}):")
        for item in stale_items:
            self._echo(
                f"  {item['id']}  main_lang={item.get('main_lang', 'en')}  " f"entries={item.get('entry_count', 0)}  updated_at={item.get('updated_at')}"
            )

    def do_missing(self, namespace: str, lang: str, ref_lang: Optional[str] = None):
        from ahvn.utils.prompt.translate import TranslationDict

        store = self._get_store()
        td = TranslationDict(namespace=namespace, store=store)
        missing = td.missing_keys(lang, ref_lang=ref_lang)
        if not missing:
            self._echo(f"No missing keys for '{lang}' in namespace '{namespace}'.")
            return
        self._echo(f"Missing {len(missing)} key(s) for '{lang}':")
        for k in missing:
            self._echo(f"  - {k}")

    def do_fill(self, namespace: str, langs: Optional[List[str]] = None, key: Optional[str] = None):
        from ahvn.utils.prompt.translate import TranslationDict

        store = self._get_store()
        td = TranslationDict(namespace=namespace, store=store)

        target_langs = langs or td.languages()
        if not target_langs:
            self._echo(f"No languages registered for namespace '{namespace}'.")
            return

        pairs: List[tuple] = []
        for lang in target_langs:
            if key:
                if td.lookup(key, lang) is None:
                    pairs.append((key, lang))
            else:
                for k in td.missing_keys(lang):
                    pairs.append((k, lang))

        if not pairs:
            self._echo(f"No missing translations for namespace '{namespace}'.")
            return

        self._echo(f"Found {len(pairs)} missing translation(s). Enter translation or press Enter to skip.\n")
        translated = 0
        for i, (k, lang) in enumerate(pairs, 1):
            try:
                user_input = input(f"  [{i}/{len(pairs)}] [{lang}] {k}\n  → ")
            except (EOFError, KeyboardInterrupt):
                self._echo("\nFill interrupted.")
                break
            value = user_input.strip()
            if value:
                td.set(k, lang, value)
                translated += 1
        self._echo(f"\nFilled {translated}/{len(pairs)} translation(s).")

    def do_export(self, namespace: str, output: Optional[str] = None):
        from ahvn.utils.prompt.translate import TranslationDict

        store = self._get_store()
        td = TranslationDict(namespace=namespace, store=store)
        text = json.dumps(td.to_dict(), ensure_ascii=False, indent=2)
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            self._echo(f"Exported namespace '{namespace}' to {output}")
        else:
            print(text)

    def do_import(self, file: str, namespace: Optional[str] = None):
        from ahvn.utils.prompt.translate import TranslationDict

        store = self._get_store()
        with open(file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if namespace:
            data["namespace"] = namespace
        td = TranslationDict.from_dict(data, store=store, replace=True)
        self._echo(f"Imported {len(td._matcher.exact_map)} entries into namespace '{td.namespace}'.")

    # -- Click registration ------------------------------------------- #

    def register_click(self, parent, group_name: str = "tr"):
        import click
        from ..utils.basic.cli_utils import AliasedGroup, apply_cli_aliases

        ref = self

        @parent.group(group_name, cls=AliasedGroup, help="Translation management.")
        def tr_group():
            pass

        @tr_group.command("list", help="List namespaces or entries.")
        @click.option("-n", "--namespace", default=None)
        @click.option("-l", "--lang", default=None)
        def list_cmd(namespace, lang):
            ref.do_list(namespace, lang)

        @tr_group.command("get", help="Get a single translation value.")
        @click.argument("key")
        @click.option("-n", "--namespace", required=True)
        @click.option("-l", "--lang", required=True)
        def get_cmd(key, namespace, lang):
            ref.do_get(namespace, key, lang)

        @tr_group.command("show", help="Show translation namespace metadata.")
        @click.option("-n", "--namespace", required=True)
        def show_cmd(namespace):
            ref.do_show(namespace)

        @tr_group.command("render", help="Render translated prompt text.")
        @click.option("-n", "--namespace", required=True)
        @click.option("-l", "--lang", default=None, help="Language override when rendering prompt text.")
        @click.option("-v", "--version", default=None, help="Prompt version (default: latest).")
        @click.option("--args", "args", multiple=True, help="Prompt args in key=value format. Repeatable.")
        @click.option("--raw", "render_raw", is_flag=True, help="When rendering, output raw JSON instead of flattened text.")
        def render_cmd(namespace, lang, version, args, render_raw):
            ref.do_render(namespace, lang=lang, version=version, args=list(args), render_raw=render_raw)

        @tr_group.command("set", help="Set a translation entry.")
        @click.argument("key")
        @click.argument("value")
        @click.option("-n", "--namespace", required=True)
        @click.option("-l", "--lang", required=True)
        @click.option("-m", "--main-lang", default="en")
        def set_cmd(key, value, namespace, lang, main_lang):
            ref.do_set(namespace, key, lang, value, main_lang)

        @tr_group.command("unset", help="Delete a translation entry.")
        @click.argument("key")
        @click.option("-n", "--namespace", required=True)
        @click.option("-l", "--lang", required=True)
        def unset_cmd(key, namespace, lang):
            ref.do_remove(namespace, key, lang)

        @tr_group.command("clear", help="Clear one namespace or all translations.")
        @click.option("-n", "--namespace", default=None)
        def clear_cmd(namespace):
            ref.do_clear(namespace)

        @tr_group.command("stale", help="List stale translation namespaces.")
        def stale_cmd():
            ref.do_stale()

        @tr_group.command("missing", help="Show keys missing for a target language.")
        @click.option("-n", "--namespace", required=True)
        @click.option("-l", "--lang", required=True)
        @click.option("-r", "--ref", "ref_lang", default=None)
        def missing_cmd(namespace, lang, ref_lang):
            ref.do_missing(namespace, lang, ref_lang)

        @tr_group.command("export", help="Export namespace translations to JSON.")
        @click.option("-n", "--namespace", required=True)
        @click.option("-o", "--output", default=None)
        def export_cmd(namespace, output):
            ref.do_export(namespace, output)

        @tr_group.command("import", help="Import translations from a JSON file.")
        @click.argument("file")
        @click.option("-n", "--namespace", default=None)
        def import_cmd(file, namespace):
            ref.do_import(file, namespace)

        @tr_group.command("fill", help="Interactively translate missing entries.")
        @click.option("-n", "--namespace", required=True)
        @click.option("-l", "--lang", multiple=True)
        @click.argument("key", required=False, default=None)
        def fill_cmd(namespace, lang, key):
            ref.do_fill(namespace, list(lang) or None, key)

        apply_cli_aliases(tr_group, "tr")
        return tr_group

    # -- Typer registration ------------------------------------------- #

    def register_typer(self, parent, group_name: str = "tr"):
        import typer
        from typing import Annotated
        from ..utils.basic.cli_utils import AliasedTyper, apply_cli_aliases

        tr_app = AliasedTyper(help="Translation management.")
        parent.add_typer(tr_app, name=group_name)
        ref = self

        @tr_app.command("list", help="List namespaces or entries.")
        def list_cmd(
            namespace: Annotated[Optional[str], typer.Option("-n", "--namespace")] = None,
            lang: Annotated[Optional[str], typer.Option("-l", "--lang")] = None,
        ):
            ref.do_list(namespace, lang)

        @tr_app.command("get", help="Get a single translation value.")
        def get_cmd(
            key: Annotated[str, typer.Argument(help="Source key.")],
            namespace: Annotated[str, typer.Option("-n", "--namespace")] = ...,
            lang: Annotated[str, typer.Option("-l", "--lang")] = ...,
        ):
            ref.do_get(namespace, key, lang)

        @tr_app.command("show", help="Show translation namespace metadata.")
        def show_cmd(
            namespace: Annotated[str, typer.Option("-n", "--namespace")] = ...,
        ):
            ref.do_show(namespace)

        @tr_app.command("render", help="Render translated prompt text.")
        def render_cmd(
            namespace: Annotated[str, typer.Option("-n", "--namespace")] = ...,
            lang: Annotated[Optional[str], typer.Option("-l", "--lang", help="Language override for rendered prompt text.")] = None,
            version: Annotated[Optional[str], typer.Option("-v", "--version", help="Prompt version (default: latest).")] = None,
            args: Annotated[Optional[List[str]], typer.Option("--args", help="Prompt args in key=value format (repeatable).")] = None,
            render_raw: Annotated[bool, typer.Option("--raw", help="When rendering, output raw JSON instead of flattened text.")] = False,
        ):
            ref.do_render(namespace, lang=lang, version=version, args=args, render_raw=render_raw)

        @tr_app.command("set", help="Set a translation entry.")
        def set_cmd(
            key: Annotated[str, typer.Argument(help="Source key.")],
            value: Annotated[str, typer.Argument(help="Translated value.")],
            namespace: Annotated[str, typer.Option("-n", "--namespace")] = ...,
            lang: Annotated[str, typer.Option("-l", "--lang")] = ...,
            main_lang: Annotated[str, typer.Option("-m", "--main-lang")] = "en",
        ):
            ref.do_set(namespace, key, lang, value, main_lang)

        @tr_app.command("unset", help="Delete a translation entry.")
        def unset_cmd(
            key: Annotated[str, typer.Argument(help="Source key.")],
            namespace: Annotated[str, typer.Option("-n", "--namespace")] = ...,
            lang: Annotated[str, typer.Option("-l", "--lang")] = ...,
        ):
            ref.do_remove(namespace, key, lang)

        @tr_app.command("clear", help="Clear one namespace or all translations.")
        def clear_cmd(
            namespace: Annotated[Optional[str], typer.Option("-n", "--namespace")] = None,
        ):
            ref.do_clear(namespace)

        @tr_app.command("stale", help="List stale translation namespaces.")
        def stale_cmd():
            ref.do_stale()

        @tr_app.command("missing", help="Show keys missing for a target language.")
        def missing_cmd(
            namespace: Annotated[str, typer.Option("-n", "--namespace")] = ...,
            lang: Annotated[str, typer.Option("-l", "--lang")] = ...,
            ref_lang: Annotated[Optional[str], typer.Option("-r", "--ref")] = None,
        ):
            ref.do_missing(namespace, lang, ref_lang)

        @tr_app.command("export", help="Export namespace translations to JSON.")
        def export_cmd(
            namespace: Annotated[str, typer.Option("-n", "--namespace")] = ...,
            output: Annotated[Optional[str], typer.Option("-o", "--output")] = None,
        ):
            ref.do_export(namespace, output)

        @tr_app.command("import", help="Import translations from a JSON file.")
        def import_cmd(
            file: Annotated[str, typer.Argument(help="Path to JSON file.")],
            namespace: Annotated[Optional[str], typer.Option("-n", "--namespace")] = None,
        ):
            ref.do_import(file, namespace)

        @tr_app.command("fill", help="Interactively translate missing entries.")
        def fill_cmd(
            key: Annotated[Optional[str], typer.Argument(help="Specific key (optional).")] = None,
            namespace: Annotated[str, typer.Option("-n", "--namespace")] = ...,
            lang: Annotated[Optional[List[str]], typer.Option("-l", "--lang")] = None,
        ):
            ref.do_fill(namespace, lang, key)

        apply_cli_aliases(tr_app, "tr")
        return tr_app

    # -- dispatch ----------------------------------------------------- #

    def register(self, parent, group_name: str = "tr", backend: Literal["typer"] = "typer"):
        return self.register_typer(parent, group_name)
