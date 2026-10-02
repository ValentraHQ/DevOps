#!/usr/bin/env python3
"""Convert the UTM observability Markdown docs to Confluence storage format.

Confluence "storage format" is XHTML plus Atlassian `ac:`/`ri:` macro tags.
Keeping this as a converter (rather than a hand-written XHTML file) means the
Markdown stays the single source of truth — regenerate after any edit.

Usage:
    python md2confluence.py INPUT.md OUTPUT.xml

Supported: headings, paragraphs, bold/italic/inline-code, links, fenced code
blocks (-> code macro), tables, blockquotes (-> info/warning/note macro),
ordered/unordered lists, horizontal rules, and a table-of-contents macro.
"""
import html
import re
import sys

LANG_MAP = {
    "bash": "bash", "sh": "bash", "shell": "bash",
    "yaml": "yml", "yml": "yml", "json": "json",
    "promql": "text", "diff": "diff", "python": "python",
    "text": "text", "": "text",
}


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def inline(s: str) -> str:
    """Inline Markdown -> Confluence XHTML. Code spans are protected first."""
    spans: list[str] = []

    def stash(m):
        spans.append(m.group(1))
        return f"\x00{len(spans) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, s)
    s = esc(s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", s)
    # [text](url) — skip pure in-page anchors, Confluence builds its own TOC
    s = re.sub(r"\[([^\]]+)\]\((#[^)]*)\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               r'<a href="\2">\1</a>', s)

    def unstash(m):
        return "<code>" + esc(spans[int(m.group(1))]) + "</code>"

    return re.sub(r"\x00(\d+)\x00", unstash, s)


def code_macro(lang: str, body: str) -> str:
    lang = LANG_MAP.get(lang.strip().lower(), "text")
    return (
        '<ac:structured-macro ac:name="code" ac:schema-version="1">'
        f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
        '<ac:parameter ac:name="theme">Midnight</ac:parameter>'
        '<ac:parameter ac:name="linenumbers">false</ac:parameter>'
        f'<ac:plain-text-body><![CDATA[{body.rstrip()}]]></ac:plain-text-body>'
        "</ac:structured-macro>"
    )


def panel_macro(kind: str, body_html: str) -> str:
    return (
        f'<ac:structured-macro ac:name="{kind}" ac:schema-version="1">'
        f"<ac:rich-text-body>{body_html}</ac:rich-text-body>"
        "</ac:structured-macro>"
    )


def convert(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        ln = lines[i]

        # ---- fenced code block -------------------------------------------
        if ln.lstrip().startswith("```"):
            lang = ln.lstrip()[3:].strip()
            i += 1
            buf = []
            while i < n and not lines[i].lstrip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            out.append(code_macro(lang, "\n".join(buf)))
            continue

        # ---- horizontal rule ---------------------------------------------
        if re.fullmatch(r"\s*---+\s*", ln):
            out.append("<hr />")
            i += 1
            continue

        # ---- heading ------------------------------------------------------
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            lvl = len(m.group(1))
            txt = m.group(2).strip()
            # strip trailing anchor ids
            txt = re.sub(r"\s*\{#.*\}$", "", txt)
            out.append(f"<h{lvl}>{inline(txt)}</h{lvl}>")
            i += 1
            # insert TOC immediately after the document title
            if lvl == 1 and not any("ac:name=\"toc\"" in o for o in out):
                out.append(
                    '<ac:structured-macro ac:name="toc" ac:schema-version="1">'
                    '<ac:parameter ac:name="maxLevel">3</ac:parameter>'
                    '<ac:parameter ac:name="minLevel">2</ac:parameter>'
                    '<ac:parameter ac:name="outline">true</ac:parameter>'
                    "</ac:structured-macro>"
                )
            continue

        # ---- blockquote -> panel macro ------------------------------------
        if ln.lstrip().startswith(">"):
            buf = []
            while i < n and (lines[i].lstrip().startswith(">") or
                             (lines[i].strip() == "" and i + 1 < n and
                              lines[i + 1].lstrip().startswith(">"))):
                stripped = re.sub(r"^\s*>\s?", "", lines[i])
                buf.append(stripped)
                i += 1
            inner = convert_fragment("\n".join(buf))
            joined = "\n".join(buf)
            kind = "info"
            if re.search(r"⚠|warning|never|must not|danger|revert", joined, re.I):
                kind = "warning"
            elif re.search(r"note", joined, re.I):
                kind = "note"
            out.append(panel_macro(kind, inner))
            continue

        # ---- table --------------------------------------------------------
        if ln.strip().startswith("|") and i + 1 < n and \
                re.match(r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]):
            header = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in
                             lines[i].strip().strip("|").split("|")])
                i += 1
            t = ["<table><tbody><tr>"]
            t += [f"<th>{inline(h)}</th>" for h in header]
            t.append("</tr>")
            for r in rows:
                t.append("<tr>")
                r += [""] * (len(header) - len(r))
                t += [f"<td>{inline(c)}</td>" for c in r[:len(header)]]
                t.append("</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue

        # ---- lists --------------------------------------------------------
        if re.match(r"^\s*[-*+]\s+", ln) or re.match(r"^\s*\d+\.\s+", ln):
            ordered = bool(re.match(r"^\s*\d+\.\s+", ln))
            tag = "ol" if ordered else "ul"
            items = []
            while i < n and (re.match(r"^\s*[-*+]\s+", lines[i]) or
                             re.match(r"^\s*\d+\.\s+", lines[i])):
                txt = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", lines[i])
                items.append(f"<li>{inline(txt)}</li>")
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue

        # ---- blank --------------------------------------------------------
        if not ln.strip():
            i += 1
            continue

        # ---- paragraph ----------------------------------------------------
        buf = [ln]
        i += 1
        while i < n and lines[i].strip() and \
                not lines[i].lstrip().startswith(("```", ">", "|", "#")) and \
                not re.match(r"^\s*[-*+]\s+", lines[i]) and \
                not re.match(r"^\s*\d+\.\s+", lines[i]) and \
                not re.fullmatch(r"\s*---+\s*", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{inline(' '.join(x.strip() for x in buf))}</p>")

    return "\n".join(out)


def convert_fragment(md: str) -> str:
    """Convert without injecting a TOC (used inside panel macros)."""
    body = convert(md)
    return re.sub(r'<ac:structured-macro ac:name="toc".*?</ac:structured-macro>',
                  "", body, flags=re.S)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    md = open(sys.argv[1], encoding="utf-8").read()
    xhtml = convert(md)
    open(sys.argv[2], "w", encoding="utf-8").write(xhtml)
    print(f"wrote {sys.argv[2]}: {len(xhtml)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
