#!/usr/bin/env python3
"""CLI entry point; compatibility exports for the original MVP helpers."""
import sys
import zipfile
from xml.etree import ElementTree as ET

from epub_pipeline.cli import build_parser, load_glossary, run
from epub_pipeline.compat import api_json, review_batch, translate_batch
from epub_pipeline.domain import Chapter, make_batches, signature, validate_translations
from epub_pipeline.parser import read_epub, normalize_text, element_text
from epub_pipeline.prompts import BASE_PROMPT, STYLE_PROMPTS, STYLE_NAMES, RESULT_SCHEMA, REVIEW_SCHEMA
from epub_pipeline.repository import write_json_atomic

if __name__ == "__main__":
    try:
        run(build_parser().parse_args())
    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile, ET.ParseError) as error:
        print(f"오류: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("중단됨: 동일한 명령으로 체크포인트에서 재개할 수 있습니다.", file=sys.stderr)
        sys.exit(130)
