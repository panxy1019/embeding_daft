"""SkillToolSpec - Tool specification for invoking Claude Skills.

This module provides the SkillToolSpec class that creates a tool interface
for LLMs to invoke skills stored as SkillUKFT objects.
"""

__all__ = [
    "SkillToolSpec",
]

from ahvn.utils.basic.debug_utils import raise_mismatch
from ..base import ToolSpec
from ...ukf.templates.basic.skill import SkillUKFT

from typing import Dict, Optional, List, Set, TYPE_CHECKING

if TYPE_CHECKING:
    from ...klbase import KLBase


class SkillToolSpec(ToolSpec):
    """\
    Tool specification for invoking Claude Skills.

    Creates a `Skill(name: str, path: Optional[str] = "SKILL.md")` tool interface
    that allows LLMs to load skill content from a collection of SkillUKFT objects.

    Example:
        >>> from ahvn.ukf.templates.basic import SkillUKFT
        >>> # Create skills
        >>> skill1 = SkillUKFT.from_path("/path/to/pdf-skill")
        >>> skill2 = SkillUKFT.from_path("/path/to/xlsx-skill")
        >>> # Create tool spec
        >>> tool = SkillToolSpec.from_skills([skill1, skill2])
        >>> # Use in LLM context
        >>> result = tool(name="pdf", path="SKILL.md")
    """

    @classmethod
    def from_skills(
        cls,
        skills: List[SkillUKFT],
        name: str = "Skill",
    ) -> "SkillToolSpec":
        """\
        Create a SkillToolSpec from a list of SkillUKFT objects.

        Args:
            skills (List[SkillUKFT]): List of skill objects to make available.
            name (str): Tool name. Defaults to "Skill".

        Returns:
            SkillToolSpec: Tool spec that can load any of the provided skills.

        Example:
            >>> skills = [SkillUKFT.from_path(p) for p in skill_paths]
            >>> tool = SkillToolSpec.from_skills(skills)
        """
        skill_map: Dict[str, SkillUKFT] = {s.name: s for s in skills}
        if len(skill_map) < len(skills):
            raise ValueError("Skills with the same names are not allowed in SkillToolSpec.")

        # Use closure variables instead of bound parameters.
        # Pydantic's type_adapter.validate_python() copies list/dict values during parameter
        # validation, so mutations to bound parameters inside the wrapper don't propagate
        # back to the original state. Closure captures avoid this issue entirely.
        loaded_tools: List[str] = []

        def wrapper(skill_name: str, path: Optional[str] = "SKILL.md") -> str:
            """\
            Load skill content for the specified skill.

            Args:
                skill_name (str): The name of the skill to invoke.
                path (Optional[str]): Path to the resource within the skill.
                    Use "SKILL.md" (default) to load main skill instructions and directory structure.
                    Use specific paths like "references/guide.md" to read bundled files.

            Returns:
                str: The skill content or file content.
            """
            suggestion = raise_mismatch(supported=list(skill_map.keys()), got=skill_name, mode="match")
            if suggestion is None:
                suggestions = [f"'{skill}'" for skill in skill_map]
                return f"[ERROR] Skill '{skill_name}' not found. Available skills: {', '.join(suggestions)}."
            elif suggestion != skill_name:
                msg = f"[WARNING] Skill '{skill_name}' not found. Proceeding with the closest skill: '{suggestion}'.\n"
                skill_name = suggestion
            else:
                msg = ""

            skill = skill_map[skill_name]
            for tool_name in skill.tools:
                if tool_name not in loaded_tools:
                    loaded_tools.append(tool_name)
            return msg + skill.text("load", path=path)

        toolspec = ToolSpec.from_func(func=wrapper, name=name, parse_docstring=True)
        toolspec.state["skill_map"] = skill_map
        toolspec.state["loaded_tools"] = loaded_tools

        return toolspec

    @classmethod
    def from_kb(
        cls,
        kb: "KLBase",
        name: str = "Skill",
        **search_kwargs,
    ) -> "SkillToolSpec":
        """\
        Create a SkillToolSpec from skills stored in a KLBase.

        Searches the knowledge base for SkillUKFT objects and creates
        a tool that can invoke any of them.

        Args:
            kb (KLBase): Knowledge base containing skill objects.
            name (str): Tool name. Defaults to "Skill".
            **search_kwargs: Additional arguments passed to kb.search().
                By default, searches for type="skill".

        Returns:
            SkillToolSpec: Tool spec that can load skills from the KB.

        Example:
            >>> tool = SkillToolSpec.from_kb(kb)
            >>> result = tool(name="pdf", path="SKILL.md")
        """
        search_kwargs.setdefault("type", "skill")
        results = kb.search(**search_kwargs)
        skills = [r["kl"] for r in results if isinstance(r["kl"], SkillUKFT)]
        return cls.from_skills(skills, name=name)
