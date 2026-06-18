from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "github-agent-bridge" / "SKILL.md"


def test_github_agent_bridge_skill_exists_with_required_metadata():
    text = SKILL.read_text(encoding="utf-8")

    assert text.startswith("---\n")
    frontmatter = text.split("---\n", 2)[1]

    assert "name: github-agent-bridge" in frontmatter
    assert "description:" in frontmatter
    assert "Operate and maintain the github-agent-bridge deployment" in frontmatter


def test_github_agent_bridge_skill_references_existing_docs():
    text = SKILL.read_text(encoding="utf-8")
    references = re.findall(r"`(\.\./\.\./\.\./docs/[^`]+\.md)`", text)

    assert references
    for reference in references:
        assert (SKILL.parent / reference).resolve().is_file()
