"""Load Markdown instructions and define Structured Outputs schemas."""
from pathlib import Path

from .domain import CATEGORIES


def _load_prompts(path: Path) -> dict[str, str]:
    expected = {"base", "faithful", "modern", "easy", "review"}
    try:
        document = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"프롬프트 파일을 읽을 수 없습니다: {path}") from exc
    sections: dict[str, list[str]] = {}
    current = None
    for line in document.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            if current not in expected or current in sections:
                raise ValueError(f"알 수 없거나 중복된 프롬프트 구역: {current} ({path})")
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    prompts = {name: "\n".join(lines).strip() for name, lines in sections.items()}
    invalid = sorted(name for name in expected if not prompts.get(name))
    if invalid:
        raise ValueError(f"필수 프롬프트 구역이 없거나 비어 있습니다: {', '.join(invalid)} ({path})")
    return prompts


_PROMPTS = _load_prompts(Path(__file__).resolve().with_suffix(".md"))
# Keep the existing separator between the base and style instructions.
BASE_PROMPT = _PROMPTS["base"] + "\n"
STYLE_PROMPTS = {name: _PROMPTS[name] for name in ("faithful", "modern", "easy")}
REVIEW_PROMPT = _PROMPTS["review"]

STYLE_NAMES = {"faithful": "원문 충실형", "modern": "자연스러운 현대어", "easy": "쉬운 한국어"}
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "description": {"type": "string"},
                },
                "required": ["id", "category", "description"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}
