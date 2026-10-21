"""EPUB adapter. Preserve source AST and expose addressable translation blocks."""
from __future__ import annotations

import logging
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .domain import Chapter, signature

XHTML = "{http://www.w3.org/1999/xhtml}"
OPF = "{http://www.idpf.org/2007/opf}"
CONTAINER = "{urn:oasis:names:tc:opendocument:xmlns:container}"
EPUB_TYPE = "{http://www.idpf.org/2007/ops}type"
BLOCKS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "dt", "dd", "pre", "td", "th", "figcaption"}
SUPPORTED = BLOCKS | {"body", "section", "article", "div", "header", "footer", "hgroup", "aside", "blockquote", "ol", "ul", "dl", "span", "em", "i", "strong", "b", "a", "br", "hr", "sup", "sub", "small", "abbr", "cite", "q", "time", "s", "u", "code"}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def tag(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def element_text(node: ET.Element) -> str:
    def text(el):
        value = el.text or ""
        for child in el:
            value += ("\n" if tag(child) == "br" else text(child)) + (child.tail or "")
        return value
    value = text(node)
    if tag(node) == "pre":
        return value.strip()
    return "\n".join(normalize_text(line) for line in value.split("\n")).strip()


def source_ast(node: ET.Element) -> dict:
    return {"tag": node.tag, "attributes": dict(node.attrib), "text": node.text,
            "tail": node.tail, "children": [source_ast(child) for child in node]}


def read_epub(path: Path, include_prologue: bool = False) -> list[Chapter]:
    chapters = []
    with zipfile.ZipFile(path) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = container.find(f".//{CONTAINER}rootfile")
        if rootfile is None:
            raise ValueError("EPUB container.xml에서 rootfile을 찾을 수 없습니다.")
        opf_path = rootfile.attrib["full-path"]
        package = ET.fromstring(archive.read(opf_path))
        manifest = {p.attrib["id"]: p for p in package.findall(f"./{OPF}manifest/{OPF}item")}
        for itemref in package.findall(f"./{OPF}spine/{OPF}itemref"):
            item = manifest.get(itemref.get("idref"))
            if item is None:
                raise ValueError(f"spine manifest 항목 누락: {itemref.get('idref')}")
            if "nav" in item.get("properties", "").split():
                continue
            if item.get("media-type") != "application/xhtml+xml":
                raise ValueError(f"지원하지 않는 spine 문서: {item.attrib.get('href')}")
            href = unquote(item.attrib["href"].split("#", 1)[0])
            document_path = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))
            doc = ET.fromstring(archive.read(document_path))
            body = doc.find(f".//{XHTML}body")
            if body is None:
                raise ValueError(f"XHTML body 누락: {document_path}")
            paths = {}
            def index_nodes(node, locator):
                paths[id(node)] = locator
                for i, child in enumerate(node):
                    index_nodes(child, f"{locator}/{i}")
            index_nodes(body, "body")

            def kind(node):
                types = node.get(EPUB_TYPE, "").split()
                role = node.get("role")
                if "chapter" in types or role == "doc-chapter":
                    return "chapter"
                if "prologue" in types or role == "doc-prologue":
                    return "prologue"
                return None

            roots = []
            def select(node):
                selected_kind = kind(node)
                if selected_kind:
                    roots.append((node, selected_kind))
                else:
                    for child in node:
                        select(child)
            select(body)
            fallback = not roots
            if fallback:
                non_chapter_types = {"titlepage", "halftitlepage", "imprint", "colophon", "copyright-page", "toc", "cover"}
                types = {t for el in body.iter() for t in el.get(EPUB_TYPE, "").split()}
                if types & non_chapter_types:
                    logging.warning("본문 이외의 EPUB 문서 제외: %s (%s)", document_path, ", ".join(sorted(types & non_chapter_types)))
                    continue
                roots = [(body, "chapter")]
            title_node = doc.find(f"./{XHTML}head/{XHTML}title")
            for root, chapter_kind in roots:
                if chapter_kind == "prologue" and not include_prologue:
                    logging.warning("프롤로그 제외: %s (전체 처리: --include-prologue)", document_path)
                    continue
                number = sum(c.kind == "chapter" for c in chapters) + 1 if chapter_kind == "chapter" else 0
                if chapter_kind == "prologue" and any(c.kind == "prologue" for c in chapters):
                    raise ValueError("여러 프롤로그는 현재 지원하지 않습니다. 문서 분할이 필요합니다.")
                heading = next((el for el in root.iter() if tag(el) in {"h1", "h2", "h3"}), None)
                title = element_text(title_node) if title_node is not None else f"Chapter {number}"
                if len(roots) > 1 and heading is not None:
                    title = element_text(heading)
                root_locator = root.get("id") or paths[id(root)]
                chapter_id = "chapter-" + signature([document_path, root_locator])[:16]
                heading_groups = [el for el in root.iter() if tag(el) == "hgroup"]
                title_source = "\n".join(normalize_text(" ".join("".join(child.itertext()) for child in group)) for group in heading_groups)
                warnings, paragraphs = [], []
                def warn(message):
                    if message not in warnings:
                        warnings.append(message)
                        logging.warning("%s: %s", document_path, message)
                if fallback:
                    warn("명시적 chapter 태그 없음: spine 문서 전체를 챕터로 처리")
                if itemref.get("linear") == "no":
                    warn("linear=no 문서를 포함함: 부록/각주 여부를 확인하세요")
                if len(roots) > 1 or root is not body:
                    selected = {id(el) for node, _ in roots for el in node.iter()}
                    if any(id(el) not in selected and normalize_text(el.text or "") for el in body.iter()):
                        warn("선택한 chapter/prologue 밖에 텍스트가 있음: EPUB 원본 확인 필요")
                def add(text, node, ancestors, suffix=""):
                    if not text.strip():
                        return
                    locator = node.get("id") or paths[id(node)]
                    paragraph_id = f"{chapter_id}-p-" + signature([locator, suffix])[:16]
                    kinds = [tag(node)] + [name for name, _ in ancestors]
                    semantics = " ".join(value for _, value in ancestors) + " " + node.get(EPUB_TYPE, "")
                    block_kind = ("footnote" if any(s in semantics for s in ("footnote", "endnote"))
                                  else "heading" if tag(node).startswith("h") and tag(node)[1:].isdigit()
                                  else "quote" if "blockquote" in kinds else "paragraph")
                    paragraphs.append({"id": paragraph_id, "text": text, "kind": block_kind,
                                       "source_locator": paths[id(node)] + suffix,
                                       "source_element_id": node.get("id"),
                                       "ancestors": [name for name, _ in ancestors]})
                def visit(node, ancestors):
                    name = tag(node)
                    if name not in SUPPORTED:
                        warn(f"확인 필요 <{name}>: 원문 AST 보존, 복잡한 구조의 번역 표현은 미보장")
                    if name in {"script", "style"}:
                        warn(f"<{name}> 번역 제외, 원문 AST에 보존")
                        return
                    # hgroup is represented by the chapter title; raw structure remains in AST.
                    if name == "hgroup":
                        return
                    nested_block = any(tag(child) in BLOCKS for child in list(node.iter())[1:])
                    if name in BLOCKS and not nested_block:
                        add(element_text(node), node, ancestors)
                        return
                    if name in BLOCKS and nested_block:
                        warn(f"중첩 <{name}>를 하위 블록으로 분리")
                    if normalize_text(node.text or ""):
                        add(normalize_text(node.text or ""), node, ancestors, ":text")
                    lineage = ancestors + [(name, node.get(EPUB_TYPE, "") + " " + node.get("role", ""))]
                    for child in node:
                        visit(child, lineage)
                        if normalize_text(child.tail or ""):
                            add(normalize_text(child.tail or ""), child, lineage, ":tail")
                visit(root, [])
                if not paragraphs:
                    logging.warning("번역 블록이 없는 문서: %s#%s (AST 파싱만 가능)", document_path, root_locator)
                if len({p["id"] for p in paragraphs}) != len(paragraphs):
                    raise ValueError(f"중복 XHTML id 또는 문단 ID: {document_path}")
                if paragraphs:
                    chapters.append(Chapter(number, title, document_path, paragraphs, chapter_kind,
                                            chapter_id, source_ast(root), warnings, title_source))
    if not chapters:
        raise ValueError("번역 가능한 챕터/문단을 찾지 못했습니다.")
    return chapters
