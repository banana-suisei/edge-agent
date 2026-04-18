from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class SkillMeta:
    name: str
    description: str
    skill_dir: Path


class SkillLoader:
    def __init__(self, skills_dir: str | Path) -> None:
        self.skills_dir = Path(skills_dir)
        self._skills: dict[str, SkillMeta] = {}
        self._scan_skills()

    def _scan_skills(self) -> None:
        if not self.skills_dir.is_dir():
            return
        for child in sorted(self.skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                meta = self._parse_frontmatter(skill_md)
                if meta:
                    self._skills[meta.name] = meta

    @staticmethod
    def _parse_frontmatter(skill_md: Path) -> SkillMeta | None:
        text = skill_md.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        end = text.find("---", 3)
        if end == -1:
            return None
        fm_text = text[3:end].strip()
        try:
            fm = yaml.safe_load(fm_text)
        except yaml.YAMLError:
            return None
        if not isinstance(fm, dict) or "name" not in fm or "description" not in fm:
            return None
        return SkillMeta(
            name=fm["name"],
            description=fm["description"],
            skill_dir=skill_md.parent,
        )

    @property
    def skills(self) -> dict[str, SkillMeta]:
        return self._skills

    def load_skill_content(self, skill_name: str) -> str | None:
        meta = self._skills.get(skill_name)
        if meta is None:
            return None
        skill_md = meta.skill_dir / "SKILL.md"
        return skill_md.read_text(encoding="utf-8")

    def skill_descriptions_text(self) -> str:
        lines = []
        for meta in self._skills.values():
            lines.append(f"- **{meta.name}**: {meta.description}")
        return "\n".join(lines)
