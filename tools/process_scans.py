"""Turn raw flatbed scans of the journal into aligned, background-free page images.

    python tools/process_scans.py                  # process new or changed scans
    python tools/process_scans.py --force          # redo everything
    python tools/process_scans.py --only Scan10007 # one scan (repeatable)

Input   scans/*.TIF  - 600 dpi, journal lying sideways on the glass with the lid
        open, so the page is surrounded by a dark background. Files are read in
        name order; that order is the reading order.

Output  processed/pages/<scan>-L.webp, <scan>-R.webp
            One image per page. Every page has the same pixel size and the
            gutter is always the inner edge, so pages line up when turned.
            The scanner background is transparent.
        processed/manifest.json     reading order and per-page details
        processed/contact-sheet.jpg overview for checking the crops by eye

What it does to each scan: rotate upright, find the book against the dark
background, straighten it, find the gutter, and place the spread on a fixed
canvas centred on the gutter. Nothing is done to the page content itself:
no colour correction, no sharpening, and writing that shows through from the
reverse of a leaf is kept on purpose.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
VERSION = 1  # bump to force reprocessing after changing the geometry below

# Canvas for one page, in millimetres. The journal page is about 147 x 207 mm;
# the extra room holds the cover boards and the edges of the page block, which
# stick out past the top sheet by a millimetre or two.
PAGE_W_MM = 152.0
PAGE_H_MM = 214.0

WORK_SCALE = 2          # detection runs on a half-size copy
FALLBACK_BG_LAB = (76.0, 136.0, 130.0)  # open-lid background, 8-bit Lab
BG_DL = 40.0            # lighter than the background by this much -> book
BG_DC = 14.0            # or this much more colourful (the plaid cover is dark)
MAX_BOOK_H_MM = 211.0   # the tallest correct detection so far is 209.3 mm
EDGE_INSET_MM = 0.15    # pull the cut-out in slightly so no background fringes
SHEET_BG = (96, 90, 90)


# Spreads that were laid on the glass the other way round (the page upside down on
# the left, a blank back-of-page on the right). Turned the right way here, rather
# than scanned again. Page 97 is NOT one of these: Adam wrote it upside down.
TURNED_AROUND = {"Scan10063", "Scan10067"}  # pages 62 and 66


class ScanError(Exception):
    pass


def odd(n: float) -> int:
    n = max(3, int(round(n)))
    return n if n % 2 else n + 1


def ellipse(mm: float, px_per_mm: float) -> np.ndarray:
    k = odd(mm * px_per_mm)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


def scan_dpi(path: Path) -> float:
    with Image.open(path) as im:
        dpi = im.info.get("dpi", (600, 600))[0]
    return float(dpi) if dpi else 600.0


def book_mask(small: np.ndarray, px_per_mm: float) -> np.ndarray:
    """Mask of the book (255) against the scanner background (0)."""
    lab = cv2.cvtColor(cv2.medianBlur(small, 9), cv2.COLOR_BGR2LAB).astype(np.float32)
    f = max(8, int(round(3 * px_per_mm)))
    frame = np.concatenate([lab[:f].reshape(-1, 3), lab[-f:].reshape(-1, 3),
                            lab[:, :f].reshape(-1, 3), lab[:, -f:].reshape(-1, 3)])
    dark = frame[frame[:, 0] < 110]
    if len(dark) <= 5000:
        # The room was lit while scanning, so the background is grey or white and the
        # paper cannot be told from it by colour. Trace the page edges instead.
        return edge_mask(small, px_per_mm)
    bg = np.median(dark, axis=0)

    lighter = lab[:, :, 0] - bg[0] > BG_DL
    coloured = np.hypot(lab[:, :, 1] - bg[1], lab[:, :, 2] - bg[2]) > BG_DC
    fg = ((lighter | coloured) * 255).astype(np.uint8)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, ellipse(2.5, px_per_mm))

    def largest_filled(m: np.ndarray) -> np.ndarray:
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            raise ScanError("no book found against the background")
        out = np.zeros_like(m)
        cv2.drawContours(out, [max(cnts, key=cv2.contourArea)], -1, 255, cv2.FILLED)
        return out

    mask = largest_filled(fg)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ellipse(1.5, px_per_mm))
    mask = largest_filled(mask)
    if cv2.boundingRect(mask)[3] / px_per_mm > MAX_BOOK_H_MM:
        # Taller than the journal can be: part of the background is light enough to
        # pass as paper (a partly lit room), so trace the page edges instead.
        return edge_mask(small, px_per_mm)
    return mask


def edge_mask(small: np.ndarray, px_per_mm: float) -> np.ndarray:
    """Mask of an open spread on a light background, from its top and bottom edges.

    Assumes the spread runs off both ends of the glass, as an open journal does. Each
    page's top and bottom edge is the strongest brightness change within 12 mm of
    the frame: a step where the background is grey, the page edge's own shadow line
    where the background is as light as the paper.
    """
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32), (0, 0), 1.0)
    h, w = gray.shape
    band, k = int(12 * px_per_mm), max(1, int(round(0.3 * px_per_mm)))

    mid_rows = gray[h // 4: 3 * h // 4]
    win = int(15 * px_per_mm)
    centre = np.median(mid_rows[:, w // 2 - win: w // 2 + win], axis=0)
    gx = w // 2 - win + int(np.argmin(cv2.blur(centre.reshape(1, -1), (odd(0.5 * px_per_mm), 1)).ravel()))

    xs = np.arange(int(0.03 * w), int(0.97 * w), 4)
    xs = xs[np.abs(xs - gx) > 8 * px_per_mm]
    prof = np.stack([np.median(gray[:, x - 3: x + 4], axis=1) for x in xs], axis=1)
    d = np.abs(prof[2 * k:] - prof[:-2 * k])          # d[i] is centred on row i + k
    top = d[:band].argmax(axis=0) + k
    bottom = h - band + d[h - band - 2 * k: h - 2 * k].argmax(axis=0) + k

    mask = np.zeros((h, w), np.uint8)
    for side, (x0, x1) in ((xs < gx, (0, gx)), (xs > gx, (gx, w))):
        if side.sum() < 20:
            raise ScanError("could not trace the page edges against a light background")
        st, it = robust_line(xs[side].astype(np.float64), top[side].astype(np.float64))
        sb, ib = robust_line(xs[side].astype(np.float64), bottom[side].astype(np.float64))
        quad = np.array([[x0, st * x0 + it], [x1, st * x1 + it], [x1, sb * x1 + ib], [x0, sb * x0 + ib]])
        cv2.fillPoly(mask, [np.round(quad).astype(np.int32)], 255)
    return mask


def robust_line(xs: np.ndarray, ys: np.ndarray) -> tuple[float, float]:
    """Least-squares line through (xs, ys), refitted once without outliers."""
    slope, icpt = np.polyfit(xs, ys, 1)
    res = ys - (slope * xs + icpt)
    mad = np.median(np.abs(res - np.median(res))) * 1.4826
    keep = np.abs(res) <= max(2.0, 2.5 * mad)
    if keep.sum() >= 10:
        slope, icpt = np.polyfit(xs[keep], ys[keep], 1)
    return float(slope), float(icpt)


def edges(mask: np.ndarray, x0: int, x1: int):
    """Top and bottom edge of the mask over columns x0..x1: (slopes, centre y)."""
    cols = np.arange(x0, x1, 4)
    sub = mask[:, cols] > 0
    ok = sub.any(axis=0)
    cols, sub = cols[ok], sub[:, ok]
    if len(cols) < 20:
        raise ScanError("could not trace the book's edges")
    top = sub.argmax(axis=0).astype(np.float64)
    bottom = (mask.shape[0] - 1 - sub[::-1].argmax(axis=0)).astype(np.float64)
    st, _ = robust_line(cols, top)
    sb, _ = robust_line(cols, bottom)
    return [st, sb], float(np.median((top + bottom) / 2))


def spans(x: int, bw: int, spread: bool):
    """Column ranges used to trace edges: one per page, clear of corners and gutter."""
    if spread:
        return [(x + int(bw * 0.08), x + int(bw * 0.45)), (x + int(bw * 0.55), x + int(bw * 0.92))]
    return [(x + int(bw * 0.12), x + int(bw * 0.88))]


def analyse(upright: np.ndarray, dpi: float, front_cover: bool):
    """Find the transform that puts this scan on the fixed canvas.

    Returns (matrix 2x3 for the full-size upright image, mask at full size, info).
    """
    H, W = upright.shape[:2]
    ws, hs = W // WORK_SCALE, H // WORK_SCALE
    sx, sy = W / ws, H / hs
    px_mm = dpi / 25.4 / WORK_SCALE
    small = cv2.resize(upright, (ws, hs), interpolation=cv2.INTER_AREA)
    mask = book_mask(small, px_mm)

    x, y, bw, bh = cv2.boundingRect(mask)
    spread = bw / bh > 1.15

    slopes = []
    for a, b in spans(x, bw, spread):
        slopes += edges(mask, a, b)[0]
    angle = math.degrees(math.atan(float(np.median(slopes))))
    if abs(angle) > 4:
        raise ScanError(f"implausible skew of {angle:.1f} degrees")

    # Straighten the working copy, then measure gutter and vertical centre on it.
    R = cv2.getRotationMatrix2D((ws / 2, hs / 2), angle, 1.0)
    d_small = cv2.warpAffine(small, R, (ws, hs), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    d_mask = cv2.warpAffine(mask, R, (ws, hs), flags=cv2.INTER_NEAREST)
    x, y, bw, bh = cv2.boundingRect(d_mask)
    cy = float(np.mean([edges(d_mask, a, b)[1] for a, b in spans(x, bw, spread)]))

    if spread:
        gray = cv2.cvtColor(d_small, cv2.COLOR_BGR2GRAY)
        rows = gray[y + bh // 6: y + bh * 5 // 6]
        mid, win = x + bw // 2, int(12 * px_mm)
        raw = np.median(rows[:, mid - win: mid + win], axis=0).astype(np.float32)
        dark = int(np.argmin(cv2.blur(raw.reshape(1, -1), (odd(0.5 * px_mm), 1)).ravel()))
        # The darkest column is the shadow beside the fold. The fold itself is the
        # sharp step where that shadow meets the facing page, if there is one.
        k = max(1, int(round(0.15 * px_mm)))
        step = np.abs(raw[2 * k:] - raw[:-2 * k])            # step[i] is centred on i + k
        near = int(1.5 * px_mm)
        lo, hi = max(0, dark - near - k), min(len(step), dark + near - k)
        if hi > lo and step[lo:hi].max() >= 30:
            dark = lo + int(np.argmax(step[lo:hi])) + k
        gx = float(mid - win + dark)
    else:
        # A closed book: the spine is the inner edge.
        gx = float(x) if front_cover else float(x + bw)

    # Compose: full-size -> working size -> straightened -> full-size -> canvas.
    half_w = int(round(PAGE_W_MM * dpi / 25.4))
    canvas_h = int(round(PAGE_H_MM * dpi / 25.4))
    S_down = np.diag([1 / sx, 1 / sy, 1.0])
    S_up = np.diag([sx, sy, 1.0])
    R3 = np.vstack([R, [0, 0, 1]])
    T = np.array([[1, 0, half_w - gx * sx], [0, 1, canvas_h / 2 - cy * sy], [0, 0, 1]], np.float64)
    M = (T @ S_up @ R3 @ S_down)[:2]

    # Soft-edged cut-out at full size.
    full_mask = cv2.resize(mask, (W, H), interpolation=cv2.INTER_LINEAR)
    inset = odd(2 * EDGE_INSET_MM * dpi / 25.4)
    full_mask = cv2.erode(full_mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (inset, inset)))
    full_mask = cv2.GaussianBlur(full_mask, (0, 0), 2.0)

    # How close did the book come to the edge of the scanner's frame?
    touch = [bool(mask[:3].any()), bool(mask[-3:].any()), bool(mask[:, :3].any()), bool(mask[:, -3:].any())]
    info = {
        "kind": "spread" if spread else "single",
        "skew_deg": round(angle, 3),
        "book_mm": [round(bw / px_mm, 1), round(bh / px_mm, 1)],
        "touches_frame": [n for n, t in zip(["top", "bottom", "left", "right"], touch) if t],
    }
    return M, full_mask, (2 * half_w, canvas_h), info


def process(path: Path, out_dir: Path, page_height: int, front_cover: bool) -> dict:
    dpi = scan_dpi(path)
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ScanError("could not be read (still being written?)")
    turned = path.stem in TURNED_AROUND
    upright = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE if turned else cv2.ROTATE_90_CLOCKWISE)
    del img

    M, mask, (cw, ch), info = analyse(upright, dpi, front_cover)
    colour = cv2.warpAffine(upright, M, (cw, ch), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    alpha = cv2.warpAffine(mask, M, (cw, ch), flags=cv2.INTER_LINEAR, borderValue=0)
    del upright, mask

    # Anything of the book that falls off the canvas is a geometry problem worth knowing about.
    off = [n for n, strip in [("top", alpha[:2]), ("bottom", alpha[-2:]), ("left", alpha[:, :2]), ("right", alpha[:, -2:])]
           if (strip > 128).any()]
    warnings = []
    if off:
        warnings.append("book extends past the canvas: " + ", ".join(off))

    # Residual tilt of the placed book, as a self-check on the straightening.
    a_small = cv2.resize(alpha, (cw // 4, ch // 4), interpolation=cv2.INTER_AREA)
    a_bin = ((a_small > 128) * 255).astype(np.uint8)
    bx, _, bbw, _ = cv2.boundingRect(a_bin)
    res = [s for a, b in spans(bx, bbw, info["kind"] == "spread") for s in edges(a_bin, a, b)[0]]
    info["residual_skew_deg"] = round(math.degrees(math.atan(float(np.median(res)))), 3)

    rgba = np.dstack([cv2.cvtColor(colour, cv2.COLOR_BGR2RGB), alpha])
    del colour
    pages = save_pages(rgba, path.stem, out_dir, page_height)
    st = path.stat()
    return {"scan": path.stem, **info, "pages": pages, "warnings": warnings,
            "source": {"size": st.st_size, "mtime": int(st.st_mtime), "version": VERSION, "height": page_height}}


def save_pages(rgba: np.ndarray, stem: str, out_dir: Path, page_height: int) -> list[dict]:
    """Split a placed spread (RGBA, on the fixed canvas) into its two pages and save them."""
    ch, cw = rgba.shape[:2]
    half = cw // 2
    sides = {"L": rgba[:, :half], "R": rgba[:, half:]}
    page_w = int(round(half * page_height / ch))
    pages = []
    (out_dir / "pages").mkdir(parents=True, exist_ok=True)
    for side, arr in sides.items():
        coverage = float((arr[:, :, 3] > 128).mean())
        target = out_dir / "pages" / f"{stem}-{side}.webp"
        if coverage < 0.2:  # the empty half of a closed-book scan
            target.unlink(missing_ok=True)
            continue
        # Premultiplied resize, so the transparent background cannot bleed into page edges.
        im = Image.fromarray(np.ascontiguousarray(arr), "RGBA").convert("RGBa")
        im = im.resize((page_w, page_height), Image.LANCZOS).convert("RGBA")
        im.save(target, "WEBP", quality=86, method=4)
        pages.append({"side": side, "file": f"pages/{target.name}", "width": page_w, "height": page_height,
                      "bytes": target.stat().st_size})
    return pages


FULL_COVER = "full cover.TIF"   # the whole outside of the notebook, opened flat
BACK_COVER = "BackCover"        # the page made from it, which ends the journal


def back_cover(path: Path, out_dir: Path, page_height: int) -> dict:
    """The outside back cover, cut from the scan of the whole open cover.

    That scan lies sideways like the others, with the back cover, the spine and the
    front cover across it once upright. The back cover is cut down the middle of
    the spine (as the front cover's own scan shows its half of the spine), freed
    from the light background, straightened, and placed like a closed-book scan:
    spine at the binding, on the left, so it reads as the last page.
    """
    dpi = scan_dpi(path)
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise ScanError("could not be read (still being written?)")
    up = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    del img
    h, w = up.shape[:2]
    px_mm = dpi / 25.4

    # The spine is the darkest band near the middle: the red-brown cloth.
    light = cv2.cvtColor(up[h // 3: 2 * h // 3], cv2.COLOR_BGR2LAB)[..., 0].astype(np.float32).mean(axis=0)
    win = int(25 * px_mm)
    seg = cv2.blur(light[w // 2 - win: w // 2 + win].reshape(1, -1), (odd(1.5 * px_mm), 1)).ravel()
    band = np.where(seg < seg.min() + 30)[0]
    cut = w // 2 - win + int((band.min() + band.max()) // 2)
    crop = up[:, :cut]

    # Background: unsaturated grey reaching in from the scan's edges. (Grey inside
    # the cover, such as the barcode label, is not connected to an edge.)
    sat = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[..., 1]
    n, labels = cv2.connectedComponents((sat < 70).astype(np.uint8))
    border = (set(np.unique(labels[0])) | set(np.unique(labels[-1])) | set(np.unique(labels[:, 0]))) - {0}
    mask = np.where(np.isin(labels, list(border)), 0, 255).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, ellipse(1.0, px_mm))

    # Straighten from the top and bottom edges.
    slopes = edges(mask, int(crop.shape[1] * .15), int(crop.shape[1] * .85))[0]
    angle = math.degrees(math.atan(float(np.median(slopes))))
    R = cv2.getRotationMatrix2D((crop.shape[1] / 2, h / 2), angle, 1.0)
    crop = cv2.warpAffine(crop, R, (crop.shape[1], h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    mask = cv2.warpAffine(mask, R, (crop.shape[1], h), flags=cv2.INTER_NEAREST)
    ys = np.where((mask > 128).any(axis=1))[0]
    top, bottom = int(ys.min()), int(ys.max())

    alpha = cv2.GaussianBlur(cv2.erode(mask, ellipse(2 * EDGE_INSET_MM, px_mm)), (0, 0), 2.0)
    half_w, ch = int(round(PAGE_W_MM * px_mm)), int(round(PAGE_H_MM * px_mm))
    canvas = np.zeros((ch, 2 * half_w, 4), np.uint8)
    x0 = half_w - crop.shape[1]                     # spine edge at the binding
    y0 = ch // 2 - (top + bottom) // 2              # cover centred top to bottom
    sy0, sy1 = max(0, -y0), min(h, ch - y0)
    sx0 = max(0, -x0)
    canvas[y0 + sy0: y0 + sy1, x0 + sx0: half_w, :3] = cv2.cvtColor(crop[sy0:sy1, sx0:], cv2.COLOR_BGR2RGB)
    canvas[y0 + sy0: y0 + sy1, x0 + sx0: half_w, 3] = alpha[sy0:sy1, sx0:]

    # The glass cut off the outer edge, rounded corners and all. Give it back the
    # real corners: the front cover's own, from its scan, mirrored.
    front = out_dir / "pages" / "Scan10005-R.webp"
    if front.exists():
        fa = np.array(Image.open(front))[..., 3]
        fys, fxs = np.where(fa > 128)
        size = int(14 * fa.shape[0] / PAGE_H_MM)                       # 14 mm square holds the curve
        scale = px_mm / (fa.shape[0] / PAGE_H_MM)
        left = x0 + sx0
        for fy, cy in ((fys.min(), y0 + top), (fys.max() - size + 1, y0 + bottom - int(size * scale) + 1)):
            patch = fa[fy: fy + size, fxs.max() - size + 1: fxs.max() + 1][:, ::-1]   # mirrored to a left corner
            patch = cv2.resize(patch, (int(size * scale), int(size * scale)), interpolation=cv2.INTER_LINEAR)
            region = canvas[cy: cy + patch.shape[0], left: left + patch.shape[1], 3]
            region[:] = (region.astype(np.float32) * patch[:region.shape[0], :region.shape[1]] / 255).astype(np.uint8)

    pages = save_pages(canvas, BACK_COVER, out_dir, page_height)
    st = path.stat()
    return {"scan": BACK_COVER, "kind": "single", "skew_deg": round(angle, 3),
            "book_mm": [round(crop.shape[1] / px_mm, 1), round((bottom - top) / px_mm, 1)],
            "touches_frame": ["left"], "residual_skew_deg": 0.0, "pages": pages, "warnings": [],
            "source": {"size": st.st_size, "mtime": int(st.st_mtime), "version": VERSION, "height": page_height}}


def contact_sheet(entries: list[dict], out_dir: Path, cols: int = 3, page_h: int = 420) -> None:
    if not entries:
        return
    pw = int(round(page_h * PAGE_W_MM / PAGE_H_MM))
    cell_w, cell_h, pad, label = 2 * pw, page_h, 18, 26
    rows = math.ceil(len(entries) / cols)
    sheet = Image.new("RGB", (cols * (cell_w + pad) + pad, rows * (cell_h + label + pad) + pad), SHEET_BG)
    for i, e in enumerate(entries):
        x0 = pad + (i % cols) * (cell_w + pad)
        y0 = pad + (i // cols) * (cell_h + label + pad)
        for p in e["pages"]:
            with Image.open(out_dir / p["file"]) as im:
                thumb = im.convert("RGBa").resize((pw, page_h), Image.LANCZOS).convert("RGBA")
            sheet.paste(thumb, (x0 + (pw if p["side"] == "R" else 0), y0 + label), thumb)
        arr = np.array(sheet)
        text = f"{i + 1}. {e['scan']}  skew {e['skew_deg']:+.2f}" + ("  ! " + "; ".join(e["warnings"]) if e["warnings"] else "")
        cv2.putText(arr, text, (x0, y0 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (235, 235, 235), 1, cv2.LINE_AA)
        sheet = Image.fromarray(arr)
    sheet.save(out_dir / "contact-sheet.jpg", quality=88)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=ROOT / "scans")
    ap.add_argument("--out", type=Path, default=ROOT / "processed")
    ap.add_argument("--height", type=int, default=2400, help="page image height in pixels")
    ap.add_argument("--force", action="store_true", help="reprocess scans that have not changed")
    ap.add_argument("--only", action="append", default=[], help="process just this scan (name without extension)")
    args = ap.parse_args()

    # Only the scanner's own numbered files are journal spreads; anything else in the
    # folder (such as "full cover.TIF", scanned for the birthday card) is left alone.
    scans = sorted(p for p in args.src.iterdir() if p.suffix.lower() in (".tif", ".tiff") and p.name.lower().startswith("scan"))
    if not scans:
        print(f"no .TIF scans in {args.src}")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "manifest.json"
    previous = {}
    if manifest_path.exists():
        previous = {e["scan"]: e for e in json.loads(manifest_path.read_text("utf-8")).get("spreads", [])}

    entries, failed = [], 0
    for idx, path in enumerate(scans):
        old = previous.get(path.stem)
        st = path.stat()
        if time.time() - st.st_mtime < 15:
            print(f"{path.stem}: skipped, still being written")
            if old:
                entries.append(old)
            continue
        fresh = (old and old["source"] == {"size": st.st_size, "mtime": int(st.st_mtime), "version": VERSION, "height": args.height}
                 and all((args.out / p["file"]).exists() for p in old["pages"]))
        wanted = not args.only or path.stem in args.only
        if old and (not wanted or (fresh and not args.force)):
            entries.append(old)
            continue
        if not wanted:
            continue
        t0 = time.time()
        try:
            e = process(path, args.out, args.height, front_cover=(idx == 0))
        except ScanError as err:
            failed += 1
            print(f"{path.stem}: FAILED - {err}")
            continue
        entries.append(e)
        notes = "".join(f"  ! {w}" for w in e["warnings"])
        if e["touches_frame"]:
            notes += f"  (reaches scan edge: {', '.join(e['touches_frame'])})"
        print(f"{path.stem}: {e['kind']}, {len(e['pages'])} page(s), skew {e['skew_deg']:+.2f} -> {e['residual_skew_deg']:+.2f} deg, "
              f"{time.time() - t0:.1f}s{notes}")

    # The outside back cover ends the journal, cut from the scan of the whole cover.
    full = args.src / FULL_COVER
    if full.exists():
        old, st = previous.get(BACK_COVER), full.stat()
        fresh = (old and old["source"] == {"size": st.st_size, "mtime": int(st.st_mtime), "version": VERSION, "height": args.height}
                 and all((args.out / p["file"]).exists() for p in old["pages"]))
        wanted = not args.only or BACK_COVER in args.only
        if old and (not wanted or (fresh and not args.force)):
            entries.append(old)
        elif wanted:
            try:
                e = back_cover(full, args.out, args.height)
                entries.append(e)
                print(f"{BACK_COVER} (from {FULL_COVER}): skew {e['skew_deg']:+.2f} deg, {e['book_mm'][0]} x {e['book_mm'][1]} mm")
            except ScanError as err:
                failed += 1
                print(f"{BACK_COVER}: FAILED - {err}")

    manifest = {"page_mm": [PAGE_W_MM, PAGE_H_MM], "page_px": [int(round(args.height * PAGE_W_MM / PAGE_H_MM)), args.height],
                "spreads": entries}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    contact_sheet(entries, args.out)
    print(f"{len(entries)} scan(s) in {manifest_path.relative_to(ROOT) if manifest_path.is_relative_to(ROOT) else manifest_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
