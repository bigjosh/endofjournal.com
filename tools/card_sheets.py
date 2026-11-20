"""Build the images for Adam's birthday card: a two-thirds-size copy of the journal.

    python tools/card_title.py      # first, if the title pieces changed
    python tools/card_qr.py         # first, if the QR code changed
    python tools/card_sheets.py     # then open card/card.html and print it

Two sheets, each one open spread glued over two boards:
  inside   the inside front cover (scanned) | a new page: EndOfJournal.com and the QR
  outside  back cover | spine | front cover (from scans/full cover.TIF)
The outside sheet's front cover is on the right: flipped over, it backs the inside
cover. Every image runs BLEED_MM past the cut line so trimming leaves no white edge.
"""
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.signal import find_peaks

ROOT = Path(__file__).resolve().parent.parent
CARD = ROOT / "card"
PAGES = ROOT / "processed" / "pages"

SCALE = 2 / 3            # the card against the real notebook
BOARD_W, BOARD_H = 98.5, 138.0   # each board, mm: the notebook's 147.8 x 207 at two-thirds
FOLD_MM = 1.0            # the card's thickness when closed, taken up at the spine
BLEED_MM = 3.0
DPI = 600
PAGE_PX_MM = 2400 / 214  # the page images' scale (px per mm of the real notebook)


def px(mm: float) -> int:
    return int(round(mm / 25.4 * DPI))


def extend(img: np.ndarray, top=0, bottom=0, left=0, right=0) -> np.ndarray:
    """Grow an image past its edges by mirroring what is inside them."""
    return cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_REFLECT_101)


def page_box(rgba: np.ndarray, inset_mm=0.8):
    """The page's own rectangle in a page image, a little inside its edge."""
    ys, xs = np.where(rgba[..., 3] > 250)
    i = int(round(inset_mm * PAGE_PX_MM))
    return xs.min() + i, ys.min() + i, xs.max() - i, ys.max() - i


def fill(bgr: np.ndarray, hole: np.ndarray) -> np.ndarray:
    """Paint over holes (rounded corners, scanner background) from what surrounds them.
    Done at a quarter size for speed; only the holes are replaced."""
    if not hole.any():
        return bgr
    small = cv2.resize(bgr, (bgr.shape[1] // 4, bgr.shape[0] // 4), interpolation=cv2.INTER_AREA)
    sh = cv2.resize(hole.astype(np.uint8) * 255, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
    sh = cv2.dilate(sh, np.ones((5, 5), np.uint8))
    painted = cv2.resize(cv2.inpaint(small, sh, 9, cv2.INPAINT_TELEA), (bgr.shape[1], bgr.shape[0]), interpolation=cv2.INTER_CUBIC)
    out = bgr.copy()
    grown = cv2.dilate(hole.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    out[grown] = painted[grown]
    return out


def mirror_fill(img: np.ndarray, hole: np.ndarray, band: int) -> np.ndarray:
    """Fill holes along the top and bottom edges with the cloth just inside, mirrored
    across the edge, column by column, so the weave and plaid carry on into the
    rounded corners and the ends of the spine instead of turning into a smear."""
    out = img.copy()
    h = img.shape[0]
    for x in np.where(hole[:band].any(axis=0) | hole[h - band:].any(axis=0))[0]:
        col = hole[:, x]
        top = np.where(col[:band])[0]
        if len(top):
            edge = top.max() + 1
            out[top, x] = img[np.clip(2 * edge - top, 0, h - 1), x]
        bot = np.where(col[h - band:])[0] + h - band
        if len(bot):
            edge = bot.min() - 1
            out[bot, x] = img[np.clip(2 * edge - bot, 0, h - 1), x]
    return out


OUTER_TRIM_MM = 3.5   # off the new page's outer edge: the page stack seen past it


def to_board(rgba: np.ndarray, fold_side: str) -> np.ndarray:
    """A page image cropped to its page, sized to one board, with bleed on its three
    outer edges and half the fold on the fourth."""
    bgr = fill(rgba[..., :3], rgba[..., 3] < 250)     # the rounded corners, filled with page
    x0, y0, x1, y1 = page_box(rgba)
    # The new page loses its outer strip (the page stack seen past it). The inside
    # cover keeps all of its edge: the cloth turned over the board, as on the real one.
    if fold_side == "left":
        x1 -= int(round(OUTER_TRIM_MM * PAGE_PX_MM))
    img = cv2.resize(bgr[y0:y1, x0:x1], (px(BOARD_W), px(BOARD_H)), interpolation=cv2.INTER_CUBIC)
    b, f = px(BLEED_MM), px(FOLD_MM / 2)
    return extend(img, b, b, f if fold_side == "left" else b, f if fold_side == "right" else b)


def place(page: np.ndarray, overlay: np.ndarray, x: int, y: int) -> None:
    a = overlay[..., 3:4].astype(np.float32) / 255
    h, w = overlay.shape[:2]
    region = page[y:y + h, x:x + w, :3].astype(np.float32)
    page[y:y + h, x:x + w, :3] = (region * (1 - a) + overlay[..., :3].astype(np.float32) * a).astype(np.uint8)


def inside() -> None:
    cover = cv2.imread(str(PAGES / "Scan10006-L.webp"), cv2.IMREAD_UNCHANGED)   # the inside front cover
    cv2.imwrite(str(CARD / "inside-left.jpg"), to_board(cover, "right"), [cv2.IMWRITE_JPEG_QUALITY, 93])

    page = cv2.imread(str(CARD / "blank-page.png"), cv2.IMREAD_UNCHANGED)
    f = page[..., :3].astype(np.float32)
    w = f.shape[1]
    rules = find_peaks(np.median(f[:, int(w * .1):int(w * .9), 0] - f[:, int(w * .1):int(w * .9), 1], axis=1),
                       height=4, distance=40)[0]
    x0, _, x1, _ = page_box(page, 0)
    title = cv2.imread(str(CARD / "title.png"), cv2.IMREAD_UNCHANGED)
    base = int((CARD / "title-baseline.txt").read_text())
    # On the sixth line, centred over the QR code. The page's middle is measured
    # without the outer strip that to_board trims away.
    mid = (x0 + x1 - int(round(OUTER_TRIM_MM * PAGE_PX_MM))) // 2
    place(page, title, mid - title.shape[1] // 2, rules[5] - base)
    qr = cv2.imread(str(CARD / "qr.png"), cv2.IMREAD_UNCHANGED)
    qx = mid - qr.shape[1] // 2
    qy = rules[8] + (rules[9] - rules[8]) // 2
    place(page, qr, qx, qy)
    cv2.imwrite(str(CARD / "inside-right.jpg"), to_board(page, "left"), [cv2.IMWRITE_JPEG_QUALITY, 93])


def outside() -> dict:
    cover = cv2.imread(str(ROOT / "scans" / "full cover.TIF"), cv2.IMREAD_COLOR)
    cover = cv2.rotate(cover, cv2.ROTATE_90_CLOCKWISE)          # back | spine | front, upright
    # Where the scanner's grey shows (rounded corners, the ends of the spine), paint
    # it over with the cloth around it. Grey is unsaturated; only look near the edges
    # and the spine, so the white barcode label is left alone.
    sat = cv2.cvtColor(cover, cv2.COLOR_BGR2HSV)[..., 1]
    near = np.zeros(sat.shape, bool)
    e = 260
    near[:e + 155] = near[-(e + 120):] = True
    near[:, :e] = near[:, -e:] = True
    hole = (sat < 70) & near
    hole = cv2.morphologyEx(hole.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    # Take in the worn rim along the edge too, so it isn't mirrored into a line.
    hole = cv2.dilate(hole, np.ones((17, 17), np.uint8)).astype(bool)
    cover = mirror_fill(cover, hole, band=420)
    # The covers run from edge to edge of the glass across, and from 155 to 4980 down;
    # come in a little from the top and bottom, past the worn edge.
    top, bottom = 155 + 60, 4980 - 85
    want_w = int(round((bottom - top) * (2 * BOARD_W + FOLD_MM) / BOARD_H))
    left = (cover.shape[1] - want_w) // 2
    crop = cover[top:bottom, left:left + want_w]
    trim_w, trim_h = 2 * BOARD_W + FOLD_MM, BOARD_H
    img = cv2.resize(crop, (px(trim_w), px(trim_h)), interpolation=cv2.INTER_AREA)
    b = px(BLEED_MM)
    cv2.imwrite(str(CARD / "outside.jpg"), extend(img, b, b, b, b), [cv2.IMWRITE_JPEG_QUALITY, 93])
    # Where the blank name label is, in mm from the top left of the cut line.
    lx, ly, lw, lh = 5171, 1103, 1278, 354
    sx, sy = trim_w / crop.shape[1], trim_h / crop.shape[0]
    return {"label": {"x": (lx - left) * sx, "y": (ly - top) * sy, "w": lw * sx, "h": lh * sy}}


def main() -> None:
    CARD.mkdir(exist_ok=True)
    inside()
    layout = {"board": [BOARD_W, BOARD_H], "fold": FOLD_MM, "bleed": BLEED_MM, **outside()}
    (CARD / "layout.json").write_text(json.dumps(layout, indent=2) + "\n")
    print("card/inside-left.jpg, card/inside-right.jpg, card/outside.jpg, card/layout.json")
    print("label on the front cover (mm):", {k: round(v, 1) for k, v in layout["label"].items()})


if __name__ == "__main__":
    main()
