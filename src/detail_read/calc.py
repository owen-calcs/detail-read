"""Turn a detected detail into Calcs.com calculator inputs, solve it, and report the governing load."""

from __future__ import annotations

import re
from typing import Any

from . import mcp_client
from .units import KN_TO_KIP, KNM_TO_KIPFT

# Role -> calculator section-field label words, most specific first.
ROLE_LABELS = {
    "beam": ("girt section", "beam section"),
    "column": ("column section designation", "column section", "square/rectangular hss section", "hss section"),
    "girder": ("girder section",),
    "chord": ("chord section",),
    "branch": ("first branch section", "branch section"),
    "brace": ("brace section",),
}
# Sign the connection calculators use for a tensile axial load (confirmed against a live solve).
AXIAL_SIGN = 1


def _section_fields(definition: dict) -> list[dict]:
    return [f for f in definition["fields"] if f["type"] == "enum" and len(f.get("options") or []) > 100]


def _field(definition: dict, ref: str) -> dict | None:
    return next((f for f in definition["fields"] if f["referenceId"] == ref), None)


def _assign_sections(definition: dict, roles: dict[str, str], attrs: dict, notes: list[str]) -> None:
    fields = _section_fields(definition)
    for role, section in roles.items():
        if not section:
            continue
        is_round = re.fullmatch(r"HSS[\d.]+X[\d.]+", section) is not None
        targets = []
        for label in ROLE_LABELS.get(role, ()):
            for f in fields:
                if f["label"].lower() != label:
                    continue
                round_field = f["referenceId"].endswith("_r") or "round" in f["label"].lower()
                if section in f["options"] and round_field == is_round:
                    targets.append(f)
            if targets:
                break
        if not targets:
            notes.append(f"No {role} field accepts {section}; left at the calculator default.")
            continue
        f = targets[0]
        attrs[f["referenceId"]] = section
        col_type = _field(definition, "col_type")
        if role == "column" and col_type and f["referenceId"].startswith("member_column"):
            attrs["col_type"] = "Round" if is_round else "Square/Rectangular"


def _set_plate(definition: dict, plates: list[dict], attrs: dict, notes: list[str]) -> None:
    if not plates or not _field(definition, "t_p"):
        return
    is_base_calc = "BasePlate" in definition["code"]
    plate = next((p for p in plates if p["data"]["is_base_plate"] == is_base_calc), None)
    if not plate:
        return
    d = plate["data"]
    # Imperial plate is ordered in 1/16" thicknesses and 1/8" widths: 10 mm -> 3/8", not 0.394".
    attrs["t_p"] = f"{round(d['thickness_in'] * 16) / 16:g} in"
    if is_base_calc and d.get("length_in") and d.get("width_in"):
        attrs["N"] = f"{round(d['length_in'] * 8) / 8:g} in"
        attrs["B"] = f"{round(d['width_in'] * 8) / 8:g} in"
    notes.append(f"Plate from drawing: {plate['metric']} -> {plate['imperial']}.")


def _set_bolts(definition: dict, bolts: list[dict], attrs: dict) -> None:
    f = _field(definition, "size_bolt")
    if f and bolts:
        opt = bolts[0]["data"]["option"]
        if opt in (f.get("options") or []):
            attrs["size_bolt"] = opt


def _load_tables(definition: dict) -> list[dict]:
    """The factored-load tables for the connection itself.

    Which one a calculator reads depends on the connection type it is set to
    (a shear-only table or a shear + axial one), so all of them are filled.
    Column, second-beam and Y-axis tables are left alone. On a truss only the
    first branch is loaded.
    """
    tables = [f for f in definition["fields"] if f["type"] == "table" and f["referenceId"].endswith("Fact")
              and f.get("defaultValue") and not re.search(r"Col|_y|2Fact$|_2", f["referenceId"])]
    branch = [f for f in tables if f["label"].lower().endswith("of the branch")]
    return branch or tables


def _load_subcolumns(table: dict) -> list[dict]:
    for c in table.get("columns") or []:
        if c["type"] == "object":
            return [s for s in c["columns"] if s["referenceId"] != "load_type"]
    return []


def loads_from_detail(loads: list[dict]) -> dict[str, float]:
    """Sum the factored design loads on a detail, in kN / kN·m, keyed by action."""
    out: dict[str, float] = {}
    for ld in loads:
        a = ld["data"]["action"]
        if a == "unknown":
            a = "shear"  # an unlabeled force next to a connection is almost always its reaction
        out[a] = max(out.get(a, 0.0), ld["data"]["value_kn"])
    return out


def _set_loads(definition: dict, loads_kn: dict[str, float], attrs: dict, notes: list[str]) -> None:
    if not loads_kn or not _field(definition, "factored_loads"):
        return
    tables = _load_tables(definition)
    if not tables:
        notes.append("Calculator has no factored load table; loads left at defaults.")
        return
    for table in tables:
        attrs[table["referenceId"]] = [_load_row(table, loads_kn)]
    attrs["factored_loads"] = "Factored Loads"
    if _field(definition, "design_method"):
        attrs["design_method"] = "LRFD"
    notes.append("Factored loads (CSA Vf/Tf/Cf/Mf) applied as LRFD factored loads in: "
                 + ", ".join(f"'{t['label']}'" for t in tables) + ".")


def _load_row(table: dict, loads_kn: dict[str, float]) -> list:
    axial_kn = loads_kn.get("tension", 0.0) - loads_kn.get("compression", 0.0) + loads_kn.get("horizontal", 0.0)
    subs = _load_subcolumns(table)
    has_lateral = any("lateral" in s["label"].lower() for s in subs)
    row = list(table["defaultValue"][0])
    obj_idx = next(i for i, v in enumerate(row) if isinstance(v, dict))
    key, old = next(iter(row[obj_idx].items()))
    values = list(old)
    for i, s in enumerate(subs):
        if s["type"] != "input" or i >= len(values):
            continue
        label = s["label"].lower()
        kip = None
        if "moment" in label:
            kip = loads_kn.get("moment", 0.0) * KNM_TO_KIPFT
        elif "lateral" in label:
            kip = (loads_kn.get("shear", 0.0) or loads_kn.get("horizontal", 0.0)) * KN_TO_KIP
        elif "axial" in label:
            kip = axial_kn * AXIAL_SIGN * KN_TO_KIP
        elif "vertical" in label and has_lateral:
            # Column-type calculators (base plates): the vertical load is the axial force, compression positive.
            kip = (loads_kn.get("compression", 0.0) - loads_kn.get("tension", 0.0)) * KN_TO_KIP
        elif "vertical" in label or "shear" in label:
            kip = loads_kn.get("shear", 0.0) * KN_TO_KIP
        if kip is not None:
            values[i] = f"{kip:.3f} {s['units']}" if kip else 0
    row[obj_idx] = {key: values}
    return row


def build_inputs(definition: dict, roles: dict[str, str], plates: list[dict], bolts: list[dict],
                 loads_kn: dict[str, float]) -> tuple[dict, list[str]]:
    attrs: dict[str, Any] = {}
    notes: list[str] = []
    _assign_sections(definition, roles, attrs, notes)
    _set_plate(definition, plates, attrs, notes)
    _set_bolts(definition, bolts, attrs)
    _set_loads(definition, loads_kn, attrs, notes)
    return attrs, notes


def pick_preset(definition: dict, hint: str | None) -> str | None:
    presets = definition.get("presets") or []
    codes = [p["code"] for p in presets]
    if hint and hint in codes:
        return hint
    # A single shear plate is the most common simple connection; use it when the drawing doesn't say.
    single = next((c for c in codes if re.search(r"single.?plate", c, re.I)), None)
    return single or (codes[0] if codes else None)


async def validate_and_solve(s, definition: dict, project_id: str, preset: str | None, attrs: dict,
                             name: str, notes: list[str]) -> dict:
    code = definition["code"]
    for _ in range(3):
        v = await mcp_client.call(s, "validate_inputs", code=code, attributes=attrs, **({"preset": preset} if preset else {}))
        bad = set(_rejected_fields(v))
        for w in (v.get("warnings") or []) if isinstance(v, dict) else []:
            ref = w.get("field") or w.get("referenceId") if isinstance(w, dict) else None
            if ref:
                bad.add(ref)
        bad &= set(attrs)
        if not bad:
            break
        for ref in bad:
            notes.append(f"Calculator rejected {ref}={attrs.pop(ref)!r}; using its default.")
    args = {"code": code, "projectId": project_id, "attributes": attrs, "name": name}
    if preset:
        args["preset"] = preset
    return await mcp_client.call(s, "create_and_solve_calculation", **args)


def _rejected_fields(v: Any) -> list[str]:
    if not isinstance(v, dict) or v.get("valid", True):
        return []
    out = []
    for e in v.get("errors") or v.get("fieldErrors") or []:
        if isinstance(e, dict):
            ref = e.get("field") or e.get("referenceId")
            if ref:
                out.append(ref)
    return out


def summarise(definition: dict, result: dict) -> dict:
    """Pull the governing load combination, its demand and the capacities out of a solve."""
    outputs = {o["referenceId"]: o for o in definition["outputs"]}
    res = result.get("results") or {}
    inputs = {k: (v.get("value") if isinstance(v, dict) and "value" in v else v)
              for k, v in (result.get("inputs") or {}).items()}
    method = str(inputs.get("design_method") or "LRFD")
    lrfd = "LRFD" in method.upper()

    def item(ref):
        o = outputs.get(ref, {})
        return {"referenceId": ref, "label": o.get("label") or ref, "value": res.get(ref), "units": o.get("units")}

    numeric = {r: v for r, v in res.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and r in outputs}

    def is_demand(r: str) -> bool:
        label = outputs[r].get("label") or ""
        return ("Demand" in label or label.startswith("Required")) and outputs[r].get("units") is not None

    governing = []
    for ref, o in outputs.items():
        label = o.get("label") or ""
        if not label.startswith("Governing Load Combination") or not res.get(ref):
            continue
        # V_LC pairs with V_u / V_a; P_LC_b with P_u_b / P_a_b.
        prefix, _, suffix = ref.partition("_LC")
        demands = [r for r in numeric if is_demand(r) and re.match(rf"{re.escape(prefix)}_[ua](_|$)", r)
                   and (not suffix or r.endswith(suffix))]
        pref = [r for r in demands if (re.match(rf"{re.escape(prefix)}_u", r) is not None) == lrfd] or demands
        governing.append({"combination": res[ref], "label": label.replace("Governing Load Combination", "").strip(" -") or "overall",
                          "referenceId": ref, "demands": [item(r) for r in pref]})

    demands = [item(r) for r in numeric if is_demand(r) and numeric[r]
               and ((r.split("_")[1:2] == ["u"]) == lrfd or r.split("_")[1:2] not in (["u"], ["a"]))]
    capacities = [item(r) for r in numeric if "Capacity" in (outputs[r].get("label") or "")
                  and (r.startswith("phi") == lrfd or not (r.startswith("phi") or "Omega" in r))]
    utilizations = sorted((item(r) for r in numeric if "Utilization" in (outputs[r].get("label") or "") and numeric[r]),
                          key=lambda u: u["value"], reverse=True)

    head = None
    for g in governing:
        nonzero = [d for d in g["demands"] if d["value"]]
        if nonzero:
            top = max(nonzero, key=lambda d: abs(d["value"]))
            head = {"combination": g["combination"], "for": g["label"], **top}
            break
    if head is None and demands:
        top = max(demands, key=lambda d: abs(d["value"]))
        head = {"combination": None, "for": top["label"], **top}

    gov_ref = result.get("summaryCheckReferenceId")
    check = result.get("summaryCheck")
    ratio = check if isinstance(check, (int, float)) and not isinstance(check, bool) else None
    if ratio is None and utilizations:
        ratio = utilizations[0]["value"]
    return {
        "status": result.get("summaryCheckStatus") or ("pass" if check is True else "unknown"),
        "summaryCheck": check,
        "utilization": ratio,
        "utilizations": utilizations[:8],
        "governingCheck": item(gov_ref) if gov_ref else None,
        "failReasons": result.get("summaryCheckFailReasons") or [],
        "designMethod": method,
        "governingLoad": head,
        "governingCombinations": governing,
        "demands": demands,
        "capacities": sorted(capacities, key=lambda c: c["value"])[:12],
        "sheetUrl": result.get("sheetUrl"),
        "sheetId": result.get("sheetId"),
        "inputs": {k: inputs.get(k) for k in inputs if k.startswith(("member", "section", "t_p", "size_bolt", "design_method", "factored", "loads", "col_type", "N", "B"))},
    }
