#!/usr/bin/env python3
"""Generates the site from each paper's facts. Run `python3 build.py` after any change under papers/ or afterword.toml.

Sources, per paper in papers/<slug>/:
  paper.toml          title, authors, who posted it, status, summary, the versions and the Afterword editions
                      (each with its date, the version it explains, its method set and an optional one-sentence
                      summary used as the page's description when it is shared)
  v<n>/<slug>.pdf     version n, with v<n>/<slug>.tex and v<n>/abstract.html (its abstract, the mathematics in
                      MathML and the words those of the TeX), never changed after the day it is posted. <slug>.pdf beside them is
                      written by this script as a copy of the latest version, so that address always serves the latest
  afterword/<n>.html  Afterword edition n, never changed after the day it is posted
Output: index.html, feed.xml, and for each paper papers/<slug>/index.html (the latest version),
papers/<slug>/v<n>/index.html and papers/<slug>/afterword/[<n>/]index.html.
"""

import html
import os
import re
import tomllib
import unicodedata
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = "https://jacobpescador.github.io/ai-math"
REPO = "https://github.com/jacobpescador/ai-math"
NAME = "AI Math"
TAGLINE = "Mathematics found and written by AI systems. Every version of every paper stays up at its own address."
CC0 = '<a href="https://creativecommons.org/publicdomain/zero/1.0/">CC0 1.0</a>'
DEDICATION = f"To the extent any copyright exists, dedicated to the public domain ({CC0})"

e = html.escape


def long_date(d):
    return f"{d.day} {d:%B %Y}"


def short_date(d):
    return f"{d.day} {d:%b %Y}"


def and_list(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def byline(paper):
    posted = f' · posted and maintained by {paper["posted_by"]}' if paper.get("posted_by") else ""
    return and_list(paper["authors"]) + posted


def note(entry):
    """What changed in a version or edition; the first has nothing to say."""
    return f'<div class="note">{e(entry["note"])}</div>' if entry.get("note") else ""


def bibtex_text(text):
    """Text as BibTeX reads it: TeX's special characters escaped, dashes as TeX writes them."""
    special = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "#": r"\#",
               "_": r"\_", "$": r"\$", "^": r"\^{}", "~": r"\~{}"}
    return "".join(special.get(c, c) for c in text).replace("–", "--").replace("—", "---")


FORMULA = "\0"
BLOCKS = {"p", "br", "div", "li", "ul", "ol", "blockquote"}


def words(text):
    """Text as the abstract check compares it: its words and inline formulas, accents and punctuation aside."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return " ".join(re.findall(rf"{FORMULA}|[^\W_]+", plain)).replace(FORMULA, "$")


class Prose(HTMLParser):
    """The words of a fragment of HTML, each inline formula as one mark and displayed formulas left out."""
    def __init__(self, text):
        super().__init__()
        self.text, self.depth = "", 0
        self.feed(text)
        self.text = words(self.text)

    def handle_starttag(self, tag, attrs):
        if tag == "math":
            if self.depth == 0:
                self.text += " " if ("display", "block") in attrs else FORMULA
            self.depth += 1
        elif tag in BLOCKS:
            self.text += " "

    def handle_endtag(self, tag):
        if tag == "math":
            self.depth -= 1
        elif tag in BLOCKS:
            self.text += " "

    def handle_data(self, data):
        if self.depth == 0:
            self.text += data


def tex_abstract(tex):
    """The words of a TeX source's abstract, in the form Prose gives the site's, or None if it has none."""
    tex = re.sub(r"(?<!\\)%.*\n?[ \t]*", "", tex)
    if r"\begin{abstract}" not in tex:
        return None
    body = tex.split(r"\begin{abstract}")[1].split(r"\end{abstract}")[0].replace(r"\$", "")
    body = re.sub(r"\\\[.*?\\\]|\$\$.*?\$\$|\\begin\{(equation|align|gather|multline|eqnarray)(\*?)\}.*?\\end\{\1\2\}",
                  " ", body, flags=re.S)
    body = re.sub(r"\$[^$]*\$|\\\(.*?\\\)", FORMULA, body, flags=re.S)
    body = re.sub(r"\\(?:cite[a-z]*|ref|eqref|label)\*?(?:\[[^]]*\])*\{[^}]*\}|\\href\{[^}]*\}", " ", body)
    body = re.sub(r"\\['\"`^~=.-]|\\[a-zA-Z]+\*?", "", body)
    body = re.sub(r"\\[^a-zA-Z]", " ", body).replace("{", "").replace("}", "")
    return words(body)


class Links(HTMLParser):
    """Every href and src in a fragment of HTML, however it is quoted."""
    def __init__(self, text):
        super().__init__()
        self.found = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.found += [value for name, value in attrs if name in ("href", "src") and value is not None]


def initials(name):
    """"Jacob Pescador" as a citation gives it: "J. Pescador"."""
    *given, family = name.split()
    return " ".join(f"{g[0]}." for g in given) + "\u00a0" + family


def fail(message):
    raise SystemExit(f"build.py: {message}")


def load_sets():
    source = ROOT / "afterword.toml"
    sets = tomllib.loads(source.read_text())["sets"] if source.is_file() else {}
    return {int(n): s for n, s in sets.items()}


def load_paper(directory, sets):
    slug = directory.name
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", slug):
        fail(f"{slug}: a paper's folder name must be lowercase letters, digits and hyphens")
    paper = tomllib.loads((directory / "paper.toml").read_text())
    paper["slug"] = slug
    paper["url"] = f"{SITE}/papers/{slug}/"
    for n, version in enumerate(paper["versions"], 1):
        version["n"] = n
        version["pdf"] = f"v{n}/{slug}.pdf"
        version["tex"] = f"v{n}/{slug}.tex"
        version["abstract_file"] = f"v{n}/abstract.html"
        version["page"] = f"v{n}/"
        version["commit"] = f"{REPO}/commits/main/papers/{slug}/v{n}"
        for file in (version["pdf"], version["tex"], version["abstract_file"]):
            if not (directory / file).is_file():
                fail(f"{slug}: v{n} lacks {file}")
        version["abstract"] = (directory / version["abstract_file"]).read_text().strip()
        if any(not link.startswith(("https://", "http://", "#")) for link in Links(version["abstract"]).found):
            fail(f'{slug}: {version["abstract_file"]} has a relative link. It is shown at two addresses, so links must be absolute')
        site, source = Prose(version["abstract"]).text, tex_abstract((directory / version["tex"]).read_text())
        if source is None:
            fail(f'{slug}: {version["tex"]} has no abstract')
        if site != source:
            at = len(os.path.commonprefix([site, source]))
            fail(f'{slug}: {version["abstract_file"]} differs from the abstract of {version["tex"]}:\n'
                 f'  site: …{site[max(at - 40, 0):at + 40]}…\n  TeX:  …{source[max(at - 40, 0):at + 40]}…')
    paper["latest"] = paper["versions"][-1]
    paper["pdf"] = f"{slug}.pdf"
    for n, edition in enumerate(paper.get("afterword", []), 1):
        edition["n"] = n
        edition["commit"] = f"{REPO}/commits/main/papers/{slug}/afterword/{n}.html"
        edition["method"] = sets.get(edition["set"]) or fail(f"{slug}: Afterword {n} names no set in afterword.toml")
        if not 1 <= edition["explains"] <= len(paper["versions"]):
            fail(f"{slug}: Afterword {n} explains a version that does not exist")
        source = directory / "afterword" / f"{n}.html"
        if not source.is_file():
            fail(f"{slug}: Afterword {n} lacks afterword/{n}.html")
        if not isinstance(edition.get("summary", ""), str):
            fail(f"{slug}: Afterword {n} has a summary that is not text")
        edition["body"] = source.read_text().strip()
        if any(not link.startswith(("https://", "http://", "#")) for link in Links(edition["body"]).found):
            fail(f"{slug}: Afterword {n} has a relative link. It is served at two addresses, so links must be absolute")
    paper["afterwords"] = paper.pop("afterword", [])
    return paper


def published_sources(paper):
    """The files of a paper's folder that are published as they are: its facts, abstract, versions and editions."""
    versions = [f for v in paper["versions"] for f in (v["pdf"], v["tex"], v["abstract_file"])]
    editions = [f'afterword/{a["n"]}.html' for a in paper["afterwords"]]
    return ["paper.toml", paper["pdf"], *versions, *editions]


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
<p class="meta">{e(byline(p))} · v{v["n"]}, {long_date(v["date"])} · {e(p["status"])} · {" · ".join(links)}</p>""")
    body = f"""<h1>{NAME}</h1>
<p>{TAGLINE}</p>

{chr(10).join(entries)}

<footer>{DEDICATION} · <a href="{REPO}">source and full history</a></footer>"""
    head = "".join(f'<meta property="{k}" content="{e(c)}">\n' for k, c in [
        ("og:type", "website"), ("og:site_name", NAME), ("og:title", NAME), ("og:description", TAGLINE),
        ("og:url", f"{SITE}/"),
    ])
    return page(NAME, "", f"{SITE}/", TAGLINE, body, head)


def cite(p, v, url):
    """The Cite section for version v of paper p, at the address url."""
    posted = f' posted by {initials(p["posted_by"]).replace(chr(160), "~")},' if p.get("posted_by") else ""
    poster = f' (posted by {initials(p["posted_by"])})' if p.get("posted_by") else ""
    citation = (f'{e(and_list(p["authors"]))}{e(poster)}, “{e(p["title"])},” preprint, version {v["n"]}, '
                f'{long_date(v["date"])}, <a href="{url}">{url}</a>.')
    bibtex = f"""@misc{{aimath-{p["slug"]},
  author       = {{{" and ".join("{" + a + "}" for a in p["authors"])}}},
  title        = {{{{{bibtex_text(p["title"])}}}}},
  howpublished = {{Preprint,{posted} \\url{{{url}}}}},
  note         = {{Version {v["n"]}, {long_date(v["date"])}}},
  year         = {{{p["versions"][0]["date"].year}}}
}}"""
    return f"""<h2>Cite</h2>
<p>{citation}</p>
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
    return "\n".join(f"""  <li>{label(x)} · {long_date(x["date"])} · <a href="{root}{x["pdf"]}">PDF</a> · <a href="{root}{x["tex"]}">TeX</a> ·
    <a href="{x["commit"]}">history</a>
    {note(x)}</li>""" for x in reversed(p["versions"]))


def paper_page(p, v, root):
    """The page of paper p showing version v: the paper's own page for the latest (root ""), and v<n>/ for every
    version (root "../"). Only the version's own facts differ: its date and files, its abstract, its citation, and
    which version is bold in the list."""
    first, url = p["versions"][0], p["url"] + (root and v["page"])
    # Scholar's tags go on the paper's own page only, so the versions are not indexed as separate works.
    head = "" if root else "".join(f'<meta name="{k}" content="{e(c)}">\n' for k, c in [
        ("citation_title", p["title"]),
        *[("citation_author", a) for a in p["authors"]],
        ("citation_publication_date", f"{first['date']:%Y/%m/%d}"),
        ("citation_abstract_html_url", p["url"]),
        ("citation_pdf_url", p["url"] + p["pdf"]),
    ])
    head += "".join(f'<meta property="{k}" content="{e(c)}">\n' for k, c in [
        ("og:type", "article"), ("og:site_name", NAME), ("og:title", p["title"]), ("og:description", p["summary"]),
        ("og:url", url),
    ])
    pdf = root + v["pdf"] if root else p["pdf"]
    links = [f'<a href="{pdf}">PDF</a>', f'<a href="{root}{v["tex"]}">TeX source</a>']
    if p["afterwords"]:
        links.append(f'<a href="{root}afterword/">Afterword</a>')
    body = f"""<p class="meta"><a href="../../{root}">{NAME}</a></p>
<h1>{e(p["title"])}</h1>
<p class="meta">{e(byline(p))} · v{v["n"]}, {long_date(v["date"])} · {e(p["status"])} ·
  {" · ".join(links)}</p>

{v["abstract"]}

<h2>Versions</h2>
<ul class="versions">
{version_list(p, v, root)}
</ul>

{cite(p, v, url)}

{footer(f'{REPO}/commits/main/papers/{p["slug"]}')}"""
    return page(p["title"], f"../../{root}", url, p["summary"], body, head)


def afterword_page(p, edition, root, canonical):
    """Afterword edition `edition` of p: at afterword/ for the latest, and at afterword/<n>/ for every edition, as the
    paper has its own page and a page for each version."""
    method = edition["method"]
    last = p["afterwords"][-1]
    explains = p["versions"][edition["explains"] - 1]
    context = f'\n<p>{e(edition["context"])}</p>' if edition.get("context") else ""
    def label(x):
        if x is edition:
            return f'<strong>Edition {x["n"]}</strong>'
        page = "" if x is last else f'{x["n"]}/'
        return f'<a href="{root}papers/{p["slug"]}/afterword/{page}">Edition {x["n"]}</a>'
    editions = "\n".join(f"""  <li>{label(x)} · {long_date(x["date"])} · on v{x["explains"]} ·
    <a href="{x["commit"]}">history</a>
    {note(x)}</li>""" for x in reversed(p["afterwords"]))
    body = f"""<p class="meta"><a href="{root}">{NAME}</a> · <a href="{root}papers/{p["slug"]}/">{e(p["title"])}</a></p>
<h1>Afterword</h1>
<p class="meta">Edition {edition["n"]}, {long_date(edition["date"])} · on v{explains["n"]} of the paper, {long_date(explains["date"])} ·
  <a href="#how">how this was written</a></p>

{edition["body"]}

<h2 id="how">How this was written</h2>
<p>{e(method["how"])}</p>{context}

<h2>Editions</h2>
<ul class="versions">
{editions}
</ul>

{footer(f'{REPO}/commits/main/papers/{p["slug"]}/afterword')}"""
    title = f"Afterword · {p['title']}"
    description = edition.get("summary") or f"Afterword to “{p['title']}”."
    head = "".join(f'<meta property="{k}" content="{e(c)}">\n' for k, c in [
        ("og:type", "article"), ("og:site_name", NAME), ("og:title", title), ("og:description", description),
        ("og:url", canonical),
    ])
    return page(title, root, canonical, description, body, head)


def feed(papers):
    items = []
    for p in papers:
        for v in p["versions"]:
            url = p["url"] + v["page"]
            items.append((v["date"], (v["n"], 0, 0), f'{p["title"]}, v{v["n"]}', url, url, v.get("note") or p["summary"], p["authors"]))
        for a in p["afterwords"]:
            url = f'{p["url"]}afterword/{a["n"]}/'
            items.append((a["date"], (a["explains"], 1, a["n"]), f'Afterword to {p["title"]}, edition {a["n"]}', url, url,
                          a.get("note") or a.get("summary", ""), p["authors"]))
    items.sort(key=lambda i: (i[0], i[1]), reverse=True)  # newest first; an edition just after the version it explains
    entries = "\n".join(f"""<entry>
  <title>{e(title)}</title>
  <link href="{link}"/>
  <id>{id_}</id>
  <updated>{date}T00:00:00Z</updated>
{"".join(f"  <author><name>{e(a)}</name></author>{chr(10)}" for a in authors)}{f"  <summary>{e(note)}</summary>{chr(10)}" if note else ""}</entry>""" for date, _, title, link, id_, note, authors in items)
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


def main():
    sets = load_sets()
    papers = [load_paper(d, sets) for d in sorted((ROOT / "papers").iterdir()) if (d / "paper.toml").is_file()]
    papers.sort(key=lambda p: (p["latest"]["date"], p["title"]), reverse=True)
    out = {"index.html": index_page(papers), "feed.xml": feed(papers)}
    for p in papers:
        base = f'papers/{p["slug"]}/'
        out[base + "index.html"] = paper_page(p, p["latest"], "")
        for v in p["versions"]:
            out[base + v["page"] + "index.html"] = paper_page(p, v, "../")
        if p["afterwords"]:
            latest = p["afterwords"][-1]
            out[base + "afterword/index.html"] = afterword_page(p, latest, "../../../", f'{p["url"]}afterword/')
            for a in p["afterwords"]:
                canonical = f'{p["url"]}afterword/{a["n"]}/'
                out[base + f'afterword/{a["n"]}/index.html'] = afterword_page(p, a, "../../../../", canonical)
    declared = {*out, *(f'papers/{p["slug"]}/{f}' for p in papers for f in published_sources(p))}
    for f in sorted((ROOT / "papers").rglob("*")):
        name = str(f.relative_to(ROOT))
        if f.is_file() and f.name != ".DS_Store" and name not in declared:
            fail(f"{name} is not part of any paper. Move it to drafts/ or declare it in paper.toml")
    # Every check has passed; only now is anything written.
    for p in papers:
        latest, copy = ROOT / "papers" / p["slug"] / p["latest"]["pdf"], ROOT / "papers" / p["slug"] / p["pdf"]
        if not copy.is_file() or copy.read_bytes() != latest.read_bytes():
            copy.write_bytes(latest.read_bytes())
            print("wrote", copy.relative_to(ROOT))
    for name, text in out.items():
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text() != text:
            path.write_text(text)
            print("wrote", name)


if __name__ == "__main__":
    main()
