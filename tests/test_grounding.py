"""Run with:  python -m pytest tests  (or: python tests/test_grounding.py)
Each case is a wrong answer the matcher once produced, or must never produce."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import grounding as g


def T(*rows):
    head, *body = rows
    return "\n".join(["| " + " | ".join(head) + " |", "|" + " --- |" * len(head)] + ["| " + " | ".join(r) + " |" for r in body])


def match(table, q):
    text, values = g.table_text(table, q)
    lines = text.split("\n")
    row = lines[1] if lines[0].startswith("Row that matches") else None
    return row, values, text


TANK = T(["Tank diameter (m)", "Hold time (h)", "Settlement points"], ["Up to 15", "24", "8"], ["Above 15", "48", "16"])
BOLT = T(["Bolt size", "Flange rating", "Torque (Nm)", "Lubricant"], ["M16", "150#", "110", "Moly"], ["M20", "150#", "190", "Moly"],
         ["M20", "300#", "215", "Moly"], ["M24", "300#", "370", "Moly"])
SPAN = T(["Pipe size (inch)", "Maximum span (m)"], ["2", "3.0"], ["4", "4.3"], ["6", "5.2"], ["8", "5.8"])
INSUL = T(["Pipe size (inch)", "Up to 200 C (mm)", "201 to 400 C (mm)"], ["Up to 2", "40", "60"], ["3 to 8", "50", "80"], ["10 and above", "60", "100"])


def test_basic_rows():
    assert "Above 15" in match(TANK, "hold time for a tank above 15 m diameter")[0]
    assert "Above 15" in match(TANK, "For a 20 m diameter tank, how many settlement points")[0]
    assert "Up to 15" in match(TANK, "10 m diameter tank hold time")[0]
    assert "215" in match(BOLT, "What torque for M20 bolts on a 300# flange?")[0]
    assert match(SPAN, "span for a 4 inch pipe with a valve")[1] == [4.3]
    r = match(INSUL, "mineral wool thickness for a 6 inch pipe at 300 C")
    assert "Value that answers the question: 80" in r[2] and r[1] == [80.0]
    assert "Value that answers the question: 40" in match(INSUL, "thickness for a 2 inch pipe at 150 C")[2]


def test_value_cells_never_select_a_row():      # audit C1
    pwht = T(["Thickness (mm)", "PWHT"], ["Up to 19", "Not required"], ["Above 19", "Required"])
    assert "Up to 19" in match(pwht, "Is PWHT required for 12 mm thickness?")[0]


def test_whole_tokens_only():                    # audit C1
    cls = T(["Flange class", "Torque (Nm)"], ["Class 150", "110"], ["Class 300", "215"], ["Class 600", "400"])
    row, values, text = match(cls, "bolt torque for class 1500 flange")
    assert row is None and values == []
    svc = T(["Service", "Corrosion allowance (mm)"], ["Water", "1.5"], ["Crude oil", "3.0"])
    assert match(svc, "corrosion allowance for seawater service")[0] is None


def test_thousands_and_negative_numbers():       # audit C2
    p = T(["Design pressure (kPa)", "Test pressure (kPa)"], ["Up to 1000", "1500"], ["1001 to 5000", "7500"])
    assert "7500" in match(p, "test pressure for a design pressure of 3,000 kPa")[0]
    mat = T(["Design temperature (C)", "Material"], ["Up to 0", "LTCS A333"], ["1 to 400", "CS A106"])
    assert "LTCS" in match(mat, "pipe material at -20 C")[0]
    assert "LTCS" in match(mat, "pipe material at minus 29 degrees C")[0]


def test_strict_and_inclusive_operators():       # audit C3
    t = T(["Tank diameter (m)", "Hold time (h)"], ["< 15", "24"], ["≥ 15", "48"])
    assert "48" in match(t, "hold time for a 15 m diameter tank")[0]
    assert "24" in match(t, "hold time for a 14.9 m diameter tank")[0]


def test_units_must_bind():                      # audit C4
    wt = T(["DN", "Sch 40 (mm)", "Sch 80 (mm)"], ["50", "3.91", "5.54"], ["80", "5.49", "7.62"], ["100", "6.02", "8.56"])
    assert match(wt, "wall thickness of 2 inch schedule 80 pipe")[0] is None
    assert match(INSUL, "insulation for a 50 mm pipe at 150 C")[0] is None


def test_out_of_table_is_said():                 # audit H4
    assert "no row of this table covers" in match(SPAN, "maximum span for 12 inch pipe")[2]
    assert "no row of this table covers" in match(BOLT, "torque for M30 bolts on a 900# flange")[2]
    assert match(BOLT, "torque for M30 bolts")[0] is None


def test_long_tables_are_not_cut_mid_row():      # audit C5
    big = T(["NPS (inch)", "STD wall (mm)"], *[[str(n), "%.2f" % (n / 3)] for n in range(1, 31)])
    row, values, text = match(big, "wall thickness of 28 inch STD pipe")
    assert "NPS (inch): 28" in row and values == [9.33]
    assert text.count("\n") < 20 and "more rows not shown" in text


def test_number_check():                         # audit H2, C6
    src = "Hold 48 hours. Span 5.2 m. Pressure 1,500 kPa and 900 kPa. Minus 29 degrees C. Reduced by 25 percent. 4.3 reduced by 25 percent = 3.225"
    ok = lambda a: g.unverified_numbers(a, src)
    assert ok("The span is 5.2 m and the hold is 48 hours [1].") == []
    assert ok("Use 1500 kPa.") == [] and ok("Use 1,500 kPa.") == []
    assert ok("4.3 x 0.75 = 3.225 m, about 3.23 m.") == []
    assert ok("The maximum span is 5") == ["5"]              # a cut-off "5.2" is not a checked value
    assert ok("Use 1,900 kPa.") == ["1,900"]
    assert ok("The span is 6 m.") == ["6"]
    assert ok("Down to -29 C.") == [] and ok("Up to 29 C.") == ["29"]


def test_value_from_a_ruled_out_row_is_flagged():
    # Seen on a real PC: matcher picked "Up to 15 | 24", a half-read scan copy showed only "Above 15 | 48",
    # and the model answered 48 h. 48 is in the sources, so only this check can catch it.
    q = "How long is the hydrotest hold for a 10 m diameter tank?"
    src = "[1] Tank standard (table)\n" + g.table_text(TANK, q)[0] + "\n\n[3] Scan\nAbove15 48 16\n" + q
    flag = lambda a: g.other_row_numbers(a, src)
    assert flag("The hydrotest hold for a 10 m diameter tank is 48 hours [2].") == ["48"]
    assert flag("Hold it for 24 hours with 8 settlement points [1].") == []
    assert flag("Tanks up to 15 m are held for 24 hours [1].") == []      # 15 is in the matched row too
    assert flag("Use 16 settlement points.") == ["16"]
    # Seen on the real pipe guide: "OD of NPS 24?" - the matcher said no row covers NPS 24, the model quoted NPS 2's 60.3.
    q3 = "What is the OD of NPS 24 pipe?"
    src3 = g.table_text(SPAN, "span for 24 inch pipe")[0] + "\nNPS 2 pipe is 60 mm or 2.375 inch.\n" + q3
    assert src3.startswith("NOTE: no row")
    assert g.other_row_numbers("The span is 5.8 m [1].", src3) == ["5.8"]
    assert g.other_row_numbers("Not in the table; see the page [1].", src3) == []
    q2 = "Maximum span for 8 inch stainless steel pipe thinner than schedule 10S?"   # 10 is the user's, not row "10 and above"
    src2 = g.table_text(INSUL, "mineral wool for 8 inch pipe at 150 C")[0] + "\n" + q2
    assert g.other_row_numbers("Below schedule 10S use 80 percent [1].", src2) == []
    assert g.other_row_numbers("48 hours", "Table, one row per line:\n- Tank diameter (m): Above 15; Hold time (h): 48") == []


def test_coverage_gate():                        # audit H4
    passages = ["5.3 HYDROSTATIC TESTING The tank shall be filled with fresh water and held for the hold time in the table.",
                "Bolt size Flange rating Torque (Nm) M20 300# 215", "6.1 PIPE SUPPORT SPACING maximum span between supports",
                "7.1 INSULATION THICKNESS mineral wool", "ring wall level tolerance"]
    gate = lambda q: g.not_covered(q, g.missing_terms(q, passages))
    for q in ["holding time for hydrotest of a 20 m tank", "recommended bolting torque for M20 on 300# flanges",
              "Could you explain what our standard says about spacing of pipe supports?", "hydro test duration for big tanks"]:
        assert not gate(q), q
    for q in ["What is the design wind speed for flare stacks?", "minimum wall thickness for 6 inch schedule 40 pipe"]:
        assert gate(q), q


def test_tanglish_guard():                       # audit M8
    en = "The hold time for a tank above 15 m diameter is 48 hours [1]."
    assert g.tanglish_ok(en, "15 m diameter ku mela irukkura tank ku hold time 48 hours [1].")
    assert g.tanglish_ok("Span is 3.225 meters [1]. Reduced by 25% [1].", "Span 3.225 meters thaan [1]. 25% reduce aagum [1][1].")
    assert not g.tanglish_ok(en, "Tank ku hold time 48 hours illa [1], 15 m ku mela.")          # negation added
    assert not g.tanglish_ok(en, "48 m diameter ku mela irukkura tank ku hold time 15 hours [1].")  # values swapped
    assert not g.tanglish_ok(en, "Tank ku hold time 24 hours [1], 15 m ku mela.")                # value changed
    assert not g.tanglish_ok("It shall not be less than 5 degrees C [1].", "5 degrees C la irukkanum [1].")  # "not" dropped


def test_tidy():
    assert g.tidy("The span is 5.2 meters for a 6 inch pipe [1].\n\nThe documents do not cover this.") == "The span is 5.2 meters for a 6 inch pipe [1]."
    assert g.tidy("For titanium: The documents do not cover this.") == "For titanium: The documents do not cover this."


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print("ok  ", name)
            except AssertionError as e:
                failed += 1; print("FAIL", name, "|", e)
    sys.exit(1 if failed else 0)


SIDEWAYS = """| NPS | ½ | ¾ | 1 | 1 ½ | 2 | 3 | 4 | 6 | 8 | 10 | 12 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| NB/DN | 15 | 20 | 25 | 40 | 50 | 80 | 100 | 150 | 200 | 250 | 300 |
| OD in mm | 21.3 | 26.7 | 33.4 | 48.3 | 60.3 | 88.9 | 114 | 168 | 219 | 273 | 324 |"""

OD_TOL = """| Nominal Pipe Size NPS | Nominal Pipe Size DN | Carbon Steel ASTM A53M | Carbon Steel ASTM A106M |
| --- | --- | --- | --- |
| ⅛to 1 ½ | 6 to 40 | ±0.4 mm | ±0.4 mm |
| Over 1½ to 4 | Over 40 to 100 | ±1% | ±0.8 mm |
| Over 4 to 8 | Over 100 to 200 | ±1% | +1.6 / -0.8 mm |"""


def test_table_on_its_side_and_fraction_sizes():
    row = lambda q: g.table_text(SIDEWAYS, q)[0].splitlines()[1]
    assert row("OD of NPS 4 pipe") == "- NPS: 4; NB/DN: 100; OD in mm: 114"
    assert row("outside diameter for 1 1/2 inch pipe") == "- NPS: 1 ½; NB/DN: 40; OD in mm: 48.3"
    assert row('OD of 3/4" pipe') == "- NPS: ¾; NB/DN: 20; OD in mm: 26.7"
    assert row("which NPS is DN 80") == "- NPS: 3; NB/DN: 80; OD in mm: 88.9"
    assert g.table_text(SIDEWAYS, "OD of NPS 24 pipe")[0].startswith("NOTE: no row")
    assert g.table_text(SIDEWAYS, "what is OD")[0].startswith("Table, one row per line")


def test_over_x_to_y_ranges():
    row = lambda q: g.table_text(OD_TOL, q)[0].splitlines()[1]
    assert "Over 4 to 8" in row("OD tolerance for NPS 6 pipe as per A106M")
    assert "Over 1½ to 4" in row("OD tolerance for NPS 4 pipe")  # 4 belongs to "over 1½ to 4", not "over 4 to 8"
    assert "⅛to 1 ½" in row("OD tolerance for 1 inch pipe")
    assert g.table_text(OD_TOL, "OD tolerance for NPS 30 pipe")[0].startswith("NOTE: no row")


def test_quantity_of_an_unknown_thing_is_not_covered():
    q = "what is the design pressure of a pig launcher"
    assert g.not_covered(q, ["pig", "launcher"])
    assert not g.not_covered("what is the design pressure of the launcher", ["launcher"])
