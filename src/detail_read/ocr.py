"""Render PDF pages and read their text with Tesseract OCR.

Drawings put dimensions and callouts at 0 and 90 degrees, so each page is
OCR'd upright and turned both ways. Any embedded PDF text is merged in too:
it is exact where present, and OCR covers drawings that are vector or raster
only. Every line comes back with a bbox normalised to 0..1 of the rendered
(rotation-applied) page, so the frontend can overlay it on a preview.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import pymupdf
import pytesseract
from PIL import Image

OCR_DPI = int(os.environ.get("OCR_DPI", "200"))
MIN_CONF = 30


@dataclass
class Line:
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in 0..1
    source: str  # "ocr" | "pdf"
    conf: float = 100.0


def _render(page: pymupdf.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _ocr_lines(img: Image.Image, angle: int) -> list[Line]:
    """OCR img turned by angle (PIL convention, counter-clockwise) and map boxes back."""
    W, H = img.size
    im = img.rotate(angle, expand=True) if angle else img
    d = pytesseract.image_to_data(im, config="--psm 11", output_type=pytesseract.Output.DICT)
    groups: dict[tuple, list[int]] = {}
    for i, word in enumerate(d["text"]):
        if word.strip() and float(d["conf"][i]) >= MIN_CONF:
            groups.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)

    def back(u: float, v: float) -> tuple[float, float]:
        if angle == 90:    # image turned CCW: (x, y) -> (y, W - x)
            return W - v, u
        if angle == -90:   # image turned CW:  (x, y) -> (H - y, x)
            return v, H - u
        return u, v

    lines = []
    for idx in groups.values():
        idx.sort(key=lambda i: d["left"][i])
        text = " ".join(d["text"][i] for i in idx)
        u0 = min(d["left"][i] for i in idx)
        v0 = min(d["top"][i] for i in idx)
        u1 = max(d["left"][i] + d["width"][i] for i in idx)
        v1 = max(d["top"][i] + d["height"][i] for i in idx)
        (xa, ya), (xb, yb) = back(u0, v0), back(u1, v1)
        conf = sum(float(d["conf"][i]) for i in idx) / len(idx)
        lines.append(Line(text, (min(xa, xb) / W, min(ya, yb) / H, max(xa, xb) / W, max(ya, yb) / H), "ocr", conf))
    return lines


def _pdf_lines(page: pymupdf.Page) -> list[Line]:
    rect = page.rect  # rotation applied
    m = page.rotation_matrix
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = " ".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            r = pymupdf.Rect(line["bbox"]) * m
            out.append(Line(text, (r.x0 / rect.width, r.y0 / rect.height, r.x1 / rect.width, r.y1 / rect.height), "pdf"))
    return out


def read_page(pdf_path: str, index: int, dpi: int = OCR_DPI) -> dict:
    """OCR one page. Opens its own document handle so pages can run on separate threads."""
    with pymupdf.open(pdf_path) as doc:
        page = doc[index]
        img = _render(page, dpi)
        lines = _pdf_lines(page)
        for angle in (0, -90, 90):
            lines.extend(_ocr_lines(img, angle))
        w, h = img.size
    return {"index": index, "size": [w, h], "lines": [asdict(l) for l in lines]}


def page_count(pdf_path: str) -> int:
    with pymupdf.open(pdf_path) as doc:
        return doc.page_count


def read_pdf(pdf_path: str, on_page=None) -> list[dict]:
    """OCR every page in parallel; on_page(done, total) reports progress.

    Threads are enough: Tesseract runs as a subprocess, outside the GIL.
    """
    n = page_count(pdf_path)
    workers = max(1, min(n, (os.cpu_count() or 2) - 1))
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(read_page, [pdf_path] * n, range(n)):
            results.append(res)
            if on_page:
                on_page(len(results), n)
    return results


def render_preview(pdf_path: str, index: int, width: int = 1400) -> bytes:
    with pymupdf.open(pdf_path) as doc:
        page = doc[index]
        zoom = width / page.rect.width
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
