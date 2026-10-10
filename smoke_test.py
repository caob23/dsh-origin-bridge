"""End-to-end smoke test: drives server.py over stdio like a real MCP client.

    python smoke_test.py            # needs Origin available
    python smoke_test.py --no-origin   # protocol + tool table only

Exits non-zero on the first hard failure. Writes artifacts to ./out/.
"""

import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
# Point this at the installed copy (…/node_modules/dsh-origin-bridge/server.py)
# to regression-test what dsh actually runs, without littering that folder.
SERVER = os.environ.get("ORIGIN_BRIDGE_SERVER") or os.path.join(HERE, "server.py")
PY = sys.executable


class Rpc:
    def __init__(self, python, script):
        self.p = subprocess.Popen([python, "-u", script], cwd=HERE,
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
        self.lines = []
        self.lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()
        self.id = 0

    def _pump(self):
        for line in iter(self.p.stdout.readline, ""):
            line = line.strip()
            if line:
                with self.lock:
                    self.lines.append(line)

    def notify(self, method, params=None):
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                       "params": params or {}}) + "\n")
        self.p.stdin.flush()

    def raw(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def request(self, method, params=None, timeout=180):
        self.id += 1
        mine = self.id
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mine, "method": method,
                                       "params": params or {}}, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        t0 = time.time()
        while time.time() - t0 < timeout:
            with self.lock:
                for raw in self.lines:
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if msg.get("id") == mine:
                        return msg
            if self.p.poll() is not None:
                raise SystemExit("server died: %s" % self.p.stderr.read()[-2000:])
            time.sleep(0.05)
        raise SystemExit("timeout on %s" % method)

    def tool(self, name, arguments=None, timeout=240):
        msg = self.request("tools/call", {"name": name, "arguments": arguments or {}}, timeout)
        if "error" in msg:
            return {"ok": False, "code": "rpc_error", "message": msg["error"]}
        res = msg.get("result", {})
        blocks = res.get("content", [])
        text = "".join(c.get("text", "") for c in blocks if c.get("type") == "text")
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"ok": False, "code": "bad_payload", "message": text[:400]}
        payload["_isError"] = res.get("isError", False)
        payload["_content_types"] = sorted({c.get("type") for c in blocks})
        payload["_image_len"] = max([len(c.get("data") or "") for c in blocks
                                     if c.get("type") == "image"] or [0])
        return payload

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        self.p.terminate()


def check(label, cond, detail=""):
    print("%-4s %s%s" % ("PASS" if cond else "FAIL", label, (" | " + str(detail)) if detail else ""))
    if not cond:
        raise SystemExit(1)


def main():
    need_origin = "--no-origin" not in sys.argv
    os.makedirs(OUT, exist_ok=True)
    rpc = Rpc(PY, SERVER)

    init = rpc.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                      "clientInfo": {"name": "smoke", "version": "1"}})
    info = init.get("result", {})
    check("initialize handshake", bool(info.get("serverInfo")), info.get("serverInfo"))
    rpc.notify("notifications/initialized")

    tools = rpc.request("tools/list").get("result", {}).get("tools", [])
    names = sorted(t["name"] for t in tools)
    check("tools/list returns 25", len(tools) == 25, len(tools))
    for t in tools:
        check("schema present: %s" % t["name"], bool(t.get("inputSchema", {}).get("properties")
                                                     or t["name"] in ("origin_status",)),
              len(t.get("inputSchema", {}).get("properties", {})))
    print("       %s" % ", ".join(names))

    # origin_read_file is pure Python, so it is checked in both modes.
    rf = rpc.tool("origin_read_file", {"path": os.path.join(HERE, "fixtures", "d_space.dat")})
    check("read_file parses space-aligned data", rf.get("ok") and rf.get("delimiter") == "whitespace"
          and len(rf.get("columns", [])) == 3, (rf.get("delimiter"), rf.get("columns"), rf.get("code")))
    mh = rpc.tool("origin_read_file", {"path": os.path.join(HERE, "fixtures", "f_multi_header.dat")})
    check("read_file skips the filler header row",
          [c["name"] for c in mh.get("columns", [])] == ["Time", "Signal", "Flag"], mh.get("columns"))
    check("read_file keeps the units row",
          [c["units"] for c in mh.get("columns", [])] == ["s", "mV", "-"], mh.get("columns"))
    pres = rpc.tool("origin_chart_presets", {})
    check("chart presets catalog loaded", pres.get("n_templates") == 40 and len(pres.get("categories", [])) >= 6,
          (pres.get("n_templates"), pres.get("categories"), pres.get("code")))
    chk = rpc.tool("origin_chart_check", {"name": pres["templates"][0]["name"],
                                          "columns": ["binding_energy", "intensity"]})
    check("chart_check dry-run matches columns", chk.get("status") == "ok", (chk.get("issues"), chk.get("code")))
    chk2 = rpc.tool("origin_chart_check", {"name": pres["templates"][0]["name"], "columns": ["only_one"]})
    check("chart_check reports missing columns", chk2.get("status") == "warning", chk2.get("issues"))

    if not need_origin:
        rpc.close()
        print("skipped Origin-dependent steps (--no-origin)")
        return 0

    st = rpc.tool("origin_status")
    check("origin_status connected", st.get("connected") is True, st.get("origin_version_raw") or st)
    check("origin version >= 2021", (st.get("origin_version_raw") or 0) >= 10.0,
          st.get("origin_version_raw"))

    imp = rpc.tool("origin_import", {"path": os.path.join(HERE, "sample.dat")})
    check("import .dat", imp.get("rows", 0) == 24, (imp.get("rows"), imp.get("columns")))
    check("import went through the robust reader", imp.get("channel") == "asciiio", imp.get("channel"))
    check("import keeps long names", [c["label"] for c in imp.get("columns", [])][:2] == ["X", "Y"],
          imp.get("columns"))
    ws = imp.get("worksheet")

    fig = rpc.tool("origin_figure", {
        "source": os.path.join(HERE, "sample.dat"),
        "plot_type": "line_symbol",
        "x_title": "Time (s)", "y_title": "Signal (mV)", "title": "Smoke chart",
        "output_dir": OUT, "fmt": "png", "width": 1400,
    })
    check("figure ok", fig.get("ok") is True and fig.get("_isError") is False,
          fig.get("message") or fig.get("steps"))
    check("figure proof", fig.get("proof_level") == "verified", fig.get("proof_level"))
    # sample.dat X runs 0..23; a graph whose axis still reads 0..1 is empty.
    x_to = (fig.get("plot") or {}).get("x_axis_to")
    check("x axis spans the data", (x_to or 0) >= 20, (x_to, (fig.get("plot") or {}).get("x_data_span")))

    img = os.path.join(OUT, "Graph1.png")
    delivered = fig.get("delivered", {})
    img = delivered.get("image") or img
    proj = delivered.get("project")
    check("image written", os.path.isfile(img) and os.path.getsize(img) > 2000,
          (img, os.path.getsize(img) if os.path.isfile(img) else None))
    with open(img, "rb") as fh:
        check("image is a real PNG", fh.read(8) == b"\x89PNG\r\n\x1a\n")
    check("opju written", bool(proj) and os.path.isfile(proj) and os.path.getsize(proj) > 2000,
          (proj, os.path.getsize(proj) if proj and os.path.isfile(proj) else None))

    fit = rpc.tool("origin_fit", {"worksheet": ws, "x": 1, "y": 2, "kind": "linear"})
    check("linear fit", fit.get("ok") is True, fit.get("code") or "slope readback")
    blob = json.dumps(fit, ensure_ascii=False)
    check("fit result is trimmed for context", len(blob) < 3000 and
          (fit.get("fit") or {}).get("slope", {}).get("value") is not None,
          (len(blob), (fit.get("fit") or {}).get("slope")))

    insp = rpc.tool("origin_inspect")
    check("inspect lists pages", len(insp.get("pages", [])) >= 2, [p["name"] for p in insp.get("pages", [])])

    bad = rpc.tool("origin_plot", {"worksheet": "ws-999", "x": 1, "y": 2})
    check("bad handle rejected", bad.get("ok") is False and bad.get("code") == "worksheet_not_found",
          bad.get("code"))

    bad2 = rpc.tool("origin_import", {"path": os.path.join(HERE, "nope.csv")})
    check("missing file rejected", bad2.get("ok") is False and bad2.get("code") == "file_not_found",
          bad2.get("code"))

    # no destination given => nothing lands on disk (and nothing in the server CWD)
    nd = rpc.tool("origin_figure", {"source": os.path.join(HERE, "sample.dat")})
    check("figure without output_dir delivers no files", not nd.get("delivered") and bool(nd.get("note")),
          (nd.get("delivered"), (nd.get("note") or "")[:40]))
    strays = [f for f in os.listdir(HERE) if f.endswith(".opju")]
    check("no stray .opju in server CWD", not strays, strays)

    # --- the capabilities that only exist after the v0.2 tool set ---
    gr_handle = (fig.get("plot") or {}).get("graph")

    wb = rpc.tool("origin_write", {"headers": ["A", "B", "C"],
                                   "rows": [[i, i * 2, i * 3] for i in range(6)]})
    check("write_block wide table", wb.get("rows") == 6 and wb.get("cols") == 3,
          (wb.get("rows"), wb.get("cols"), wb.get("code")))

    cf = rpc.tool("origin_formula", {"worksheet": wb["worksheet"], "col": 3,
                                     "formula": "Col(1)+Col(2)", "label": "Sum"})
    check("column formula computes", cf.get("computed_rows") == 6 and cf.get("last") == 15,
          (cf.get("computed_rows"), cf.get("first"), cf.get("last"), cf.get("code")))

    sty = rpc.tool("origin_style", {"graph": gr_handle, "y_scale": "log10",
                                    "xlim": [0, 25], "color": "#D55E00", "symbol_size": 12,
                                    "line_width": 2.5})
    ops = {o["op"]: o.get("readback") for o in sty.get("applied", [])}
    check("style readback y_scale", ops.get("y_scale") == 2, ops.get("y_scale"))
    check("style readback xlim", ops.get("xlim") is not None, ops.get("xlim"))
    check("style readback color", ops.get("p0.color") is not None, ops.get("p0.color"))
    # set_cmd("w=2.5") used to be reported as applied while the line stayed thin
    check("style readback line width", ops.get("p0.line.width") == 2.5,
          (ops.get("p0.line.width"), sty.get("failed")))
    check("style failures visible", isinstance(sty.get("failed"), list), sty.get("failed"))

    ann = rpc.tool("origin_annotate", {"graph": gr_handle,
                                       "labels": [{"text": "peak", "x": 12, "y": 60}],
                                       "lines": [{"x1": 0, "y1": 50, "x2": 23, "y2": 50}]})
    check("annotate label+line", all(i["ok"] for i in ann.get("items", [])) and len(ann.get("items", [])) == 2,
          ann.get("items") or ann.get("code"))

    lyr = rpc.tool("origin_layer", {"graph": gr_handle, "layer_type": 2})
    check("add_layer grows page", (lyr.get("layers_after") or 0) > (lyr.get("layers_before") or 0),
          (lyr.get("layers_before"), lyr.get("layers_after"), lyr.get("code")))
    check("add_layer names the layer like Origin does", lyr.get("layer_name_from_origin") == "RightY",
          lyr.get("layer_name_from_origin"))
    check("add_layer proof", lyr.get("proof_level") == "verified", lyr.get("proof_level"))

    # --- merged features: multi-series, dual-Y, legend, matrix, presets ---
    ms = rpc.tool("origin_plot", {"worksheet": wb["worksheet"], "x": 1, "y": [2, 3],
                                  "plot_type": "line_symbol"})
    check("multi-series in one layer", ms.get("ok") and ms.get("plots_in_layer") == 2
          and ms.get("channel") == "add_plot",
          (ms.get("plots_in_layer"), ms.get("channel"), ms.get("code")))

    l1 = lyr.get("layer_index")
    dy = rpc.tool("origin_plot", {"worksheet": wb["worksheet"], "x": 1, "y": 3,
                                  "graph": gr_handle, "layer": l1})
    check("curve lands in the right-Y layer", dy.get("ok") and dy.get("plots_in_layer") == 1
          and dy.get("layer") == l1, (dy.get("plots_in_layer"), dy.get("layer"), dy.get("code"),
                                      dy.get("message")))
    check("right-Y layer got its own scale", (dy.get("x_axis_to") or 0) >= 4,
          (dy.get("x_axis_from"), dy.get("x_axis_to")))

    tpl = rpc.tool("origin_plot", {"worksheet": wb["worksheet"], "x": 1, "y": 2,
                                   "template": "doubley"})
    check("Origin graph template builds a 2-layer page", tpl.get("ok") and tpl.get("template") == "doubley",
          (tpl.get("code"), tpl.get("message")))
    badtpl = rpc.tool("origin_plot", {"worksheet": wb["worksheet"], "x": 1, "y": 2,
                                      "template": "no_such_thing"})
    check("unknown template rejected before Origin sees it", badtpl.get("code") == "bad_template",
          badtpl.get("code"))

    lg = rpc.tool("origin_legend", {"graph": ms.get("graph"), "position": "inside_top_right"})
    # %(i) is Origin's own "this plot's Y column long name" token: the stored text
    # keeps the token and the rendered image shows the name, so assert on rows.
    check("legend auto-generates one row per curve",
          lg.get("ok") and len([r for r in (lg.get("text") or "").splitlines() if "%(" in r]) == 2,
          (lg.get("text"), lg.get("code")))
    check("legend position read back", lg.get("proof_level") == "verified",
          (lg.get("applied"), lg.get("failed")))
    lg2 = rpc.tool("origin_legend", {"graph": ms.get("graph"), "entries": ["first", "second"],
                                     "position": "inside_bottom_left"})
    check("legend entries override", lg2.get("ok") and "first" in (lg2.get("text") or "")
          and "second" in (lg2.get("text") or ""), (lg2.get("text"), lg2.get("code")))
    lg3 = rpc.tool("origin_legend", {"graph": ms.get("graph"), "position": "nowhere"})
    check("bad legend position rejected", lg3.get("code") == "bad_position", lg3.get("code"))

    grid = [[float((i - 3) ** 2 + (j - 3) ** 2) % 25 for j in range(10)] for i in range(10)]
    mx = rpc.tool("origin_matrix", {"rows": grid, "template": "heat_map",
                                    "export_path": os.path.join(OUT, "matrix_heat.png")})
    check("matrix heat_map builds", mx.get("ok") and mx.get("plots_in_layer", 0) >= 1,
          (mx.get("code"), mx.get("message"), mx.get("plots_in_layer")))
    check("matrix z range matches the grid", mx.get("z_min") == min(min(r) for r in grid)
          and mx.get("z_max") == max(max(r) for r in grid), (mx.get("z_min"), mx.get("z_max")))
    check("matrix image delivered", os.path.isfile(os.path.join(OUT, "matrix_heat.png")),
          mx.get("export"))
    s3 = rpc.tool("origin_matrix", {"rows": grid, "template": "cmap"})
    check("3D color-map surface builds", s3.get("ok") and s3.get("plots_in_layer", 0) >= 1,
          (s3.get("code"), s3.get("message")))

    preset_name = "xrd"
    pv = rpc.tool("origin_chart_check", {"name": preset_name,
                                         "data": os.path.join(HERE, "sample.dat")})
    check("chart_check reads columns from the file", pv.get("ok") and
          pv.get("columns_detected") == ["X", "Y"], (pv.get("columns_detected"), pv.get("issues")))
    cr = rpc.tool("origin_chart_render", {"name": preset_name,
                                          "source": os.path.join(HERE, "sample.dat"),
                                          "x": 1, "y": 2, "output_dir": OUT})
    check("chart_render delivers image+opju", cr.get("ok") and
          bool((cr.get("delivered") or {}).get("image")) and bool((cr.get("delivered") or {}).get("project")),
          (cr.get("delivered"), cr.get("code"), cr.get("message")))
    check("chart_render applied the preset titles",
          (cr.get("preset") or {}).get("x_title") and (cr.get("preset") or {}).get("y_title"),
          cr.get("preset"))

    gp = rpc.tool("origin_fit", {"worksheet": ws, "x": 1, "y": 2, "kind": "gauss"})
    check("fit preset resolves to an installed function", gp.get("ok") and gp.get("func") == "Gauss",
          (gp.get("func"), gp.get("code"), gp.get("message")))
    gp2 = rpc.tool("origin_fit", {"worksheet": ws, "x": 1, "y": 2, "kind": "nlfitsing", "func": "gauss1"})
    check("bogus function name still rejected", gp2.get("code") == "fit_function_unavailable",
          gp2.get("code"))

    import tempfile as _tf
    def _previews():
        return len([f for f in os.listdir(_tf.gettempdir()) if f.startswith("origin_bridge_view_")])
    before = _previews()
    vw = rpc.tool("origin_view", {"graph": gr_handle, "width": 700})
    check("view returns image content", "image" in vw.get("_content_types", []) and vw.get("_image_len", 0) > 2000,
          (vw.get("_content_types"), vw.get("_image_len"), vw.get("code")))
    check("view cleans up its temp file", _previews() <= before, (before, _previews()))

    page = (fig.get("plot") or {}).get("graph_page")
    lt = rpc.tool("origin_labtalk", {"script": "win -a %s; layer.x.type=2;" % page,
                                     "numeric": ["layer.x.type", "layer.x.to"]})
    check("labtalk runs + reads back", lt.get("returned") and
          lt.get("numeric", {}).get("layer.x.type") == 2.0,
          (lt.get("returned"), lt.get("numeric")))

    # string assignment is NOT executable through lt_exec on this build; the
    # bridge must report that instead of pretending success
    ltbad = rpc.tool("origin_labtalk", {"script": 'layer.x.title$="nope";'})
    check("labtalk reports failure honestly", ltbad.get("returned") is False, ltbad.get("returned"))

    ltd = rpc.tool("origin_labtalk", {"script": "doc -s;"})
    check("labtalk denies destructive", ltd.get("code") == "labtalk_denied", ltd.get("code"))
    ltd2 = rpc.tool("origin_labtalk", {"script": "doc   -s   -y;"})
    check("deny list survives whitespace tricks", ltd2.get("code") == "labtalk_denied", ltd2.get("code"))

    nl2 = rpc.tool("origin_fit", {"worksheet": ws, "kind": "nlfitsing", "func": "expDec1",
                                  "fixed": {"y0": 0}, "starts": {"A1": 80, "t1": 10}})
    check("nlfit honors fixed param", nl2.get("ok") and
          any(c.get("fix_param") == "y0" and c.get("ok") for c in nl2.get("constraints", [])),
          (nl2.get("r_squared"), nl2.get("constraints"), nl2.get("code")))

    # op.save(file) is Save-As: a second save to the same path must not look like a failure
    again = rpc.tool("origin_save_project", {"path": proj})
    check("re-save same path ok", again.get("ok") is True, again.get("code") or again.get("saved"))

    # Long dsh sessions accumulate pages, which is what makes Origin's COM bridge
    # start answering "无效指针"; the GC tool has to prove it actually removed them.
    dry = rpc.tool("origin_close_pages", {"kind": "graph", "dry_run": True})
    check("close_pages dry run lists without deleting", dry.get("ok") and dry.get("dry_run")
          and len(dry.get("would_close", [])) >= 2, dry.get("would_close"))
    handles_before = len(rpc.tool("origin_inspect", {}).get("handles", []))
    one = dry["would_close"][0]
    gc = rpc.tool("origin_close_pages", {"kind": "graph", "names": [one]})
    check("close_pages closes by name", gc.get("ok") and one in gc.get("closed", [])
          and not gc.get("refused"), (gc.get("closed"), gc.get("refused"), gc.get("code")))
    check("closed page is really gone", one not in [p["name"] for p in rpc.tool("origin_inspect", {}).get("pages", [])],
          one)
    check("handles for closed pages are dropped",
          len(rpc.tool("origin_inspect", {}).get("handles", [])) < handles_before,
          (handles_before, rpc.tool("origin_inspect", {}).get("handles")))
    rest = rpc.tool("origin_close_pages", {"kind": "graph"})
    check("close_pages sweeps the rest", rest.get("proof_level") == "verified",
          (rest.get("closed"), rest.get("refused")))

    # exit_origin used to deadlock: it called save_project(), which queues onto the
    # same COM thread that was already running.
    bye = rpc.tool("origin_exit", {"save_to": ""}, timeout=120)
    check("origin_exit no deadlock", bye.get("ok") is True and bye.get("exited") is True,
          bye.get("code") or bye.get("message"))

    rpc.close()

    # Regression for the thread-affinity bug: a graph tool must work as the FIRST
    # tool of a fresh process without initialising COM on the stdio reader thread.
    cold = Rpc(PY, SERVER)
    cold.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "cold", "version": "1"}})
    cold.notify("notifications/initialized")
    first = cold.tool("origin_style", {"graph": "gr-999", "x_title": "nope"}, timeout=200)
    check("graph tool safe as first call", first.get("code") == "graph_not_found",
          (first.get("code"), str(first.get("message", ""))[:60]))
    cold.close()

    # Protocol abuse: dsh spawns this process once per session, so one malformed
    # line must never take the whole tool set down.
    proto = Rpc(PY, SERVER)
    proto.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "abuse", "version": "1"}})
    proto.notify("notifications/initialized")
    # Junk ids live far away from the client's counter, otherwise the waiter
    # matches a stale reply and the test lies about the outcome.
    for junk in ('[{"jsonrpc":"2.0","id":9001,"method":"ping"}]', "42", '"ping"',
                 '{"jsonrpc":"2.0","id":9002,"method":"tools/call","params":4}',
                 '{"jsonrpc":"2.0","id":9003,"method":"tools/call","params":{"name":"origin_status","arguments":"nope"}}',
                 '{"jsonrpc":"2.0","id":9004,"method":"no_such_method"}'):
        proto.raw(junk)
    alive = proto.request("ping", timeout=40)
    check("survives malformed messages", alive is not None and "result" in alive
          and proto.p.poll() is None, (alive, proto.p.poll()))
    still = proto.tool("origin_status", {}, timeout=120)
    check("tools still work after abuse", still.get("connected") is True, still.get("code"))
    proto.close()

    print("\nartifacts:")
    for f in sorted(os.listdir(OUT)):
        print("  %s (%d bytes)" % (f, os.path.getsize(os.path.join(OUT, f))))
    print("ALL GOOD")


if __name__ == "__main__":
    main()
