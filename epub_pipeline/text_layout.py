"""Conservative paragraph layout; never rewrite words or punctuation."""
import re


LINEATED = {"poem", "poetry", "verse", "stanza", "line", "linegroup"}


def lineated(name: str, attributes: dict) -> bool:
    tokens = (attributes.get("class", "") + " " + attributes.get("{http://www.idpf.org/2007/ops}type", "")).split()
    return name == "pre" or any(token.rsplit(":", 1)[-1] in LINEATED for token in tokens)


def translation_text(text: str, paragraph: dict) -> str:
    if paragraph.get("preserve_line_breaks") is not False:
        return text
    return re.sub(r"\s*[\r\n]+\s*", " ", text).strip()


def annotate_layout(paragraphs: list[dict], ast: dict) -> None:
    """Recover legacy layout from the preserved AST, retaining ambiguous breaks."""
    policies, preformatted = {}, set()
    def visit(node, inherited=False):
        name = node["tag"].rsplit("}", 1)[-1]
        preserve = inherited or lineated(name, node.get("attributes", {}))
        value = node.get("text") or ""
        has_br = name == "br"
        for child in node.get("children", []):
            child_text, child_br = visit(child, preserve)
            value += ("\n" if child["tag"].rsplit("}", 1)[-1] == "br" else child_text) + (child.get("tail") or "")
            has_br = has_br or child_br
        key = value.strip() if name == "pre" else "\n".join(re.sub(r"\s+", " ", part).strip() for part in value.split("\n")).strip()
        if key:
            policies[key] = policies.get(key, False) or preserve or has_br or name in {"h1", "h2", "h3", "h4", "h5", "h6"}
            if name == "pre":
                preformatted.add(key)
        return value, has_br
    visit(ast)
    for paragraph in paragraphs:
        source = paragraph.get("source_text", paragraph.get("text", ""))
        if source in preformatted:
            paragraph["preformatted"] = True
        if isinstance(paragraph.get("preserve_line_breaks"), bool):
            continue
        paragraph["preserve_line_breaks"] = policies.get(source, True)


def render_markdown(title: str, paragraphs: list[dict], values: dict[str, str]) -> str:
    chunks = []
    for paragraph in paragraphs:
        text = translation_text(values[paragraph["id"]], paragraph)
        if paragraph.get("kind") == "heading":
            text = "## " + text.replace("\n", " ")
        elif paragraph.get("kind") == "quote":
            text = "\n".join("> " + line for line in text.splitlines())
        elif paragraph.get("preformatted"):
            text = "\n".join("    " + line for line in text.splitlines())
        if paragraph.get("kind") != "quote" and not text.startswith("    "):
            text = text.replace("\n", "  \n")
        chunks.append(text)
    return "# " + title.replace("\n", " ") + "\n\n" + "\n\n".join(chunks) + "\n"
