"""A QR code for https://endofjournal.com, its dark squares cut from the cover's plaid.

    python tools/card_qr.py

The dark modules are the real plaid from the scanned cover, at the card's scale,
darkened just enough that every one is far darker than the page; the light modules
are left transparent so the ruled page shows through, as if the code were printed
on it. Highest error correction, and the result is checked with a QR reader at
print size and blurred, before it is saved: card/qr.png (and card/qr-print-test.png).
"""
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
URL = "https://endofjournal.com"
SCAN_DPI = 600
CARD_SCALE = 2 / 3                  # the card is two-thirds the size of the notebook
MODULE_MM = 1.1                     # on the card: comfortable for any phone camera
OUT_PX_PER_MM = 2400 / 214          # the page images' scale, where the QR is placed
MAX_Y = 72                          # darkest-allowed brightness for a dark module (of 255)


def modules() -> np.ndarray:
    p = cv2.QRCodeEncoder_Params()
    p.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_H
    q = cv2.QRCodeEncoder.create(p).encode(URL)        # 1 px per module, 2-module border
    return q[2:-2, 2:-2] == 0                           # True = dark


def plaid(size_px: int) -> np.ndarray:
    """A square of the cover's plaid, at the scale it will have on the card."""
    cover = cv2.imread(str(ROOT / "scans" / "full cover.TIF"), cv2.IMREAD_COLOR)
    cover = cv2.rotate(cover, cv2.ROTATE_90_CLOCKWISE)
    # The QR on the card, measured on the real notebook: card size / card scale.
    real_mm = size_px / OUT_PX_PER_MM / CARD_SCALE
    side = int(real_mm / 25.4 * SCAN_DPI)
    x0, y0 = 3720, 1350                                  # front cover, between the label and the logo
    patch = cover[y0:y0 + side, x0:x0 + side]
    # The page images are at the notebook's own scale, so the plaid goes in at that scale too.
    return cv2.resize(patch, (size_px, size_px), interpolation=cv2.INTER_AREA)


def main() -> None:
    dark = modules()
    n = dark.shape[0]
    mod_px = MODULE_MM / CARD_SCALE * OUT_PX_PER_MM     # module size in page-image pixels
    size = int(round(n * mod_px))
    tex = plaid(size).astype(np.float32)
    y = tex @ np.array([0.114, 0.587, 0.299], np.float32)
    tex *= np.minimum(1, MAX_Y / np.maximum(y, 1))[..., None]   # darken, keeping the colours
    mask = cv2.resize(dark.astype(np.uint8) * 255, (size, size), interpolation=cv2.INTER_NEAREST)
    mask = cv2.GaussianBlur(mask, (0, 0), 0.6)                    # a printed edge, not a pixel step
    out = np.dstack([np.clip(tex, 0, 255).astype(np.uint8), mask])
    (ROOT / "card").mkdir(exist_ok=True)
    cv2.imwrite(str(ROOT / "card" / "qr.png"), out)

    # Check: on paper, at the card's print size, then blurred and small like a phone sees it.
    paper = np.full((size + 200, size + 200, 3), (232, 240, 246), np.float32)
    a = mask[..., None].astype(np.float32) / 255
    paper[100:-100, 100:-100] = paper[100:-100, 100:-100] * (1 - a) + tex * a
    det = cv2.QRCodeDetector()
    for label, img in [("full size", paper),
                       ("phone, 300 px, blurred", cv2.GaussianBlur(cv2.resize(paper, (300, 300), interpolation=cv2.INTER_AREA), (0, 0), 1.0))]:
        text, _, _ = det.detectAndDecode(img.astype(np.uint8))
        print(f"{label}: {'reads ' + text if text else 'NOT READ'}")
        if text != URL:
            raise SystemExit("the QR code does not read back correctly")
    cv2.imwrite(str(ROOT / "card" / "qr-print-test.png"), paper.astype(np.uint8))
    print(f"card/qr.png: {n} x {n} modules, {n * MODULE_MM:.1f} mm square on the card")


if __name__ == "__main__":
    main()
