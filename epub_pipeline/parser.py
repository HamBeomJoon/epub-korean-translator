"""EPUB adapter. Preserve source AST and expose addressable translation blocks."""
from __future__ import annotations

import logging
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from .domain import Chapter, signature
from .text_layout import lineated

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


def navigation_targets(archive, package, manifest, opf_path):
    targets = set()
    spine = package.find(f"./{OPF}spine")
    ncx_id = spine.get("toc") if spine is not None else None
    for item in manifest.values():
        is_nav = "nav" in item.get("properties", "").split()
        if not is_nav and item.get("id") != ncx_id and item.get("media-type") != "application/x-dtbncx+xml":
            continue
        path = posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), unquote(item.attrib["href"])))
        doc = ET.fromstring(archive.read(path))
        if is_nav:
            links = [node.get("href") for nav in doc.iter()
                     if tag(nav) == "nav" and "toc" in nav.get(EPUB_TYPE, "").split()
                     for node in nav.iter() if tag(node) == "a"]
        else:
            links = [node.get("src") for nav in doc.iter() if tag(nav) == "navMap"
                     for node in nav.iter() if tag(node) == "content"]
        for link in links:
            if not link:
                continue
            url = urlsplit(link)
            if url.scheme or url.netloc:
                continue
            document = posixpath.normpath(posixpath.join(posixpath.dirname(path), unquote(url.path)))
            targets.add((document, unquote(url.fragment)))
    return targets


def is_chapter_heading(node):
    return tag(node) in {"h1", "h2", "h3", "h4", "h5", "h6"} and "chapter" in node.get("class", "").split()


def heading_parts(body, document_path, targets, paths, level):
    """Clip chapter ranges while keeping original XML paths, text and tails."""
    starts, ends = {}, {}
    def index(node):
        starts[id(node)] = len(starts) + len(ends)
        for child in node:
            index(child)
        ends[id(node)] = len(starts) + len(ends)
    index(body)
    blocked = {id(child) for node in body.iter()
               if node.get("id") in {"pg-header", "pg-footer"} or "pg-boilerplate" in node.get("class", "").split()
               for child in node.iter()}
    boundaries = [(starts[id(body)], "continuation", None)]
    for node in body.iter():
        if id(node) in blocked:
            if node.get("id") in {"pg-header", "pg-footer"} or "pg-boilerplate" in node.get("class", "").split():
                boundaries.append((starts[id(node)], "exclude", None))
            continue
        if is_chapter_heading(node):
            boundaries.append((starts[id(node)], "chapter" if element_text(node) else "prologue", node))
        elif (document_path, node.get("id")) in targets:
            heading = next((el for el in node.iter() if tag(el) in {"h1", "h2", "h3", "h4", "h5", "h6"}), None)
            if heading is not None and int(tag(heading)[1]) <= level:
                boundaries.append((starts[id(node)], "exclude", None))
    boundaries.sort(key=lambda value: value[0])

    def clip(node, start, end):
        if starts[id(node)] >= end or ends[id(node)] < start:
            return None
        copy = ET.Element(node.tag, dict(node.attrib))
        paths[id(copy)] = paths[id(node)]
        if start <= starts[id(node)] < end:
            copy.text = node.text
        for child in node:
            part = clip(child, start, end)
            tail = child.tail if start <= ends[id(child)] < end else None
            if part is not None:
                part.tail = tail
                copy.append(part)
            elif tail:
                if len(copy):
                    copy[-1].tail = (copy[-1].tail or "") + tail
                else:
                    copy.text = (copy.text or "") + tail
        return copy

    result = []
    for index, (start, kind, heading) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else ends[id(body)] + 1
        if end > start:
            part = clip(body, start, end) if kind != "exclude" else body
            if kind != "continuation" or element_text(part):
                result.append((part, kind, heading))
    return result


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
        targets = navigation_targets(archive, package, manifest, opf_path)
        documents = []
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
            documents.append((itemref, document_path, doc, body))
        heading_levels = [int(tag(node)[1]) for _, _, _, body in documents for node in body.iter()
                          if is_chapter_heading(node) and element_text(node)]
        heading_mode = bool(heading_levels)
        active_heading = None
        for itemref, document_path, doc, body in documents:
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
            implicit = False
            metadata = {}
            if fallback:
                non_chapter_types = {"titlepage", "halftitlepage", "imprint", "colophon", "copyright-page", "toc", "cover"}
                types = {t for el in body.iter() for t in el.get(EPUB_TYPE, "").split()}
                if types & non_chapter_types:
                    logging.warning("본문 이외의 EPUB 문서 제외: %s (%s)", document_path, ", ".join(sorted(types & non_chapter_types)))
                    active_heading = None
                    continue
                if heading_mode:
                    implicit = True
                    roots = []
                    for part, part_kind, part_heading in heading_parts(body, document_path, targets, paths, min(heading_levels)):
                        roots.append((part, part_kind))
                        metadata[id(part)] = part_heading
                else:
                    roots = [(body, "chapter")]
            else:
                active_heading = None
            title_node = doc.find(f"./{XHTML}head/{XHTML}title")
            for root, chapter_kind in roots:
                if chapter_kind == "exclude":
                    active_heading = None
                    continue
                merge_target = active_heading if chapter_kind == "continuation" else None
                if implicit and chapter_kind != "continuation":
                    active_heading = None
                if chapter_kind == "continuation":
                    if merge_target is None:
                        continue
                    chapter_kind = merge_target.kind
                if chapter_kind == "prologue" and not include_prologue:
                    logging.warning("프롤로그 제외: %s (전체 처리: --include-prologue)", document_path)
                    active_heading = None
                    continue
                number = sum(c.kind == "chapter" for c in chapters) + 1 if chapter_kind == "chapter" else 0
                if chapter_kind == "prologue" and merge_target is None and any(c.kind == "prologue" for c in chapters):
                    raise ValueError("여러 프롤로그는 현재 지원하지 않습니다. 문서 분할이 필요합니다.")
                heading = next((el for el in root.iter() if tag(el) in {"h1", "h2", "h3"}), None)
                title = element_text(title_node) if title_node is not None else f"Chapter {number}"
                split_heading = metadata.get(id(root))
                if split_heading is not None:
                    heading = split_heading
                    title = element_text(heading) or "Prologue"
                elif len(roots) > 1 and heading is not None:
                    title = element_text(heading)
                root_locator = ((split_heading.get("id") or paths[id(split_heading)]) if split_heading is not None
                                else root.get("id") or paths[id(root)])
                chapter_id = "chapter-" + signature([document_path, root_locator])[:16]
                if merge_target is not None:
                    chapter_id, title, number = merge_target.chapter_id, merge_target.title, merge_target.index
                heading_groups = [el for el in root.iter() if tag(el) == "hgroup"]
                title_source = "\n".join(normalize_text(" ".join("".join(child.itertext()) for child in group)) for group in heading_groups)
                warnings, paragraphs = [], []
                def warn(message):
                    if message not in warnings:
                        warnings.append(message)
                        logging.warning("%s: %s", document_path, message)
                if fallback and not implicit:
                    warn("명시적 chapter 태그 없음: spine 문서 전체를 챕터로 처리")
                if itemref.get("linear") == "no":
                    warn("linear=no 문서를 포함함: 부록/각주 여부를 확인하세요")
                if not implicit and (len(roots) > 1 or root is not body):
                    selected = {id(el) for node, _ in roots for el in node.iter()}
                    if any(id(el) not in selected and normalize_text(el.text or "") for el in body.iter()):
                        warn("선택한 chapter/prologue 밖에 텍스트가 있음: EPUB 원본 확인 필요")
                def add(text, node, ancestors, suffix="", preserve=False):
                    if not text.strip():
                        return
                    locator = node.get("id") or paths[id(node)]
                    identity = [document_path, locator, suffix] if implicit else [locator, suffix]
                    paragraph_id = f"{chapter_id}-p-" + signature(identity)[:16]
                    kinds = [tag(node)] + [name for name, _ in ancestors]
                    semantics = " ".join(value for _, value in ancestors) + " " + node.get(EPUB_TYPE, "")
                    block_kind = ("footnote" if any(s in semantics for s in ("footnote", "endnote"))
                                  else "heading" if tag(node).startswith("h") and tag(node)[1:].isdigit()
                                  else "quote" if "blockquote" in kinds else "paragraph")
                    paragraphs.append({"id": paragraph_id, "text": text, "kind": block_kind,
                                       "source_locator": paths[id(node)] + suffix,
                                       "source_element_id": node.get("id"),
                                       "ancestors": [name for name, _ in ancestors],
                                       "preformatted": "pre" in kinds,
                                       "preserve_line_breaks": preserve or block_kind == "heading"
                                       or any(tag(child) == "br" for child in node.iter())})
                    if implicit:
                        paragraphs[-1]["source_file"] = document_path
                def visit(node, ancestors, preserve=False):
                    name = tag(node)
                    preserve = preserve or lineated(name, node.attrib)
                    if implicit and split_heading is not None and paths[id(node)] == paths[id(split_heading)]:
                        return
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
                        add(element_text(node), node, ancestors, preserve=preserve)
                        return
                    if name in BLOCKS and nested_block:
                        warn(f"중첩 <{name}>를 하위 블록으로 분리")
                    if normalize_text(node.text or ""):
                        add(normalize_text(node.text or ""), node, ancestors, ":text", preserve=preserve)
                    lineage = ancestors + [(name, node.get(EPUB_TYPE, "") + " " + node.get("role", ""))]
                    for child in node:
                        visit(child, lineage, preserve)
                        if normalize_text(child.tail or ""):
                            add(normalize_text(child.tail or ""), child, lineage, ":tail", preserve=preserve)
                visit(root, [])
                if not paragraphs:
                    logging.warning("번역 블록이 없는 문서: %s#%s (AST 파싱만 가능)", document_path, root_locator)
                if len({p["id"] for p in paragraphs}) != len(paragraphs):
                    raise ValueError(f"중복 XHTML id 또는 문단 ID: {document_path}")
                if paragraphs:
                    if implicit:
                        fragment = {**source_ast(root), "source_file": document_path}
                        if merge_target is not None:
                            merge_target.paragraphs.extend(paragraphs)
                            merge_target.ast["children"].append(fragment)
                            merge_target.warnings.extend(w for w in warnings if w not in merge_target.warnings)
                            if len({p["id"] for p in merge_target.paragraphs}) != len(merge_target.paragraphs):
                                raise ValueError(f"중복 XHTML id 또는 문단 ID: {document_path}")
                            continue
                        ast = {"tag": "chapter-fragments", "attributes": {}, "text": None, "tail": None,
                               "children": [fragment]}
                    else:
                        ast = source_ast(root)
                    chapter = Chapter(number, title, document_path, paragraphs, chapter_kind,
                                      chapter_id, ast, warnings, title_source)
                    chapters.append(chapter)
                    if implicit:
                        active_heading = chapter
    if not chapters:
        raise ValueError("번역 가능한 챕터/문단을 찾지 못했습니다.")
    if len({chapter.chapter_id for chapter in chapters}) != len(chapters):
        raise ValueError("중복 XHTML 장 ID 또는 spine 문서: 장 경계를 확인하세요.")
    return chapters
