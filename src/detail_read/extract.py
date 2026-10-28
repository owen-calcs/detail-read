"""Pull structural callouts out of OCR'd drawing text and convert them to imperial.

Everything on these drawings is assumed metric (mm, kN, kg/m sections) unless
the callout is unambiguously imperial (e.g. W8X24, 3/4" bolts).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import sections
from .units import (
    BOLT_MM_TO_IN,
    KN_TO_KIP,
    KNM_TO_KIPFT,
    KPA_TO_PSF,
    MPA_TO_KSI,
    REBAR_M_TO_US,
    bolt_in_option,
    fmt_fraction_in,
    fmt_length_mm,
    mm_to_in,
)


@dataclass
class Entity:
    kind: str            # member, plate, bolt, rebar, weld, load, stress, elevation, dimension, title
    raw: str             # text as read
    metric: str          # cleaned metric designation / value
    imperial: str        # converted designation / value
    bbox: list[float]
    data: dict = field(default_factory=dict)


# OCR confuses these inside numbers: W610x24l, W2OOx36, HSS152x152xS
_DIGIT_FIX = str.maketrans({"l": "1", "I": "1", "|": "1", "O": "0", "o": "0", "S": "5", "B": "8"})


def _fix_digits(s: str) -> str:
    return s.translate(_DIGIT_FIX)


def _normalise(text: str) -> str:
    t = text.replace("×", "x").replace("X", "x").replace("—", "-").replace("–", "-")
    # Repair digits inside "<prefix><digits>x<digits>" designations.
    def fix(m: re.Match) -> str:
        return m.group(1) + _fix_digits(m.group(2))
    t = re.sub(r"\b(HSS|W|WT|HP|MC|C|L|PL|BP\d?)\s?([0-9lIOoSB|][0-9lIOoSB|.x\s]*[0-9lIOoSB|])", fix, t)
    return t


NUM = r"\d+(?:\.\d+)?"
RE_DW = re.compile(rf"\b(W|WT|HP|MC|C|S|M)\s?(\d{{1,4}})\s?x\s?({NUM})\b")
RE_HSS_RECT = re.compile(rf"\bHSS\s?({NUM})\s?x\s?({NUM})\s?x\s?({NUM}|\d+/\d+)")
RE_HSS_ROUND = re.compile(rf"\bHSS\s?({NUM})\s?x\s?({NUM}|\d+/\d+)(?!\s?x)")
RE_ANGLE = re.compile(rf"\b(2?L)\s?({NUM})\s?x\s?({NUM})\s?x\s?({NUM}|\d+/\d+)")
RE_PLATE = re.compile(rf"\b(BP\d*|CAP\s?PL|BASE\s?PL|PL|PLATE)\s*:?\s*({NUM})(?:\s?(?:mm)?\s?x\s?({NUM}))?(?:\s?x\s?({NUM}))?", re.I)
# "10mm THK Shear Tab", "12 mm Cap PL", "lOmm THK Shear Tab" (OCR l/O for 1/0)
RE_PLATE_THK = re.compile(r"\b([0-9lIO]{1,2})\s?mm\.?\s*(?:THK\.?\s*)?(SHEAR\s*TABS?|FIN\s*PL(?:ATE)?|CAP\s*PL(?:ATE)?|BASE\s*PL(?:ATE)?|KNIFE\s*PL(?:ATE)?|STIFF(?:ENER)?S?|PL(?:ATE)?S?)\b", re.I)
RE_BOLT_M =re.compile(r"\b(?:(\d{1,2})\s?-\s?)?M(1[26]|2[0247]|3[036])\b")
RE_BOLT_IN = re.compile(r"\b(?:(\d{1,2})\s?-\s?)?(\d/\d|1(?:-\d/\d)?)\s?(?:\"|''|in\.?)\s?(?:dia\.?|Ø|A325|A490|bolts?)", re.I)
RE_BOLT_GRADE = re.compile(r"\b(A325|A490|F1852|F2280)\b", re.I)
RE_REBAR = re.compile(r"\b(?:(\d{1,2})\s?-\s?)?(10|15|20|25|30|35|45|55)M\b")
RE_WELD = re.compile(rf"\b({NUM})\s?mm?\s?(?:fillet|weld|fw|cjp|pjp)\b", re.I)
# CSA notation: Vf/Tf/Cf/Mf/Hf are factored shear, tension, compression, moment, horizontal.
RE_LOAD = re.compile(
    r"(?:\b(?:Vertical\s+|Horizontal\s+|Uplift\s*)?([RVPNTMCH][fFu]?|SHEAR|AXIAL|REACTION|TENSION|COMPRESSION|MOMENT|UPLIFT|VERTICAL|HORIZONTAL)\.?\s*[=:]\s*)?"
    r"(-?[0-9IlOoSG][0-9IlOoSG]*(?:\.\d+)?)\s?(k?kN\s?[·.\-*]?\s?m\b|kNm\b|k?kN)",
    re.I,
)
LOAD_ACTION = {
    "V": "shear", "R": "shear", "SHEAR": "shear", "REACTION": "shear", "VERTICAL": "shear",
    "T": "tension", "N": "tension", "TENSION": "tension", "UPLIFT": "tension",
    "C": "compression", "P": "compression", "COMPRESSION": "compression", "AXIAL": "compression",
    "M": "moment", "MOMENT": "moment",
    "H": "horizontal", "HORIZONTAL": "horizontal",
}
_LOAD_DIGITS = str.maketrans({"I": "1", "l": "1", "O": "0", "o": "0", "S": "5", "G": "6"})
RE_STRESS = re.compile(rf"\b({NUM})\s?(MPa|kPa)\b", re.I)
RE_ELEV = re.compile(rf"\b(?:EL|ELEV|T/O|B/O)\.?\s?:?\s?(-?\d{{3,6}})\b", re.I)
RE_DIM_MM = re.compile(r"\b(\d{2,5})\s?mm\b", re.I)
RE_DIM_BARE = re.compile(r"^\s*(\d{2,5})\s*[|/\\]?\s*$")
RE_TITLE = re.compile(r"\b((?:TYP(?:ICAL)?\.?\s+)?(?:SECTION|DETAIL|ELEVATION|PLAN|CONNECTION|VIEW)\b.{0,40})", re.I)
RE_DWG = re.compile(r"\b(?:DWG\.?\s?NO\.?|DRAWING)\s*:?\s*([A-Z]{0,3}-?\d{1,4}[A-Z]?)\b", re.I)


def _member(family: str, dims: tuple[float, ...], raw: str, bbox) -> Entity | None:
    # Imperial callouts (W8X24, HSS6X6X3/8) are small numbers; metric ones are mm.
    is_imperial = (family in sections.DEPTH_WEIGHT and dims[0] <= 44) or (
        family.startswith("HSS") or family == "L") and dims[0] <= 24
    if is_imperial:
        sec = sections.imperial_lookup(raw)
        metric = raw
    else:
        sec = sections.metric_to_imperial(family, dims)
        metric = _metric_name(family, dims)
    if not sec:
        return None
    return Entity("member", raw, metric, sec.name, bbox,
                  {"family": sec.family, "imperial_dims": list(sec.dims), "source_units": "imperial" if is_imperial else "metric"})


def _metric_name(family: str, dims: tuple[float, ...]) -> str:
    def f(v: float) -> str:
        return f"{v:g}"
    if family == "HSS_RECT":
        return "HSS" + "x".join(f(d) for d in dims)
    if family == "HSS_ROUND":
        return "HSS" + "x".join(f(d) for d in dims)
    return family + "x".join(f(d) for d in dims) if family == "L" else f"{family}{f(dims[0])}x{f(dims[1])}"


def _strip_stray_one(s: str) -> float:
    """No rectangular HSS side exceeds ~600 mm; a leading 1 is a misread leader line."""
    v = float(s)
    if v > 1000 and s.startswith("1"):
        v = float(s[1:])
    return v


def _bolt_fraction(s: str) -> float:
    """'3/4' -> 0.75, '1-1/8' -> 1.125. OCR reads 3/4 as 7/4; bolts over 1 1/2" are rare."""
    if "-" in s:
        whole, frac = s.split("-", 1)
        return float(whole) + _frac_or_num(frac)
    v = _frac_or_num(s)
    if v > 1.5 and s.startswith("7/"):
        v = _frac_or_num("3" + s[1:])
    return v


def _frac_or_num(s: str) -> float:
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b)
    return float(s)


def extract_line(text: str, bbox) -> list[Entity]:
    t = _normalise(text)
    out: list[Entity] = []
    claimed: list[tuple[int, int]] = []

    def take(m: re.Match) -> bool:
        span = m.span()
        if any(a < span[1] and span[0] < b for a, b in claimed):
            return False
        claimed.append(span)
        return True

    for m in RE_HSS_RECT.finditer(t):
        if take(m):
            dims = (_strip_stray_one(m.group(1)), _strip_stray_one(m.group(2)), _frac_or_num(m.group(3)))
            if e := _member("HSS_RECT", dims, m.group(0), bbox):
                out.append(e)
    for m in RE_HSS_ROUND.finditer(t):
        if take(m):
            d, wall = float(m.group(1)), _frac_or_num(m.group(2))
            if d > 1000:          # "HSS2190x9.5" is HSS219.0x9.5 with the point lost
                d /= 10
            if wall > d / 4:      # "HSS102x102": a rectangular callout cut short, not a pipe
                continue
            if e := _member("HSS_ROUND", (d, wall), m.group(0), bbox):
                out.append(e)
    for m in RE_ANGLE.finditer(t):
        if take(m):
            dims = (float(m.group(2)), float(m.group(3)), _frac_or_num(m.group(4)))
            if e := _member("L", dims, m.group(0), bbox):
                out.append(e)
    for m in RE_PLATE_THK.finditer(text):
        if not take(m):
            continue
        mm = float(_fix_digits(m.group(1)))
        label = re.sub(r"\s+", " ", m.group(2)).upper()
        out.append(Entity("plate", m.group(0), f"{mm:g} mm {label}", f"{fmt_fraction_in(mm_to_in(mm))} {label}", bbox,
                          {"label": label, "thickness_in": mm_to_in(mm), "length_in": None, "width_in": None,
                           "is_base_plate": label.startswith("BASE")}))
    for m in RE_PLATE.finditer(t):
        if not take(m):
            continue
        nums = [float(g) for g in m.groups()[1:] if g]
        if not nums or max(nums) < 3:
            continue
        thickness = min(nums)
        others = sorted((n for n in nums if n != thickness), reverse=True) or []
        imp = " x ".join(fmt_fraction_in(mm_to_in(n)) if n == thickness else fmt_length_mm(n) for n in nums)
        out.append(Entity("plate", m.group(0), " x ".join(f"{n:g}" for n in nums) + " mm", f"{m.group(1).upper()} {imp}", bbox,
                          {"label": m.group(1).upper(), "thickness_in": mm_to_in(thickness),
                           "length_in": mm_to_in(others[0]) if others else None,
                           "width_in": mm_to_in(others[1]) if len(others) > 1 else None,
                           "is_base_plate": bool(re.match(r"BP|BASE", m.group(1), re.I))}))
    for m in RE_DW.finditer(t):
        if take(m):
            dims = (float(m.group(2)), float(m.group(3)))
            if e := _member(m.group(1), dims, m.group(0), bbox):
                out.append(e)
    for m in RE_REBAR.finditer(t):
        if take(m):
            n = m.group(1)
            us = REBAR_M_TO_US[int(m.group(2))]
            out.append(Entity("rebar", m.group(0), m.group(0), f"{n + '-' if n else ''}{us}", bbox, {"count": int(n) if n else None}))
    for m in RE_BOLT_M.finditer(t):
        if take(m):
            d_in = BOLT_MM_TO_IN[int(m.group(2))]
            n = m.group(1)
            out.append(Entity("bolt", m.group(0), m.group(0), f'{n + "-" if n else ""}{fmt_fraction_in(d_in)} dia.', bbox,
                              {"diameter_in": d_in, "option": bolt_in_option(d_in), "count": int(n) if n else None}))
    for m in RE_BOLT_IN.finditer(t):
        if take(m):
            d_in = _bolt_fraction(m.group(2))
            n = m.group(1)
            grade = (RE_BOLT_GRADE.search(m.group(0)) or [""])[0].upper()
            out.append(Entity("bolt", m.group(0), m.group(0), f'{n + "-" if n else ""}{fmt_fraction_in(d_in)} dia. {grade}'.strip(), bbox,
                              {"diameter_in": d_in, "option": bolt_in_option(d_in), "count": int(n) if n else None}))
    for m in RE_BOLT_GRADE.finditer(t):
        out.append(Entity("bolt_grade", m.group(0), m.group(0).upper(), m.group(0).upper(), bbox))
    for m in RE_WELD.finditer(t):
        if take(m):
            mm = float(m.group(1))
            out.append(Entity("weld", m.group(0), f"{mm:g} mm", f"{fmt_fraction_in(mm_to_in(mm))} weld", bbox, {"size_in": mm_to_in(mm)}))
    for m in RE_LOAD.finditer(text):  # raw text: the digit repair below is load-specific
        if not take(m):
            continue
        try:
            v = float(m.group(2).translate(_LOAD_DIGITS))
        except ValueError:
            continue
        if v == 0:
            continue
        moment = m.group(3).lower().replace(" ", "").lstrip("k") not in ("kn", "n")
        label = (m.group(1) or "").upper()
        action = "moment" if moment else LOAD_ACTION.get(label.rstrip("FU"), LOAD_ACTION.get(label, "unknown"))
        factored = label.endswith(("F", "U")) and len(label) == 2
        if moment:
            imp, val = f"{v * KNM_TO_KIPFT:.2f} kip·ft", v * KNM_TO_KIPFT
        else:
            imp, val = f"{v * KN_TO_KIP:.2f} kip", v * KN_TO_KIP
        tag = (m.group(1) or "") + " = " if label else ""
        out.append(Entity("load", m.group(0), f"{tag}{v:g} {'kN·m' if moment else 'kN'}", f"{tag}{imp}", bbox,
                          {"label": label, "action": action, "factored": factored, "is_moment": moment,
                           "value_kn": v, "value_imperial": val}))
    for m in RE_STRESS.finditer(t):
        if take(m):
            v, u = float(m.group(1)), m.group(2)
            imp = f"{v * MPA_TO_KSI:.1f} ksi" if u.lower() == "mpa" else f"{v * KPA_TO_PSF:.0f} psf"
            out.append(Entity("stress", m.group(0), f"{v:g} {u}", imp, bbox))
    for m in RE_ELEV.finditer(t):
        if take(m):
            mm = float(m.group(1))
            out.append(Entity("elevation", m.group(0), f"EL {mm:g} mm", f"EL {fmt_length_mm(mm)}", bbox, {"mm": mm}))
    for m in RE_DIM_MM.finditer(t):
        if take(m):
            mm = float(m.group(1))
            out.append(Entity("dimension", m.group(0), f"{mm:g} mm", fmt_length_mm(mm), bbox, {"mm": mm}))
    if not out and (m := RE_DIM_BARE.match(t)):
        mm = float(m.group(1))
        if 10 <= mm <= 60000:
            out.append(Entity("dimension", m.group(0).strip(), f"{mm:g} mm", fmt_length_mm(mm), bbox, {"mm": mm}))
    for m in RE_TITLE.finditer(text):
        title = m.group(1).strip(" |")
        # "Connection to be Designed for Vf=..." is a load note, not a detail title.
        if len(title) > 6 and not re.search(r"design(ed)?\s+for|to\s+be", text, re.I):
            out.append(Entity("title", title, title, title, bbox))
    for m in RE_DWG.finditer(text):
        out.append(Entity("drawing_no", m.group(1), m.group(1), m.group(1), bbox))
    return out


def _overlap(a, b) -> bool:
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def extract_page(lines: list[dict]) -> list[dict]:
    """Entities for one OCR'd page, de-duplicated across OCR passes and the PDF text layer."""
    found: list[Entity] = []
    # PDF text first: it is exact, so it wins over an overlapping OCR read.
    for line in sorted(lines, key=lambda l: l["source"] != "pdf"):
        for e in extract_line(line["text"], line["bbox"]):
            if any(f.kind == e.kind and f.imperial == e.imperial and _overlap(f.bbox, e.bbox) for f in found):
                continue
            found.append(e)
    return [asdict(e) for e in found]
