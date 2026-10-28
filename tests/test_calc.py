from detail_read import calc

SECTIONS = ["W12X26", "W8X24", "HSS8X8X.375", "HSS6.625X.250"] + [f"X{i}" for i in range(100)]

DEFINITION = {
    "code": "steelConnectionWfBeamToHssAISC360-22",
    "fields": [
        {"referenceId": "member_beam", "label": "Beam Section", "type": "enum", "options": SECTIONS},
        {"referenceId": "member_column", "label": "Column Section", "type": "enum", "options": SECTIONS},
        {"referenceId": "member_column_r", "label": "Column Section", "type": "enum", "options": SECTIONS},
        {"referenceId": "col_type", "label": "Column Type", "type": "enum", "options": ["Square/Rectangular", "Round"]},
        {"referenceId": "t_p", "label": "Plate Thickness", "type": "number"},
        {"referenceId": "design_method", "label": "Design Method", "type": "enum", "options": ["ASD", "LRFD"]},
        {"referenceId": "factored_loads", "label": "Load Type", "type": "enum", "options": ["Individual Load Inputs", "Factored Loads"]},
        {"referenceId": "loadsConcFact", "label": "Vertical Load", "type": "table",
         "columns": [{"referenceId": "Label", "type": "input"},
                     {"referenceId": "V", "type": "object", "columns": [
                         {"referenceId": "load_type", "label": "Load Type", "type": "lookup"},
                         {"referenceId": "V", "label": "Vertical Load", "type": "input", "units": "kip"},
                         {"referenceId": "M", "label": "Moment Load", "type": "hidden", "units": "kip*ft"}]}],
         "defaultValue": [["Connection Loads", {"V": ["1 kip", 0]}, None]]},
        {"referenceId": "loadsConcColFact", "label": "Loads in the Column", "type": "table", "columns": [],
         "defaultValue": [["Connection Loads", {"P": ["1 kN", 0]}, None]]},
    ],
    "outputs": [
        {"referenceId": "V_u", "label": "Ultimate Shear Demand", "units": "kip"},
        {"referenceId": "V_a", "label": "Maximum Shear Demand", "units": "kip"},
        {"referenceId": "V_LC", "label": "Governing Load Combination for Shear", "units": None},
        {"referenceId": "phiR_n_bolt", "label": "Bolt Capacity in the Shear Connection", "units": "kip"},
    ],
    "presets": [{"code": "WT"}, {"code": "Single-Plate"}],
}


def test_build_inputs_maps_roles_plate_and_factored_loads():
    plate = {"metric": "10 mm SHEAR TAB", "imperial": '3/8" SHEAR TAB',
             "data": {"thickness_in": 10 / 25.4, "is_base_plate": False}}
    attrs, notes = calc.build_inputs(DEFINITION, {"beam": "W12X26", "column": "HSS6.625X.250"}, [plate], [], {"shear": 120})
    assert attrs["member_beam"] == "W12X26"
    assert attrs["member_column_r"] == "HSS6.625X.250" and attrs["col_type"] == "Round"
    assert attrs["t_p"] == "0.375 in"
    assert attrs["loadsConcFact"] == [["Connection Loads", {"V": ["26.977 kip", 0]}, None]]
    assert "loadsConcColFact" not in attrs
    assert attrs["factored_loads"] == "Factored Loads" and attrs["design_method"] == "LRFD"


def test_preset_defaults_to_single_plate():
    assert calc.pick_preset(DEFINITION, None) == "Single-Plate"
    assert calc.pick_preset(DEFINITION, "WT") == "WT"


def test_summarise_pairs_governing_combination_with_its_demand():
    result = {"results": {"V_u": 26.977, "V_a": 18.0, "V_LC": "1.2D + 1.6L", "phiR_n_bolt": 48.5},
              "inputs": {"design_method": {"value": "LRFD"}},
              "summaryCheck": 0.556, "summaryCheckStatus": "pass", "summaryCheckReferenceId": "phiR_n_bolt",
              "sheetUrl": "https://example/sheet"}
    s = calc.summarise(DEFINITION, result)
    assert s["governingLoad"]["combination"] == "1.2D + 1.6L"
    assert s["governingLoad"]["referenceId"] == "V_u" and s["governingLoad"]["value"] == 26.977
    assert s["utilization"] == 0.556 and s["status"] == "pass"
    assert s["governingCheck"]["label"] == "Bolt Capacity in the Shear Connection"


def test_summarise_ignores_required_geometry_outputs():
    definition = {"outputs": [
        {"referenceId": "c_req", "label": "Required Concrete Cover from Anchor Rods", "units": "in"},
        {"referenceId": "Dw", "label": "Governing Weld Demand", "units": "kip"},
    ]}
    s = calc.summarise(definition, {"results": {"c_req": 3.0, "Dw": 89.9}, "inputs": {}})
    assert s["governingLoad"]["referenceId"] == "Dw"
    assert [d["referenceId"] for d in s["demands"]] == ["Dw"]
