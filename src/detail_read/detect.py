"""Find connection details on a page and decide which Calcs.com calculator fits each.

A shop drawing page usually holds several details. Design-load notes
("Connection to be Designed for Vf=80kN") sit next to the connection they
describe, so each group of load notes seeds a detail, and the members, plates
and bolts closest to it make up that detail. A page without load notes is
treated as one detail.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# Distance (in page-normalised units) within which callouts belong to one detail.
DETAIL_RADIUS = 0.16
LOAD_GROUP_RADIUS = 0.06

KEYWORDS = {
    "shear_tab": r"shear\s*tab|fin\s*plate|single\s*plate",
    "angle": r"clip\s*angle|double\s*angle|\b2L\b",
    "seat": r"\bseat",
    "end_plate": r"end\s*plate|end\s*pl\b",
    "knife": r"knife",
    "moment": r"\bmoment\b|flange\s*plate|\bMf\b|\bmf=",
    "brace": r"\bbrac(e|ing)\b|gusset",
    "chevron": r"chevron|inverted\s*v",
    "truss": r"\btruss\b|\bchord\b|\bbranch\b|\bweb\s*member",
    "splice": r"splice",
    "girt": r"\bgirt\b",
    "girder": r"\bgirder\b",
    "base": r"base\s*pl|\bbp\d|anchor|grout",
    "cap": r"cap\s*pl",
    "through": r"through\s*pl",
}


@dataclass
class Candidate:
    code: str
    name: str
    score: float
    reasons: list[str]
    roles: dict[str, str]          # role -> imperial section name (beam, column, chord, branch, brace)
    preset_hint: str | None = None


@dataclass
class Detail:
    id: str
    title: str
    bbox: list[float]
    members: list[dict]
    plates: list[dict]
    bolts: list[dict]
    loads: list[dict]
    keywords: list[str]
    candidates: list[Candidate] = field(default_factory=list)


def _dist(a, b, aspect: float) -> float:
    """Gap between two boxes (0 if they overlap). Centre distance would penalise long callout lines."""
    dx = max(b[0] - a[2], a[0] - b[2], 0.0)
    dy = max(b[1] - a[3], a[1] - b[3], 0.0)
    return math.hypot(dx * aspect, dy)


def _keywords(lines: list[dict]) -> set[str]:
    text = " ".join(l["text"] for l in lines)
    return {k for k, pat in KEYWORDS.items() if re.search(pat, text, re.I)}


def _union(boxes) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def find_details(entities: list[dict], lines: list[dict], page_size: tuple[int, int]) -> list[Detail]:
    aspect = page_size[0] / page_size[1]
    loads = [e for e in entities if e["kind"] == "load"]
    callouts = [e for e in entities if e["kind"] in ("member", "plate", "bolt")]
    titles = [e for e in entities if e["kind"] == "title"]

    # Group load notes that sit together (one note often spans several lines).
    groups: list[list[dict]] = []
    for ld in loads:
        for g in groups:
            if any(_dist(ld["bbox"], o["bbox"], aspect) < LOAD_GROUP_RADIUS for o in g):
                g.append(ld)
                break
        else:
            groups.append([ld])

    details: list[Detail] = []
    for i, g in enumerate(groups):
        seed = _union([e["bbox"] for e in g])
        near = sorted((c for c in callouts if _dist(c["bbox"], seed, aspect) < DETAIL_RADIUS),
                      key=lambda c: _dist(c["bbox"], seed, aspect))
        if not any(c["kind"] == "member" for c in near):
            continue
        near_lines = [l for l in lines if _dist(l["bbox"], seed, aspect) < DETAIL_RADIUS]
        title = min(titles, key=lambda t: _dist(t["bbox"], seed, aspect))["raw"] if titles else f"Detail {i + 1}"
        details.append(_make_detail(f"d{i + 1}", title, [seed] + [c["bbox"] for c in near], near, g, _keywords(near_lines)))

    if not details and any(c["kind"] == "member" for c in callouts):
        title = titles[0]["raw"] if titles else "Whole page"
        details.append(_make_detail("page", title, [c["bbox"] for c in callouts], callouts, loads, _keywords(lines)))

    for d in details:
        d.candidates = rank_calculators(d)
    return details


def _make_detail(did, title, boxes, near, loads, keywords) -> Detail:
    def uniq(kind):
        seen, out = set(), []
        for c in near:
            if c["kind"] == kind and c["imperial"] not in seen:
                seen.add(c["imperial"])
                out.append(c)
        return out
    return Detail(did, title, _union(boxes), uniq("member"), uniq("plate"), uniq("bolt"), loads, sorted(keywords))


def _by_size(members: list[dict], family_prefix: str) -> list[dict]:
    ms = [m for m in members if m["data"]["family"].startswith(family_prefix)]
    return sorted(ms, key=lambda m: m["data"]["imperial_dims"][0], reverse=True)


def rank_calculators(d: Detail) -> list[Candidate]:
    kw = set(d.keywords)
    W = _by_size(d.members, "W")
    W = [m for m in W if m["data"]["family"] == "W"]
    HSS = _by_size(d.members, "HSS")
    rect = [m for m in HSS if m["data"]["family"] == "HSS_RECT"]
    has_base = "base" in kw or any(p["data"]["is_base_plate"] for p in d.plates)
    actions = {l["data"]["action"] for l in d.loads}
    axial_only = actions and actions <= {"tension", "compression"}
    cands: list[Candidate] = []

    def add(code, name, score, reasons, roles, preset=None):
        cands.append(Candidate(code, name, score, reasons, roles, preset))

    shear_preset = ("Single-Plate" if "shear_tab" in kw else "Double Angle" if "angle" in kw
                    else "Unstiffened Seated" if "seat" in kw else "Through-Plate" if "through" in kw else None)

    if "truss" in kw and len(HSS) >= 2:
        add("steelConnectionHssTrussAISC360-22", "HSS Truss Connection", 0.9,
            ["chord/branch wording", f"{len(HSS)} HSS members"],
            {"chord": HSS[0]["imperial"], "branch": HSS[1]["imperial"]})
    if "brace" in kw and HSS:
        code = "steelConnectionChevronBracingAISC360-22" if "chevron" in kw else "steelConnectionColumnBracingAISC360-22"
        roles = {"brace": HSS[-1]["imperial"]}
        if W:
            roles["beam"] = W[-1]["imperial"]
        add(code, "Bracing Connection", 0.85, ["brace/gusset wording"], roles,
            "Bolted Knife Plate" if "knife" in kw else None)
    if "splice" in kw and HSS:
        add("steelConnectionHssToHssSpliceColumnAISC360-22", "HSS to HSS Splice Connection", 0.85,
            ["splice wording"], {"column": HSS[0]["imperial"]})
    if has_base and HSS:
        add("steelConnectionHssBasePlateAISC360-22", "HSS Steel Base Plate", 0.85 if not W else 0.7,
            ["base plate callout", f"column {HSS[0]['metric']}"], {"column": HSS[0]["imperial"]})
    if "girt" in kw and HSS:
        if W:
            add("steelConnectionHssGirtToWfAISC360-22", "HSS Girt to WF Column", 0.8, ["girt wording"],
                {"beam": HSS[-1]["imperial"], "column": W[0]["imperial"]})
        elif len(HSS) >= 2:
            add("steelConnectionHssGirtToHssAISC360-22", "HSS Girt to HSS Column", 0.8, ["girt wording"],
                {"beam": HSS[-1]["imperial"], "column": HSS[0]["imperial"]})

    moment = "moment" in kw
    if W and HSS:
        code = "steelConnectionWfBeamToHssMomentAISC360-22" if moment else "steelConnectionWfBeamToHssAISC360-22"
        add(code, f"WF to HSS {'Moment' if moment else 'Shear'} Connection", 0.75 + (0.1 if "shear_tab" in kw else 0),
            [f"WF beam {W[-1]['metric']}", f"HSS {HSS[0]['metric']}"],
            {"beam": W[-1]["imperial"], "column": HSS[0]["imperial"]}, None if moment else shear_preset)
        code = "steelConnectionHssBeamToWFMomentAISC360-22" if moment else "steelConnectionHSSBeamToWfAISC360-22"
        add(code, f"HSS to WF {'Moment' if moment else 'Shear'} Connection", 0.55,
            [f"HSS beam {HSS[-1]['metric']}", f"WF {W[0]['metric']}"],
            {"beam": HSS[-1]["imperial"], "column": W[0]["imperial"]})
    if len(HSS) >= 2:
        beam, col = HSS[-1], HSS[0]
        if "girder" in kw:
            add("steelConnectionHssBeamToHssGirderShearAISC360-22", "HSS Beam to HSS Girder Shear Connection", 0.8,
                ["girder wording"], {"beam": beam["imperial"], "girder": col["imperial"]})
        code = "steelConnectionHssBeamToHssMomentAISC360-22" if moment else "steelConnectionHssBeamToHssShearAISC360-22"
        add(code, f"HSS to HSS {'Moment' if moment else 'Shear'} Connection", 0.7 + (0.1 if "shear_tab" in kw else 0) - (0.2 if axial_only else 0),
            [f"HSS beam {beam['metric']}", f"HSS column {col['metric']}"],
            {"beam": beam["imperial"], "column": col["imperial"]},
            None if moment else ("single_plate" if "shear_tab" in kw else None))
        if axial_only:
            add("steelConnectionHssTrussAISC360-22", "HSS Truss Connection", 0.65, ["axial-only design loads between HSS"],
                {"chord": col["imperial"], "branch": beam["imperial"]})
    if W and not HSS:
        # connectionshub has no WF-to-WF calculator: use WF beam to HSS support as the nearest fit.
        add("steelConnectionWfBeamToHssAISC360-22", "WF to HSS Shear Connection", 0.4,
            [f"WF beam {W[-1]['metric']}", "no HSS support found; support uses calculator default"],
            {"beam": W[-1]["imperial"]}, shear_preset)
    if rect and not cands:
        add("steelConnectionHssBasePlateAISC360-22", "HSS Steel Base Plate", 0.3, ["single HSS member"],
            {"column": rect[0]["imperial"]})

    cands.sort(key=lambda c: c.score, reverse=True)
    seen, out = set(), []
    for c in cands:
        if c.code not in seen:
            seen.add(c.code)
            out.append(c)
    return out
