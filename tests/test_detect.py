from detail_read.detect import find_details
from detail_read.extract import extract_line


def _page(lines):
    """Stack callout lines down a landscape page the way a detail note block reads."""
    out = []
    for i, text in enumerate(lines):
        y = 0.36 + 0.033 * i
        out.append({"text": text, "bbox": [0.343, y, 0.343 + 0.012 * len(text), y + 0.024], "source": "pdf"})
    entities = []
    for l in out:
        entities += [e.__dict__ for e in extract_line(l["text"], l["bbox"])]
    return entities, out


def test_long_note_lines_still_group_with_their_members():
    entities, lines = _page(["W310x39 BEAM", "HSS203x203x9.5 COL.", "10mm SHEAR TAB c/w 3-M20 A325 BOLTS",
                             "CONNECTION TO BE DESIGNED FOR Vf=120 kN"])
    [detail] = find_details(entities, lines, (3400, 2200))
    best = detail.candidates[0]
    assert best.code == "steelConnectionWfBeamToHssAISC360-22"
    assert best.roles == {"beam": "W12X26", "column": "HSS8X8X.375"}
    assert best.preset_hint == "Single-Plate"
