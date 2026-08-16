# Historic Ink

A touch-first web game: trace the signatures of history over a faded guide, get
scored on accuracy, and unlock progressively harder hands across six collections.

96 signatures — six tracks of sixteen — sourced from Wikidata and Wikimedia
Commons.

## Playing it

Open `docs/index.html`. That's the whole instruction — no server, no build step,
no dependencies. It works by double-click from the filesystem, and equally well
served over HTTP.

The game lives in `docs/` and nothing else does. GitHub Pages will only serve
from the repo root or a folder called `docs/`, so this is the one layout that
publishes the game — and only the game — with no workflow file and no build:
point Pages at *main / docs* and it is live. The corpus pipeline, the grading
harness and this README all stay outside it.

The corpus is baked into `data/signatures.js` as one global assignment rather
than a JSON file, precisely so that `file://` works: `fetch()` is blocked there,
a plain `<script src>` is not. The same reasoning keeps the app on classic
scripts hanging off a `window.SG` namespace instead of ES modules, and puts the
wordmark typeface inline as a data URI rather than a linked font file.

Served over HTTP it also registers a service worker, so it installs to a phone
home screen and runs offline. It deliberately does not register on localhost,
because it would otherwise serve you stale code all afternoon.

The worker is cache-first, so a shipped change only reaches a returning player
when the cache name changes. Both that name and the precached asset list are
generated from the actual contents of `docs/` by `tools/bump_cache.py`, and the
pre-commit hook runs it — doing either by hand had already failed twice, once
shipping a change under an unchanged version and once leaving a new file out of
the offline list.

```sh
git config core.hooksPath tools/hooks    # enable (once per clone)
python tools/bump_cache.py               # or run it by hand
python tools/bump_cache.py --check       # exit 1 if stale, for CI
```

## Layout

```
docs/                     THE GAME — everything Pages serves, and nothing else
  index.html              the app shell; every screen lives here
  css/app.css             ink & parchment theme; all colours are CSS variables
  css/wordmark.css        GENERATED. the wordmark typeface, subset and inlined
  fonts/                  the typeface's licence
  js/geom.js              signature space <-> screen space
  js/ink.js               painting signatures and strokes; masks + distance fields
  js/grade.js             accuracy scoring
  js/pad.js               the drawing surface, and the magnifier
  js/store.js             progress in localStorage
  js/app.js               screens, routing, play loop
  data/signatures.js      GENERATED corpus (~360 KB)
  sw.js                   offline cache — bump CACHE when files change

tools/                    the corpus pipeline (Python, dev-only, never shipped)
dev/grade-test.html       grading harness, with the calibration sweep
```

## The magnifier

A finger covers the very thing it is trying to trace, which is the whole
difficulty of this game on a phone rather than at a desk. While a stroke is in
progress the pad draws a round glass riding just above the fingertip,
magnifying the area underneath it. The player's own line is drawn faded inside
it — the point is to see the guide the finger is hiding, and at full strength
their stroke simply hides it again.

It rides with the finger rather than parking in a corner. A corner loupe has to
switch sides to stay out from under the hand, and that jump turned out to be far
more distracting than the problem it was solving.

It is its own element floating over the page, not something drawn into the pad's
canvas. Inside the canvas it could never leave the card, so anywhere near the top
of a short card there was no room above the finger and it had to drop below —
the same distracting jump by another route, firing across most of the card's
height. Over the page it has the header's worth of room and stays above the hand
always, which is the only place it is any use; if it ever does run out of room it
stops against the top of the viewport rather than flipping.

It only shows up for detail work. Appearing on every stroke makes it scenery, and
a long confident sweep does not need magnifying, so it waits for evidence that the
hand has settled: roughly 850ms spent *moving slowly*, not merely 850ms with a
finger down. Two speed thresholds rather than one give it hysteresis, so a hand
hovering near the cutoff cannot flicker it. A pause counts as careful — the
measured speed decays toward zero when no events arrive, because holding still to
line something up is the most careful thing a hand does.

That clock is a timer, not `requestAnimationFrame`. rAF is a paint clock, and a
device throttling frames would then never decide the hand had settled; timing and
drawing are different questions.

The drawing card deliberately keeps the page's normal gutter. Bleeding it wider
was tried and it put the pad's `touch-action: none` inside the phone's edge-swipe
zone, which broke the system back gesture.

It renders through the same `_scene()` call as the pad itself, so it magnifies
exactly what is on the card rather than a lookalike that could drift out of sync.

## How scoring works

Both the target and the attempt are rasterized to binary ink masks, and each
gets a distance field. That yields three numbers:

- **precision** — how close the player's ink sits to the target's
- **coverage** — how much of the target's ink the player actually reached
- **economy** — whether they got there without drawing far further than the
  signature is long

Precision and coverage combine as a **harmonic mean**, then economy scales the
result.

All three are needed. Precision and coverage alone were exploitable, and badly:
scribbling back and forth across the card scored 80%+. Signatures are mostly
horizontal ink, so a dense zigzag crosses nearly all of it — full marks for
coverage — while staying within tolerance of *something* simply by remaining
inside the signature's own bounding box. Precision is a *mean* over the player's
ink and is therefore blind to how much ink there is: drawing ten times as much at
the same average closeness scores identically. Economy supplies that dimension.

Economy measures pen **travel**, not inked area. Area was tried first and was
wrong — a wavering hand covers more area without drawing any further, so it
punished shaky tracing, which precision already accounts for. The penalty grows
with the *square* of the excess past a grace of 1.6×, so going back over the
whole signature a second time costs a few points while a scribble collapses.

Precision also falls off on a power curve rather than linearly, and strokes that
spend most of their length away from the signature are charged for individually.
That threshold is measured rather than guessed: across the corpus an honestly
traced stroke never averages worse than 0.25 off, while the strokes of a scribble
sit at 0.4–0.7.

Comparing pixels rather than paths makes stroke order, stroke direction and
pen-lift count irrelevant — which matters, because almost every Commons file
stores a traced *outline* of the ink rather than the original pen path.

### Pass marks

`pass_mark` in `tools/build_corpus.py` is calibrated against the scorer rather
than guessed, by running every level against a realistic traced attempt and
against the ways people try to cheat a tracing game. The two populations
separate cleanly, and the thresholds sit in the band between them, 66–76.

**If you change the scoring constants, recalibrate.** Serve the directory, open
`dev/grade-test.html`, and press **Run full sweep**: it puts every level through
five honest attempts and ten cheats and reports the two numbers that matter —
*best cheat anywhere* and *worst honest attempt*. Those must not cross.
`SG.grade.tune({...})` from the console changes the constants live, so a
parameter search does not mean editing files between runs.

As it stands: **960 cheat attempts, none of them pass**, the best reaching 65%
against a 72% bar. Clean, steady, doubled-back and edge-traced attempts clear
every level. A deliberately shaky attempt — wandering about two pen widths off
the line — fails on about a fifth of them, which is a difficulty judgement rather
than a correctness one; `pass_mark` is the single line to change if that feels
wrong on a real phone.

Two things that look like exploits and are not, both recorded so nobody re-adds
them to the cheat list: tracing only a third of the *strokes* passes, because the
corpus orders contours largest first and a third of them is 80–95% of the pen
travel; and a scribble generator that emits a point per stored point produces
wildly different attacks per signature, which made an honest wobble look like a
cheat on Warhol.

## Difficulty

Each signature is scored on six shape metrics plus **fineness**, all ranked as
percentiles across the corpus and combined. Fineness — how narrow the pen is —
carries the second-largest weight, because difficulty is not only about shape:
van Gogh's broad, blunt hand is forgiving no matter how it loops, while Curie's
hairline punishes a millimetre. Judging on shape alone put fat, showy signatures
above fine, plain ones that are markedly harder to trace, and the tracks did not
feel like they ramped.

Tracks are then ordered by that score, so each collection runs easiest to
hardest, and every track is trimmed to the same even length.

## Rebuilding the corpus

Requires Python 3 and network access. Standard library only — nothing to install.

```sh
python tools/build_corpus.py -v      # writes docs/data/signatures.js + tools/preview.html
python tools/contact_sheet.py        # writes tools/sheet_<theme>.png for eyeballing
```

The pipeline asks Wikidata for deceased public figures who have an SVG signature
(`P109`) and at least 40 Wikipedia sitelinks, sorts them into six themes by
occupation, downloads the artwork from Commons, flattens it to polylines, scores
each signature's complexity, and keeps the most famous per theme ordered by
difficulty. Fame picks the names; difficulty sets the ramp. Each level also
carries a link to the subject's English Wikipedia article.

Everything network-touching is cached under `tools/.cache`, so re-runs are fast
and offline. Difficulty, vectorization and pen widths are all precomputed here —
the browser does no parsing or geometry prep at load.

### Curation

Sitelinks measure how famous the *person* is, which is not how famous their
*hand* is. `PRIORITY` in `tools/sources.py` pulls a list of signatures to the
front of their track's queue regardless of ranking — John Hancock's is the most
recognisable signature in the English-speaking world and his 47 sitelinks had him
nowhere near the cut.

`THEME_OVERRIDES` pins people the occupation scoring files oddly. Wikidata's
"writer" is a catch-all that lands on anyone who published anything, so it drags
explorers and presidents into the letters track — Amelia Earhart was filed under
Writers & Philosophers. Entries are keyed by label, and the build reports any
that matched nobody, so a renamed label surfaces instead of silently doing
nothing.

`EXCLUDE_NAMES` drops people entirely: a group of Nazi figures, who would be grim
company in a lighthearted game whatever their historical weight, and athletes who
score into Stage & Screen off film cameos. That is an editorial call, not a
technical one.

### Checking a rebuild

Look at `tools/sheet_*.png`. Commons SVG quality varies a lot, and the automated
checks are deliberately loose, because they are far more costly when they discard
a good signature than when they let a mediocre one through. Known failure modes,
each with its own check:

- **hollow outline art** — the file draws the *outline* of a signature, so every
  stroke is two hairlines and filling it gives wiry rings
- **blobby traces** — crude tracing collapses letterforms into filled masses.
  Caught by the largest disc that fits inside the ink: a pen of any width draws
  a ribbon, so that disc is about half the nib, while a collapsed bowl swallows
  one many times larger. Neither stroke width nor contour roundness works here —
  both rank honest broad hands like Rembrandt's above the bad traces.

If something converted badly anyway, add its Commons filename to `BLACKLIST` in
`tools/sources.py` and rebuild; the next-most-famous candidate takes its place.

## The wordmark

Pinyon Script, a 19th-century copperplate — the actual hand of the documents
these signatures came off, which no system font gets near. `tools/build_font.py`
subsets it to the characters the wordmark needs and inlines it as base64 in
`docs/css/wordmark.css`, 19 KB, so it survives `file://` where a linked font
counts as cross-origin and is refused.

```sh
python -m pip install fonttools brotli
python tools/build_font.py
```

SIL Open Font License 1.1 — the licence travels with it in
`docs/fonts/OFL-PinyonScript.txt`.

## Licensing

Signature artwork comes from Wikimedia Commons, overwhelmingly public domain with
a few CC0 and CC BY-SA files. Per-file licence and author are carried through into
the corpus and listed on the app's About screen, alongside a Wikipedia link for
each person.
