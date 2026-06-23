"""SkillUKFT - Universal Knowledge Framework template for Claude Skills.

This module provides the SkillUKFT class for representing Claude Skills,
which are modular packages that extend AI capabilities with specialized
knowledge, workflows, and tool integrations.
"""

__all__ = [
    "SkillUKFT",
    "SkillType",
    "desc_composer",
    "load_composer",
    "full_composer",
]

from pydantic import model_validator

from ahvn.ukf.ukf_utils import ptags
from .resource import ResourceUKFT, diagram_composer
from ...registry import register_ukft
from ....utils.basic.serialize_utils import serialize_path, load_txt
from ....utils.basic.skill_utils import parse_skill_md, load_skill
from ....utils.basic.config_utils import CM_AHVN
from ....utils.basic.path_utils import get_file_basename, pj
from ....utils.basic.file_utils import exists_file
from ....utils.basic.log_utils import get_logger

logger = get_logger(__name__)

from typing import Dict, Any, Optional, ClassVar, Union, List
from xml.sax.saxutils import escape as xml_escape


def normalize_tool_refs(tools: Any) -> List[Dict[str, str]]:
    """Normalize tool references to [{'name': ..., 'capsule_id'?: ...}] format."""
    if not isinstance(tools, list):
        return []

    normalized: List[Dict[str, str]] = []
    for tool in tools:
        if isinstance(tool, str):
            name = tool.strip()
            if name:
                normalized.append({"name": name})
            continue

        if not isinstance(tool, dict):
            continue

        name = tool.get("name")
        if not isinstance(name, str):
            continue
        name = name.strip()
        if not name:
            continue

        ref = {"name": name}
        capsule_id = tool.get("capsule_id")
        if isinstance(capsule_id, str) and capsule_id.strip():
            ref["capsule_id"] = capsule_id.strip()
        normalized.append(ref)

    return normalized


def desc_composer(kl: "SkillUKFT", **kwargs) -> str:
    """\
    Compose a skill description in XML format for LLM previews.

    Generates a `<skill>` XML block that can be included in system prompts
    to inform LLMs about available skills.

    Args:
        kl (SkillUKFT): The skill knowledge object.
        **kwargs: Optional overrides (unused).

    Returns:
        str: XML-formatted skill description.

    Example:
        >>> skill = SkillUKFT.from_path("/path/to/skill-creator")
        >>> print(desc_composer(skill))
        <skill>
        <name>skill-creator</name>
        <description>Guide for creating effective skills...</description>
        </skill>
    """
    name = xml_escape(kl.name)
    description = xml_escape(kl.description or "")
    tools = normalize_tool_refs(kl.get("tools", []))

    lines = [
        "<skill>",
        f"<name>{name}</name>",
        f"<description>{description}</description>",
    ]

    if tools:
        lines.append("<tools>")
        for tool_ref in tools:
            tool_name = xml_escape(tool_ref["name"])
            capsule_id = tool_ref.get("capsule_id")
            if capsule_id:
                lines.append(f'<tool capsule_id="{xml_escape(capsule_id)}">{tool_name}</tool>')
            else:
                lines.append(f"<tool>{tool_name}</tool>")
        lines.append("</tools>")

    lines.append("</skill>")
    return "\n".join(lines)


def load_composer(kl: "SkillUKFT", path: Optional[str] = "SKILL.md", **kwargs) -> str:
    """\
    Compose skill content for LLM consumption when the skill is invoked.

    When path is "SKILL.md" or None (default), returns structured content:
    - Loading header
    - Skill resources diagram
    - SKILL.md content in a code block

    When path is a specific file, returns that file's content in a code block.

    Args:
        kl (SkillUKFT): The skill knowledge object.
        path (Optional[str]): Path to resource within the skill. Defaults to "SKILL.md".
        **kwargs: Additional arguments (unused).

    Returns:
        str: Formatted skill content with proper code block wrapping.

    Example:
        >>> skill = SkillUKFT.from_path("/path/to/skill-creator")
        >>> # Load main skill content
        >>> print(load_composer(skill))
        Loading Skill: skill-creator
        Skill Resources:
        ```
        skill-creator/
        ├── SKILL.md
        └── references/
            └── workflows.md
        ```
        SKILL.md:
        ```markdown
        # Skill Creator
        ...
        ```

        >>> # Load specific file
        >>> print(load_composer(skill, path="references/workflows.md"))
        references/workflows.md:
        ```markdown
        # Workflow Patterns
        ...
        ```
    """
    name = kl.name
    skill_body = kl.get("skill_body", "")
    data = kl.get("data", {})

    def get_diagram():
        return diagram_composer(kl, name=name)

    return load_skill(
        name=name,
        path=path,
        skill_body=skill_body,
        data=data,
        diagram_func=get_diagram,
    )


def full_composer(kl: "SkillUKFT", **kwargs) -> str:
    """\
    Compose full skill content including XML description and loaded resources.

    Combines output of `desc_composer` and `load_composer`.
    Removes the "Loading Skill: ..." header from `load_composer` output.

    Args:
        kl (SkillUKFT): The skill knowledge object.
        **kwargs: Passed to underlying composers.

    Returns:
        str: Combined content.
    """
    desc = desc_composer(kl, **kwargs)
    content = load_composer(kl, **kwargs)

    # Remove the specific header string
    header = f"Loading Skill: {kl.name}\n"
    if content.startswith(header):
        content = content[len(header) :]

    return f"{desc}\n\n{content}"


@register_ukft
class SkillUKFT(ResourceUKFT):
    """\
    Skill class for representing Claude Skills (SKILL.md format).

    SkillUKFT extends ResourceUKFT to store and manage Claude Skills,
    which are modular packages that provide specialized knowledge,
    workflows, and tool integrations to AI agents.

    UKF Type: skill

    Content Resources (inherited from ResourceUKFT):
        - path (str): The original skill directory path.
        - data (Dict[str, Optional[str]]): Serialized file/directory structure.
        - annotations (Dict[str, str]): File-level annotations.
        - skill_body (str): The SKILL.md body content (without YAML frontmatter).

    UKF Attributes:
        - name: Skill name (from YAML frontmatter).
        - description: Skill description (from YAML frontmatter).

    Composers:
        desc:
            Generates XML format for LLM previews:
            ```xml
            <skill>
            <name>skill-creator</name>
            <description>Guide for creating effective skills...</description>
            </skill>
            ```

        load:
            Returns structured skill content for invocation.
            Use `text("load")` for SKILL.md or `text("load", path="...")` for specific files.

    Example:
        >>> skill = SkillUKFT.from_path("/path/to/skill-creator")
        >>> # Get skill preview for system prompt
        >>> print(skill.text("desc"))
        <skill>
        <name>skill-creator</name>
        <description>Guide for creating effective skills...</description>
        </skill>

        >>> # Invoke skill (load main content)
        >>> print(skill.text("load"))
        Loading Skill: skill-creator
        ...

        >>> # Read specific bundled resource
        >>> print(skill.text("load", path="references/workflows.md"))
        references/workflows.md:
        ...
    """

    type_default: ClassVar[str] = "skill"

    @property
    def tool_refs(self) -> List[Dict[str, str]]:
        """Return normalized tool references (name + optional capsule_id)."""
        return normalize_tool_refs(self.get("tools", []))

    @model_validator(mode="before")
    @classmethod
    def _migrate_tools(cls, data: Any) -> Any:
        """Move 'tools' from root to content_resources for backward compatibility."""
        if isinstance(data, dict) and "tools" in data:
            tools = data.pop("tools")
            cr = data.setdefault("content_resources", {})
            if isinstance(cr, dict) and "tools" not in cr:
                cr["tools"] = tools
        return data

    @classmethod
    def from_path(
        cls,
        path: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        keep_path: bool = True,
        **updates,
    ) -> "SkillUKFT":
        """\
        Create a SkillUKFT instance from a skill directory path.

        Parses the SKILL.md file to extract name, description, and body content,
        then serializes the entire skill directory.

        Args:
            path (str): Path to the skill directory (must contain SKILL.md).
            name (str, optional): Override skill name from frontmatter.
            description (str, optional): Override skill description from frontmatter.
            keep_path (bool): Whether to keep the original path. Defaults to True.
            **updates: Additional attributes to set on the SkillUKFT instance.

        Returns:
            SkillUKFT: New SkillUKFT instance with configured composers.

        Raises:
            FileNotFoundError: If SKILL.md is not found in the directory.
            ValueError: If SKILL.md has no name in frontmatter and name is not provided.

        Example:
            >>> skill = SkillUKFT.from_path("/path/to/skill-creator")
            >>> skill.name
            'skill-creator'
            >>> skill.description
            'Guide for creating effective skills...'
        """
        path = CM_AHVN.pj(path)
        skill_md_path = pj(path, "SKILL.md", abs=True)

        if not exists_file(skill_md_path):
            raise FileNotFoundError(f"SKILL.md not found in '{path}'")

        # Read and parse SKILL.md
        skill_md_content = load_txt(skill_md_path, strict=True)
        frontmatter, skill_body = parse_skill_md(skill_md_content)

        # Extract name and description from frontmatter
        skill_name = name or frontmatter.get("name")
        skill_description = description or frontmatter.get("description", "")

        # Get tools from frontmatter only
        tool_refs = normalize_tool_refs(frontmatter.get("tools", []))
        tool_tags = ptags(TOOL=[ref["name"] for ref in tool_refs])
        updates.pop("tools", None)

        if not skill_name:
            # Fallback to directory name
            skill_name = get_file_basename(path)

        # Serialize the entire skill directory
        serialized_data = serialize_path(path)

        return cls(
            name=skill_name,
            description=skill_description,
            tags=tool_tags,
            content_resources=({"path": path} if keep_path else {})
            | {
                "data": serialized_data,
                "annotations": {},
                "skill_body": skill_body,
                "tools": tool_refs,
            },
            content_composers={
                "default": desc_composer,
                "desc": desc_composer,
                "load": load_composer,
                "full": full_composer,
                "diagram": diagram_composer,
            },
            **updates,
        )


SkillType = Union[SkillUKFT]
