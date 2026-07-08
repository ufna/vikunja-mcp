"""Agent comment text <-> Vikunja HTML (tracker #85).

Vikunja stores task comments (and descriptions) as HTML and its web UI renders them
AS HTML — never Markdown. Agents author PLAIN TEXT with '\\n' line breaks (and the
occasional markdown-ish '**bold**' / '- item'). Sent raw into that HTML field the
newlines collapse to a single space and a multi-paragraph report becomes one
unreadable run-on blob — the complaint in #85. So every agent-authored comment body
is converted to structure-preserving, HTML-escaped HTML on the way IN (text_to_html),
and rendered back to plain text on the way OUT for agents and marker detection
(html_to_text).

Deliberately dependency-free and minimal — this project's ethos (cf. the dependency-
free POSIX-sh SessionStart hook): blank-line-separated blocks -> <p>, single newlines
-> <br>, everything HTML-escaped so a literal '<' or '&' in a report (code, '<id>')
can't corrupt the markup. No Markdown rendering: a run-on with a visible '**' is
already readable once the line breaks are back, and real Markdown->HTML would mean a
new dependency for marginal gain.
"""
import html
import re

_BR = "<br>"


def text_to_html(text: str) -> str:
    """Plain agent text -> structure-preserving, HTML-escaped HTML for a Vikunja
    comment. Blank lines separate <p> paragraphs; single newlines within a paragraph
    become <br>. Runs of blank lines collapse (no empty paragraphs). Empty input ->
    empty string. The ASCII comment markers ([worklog], [review], ...) contain no
    HTML-special characters, so escaping leaves them intact and greppable once
    rendered back by html_to_text."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in normalized.split("\n"):
        if line.strip():
            current.append(line)
        elif current:  # a blank line closes the current paragraph
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)
    return "".join(
        "<p>" + _BR.join(html.escape(line, quote=False) for line in para) + "</p>"
        for para in paragraphs
    )


_CLOSE_P_RE = re.compile(r"</p\s*>", re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(value: str) -> str:
    """Inverse of text_to_html for AGENT-facing output and marker detection:
    paragraph/line boundaries (</p>, <br>) go back to newlines, every other tag is
    dropped, HTML entities are unescaped. No-op-ish on already-plain text, so legacy
    plain comments and human-written ones survive the round trip too."""
    text = _CLOSE_P_RE.sub("\n\n", value or "")
    text = _BR_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    return html.unescape(text).strip()
