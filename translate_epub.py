#!/usr/bin/env python3
"""Command-line entry point."""
import sys
import zipfile
from xml.etree import ElementTree as ET

from epub_pipeline.cli import build_parser, run

if __name__ == "__main__":
    try:
        run(build_parser().parse_args())
    except (OSError, KeyError, ValueError, RuntimeError, zipfile.BadZipFile, ET.ParseError) as error:
        print(f"오류: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("중단됨: 동일한 명령으로 체크포인트에서 재개할 수 있습니다.", file=sys.stderr)
        sys.exit(130)
