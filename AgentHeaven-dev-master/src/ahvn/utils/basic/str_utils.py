"""\
String manipulation and text processing utilities for AgentHeaven.
"""

__all__ = [
    "truncate",
    "value_repr",
    "omission_list",
    "md_symbol",
    "line_numbered",
    "indent",
    "bullet_block",
    "bullet_list",
    "numbered_list",
    "md_block",
    "code_block",
    "file_block",
    "json_block",
    "tag_block",
    "format_value",
    "bullet_dict",
    "md_section",
    "example_block",
    "is_delimiter",
    "is_spacy_available",
    "normalize_text",
    "indexed_normalize_text",
    "generate_ngrams",
    "asymmetric_jaccard_score",
    "resolve_match_conflicts",
]

import string
import textwrap
from typing import Any, Union, Set, List, Optional, Tuple, Callable


def truncate(s: str, cutoff: int = -1) -> str:
    """\
    Truncate a string if it exceeds the specified cutoff length.

    Args:
        s (str): The string to truncate.
        cutoff (int): Maximum length before truncation. Defaults to -1, meaning no cutoff.

    Returns:
        str: Truncated string if it exceeds cutoff, otherwise the original string.
    """
    if cutoff < 0 or len(s) <= cutoff:
        return s
    return s[: cutoff - 4] + "..." + s[-1:]


from decimal import Decimal


def value_repr(value: Any, cutoff: int = -1, round_digits: int = 6) -> str:
    """\
    Format a value representation for display, truncating if too long.

    Args:
        value (Any): The value to represent.
        cutoff (int): Maximum length before truncation. Defaults to -1, meaning no cutoff.
        round_digits (int): Number of decimal places to round floats to.
            Only applied if the value is a float. Default is 6.

    Returns:
        str: Formatted value representation.
    """
    if isinstance(value, Decimal):
        value = round(float(value), round_digits)
    elif isinstance(value, float):
        value = round(value, round_digits)
    value_repr_str = repr(value)
    return truncate(value_repr_str, cutoff=cutoff)


def omission_list(items: List, top: int = -1, bottom: int = 1) -> List:
    """\
    Cuts down a list by omitting middle items if it exceeds the specified limit.

    Args:
        items (List): The list of items.
        top (int): Number of items to keep from the start. Defaults to -1 (keep all).
        bottom (int): Number of items to keep from the end. Defaults to 1.
            Bottom is ignored if top is negative.
            Otherwise, total kept items = top + bottom + 1.

    Returns:
        List: The truncated list with middle items omitted if necessary.
    """
    max_items = -1 if top < 0 else top + bottom
    n = len(items)
    if max_items < 0 or n <= max_items:
        return items
    omitted_cnt = n - max_items
    return items[:top] + [f"... (omitting {omitted_cnt})"] + (items[-bottom:] if bottom > 0 else [])


def md_symbol(content: str):
    """\
    Generate a markdown code block symbol that does not conflict with the content.

    Args:
        content (str): The content to check for conflicts.

    Returns:
        str: A markdown code block symbol (e.g., "```", "``````", etc.) that does not appear in the content.
    """
    symbol = "```"
    while symbol in content:
        symbol += "```"
    return symbol


def line_numbered(content: str, start: int = -1, window: Optional[Tuple[int, int]] = None) -> str:
    """\
    Adds line numbers to the given content starting from the specified number.

    Args:
        content (str): The content to be numbered.
        start (int): The starting line number. If negative, no line numbers
            are added. Defaults to -1.
        window (Optional[Tuple[int, int]]): A tuple specifying the (start, end)
            line numbers to include. If None, includes all lines. Defaults to None.

    Returns:
        str: The content with line numbers added.
    """
    if not isinstance(content, str):
        content = str(content)
    lines = content.splitlines()
    if start >= 0:
        contents = [f"({i:4d})  {line}" for i, line in enumerate(lines, start=start)]
    else:
        contents = [f"{line}" for i, line in enumerate(lines)]
    return "\n".join(contents[window[0] : window[1]] if window is not None else contents)


def indent(s: str, tab: Union[int, str] = 4, **kwargs) -> str:
    """\
    Indent a string by a specified number of spaces or a tab character.

    Args:
        s (str): The string to indent.
        tab (int or str, optional): The number of spaces or a tab character to use for indentation. Defaults to 4 spaces.
        **kwargs: Additional keyword arguments are ignored.

    Returns:
        str: The indented string.
    """
    return textwrap.indent(s, prefix=(" " * tab if isinstance(tab, int) else tab), **kwargs)


def bullet_block(text: str, bullet: str = "-") -> str:
    """\
    Format a block of text with a bullet point.

    Args:
        text (str): The text to format.
        bullet (str, optional): The bullet character to use. Defaults to "-".

    Returns:
        str: The formatted text with a bullet point.
    """
    indent_len = 0 if bullet is None else (len(str(bullet)) + 1)
    if indent_len == 0:
        return text
    return indent(str(text), indent_len).replace(" " * indent_len, f"{bullet} ", 1)


def bullet_list(items: list[str], bullet: str = "-") -> str:
    """\
    Format a list of items with bullet points.

    Args:
        items (list[str]): The list of items to format.
        bullet (str, optional): The bullet character to use. Defaults to "-".

    Returns:
        str: The formatted list with bullet points.
    """
    return "\n".join(bullet_block(item, bullet) for item in items)


def numbered_list(items: list[str]) -> str:
    """\
    Format a list of items with numbered bullet points.

    Args:
        items (list[str]): The list of items to format.

    Returns:
        str: The formatted list with numbered bullet points.
    """
    max_len = len(str(len(items)))
    return "\n".join(bullet_block(item, f"{i + 1}." + " " * (max_len - len(str(i + 1)))) for i, item in enumerate(items))


def md_block(content: str, lang: str = "", header: str = "") -> str:
    """\
    Format content as a markdown code block with an optional language specifier.

    Args:
        content (str): The content to format.
        lang (str, optional): The language specifier for syntax highlighting. Defaults to "".
        header (str, optional): An optional header to include above the code block. Defaults to "".

    Returns:
        str: The formatted markdown code block.
    """
    symbol = md_symbol(content)
    block = f"{symbol}{lang}\n{content}\n{symbol}"
    if header:
        return f"{header}\n{block}"
    return block


def code_block(content: str, lang: str = "", start: int = -1, window: Optional[Tuple[int, int]] = None, header: str = "") -> str:
    """\
    Format content as a markdown code block with optional language specifier and line numbers.

    Args:
        content (str): The content to format.
        lang (str, optional): The language specifier for syntax highlighting. Defaults to "".
        start (int, optional): The starting line number for line numbering. If negative, no line numbers are added. Defaults to -1.
        window (Optional[Tuple[int, int]], optional): A tuple specifying the (start, end) line numbers to include. If None, includes all lines. Defaults to None.
        header (str, optional): An optional header to above the code block. Defaults to "".

    Returns:
        str: The formatted markdown code block with optional line numbers.
    """
    numbered_content = line_numbered(content, start=start, window=window)
    return md_block(numbered_content, lang=lang, header=header)


def file_block(file: str, content: str, lang: str = "", start: int = -1, window: Optional[Tuple[int, int]] = None) -> str:
    """\
    Format content as a markdown code block with an optional file header and line numbers.

    Args:
        file (str): The file name to include in the header.
        content (str): The content to format.
        lang (str, optional): The language specifier for syntax highlighting. Defaults to "".
        start (int, optional): The starting line number for line numbering. If negative, no line numbers are added. Defaults to -1.
        window (Optional[Tuple[int, int]], optional): A tuple specifying the (start, end) line numbers to include. If None, includes all lines. Defaults to None.

    Returns:
        str: The formatted markdown code block with file header and optional line numbers.
    """
    header = f"File: `{file}` ({len(content.splitlines())} lines)"
    return code_block(content, lang=lang, start=start, window=window, header=header)


def json_block(data: Any, start: int = -1, window: Optional[Tuple[int, int]] = None, header: str = "", **kwargs) -> str:
    """\
    Format data as a JSON markdown code block.

    Args:
        data (Any): The data to format as JSON.
        start (int, optional): The starting line number for line numbering. If negative, no line numbers are added. Defaults to -1.
        window (Optional[Tuple[int, int]], optional): A tuple specifying the (start, end) line numbers to include. If None, includes all lines. Defaults to None.
        header (str, optional): An optional header to include above the code block. Defaults to "".
        **kwargs: Additional keyword arguments to pass to `dumps_json`.

    Returns:
        str: The formatted JSON markdown code block.
    """
    from .serialize_utils import dumps_json

    content = dumps_json(data, **kwargs)
    return code_block(content, lang="json", start=start, window=window, header=header)


def tag_block(tag: str, content: str, inline: bool = False) -> str:
    """\
    Format content with a custom tag, either inline or as a block.

    Args:
        tag (str): The tag to use for formatting.
        content (str): The content to format.
        inline (bool, optional): Whether to format as an inline tag or a block. Defaults to False (block).

    Returns:
        str: The formatted content with the custom tag.
    """
    if inline:
        return f"<{tag}>{content}</{tag}>"
    else:
        return f"<{tag}>\n{content}\n</{tag}>"


def format_value(value: Any, schema: Optional[dict] = None, bullet: Optional[str] = None, key: Optional[str] = None, tag: Optional[str] = None) -> str:
    """\
    Format a value based on a specified schema.
    Args:
        value (Any): The value to format.
        schema (dict, optional): An optional schema dict specifying formatting. Defaults to None.
            A schema object should contain at least a "mode" key, and optionally a "kwargs" key.
            Supported formatting mode:
            - "base": Use `str(value)` for the value representation
            - "repr": Use `repr(value)` for the value representation (default)
            - "dump": Use `dumps_json(value, indent=None, **kwargs)` for the value representation
                The "indent" argument allows overriding via `kwargs`, but None gives better formatting.
            - "code": Use `code_block(str(value), **kwargs)` for the value representation
            - "file": Use `file_block(key, str(value), **kwargs)` for the value representation
            - "json": Use `json_block(value, indent=0, **kwargs)` for the value representation
            - "nest": Use `bullet_dict(value, bullet=kwargs.get("bullet", "-"), schema=kwargs.get("schema"))` for the value representation
            - "tag": Use `tag_block(tag=kwargs.get("tag", "value"), content=format_value(value, schema=kwargs.get("schema", "base")), inline=kwargs.get("inline", False))` for the value representation
            - "param": Render a JSON Schema property dict as a human-readable annotation.
                The value should be a JSON Schema property dict (with keys like "type", "description", "enum", "default").
                Extra kwargs: "required" (bool, default False).
                Output: ``  `key`: type *(required)* (v1, v2) = default — description  ``
            - "todo": Use a "TODO" placeholder for the value, used for instances where the output is expected but not yet available.
            - "none": Do not include the value in the output.
            When schema is not provided, default to "repr" mode.
        bullet (str, optional): The bullet character to use for nested formatting modes. Defaults to None.
        key (str, optional): The key associated with the value, it results in a "key: value" format if provided. Defaults to None.
        tag (str, optional): An optional tag to use when the mode is "tag". Defaults to None.
            If None, first resolves to kwargs.get("tag"), then `key`, then "output".

    Returns:
        str: The formatted value based on the schema.
    """
    from .debug_utils import raise_mismatch
    from .serialize_utils import dumps_json

    if schema is None:
        schema = {"mode": "repr", "kwargs": dict()}
    elif isinstance(schema, str):
        schema = {"mode": schema, "kwargs": dict()}
    mode, kwargs = schema.get("mode", "repr"), schema.get("kwargs", dict())
    raise_mismatch(supported=["base", "repr", "dump", "code", "file", "json", "nest", "tag", "param", "todo", "none"], got=mode, name="mode")
    if mode == "base":
        value_str = str(value)
    elif mode == "repr":
        value_str = repr(value)
    elif mode == "dump":
        value_str = dumps_json(value, **({"indent": None} | kwargs))
    elif mode == "code":
        value_str = code_block(str(value), **kwargs)
    elif mode == "file":
        value_str = file_block("value", str(value), **kwargs)
    elif mode == "json":
        indent_len = 0 if bullet is None else (len(str(bullet)) + 1)
        value_str = json_block(value, **({"indent": indent_len} | kwargs))
    elif mode == "nest":
        if not isinstance(value, dict):
            raise ValueError(f"Value must be a dict when using 'nest' mode. Got {type(value).__name__}.")
        value_str = bullet_dict(value, bullet=kwargs.get("bullet", bullet), schema=kwargs.get("schema", None))
    elif mode == "tag":
        tag = tag or kwargs.get("tag") or key or "output"
        value_str = tag_block(tag=tag, content=format_value(value, schema=kwargs.get("schema", "base")), inline=kwargs.get("inline", True))
    elif mode == "param":
        p_type = value.get("type", "any") if isinstance(value, dict) else "any"
        req = " *(required)*" if kwargs.get("required", False) else ""
        enum_vals = value.get("enum") if isinstance(value, dict) else None
        default = value.get("default") if isinstance(value, dict) else None
        p_desc = value.get("description", "") if isinstance(value, dict) else ""
        item = f"`{key}`: {p_type}{req}"
        if enum_vals:
            item += f" ({', '.join(str(v) for v in enum_vals)})"
        if default is not None:
            item += f" = {default}"
        if p_desc:
            item += f" — {p_desc}"
        return item
    elif mode == "todo":
        value_str = "TODO"
    elif mode == "none":
        return None
    if key is not None:
        if (mode in ["code", "file", "json", "nest"]) or (mode == "tag" and not kwargs.get("inline", True)):
            value_str = "\n" + value_str
        if mode != "tag":
            return f"{key}: {value_str}"
    return value_str


def bullet_dict(d: dict, bullet: str = "-", schema: Optional[dict] = None) -> str:
    """\
    Format a dictionary as a bullet list, optionally using a schema for formatting.

    Args:
        d (dict): The dictionary to format.
        bullet (str, optional): The bullet character to use. Defaults to "-".
        schema (dict, optional): An optional schema dict specifying formatting for keys. Defaults to None.
            A schema object should contain at least a "mode" key, and optionally a "kwargs" key.
            Supported formatting mode for each key:
            - "base": Use `str(value)` for the value representation
            - "repr": Use `repr(value)` for the value representation (default)
            - "dump": Use `dumps_json(value, indent=None, **kwargs)` for the value representation
                The "indent" argument allows overriding via `kwargs`, but None gives better formatting.
            - "code": Use `code_block(str(value), **kwargs)` for the value representation
            - "file": Use `file_block(key, str(value), **kwargs)` for the value representation
            - "json": Use `json_block(value, indent=len(bullet)+1, **kwargs)` for the value representation
            - "nest": Use `bullet_dict(value, bullet=kwargs.get("bullet", bullet), schema=kwargs.get("schema", "base"))` for the value representation
            - "tag": Use `tag_block(tag=kwargs.get("tag", key), content=format_value(value, schema=kwargs.get("schema", "base")), inline=kwargs.get("inline", False))` for the value representation
            - "todo": Use a "TODO" placeholder for the value, used for instances where the output is expected but not yet available.
            - "none": Do not include the value in the output.
            When schema is not provided, default to "repr" mode for all keys.
            Use a string directly to specify the same mode for all keys, e.g. `schema="dump"`.

    Returns:
        str: The formatted dictionary as a bullet list.
    """
    from .debug_utils import raise_mismatch
    from .serialize_utils import dumps_json

    if schema is None:
        schema = dict()
    if isinstance(schema, str):
        schema = {k: schema for k in d}
    schema = {k: "repr" for k in d} | schema
    formatted_items = []
    for key, value in d.items():
        mode, kwargs = schema.get(key, "repr"), dict()
        if isinstance(mode, dict):
            mode, kwargs = mode.get("mode", "repr"), mode.get("kwargs", dict())
        key_value_str = format_value(value, schema={"mode": mode, "kwargs": kwargs}, bullet=bullet, key=key)
        if key_value_str is not None:
            formatted_items.append(key_value_str)
    return bullet_list(formatted_items, bullet=bullet)


def md_section(title: Optional[str] = None, level: int = 1, content: Optional[str] = None, sections: List[dict] = None, end: Optional[str] = None) -> str:
    """\
    Format a markdown section with a title and content, optionally including subsections.

    Args:
        title (str): The title of the section. If None, no title is included. Defaults to None.
        level (int): The heading level for the section (e.g., 1 for H1, 2 for H2, etc.). Defaults to 1.
        content (str): The content of the section. Defaults to an empty string.
        sections (List[dict], optional): A list of formatted subsection dictionaries to include under this section. Defaults to None.
        end (str, optional): The string to append at the end of the section. Defaults to "".

    Returns:
        str: The formatted markdown section with the title, content, and optional subsections.
    """
    if title is None:
        formatted = f"{content.strip()}\n" if content is not None else ""
    else:
        formatted = f"{'#' * level} {title}\n{content.strip()}\n" if content is not None else f"{'#' * level} {title}\n"
    if sections:
        for section in sections:
            formatted += "\n"
            formatted += md_section(
                title=section["title"],
                level=level + 1,
                content=section.get("content", None),
                sections=section.get("sections", None),
                end=section.get("end", None),
            )
    if end is not None:
        formatted += f"\n{end}\n"
    return formatted.lstrip()


def example_block(
    inputs: dict,
    output: Any,
    bullet: str = "-",
    inputs_schema: Optional[dict] = None,
    output_schema: Optional[Union[str, dict]] = "tag",
    hints: Optional[List[str]] = None,
    expected: Any = ...,
    notes: Optional[List[str]] = None,
    tag: Optional[str] = None,
    tr: Optional[Callable[[str], str]] = None,
) -> str:
    """\
    Format an example block with inputs and output.

    Args:
        inputs (dict): The input data for the example.
        output (Any): The expected output for the example.
        bullet (str, optional): The bullet character to use for formatting. Defaults to "-".
        inputs_schema (dict, optional): An optional schema dict specifying formatting for input keys. Defaults to None.
        output_schema (str, optional): The schema to use for formatting the output. Defaults to "tag".
        hints (List[str], optional): An optional list of hints to include in the example block. Defaults to None.
        expected (Any, optional): An optional expected value to include in the example block. Defaults to ... (not included).
        notes (List[str], optional): An optional list of additional notes to include in the example block. Defaults to None.
        tag (str, optional): An optional tag to wrap the entire example block with. Defaults to None.

    Returns:
        str: The formatted example block with inputs and output.
    """
    tr = tr or str
    formatted = f"{tr('Inputs')}:\n{bullet_dict(inputs, bullet=bullet, schema=inputs_schema)}"
    hints = [hints] if isinstance(hints, str) else (hints or list())
    if hints:
        formatted += f"\n{tr('Hints')}:\n{bullet_list([tr(hint) for hint in hints], bullet=bullet)}"
    if output is not ...:
        formatted += f"\n{tr('Output')}:\n{format_value(output, schema=output_schema, bullet=None, tag='output')}"
    if expected is not ...:
        formatted += f"\n{tr('Expected')}:\n{format_value(expected, schema=output_schema, bullet=None, tag='expected')}"
    notes = [notes] if isinstance(notes, str) else (notes or list())
    if notes:
        formatted += f"\n{tr('Notes')}:\n{bullet_list([tr(note) for note in notes], bullet=bullet)}"
    if tag:
        formatted = tag_block(tag=tag, content=formatted, inline=False)
    return formatted


def is_delimiter(char: str) -> bool:
    """\
    Check if a character is a word boundary breaker.

    Args:
        char (str): The character to check.

    Returns:
        bool: True if the character is whitespace or punctuation, False otherwise.
    """
    return (char in string.whitespace) or (char in string.punctuation)


_spacy_nlp = None
_spacy_available = None  # None = not yet checked, True/False after check


def is_spacy_available() -> bool:
    """Check if spacy and en_core_web_sm model are available."""
    global _spacy_available, _spacy_nlp
    if _spacy_available is not None:
        return _spacy_available
    try:
        import spacy

        _spacy_nlp = spacy.load("en_core_web_sm", disable=["parser", "ner", "textcat", "attribute_ruler"])
        _spacy_available = True
    except (ImportError, OSError):
        _spacy_available = False
    return _spacy_available


def _is_cjk(char: str) -> bool:
    """\
    Check if a character is a CJK ideograph.
    """
    cp = ord(char)
    return (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF) or (0xF900 <= cp <= 0xFAFF) or (0x20000 <= cp <= 0x2A6DF)


def _simple_normalize_text(text: str) -> str:
    """\
    Simple language-agnostic text normalization without external dependencies.
    Lowercases, splits on non-alphanumeric boundaries for Latin text,
    and uses character-level tokenization for CJK characters.
    """
    text = text.lower().replace("_", " ").replace("-", " ")
    tokens = []
    buf = []
    for ch in text:
        if _is_cjk(ch):
            if buf:
                tokens.append("".join(buf))
                buf = []
            tokens.append(ch)
        elif ch.isalnum():
            buf.append(ch)
        else:
            if buf:
                tokens.append("".join(buf))
                buf = []
    if buf:
        tokens.append("".join(buf))
    return " ".join(tokens)


_spacy_nlp_cache: dict = {}


def normalize_text(text: str, lang: Optional[str] = None) -> str:
    """\
    Normalize text through tokenization, stop word removal, lemmatization, and lowercasing.
    Prioritizes spacy when available; falls back to a simple tokenizer silently.

    Requires spacy with en_core_web_sm model. Raises RuntimeError if unavailable.

    Requires spacy with en_core_web_sm model. Raises RuntimeError if unavailable.

    Requires spacy with en_core_web_sm model. Raises RuntimeError if unavailable.

    Args:
        text (str): The input text to normalize.
        lang (Optional[str]): Language code (e.g., "en", "zh"). If None, reads from
            config ("prompts.lang"), defaulting to "en".

    Returns:
        str: The normalized text with tokens separated by spaces.
    """
    from .config_utils import CM_AHVN

    if lang is None:
        lang = CM_AHVN.get("prompts.lang", "en")

    # Try spacy first, fail silently to simple fallback
    if lang not in _spacy_nlp_cache:
        try:
            import spacy

            _spacy_nlp_cache[lang] = spacy.load(f"{lang}_core_web_sm", disable=["parser", "ner"])
        except Exception:
            _spacy_nlp_cache[lang] = None

    nlp = _spacy_nlp_cache[lang]
    if nlp is not None:
        try:
            return " ".join(
                [
                    token.lemma_.strip()
                    for token in nlp(text.lower().replace("_", "-").replace("-", " "))
                    if not token.is_stop and not token.is_punct and token.lemma_.strip()
                ]
            )
        except Exception:
            pass

    return _simple_normalize_text(text)


def _build_indexed_normalized(tokens: list) -> Tuple[str, List[Tuple[int, int]]]:
    """\
    Build normalized text and char_origins mapping from token tuples.

    Tokens are joined by single spaces. Each character in the normalized text maps
    back to a span in the original query string. Token tuples may be either
    ``(token, orig_start, orig_end)`` or ``(token, orig_start, orig_end, source)``;
    when the normalized token is identical to ``source``, per-character spans are
    preserved, otherwise the token is mapped as a whole.

    For inter-token spaces, the mapping uses (prev_token_orig_end, next_token_orig_start)
    so that boundary lookups degrade gracefully.
    """
    if not tokens:
        return "", []

    parts = []
    char_origins = []

    for idx, token in enumerate(tokens):
        tok, orig_start, orig_end = token[:3]
        source = token[3] if len(token) > 3 else tok
        if idx > 0:
            parts.append(" ")
            prev_end = tokens[idx - 1][2]
            char_origins.append((prev_end, orig_start))
        parts.append(tok)
        if tok == source and len(tok) == orig_end - orig_start:
            char_origins.extend((orig_start + i, orig_start + i + 1) for i in range(len(tok)))
        else:
            char_origins.extend((orig_start, orig_end) for _ in tok)

    return "".join(parts), char_origins


def _indexed_simple_normalize_text(text: str) -> Tuple[str, List[Tuple[int, int]]]:
    """\
    Simple normalization with position tracking.
    Same logic as _simple_normalize_text, but also returns char_origins
    where char_origins[i] points to the corresponding character span in
    the original query string.
    """
    lowered = text.lower().replace("_", " ").replace("-", " ")
    tokens = []  # (token_str, orig_start, orig_end)
    buf = []
    buf_start = 0

    for i, ch in enumerate(lowered):
        if _is_cjk(ch):
            if buf:
                tok = "".join(buf)
                tokens.append((tok, buf_start, buf_start + len(tok), tok))
                buf = []
            tokens.append((ch, i, i + 1))
        elif ch.isalnum():
            if not buf:
                buf_start = i
            buf.append(ch)
        else:
            if buf:
                tok = "".join(buf)
                tokens.append((tok, buf_start, buf_start + len(tok), tok))
                buf = []
    if buf:
        tok = "".join(buf)
        tokens.append((tok, buf_start, buf_start + len(tok), tok))

    return _build_indexed_normalized(tokens)


def indexed_normalize_text(text: str, lang: Optional[str] = None) -> Tuple[str, List[Tuple[int, int]]]:
    """\
    Normalize text with position tracking for mapping back to original text.

    Returns both the normalized text and a per-character origin mapping. Each position
    in the normalized text maps to the corresponding span in the original query string.

    To convert a match range (norm_start, norm_end) in the normalized text to the
    corresponding range in the original text::

        orig_start = char_origins[norm_start][0]
        orig_end = char_origins[norm_end - 1][1]

    Args:
        text (str): The input text to normalize.
        lang (Optional[str]): Language code (e.g., "en", "zh"). If None, reads from
            config ("prompts.lang"), defaulting to "en".

    Returns:
        Tuple[str, List[Tuple[int, int]]]: A tuple of (normalized_text, char_origins).
    """
    from .config_utils import CM_AHVN

    if lang is None:
        lang = CM_AHVN.get("prompts.lang", "en")

    if lang not in _spacy_nlp_cache:
        try:
            import spacy

            _spacy_nlp_cache[lang] = spacy.load(f"{lang}_core_web_sm", disable=["parser", "ner"])
        except Exception:
            _spacy_nlp_cache[lang] = None

    nlp = _spacy_nlp_cache[lang]
    if nlp is not None:
        try:
            processed = text.lower().replace("_", "-").replace("-", " ")
            tokens = []
            for token in nlp(processed):
                lemma = token.lemma_.strip()
                if not token.is_stop and not token.is_punct and lemma:
                    tokens.append((lemma, token.idx, token.idx + len(token.text), token.text.strip()))
            return _build_indexed_normalized(tokens)
        except Exception:
            pass

    return _indexed_simple_normalize_text(text)


def generate_ngrams(tokens: list, n: int) -> Set[str]:
    """\
    Generate n-grams from a list of tokens.

    Args:
        tokens (list): List of tokens to generate n-grams from.
        n (int): Maximum n-gram size.

    Returns:
        Set[str]: Set of n-grams with sizes from 1 to n.
    """
    return {" ".join(tokens[i : i + k]) for k in range(1, n + 1) for i in range(len(tokens) - k + 1)}


def asymmetric_jaccard_score(query: str, doc: str, ngram: int = 6, lang: Optional[str] = None) -> float:
    """\
    Calculate asymmetric Jaccard containment score between query and document.

    Args:
        query (str): The query text.
        doc (str): The document text.
        ngram (int, optional): Maximum n-gram size. Defaults to 6.
        lang (Optional[str]): Language code for normalization. Defaults to None.

    Returns:
        float: Containment score between 0.0 and 1.0.
    """
    q = generate_ngrams(normalize_text(query, lang=lang).split(), n=ngram)
    d = generate_ngrams(normalize_text(doc, lang=lang).split(), n=ngram)
    if not q:
        return 1.0
    return len(q.intersection(d)) / len(q)


def resolve_match_conflicts(
    results: list,
    conflict: str = "overlap",
    query_length: int = 0,
    inverse: bool = False,
) -> list:
    """\
    Resolve overlapping matches in search results based on conflict strategy.

    This utility function filters overlapping text spans when multiple entities match
    at the same or overlapping positions in a query string. It operates on search results
    that contain match position information.

    Args:
        results (list): List of result dictionaries. Each dictionary must contain:
            - 'id': Entity identifier
            - 'matches': List of (start, end) tuples representing match positions in the query
        conflict (str, optional): Strategy for handling overlapping matches. Options:
            - "overlap": Keep all matches including overlapping ones (no filtering)
            - "longest": Keep only the longest match for any overlapping set
            - "longest_distinct": Allow multiple entities to have overlapping matches
                                as long as they are the longest matches
            Defaults to "overlap".
        query_length (int, optional): Length of the query string. Required for "longest"
            and "longest_distinct" strategies when inverse=True. Defaults to 0.
        inverse (bool, optional): Whether the matches were computed on reversed strings.
            Affects the sorting and comparison logic. Defaults to False.

    Returns:
        list: Filtered list of result dictionaries with the same structure as input,
            where each result's 'matches' list has been filtered according to the
            conflict resolution strategy.

    Examples:
        >>> results = [
        ...     {'id': 1, 'matches': [(0, 5), (10, 15), (22, 27), (32, 37)]},
        ...     {'id': 2, 'matches': [(2, 8), (12, 18), (21, 27), (32, 38)]}
        ... ]
        >>> resolve_match_conflicts(results, conflict="longest", query_length=40)
        [{'id': 1, 'matches': [(0, 5), (10, 15)]}, {'id': 2, 'matches': [(21, 27), (32, 38)]}]
    """
    if conflict == "overlap":
        return results

    # Extract all intervals with their entity IDs
    intervals = [(r["id"], start, end) for r in results for start, end in r["matches"]]

    # Sort intervals: for inverse mode, sort by end descending then start ascending
    # For normal mode, sort by start ascending then end descending
    sorted_intervals = sorted(intervals, key=(lambda x: (-x[2], x[1], x[0])) if inverse else (lambda x: (x[1], -x[2], x[0])))

    if conflict == "longest":
        filtered = _resolve_longest_conflicts(sorted_intervals, query_length, inverse)
    elif conflict == "longest_distinct":
        filtered = _resolve_longest_distinct_conflicts(sorted_intervals, query_length, inverse)
    else:
        return results

    results_mapping = {r["id"]: r for r in results}
    grouped_results = dict()
    for entity_id, start, end in filtered:
        if entity_id not in grouped_results:
            grouped_results[entity_id] = {"id": entity_id, "matches": []}
        grouped_results[entity_id]["matches"].append((start, end))

    return [results_mapping[result["id"]] | {"matches": sorted(result["matches"])} for result in grouped_results.values() if result["matches"]]


def _resolve_longest_conflicts(intervals: list, query_length: int, inverse: bool) -> list:
    """\
    Internal helper: Resolve conflicts using longest match strategy.

    Keeps only the left-longest non-overlapping matches.
    Specifically, if (l1, r1) and (l2, r2) overlaps:
        if l1 < l2: keep (l1, r1)
        if l1 > l2: keep (l2, r2)
        if l1 == l2: keep the longer one
        if l1 == r1 and l2 == r2: keep either one (arbitrary, the one with smaller id in practice)
    When inverse=True, the logic is applied in reverse order (right-longest)
    """
    filtered = []
    prev = query_length if inverse else 0

    for entity_id, start, end in intervals:
        if (end <= prev) if inverse else (start >= prev):
            filtered.append((entity_id, start, end))
            prev = start if inverse else end

    return filtered


def _resolve_longest_distinct_conflicts(intervals: list, query_length: int, inverse: bool) -> list:
    """\
    Internal helper: Resolve conflicts using longest distinct match strategy.

    Keeps only the left-longest non-overlapping matches.
    Specifically, if (l1, r1) and (l2, r2) overlaps:
        if l1 < l2: keep (l1, r1)
        if l1 > l2: keep (l2, r2)
        if l1 == l2: keep the longer one
        if l1 == r1 and l2 == r2: keep both
    When inverse=True, the logic is applied in reverse order (right-longest)
    """
    filtered = []
    prev = query_length if inverse else 0
    selected = (-1, -1)

    for entity_id, start, end in intervals:
        if ((end <= prev) if inverse else (start >= prev)) or (start, end) == selected:
            filtered.append((entity_id, start, end))
            prev = start if inverse else end
            selected = (start, end)

    return filtered
