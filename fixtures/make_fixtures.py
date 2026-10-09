"""Regenerate the fixtures/ files used by test_asciiio.py.

Filenames describe the parser behaviour each one exercises, so a new messy real
file can be dropped in alongside them.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))

FILES = {
    "a_tab.dat": ("utf-8", "X\tY\n0\t1.5\n1\t2.5\n2\t3.5\n"),
    "b_csv.csv": ("utf-8", "Time,Signal,V\ns,mV,\n0,1.5,0.1\n1,2.5,0.2\n"),
    "c_semicolon.txt": ("utf-8", "A;B\n1;2\n3;4\n"),
    "d_space.dat": ("utf-8", "0   1.50   0.01\n1   2.50   0.02\n2   3.50   0.01\n"),
    "e_comment_lines.dat": ("utf-8", "// instrument: UV-8000\n! legacy banner\n"
                            "# run 2026-10-08\nX\tY\n1.0\t1.0\n2.0\t2.0\n3.0\t3.0\n"),
    "f_multi_header.dat": ("utf-8", "Sample\tSample\tSample\nTime\tSignal\tFlag\n"
                            "s\tmV\t-\n0\t1.5\t1\n1\t2.5\t0\n"),
    "g_na_and_D.dat": ("utf-8", "X\tY\tZ\n0\t1.5D+00\tNA\n1\t2.5d+00\t-\n2\tNA\t3.1\n"),
    "h_gbk_units.dat": ("gbk", "温度\t电导\n25.0\t1.23\n30.0\t1.45\n"),
    "i_bom_utf8.csv": ("utf-8-sig", "波长,吸光度\n200,0.12\n210,0.34\n"),
}


def main():
    for name, (enc, text) in sorted(FILES.items()):
        path = os.path.join(HERE, name)
        with open(path, "w", encoding=enc, newline="") as fh:
            fh.write(text)
        print("wrote %s (%s, %d bytes)" % (name, enc, os.path.getsize(path)))


if __name__ == "__main__":
    main()
