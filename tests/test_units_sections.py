from detail_read.sections import imperial_lookup, metric_to_imperial
from detail_read.units import bolt_in_option, fmt_fraction_in, fmt_ft_in, fmt_length_mm


def test_fractions():
    assert fmt_fraction_in(0.748) == '3/4"'
    assert fmt_fraction_in(2.5) == '2 1/2"'
    assert fmt_ft_in(234.25) == "19'-6 1/4\""
    assert fmt_length_mm(5950) == "19'-6 1/4\""
    assert fmt_length_mm(19) == '3/4"'


def test_bolt_option_spelling():
    assert bolt_in_option(0.75) == "3/4''"
    assert bolt_in_option(1.0) == "1''"
    assert bolt_in_option(1.125) == "1-1/8''"


def test_w_shapes():
    assert metric_to_imperial("W", (200, 36)).name == "W8X24"
    assert metric_to_imperial("W", (610, 241)).name == "W24X162"
    assert metric_to_imperial("W", (200, 27)).name == "W8X18"
    assert metric_to_imperial("W", (310, 39)).name == "W12X26"


def test_hss_and_angles():
    assert metric_to_imperial("HSS_RECT", (152, 152, 9.5)).name == "HSS6X6X.375"
    assert metric_to_imperial("HSS_RECT", (102, 203, 6.4)).name == "HSS8X4X.250"
    assert metric_to_imperial("HSS_ROUND", (168, 6.4)).name == "HSS6.625X.250"
    assert metric_to_imperial("L", (102, 76, 9.5)).name == "L4X3X3/8"


def test_imperial_lookup():
    assert imperial_lookup("W8x24").name == "W8X24"
    assert imperial_lookup("HSS 6x6x3/8").name == "HSS6X6X.375"
