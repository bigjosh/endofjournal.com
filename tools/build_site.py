"""Build the static reader in site/ from the processed pages and the transcript.

    python tools/build_site.py

Reads   processed/manifest.json      reading order and page geometry
        processed/pages/*.webp       2400 px master page images
        content/transcript.json      typed text, page numbers and dates per page
Writes  site/img/<height>/<page>.webp  the page images at each display size
        site/journal.json              everything the reader needs, in reading order

Run process_scans.py first. site/ is then a complete static website: upload the
folder as it is to any static host.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
HEIGHTS = (600, 1200, 1600)  # smaller copies; the 2400 px master is copied unchanged
# 600: thumbnails, and the low-res stand-in shown while a sharper page loads.
# 1600: phones and high-density screens, which need about 1500 px and used to get 2400.
QUALITY = 84
COVER_QUALITY = 70  # the plaid cloth compresses badly; at 70 it looks the same and is a third the size
IMAGES_VERSION = "2"  # bump to rebuild every image after changing the sizes or qualities above


def label(page: dict) -> str:
    role = page.get("role")
    if role:
        return {"cover": "Front cover", "inside-cover": "Inside front cover",
                "inside-back-cover": "Inside back cover", "back-cover": "Back cover"}[role]
    if page.get("n") is not None:
        return f"Page {page['n']}"
    if page.get("back_of") is not None:
        return f"Back of page {page['back_of']}"
    return "Unnumbered page"


def slug(page: dict) -> str:
    if page.get("role"):
        return page["role"]
    if page.get("n") is not None:
        return str(page["n"])
    if page.get("back_of") is not None:
        return f"{page['back_of']}b"
    return page["id"].lower()


def order(spreads: list[dict], transcript: dict) -> list[dict]:
    """Put the scans in the journal's own page order.

    Scans are numbered in the order they were made, so a spread scanned again later,
    or one that was skipped and scanned afterwards, would otherwise land at the end.
    The front cover goes first and a closed-book scan after it is the back cover.
    Between them, spreads are ordered by the page number Adam wrote on the right-hand
    page; a spread without one stays after the spread scanned before it. When two
    scans show the same page, the later scan wins.
    """
    spreads = [e for e in spreads if e["pages"]]
    keyed, key = [], 0.0
    for i, e in enumerate(spreads):
        if e["kind"] == "single":
            key = -1.0 if i == 0 else float("inf")
        else:
            n = transcript.get(f"{e['scan']}-R", {}).get("page_number")
            key = float(n) if n is not None else max(key, 0.0) + 0.001
        keyed.append((key, i, e))
    best = {}
    for key, i, e in keyed:
        if key in best:
            print(f"page {key:g}: using {e['scan']} instead of the earlier {best[key][2]['scan']}")
        best[key] = (key, i, e)
    result = [e for _, _, e in sorted(best.values(), key=lambda t: (t[0], t[1]))]
    numbers = sorted(int(k) for k in best if 0 < k < float("inf") and k == int(k))
    missing = [n for n in range(1, numbers[-1] + 1) if n not in numbers] if numbers else []
    if missing:
        print("not scanned yet: page " + ", ".join(map(str, missing)))
    return result


def resize(src: Path, dst: Path, height: int, quality: int, fresh: bool) -> None:
    if not fresh and dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        if im.height != height:
            w = round(im.width * height / im.height)
            im = im.convert("RGBa").resize((w, height), Image.LANCZOS).convert("RGBA")
        im.save(dst, "WEBP", quality=quality, method=5)


def main() -> int:
    manifest = json.loads((ROOT / "processed" / "manifest.json").read_text("utf-8"))
    tr_path = ROOT / "content" / "transcript.json"
    transcript = {}
    if tr_path.exists():
        transcript = {f"{p['scan']}-{p['side']}": p for p in json.loads(tr_path.read_text("utf-8"))}
    else:
        print("no content/transcript.json yet: pages will have no text or numbers")

    entries = order(manifest["spreads"], transcript)
    master_h = manifest["page_px"][1]
    version_file = SITE / "img" / "version.txt"
    fresh = not version_file.exists() or version_file.read_text().strip() != IMAGES_VERSION
    if fresh:
        print("image settings changed: rebuilding every image")
    pages, spreads = [], []
    last_recto = None
    for i, e in enumerate(entries):
        sides = {p["side"]: p for p in e["pages"]}
        closed = e["kind"] == "single"
        row = [None, None]
        for side in "LR":
            if side not in sides:
                continue
            pid = f"{e['scan']}-{side}"
            t = transcript.get(pid, {})
            page = {"id": pid, "scan": e["scan"], "side": side}
            if closed:
                page["role"] = "cover" if i == 0 else "back-cover"
            elif i == 1 and side == "L":
                page["role"] = "inside-cover"
            elif t.get("role") == "inside-back-cover":
                page["role"] = "inside-back-cover"   # marked in the transcript
            if t.get("page_number") is not None and "role" not in page:
                page["n"] = t["page_number"]
            if side == "L" and "role" not in page and last_recto is not None:
                page["back_of"] = last_recto
            if side == "R" and page.get("n") is not None:
                last_recto = page["n"]
            page["dates"] = t.get("dates", [])
            page["text"] = t.get("text", "").strip()
            if t.get("lines"):
                page["lines"] = t["lines"]  # the same words, broken where the handwriting breaks
            page["writing"] = bool(t.get("has_writing", side == "R" or closed))
            page["label"] = label(page)
            page["slug"] = slug(page)
            row[0 if side == "L" else 1] = len(pages)
            pages.append(page)

            src = ROOT / "processed" / sides[side]["file"]
            cover = page.get("role") in ("cover", "back-cover")
            for h in HEIGHTS:
                resize(src, SITE / "img" / str(h) / f"{pid}.webp", h, COVER_QUALITY if cover else QUALITY, fresh)
            dst = SITE / "img" / str(master_h) / f"{pid}.webp"
            if cover:
                resize(src, dst, master_h, COVER_QUALITY, fresh)
            elif fresh or not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        spreads.append(row)

    seen = {}
    for p in pages:  # keep slugs unique even if two pages carry the same number
        if p["slug"] in seen:
            seen[p["slug"]] += 1
            p["slug"] += f"-{seen[p['slug']]}"
        else:
            seen[p["slug"]] = 1

    meta_path = ROOT / "content" / "journal.json"
    meta = json.loads(meta_path.read_text("utf-8")) if meta_path.exists() else {}
    out = {
        **meta,
        "page": {"width": manifest["page_px"][0], "height": master_h},
        "heights": sorted([*HEIGHTS, master_h]),
        "pages": pages,
        "spreads": spreads,
    }
    (SITE / "journal.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), "utf-8")
    cover = next((p for p in pages if p.get("role") == "cover"), pages[0] if pages else None)
    if cover:  # the picture shown when someone shares a link to the site
        with Image.open(ROOT / "processed" / "pages" / f"{cover['id']}.webp") as im:
            im = im.convert("RGBa").resize((round(im.width * 560 / im.height), 560), Image.LANCZOS).convert("RGBA")
            card = Image.new("RGB", (1200, 630), (43, 22, 41))
            card.paste(im, ((1200 - im.width) // 2, 35), im)
            card.save(SITE / "og.jpg", quality=86)
    used = {f"{p['id']}.webp" for p in pages}
    for d in (SITE / "img").glob("*"):
        for f in d.glob("*.webp"):
            if f.name not in used:
                f.unlink()
    version_file.write_text(IMAGES_VERSION + "\n")
    print(f"{len(pages)} pages in {len(spreads)} spreads -> site/journal.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
