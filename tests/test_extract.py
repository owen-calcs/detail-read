from detail_read.extract import extract_line

BOX = [0, 0, 1, 1]


def kinds(text):
    return {(e.kind, e.imperial) for e in extract_line(text, BOX)}


def test_wide_flange_with_ocr_noise():
    assert ("member", "W24X162") in kinds("Curved W610x24l")
    assert ("member", "W8X24") in kinds("Curved W200x36")


def test_base_plate_and_rebar():
    es = extract_line("BP6: 250x19x290 c/w", BOX)
    plate = next(e for e in es if e.kind == "plate")
    assert plate.data["is_base_plate"]
    assert abs(plate.data["thickness_in"] - 0.748) < 0.01
    assert plate.imperial == 'BP6 9 13/16" x 3/4" x 11 7/16"'
    assert ("rebar", "2-#5") in kinds("2-15M Weldable Bars 600mm Long")
    assert ("dimension", "1'-11 5/8\"") in kinds("2-15M Weldable Bars 600mm Long")


def test_hss_bolts_loads():
    assert ("member", "HSS6X6X.375") in kinds("HSS152x152x9.5")
    assert ("bolt", '4-3/4" dia.') in kinds("4-M20 A325")
    loads = [e for e in extract_line("R = 120 kN", BOX) if e.kind == "load"]
    assert loads and loads[0].imperial == "R = 26.98 kip"


def test_elevation_and_bare_dimension():
    assert ("elevation", "EL 64'-4 3/4\"") in kinds("EL: 19628")
    assert ("dimension", "19'-6 1/4\"") in kinds("5950")


def test_imperial_callout_kept():
    assert ("member", "W8X24") in kinds("W8x24")


def test_csa_factored_loads_with_ocr_noise():
    loads = [e for e in extract_line("Vertical VF=IG0KN, Horizontal", BOX) if e.kind == "load"]
    assert loads[0].data["value_kn"] == 160 and loads[0].data["action"] == "shear" and loads[0].data["factored"]
    t = [e for e in extract_line("Tf=400 kN", BOX) if e.kind == "load"][0]
    assert t.data["action"] == "tension" and t.imperial == "Tf = 89.92 kip"
    m = [e for e in extract_line("with Mf=5kN.m", BOX) if e.kind == "load"][0]
    assert m.data["is_moment"] and m.imperial == "Mf = 3.69 kip·ft"
    assert not [e for e in extract_line("0 kN", BOX) if e.kind == "load"]


def test_ocr_artifacts():
    assert ("member", "HSS6X6X.250") in kinds("HSS1152x152x6.4")
    assert not [e for e in extract_line("HSS102x102", BOX) if e.kind == "member"]
    assert ("bolt", '2-3/4" dia. A325') in kinds('2-7/4" A325')


def test_plate_thickness_callouts():
    p = [e for e in extract_line("lOmm THK Shear Tab c/w", BOX) if e.kind == "plate"][0]
    assert p.imperial == '3/8" SHEAR TAB' and not p.data["is_base_plate"]
    assert not [e for e in extract_line("lOmm THK Shear Tab c/w", BOX) if e.kind == "dimension"]
