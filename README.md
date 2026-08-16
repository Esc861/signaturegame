# The Forger's Archive

A touch-first web game: trace the signatures of history over a faded guide, get
scored on accuracy, and unlock progressively harder hands across six collections.

72 signatures, sourced from Wikidata and Wikimedia Commons.

## Playing it

Open `index.html`. That's the whole instruction — no server, no build step, no
dependencies. It works by double-click from the filesystem, and equally well
served over HTTP.

The corpus is baked into `data/signatures.js` as one global assignment rather
than a JSON file, precisely so that `file://` works: `fetch()` is blocked there,
a plain `<script src>` is not. The same reasoning keeps the app on classic
scripts hanging off a `window.SG` namespace instead of ES modules, which
browsers refuse to load over `file://`.

Served over HTTP it also registers a service worker, so it installs to a phone
home screen and runs offline.

## Layout

```
index.html                the app shell — every screen lives here
css/app.css               ink & parchment theme; all colours are CSS variables
js/geom.js                signature space <-> screen space
js/ink.js                 painting signatures and strokes; masks + distance fields
js/grade.js               accuracy scoring
js/pad.js                 the drawing surface
js/store.js               progress in localStorage
js/app.js                 screens, routing, play loop
data/signatures.js        GENERATED corpus (~270 KB)
sw.js                     offline cache — bump CACHE when files change
tools/                    the corpus pipeline (Python, dev-only, never shipped)
dev/grade-test.html       grader harness with a self-running check suite
```

## How scoring works

Both the target and the attempt are rasterized to binary ink masks, and each
gets a distance field. That yields two numbers:

- **precision** — how close the player's ink sits to the target's
- **coverage** — how much of the target's ink the player actually reached

The score is their **harmonic mean**. That is what makes it hard to cheat:
scribbling over the whole card destroys precision, and carefully tracing one
flourish while skipping the rest destroys coverage.

Comparing pixels rather than paths also makes stroke order, stroke direction and
pen-lift count irrelevant — which matters, because almost every Commons file
stores a traced *outline* of the ink rather than the original pen path.

Each level ships a measured pen width, and the grader inks the player's line to
match, so precision and coverage stay symmetric on both fine and fat hands.

## Rebuilding the corpus

Requires Python 3 and network access. Standard library only — nothing to install.

```sh
python tools/build_corpus.py -v      # writes data/signatures.js + tools/preview.html
python tools/contact_sheet.py        # writes tools/sheet_<theme>.png for eyeballing
```

The pipeline asks Wikidata for deceased public figures who have an SVG signature
(`P109`) and at least 40 Wikipedia sitelinks, sorts them into six themes by
occupation, downloads the artwork from Commons, flattens it to polylines, scores
each signature's complexity, and keeps the twelve most famous per theme ordered
by difficulty. Fame picks the names; difficulty sets the ramp.

Everything network-touching is cached under `tools/.cache`, so re-runs are fast
and offline. Difficulty, vectorization and pen widths are all precomputed here —
the browser does no parsing or geometry prep at load.

### Checking a rebuild

Look at `tools/sheet_*.png`. Commons SVG quality varies a lot and the automated
checks are deliberately loose, because they are far more costly when they
discard a good signature than when they let a mediocre one through. If something
converted badly, add its Commons filename to `BLACKLIST` in `tools/sources.py`
and rebuild; the next-most-famous candidate takes its place.

`THEME_OVERRIDES` in the same file pins the handful of polymaths whose Wikidata
occupations send them somewhere a player would find odd — Goethe carries a dozen
naturalist occupations and otherwise files under Scientists.

`EXCLUDE_NAMES` drops people entirely. It currently holds a group of Nazi
figures, who would be grim company in a lighthearted game whatever their
historical weight, and three athletes who score into Stage & Screen off film
cameos. That is an editorial call, not a technical one, and it is one line to
reverse.

## Checking the grader

Serve the directory and open `dev/grade-test.html` (the harness needs HTTP
because it loads across directories). It runs a synthetic suite on load —
tracing, increasing wobble, a shift, a partial attempt, a scribble, an empty
card — and asserts the score moves the right way in each case. It also gives you
a pad to draw on and score by hand.

## Licensing

Signature artwork comes from Wikimedia Commons: currently 66 public domain,
4 CC0, and 2 CC BY-SA. Per-file licence and author are carried through into the
corpus and listed on the app's About screen.
