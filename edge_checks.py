"""Edge-case battery: code paths the main smoke test never executes.

Run:  python -X utf8 edge_checks.py
"""

import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke_test as S  # noqa: E402

SANDBOX = os.path.join(HERE, "edge")
results = []


def check(label, cond, detail=""):
    ok = bool(cond)
    results.append((label, ok))
    print("%-5s %-42s %s" % ("PASS" if ok else "FAIL", label, str(detail)[:150]))


def main():
    shutil.rmtree(SANDBOX, ignore_errors=True)
    os.makedirs(SANDBOX, exist_ok=True)
    rpc = S.Rpc(S.PY, S.SERVER)
    rpc.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "edge", "version": "1"}})
    rpc.notify("notifications/initialized")

    # 1) every export format the tool claims to support
    imp = rpc.tool("origin_import", {"path": os.path.join(HERE, "sample.dat")})
    ws = imp["worksheet"]
    p = rpc.tool("origin_plot", {"worksheet": ws, "plot_type": "line"})
    gr = p["graph"]
    for fmt, magic in (("svg", b"<"), ("pdf", b"%PDF"), ("tif", b"II*\x00"), ("emf", b"\x01\x00\x00\x00")):
        target = os.path.join(SANDBOX, "chart." + fmt)
        r = rpc.tool("origin_export", {"graph": gr, "path": target, "fmt": fmt})
        head = b""
        if os.path.isfile(target):
            with open(target, "rb") as fh:
                head = fh.read(8)
        check("export %s" % fmt, r.get("ok") and head.startswith(magic),
              (r.get("bytes"), head[:4], r.get("code")))

    # 2) inline data end to end (no file at all)
    fig = rpc.tool("origin_figure", {
        "columns": {"Dose": [0, 1, 2, 3, 4], "Response": [1.2, 4.1, 8.8, 12.2, 19.5]},
        "x": 1, "y": 2, "plot_type": "scatter", "output_dir": os.path.join(SANDBOX, "inline"),
        "x_title": "Dose (mg)", "y_title": "Response"})
    check("figure from inline columns", fig.get("ok") and fig.get("data", {}).get("rows") == 5,
          (fig.get("data", {}).get("rows"), fig.get("delivered"), fig.get("code")))

    # 3) address columns by label instead of index
    bylabel = rpc.tool("origin_plot", {"worksheet": ws, "x": "X", "y": "Y", "plot_type": "column"})
    check("plot by column label", bylabel.get("ok") and bylabel.get("y_columns") == [2],
          (bylabel.get("x_column"), bylabel.get("y_columns"), bylabel.get("code")))
    both = rpc.tool("origin_plot", {"worksheet": ws, "x": "X", "y": ["Y", "Y"],
                                    "plot_type": "line"})
    check("plot several labelled series", both.get("ok") and both.get("y_columns") == [2, 2]
          and both.get("plots_in_layer") == 2,
          (both.get("y_columns"), both.get("plots_in_layer"), both.get("code")))
    badlabel = rpc.tool("origin_plot", {"worksheet": ws, "x": "nope", "y": "Y"})
    check("unknown label lists real ones", badlabel.get("code") == "bad_column"
          and "X" in str(badlabel.get("message")), badlabel.get("message"))

    # 4) error branches that must be clean, not internal_error
    bs = rpc.tool("origin_style", {"graph": gr, "series": 9, "color": "#000"})
    check("style bad series", bs.get("code") == "bad_series", bs.get("code"))
    bl = rpc.tool("origin_style", {"graph": gr, "layer": 7, "x_title": "z"})
    check("style bad layer", bl.get("code") == "bad_layer", bl.get("code"))
    nf = rpc.tool("origin_fit", {"worksheet": ws, "kind": "nlfitsing", "func": "totally_bogus_fn"})
    check("bogus fit function", nf.get("code") == "fit_function_unavailable", nf.get("code"))
    vwbad = rpc.tool("origin_view", {"graph": "gr-99999"})
    check("view refuses unknown handle", vwbad.get("ok") is False
          and vwbad.get("code") == "graph_not_found", vwbad.get("code"))
    exbad = rpc.tool("origin_export", {"graph": "gr-99999",
                                       "path": os.path.join(SANDBOX, "wrong.png")})
    check("export refuses unknown handle", exbad.get("ok") is False
          and not os.path.isfile(os.path.join(SANDBOX, "wrong.png")), exbad.get("code"))
    check("write unequal lengths rejected", rpc.tool("origin_write", {"columns": {"a": [1, 2], "b": [1]}}).get("code")
          == "bad_arguments", "bad_arguments")
    strcol = rpc.tool("origin_write", {"columns": {"a": ["x", "y"]}})
    check("non-numeric data gives clean error", strcol.get("code") == "bad_arguments"
          and "a" in str(strcol.get("message")), (strcol.get("code"), str(strcol.get("message"))[:60]))
    check("unknown extra param tolerated", rpc.tool("origin_status", {"wat": 1}).get("connected") is True, "ok")

    # 5) CJK + spaces in the data path (Origin/LabTalk historically choke here)
    hard = os.path.join(SANDBOX, "中文 目录 with spaces")
    os.makedirs(hard, exist_ok=True)
    hard_dat = os.path.join(hard, "数据 表.dat")
    shutil.copyfile(os.path.join(HERE, "sample.dat"), hard_dat)
    cjk = rpc.tool("origin_import", {"path": hard_dat})
    check("import from CJK+space path", cjk.get("rows") == 24, (cjk.get("rows"), cjk.get("code")))
    cfig = rpc.tool("origin_figure", {"source": hard_dat,
                                      "output_dir": os.path.join(hard, "输出 out"),
                                      "x_title": "时间 (s)", "y_title": "信号 (mV)"})
    files = cfig.get("delivered", {})
    check("deliver into CJK+space dir", cfig.get("ok") and all(
        os.path.isfile(v) for v in files.values()), files or cfig.get("message", "")[:80])

    # 6) xlsx round trip
    try:
        import openpyxl
        wbk = openpyxl.Workbook()
        sh = wbk.active
        sh.append(["Temp", "Value"])
        for i in range(20):
            sh.append([i * 1.0, (i ** 0.5) * 3])
        xp = os.path.join(SANDBOX, "data.xlsx")
        wbk.save(xp)
        xr = rpc.tool("origin_import", {"path": xp})
        check("import xlsx", xr.get("rows") == 20 and xr.get("cols") == 2,
              (xr.get("rows"), xr.get("cols"), xr.get("code")))
    except ImportError:
        print("SKIP  import xlsx (openpyxl not installed in this venv)")

    # 7) formula that creates a brand-new column
    fx = rpc.tool("origin_formula", {"worksheet": ws, "col": 3, "formula": "Col(2)*2", "label": "Doubled"})
    check("formula creates new column", fx.get("ok") and fx.get("computed_rows") == 24,
          (fx.get("computed_rows"), fx.get("code"), fx.get("message", "")[:60]))

    # 8) big table: 5000 rows through write_block
    rows = [[float(i), float(i * i % 977), float((i * 7) % 311)] for i in range(5000)]
    t0 = time.time()
    big = rpc.tool("origin_write", {"headers": ["i", "a", "b"], "rows": rows})
    dt = time.time() - t0
    check("5000x3 block write", big.get("rows") == 5000 and big.get("cols") == 3,
          "%d rows in %.1fs" % (big.get("rows") or 0, dt))
    bigplot = rpc.tool("origin_plot", {"worksheet": big["worksheet"], "x": 1, "y": 2})
    check("plot big table", bigplot.get("ok") and (bigplot.get("x_axis_to") or 0) >= 4990,
          (bigplot.get("x_axis_to"), bigplot.get("code")))

    # 9) open a project we saved earlier, then exit and reconnect in one process
    saved = (fig.get("project") or {}).get("path")
    if saved and os.path.isfile(saved):
        op = rpc.tool("origin_open", {"path": saved})
        check("reopen saved opju", op.get("ok") and len(op.get("open_pages", [])) >= 1,
              (op.get("open_pages"), op.get("code")))
    bad_open = rpc.tool("origin_open", {"path": os.path.join(SANDBOX, "nope.opju")})
    check("open missing file", bad_open.get("code") == "file_not_found", bad_open.get("code"))

    ex = rpc.tool("origin_exit", {})
    after = rpc.tool("origin_status", {}, timeout=200)
    check("exit then auto-reconnect", ex.get("exited") and after.get("connected") is True,
          (ex.get("exited"), after.get("connected"), after.get("code")))

    rpc.close()

    # 10) the node launcher shipped in bin/ must actually start the server
    node = shutil.which("node")
    if node:
        mjs = os.path.join(HERE, "bin", "origin-bridge.mjs")
        proc = subprocess.Popen([node, mjs], cwd=HERE, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "node", "version": "1"}}}) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            info = json.loads(line)["result"]["serverInfo"]
            check("bin/origin-bridge.mjs launcher", info.get("name") == "origin-bridge", info)
        except Exception as exc:
            check("bin/origin-bridge.mjs launcher", False, repr(exc)[:120])
        finally:
            proc.kill()
    else:
        print("SKIP  node launcher (node not on PATH)")

    bad = [n for n, ok in results if not ok]
    print("\n%d/%d passed" % (len(results) - len(bad), len(results)))
    if bad:
        print("FAILED:", ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
