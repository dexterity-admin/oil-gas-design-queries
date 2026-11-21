"""Builds a small synthetic library (made-up company standards) for model testing."""
import sys
from pathlib import Path

import docx
import openpyxl
import pymupdf

out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)


def pdf(name, pages):
    d = pymupdf.open()
    for title, paras, table in pages:
        p = d.new_page(); y = 70
        p.insert_text((60, y), title, fontsize=14); y += 20
        for t in paras:
            p.insert_textbox(pymupdf.Rect(60, y, 540, y + 70), t, fontsize=10.5); y += 78
        if table:
            n = len(table[0]); xs = [60 + i * 480 / n for i in range(n + 1)]; ys = [y + i * 24 for i in range(len(table) + 1)]
            for x in xs: p.draw_line((x, ys[0]), (x, ys[-1]))
            for yy in ys: p.draw_line((xs[0], yy), (xs[-1], yy))
            for r, row in enumerate(table):
                for c, t in enumerate(row): p.insert_text((xs[c] + 5, ys[r] + 16), t, fontsize=9.5)
    d.save(out / name)


pdf("DDS-ST-001 Tank Design Basis.pdf", [
    ("4.2 CORROSION ALLOWANCE", [
        "4.2.1 The minimum corrosion allowance for carbon steel tank shells in crude oil service shall be 3.0 mm. For tank bottoms the corrosion allowance shall be 1.5 mm unless a bottom lining is applied.",
        "4.2.2 Roof plates shall have a minimum nominal thickness of 5 mm excluding corrosion allowance. Stainless steel tanks do not require a corrosion allowance.",
        "4.2.3 Nozzle necks shall have the same corrosion allowance as the shell course to which they are attached."], None),
    ("5.3 HYDROSTATIC TESTING", [
        "5.3.1 All storage tanks shall be hydrostatically tested after completion of erection. The tank shall be filled with fresh water to the maximum design liquid level and held for the time given in the table below. The test water temperature shall not be less than 5 degrees C.",
        "5.3.2 Settlement measurements shall be taken at equally spaced points around the tank shell before filling, at 50 percent fill, at 100 percent fill and after emptying. The number of points is given in the table below.",
        "5.3.3 Chloride content of the test water shall not exceed 50 ppm for stainless steel tanks."],
     [["Tank diameter (m)", "Hold time (h)", "Settlement points"], ["Up to 15", "24", "8"], ["Above 15", "48", "16"]]),
    ("5.4 FOUNDATION TOLERANCES", [
        "5.4.1 The top of the ring wall shall be level within plus or minus 3 mm in any 9 m of circumference and within plus or minus 6 mm over the total circumference.",
        "5.4.2 Differential settlement exceeding 13 mm between adjacent measurement points shall be reported to the Engineer before the tank is emptied."], None),
])
pdf("DDS-PI-014 Piping Support Guide.pdf", [
    ("6.1 PIPE SUPPORT SPACING", [
        "6.1.1 The maximum span between supports for carbon steel pipe filled with water shall be as given in the table below. Spans shall be reduced by 25 percent where concentrated loads such as valves are present within the span.",
        "6.1.2 Stainless steel pipe thinner than schedule 10S shall use 80 percent of the tabulated span."],
     [["Pipe size (inch)", "Maximum span (m)"], ["2", "3.0"], ["4", "4.3"], ["6", "5.2"], ["8", "5.8"]]),
    ("6.2 GUIDES AND ANCHORS", [
        "6.2.1 Vertical pipe runs shall be guided at intervals not exceeding 6 m. The first guide shall be located within 1.5 m of the top elbow.",
        "6.2.2 Line stops shall not be welded directly to pipe in services below minus 29 degrees C; a clamped shoe shall be used instead."], None),
    ("7.1 INSULATION THICKNESS", [
        "7.1.1 Hot insulation thickness for mineral wool shall be selected from the table below according to pipe size and operating temperature."],
     [["Pipe size (inch)", "Up to 200 C (mm)", "201 to 400 C (mm)"], ["Up to 2", "40", "60"], ["3 to 8", "50", "80"], ["10 and above", "60", "100"]]),
])
w = docx.Document()
w.add_heading("3 Painting Specification", 1)
w.add_paragraph("Carbon steel piping operating below 120 degrees C shall receive a three coat epoxy system with a total dry film thickness of 250 microns. Surface preparation shall be to Sa 2.5 with a surface profile of 50 to 75 microns.")
t = w.add_table(rows=4, cols=2)
for i, (a, b) in enumerate([("Coat", "DFT (microns)"), ("Zinc rich epoxy primer", "75"), ("High build epoxy intermediate", "125"), ("Polyurethane finish", "50")]):
    t.cell(i, 0).text = a; t.cell(i, 1).text = b
w.add_heading("4 Painting of Insulated Surfaces", 1)
w.add_paragraph("Insulated carbon steel operating between minus 4 and 150 degrees C shall receive an epoxy phenolic coating of 200 microns total dry film thickness to prevent corrosion under insulation. Galvanised surfaces shall not be painted unless specified.")
w.save(out / "DDS-PA-003 Painting Spec.docx")
wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Bolt Torque"
for row in [["Bolt size", "Flange rating", "Torque (Nm)", "Lubricant"], ["M16", "150#", 110, "Molybdenum disulphide"], ["M20", "150#", 190, "Molybdenum disulphide"],
            ["M20", "300#", 215, "Molybdenum disulphide"], ["M24", "300#", 370, "Molybdenum disulphide"], ["M27", "600#", 540, "Nickel anti-seize"]]:
    ws.append(row)
wb.save(out / "DDS-ME-020 Bolt Torque Chart.xlsx")
