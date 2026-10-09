"""Parser checks for asciiio — runs without Origin, so it is the fastest loop."""

import os
import sys

import asciiio

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")

fails = []


def check(label, cond, got=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s  -> %r" % (label, got))
        fails.append(label)


def load(name):
    return asciiio.parse(os.path.join(FIX, name))


def view(d):
    return [(c["name"], c["units"], c["numeric_rows"]) for c in d["columns"]]


print("=" * 60)
print("asciiio parser checks")
print("=" * 60)

d = load("a_tab.dat")
check("tab -> 2 cols", len(d["columns"]) == 2, view(d))
check("tab names", [c["name"] for c in d["columns"]] == ["X", "Y"], view(d))
check("tab delimiter recorded", d["delimiter"] == "\t", d["delimiter"])

d = load("b_csv.csv")
check("csv delimiter", d["delimiter"] == ",", d["delimiter"])
check("csv 3 cols", len(d["columns"]) == 3, view(d))
check("csv long name + units rows",
      [(c["name"], c["units"]) for c in d["columns"]] ==
      [("Time", "s"), ("Signal", "mV"), ("V", "")], view(d))

d = load("c_semicolon.txt")
check("semicolon delimiter", d["delimiter"] == ";", d["delimiter"])

d = load("d_space.dat")
check("whitespace fallback", d["delimiter"] == "whitespace", d["delimiter"])
check("whitespace 3 cols", len(d["columns"]) == 3, view(d))
check("headerless cols get generated names",
      [c["name"] for c in d["columns"]] == ["col1", "col2", "col3"], view(d))

d = load("e_comment_lines.dat")
check("#, //, ! and ; comments all skipped", len(d["columns"]) == 2, view(d))
check("comment file keeps numeric rows", d["rows"] == 3, d["rows"])
check("comment file values", d["columns"][1]["values"] == [1.0, 2.0, 3.0],
      d["columns"][1]["values"])

d = load("f_multi_header.dat")
check("repeated filler row not used as names",
      [c["name"] for c in d["columns"]] == ["Time", "Signal", "Flag"], view(d))
check("units row follows the name row",
      [c["units"] for c in d["columns"]] == ["s", "mV", "-"], view(d))

d = load("g_na_and_D.dat")
c = {x["name"]: x for x in d["columns"]}
check("uppercase D exponent parsed", c["Y"]["numeric_rows"] == 2, c["Y"]["numeric_rows"])
check("lowercase d exponent parsed", c["Y"]["values"][:2] == [1.5, 2.5], c["Y"]["values"])
check("NA and '-' become None", c["Y"]["values"][2] is None, c["Y"]["values"])
check("sparse column survives with its one value", c["Z"]["numeric_rows"] == 1,
      c["Z"]["values"])
check("full column unaffected", c["X"]["numeric_rows"] == 3, c["X"]["values"])

d = load("h_gbk_units.dat")
check("gb18030 decoded", d["encoding"] == "gb18030", d["encoding"])
check("cjk long names survive",
      [c["name"] for c in d["columns"]] == ["温度", "电导"], view(d))

d = load("i_bom_utf8.csv")
check("BOM stripped from first name", d["columns"][0]["name"] == "波长", view(d))
check("utf-8-sig reported", d["encoding"] == "utf-8-sig", d["encoding"])

print()
if fails:
    print("FAILED (%d): %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all asciiio checks passed")
