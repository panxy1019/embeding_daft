"""Tests for SkillUKFT tool reference normalization."""

from ahvn.ukf.templates.basic.skill import SkillUKFT


def test_skill_from_path_normalizes_tool_refs(tmp_path):
    skill_dir = tmp_path / "skill-demo"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            "name: skill-demo\n"
            "description: Demo skill\n"
            "tools:\n"
            "  - search\n"
            "  - name: calc\n"
            '    capsule_id: "000000000000000000000001"\n'
            "---\n"
            "# Skill Body\n"
        ),
        encoding="utf-8",
    )

    skill = SkillUKFT.from_path(str(skill_dir))
    tool_refs = skill.content_resources["tools"]
    assert tool_refs == [
        {"name": "search"},
        {"name": "calc", "capsule_id": "000000000000000000000001"},
    ]
    assert skill.tools == ["search", "calc"]
    assert skill.tool_refs == tool_refs
    assert "[TOOL:search]" in skill.tags
    assert "[TOOL:calc]" in skill.tags
