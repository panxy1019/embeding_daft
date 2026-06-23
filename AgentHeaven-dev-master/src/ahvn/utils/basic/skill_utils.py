"""Skill utilities for loading and parsing Claude Skills (SKILL.md format).

This module provides utilities for working with Claude Skills, which are
modular packages that extend AI capabilities with specialized knowledge,
workflows, and tool integrations.
"""

__all__ = [
    "parse_skill_md",
    "load_skill",
]

import re
import base64
from typing import Dict, Any, Optional, Tuple

import yaml

from .str_utils import code_block, file_block, md_symbol
from .path_utils import get_file_ext


def parse_skill_md(content: str) -> Tuple[Dict[str, Any], str]:
    """\
    Parse a SKILL.md file content into frontmatter metadata and body.

    The SKILL.md format consists of:
    - YAML frontmatter between `---` delimiters containing `name` and `description`
    - Markdown body with instructions and guidance

    Args:
        content (str): The raw content of a SKILL.md file.

    Returns:
        Tuple[Dict[str, Any], str]: A tuple of (frontmatter_dict, body_str).
            frontmatter_dict contains at least 'name' and 'description' keys.
            body_str is the markdown content after the frontmatter.

    Example:
        >>> content = '''---
        ... name: my-skill
        ... description: A helpful skill
        ... ---
        ... # My Skill
        ... Instructions here.
        ... '''
        >>> meta, body = parse_skill_md(content)
        >>> meta['name']
        'my-skill'
        >>> body.strip()
        '# My Skill\\nInstructions here.'
    """
    # Match YAML frontmatter pattern: starts with ---, ends with ---
    pattern = r"^---\s*\n(.*?)\n---\s*\n(.*)$"
    match = re.match(pattern, content, re.DOTALL)

    if not match:
        # No frontmatter found, return empty dict and full content
        return {}, content.strip()

    frontmatter_str = match.group(1)
    body = match.group(2)

    try:
        frontmatter = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError:
        frontmatter = {}

    return frontmatter, body.strip()


def _decode_b64_content(content: str) -> str:
    """\
    Decode base64 encoded file content to text.

    Args:
        content (str): Base64 encoded string.

    Returns:
        str: Decoded text content.
    """
    try:
        return base64.b64decode(content).decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return "[ERROR] Unable to decode file content as text."


def _get_language_from_path(path: str) -> str:
    """\
    Infer programming language from file extension for syntax highlighting.

    Args:
        path (str): File path.

    Returns:
        str: Language identifier for markdown code blocks.
    """
    ext = get_file_ext(path).lower()
    ext_to_lang = {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "md": "markdown",
        "json": "json",
        "yaml": "yaml",
        "yml": "yaml",
        "sh": "bash",
        "bash": "bash",
        "sql": "sql",
        "html": "html",
        "css": "css",
        "xml": "xml",
        "txt": "",
    }
    return ext_to_lang.get(ext, "")


def _render_code_block(content: str, filepath: str = None, language: str = None) -> str:
    """\
    Render content as a markdown code block.

    Args:
        content (str): The content to wrap in a code block.
        filepath (str, optional): File path to display as header.
        language (str, optional): Language for syntax highlighting.

    Returns:
        str: Formatted code block.
    """
    if language is None and filepath:
        language = _get_language_from_path(filepath)
    if filepath:
        return file_block(filepath, content, lang=language or "", start=-1, window=None)
    return code_block(content, lang=language or "", start=-1, window=None)


def load_skill(
    name: str,
    path: Optional[str] = "SKILL.md",
    *,
    skill_body: str = "",
    data: Dict[str, Optional[str]] = None,
    diagram_func=None,
) -> str:
    """\
    Load skill content for LLM consumption.

    This function implements the skill loading interface:
    `load_skill(name: str, path: Optional[str] = "SKILL.md") -> str`

    When path is "SKILL.md" or None (default), returns structured content:
    - Loading header with skill name
    - Skill resources diagram
    - SKILL.md content in a markdown code block

    When path is a specific file, returns that file's content in a code block.

    Args:
        name (str): The skill name.
        path (Optional[str]): Path to the resource within the skill.
            Defaults to "SKILL.md" which returns the main skill content.
            Use specific paths like "references/workflows.md" to read bundled files.
        skill_body (str): The parsed SKILL.md body content (without frontmatter).
        data (Dict[str, Optional[str]]): Serialized file/directory structure.
            Keys are file paths, values are base64-encoded content (None for directories).
        diagram_func (Callable, optional): Function to generate directory diagram.
            If None, uses a simple file listing.

    Returns:
        str: The formatted skill content for LLM consumption.

    Example:
        >>> # Load main skill content
        >>> content = load_skill("my-skill", skill_body="# Instructions", data={...})
        >>> print(content)
        Loading Skill: my-skill
        Skill Resources:
        ```
        my-skill/
        ├── SKILL.md
        └── references/
            └── guide.md
        ```
        SKILL.md:
        ```markdown
        # Instructions
        ```

        >>> # Load specific file
        >>> content = load_skill("my-skill", path="references/guide.md", data={...})
        >>> print(content)
        references/guide.md:
        ```markdown
        # Guide content here...
        ```
    """
    data = data or {}

    # Normalize path
    if path is None or path == "SKILL.md":
        # Handle SKILL.md - return structured content
        parts = []

        # Add loading header
        parts.append(f"Loading Skill: {name}")

        # Add directory structure
        if data:
            diagram = ""
            if diagram_func:
                diagram = diagram_func()
            else:
                # Simple fallback: list files
                files = sorted([p for p, c in data.items() if c is not None])
                if files:
                    diagram = f"{name}/\n" + "\n".join(f"  {f}" for f in files)

            if diagram:
                sym = md_symbol(diagram)
                parts.append(f"Skill Resources:\n{sym}\n{diagram}\n{sym}")

        # Add skill body in code block
        if skill_body:
            skill_content = _render_code_block(skill_body, filepath="SKILL.md", language="markdown")
            parts.append(skill_content)

        return "\n".join(parts)

    # Handle specific file path
    # Normalize: remove leading slash if present
    normalized_path = path.lstrip("/")

    # Try to find the file in data
    file_content = None
    matched_path = None

    if normalized_path in data:
        file_content = data[normalized_path]
        matched_path = normalized_path
    else:
        # Try partial match
        for key in data:
            if key.endswith(normalized_path) or normalized_path.endswith(key):
                file_content = data[key]
                matched_path = key
                break

    if file_content is None:
        # Distinguish between "path exists but is a directory" and "path not found"
        path_exists = normalized_path in data or matched_path is not None
        if path_exists:
            return f"[ERROR] '{path}' is a directory, not a file."
        available = [p for p in data.keys() if data[p] is not None]
        return f"[ERROR] File '{path}' not found in skill '{name}'.\nAvailable files: {', '.join(available) if available else '(none)'}"

    # Decode and render
    decoded_content = _decode_b64_content(file_content)
    return _render_code_block(decoded_content, filepath=matched_path)
