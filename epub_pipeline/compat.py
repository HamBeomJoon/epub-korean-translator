"""Small compatibility facade for existing callers with an explicitly supplied client."""
import json

from .domain import validate_result, validate_review
from .prompts import BASE_PROMPT, STYLE_PROMPTS, RESULT_SCHEMA, REVIEW_SCHEMA, REVIEW_PROMPT
from .provider import OpenAIProvider


def api_json(client, model, system, payload, schema, name):
    response = OpenAIProvider(client).complete(model, system, payload, schema, name)
    if response.status != "completed":
        raise RuntimeError(f"완료되지 않은 응답: {response.status}")
    usage = response.usage or {}
    return json.loads(response.text), {k: usage.get(k, 0) for k in ("input_tokens", "output_tokens")}


def translate_batch(client, model, style, batch, glossary, title):
    result, usage = api_json(client, model, BASE_PROMPT + "\n" + STYLE_PROMPTS[style],
                             {"book_chapter_title": title, "glossary": glossary, "paragraphs": batch},
                             RESULT_SCHEMA, "translated_paragraphs")
    return validate_result(batch, result), usage


def review_batch(client, model, batch, translated, style):
    payload = {"style_rules": STYLE_PROMPTS[style],
               "paragraphs": [{"id": p["id"], "source": p["text"], "translation": translated[p["id"]]} for p in batch]}
    result, usage = api_json(client, model, REVIEW_PROMPT, payload, REVIEW_SCHEMA, "review_issues")
    return validate_review(batch, result), usage
