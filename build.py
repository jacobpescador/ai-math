#!/usr/bin/env python3
"""Generates the site from the files each paper posts. Run `python3 build.py`; it needs Python 3.11 and latex2mathml
3.81.1 (`python3 -m pip install latex2mathml==3.81.1`), pinned so that every machine builds the same pages.

Sources, per paper in papers/<slug>/, each fact written once:
  v<n>/<slug>.tex     version n: its title, authors and abstract are read from here, and the version number and date
                      it states (in \\thanks and pdfsubject) must be the ones paper.toml gives it
  v<n>/<slug>.pdf     version n; from v3 on, compiled from that TeX by the posting kit, which stamps page 1's left
                      margin with the site, the version and its date, as arXiv does
  afterword/<n>.html  Afterword edition n, converted from its Markdown when it was posted
  paper.toml          what the files cannot say: who posted the paper, its status and summary, each version's date and
                      note, and each edition's date, note, summary, method set and the version it explains
A version's files never change after its day; Afterwords carry no such promise. afterword.toml holds the Afterword
method sets.
Output: index.html, feed.xml, and per paper index.html (the latest version), v<n>/index.html, <slug>.pdf (a copy of the
latest version), and, when a paper has an Afterword, afterword/index.html and afterword/<n>/index.html.

Before writing anything it refuses: a missing or undeclared file under papers/, a stated version or date that is not
the version's, dates that do not increase, an abstract or title it cannot render faithfully, a self-citation that
differs from the site's, and any link between the site's own files that points nowhere.
`python3 build.py --release` also prints, as JSON, the release (every published file, and nothing else) and the date of
each version file, which is frozen after its day.
"""

import html
import json
import re
import sys
import tomllib
import unicodedata
from html.parser import HTMLParser
from importlib import metadata
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parent
SITE = "https://jacobpescador.github.io/ai-math"
REPO = "https://github.com/jacobpescador/ai-math"
NAME = "AI Math"
TAGLINE = "Mathematics found and written by AI systems. Every version of every paper stays up at its own address, its TeX and PDF unchanged after the day it is posted."
CC0 = '<a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0 1.0</a>'
DEDICATION = f"To the extent any copyright exists, dedicated to the public domain ({CC0})"
STATIC = [".gitignore", ".nojekyll", "LICENSE", "README.md", "build.py", "style.css", "fonts/OFL.txt",
          *sorted(str(f.relative_to(ROOT)) for f in (ROOT / "fonts").glob("*.woff2"))]
LATEX2MATHML = "3.81.1"

e = html.escape


class Refused(Exception):
    """A source the site cannot show faithfully; the message says which and why."""


def fail(message):
    raise SystemExit(f"build.py: {message}")


try:
    if metadata.version("latex2mathml") != LATEX2MATHML:
        raise ImportError
    from latex2mathml.converter import convert as latex2mathml
except (ImportError, metadata.PackageNotFoundError):
    fail(f"needs latex2mathml {LATEX2MATHML}: python3 -m pip install latex2mathml=={LATEX2MATHML}")


# TeX, read as the site shows it ------------------------------------------------------------------------------------

def uncomment(tex):
    """TeX without comments: a % eats the rest of its line, the line break and the next line's leading spaces."""
    return re.sub(r"(?<!\\)%[^\n]*(\n[ \t]*)?", "", tex)


def group(tex, i):
    """The text inside the braces opening at tex[i], and the index after them."""
    if i >= len(tex) or tex[i] != "{":
        raise Refused(f"expected {{ at …{tex[i:i + 30]}…")
    depth, j = 0, i
    while j < len(tex):
        if tex[j] == "\\":
            j += 2
            continue
        if tex[j] == "{":
            depth += 1
        elif tex[j] == "}":
            depth -= 1
            if depth == 0:
                return tex[i + 1:j], j + 1
        j += 1
    raise Refused(f"unbalanced braces after …{tex[i:i + 30]}…")


def arguments(tex, command):
    """Each use of \\command[optional]{mandatory} in tex, as (optional or None, mandatory)."""
    found = []
    for m in re.finditer(rf"\\{command}(?![a-zA-Z])\s*", tex):
        i, optional = m.end(), None
        if tex[i:i + 1] == "[":
            j = tex.index("]", i)
            optional, i = tex[i + 1:j], j + 1
        found.append((optional, group(tex, i)[0]))
    return found


def macros(tex):
    """The preamble's macros without arguments, by name; a macro with arguments maps to None (refused where used)."""
    found = {}
    for m in re.finditer(r"\\(?:re)?newcommand\*?\s*\{?\\([a-zA-Z]+)\}?\s*(\[\d\])?\s*(?=\{)", tex):
        found[m.group(1)] = None if m.group(2) else group(tex, m.end())[0]
    for m in re.finditer(r"\\def\\([a-zA-Z]+)(#?)", tex):
        if tex[m.end():m.end() + 1] == "{" or m.group(2):
            found[m.group(1)] = None if m.group(2) else group(tex, m.end())[0]
    for m in re.finditer(r"\\DeclareMathOperator\*?\s*\{?\\([a-zA-Z]+)\}?\s*(?=\{)", tex):
        found[m.group(1)] = r"\operatorname{" + group(tex, m.end())[0] + "}"
    for m in re.finditer(r"\\DeclarePairedDelimiter\s*\{?\\([a-zA-Z]+)\}?", tex):
        found[m.group(1)] = None
    return found


def expand(tex, defs):
    for _ in range(20):
        new = re.sub(r"\\([a-zA-Z]+)", lambda m: macro(m, defs), tex)
        if new == tex:
            return tex
        tex = new
    raise Refused("macros nest too deeply")


def macro(m, defs):
    if m.group(1) not in defs:
        return m.group(0)
    if defs[m.group(1)] is None:
        raise Refused(f"\\{m.group(1)} takes arguments, which the site cannot expand")
    return "{" + defs[m.group(1)] + "}"


SPACED = r"(?:log|ln|exp|sin|cos|tan|det|deg|dim|ker|max|min|sup|inf|lim|gcd|mod|Re|Im|arg|Pr)"


def math(tex, display, defs={}):
    """A TeX formula as MathML, refused if latex2mathml leaves any command unread."""
    try:
        m = latex2mathml(expand(tex, defs), display="block" if display else "inline")
    except Refused:
        raise
    except Exception as error:
        raise Refused(f"cannot convert ${tex}$ ({type(error).__name__}: {error})")
    if "\\" in m:
        raise Refused(f"${tex}$ uses {re.search(r'[\\][a-zA-Z]+|[\\].', m).group()}, which latex2mathml does not know")
    m = m.replace(' xmlns="http://www.w3.org/1998/Math/MathML"', "").replace(' display="inline"', "")
    m = re.sub(r"&#x0*([0-9A-Fa-f]+);", lambda g: g.group(0) if g.group(1).upper() in ("3C", "26", "3E") else chr(int(g.group(1), 16)), m)
    m = m.replace(" />", "/>")
    # thin spaces around a function name, as TeX sets them: after it unless a parenthesis or the end follows, and
    # before it after a symbol, number, fraction, script or closing parenthesis
    m = re.sub(rf"<mi>({SPACED})</mi>(?!<mo[^>]*>\(|</mrow>|</math>|<mspace)", r'<mi>\1</mi><mspace width="0.1667em"/>', m)
    m = re.sub(rf"(</mi>|</mn>|</mfrac>|</msub>|</msup>|</msqrt>|\)</mo>|</mstyle>)<mi>({SPACED})</mi>", r'\1<mspace width="0.1667em"/><mi>\2</mi>', m)
    # a sign with no operand before it is unary, as TeX sets it: "= −1", not "= − 1"
    m = re.sub(r"(<mo[^>]*>[^<)\]|}!′⋯…]*</mo>|<mrow>|<mtd>|<math[^>]*>)<mo>([−+])</mo>", r'\1<mo form="prefix">\2</mo>', m)
    return f'<div class="display">{m}</div>' if display else m


ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303", "=": "\u0304", ".": "\u0307",
           "c": "\u0327", "v": "\u030c", "u": "\u0306", "H": "\u030b", "k": "\u0328", "r": "\u030a"}
SYMBOLS = {"%": "%", "&": "&amp;", "$": "$", "#": "#", "_": "_", "{": "{", "}": "}", ",": "\u2009", " ": " ",
           "@": "", "/": "", "-": "", "ldots": "…", "dots": "…", "textendash": "–", "textemdash": "—", "S": "§",
           "TeX": "TeX", "LaTeX": "LaTeX", "ss": "ß", "o": "ø", "O": "Ø", "ae": "æ", "l": "ł", "i": "ı"}
STYLES = {"emph": "em", "textit": "em", "textbf": "strong", "textrm": "", "textup": "", "text": "", "mbox": ""}
DISPLAY = r"\\\[(.*?)\\\]|\$\$(.*?)\$\$|\\begin\{(equation|align|gather|multline)(\*?)\}(.*?)\\end\{\3\4\}"


def tex_inline(tex, defs):
    """A run of TeX text, with inline formulas, as HTML; anything it cannot render faithfully is refused."""
    out, i = [], 0
    while i < len(tex):
        c = tex[i]
        if c == "$" or tex.startswith(r"\(", i):
            close = "$" if c == "$" else r"\)"
            j = tex.find(close, i + len(close))
            if j < 0:
                raise Refused(f"unclosed formula at …{tex[i:i + 30]}…")
            formula, i = math(tex[i + len(close):j], False, defs), j + len(close)
            after = re.match(r"(?:''|[.,:;)?!])+|[A-Za-z']+(?=[\s.,:;)?!]|$)", tex[i:])
            if after:
                formula = f'<span class="nobr">{formula}{tex_inline(after.group(), defs)}</span>'
                i += after.end()
            out.append(formula)
        elif c == "\\":
            m = re.match(r"\\([a-zA-Z]+)\s*|\\(.)", tex[i:], re.S)
            name, i = m.group(1) or m.group(2), i + m.end()
            if name in ACCENTS and (m.group(2) or name in {"c", "v", "u", "H", "k", "r"}):
                letter, i = (group(tex, i) if tex[i:i + 1] == "{" else (tex[i], i + 1))
                out.append(unicodedata.normalize("NFC", letter + ACCENTS[name]))
            elif name in SYMBOLS:
                out.append(SYMBOLS[name])
            elif name in STYLES:
                inner, i = group(tex, i)
                tag = STYLES[name]
                out.append(f"<{tag}>{tex_inline(inner, defs)}</{tag}>" if tag else tex_inline(inner, defs))
            elif name in ("url", "href"):
                url, i = group(tex, i)
                text = e(url)
                if name == "href":
                    inner, i = group(tex, i)
                    text = tex_inline(inner, defs)
                out.append(f'<a href="{e(url)}">{text}</a>')
            elif name in defs:
                if defs[name] is None:
                    raise Refused(f"\\{name} takes arguments, which the site cannot expand")
                tex = tex[:i] + "{" + defs[name] + "}" + tex[i:]
            else:
                raise Refused(f"\\{name} is a command the site cannot render")
        elif c in "{}":
            i += 1
        elif tex.startswith("---", i):
            out.append("—"); i += 3
        elif tex.startswith("--", i):
            out.append("–"); i += 2
        elif tex.startswith("``", i):
            out.append("“"); i += 2
        elif tex.startswith("''", i):
            out.append("”"); i += 2
        elif c == "`":
            out.append("‘"); i += 1
        elif c == "'":
            out.append("’"); i += 1
        elif c == "~":
            out.append("\u00a0"); i += 1
        elif c.isspace():
            out.append(" "); i += 1
            while i < len(tex) and tex[i].isspace():
                i += 1
        else:
            out.append(e(c, quote=False)); i += 1
    return "".join(out)


def tex_blocks(tex, defs):
    """TeX paragraphs and displayed formulas as HTML: each paragraph a <p>, each display a block formula."""
    out = []
    for paragraph in re.split(r"\n\s*\n", tex.strip()):
        pos = 0
        for m in re.finditer(DISPLAY, paragraph, re.S):
            if paragraph[pos:m.start()].strip():
                out.append(f"<p>{tex_inline(paragraph[pos:m.start()].strip(), defs)}</p>")
            body = next(g for g in (m.group(1), m.group(2), m.group(5)) if g is not None)
            if m.group(3) and m.group(3) != "equation":
                raise Refused(f"the site shows no {m.group(3)} environment; write the display with \\[ \\]")
            out.append(math(body.strip(), True, defs))
            pos = m.end()
        if paragraph[pos:].strip():
            out.append(f"<p>{tex_inline(paragraph[pos:].strip(), defs)}</p>")
    return "\n".join(out)


def text_of(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def read_tex(path):
    """A version's title, authors and abstract as the site shows them, and the version and dates it states."""
    source = path.read_text()
    tex = uncomment(source)
    defs = macros(tex.split(r"\begin{document}")[0])
    try:
        titles = arguments(tex, "title")
        if not titles:
            raise Refused("it has no \\title")
        title_tex = re.sub(r"\\\\(\[[^]]*\])?|\\newline", " ", titles[0][1])
        if "$" in title_tex:
            raise Refused("its title holds a formula, which the site does not show in titles")
        title = text_of(tex_inline(title_tex, defs))
        authors = []
        for short, full in arguments(tex, "author"):
            names = re.split(r"\s*(?:\\and\b|,|\band\b)\s*", short or full)
            authors += [text_of(tex_inline(a, defs)) for a in names if a.strip()]
        if not authors:
            raise Refused("it has no \\author")
        if r"\begin{abstract}" not in tex:
            raise Refused("it has no abstract")
        abstract = tex_blocks(tex.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0], defs)
    except Refused as why:
        fail(f"{path.relative_to(ROOT)}: {why}")
    stated = [m.group(1) for m in re.finditer(r"pdfsubject=\{[^}]*?(version \d+, \d{1,2} \w+ \d{4})", tex)]
    stated += [m.group(1).replace("~", " ").replace("V", "v", 1)
               for m in re.finditer(r"\\thanks\{(Version~\d+, \d{1,2} \w+ \d{4})", tex)]
    pdfauthor = [m.group(1) for m in re.finditer(r"pdfauthor=\{([^}]*)\}", tex)]
    dedicatory = [text_of(tex_inline(d, defs)) for _, d in arguments(tex, "dedicatory")]
    return {"title": title, "authors": authors, "abstract": abstract, "stated": stated, "source": source,
            "pdfauthor": pdfauthor, "dedicatory": dedicatory}


# Facts and pages ----------------------------------------------------------------------------------------------------

def long_date(d):
    return f"{d.day} {d:%B %Y}"


def and_list(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def byline(p, v):
    posted = f' · posted and maintained by {p["posted_by"]}' if p.get("posted_by") else ""
    return and_list(v["authors"]) + posted


def note(entry):
    """What changed in a version or edition; the first has nothing to say."""
    return f'<div class="note">{e(entry["note"])}</div>' if entry.get("note") else ""


def bibtex_text(text):
    """Text as BibTeX reads it: TeX's special characters escaped, dashes as TeX writes them."""
    special = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "#": r"\#",
               "_": r"\_", "$": r"\$", "^": r"\^{}", "~": r"\~{}"}
    return "".join(special.get(c, c) for c in text).replace("–", "--").replace("—", "---")


def initials(name):
    """"Jacob Pescador" as a citation gives it: "J. Pescador"."""
    *given, family = name.split()
    return " ".join(f"{g[0]}." for g in given) + "\u00a0" + family


def plain(text):
    """Text as a citation compares it: TeX or HTML markup, quotation marks and spacing aside."""
    text = re.sub(r"\\(?:url|emph|textit)\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\href\{[^}]*\}\{([^}]*)\}", r"\1", text)
    text = re.sub(r"<[^>]+>", "", html.unescape(text)).replace("--", "\u2013").replace("~", " ").replace("\\,", " ")
    text = re.sub(r"[{}“”\"*]", "", text.replace("\u00a0", " "))
    return re.sub(r"\s+", " ", text).strip().rstrip(".")


def poster(p):
    return f' (posted by {initials(p["posted_by"])})' if p.get("posted_by") else ""


def citation(p, v):
    """A version's citation as plain text, the one form every self-citation must take."""
    return plain(f'{and_list(v["authors"])}{poster(p)}, {v["title"]}, preprint, version {v["n"]}, '
                 f'{long_date(v["date"])}, {p["url"]}{v["page"]}')


def check_self_citations(p):
    """The latest version cites its earlier versions as the site does."""
    latest = p["latest"]
    for m in re.finditer(r"\\bibitem\{v(\d+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\})", latest["source"], re.S):
        k = int(m.group(1))
        if not 1 <= k <= len(p["versions"]):
            fail(f'{p["slug"]}: {latest["tex"]} cites a version {k} that does not exist')
        want = citation(p, p["versions"][k - 1])
        if plain(m.group(2)) != want:
            fail(f'{p["slug"]}: {latest["tex"]} cites version {k} as\n  {plain(m.group(2))}\nbut the site cites it as\n  {want}')


def load_sets():
    source = ROOT / "afterword.toml"
    sets = tomllib.loads(source.read_text())["sets"] if source.is_file() else {}
    return {int(n): s for n, s in sets.items()}


def increasing(entries, what, slug):
    for a, b in zip(entries, entries[1:]):
        if not a["date"] < b["date"]:
            fail(f"{slug}: {what} {b['n']} is dated {b['date']}, not after {what} {a['n']} ({a['date']}); "
                 "a day has at most one, and a revision the same day replaces it")


def load_paper(directory, sets):
    slug = directory.name
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        fail(f"{slug}: a paper's folder name must be lowercase letters, digits and hyphens")
    paper = tomllib.loads((directory / "paper.toml").read_text())
    unknown = set(paper) - {"posted_by", "status", "summary", "versions", "afterword"}
    if unknown or not paper.get("versions") or not paper.get("summary") or not paper.get("status"):
        fail(f"{slug}: paper.toml needs status, summary and at least one [[versions]], and holds nothing else "
             f"(title and authors come from each version's TeX){'; found ' + ', '.join(sorted(unknown)) if unknown else ''}")
    paper["slug"], paper["url"] = slug, f"{SITE}/papers/{slug}/"
    for n, v in enumerate(paper["versions"], 1):
        v.update(n=n, pdf=f"v{n}/{slug}.pdf", tex=f"v{n}/{slug}.tex", page=f"v{n}/")
        for file in (v["pdf"], v["tex"]):
            if not (directory / file).is_file():
                fail(f"{slug}: v{n} lacks {file}")
        v.update(read_tex(directory / v["tex"]))
        expected = f'version {n}, {long_date(v["date"])}'
        for stated in v["stated"]:
            if stated != expected:
                fail(f'{slug}: {v["tex"]} says "{stated}", but it is posted as {expected}')
        poster = paper.get("posted_by", "")
        for meta in v["pdfauthor"]:
            missing = [a for a in v["authors"] + ([poster] if poster else []) if a not in meta]
            if missing:
                fail(f'{slug}: {v["tex"]} pdfauthor "{meta}" does not name {", ".join(missing)}')
        for line in v["dedicatory"]:
            if poster and poster not in line:
                fail(f'{slug}: {v["tex"]} names its poster as "{line}", but paper.toml has {poster}')
    increasing(paper["versions"], "version", slug)
    paper["latest"] = paper["versions"][-1]
    paper["title"], paper["pdf"] = paper["latest"]["title"], f"{slug}.pdf"
    for n, a in enumerate(paper.get("afterword", []), 1):
        a["n"] = n
        a["method"] = sets.get(a.get("set")) or fail(f"{slug}: Afterword {n} names no set in afterword.toml")
        if not 1 <= a.get("explains", 0) <= len(paper["versions"]):
            fail(f"{slug}: Afterword {n} explains a version that does not exist")
        if paper["versions"][a["explains"] - 1]["date"] > a["date"]:
            fail(f"{slug}: Afterword {n} is dated before the version it explains")
        source = directory / "afterword" / f"{n}.html"
        if not source.is_file():
            fail(f"{slug}: Afterword {n} lacks afterword/{n}.html")
        a["body"] = source.read_text().strip()
    paper["afterwords"] = paper.pop("afterword", [])
    increasing(paper["afterwords"], "Afterword", slug)
    check_self_citations(paper)
    return paper


def page(title, root, canonical, description, body, head=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<link rel="canonical" href="{canonical}">
<link rel="icon" href="data:,">
<link rel="alternate" type="application/atom+xml" title="{NAME}" href="{SITE}/feed.xml">
<link rel="stylesheet" href="{root}style.css">
{head}</head>
<body>
{body}
</body>
</html>
"""


def tags(kind, pairs):
    return "".join(f'<meta {kind}="{k}" content="{e(c)}">\n' for k, c in pairs)


def footer(history):
    return f'<footer>{DEDICATION} · <a href="{history}">full history</a></footer>'


def index_page(papers):
    entries = []
    for p in papers:
        v = p["latest"]
        links = [f'<a href="papers/{p["slug"]}/{p["pdf"]}">PDF</a>', f'<a href="papers/{p["slug"]}/{v["tex"]}">TeX source</a>']
        if p["afterwords"]:
            links.append(f'<a href="papers/{p["slug"]}/afterword/">Afterword</a>')
        entries.append(f"""<h2><a href="papers/{p["slug"]}/">{e(p["title"])}</a></h2>
<p class="meta">{e(byline(p, v))} · v{v["n"]}, {long_date(v["date"])} · {e(p["status"])} · {" · ".join(links)}</p>""")
    body = f"""<h1>{NAME}</h1>
<p>{TAGLINE}</p>

{chr(10).join(entries)}

<footer>{DEDICATION} · <a href="{REPO}">source and full history</a></footer>"""
    head = tags("property", [("og:type", "website"), ("og:site_name", NAME), ("og:title", NAME),
                             ("og:description", TAGLINE), ("og:url", f"{SITE}/")])
    return page(NAME, "", f"{SITE}/", TAGLINE, body, head)


def cite(p, v, url):
    """The Cite section for version v of paper p, at the address url."""
    posted = f' posted by {initials(p["posted_by"]).replace(chr(160), "~")},' if p.get("posted_by") else ""
    text = (f'{e(and_list(v["authors"]))}{e(poster(p))}, “{e(v["title"])},” preprint, version {v["n"]}, '
            f'{long_date(v["date"])}, <a href="{url}">{url}</a>.')
    bibtex = f"""@misc{{aimath-{p["slug"]},
  author       = {{{" and ".join("{" + a + "}" for a in v["authors"])}}},
  title        = {{{{{bibtex_text(v["title"])}}}}},
  howpublished = {{Preprint,{posted} \\url{{{url}}}}},
  note         = {{Version {v["n"]}, {long_date(v["date"])}}},
  year         = {{{p["versions"][0]["date"].year}}}
}}"""
    return f"""<h2>Cite</h2>
<p>{text}</p>
<details>
<summary>BibTeX</summary>
<pre>{e(bibtex)}</pre>
</details>
<p class="meta">To report an error, <a href="{REPO}/issues">open an issue</a>. Corrections appear as revised versions.</p>"""


def version_list(p, here, root):
    """Every version of p, newest first: the one shown here in bold, the others linked, the latest to the paper page."""
    def label(x):
        if x is here:
            return f'<strong>v{x["n"]}</strong>'
        return f'<a href="{root}{"" if x is p["latest"] else x["page"]}">v{x["n"]}</a>'
    return "\n".join(f"""  <li>{label(x)} · {long_date(x["date"])} · <a href="{root}{x["pdf"]}">PDF</a> · <a href="{root}{x["tex"]}">TeX</a>
    {note(x)}</li>""" for x in reversed(p["versions"]))


def paper_page(p, v, root):
    """The page of paper p showing version v: the paper's own page for the latest (root ""), and v<n>/ for every
    version (root "../"). Only the version's own facts differ: its title, authors, date, files, abstract and
    citation, and which version is bold in the list."""
    url = p["url"] + (root and v["page"])
    # Scholar's tags go on the paper's own page only, so the versions are not indexed as separate works.
    head = "" if root else tags("name", [
        ("citation_title", v["title"]), *[("citation_author", a) for a in v["authors"]],
        ("citation_publication_date", f"{p['versions'][0]['date']:%Y/%m/%d}"),
        ("citation_abstract_html_url", p["url"]), ("citation_pdf_url", p["url"] + p["pdf"])])
    head += tags("property", [("og:type", "article"), ("og:site_name", NAME), ("og:title", v["title"]),
                              ("og:description", p["summary"]), ("og:url", url)])
    links = [f'<a href="{root + v["pdf"] if root else p["pdf"]}">PDF</a>', f'<a href="{root}{v["tex"]}">TeX source</a>']
    if p["afterwords"]:
        links.append(f'<a href="{root}afterword/">Afterword</a>')
    body = f"""<p class="meta"><a href="../../{root}">{NAME}</a></p>
<h1>{e(v["title"])}</h1>
<p class="meta">{e(byline(p, v))} · v{v["n"]}, {long_date(v["date"])} · {e(p["status"])} ·
  {" · ".join(links)}</p>

{v["abstract"]}

<h2>Versions</h2>
<ul class="versions">
{version_list(p, v, root)}
</ul>

{cite(p, v, url)}

{footer(f'{REPO}/commits/main/papers/{p["slug"]}')}"""
    return page(v["title"], f"../../{root}", url, p["summary"], body, head)


def afterword_page(p, edition, root, canonical):
    """Afterword edition `edition` of p: at afterword/ for the latest, and at afterword/<n>/ for every edition."""
    last = p["afterwords"][-1]
    explains = p["versions"][edition["explains"] - 1]
    context = f' {e(edition["context"])}' if edition.get("context") else ""

    def label(x):
        if x is edition:
            return f'<strong>Edition {x["n"]}</strong>'
        return f'<a href="{root}papers/{p["slug"]}/afterword/{"" if x is last else str(x["n"]) + "/"}">Edition {x["n"]}</a>'
    editions = "\n".join(f"""  <li>{label(x)} · {long_date(x["date"])} · on v{x["explains"]}
    {note(x)}</li>""" for x in reversed(p["afterwords"]))
    body = f"""<p class="meta"><a href="{root}">{NAME}</a> · <a href="{root}papers/{p["slug"]}/">{e(p["title"])}</a></p>
<h1>Afterword</h1>
<p class="meta">Edition {edition["n"]}, {long_date(edition["date"])} · on v{explains["n"]} of the paper, {long_date(explains["date"])}</p>
<p class="meta">{e(edition["method"]["how"])}{context}</p>

{edition["body"]}

<h2>Editions</h2>
<ul class="versions">
{editions}
</ul>

{footer(f'{REPO}/commits/main/papers/{p["slug"]}/afterword')}"""
    title = f"Afterword · {p['title']}"
    description = edition.get("summary") or f"Afterword to “{p['title']}”."
    head = tags("property", [("og:type", "article"), ("og:site_name", NAME), ("og:title", title),
                             ("og:description", description), ("og:url", canonical)])
    return page(title, root, canonical, description, body, head)


def feed(papers):
    items = []
    for p in papers:
        for v in p["versions"]:
            url = p["url"] + v["page"]
            items.append((v["date"], (v["n"], 0, 0), f'{v["title"]}, v{v["n"]}', url, v.get("note") or p["summary"], v["authors"]))
        for a in p["afterwords"]:
            url = f'{p["url"]}afterword/{a["n"]}/'
            items.append((a["date"], (a["explains"], 1, a["n"]), f'Afterword to {p["title"]}, edition {a["n"]}', url,
                          a.get("note") or a.get("summary", ""), p["latest"]["authors"]))
    items.sort(key=lambda i: (i[0], i[1]), reverse=True)  # newest first; an edition just after the version it explains
    entries = "\n".join(f"""<entry>
  <title>{e(title)}</title>
  <link href="{url}"/>
  <id>{url}</id>
  <updated>{date}T00:00:00Z</updated>
{"".join(f"  <author><name>{e(a)}</name></author>{chr(10)}" for a in authors)}{f"  <summary>{e(text)}</summary>{chr(10)}" if text else ""}</entry>""" for date, _, title, url, text, authors in items)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>{NAME}</title>
<subtitle>{e(TAGLINE)}</subtitle>
<link href="{SITE}/"/>
<link rel="self" href="{SITE}/feed.xml"/>
<id>{SITE}/</id>
<updated>{items[0][0]}T00:00:00Z</updated>
{entries}
</feed>
"""


# Links --------------------------------------------------------------------------------------------------------------

class Anchors(HTMLParser):
    """Every link (href or src) in a page, and every id it defines."""
    def __init__(self, text):
        super().__init__()
        self.links, self.ids = [], set()
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src", "xlink:href") and value:
                self.links.append(value)
            if name == "id" and value:
                self.ids.add(value)
    handle_startendtag = handle_starttag


def broken_links(texts, release):
    """Links from the site's pages, and site addresses in its TeX, that resolve to no published file or anchor."""
    files, broken = set(release), []
    ids = {name: Anchors(text).ids for name, text in texts.items() if name.endswith(".html")}
    for name, text in texts.items():
        if name.endswith((".html", ".xml")):
            links = Anchors(text).links
        else:
            links = re.findall(rf"{re.escape(SITE)}/[^\s}}]*", text)
        for link in links:
            url = urljoin(f"{SITE}/{name}", link)
            if not (url + "/").startswith(SITE + "/"):
                continue
            parts = urlsplit(url)
            path = parts.path[len(urlsplit(SITE).path) + 1:]
            target = path + "index.html" if path == "" or path.endswith("/") else path
            if target not in files:
                broken.append(f"{name} links to {link}, which is not on the site")
            elif parts.fragment and target in ids and parts.fragment not in ids[target]:
                broken.append(f"{name} links to {link}, but {target} has no id {parts.fragment}")
    return broken


def main():
    sets = load_sets()
    papers = [load_paper(d, sets) for d in sorted((ROOT / "papers").iterdir()) if (d / "paper.toml").is_file()]
    papers.sort(key=lambda p: (p["latest"]["date"], p["title"]), reverse=True)
    out = {"index.html": index_page(papers), "feed.xml": feed(papers)}
    copies, frozen, editions = {}, {}, []
    for p in papers:
        base = f'papers/{p["slug"]}/'
        out[base + "index.html"] = paper_page(p, p["latest"], "")
        copies[base + p["pdf"]] = base + p["latest"]["pdf"]
        for v in p["versions"]:
            out[base + v["page"] + "index.html"] = paper_page(p, v, "../")
            frozen.update({base + v["pdf"]: v["date"], base + v["tex"]: v["date"]})
        for a in p["afterwords"]:
            canonical = f'{p["url"]}afterword/{a["n"]}/'
            out[base + f'afterword/{a["n"]}/index.html'] = afterword_page(p, a, "../../../../", canonical)
            editions.append(base + f'afterword/{a["n"]}.html')
        if p["afterwords"]:
            out[base + "afterword/index.html"] = afterword_page(p, p["afterwords"][-1], "../../../", f'{p["url"]}afterword/')
    for name in STATIC:
        if not (ROOT / name).is_file():
            fail(f"{name} is missing")
    sources = [*STATIC, *(["afterword.toml"] if sets else []), *(f'papers/{p["slug"]}/paper.toml' for p in papers), *frozen, *editions]
    release = sorted({*sources, *out, *copies})
    for f in sorted((ROOT / "papers").rglob("*")):
        name = str(f.relative_to(ROOT))
        if f.is_file() and f.name != ".DS_Store" and name not in release:
            fail(f"{name} is not part of any paper. Move it out of papers/ or declare it in paper.toml")
    for url in re.findall(r'url\("?([^")]+)"?\)', (ROOT / "style.css").read_text()):
        if not url.startswith(("http:", "https:", "data:")) and url not in release:
            fail(f"style.css loads {url}, which is not published")
    texts = {**out, **{f'papers/{p["slug"]}/{v["tex"]}': v["source"] for p in papers for v in p["versions"]}}
    if broken := broken_links(texts, release):
        fail("links that point nowhere:\n  " + "\n  ".join(broken))
    # Every check has passed; only now is anything written.
    for copy, latest in copies.items():
        if not (ROOT / copy).is_file() or (ROOT / copy).read_bytes() != (ROOT / latest).read_bytes():
            (ROOT / copy).write_bytes((ROOT / latest).read_bytes())
            print("wrote", copy, file=sys.stderr)
    for name, text in out.items():
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text() != text:
            path.write_text(text)
            print("wrote", name, file=sys.stderr)
    if "--release" in sys.argv:
        print(json.dumps({"release": release, "frozen": {k: str(d) for k, d in frozen.items()}}, indent=1))


if __name__ == "__main__":
    main()
