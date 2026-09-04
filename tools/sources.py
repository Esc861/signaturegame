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
# The floor sits at 5 rather than somewhere more selective because of the
# explorers: it is much the smallest pool - there are twenty politicians with a
# signature on file for every mountaineer - and requiring every signature to be
# wide cuts it roughly in half again. Counted end to end, every explorer and
# aviator on Wikidata with a wide SVG signature and a date of death comes to
# about 45 people; a track of 36 needs essentially all of them, and that is what
# sets this number. It is measured, not chosen: at a floor of 25 the track can
# only fill 28 levels, and no other track is anywhere near its own limit.
#
# The tail is only ever reached by the tracks that need it, since candidates are
# taken in descending fame order and the others fill up long before. Writers
# stop around 130 sitelinks, statesmen around 130; the explorers are alone down
# here.
_BANDS = [(5, 12), (12, 25), (25, 40), (40, 55), (55, 75), (75, 100),
          (100, 140), (140, 220), (220, 100000)]


def _year(iso):
    if not iso:
        return None
    m = re.match(r"(-?)(\d+)-", iso)
    if not m:
        return None
    y = int(m.group(2))
    return -y if m.group(1) else y


def _name_from_article(url):
    """The English Wikipedia title, which beats the Wikidata label as a name.

    The label service answers in the first of "en,de,fr,es,it" that has a
    label, and for a few people Wikidata carries no English label at all -
    which had the game showing "Rosa Luxemburgo". Where an English label does
    exist it is still sometimes not the name a reader expects: "Benedictus de
    Spinoza", "Elizabeth I of England", "Franklin Delano Roosevelt". The
    article title is the name Wikipedia settled on after an argument, and every
    person in the pool has one.

    The parenthetical disambiguator goes: "Nadar (photographer)" is a
    filename, not a name.
    """
    title = urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace("_", " ")
    return re.sub(r"\s*\([^)]*\)$", "", title).strip()


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
                rec = people[qid] = {
                    "qid": qid,
                    "name": r.get("pLabel", {}).get("value", qid),
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

    # Names last, once every row for a person has been merged and we know
    # whether they have an article. An entity with neither an article nor a
    # label in any of the service's languages comes back as its own Q-id,
    # which is useless as a level title, so it goes.
    for qid, rec in list(people.items()):
        if rec["wiki"]:
            rec["name"] = _name_from_article(rec["wiki"]) or rec["name"]
        if re.fullmatch(r"Q\d+", rec["name"]):
            del people[qid]
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
        "blurb": "Conquistadors, polar explorers, pilots and astronauts.",
        "roots": ["Q11900058",   # explorer
                  "Q2095549",    # aviator
                  "Q11631",      # astronaut
                  "Q1304013"],   # mountaineer
    },
    {
        "id": "stage",
        "name": "Stage & Screen",
        "blurb": "Actors, directors and singers.",
        "roots": ["Q33999",      # actor
                  "Q2526255",    # film director
                  "Q177220",     # singer
                  "Q245068",     # comedian
                  "Q3282637"],   # film producer
    },
    {
        "id": "music",
        "name": "Composers & Musicians",
        "blurb": "Composers, conductors and the odd rock star.",
        # Split from the painters, who kept the "arts" id. Singers stay with
        # Stage & Screen: a singer is a performer first, and moving them here
        # would empty half of that track into this one.
        "roots": ["Q36834",      # composer
                  "Q639669"],    # musician
    },
    {
        "id": "arts",
        "name": "Painters & Sculptors",
        "blurb": "Signatures that were part of the work.",
        # Deliberately not rooted at "artist" (Q483501): its subclass tree
        # reaches writers and performers and would swallow two other themes.
        "roots": ["Q1028181",    # painter
                  "Q1281618",    # sculptor
                  "Q33231"],     # photographer
    },
    {
        "id": "thought",
        "name": "Philosophers & Historians",
        "blurb": "The economists and sociologists are in here too.",
        # Listed before the writers, and that ordering is what makes the split
        # work. Nearly every philosopher is also tagged "writer", so with the
        # writers first this track would be emptied into theirs. Social
        # scientist sits here rather than under science: its subtree
        # (economist, sociologist, political scientist) hangs off "scientist"
        # in Wikidata, which was filing Marx as a scientist.
        "roots": ["Q4964182",    # philosopher
                  "Q201788",     # historian
                  "Q2374149"],   # social scientist
    },
    {
        "id": "letters",
        "name": "Writers & Poets",
        "blurb": "The fame came from the writing, not the handwriting.",
        "roots": ["Q36180",      # writer
                  "Q49757",      # poet
                  "Q214917",     # playwright
                  "Q6625963"],   # novelist
    },
    {
        "id": "science",
        "name": "Scientists & Inventors",
        "blurb": "Physicists, doctors, engineers and mathematicians.",
        "roots": ["Q901",        # scientist
                  "Q205375",     # inventor
                  "Q170790",     # mathematician
                  "Q39631",      # physician
                  "Q82594"],     # computer scientist
    },
    {
        "id": "crown",
        "name": "Monarchs & Nobility",
        "blurb": "A royal signature is often just a first name and a title.",
        # Before the statesmen, so that a king who was also a politician reads
        # as a king. Wikidata carries two unrelated "ruler" items and neither
        # subclasses the other, hence both.
        "roots": ["Q116",        # monarch
                  "Q12097",      # ruler
                  "Q1097498",    # ruler (a second, unconnected item)
                  "Q11573099",   # royalty
                  "Q2478141",    # aristocrat
                  "Q5784340"],   # consort
    },
    {
        "id": "statesmen",
        "name": "Statesmen & Revolutionaries",
        "blurb": "Presidents and generals, and the people who pushed back.",
        # The campaigners were tried as a track of their own and it did not
        # work. Splitting them off left a collection defined by opposition
        # rather than by achievement, which turns out to be a category that
        # sweeps in whoever opposed anything: it filed the Unabomber between
        # Rosa Parks and John Brown, and the founder of the Cheka alongside
        # Martin Luther King Jr. It also kept claiming heads of state on the
        # strength of how they came to power - Carter, Nehru, Tito, Ben-Gurion
        # - so the track needed constant hand-correction in both directions.
        # Reformers sit here with the presidents and the generals they spent
        # their lives arguing with, which is where they were to begin with.
        "roots": ["Q82955",      # politician
                  "Q189290",     # military officer
                  "Q193391",     # diplomat
                  "Q3242115",    # revolutionary
                  "Q15253558",   # activist
                  "Q42603"],     # priest
    },
    {
        "id": "builders",
        "name": "Architects & Engineers",
        "blurb": "The signature went on the drawing, not the building.",
        # Last, and the position is the whole design. Architecture and design
        # attach to a great many people who are not architects - Michelangelo,
        # Munch, Manet, Klee, Mondrian and Turner all carry one - and so does
        # engineering, to every politician with a technical degree. Placed
        # last, every tie falls to the other track, and all three tie cases
        # resolve correctly: painter beats architect, politician beats
        # engineer, scientist beats engineer. A genuine architect has no tie
        # to lose, so the track fills with the people it should.
        #
        # Not "draftsperson", which was tried: Wikidata means it in the artist
        # sense, and it brought Dali, Munch and Manet with a wave of
        # cartoonists behind them - Herge, Schulz, Tezuka, Toriyama, Peyo.
        "roots": ["Q42973",      # architect
                  "Q81096",      # engineer
                  "Q5322166"],   # designer
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
    "Q2478141",    # aristocrat -- a fact of birth, not a thing someone did
    "Q11573099",   # royalty -- likewise
}

# Occupations that sit in two subclass trees at once, where the tree that
# claims them first is not the one a player would pick. Applied on top of the
# expanded closures, so they win outright.
#
# Nearly all of these are one of three recurring mistakes, and each one was
# visible in the tracks before it was fixed:
#
#   "autobiographer" and "biographer" hang off historian, so anyone who wrote
#   a memoir landed with the philosophers - Patton, P. T. Barnum, Kissinger
#   and Lewis Carroll were all filed as thinkers on the strength of it.
#
#   the social sciences hang off "scientist", which put Keynes, Adam Smith and
#   Max Weber in among the physicists. Their home was always meant to be the
#   philosophers' track; that is what "social scientist" is doing in its roots.
#
#   "president", "governor" and "First Lady" hang off the ruler tree, which
#   was filing American vice-presidents and presidential wives as nobility.
#   Elective office is not a crown.
OCCUPATION_PINS = {
    "Q18814623": "letters",    # autobiographer
    "Q11774156": "letters",    # memoirist
    "Q864380":   "letters",    # biographer
    "Q18939491": "letters",    # diarist
    "Q4263842":  "letters",    # literary critic
    "Q17167049": "letters",    # literary scholar
    "Q188094":   "thought",    # economist
    "Q1227195":  "thought",    # political economist
    "Q1238570":  "thought",    # political scientist
    "Q2306091":  "thought",    # sociologist
    "Q30461":    "statesmen",  # president
    "Q132050":   "statesmen",  # governor
    "Q203184":   "statesmen",  # First Lady
    "Q1259323":  "statesmen",  # traditional leader or chief
    "Q1414443":  "stage",      # filmmaker
    "Q222344":   "stage",      # cinematographer
    "Q7042855":  "stage",      # film editor
    "Q1208175":  "stage",      # camera operator

    # Clergy. Priest is a Statesmen & Soldiers root, but Wikidata's more
    # specific clerical items hang off "activist" instead, which had three
    # popes filed as Rebels & Reformers.
    "Q1469535":   "statesmen",  # Latin Catholic priest
    "Q250867":    "statesmen",  # Catholic priest
    "Q102039658": "statesmen",  # Latin Catholic bishop
    "Q611644":    "statesmen",  # Catholic bishop
    "Q29182":     "statesmen",  # bishop
    "Q49476":     "statesmen",  # archbishop
    "Q105169902": "statesmen",  # Latin Catholic deacon
    "Q7834465":   "statesmen",  # transitional deacon
    "Q955464":    "statesmen",  # parson
    # ...and the mirror image: these two hang off the philosophers, which had
    # Martin Luther King Jr. filed as a historian.
    "Q16003550":  "statesmen",  # pacifist
    "Q16323111":  "statesmen",  # peace activist
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
    index.update(OCCUPATION_PINS)
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
    "Martin Luther":              "thought",   # scored into arts as a hymnodist
    "Friedrich Nietzsche":        "thought",   # likewise, as a composer
    "Immanuel Kant":              "thought",   # astronomer occupation outweighed him
    "René Descartes":             "thought",   # mathematician, known as a thinker
    "Isaac Newton":               "science",   # "natural philosopher" reads as letters
    "Leonardo da Vinci":          "arts",      # scored into science
    "Mahatma Gandhi":             "statesmen",
    "Thomas Jefferson":           "statesmen", # inventor occupation outweighed him

    # Three American presidents whose day jobs outscored the presidency: Hoover
    # really was a mining engineer and Yeltsin a civil engineer, and Kissinger
    # a political scientist. All true, and all read as the classifier being
    # broken.
    "Herbert Hoover":             "statesmen",
    "Boris Yeltsin":              "statesmen",
    "Henry Kissinger":            "statesmen",
    "Leonid Kravchuk":            "statesmen", # a film-actor credit, like Reagan
    "Paul Revere":                "statesmen", # silversmith: no track claims him
    "P. T. Barnum":               "stage",
    # Nabokov's lepidoptery is not a joke - he named species - but it outscored
    # the novels, which is not how anybody thinks of him.
    "Vladimir Nabokov":           "letters",
    # Wikidata lists exactly two occupations for her, librettist and musician,
    # which is how a nun ended up filed with the poets.
    "Mother Teresa":              "statesmen",
    # Monarchs who wrote music. Both genuinely composed; both read as errors
    # sitting in a track of painters.
    "Henry VIII":                 "crown",

    # Second pass, after the occupation pins above fixed the classes of error
    # and left the individuals. Every one of these is a true fact about the
    # person that reads as a broken classifier on a card.
    "Andy Warhol":                "arts",      # films outscored the paintings
    "Samuel Beckett":             "letters",   # likewise, his films
    # Turned up once the statesmen split in two, and all three read as the
    # classifier being broken rather than as facts about the person.
    "Martin Luther King Jr.":     "statesmen", # filed under the historians
    "Mikhail Gorbachev":          "statesmen", # a film-actor credit, like Reagan
    "Gustaf VI Adolf":            "crown",     # a king, and a real archaeologist

    # Two artists that the architects' track claimed on a design credit. The
    # politicians it also claims are left alone on purpose: Sagasta, Febres
    # Cordero and C. D. Howe were career engineers before they were ministers,
    # and an engineers' track is where they belong.
    "Piet Mondrian":              "arts",
    "Hergé":                      "arts",
    "Yannis Tsarouchis":          "arts",      # a painter, on a design credit
    # And the reverse: three people the painters picked up on a photography or
    # drawing credit, two of them wearing a crown at the time.
    "Alexandra of Denmark":       "crown",
    "Carlos I of Portugal":       "crown",
    "Ryszard Kapuściński":        "letters",   # a reporter who took photographs

    # Two more monarchs whose hobbies outscored their thrones, and who were
    # sitting in a track of painters.
    "Christina, Queen of Sweden": "crown",     # a collector and patron
    "Leopold III of Belgium":     "crown",     # a photographer
    "Huldrych Zwingli":           "thought",   # a reformer who played six instruments

    "Samuel Morse":               "science",   # a celebrated portrait painter first
    "William Herschel":           "science",   # a working composer before an astronomer
    "Arthur Schopenhauer":        "thought",   # filed as a scientist, of all things
    "Florence Nightingale":       "science",   # a statistician, which now means philosophy
    "Salvador Allende":           "statesmen", # a physician
    # Economists who ran countries. Pinning economics to the philosophers is
    # right for Keynes and Smith and wrong for these four.
    "Dag Hammarskjöld":           "statesmen",
    "Carlo Azeglio Ciampi":       "statesmen",
    "Horst Köhler":               "statesmen",
    "Theodor Heuss":              "statesmen",
}

# Signatures too well known to leave to a popularity ranking. Sitelinks measure
# how famous the *person* is, which is not the same as how famous their hand is:
# John Hancock's is the most recognizable signature in the English-speaking
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
    "Audrey Hepburn",
    "Susan B. Anthony",
    "Frederick Douglass",
    "John Adams",
    "Alexander Hamilton",
    "Theodore Roosevelt",
    "Ernest Hemingway",

    # The rest of these are here for the same reason Hancock is, and it is
    # worth saying what that reason is, because it is the most interesting
    # thing this list does. Some signatures are famous *as signatures*, quite
    # apart from the person: because the document they sit on is the artefact,
    # because the hand itself became a trademark, or because so few of them
    # survive that collectors price them by the letter. Sitelinks cannot see
    # any of that. Every name below would have missed its track's cut on fame
    # alone, most of them by a wide margin.
    #
    # Button Gwinnett is the extreme case and the reason this rule exists at
    # all: 19 sitelinks, dead in a duel a year after signing the Declaration,
    # and roughly fifty surviving examples of his hand - which makes his the
    # most valuable autograph in America by a distance nobody else is close to.
    "Button Gwinnett",
    "Josiah Bartlett",       # signed the Declaration directly under Hancock
    "Richard Henry Lee",
    "Paul Revere",
    "John Paul Jones",
    "Marquis de Lafayette",
    # The American frontier, where the signature was often the whole point:
    # Boone's shaky hand and Cody's showman's flourish are both collected far
    # out of proportion to the sitelinks either man carries.
    "Daniel Boone",
    "Davy Crockett",
    "Meriwether Lewis",
    "William Clark",
    "Kit Carson",
    "Wild Bill Hickok",
    "Wyatt Earp",
    "Annie Oakley",
    "Buffalo Bill",
    "Sitting Bull",          # sold his autograph on the Wild West circuit
    # First flight, and the paperwork that proved it.
    "Orville Wright",
    "Wilbur Wright",
    # A hand that became a logo.
    "Beatrix Potter",
    # Iconic in Mexico, and nowhere near the statesmen's cut on sitelinks.
    "Emiliano Zapata",
    "Pancho Villa",
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
#
# Max Weber's file is the type case and worth describing, since the next one
# will look like it: an auto-trace off a poor scan, 4876 points across 45
# contours where the corpus median is 411, so the line wanders by a unit or two
# at every single step. It draws as a plausible signature and passes every
# automated check, but there is no such thing as tracing it well - the sweep had
# a *perfect* trace of it scoring 45%, with line quality at zero, because the
# reference line itself has the tremor the score is looking for. Density like
# that is the tell: he was a four-fold outlier over the next densest signature.
BLACKLIST = {
    "Max Weber's Signature.svg",
    # The other way a file can be unusable: not noise but loss. This one is a
    # single filled path whose subpaths simply do not join up - "Claude Monet"
    # arrives with the connecting strokes missing and the letters in pieces.
    # Nothing in the pipeline drops them; all 20 subpaths survive to the corpus
    # exactly as the file draws them. The source is just poor.
    "Claude Monet Signature.svg",
    # A third way to be unusable, and the subtlest. Nothing is wrong with the
    # artwork: 317 points, below the corpus median, and it draws as a perfectly
    # good hand. But the long underline beneath the name is a stroke that spends
    # its whole length away from the lettering, which is what the stray-stroke
    # charge exists to catch, and it fires on the real signature. The sweep had
    # every honest attempt failing by twelve points or more - clean 64, steady
    # 54, doubled-back 46, edges 61, against a pass mark of 76 - on components
    # that all look healthy in isolation (precision 92, coverage 99, fluency
    # 81). Unclearable in practice, so it goes.
    "André Citroën signature.svg",
}

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
    "Kim Jong Il", "Saddam Hussein",
    # Riefenstahl and Mölders reach the corpus as a director and a pilot, which
    # is true and is not the point; same call as the group above.
    "Leni Riefenstahl", "Werner Mölders",
    "Pelé", "Diego Maradona", "Kobe Bryant",

    # A murdered child, whose hand is famous for the worst reason there is.
    # Nothing to do with her; this game is not the place.
    "Anne Frank",

    # The kings of the Chakri dynasty, and the one princess who reaches the
    # candidate pool. Thailand's lese-majeste law is among the strictest
    # anywhere - three to fifteen years a count, and applied to a Facebook
    # like - and while its reach to dead kings is legally contested, Bhumibol
    # is the reigning king's father and prosecutions touching him have
    # continued since 2016. The law is the smaller half of it. His image is
    # revered to the point that standing on a banknote is an insult, and this
    # game does something a photograph does not: it prints a faded copy of the
    # signature, asks you to draw over it, and scores you out of a hundred. A
    # royal signature is a formal, protected thing in Thailand, nearer a seal
    # than a name, and imitating one is nearer counterfeiting than portraiture.
    #
    # Mongkut died in 1868 and is the mildest case, but The King and I is
    # banned in Thailand for its portrayal of him, so the whole dynasty is the
    # line that is actually possible to state.
    "Bhumibol Adulyadej", "Ananda Mahidol", "Mongkut", "Galyani Vadhana",

    # Surfaced by the ten-track split, which put the first two in company that
    # made the problem obvious: Eva Braun arrived as a photographer and was
    # sitting among the painters, and the Unabomber was filed under Rebels &
    # Reformers between Rosa Parks and John Brown. Dzerzhinsky founded the
    # Cheka and ran the Red Terror, and was in the same track as King and
    # Anthony. Same standard as the group above, applied to three regimes.
    "Eva Braun", "Ted Kaczynski", "Felix Dzerzhinsky",
    # Surfaced by the architects' track, which is a reminder that a technical
    # profession is no guide to a life. Globocnik was an architect and ran
    # Operation Reinhard; Popper was an engineer who hunted the Selk'nam of
    # Tierra del Fuego for sport.
    "Odilo Globocnik", "Julius Popper",
    # A Chakri prince, and an architect. Same call as his father Mongkut.
    "Narisara Nuwattiwong",

    # Not a cultural taboo but a live political one: the Turkish state
    # designates his movement a terrorist organisation and blames him for the
    # 2016 coup attempt, so including him reads as taking a side.
    "Fethullah Gülen",

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
