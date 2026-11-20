"""Make a clean ruled page for the birthday card from a scanned left-hand page.

    python tools/blank_page.py [ScanNNNNN-L]

Every left-hand page has the writing from the other side showing through. This
keeps the paper (its colour, ageing and grain) and the purple rules, and replaces
the show-through with grain borrowed from clean paper on the same row. The result
is mirrored so it sits as a right-hand page: card/blank-page.png.
"""
import sys
from pathlib import Path

import cv2
import numpy as np
from scipy.signal import find_peaks

ROOT = Path(__file__).resolve().parent.parent
name = sys.argv[1] if len(sys.argv) > 1 else "Scan10008-L"  # the back of page 2: the least show-through
a = cv2.imread(str(ROOT / "processed" / "pages" / f"{name}.webp"), cv2.IMREAD_UNCHANGED)
bgr, al = a[..., :3], a[..., 3]
f = bgr.astype(np.float32)
h, w = al.shape

# The rules: rows bluer than the paper across the page; protect only their lilac pixels.
bluish = f[..., 0] - f[..., 1]
prof = np.median(bluish[:, int(w * .1):int(w * .9)], axis=1)
peaks, _ = find_peaks(prof, height=4, distance=40)
band = np.zeros((h, w), bool)
for y in peaks:
    band[max(0, y - 4): y + 5] = True
rules = band & (bluish > 3)

base = cv2.medianBlur(bgr, 61).astype(np.float32)             # colour and ageing, without strokes
gray = f.mean(axis=2)
dog = cv2.GaussianBlur(gray, (0, 0), 1.5) - cv2.GaussianBlur(gray, (0, 0), 10)
stroke = ((dog < -1.6) & (al > 250)).astype(np.uint8)
stroke = cv2.morphologyEx(stroke, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
stroke = cv2.dilate(stroke, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))).astype(bool)
keep = np.zeros((h, w), bool)
keep[int(h * .02):int(h * .985), int(w * .03):int(w * .95)] = True  # leave the edges and gutter as scanned
stroke &= keep & ~rules

grain = f - base
out, todo = f.copy(), stroke.copy()
for dx in (53, -61, 97, -131, 173, -211):                       # same row, so the grain matches
    use = todo & ~np.roll(stroke | band, dx, axis=1)
    out[use] = base[use] + np.roll(grain, dx, axis=1)[use]
    todo &= ~use
out[todo] = base[todo]
out = np.clip(out, 0, 255).astype(np.uint8)
(ROOT / "card").mkdir(exist_ok=True)
cv2.imwrite(str(ROOT / "card" / "blank-page.png"), cv2.flip(np.dstack([out, al]), 1))
print(f"{len(peaks)} rules kept; {stroke.mean() * 100:.1f}% of the page cleaned -> card/blank-page.png")
