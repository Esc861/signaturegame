# Historic Ink

A touch-first web game: trace the signatures of history over a faded guide, get
scored on accuracy, and unlock progressively harder hands across six collections.

96 signatures — six tracks of sixteen — sourced from Wikidata and Wikimedia
Commons.

## Playing it

Open `docs/index.html`. That's the whole instruction — no server, no build step,
no dependencies. It works by double-click from the filesystem, and equally well
served over HTTP.

**It asks for a landscape touchscreen**, and both halves of that are load-bearing
rather than preference. Landscape because every signature in the corpus is wide;
turned upright, one has to shrink to a third of the size to fit the screen's
short edge. Touch because the difficulty is calibrated against a fingertip
covering the very line it is following — which is what the loupe exists for, and
what a mouse pointer does not do. Turning the phone fixes the first, so portrait
is a hard stop. Nothing fixes the second on a laptop, and a dead end is a worse
answer than a warning, so that gate can be waved through (per load, not
remembered).

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

Modelled on what a forensic document examiner actually looks for. The first and
best tell is **line quality**: a genuine signature is written fast and
automatically, so it runs smooth and continuous, while a copy betrays itself with
tremor, hesitation and patching. Where the signature sits on the page is not a
criterion at all — an exact positional match to a known specimen is evidence *of*
tracing — so a steady line slightly off the mark beats a shaky one dead on it.

Both the target and the attempt are rasterized to binary ink masks, and each
gets a distance field. That yields four numbers:

- **fluency** — line quality: how much the stroke shortens when smoothed, which
  a fluent curve barely does and a shaky one does a lot
- **precision** — how close the player's ink sits to the target's
- **coverage** — how much of the target's ink the player actually reached
- **economy** — whether they got there without drawing far further than the
  signature is long

Precision and coverage combine as a **harmonic mean**; fluency and economy scale
the result.

Before any of that, the attempt is nudged onto the target by up to 3.5% of the
diagonal, so a well-formed but transposed trace is not punished for the one thing
nobody judges. Two details make that work rather than backfire: both centres are
measured from the *rendered ink* (the target's stored contour points are a
different quantity, weighted by however densely that file was traced, and using
one against the other dragged correct attempts off the mark), and the nudge is
kept only if it actually scores better — so alignment can help but never hurt.

Fluency is sampled at a fine, fixed spatial scale rather than a multiple of the
pen width. Tremor is high-frequency and genuine curvature is low-frequency; step
coarsely and the smoothing eats the curvature too, which read van Gogh's broad
sweeping hand as though it were shaking.

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

As it stands: **960 cheat attempts, none of them pass**, the best reaching 64%
against a 73% bar. Clean, steady, doubled-back and edge-traced attempts clear
every level. A deliberately shaky attempt — wandering about two pen widths off
the line — fails on 14 of 96, which is a difficulty judgement rather than a
correctness one; `pass_mark` is the single line to change if that feels wrong on
a real phone.

### The drawn line vs. the scored line

The player's ink is drawn at exactly the width the grader inks it at, with one
bounded exception: a short taper as the nib lands and leaves, plus a darker core
down the middle. Both are display only — `paintStrokes` takes the pen dressing as
an argument, `pad.js` passes it and `grade.js` does not. A velocity-tapered nib
along the whole stroke stays off the table for the original reason: the line
being scored would stop being the line on screen, and near-misses would look
unfair. The end taper survives that test because it covers a nib and a half at
each end and makes the drawn line *thinner* than the scored one, so the ink can
never claim credit the score did not give.

Two things that look like exploits and are not, both recorded so nobody re-adds
them to the cheat list: tracing only a third of the *strokes* passes, because the
corpus orders contours largest first and a third of them is 80–95% of the pen
travel; and a scribble generator that emits a point per stored point produces
wildly different attacks per signature, which made an honest wobble look like a
cheat on Warhol.

## Difficulty

Built to reproduce one description, arrived at by playing it: *the easy ones have
thick lines, big obvious curves, and a connected flow for each part of the name.*
Each metric below is one clause of that, ranked as a percentile across the corpus
and combined.

| weight | metric | what it catches |
|---|---|---|
| 0.30 | fineness | how narrow the nib is **relative to the signature's own size** |
| 0.24 | ink ratio | sheer length of line |
| 0.16 | fragments | small disconnected marks, each needing the hand re-sited |
| 0.10 | curl | how tightly the line turns as it travels: fiddly vs sweeping |
| 0.10 | contours | pen lifts and counters |
| 0.05 | turning | overall curliness |
| 0.05 | corners | abrupt direction changes |

Fineness dominates because it is the strongest single predictor: van Gogh's broad
blunt hand forgives a wobble no matter how it loops, while Curie's hairline
punishes a millimetre. Inverse pen width alone very nearly reproduces the whole
ordering.

Fragments are counted at high resolution on purpose. At the resolution used
elsewhere a hairline breaks into hundreds of "pieces" that are raster gaps rather
than pen lifts — Voltaire read as 211 at 340px and 18 at 1000px — which would
have measured thinness a second time under a different name.

### Aspect ratio used to be in this table, and no longer is

It was never a claim about the handwriting. Signatures are normalized to a long
side of 1000, so a wide one fills the card's width while a square one has to
shrink to fit the card's height: Pasteur at 0.96:1 arrived on screen a third the
size of Monet at 8.5:1. A finger's error is fixed in screen pixels while the
score is measured in signature units, so the small ones were quietly much harder
to trace, for a reason that had nothing to do with the pen. Weighting aspect was
paying that bill in difficulty points.

It is now fixed where the problem actually was. The game requires a landscape
screen, `MIN_ASPECT` in `tools/build_corpus.py` refuses anything squarer than
3:1, and `CARD_ASPECT` in `docs/js/app.js` caps the card at the same 3:1 so that
**every signature in the corpus is limited by the card's width and lands at one
identical scale**. Those two constants have to agree; the card-sizing comment
says so, and a card that overflows its row is the symptom if they drift.

The cost is real and worth stating: van Gogh (2.58:1), Napoleon (2.18:1) and
Gandhi (2.27:1) are not in the corpus any more. Recovering them means lowering
`MIN_ASPECT` to roughly 2.1, which filters almost nothing and gives the whole
problem back.

### Where measurement and play disagree

`DIFFICULTY_NUDGE` in `tools/sources.py` adjusts the remainder. Lennon and Hughes
each carry a small one: in both cases the letters are so unpronounced that there
is nothing to aim at, which no shape metric sees. It is kept deliberately short —
it exists for signatures where playing the thing disagrees with measuring it, not
as a place to hand-rank the corpus. If it starts filling up, the metrics are
wrong and should be fixed.

Cervantes was dropped from the corpus rather than nudged. His artwork is a dense
document rubric — several lines of compact writing plus a flourish — not a
signature the way the others are.

Tracks are then ordered by that score, so each collection runs easiest to
hardest, and every track is trimmed to the same even length.

## Rebuilding the corpus

Requires Python 3 and network access. Standard library only — nothing to install.

```sh
python tools/build_corpus.py -v      # writes docs/data/signatures.js + tools/preview.html
python tools/contact_sheet.py        # writes tools/sheet_<theme>.png for eyeballing
```

The pipeline asks Wikidata for deceased public figures who have an SVG signature
(`P109`) and at least 25 Wikipedia sitelinks, sorts them into six themes by
occupation, downloads the artwork from Commons, flattens it to polylines, scores
each signature's complexity, and keeps the most famous per theme ordered by
difficulty. Fame picks the names; difficulty sets the ramp. Each level also
carries a link to the subject's English Wikipedia article.

That fame floor is low, and set by one track. There are twenty politicians with a
signature on file for every mountaineer, so when the wide-only rule cut Explorers
& Aviators below a full sixteen, the fix was to source deeper rather than to
shorten every track to match. Only the tracks that need the tail ever reach it —
candidates are taken in descending fame order and the others fill up long before.

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
