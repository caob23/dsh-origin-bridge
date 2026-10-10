"""Tiny deterministic datasets for the case bank.

Nothing here is real experimental data: every series is generated from a closed
formula so a re-run reproduces byte-identical files, and every file stays under
24 rows. `.bin` workstation samples are deliberately NOT covered here.

build(dir) writes the files and returns {placeholder: value}; the catalogue
references them as "{dat}", "{csv}", ... so cases never hardcode a path.
"""

import math
import os


def _rows(xs, ys):
    return "\n".join("%g\t%g" % (x, y) for x, y in zip(xs, ys))


def _write(path, text, encoding="utf-8"):
    with open(path, "w", encoding=encoding, newline="\n") as handle:
        handle.write(text)
    return path


def _exp(n=24, k=0.16):
    xs = [float(i) for i in range(n)]
    ys = [round(1.8 * math.exp(k * x), 3) for x in xs]
    return xs, ys


def build(target):
    os.makedirs(target, exist_ok=True)
    j = lambda name: os.path.join(target, name)  # noqa: E731
    out = {"dir": target}

    xs, ys = _exp()

    out["dat"] = _write(j("curve.dat"),
                        "X\tY\n" + _rows(xs, ys))
    out["units"] = _write(j("units.dat"),
                          "Long Name\tBinding Energy\tIntensity\n"
                          "Units\teV\tcps\n"
                          "Comments\tsynthetic\tpaired\n"
                          + _rows([round(i * 0.9, 2) for i in range(20)],
                                  [round(500 + 40 * math.sin(i / 3.0) + i * 6, 2) for i in range(20)]))
    out["xps_file"] = _write(j("xps_survey.dat"),
                             "Binding Energy\tIntensity\n"
                             + _rows([round(284 + i * 0.3, 2) for i in range(18)],
                                     [round(900 - 12 * i + 40 * math.sin(i / 2.0)) for i in range(18)]))
    out["csv"] = _write(j("curve.csv"),
                        "time,signal\n" + "\n".join("%g,%g" % p for p in zip(xs, ys)))
    out["semi"] = _write(j("curve.txt"),
                         "time;signal\n" + "\n".join("%g;%g" % p for p in zip(xs, ys)))
    out["spaced"] = _write(j("aligned.dat"),
                           "time      signal\n" + "\n".join("%-6g %-9g" % p for p in zip(xs, ys)))
    out["multi"] = _write(j("multi_header.dat"),
                          "run 1\nsample A\n\time\tmV\n" + _rows(xs[:12], ys[:12]))
    out["na"] = _write(j("na_values.dat"),
                       "x\ty\n" + "\n".join(
                           "%g\t%s" % (x, ("NA" if i % 7 == 3 else ("-" if i % 11 == 5 else "%g" % y)))
                           for i, (x, y) in enumerate(zip(xs, ys))))
    out["comment"] = _write(j("commented.dat"),
                            "# generated for the case bank\n# keep\nx\ty\n" + _rows(xs[:10], ys[:10]))
    out["gbk"] = _write(j("gbk_units.dat"),
                        "名称\t电势\t电流\n单位\tmV\tuA\n" + _rows([-2.0, -1.0, 0.0, 1.0, 2.0],
                                                            [12.5, 40.0, 999.0, 38.0, 11.0]),
                        encoding="gbk")
    out["bom"] = _write(j("bom.csv"), "\ufefftime,signal\n" + "\n".join("%g,%g" % p for p in zip(xs[:8], ys[:8])))
    out["dup"] = _write(j("dup_names.dat"), "x\ty\ty\n" + "\n".join("%g\t%g\t%g" % (x, y, y / 2)
                                                                   for x, y in zip(xs[:9], ys[:9])))
    out["wide"] = _write(j("wide.dat"),
                         "x\ta\tb\tc\td\n" + "\n".join(
                             "%g\t%g\t%g\t%g\t%g" % (x, y, y * 0.8, y * 0.6, y * 0.4)
                             for x, y in zip(xs, ys)))
    out["fit"] = _write(j("fit_pair.dat"),
                        "x\ty\n" + "\n".join("%g\t%g" % (x, 2.5 * x + 1.0 + (0.4 if i % 2 else -0.4))
                                             for i, x in enumerate([float(i) for i in range(1, 15)])))
    out["chi"] = _write(j("chi_lsv.txt"), (
        "Mar. 02, 2024   09:41:07\r\n"
        "Linear Sweep Voltammetry\r\n"
        "File: d:\\lab\\chi_lsv.txt\r\n"
        "Data Source:  Experiment\r\n"
        "Instrument Model:  CHI660E\r\n"
        "Header: \r\n"
        "Note: \r\n"
        "\r\n"
        "Init E (V) = 0.0\r\n"
        "Final E (V) = 0.6\r\n"
        "Scan Rate (V/s) = 0.05\r\n"
        "Sample Interval (V) = 0.001\r\n"
        "Quiet Time (sec) = 2\r\n"
        "Sensitivity (A/V) = 0.0001\r\n"
        "\r\n"
        "Results:\r\n"
        "\r\n"
        "Channel 1:\r\n"
        "Segment 1:\r\n"
        "\r\n"
        "Potential/V, Current/A\r\n"
        "\r\n" + "\r\n".join("%.3f, %.3e" % (i * 0.01, 1e-5 + 2e-6 * math.sin(i / 3.0))
                            for i in range(40)) + "\r\n"))
    out["empty"] = _write(j("empty.dat"), "")
    out["text"] = _write(j("text_only.dat"), "name\tnote\nalpha\tthis file has no numbers\nbeta\tnor does this\n")
    out["onecol"] = _write(j("one_column.dat"), "y\n1\n2\n3\n")
    out["huge"] = _write(j("long_line.dat"), "x\ty\n" + "\n".join("%g\t%g" % (i, i * i) for i in range(400)))

    out["columns"] = {"t": [round(v, 2) for v in xs[:12]],
                      "v": [round(v, 2) for v in ys[:12]]}
    out["wide_columns"] = {"t": [round(v, 2) for v in xs[:10]],
                           "a": [round(v, 2) for v in ys[:10]],
                           "b": [round(v * 0.7, 2) for v in ys[:10]],
                           "c": [round(v * 0.4, 2) for v in ys[:10]]}
    out["headers"] = ["x", "y1", "y2"]
    out["rows"] = [[round(x, 2), round(y, 2), round(y * 0.5, 2)] for x, y in zip(xs[:10], ys[:10])]
    out["matrix_rows"] = [[round(math.sin(i / 2.0) + math.cos(j / 3.0), 4) for j in range(8)] for i in range(8)]
    out["xps"] = {"binding_energy": [round(284.0 + i * 0.5, 2) for i in range(18)],
                  "intensity": [round(120 + 90 * math.exp(-((i - 9) ** 2) / 8.0), 2) for i in range(18)]}
    out["cv"] = {"potential": [round(-0.2 + i * 0.02, 3) for i in range(24)],
                 "current": [round(1e-5 * math.sin(i / 2.4) * (1 + i / 12.0), 8) for i in range(24)]}
    return out
