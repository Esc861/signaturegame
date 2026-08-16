"""Regenerate the service worker's cache name and asset list from docs/.

    python tools/bump_cache.py

The cache name is a hash of every file the game ships, so it changes exactly
when the game changes -- no more and no less. The asset list is the same set of
files, so a new file is offline-cached without anyone remembering to add it.

Both used to be maintained by hand, and both had already gone wrong: a shipped
change went out under an unchanged cache name, and css/wordmark.css was added to
the app before it was added to the list.

Idempotent, and prints nothing unless something moved, so it is safe to run from
a pre-commit hook (see tools/hooks/pre-commit).
"""

import argparse
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "docs")
SW = os.path.join(APP, "sw.js")

PREFIX = "historic-ink-"

# The worker itself is fetched and versioned by the browser, not by us. CNAME is
# GitHub Pages configuration, served but never requested by the app.
SKIP_NAMES = {"sw.js", "CNAME", ".DS_Store"}

BLOCK = re.compile(
    r"(/\* GENERATED-BEGIN tools/bump_cache\.py \*/\n).*?(/\* GENERATED-END \*/)",
    re.S)


def shipped_files():
    """Every file under docs/, as posix-relative paths, sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(APP):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name in SKIP_NAMES or name.startswith("."):
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, APP).replace(os.sep, "/")
            out.append((rel, full))
    return out


def fingerprint(files):
    """Hash of the paths and their contents together.

    Paths are included so that renaming a file changes the fingerprint even if
    no byte of content does.
    """
    h = hashlib.sha256()
    for rel, full in files:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with open(full, "rb") as f:
            h.update(f.read())
        h.update(b"\0")
    return h.hexdigest()[:10]


def render(files, digest):
    listing = ",\n".join("  './%s'" % rel for rel, _ in files)
    return ("var CACHE = '%s%s';\n\n"
            "var ASSETS = [\n  './',\n%s\n];\n" % (PREFIX, digest, listing))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the worker is out of date, without writing")
    args = ap.parse_args()

    with open(SW, "r", encoding="utf-8") as f:
        text = f.read()

    m = BLOCK.search(text)
    if not m:
        print("bump_cache: no GENERATED block in docs/sw.js")
        return 2

    files = shipped_files()
    digest = fingerprint(files)
    updated = BLOCK.sub(
        lambda _: m.group(1) + render(files, digest) + m.group(2), text, count=1)

    if updated == text:
        return 0

    was = re.search(r"var CACHE = '([^']*)'", text)
    if args.check:
        print("bump_cache: docs/sw.js is stale (%s -> %s%s)"
              % (was.group(1) if was else "?", PREFIX, digest))
        return 1

    with open(SW, "w", encoding="utf-8", newline="\n") as f:
        f.write(updated)
    print("bump_cache: %s -> %s%s  (%d files)"
          % (was.group(1) if was else "?", PREFIX, digest, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
