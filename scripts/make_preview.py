"""Regenerate docs/preview.png through the real engine.

The picture is a genuine Origin export (3216x2461), so it is rebuilt the same
way the plugin serves it rather than touched with an image editor: the legend
box sits on top of the rising curve, so erasing it would leave a hole.

Note that `figure(legend=False)` SKIPS our legend step -- it does not hide the
legend Origin's own template already put on the layer. Switching it off needs
an explicit legend_entries(show=False), and the readback is asserted here so a
silent regression cannot ship a legend-bearing preview.

`Smoothed` is a trailing 9-point mean of the sample column, which lags the
exponential the way the original demo did.

    python scripts/make_preview.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import asciiio  # noqa: E402
import originlab  # noqa: E402

SMOOTH_WINDOW = 9
OUT = os.path.join(ROOT, "docs", "preview.png")
# Origin's default theme for a two-curve line+symbol graph: blue squares and a
# red dashed line with triangles.
SERIES_STYLE = ((0, "blue", 2, 2.5), (1, "red", 3, 1.5))


def trailing_mean(values, window=SMOOTH_WINDOW):
    out = []
    for i in range(len(values)):
        part = values[max(0, i - window + 1):i + 1]
        out.append(sum(part) / len(part))
    return out


def main():
    table = asciiio.parse(os.path.join(ROOT, "sample.dat"))
    cols = table["columns"]
    x = [float(v) for v in cols[0]["values"]]
    y = [float(v) for v in cols[1]["values"]]

    sheet = originlab.write_columns({"X": x, "Y": y, "Smoothed": trailing_mean(y)})
    p = originlab.plot(sheet["worksheet"], x=1, y=[2, 3], plot_type="line_symbol",
                       template="linesymb")
    titles = originlab.style(p["graph"], x_title="Time (s)", y_title="Signal (mV)")

    hidden = originlab.legend_entries(p["graph"], show=False)
    shown = [op for op in hidden.get("applied", []) if op.get("op") == "show"]
    if not shown or shown[0].get("readback") != 0:
        raise SystemExit("图例没能关掉（readback=%r），不导出" % (shown or "没有 show 这项",))
    if hidden.get("failed"):
        raise SystemExit("图例设置部分失败：%s" % hidden["failed"])

    for series, color, symbol, width in SERIES_STYLE:
        originlab.style(p["graph"], series=series, color=color,
                        symbol_kind=symbol, line_width=width)

    export = originlab.export(p["graph"], OUT, fmt="png", width=1600)
    print("wrote %s" % export["path"])
    print("  bytes     : %s  dimensions: %s  channel=%s"
          % (export["bytes"], export.get("dimensions"), export.get("channel")))
    print("  标题读回   : %s" % [o.get("readback") for o in titles.get("applied", [])])
    print("  图例       : show 读回 %s" % shown[0].get("readback"))
    print("  数据       : %d 行 x 3 列（X / Y / Smoothed），graph=%s"
          % (len(x), p["graph_page"]))
    originlab.close_pages("graph", [p["graph_page"]])


if __name__ == "__main__":
    main()
