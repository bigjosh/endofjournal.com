# endofjournal.com

A reader for Adam's handwritten journal from India, made from flatbed scans of every page.

## Adding pages

1. Scan spreads into `scans/` (600 dpi TIFF, journal sideways, as before).
2. Cut out, straighten and split them into pages:
   `python tools/process_scans.py`
   It only processes new or changed scans, and prints a warning if a page was clipped.
3. Add the new pages to `content/transcript.json`: one entry per page, with the
   circled page number, any date headings, the typed text, and the same words split
   into the handwritten lines (`lines`, a tab at the start of an indented line),
   which the typed view follows line for line. The page number is
   what puts a spread in its place, so a missed spread scanned later still lands
   in the right spot. A later scan of the same page replaces the earlier one.
4. Rebuild the website:
   `python tools/build_site.py`
   It lists any page numbers that have not been scanned yet.

`content/journal.json` holds the subtitle and the route (the page on which Adam
reaches each place), which the contents and the bottom rail use.

## Looking at it

`python -m http.server 8417 --directory site`, then open http://localhost:8417.

## Publishing

`site/` is the whole website: plain HTML, CSS, JavaScript and images, no build
service needed. Upload that folder to any static host (Cloudflare Pages, Netlify,
GitHub Pages) and point endofjournal.com at it.

`scans/` and `processed/` stay out of git (see `.gitignore`); keep the TIFFs backed up
somewhere, since everything else can be rebuilt from them.
