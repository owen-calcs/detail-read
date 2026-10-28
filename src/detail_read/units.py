"""Metric -> imperial conversions and formatting used throughout the app."""

from __future__ import annotations

from fractions import Fraction

MM_PER_IN = 25.4
KN_TO_KIP = 0.224809
KNM_TO_KIPFT = 0.737562
KPA_TO_PSF = 20.8854
MPA_TO_KSI = 0.145038
KGM_TO_LBFT = 0.671969

# CSA metric bolt sizes -> nearest ASTM imperial bolt diameter (in)
BOLT_MM_TO_IN = {12: 0.5, 16: 0.625, 20: 0.75, 22: 0.875, 24: 1.0, 27: 1.125, 30: 1.25, 33: 1.25, 36: 1.5}

# CSA G30.18 metric rebar -> nearest ASTM A615 bar
REBAR_M_TO_US = {10: "#3", 15: "#5", 20: "#6", 25: "#8", 30: "#9", 35: "#11", 45: "#14", 55: "#18"}


def mm_to_in(mm: float) -> float:
    return mm / MM_PER_IN


def fmt_fraction_in(inches: float, denom: int = 16) -> str:
    """0.748 -> '3/4"', 2.5 -> '2 1/2"'."""
    sign = "-" if inches < 0 else ""
    f = Fraction(round(abs(inches) * denom), denom)
    whole, rem = divmod(f.numerator, f.denominator)
    rem = Fraction(rem, f.denominator)
    if whole and rem:
        return f'{sign}{whole} {rem}"'
    if rem:
        return f'{sign}{rem}"'
    return f'{sign}{whole}"'


def fmt_ft_in(inches: float, denom: int = 16) -> str:
    """234.25 -> 19'-6 1/4"."""
    total = round(inches * denom) / denom
    ft, rem = divmod(total, 12)
    ft = int(ft)
    if ft == 0:
        return fmt_fraction_in(rem, denom)
    return f"{ft}'-{fmt_fraction_in(rem, denom)}"


def fmt_length_mm(mm: float) -> str:
    """Lengths above a foot read as ft-in, smaller ones as fractional inches."""
    inches = mm_to_in(mm)
    return fmt_ft_in(inches) if inches >= 12 else fmt_fraction_in(inches)


def bolt_in_option(diam_in: float) -> str:
    """Format a bolt diameter the way Calcs.com options spell it: 3/4''  1''  1-1/8''."""
    f = Fraction(diam_in).limit_denominator(16)
    whole, rem = divmod(f.numerator, f.denominator)
    if whole and rem:
        return f"{whole}-{Fraction(rem, f.denominator)}''"
    if rem:
        return f"{Fraction(rem, f.denominator)}''"
    return f"{whole}''"
