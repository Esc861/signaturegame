"""Wikidata + Wikimedia Commons fetching, with an on-disk cache.

Standard library only. Everything is cached under tools/.cache so that
re-running the build is cheap and doesn't hammer Wikimedia.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "SignatureGameCorpusBuilder/1.0 (https://github.com/; esc861@gmail.com)"
WDQS = "https://query.wikidata.org/sparql"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/"

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")

_last_request = [0.0]
MIN_INTERVAL = 0.12          # be a polite client


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def _throttle():
    dt = time.time() - _last_request[0]
    if dt < MIN_INTERVAL:
        time.sleep(MIN_INTERVAL - dt)
    _last_request[0] = time.time()


def _get(url, accept=None, tries=5, timeout=180):
    headers = {"User-Agent": UA}
    if accept:
        headers["Accept"] = accept
    last = None
    for attempt in range(tries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            # 404 is a real answer: the file is gone, don't keep asking.
            if e.code in (404, 400):
                raise
            time.sleep(2 ** attempt)
        except Exception as e:
            last = e
            time.sleep(2 ** attempt)
    raise last


def _cache_path(kind, key):
    # md5, not hash(): str hashing is salted per process, so hash() would miss
    # the cache on every fresh run.
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)[:100]
    d = os.path.join(CACHE, kind)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "%s_%s" % (h, safe))


def cached(kind, key, produce):
    """Memoize a text-producing call on disk."""
    p = _cache_path(kind, key)
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    val = produce()
    with open(p, "w", encoding="utf-8") as f:
        f.write(val)
    return val


# --------------------------------------------------------------------------
# Wikidata
# --------------------------------------------------------------------------

def wdqs(query):
    url = WDQS + "?format=json&query=" + urllib.parse.quote(query)
    raw = cached("wdqs", query, lambda: _get(
        url, accept="application/sparql-results+json").decode("utf-8"))
    return json.loads(raw)["results"]["bindings"]


_CANDIDATE_QUERY = """
SELECT ?p ?pLabel ?sig ?sitelinks ?dob ?dod ?occ ?article WHERE {
  ?p wdt:P109 ?sig ;
     wikibase:sitelinks ?sitelinks ;
     wdt:P570 ?dod .
  FILTER(?sitelinks >= %d && ?sitelinks < %d)
  FILTER(STRENDS(STR(?sig), ".svg"))
  OPTIONAL { ?p wdt:P569 ?dob }
  OPTIONAL { ?p wdt:P106 ?occ }
  OPTIONAL {
    ?article schema:about ?p ;
             schema:isPartOf <https://en.wikipedia.org/> .
  }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,de,fr,es,it". }
}
"""

# Sitelink bands, sized so no single query is heavy enough to hit the WDQS
# gateway timeout. One unbounded query reliably 504s.
#
# The floor sits at 25 rather than somewhere more selective because of the
# explorers: it is much the smallest pool - there are twenty politicians with a
# signature on file for every mountaineer - and requiring every signature to be
# wide cut it below a full track. The tail is only ever reached by the tracks
# that need it, since candidates are taken in descending fame order and the
# others fill up long before.
_BANDS = [(25, 40), (40, 55), (55, 75), (75, 100), (100, 140), (140, 220),
          (220, 100000)]


def _year(iso):
    if not iso:
        return None
    m = re.match(r"(-?)(\d+)-", iso)
    if not m:
        return None
    y = int(m.group(2))
    return -y if m.group(1) else y


def fetch_candidates(verbose=True):
    """All deceased, reasonably famous people with an SVG signature.

    Returns {qid: {...}} with occupations aggregated. One row comes back per
    person/occupation pair; we merge them here rather than making WDQS do a
    GROUP_CONCAT, which is markedly less reliable.
    """
    people = {}
    for lo, hi in _BANDS:
        rows = wdqs(_CANDIDATE_QUERY % (lo, hi))
        if verbose:
            print("  sitelinks %-6s %5d rows" % ("%d-%d" % (lo, hi), len(rows)))
        for r in rows:
            qid = r["p"]["value"].rsplit("/", 1)[-1]
            rec = people.get(qid)
            if rec is None:
                name = r.get("pLabel", {}).get("value", qid)
                # An unlabelled entity comes back as its own Q-id; useless as
                # a level title, so drop it.
                if re.fullmatch(r"Q\d+", name):
                    continue
                rec = people[qid] = {
                    "qid": qid,
                    "name": name,
                    "file": urllib.parse.unquote(
                        r["sig"]["value"].rsplit("/", 1)[-1]).replace("_", " "),
                    "sitelinks": int(r["sitelinks"]["value"]),
                    "born": _year(r.get("dob", {}).get("value")),
                    "died": _year(r.get("dod", {}).get("value")),
                    "wiki": r.get("article", {}).get("value", ""),
                    "occupations": set(),
                }
            occ = r.get("occ", {}).get("value")
            if occ:
                rec["occupations"].add(occ.rsplit("/", 1)[-1])
            # The article only appears on some of a person's rows, since the
            # optional joins multiply out against occupations.
            if not rec["wiki"]:
                rec["wiki"] = r.get("article", {}).get("value", "")
    return people


# --------------------------------------------------------------------------
# Commons
# --------------------------------------------------------------------------

def download_svg(title):
    url = FILEPATH + urllib.parse.quote(title.replace(" ", "_"))
    return cached("svg", title, lambda: _get(url).decode("utf-8", "replace"))


_STRIP_TAGS = re.compile(r"<[^>]+>")


def _clean(html):
    if not html:
        return ""
    txt = _STRIP_TAGS.sub(" ", html)
    txt = (txt.replace("&amp;", "&").replace("&quot;", '"')
              .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
              .replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", txt).strip()


def commons_metadata(titles, verbose=True):
    """License + author per file title. Batched 50 at a time (API limit)."""
    out = {}
    titles = list(titles)
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        params = {
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "iiprop": "extmetadata|url",
            "iiextmetadatafilter": "LicenseShortName|Artist|LicenseUrl|AttributionRequired",
            "titles": "|".join("File:" + t for t in batch),
        }
        url = COMMONS_API + "?" + urllib.parse.urlencode(params)
        try:
            raw = cached("meta", url, lambda: _get(url).decode("utf-8"))
            pages = json.loads(raw).get("query", {}).get("pages", {})
        except Exception as e:
            if verbose:
                print("    metadata batch failed: %s" % e)
            continue
        for page in pages.values():
            title = page.get("title", "")[5:]      # strip "File:"
            info = (page.get("imageinfo") or [{}])[0]
            ex = info.get("extmetadata", {}) or {}
            out[title] = {
                "license": _clean(ex.get("LicenseShortName", {}).get("value")) or "unknown",
                "author": _clean(ex.get("Artist", {}).get("value")) or "unknown",
                "url": info.get("descriptionurl")
                       or ("https://commons.wikimedia.org/wiki/File:"
                           + urllib.parse.quote(title.replace(" ", "_"))),
            }
        if verbose:
            print("  metadata %d/%d" % (min(i + 50, len(titles)), len(titles)))
    return out


# --------------------------------------------------------------------------
# themes
# --------------------------------------------------------------------------
#
# Occupations are matched by QID, never by label - labels vary by language and
# get renamed. Rather than enumerate QIDs by hand, each theme names a few root
# occupations and we pull their full subclass tree from Wikidata: "physicist"
# is not literally "scientist", and Heisenberg is filed under "theoretical
# physicist", three levels down. Matching exact QIDs missed people like him and
# Turing entirely.

THEMES = [
    {
        "id": "frontier",
        "name": "Explorers & Aviators",
        "blurb": "Signed from the edges of the map.",
        "roots": ["Q11900058",   # explorer
                  "Q2095549",    # aviator
                  "Q11631",      # astronaut
                  "Q1304013"],   # mountaineer
    },
    {
        "id": "stage",
        "name": "Stage & Screen",
        "blurb": "Autographs practised for the crowd.",
        "roots": ["Q33999",      # actor
                  "Q2526255",    # film director
                  "Q177220",     # singer
                  "Q245068",     # comedian
                  "Q3282637"],   # film producer
    },
    {
        "id": "arts",
        "name": "Artists & Composers",
        "blurb": "Signatures by people who signed for a living.",
        # Deliberately not rooted at "artist" (Q483501): its subclass tree
        # reaches writers and performers and would swallow two other themes.
        "roots": ["Q1028181",    # painter
                  "Q36834",      # composer
                  "Q1281618",    # sculptor
                  "Q42973",      # architect
                  "Q33231",      # photographer
                  "Q639669"],    # musician
    },
    {
        "id": "letters",
        "name": "Writers & Philosophers",
        "blurb": "Hands better known for what they wrote.",
        # Social scientist sits here rather than under science: its subtree
        # (economist, sociologist, political scientist) hangs off "scientist"
        # in Wikidata, which was filing Marx and Gandhi as scientists. Listed
        # before science so the overlapping QIDs resolve this way.
        "roots": ["Q36180",      # writer
                  "Q4964182",    # philosopher
                  "Q201788",     # historian
                  "Q2374149"],   # social scientist
    },
    {
        "id": "science",
        "name": "Scientists & Inventors",
        "blurb": "The hands behind the discoveries.",
        "roots": ["Q901",        # scientist
                  "Q205375",     # inventor
                  "Q170790",     # mathematician
                  "Q39631",      # physician
                  "Q82594",      # computer scientist
                  "Q81096"],     # engineer
    },
    {
        "id": "statesmen",
        "name": "Heads of State & Statesmen",
        "blurb": "The signatures that moved borders.",
        "roots": ["Q82955",      # politician
                  "Q116",        # monarch
                  "Q12097",      # ruler
                  "Q189290",     # military officer
                  "Q193391",     # diplomat
                  "Q3242115",    # revolutionary
                  "Q15253558",   # activist
                  "Q42603"],     # priest
    },
]

# Occupations too generic to say what someone is known for. They still count,
# but only a third as much as a specific one, so that Edison's "inventor"
# outweighs his "film producer" and Mandela's "revolutionary" outweighs the
# "film actor" credit he picked up from a cameo.
GENERIC = {
    "Q36180",      # writer
    "Q82955",      # politician
    "Q483501",     # artist
    "Q639669",     # musician
    "Q901",        # scientist
    "Q81096",      # engineer
    "Q177220",     # singer
    "Q1930187",    # journalist
    "Q40348",      # lawyer
    "Q189290",     # military officer
    "Q43845",      # businessperson
    "Q15253558",   # activist
    "Q42603",      # priest
    "Q1622272",    # university teacher
    "Q37226",      # teacher
    "Q15980158",   # non-fiction writer
    "Q49757",      # poet -- attached to a great many people incidentally
}

_SUBCLASS_QUERY = """
SELECT ?root ?sub WHERE {
  VALUES ?root { %s }
  ?sub wdt:P279* ?root .
}
"""

_expanded = None


def occupation_index(verbose=True):
    """{occupation qid: theme id}, built from each theme's subclass closure.

    Where trees overlap, the theme listed first in THEMES wins the QID; the
    ordering there runs from most specific identity to most generic.
    """
    global _expanded
    if _expanded is not None:
        return _expanded

    roots = []
    for t in THEMES:
        roots.extend(t["roots"])
    rows = wdqs(_SUBCLASS_QUERY % " ".join("wd:" + r for r in roots))

    by_root = {}
    for r in rows:
        root = r["root"]["value"].rsplit("/", 1)[-1]
        sub = r["sub"]["value"].rsplit("/", 1)[-1]
        by_root.setdefault(root, set()).add(sub)

    index = {}
    for theme in THEMES:
        for root in theme["roots"]:
            for qid in by_root.get(root, {root}):
                index.setdefault(qid, theme["id"])
    if verbose:
        print("  occupation index: %d occupations -> %d themes"
              % (len(index), len(THEMES)))
    _expanded = index
    return index


# Automated classification lands ~90% of people sensibly, but a handful of
# polymaths carry so many occupations in one field that scoring sends them
# somewhere a player would find odd. These are the ones famous enough to
# actually reach a track, so they are worth pinning by hand.
#
# Keyed by label rather than Q-id: it is the difference between a line you can
# check by reading it and one you have to look up. build_corpus reports any
# entry here that matched nobody, so a renamed label surfaces instead of
# silently doing nothing.
THEME_OVERRIDES = {
    # "writer" is Wikidata's great catch-all - it lands on anyone who published
    # anything, including memoirs - so it drags explorers and presidents into
    # the letters track.
    "Amelia Earhart":            "frontier",
    "Theodore Roosevelt":        "statesmen",
    # Reagan really does score into Stage & Screen off nearly thirty years of
    # screen credits, but a player reads that as the classifier being broken.
    "Ronald Reagan":             "statesmen",
    "Alexander Hamilton":        "statesmen",
    "Benjamin Franklin":         "science",    # inventor first, to a player
    "Frederick Douglass":        "statesmen",
    # Churchill would belong here too, but Wikidata has no SVG of his hand.

    "Johann Wolfgang von Goethe": "letters",   # a dozen naturalist occupations
    "Martin Luther":              "letters",   # scored into arts as a hymnodist
    "Friedrich Nietzsche":        "letters",   # likewise, as a composer
    "Immanuel Kant":              "letters",   # astronomer occupation outweighed him
    "René Descartes":             "letters",   # mathematician, known as a thinker
    "Isaac Newton":               "science",   # "natural philosopher" reads as letters
    "Leonardo da Vinci":          "arts",      # scored into science
    "Mahatma Gandhi":             "statesmen",
    "Thomas Jefferson":           "statesmen", # inventor occupation outweighed him
}

# Signatures too well known to leave to a popularity ranking. Sitelinks measure
# how famous the *person* is, which is not the same as how famous their hand is:
# John Hancock's is the most recognisable signature in the English-speaking
# world and his 47 sitelinks had him nowhere near the cut. These are pulled to
# the front of their track's queue; they still have to convert cleanly.
PRIORITY = {
    "John Hancock",
    "Amelia Earhart",
    "Benjamin Franklin",
    "Ernest Shackleton",
    "Harry Houdini",
    "Alan Turing",
    "Ada Lovelace",
    "Jane Austen",
    "Edgar Allan Poe",
    "Oscar Wilde",
    "Claude Monet",
    "Audrey Hepburn",
    "Susan B. Anthony",
    "Frederick Douglass",
    "John Adams",
    "Alexander Hamilton",
    "Theodore Roosevelt",
    "Ernest Hemingway",
}

# Difficulty adjustments, in points, for signatures where playing the thing
# disagrees with measuring it. Applied before the tracks are ordered.
#
# The metrics reproduce the stated rule well - thick lines, big obvious curves
# and a connected flow are easy; thin, fragmented and tightly curved are hard -
# but they are geometry, and some difficulty is not geometric.
#
# Lennon and Hughes are here because in both cases the letters are so
# unpronounced that there is nothing to aim at, which no shape metric sees.
# Middling-to-hard, not hardest. These used to be topped up by a weight on how
# wide a signature was; that weight is gone, since the landscape card now shows
# every signature at the same size, and what is left is the part that was really
# about the handwriting.
#
# Kept deliberately short. It exists for signatures where playing the thing
# disagrees with measuring it, not as a place to hand-rank the corpus - if it
# starts filling up, the metrics are wrong and should be fixed instead.
DIFFICULTY_NUDGE = {
    "John Lennon": 14,
    "Howard Hughes": 18,
}

# Commons file titles that convert badly in ways the automated checks in
# build_corpus don't catch -- hollow outline art, cropped scans, artwork that
# is a monogram rather than a signature. The checks there are deliberately
# loose so they never discard a good signature; this is the escape hatch.
# Review tools/sheet_*.png after a build and add offenders here.
BLACKLIST = set()

# Dropped from the corpus entirely. The first group would be grim company in a
# lighthearted game whatever their historical weight; the second are athletes
# who score into Stage & Screen off film cameos and have no track of their own.
# Matched on label, and easily reversed - it is an editorial call, not a
# technical constraint.
EXCLUDE_NAMES = {
    "Adolf Hitler", "Benito Mussolini", "Heinrich Himmler", "Hermann Göring",
    "Reinhard Heydrich", "Martin Bormann", "Joseph Goebbels", "Rudolf Hess",
    "Adolf Eichmann", "Josef Mengele",
    # Reached the corpus once the fame floor came down, and scored into Stage &
    # Screen off his film-producer credits, of all things. Same call as the
    # group above.
    "Kim Jong-il",
    "Pelé", "Diego Maradona", "Kobe Bryant",

    # Not a signature the way the rest are: a dense document rubric, several
    # lines of compact writing plus a flourish. (It is also nowhere near wide
    # enough for the card now, so this line is belt and braces.)
    "Miguel de Cervantes",
}


_override_hits = set()


def assign_theme(occupations, name=None):
    """Best-scoring theme for a set of occupation QIDs, or None.

    Scoring rather than first-match: people carry several occupations and the
    first one alphabetically or by QID says nothing about which they are known
    for. Weight of 3 for a specific occupation, 1 for a generic one.
    """
    if name and name in THEME_OVERRIDES:
        _override_hits.add(name)
        return THEME_OVERRIDES[name]
    index = occupation_index(verbose=False)
    scores = {}
    for occ in occupations:
        theme = index.get(occ)
        if theme:
            scores[theme] = scores.get(theme, 0) + (1 if occ in GENERIC else 3)
    if not scores:
        return None
    order = {t["id"]: i for i, t in enumerate(THEMES)}
    return min(scores, key=lambda t: (-scores[t], order[t]))


def unmatched_overrides():
    """Override entries that matched nobody - a renamed label, most likely."""
    return sorted(set(THEME_OVERRIDES) - _override_hits)
