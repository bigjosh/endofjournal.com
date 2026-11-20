"""Write "EndOfJournal.com" in Adam's own hand, from letters in the scans.

    python tools/card_title.py

Adam prints, with each letter on its own, so a word made from his letters needs no
joins faked. What makes pasted-up letters look like a ransom note is everything
around them: different paper, ink, size and baseline. So each piece is taken as ink
only (paper and rules removed), given the same ink colour and density, kept at the
scale it was written (every page image shares one scale), and set on one ruled
line with Adam's own spacing between letters.

Writes card/title.png: transparent, at the page images' scale (2400 px = 214 mm).
"""
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
PAGES = ROOT / "processed" / "pages"

# Each piece: (text, scan, box x0, x1, y0, y1, keep x0, x1, baseline y), in the 2400 px
# page images. The box is generous; within it, only whole pen strokes whose centre
# falls between the keep columns are used, so letters are never sliced and a
# neighbouring letter that overlaps the box is left out. The baseline is the ruled
# line the piece was written on.
PIECES = [
    ("E",   "Scan10015-R",  975, 1050, 1425, 1510,  985, 1038, 1496),  # English, page 11
    ("nd",  "Scan10006-R",  695,  805,  600,  700,  714,  800,  681),  # and (line 5), page 1
    ("O",   "Scan10006-R",  178,  240,  245,  330,  180,  224,  322),  # Oct, page 1
    ("f",   "Scan10006-R",  945, 1030,  790,  875,  945, 1000,  860),  # for, page 1 (cut at CUT)
    ("Jo",  "Scan10027-R",  378,  500, 1685, 1785,  380,  466, 1752),  # Jodpur, page 23
    ("urn", "Scan10014-R",  405,  510,  905,  970,  414,  505,  957),  # turn, page 10
    ("al",  "Scan10006-R", 1115, 1200,  790,  875, 1120, 1176,  860),  # all, page 1
    (".",   "Scan10007-R",  285,  315, 1735, 1768,  285,  315, 1760),  # Nepal., page 2
    ("com", "Scan10006-R",  255,  415, 1880, 1945,  260,  362, 1937),  # come, page 1
]
# Where a letter's stroke runs into the next one, cut it straight here (page x).
CUT = {"f": 994}   # the f's crossbar touches the o of "for"
GAP = 7            # px between pieces: Adam's usual space between letters of a word
INK = (58, 50, 58)  # one ink colour for every piece (BGR), from the darker strokes on page 1


def ink(scan: str, x0: int, x1: int, y0: int, y1: int, k0: int, k1: int, base: int) -> np.ndarray:
    """The ink in a box, as alpha (0..1), with paper, show-through and rules removed,
    keeping only the strokes centred between columns k0 and k1 and centred on this
    line of writing (not a descender from the line above or ascender from below)."""
    page = cv2.imread(str(PAGES / f"{scan}.webp"), cv2.IMREAD_COLOR)
    pad = 30
    box = page[y0 - pad:y1 + pad, x0 - pad:x1 + pad].astype(np.float32)
    gray = box.mean(axis=2)
    paper = cv2.medianBlur(np.clip(gray, 0, 255).astype(np.uint8), 31).astype(np.float32)
    dark = paper - gray
    rule = (box[..., 0] - box[..., 1] > 5) & (dark < 45)       # lilac and light: the ruling
    dark[rule] = 0
    a = np.clip((dark - 14) / 60, 0, 1) ** 0.85
    a = a[pad:-pad, pad:-pad]
    # Whole strokes: connected ink, slightly thickened so a stroke's faint end stays with it.
    solid = cv2.dilate((a > 0.12).astype(np.uint8), np.ones((3, 3), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(solid)
    keep = np.zeros(n, bool)
    for i in range(1, n):
        cx, cy = cents[i][0] + x0, cents[i][1] + y0
        if k0 <= cx <= k1 and base - 75 <= cy <= base + 8 and stats[i, cv2.CC_STAT_AREA] >= 25:
            keep[i] = True
    a = a * keep[labels]
    # Even density: the darkest strokes of every piece reach the same strength.
    strong = np.percentile(a[a > 0.15], 92) if (a > 0.15).any() else 1
    return np.clip(a / max(strong, 1e-3) * 0.92, 0, 1)


def main() -> None:
    parts = []
    for text, scan, x0, x1, y0, y1, k0, k1, base in PIECES:
        a = ink(scan, x0, x1, y0, y1, k0, k1, base)
        if text in CUT:
            a[:, CUT[text] - x0:] = 0
        cols = np.where(a.max(axis=0) > 0.2)[0]                 # trim to the ink
        a = a[:, cols[0]: cols[-1] + 1]
        parts.append((text, a, base - y0))                       # baseline within the piece
    above = max(b for _, _, b in parts)
    below = max(a.shape[0] - b for _, a, b in parts)
    width = sum(a.shape[1] for _, a, _ in parts) + GAP * (len(parts) - 1)
    alpha = np.zeros((above + below, width), np.float32)
    x = 0
    for text, a, b in parts:
        y = above - b
        region = alpha[y:y + a.shape[0], x:x + a.shape[1]]
        np.maximum(region, a, out=region)
        x += a.shape[1] + GAP
    out = np.zeros((*alpha.shape, 4), np.uint8)
    out[..., :3] = INK
    out[..., 3] = (alpha * 255).astype(np.uint8)
    (ROOT / "card").mkdir(exist_ok=True)
    cv2.imwrite(str(ROOT / "card" / "title.png"), out)
    (ROOT / "card" / "title-baseline.txt").write_text(f"{above}\n")
    print(f"card/title.png: {width} x {alpha.shape[0]} px, baseline {above} px from the top "
          f"({width / 2400 * 214:.0f} mm wide at the journal's size)")


if __name__ == "__main__":
    main()
