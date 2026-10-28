"""Map metric steel section designations to the nearest AISC imperial section.

The AISC catalog is the section list the Calcs.com calculators accept
(data/sections_aisc.json), so a match here is always a valid enum option.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from importlib import resources

from .units import KGM_TO_LBFT, mm_to_in

# Families described by "<depth>X<weight>"
DEPTH_WEIGHT = ("W", "WT", "HP", "C", "MC", "S", "M", "MT", "ST")


@dataclass(frozen=True)
class Section:
    name: str          # catalog spelling, e.g. W8X24, HSS6X6X.375, L4X3X3/8
    family: str        # W, WT, HSS_RECT, HSS_ROUND, L, ...
    dims: tuple[float, ...]


def _num(s: str) -> float:
    """'3-1/2' -> 3.5, '1/4' -> 0.25, '.375' -> 0.375."""
    if "-" in s:
        whole, frac = s.split("-", 1)
        return float(whole) + float(Fraction(frac))
    if "/" in s:
        return float(Fraction(s))
    return float(s)


_NUM = r"(\d+(?:-\d+/\d+)?(?:/\d+)?|\d*\.\d+|\d+)"


def parse_catalog_name(name: str) -> Section | None:
    m = re.fullmatch(rf"({'|'.join(DEPTH_WEIGHT)})(\d+(?:\.\d+)?)X(\d+(?:\.\d+)?)", name)
    if m:
        return Section(name, m.group(1), (float(m.group(2)), float(m.group(3))))
    m = re.fullmatch(rf"HSS{_NUM}X{_NUM}X{_NUM}", name)
    if m:
        a, b, t = (_num(g) for g in m.groups())
        return Section(name, "HSS_RECT", (max(a, b), min(a, b), t))
    m = re.fullmatch(rf"HSS{_NUM}X{_NUM}", name)
    if m:
        return Section(name, "HSS_ROUND", tuple(_num(g) for g in m.groups()))
    m = re.fullmatch(rf"L{_NUM}X{_NUM}X{_NUM}", name)
    if m:
        a, b, t = (_num(g) for g in m.groups())
        return Section(name, "L", (max(a, b), min(a, b), t))
    return None


@lru_cache(maxsize=1)
def catalog() -> tuple[Section, ...]:
    names = json.loads(resources.files("detail_read.data").joinpath("sections_aisc.json").read_text())
    return tuple(s for s in (parse_catalog_name(n) for n in names) if s)


@lru_cache(maxsize=1)
def catalog_names() -> frozenset[str]:
    return frozenset(s.name for s in catalog())


def _nearest(family: str, target: tuple[float, ...], weights: tuple[float, ...]) -> Section | None:
    best, best_err = None, float("inf")
    for s in catalog():
        if s.family != family:
            continue
        err = sum(w * abs(a - b) / max(b, 1e-9) for w, a, b in zip(weights, s.dims, target))
        if err < best_err:
            best, best_err = s, err
    return best


def metric_to_imperial(family: str, dims_mm: tuple[float, ...]) -> Section | None:
    """Nearest AISC section for a metric designation.

    family is one of DEPTH_WEIGHT (dims = depth mm, mass kg/m), HSS_RECT
    (a mm, b mm, t mm), HSS_ROUND (d mm, t mm) or L (a mm, b mm, t mm).
    """
    if family in DEPTH_WEIGHT:
        depth_mm, kg_m = dims_mm
        # Depth must match first (W200 is a W8), then the closest weight.
        return _nearest(family, (mm_to_in(depth_mm), kg_m * KGM_TO_LBFT), (10.0, 1.0))
    if family in ("HSS_RECT", "L"):
        a, b, t = dims_mm
        a, b = max(a, b), min(a, b)
        return _nearest(family, (mm_to_in(a), mm_to_in(b), mm_to_in(t)), (4.0, 4.0, 1.0))
    if family == "HSS_ROUND":
        d, t = dims_mm
        return _nearest(family, (mm_to_in(d), mm_to_in(t)), (4.0, 1.0))
    return None


def imperial_lookup(name: str) -> Section | None:
    """Normalise an imperial designation found on a drawing (W8x24, HSS 6x6x3/8)."""
    n = re.sub(r"\s+", "", name.upper()).replace("×", "X")
    if n in catalog_names():
        return parse_catalog_name(n)
    sec = parse_catalog_name(n)
    if not sec:
        return None
    return _nearest(sec.family, sec.dims, (4.0,) * len(sec.dims))


def depth_in(sec: Section) -> float:
    """Overall size used to tell beam from column (bigger section = supporting member)."""
    return sec.dims[0]
