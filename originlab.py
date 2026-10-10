"""Origin automation engine.

Every COM call is marshalled onto one dedicated worker thread: comtypes/COM is
thread-affine, and driving Origin from the stdio reader thread is what makes
other bridges hang.

Object references handed to the model are opaque handles ("ws-1", "gr-2") backed
by a registry, because parsing Origin's own [Book]Sheet! range strings back and
forth is the most common source of silent failures.
"""

import atexit
import math
import os
import queue
import re
import struct
import subprocess
import sys
import threading
import time

import asciiio
import binio

MISSING_SENTINEL = -1.23456789e-300
# Verified against OriginPro 2024 SR1 by exporting each code and looking at the
# image: 200 line, 201 symbol, 202 line+symbol, 203 column.
PLOT_CODES = {"line": 200, "scatter": 201, "line_symbol": 202, "column": 203}
# Same four kinds as the letters add_plot() takes (verified by viewing the export).
PLOT_LETTERS = {"line": "l", "scatter": "s", "line_symbol": "y", "column": "c"}
# originpro passes this integer straight to layadd, and the layer's own name is the
# proof: read back as BottomXLeftY/TopX/RightY/LeftY/TopXRightY/BottomXRightY/Inset.
LAYER_TYPES = {"bottom_left": 0, "top": 1, "right": 2, "left": 3,
               "top_right": 4, "bottom_right": 5, "inset": 6}
# What Origin itself calls each layer, read back from layer.name after add_layer.
LAYER_NAME_BY_CODE = {0: "BottomXLeftY", 1: "TopX", 2: "RightY", 3: "LeftY",
                      4: "TopXRightY", 5: "BottomXRightY", 6: "Inset"}
# originpro's scale setter takes a name but the getter returns the int.
SCALE_TYPES = {"linear": 1, "log10": 2, "probability": 3, "probit": 4, "reciprocal": 5,
               "offset_reciprocal": 6, "logit": 7, "ln": 8, "log2": 9}
# new_graph(template=NAME) accepts these OriginPro 2024 template stems (extension
# optional) and returns None for an unknown one, which is how they get validated.
GRAPH_TEMPLATES = ("line", "scatter", "linesymb", "column", "doubley", "dply",
                   "heat_map", "cmap", "mesh", "contline", "contgray", "3d",
                   "glwireface", "pie", "box", "histdist", "polar", "ternary",
                   "vector", "errbar")
# NLFit(func) raises "Invalid fitting function" for anything not installed, so
# these were each constructed successfully on OriginPro 2024 SR1.
FIT_PRESETS = {"gauss": "Gauss", "gaussian": "Gauss", "lorentz": "Lorentz",
               "voigt": "Voigt", "expdec1": "ExpDec1", "expdec2": "ExpDec2",
               "sine": "Sine", "power": "Power", "logistic": "Logistic",
               "boltzmann": "Boltzmann", "doseresp": "DoseResp", "cubic": "Cubic"}
# Legend anchors are layer DATA coordinates (Legend.x/.y read back exactly), so a
# position is a fraction of the axis span rather than a page measurement.
LEGEND_POSITIONS = {
    "top_left": (-0.30, 1.10), "top": (0.50, 1.10), "top_right": (1.30, 1.10),
    "left": (-0.30, 0.50), "right": (1.30, 0.50),
    "bottom_left": (-0.30, -0.10), "bottom": (0.50, -0.10), "bottom_right": (1.30, -0.10),
    "inside_top_left": (0.12, 0.88), "inside_top_right": (0.88, 0.88),
    "inside_bottom_left": (0.12, 0.12), "inside_bottom_right": (0.88, 0.12),
    "inside": (0.75, 0.80),
}
IMAGE_MAGIC = {".png": b"\x89PNG\r\n\x1a\n", ".tif": b"II*\x00", ".tiff": b"II*\x00",
               ".pdf": b"%PDF", ".svg": b"<", ".emf": b"\x01\x00\x00\x00"}
ASCII_EXTS = (".dat", ".csv", ".txt", ".tsv")


class OriginError(Exception):
    def __init__(self, code, message, next_actions=(), **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_actions = list(next_actions)
        self.extra = extra


# ---------------------------------------------------------------- COM thread

_q = queue.Queue()
_thread = None
_thread_lock = threading.Lock()
_op = None


def _worker():
    while True:
        fn, args, kwargs, box = _q.get()
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # re-raised on the caller's thread
            box["error"] = exc
        box["done"].set()


def call(fn, *args, timeout=240, **kwargs):
    global _thread
    with _thread_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, daemon=True, name="origin-com")
            _thread.start()
    box = {"done": threading.Event()}
    _q.put((fn, args, kwargs, box))
    if not box["done"].wait(timeout):
        raise OriginError(
            "com_timeout",
            "Origin 在 %d 秒内没有响应" % timeout,
            ["看 Origin 窗口是否卡在对话框或许可证弹窗", "任务管理器结束 Origin64.exe 后重试"],
        )
    if "error" in box:
        raise box["error"]
    return box["value"]


def origin():
    """Import originpro and force the COM connection (launches Origin if needed)."""
    global _op
    if _op is not None:
        return _op
    try:
        import originpro as op
    except ImportError as exc:
        raise OriginError("missing_originpro", "这个 Python 环境里没有 originpro：%s" % exc,
                          ["pip install originpro"])
    # Record who was already running: anything that appears after we connect was
    # started by us, and anything windowless that predates us is an orphan left by
    # a client that exited without releasing its Origin.
    before = _process_rows()
    try:
        op.path()
    except Exception as exc:
        raise OriginError(
            "connection_error",
            "连不上 Origin 自动化服务器：%s" % exc,
            ["确认装了 Origin/OriginPro 2021 或更高（originpro 的硬性下限）",
             "确认 Origin 与 Python 都是 64 位",
             "若任务管理器里有多个 Origin64.exe，只保留一个",
             "长会话攒页面后桥会坏：先 origin_exit 让插件重启 Origin",
             "实例数到上限时：origin_reclaim 会关掉没有窗口的残留实例"],
        )
    existing = {row["pid"] for row in before} if before is not None else set()
    after = _process_rows()
    if after is not None:
        _started_pids.update(row["pid"] for row in after if row["pid"] not in existing)
    _op = op
    return op


# ------------------------------------------------------- instance lifecycle

ORIGIN_EXE = "Origin64.exe"
_started_pids = set()   # instances that appeared because WE connected


def _process_rows():
    """List Origin64 processes as {pid, started, window}, or None if unknown.

    MainWindowHandle is what separates an instance the human can see from an
    orphan a departing client left behind: originpro launches Origin on demand
    and Origin does not quit when its client process exits.

    A failed probe returns None, never an empty list: an empty list reads as
    "there is nothing to reclaim", which is the one thing a caller must not be
    told when we could not actually look.
    """
    if os.name != "nt":
        return []
    script = ("Get-Process Origin64 -ErrorAction SilentlyContinue | ForEach-Object {"
              "\"$($_.Id)|\" + $(try { $_.StartTime.ToString('o') } catch { '' }) + "
              "\"|$($_.MainWindowHandle)\"}")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                             capture_output=True, text=True, timeout=25).stdout
    except Exception:
        return None
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        try:
            rows.append({"pid": int(parts[0]), "started": parts[1], "window": int(parts[2] or 0)})
        except ValueError:
            continue
    return rows


def _terminate(pid, force=False):
    args = ["taskkill"] + (["/F"] if force else []) + ["/PID", str(pid)]
    return subprocess.run(args, capture_output=True, text=True, timeout=30)


def _close_pid(pid):
    """Ask for a polite exit first; escalate to a forced kill only if it survives."""
    _terminate(pid)
    for _ in range(6):
        time.sleep(0.5)
        rows = _process_rows()
        if rows is None:
            continue  # probe failed; keep polling rather than guessing "gone"
        if pid not in {row["pid"] for row in rows}:
            return "closed"
    rows = _process_rows()
    if rows is not None and pid not in {row["pid"] for row in rows}:
        return "closed"
    _terminate(pid, force=True)
    return "force_closed"


def instances():
    """Report every Origin instance: background (reclaimable) vs foreground (the user's)."""
    rows = _process_rows()
    if rows is None:
        return sanitize({"count": None, "probe": "unavailable",
                         "error": "读不到进程表（powershell 不可用或超时），无法确定实例数量",
                         "next_actions": ["重试 origin_instances", "或在任务管理器里看 Origin64.exe"]})
    for row in rows:
        row["foreground"] = bool(row["window"])
        row["ours"] = row["pid"] in _started_pids
    return sanitize({"count": len(rows), "probe": "ok",
                     "background": [r for r in rows if not r["foreground"]],
                     "foreground": [r for r in rows if r["foreground"]],
                     "started_by_us": sorted(_started_pids)})


def reclaim(close_background=True):
    """Close windowless Origin instances; a visible window is reported, never killed.

    An instance with no window has no client and no human in front of it. One with
    a window may be the user's own unsaved work, so it is listed for them to close.
    """
    rows = _process_rows()
    if rows is None:
        return sanitize({"closed": [], "force_closed": [], "left_running": [],
                         "probe": "unavailable",
                         "error": "读不到进程表，没有动任何进程",
                         "notify": "无法确认实例情况（进程探测失败），这次什么都没关。"})
    back = [r for r in rows if not r["window"]]
    front = [r for r in rows if r["window"]]
    result = {"probe": "ok", "closed": [], "force_closed": [],
              "left_running": [r["pid"] for r in front], "notify": ""}
    if not close_background:
        result["skipped"] = [r["pid"] for r in back]
        return sanitize(result)

    for row in back:
        pid = row["pid"]
        try:
            outcome = _close_pid(pid)
        except Exception as exc:
            result["left_running"].append(pid)
            result["notify"] += "PID %s 关不掉：%s；" % (pid, exc)
            continue
        result[outcome].append(pid)

    if front:
        result["notify"] += ("检测到 %d 个**有窗口**的 Origin 实例，没有动它们（可能是你自己开着的项目，"
                            "含未保存工作）。要释放实例配额请自己关掉这些窗口，"
                            "或先 origin_save_project 保存。") % len(front)
    if not result["notify"]:
        result["notify"] = "已回收全部后台实例，没有发现前台窗口。"
    _started_pids.difference_update(result["closed"] + result["force_closed"])
    return sanitize(result)


def _release_on_exit():
    """A client that exits must not leave its own Origin instance running."""
    if not _started_pids:
        return
    try:
        rows = _process_rows()
        if rows is None:
            return  # cannot tell which pids are ours; leaving Origin is the safe error
        for row in rows:
            if row["pid"] in _started_pids and not row["window"]:
                _close_pid(row["pid"])
    except Exception:
        pass


atexit.register(_release_on_exit)


# ---------------------------------------------------------------- handles

_handles = {}
_names = {}
_seq = [0]
_project_file = ""


def _bind(kind, obj, name=""):
    """Hand back an opaque handle, remembering the page name WITHOUT a COM call.

    Reading .name later can hit a destroyed page and leave a C-level error pending,
    which then surfaces as a confusing SystemError from an unrelated len() call.
    """
    _seq[0] += 1
    ref = "%s-%d" % (kind, _seq[0])
    _handles[ref] = obj
    _names[ref] = name or ""
    return ref


def _resolve(kind, ref):
    """Handle-table lookup only. Never touches COM: the caller may be on the
    server thread, and origin() must run on the COM thread (see _graph_of)."""
    if ref is None:
        return None
    if not isinstance(ref, str):
        return ref
    return _handles.get(ref)


def _graph_of(op, ref):
    """Resolve a graph handle. Must be called ON the COM thread: _resolve's
    fallback used to call origin() from the caller thread, which initialised COM
    on the wrong apartment while still working by luck."""
    if not ref:
        return _safe(lambda: op.find_graph(""))
    if not isinstance(ref, str):
        return ref
    obj = _handles.get(ref)
    if obj is not None:
        return obj
    if ref.startswith("gr-"):
        # An unknown handle must NOT fall back to the active page: previewing or
        # exporting some other graph while claiming success is worse than failing.
        return None
    return _safe(lambda: op.find_graph(ref))


def sanitize(obj):
    """Origin encodes missing numbers as -1.23456789e-300; never leak that upstream."""
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, float) and (obj != obj or abs(obj - MISSING_SENTINEL) < 1e-310):
        return None
    return obj


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _round2(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _col_index(ws, col):
    ncol = int(_safe(lambda: ws.cols, 0) or 0)
    idx = int(col) if isinstance(col, float) and float(col).is_integer() else (
        int(col) if isinstance(col, int) else None)
    if idx is not None:
        idx = idx - 1 if idx >= 1 else idx
        if 0 <= idx < ncol:
            return idx
        raise OriginError("bad_column", "列号 %s 超出范围（共 %d 列）" % (col, ncol),
                          ["列号从 1 开始，或用列名字符串"])
    for i in range(ncol):
        if str(_safe(lambda i=i: ws.get_label(i, "L"), "")).strip().lower() == str(col).strip().lower():
            return i
    names = [_safe(lambda i=i: ws.get_label(i, "L"), "") for i in range(ncol)]
    raise OriginError("bad_column", "找不到列 %r，实际列名：%s" % (col, names))


def _col_exists(ws, col):
    try:
        _col_index(ws, col)
        return True
    except OriginError:
        return False


def _extend_to(ws, col):
    """Add columns up to the requested 1-based index so a formula can create one."""
    if isinstance(col, str):
        raise OriginError("bad_column", "要新建派生列请给列号，不要给列名：%r" % col)
    want = int(col)
    if want < 1:
        raise OriginError("bad_column", "列号从 1 开始，收到 %s" % col)
    have = int(_safe(lambda: ws.cols, 0) or 0)
    if want > have + 10:
        raise OriginError("bad_column", "列号 %d 离现有 %d 列太远" % (want, have))
    ws.cols = want
    return want - 1


def _sheet_info(ws):
    rows = int(_safe(lambda: ws.rows, 0) or 0)
    cols = int(_safe(lambda: ws.cols, 0) or 0)
    nm = _safe(lambda: ws.name, "")
    return {
        "worksheet": _bind("ws", ws, nm),
        "name": nm,
        "rows": rows,
        "cols": cols,
        "columns": [{"index": i + 1,
                     "label": _safe(lambda i=i: ws.get_label(i, "L"), ""),
                     "units": _safe(lambda i=i: ws.get_label(i, "U"), ""),
                     "axis": _safe(lambda i=i: str(ws.get_label(i, "D")), "")} for i in range(cols)],
    }


def _verify_file(path, must_be_image=True):
    size = os.path.getsize(path)
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        head = fh.read(24)
    magic = IMAGE_MAGIC.get(ext) if must_be_image else None
    ok = size > 1024 and (magic is None or head.startswith(magic))
    dims = None
    if ext == ".png" and head[12:16] == b"IHDR":
        dims = list(struct.unpack(">II", head[16:24]))
    return {"ok": ok, "path": path, "bytes": size, "dimensions": dims, "head": head[:8].hex()}


# ---------------------------------------------------------------- operations


def status():
    def run():
        op = origin()
        paths = {t: _safe(lambda t=t: op.path(t), "") for t in ("u", "p", "e", "l", "s")}
        return {
            "connected": True,
            "origin_version_raw": _safe(op.org_ver),
            "python": sys.executable,
            "paths": paths,
            "pro_hint": any("originpro" in str(v).lower() for v in paths.values() if v),
            "open_pages": _safe(lambda: [p.name for p in op.pages()], []),
            "note": "pro_hint 只是从安装路径推断；Pro 能力以 origin_fit 能否跑通为准",
        }

    return call(run)


def read_file(path, comment=None):
    """Parse a data file without touching Origin.

    Cheap and side-effect free, so the model can look at a messy file before
    deciding what to plot. Origin's own ASCII import is the thing that needs a
    second opinion, not this.
    """
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise OriginError("file_not_found", "文件不存在：%s" % path)
    if os.path.splitext(path)[1].lower() == ".bin":
        try:
            decoded = binio.read_bin(path)
        except binio.BinFormatError as exc:
            raise OriginError("parse_failed", str(exc),
                              ["这个 .bin 不是电化学工作站导出的格式",
                               "工作站二进制没有通用规范，需要按厂商解析"])
        decoded.pop("series", None)          # values belong in Origin, not in context
        decoded["proof_level"] = "verified" if decoded["axis"] == "reconstructed" \
            else "readback_only"
        decoded["next_actions"] = [
            "origin_import 会把 X/Y 两列写进 Origin 工作表",
            "axis=none 时先问用户要 X 轴（EIS 的频率表不在文件里）",
        ]
        return sanitize(decoded)
    try:
        parsed = asciiio.parse(path, comment=comment)
    except ValueError as exc:
        raise OriginError("parse_failed", str(exc),
                          ["确认文件里有数值行", "给 comment 指定实际的注释前缀"])
    parsed["columns"] = [{k: v for k, v in c.items() if k != "values"}
                         for c in parsed["columns"]]
    parsed["proof_level"] = "verified"
    return sanitize(parsed)


def _import_ascii(op, path, book_name, parsed):
    """Fill a fresh sheet from our own parser, keeping long names and units."""
    ws = op.new_sheet(lname=book_name or "")
    ws.cols = max(1, len(parsed["columns"]))
    for i, col in enumerate(parsed["columns"]):
        axis = "X" if i == 0 else "Y"
        ws.from_list(i, col["values"], lname=col["name"], units=col["units"], axis=axis)
    return ws


def import_file(path, book_name=""):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise OriginError("file_not_found", "文件不存在：%s" % path)
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".dat", ".csv", ".txt", ".tsv", ".xls", ".xlsx", ".wks", ".bin"):
        raise OriginError("unsupported_file_type", "不支持的类型 %s" % ext,
                          ["支持 .dat/.csv/.txt/.tsv/.xls/.xlsx/.wks/.bin"])

    def run():
        op = origin()
        if ext == ".bin":
            decoded = binio.read_bin(path)
            series = decoded["series"]
            ws = op.new_sheet(lname=book_name or decoded["technique_name"] or "")
            cols = []
            if series["x"]:
                cols.append((decoded["x"]["name"] or "X", series["x"], "X"))
            cols.append((decoded["y"]["name"] or "Y", series["y"], "Y"))
            ws.cols = len(cols)
            for i, (label, values, axis) in enumerate(cols):
                ws.from_list(i, values, lname=label, axis=axis,
                             comments="%s %s" % (decoded["technique"], decoded["technique_name"]))
            info = _sheet_info(ws)
            info.update({"source": path, "format": ext, "channel": "binio",
                         "technique": decoded["technique"],
                         "technique_name": decoded["technique_name"],
                         "method": decoded["method"], "axis": decoded["axis"],
                         "warnings": decoded["warnings"],
                         "proof_level": "verified" if decoded["axis"] == "reconstructed"
                                        else "readback_only"})
            if decoded["axis"] != "reconstructed":
                info["next_actions"] = ["没有重建出 X 轴：只有测量列，X 需要用户给参数或另配导出文件"]
            return sanitize(info)
        parsed = None
        if ext in ASCII_EXTS:
            # Origin's ASCII import gives up on space-aligned columns and on
            # multi-row headers by returning an empty sheet, so parse those here.
            try:
                parsed = asciiio.parse(path)
            except ValueError as exc:
                parsed = {"fallback_reason": str(exc)}
        if parsed is not None and "columns" in parsed:
            ws = _import_ascii(op, path, book_name, parsed)
            info = _sheet_info(ws)
            info.update({"source": path, "format": ext, "channel": "asciiio",
                         "delimiter": parsed["delimiter"], "encoding": parsed["encoding"],
                         "warnings": parsed["warnings"],
                         "units": [c["units"] for c in parsed["columns"]]})
            return sanitize(info)

        ws = op.new_sheet(lname=book_name or "")
        ws.from_file(path)
        info = _sheet_info(ws)
        if not info["rows"]:
            raise OriginError("import_empty", "读进来了但是 0 行：%s" % path,
                              ["自研解析器也失败了：%s" % (parsed or {}).get("fallback_reason", "见上"),
                               "确认文件里有数值行，或改用 origin_read_file 先看结构"])
        info.update({"source": path, "format": ext, "channel": "origin_from_file"})
        return sanitize(info)

    return call(run)


def write_columns(columns, book_name=""):
    if not isinstance(columns, dict) or not columns:
        raise OriginError("bad_arguments", "columns 必须是非空 dict：{列名: [数值...]}")
    lengths = {len(v) for v in columns.values() if isinstance(v, (list, tuple))}
    if len(lengths) > 1:
        raise OriginError("bad_arguments", "各列长度不一致：%s" % sorted(lengths))

    def run():
        op = origin()
        ws = op.new_sheet(lname=book_name or "")
        for i, (name, values) in enumerate(columns.items()):
            try:
                nums = [float(v) for v in values]
            except (TypeError, ValueError) as exc:
                raise OriginError("bad_arguments", "列 %r 里有非数值项：%s" % (name, exc),
                                  ["数值列只接受数字或 null；文本请放 Notes 页，或先把分类编码成数字"])
            ws.from_list(i, nums, str(name))
        return sanitize(_sheet_info(ws))

    return call(run)


def write_block(headers, rows, book_name=""):
    """Wide table in one shot. from_list costs one COM round trip per column,
    which is why a 40-column sheet is slow; from_list2 writes the block at once."""
    if not rows or not isinstance(rows, (list, tuple)):
        raise OriginError("bad_arguments", "rows 必须是非空的二维列表")
    ncol = max(len(r) for r in rows)
    if headers and len(headers) != ncol:
        raise OriginError("bad_arguments", "headers 有 %d 项，但 rows 最宽 %d 列" % (len(headers), ncol))

    def run():
        op = origin()
        ws = op.new_sheet(lname=book_name or "")
        try:
            if int(ws.cols) < ncol:
                ws.cols = ncol
        except Exception:
            pass
        # originpro's from_list2 takes a list of COLUMNS, not of rows.
        cols_major = [[r[i] if i < len(r) else None for r in rows] for i in range(ncol)]
        ws.from_list2(cols_major)
        if headers:
            _safe(lambda: ws.set_labels([str(h) for h in headers], "L"))
        info = _sheet_info(ws)
        if not info["rows"]:
            raise OriginError("write_empty", "写入后 0 行，可能被 Origin 拒收",
                              ["确认每个值都是数字或空，不要塞字符串进数值列"])
        return sanitize(info)

    return call(run)


def column_formula(worksheet, col, formula, label="", units=""):
    """Set a column formula (Fx) so the column recomputes from its siblings."""
    ws = _resolve("ws", worksheet)
    if ws is None:
        raise OriginError("worksheet_not_found", "工作表句柄无效：%s" % worksheet)
    if not str(formula).strip():
        raise OriginError("bad_arguments", "formula 不能为空")

    def run():
        idx = _col_index(ws, col) if _col_exists(ws, col) else _extend_to(ws, col)
        # originpro 1.1.15 documents set_formula's column as 1-offset, but it is
        # 0-based here: passing idx+1 grabs a non-existent column and the library
        # dies inside SetStrProp on a None object.
        ws.set_formula(idx, str(formula))
        if label:
            _safe(lambda: ws.set_label(idx, label, "L"))
        if units:
            _safe(lambda: ws.set_label(idx, units, "U"))
        vals = _safe(lambda: [v for v in ws.to_list(idx) if v is not None], [])
        info = _sheet_info(ws)
        if not vals:
            raise OriginError("formula_no_effect",
                              "公式设了但这一列还是空的：%s" % formula,
                              ["LabTalk 里底 10 对数是 log() 不是 log10()",
                               "跨列引用要先绑定 range，不能内联 [Book]Sheet!col(N)",
                               "列号用 Col(1) 这种当前表写法试试"])
        return sanitize({"column": idx + 1, "formula": str(formula),
                         "computed_rows": len(vals), "first": vals[0], "last": vals[-1],
                         "columns": info["columns"], "proof_level": "verified"})

    return call(run)


def plot(worksheet, x=1, y=2, plot_type="line", graph_name="", title="",
         graph="", layer=0, template=""):
    """Draw one or more series.

    A single x/y pair on a fresh page goes through LabTalk plotxy, which is the
    path Origin's own toolbar uses and therefore gets the default theme. Anything
    else (several y columns, a second layer, a named template) uses
    layer.add_plot + rescale, which is the only way that provably targets a given
    layer: plotxy with a qualified range returned True while drawing somewhere else.
    """
    if plot_type not in PLOT_CODES:
        raise OriginError("bad_plot_type", "plot_type 只能是 %s" % sorted(PLOT_CODES))
    ws = _resolve("ws", worksheet)
    if ws is None:
        raise OriginError("worksheet_not_found", "句柄无效：%s（用 origin_import 返回的 worksheet）" % worksheet)
    ys = [y] if isinstance(y, (int, float, str)) else list(y or [])
    if not ys:
        raise OriginError("bad_arguments", "y 不能为空")
    tpl = str(template or "").strip()
    if tpl and tpl.lower() not in GRAPH_TEMPLATES:
        raise OriginError("bad_template", "没有这个图模模板：%r" % template,
                          ["可用：%s" % ", ".join(sorted(GRAPH_TEMPLATES))])

    def run():
        op = origin()
        xi = _col_index(ws, x)
        yis = [_col_index(ws, c) for c in ys]
        book = ws.get_book().name
        # plotxy resolves its range against the ACTIVE worksheet, so activate the
        # book first or it silently plots the wrong sheet.
        op.lt_exec("win -a %s;" % book)
        if len(yis) == 1 and not layer and not tpl:
            before = {g.name for g in _safe(lambda: op.graph_list(), []) or []}
            if not op.lt_exec("plotxy iy:=(%d,%d) plot:=%d;"
                              % (xi + 1, yis[0] + 1, PLOT_CODES[plot_type])):
                raise OriginError("plot_failed", "plotxy 返回 False，图没有画出来",
                                  ["确认两列都有数值（不是文本列）", "先用 origin_inspect 看列情况"])
            fresh = [g for g in op.graph_list() if g.name not in before]
            gr = fresh[-1] if fresh else op.find_graph("")
            if gr is None:
                raise OriginError("plot_failed", "plotxy 说成功了但项目里找不到新图页",
                                  ["origin_inspect 看当前页面清单"])
            channel = "plotxy"
        else:
            if layer:
                gr = _graph_of(op, graph)
                if gr is None:
                    raise OriginError("graph_not_found",
                                      "往第 %d 层画之前要先有图页：给 graph 句柄" % layer,
                                      ["origin_inspect 看现有图页", "或先 origin_plot 建第 0 层"])
            elif tpl:
                gr = op.new_graph(graph_name or "", template=tpl)
                if gr is None:
                    raise OriginError("bad_template", "Origin 拒绝用模板 %r 建图页" % tpl,
                                      ["模板名不区分大小写、可不带扩展名",
                                       "可用：%s" % ", ".join(sorted(GRAPH_TEMPLATES))])
            else:
                gr = op.new_graph(graph_name or "")
            try:
                lay = gr[int(layer)]
            except Exception:
                raise OriginError("bad_layer", "图页 %s 没有第 %d 层" % (gr.name, int(layer)),
                                  ["先 origin_add_layer 加层"])
            letter = PLOT_LETTERS[plot_type]
            for yi in yis:
                lay.add_plot(ws, yi, xi, type=letter)
            lay.rescale()
            channel = "add_plot"

        if graph_name and channel == "plotxy":
            _safe(lambda: setattr(gr, "lname", graph_name))
        if title:
            _safe(lambda: setattr(gr[0], "title", title))

        lay = gr[int(layer)]
        drawn = len(_safe(lambda: lay.plot_list(), []) or [])
        # Read the axes back rather than trusting the command's return value: the
        # add_plot path used to return a Plot object and still draw an empty 0..1 axis.
        x_from = _safe(lambda: lay.xlim[0])
        x_to = _safe(lambda: lay.xlim[1])
        xvals = _safe(lambda: [v for v in ws.to_list(xi) if v is not None], [])
        span = {"x_axis_from": x_from, "x_axis_to": x_to, "graph_page": gr.name,
                "layer": int(layer), "plots_in_layer": drawn}
        if xvals and x_from is not None and x_to is not None:
            want = max(xvals) - min(xvals)
            got = abs(x_to - x_from)
            span["x_data_span"] = want
            if want > 0 and got < want * 0.5:
                raise OriginError(
                    "plot_axis_degenerate",
                    "X 轴读回 %g..%g 与数据范围 %g..%g 不符，图是空的" % (x_from, x_to, min(xvals), max(xvals)),
                    ["确认 x 指定的列是数值列且非空"],
                )
        if drawn < len(yis):
            raise OriginError("plot_incomplete",
                              "要求 %d 条曲线，层里只有 %d 条" % (len(yis), drawn),
                              ["origin_inspect 看该层，或改用 origin_view 目视确认"])
        return sanitize({"graph": _bind("gr", gr, gr.name), "plot_type": plot_type,
                         "channel": channel, "template": tpl,
                         "source_sheet": _safe(lambda: ws.name, ""),
                         "x_column": xi + 1, "y_columns": [c + 1 for c in yis],
                         "proof_level": "verified" if xvals else "readback_only", **span})

    return call(run)


def style(graph, layer=0, series=None, x_title="", y_title="", x_scale="", y_scale="",
          xlim=None, ylim=None, xtick=0.0, ytick=0.0, color="", symbol_kind=0,
          symbol_size=0.0, symbol_interior=0, line_width=0.0, transparency=0.0,
          fill_area=False):
    """Apply edits and read every one of them back.

    Plot exposes getters (color/symbol_kind/symbol_size/...), so a style op is
    only reported as applied when the value comes back; the old version trusted
    setattr and could claim success on a silent no-op.
    """
    series = 0 if series is None else int(series)

    def run():
        op = origin()
        gr = _graph_of(op, graph)
        if gr is None:
            raise OriginError("graph_not_found", "图页句柄无效：%s" % graph)
        try:
            lay = gr[layer]
        except Exception:
            raise OriginError("bad_layer", "图页 %s 没有第 %d 层" % (gr.name, layer),
                              ["先用 origin_inspect 看层数"])
        applied, failed = [], []

        def attempt(desc, setter, getter=None, expect=None):
            try:
                setter()
            except Exception as exc:
                failed.append({"op": desc, "error": str(exc)[:120]})
                return
            if getter is None:
                applied.append({"op": desc, "readback": None})
                return
            got = _safe(getter)
            ok = True if expect is None else (got == expect)
            (applied if ok else failed).append({"op": desc, "readback": got,
                                                **({} if ok else {"wanted": expect})})

        if x_title:
            attempt("x_title", lambda: setattr(lay.axis("x"), "title", x_title),
                    lambda: lay.axis("x").title, x_title)
        if y_title:
            attempt("y_title", lambda: setattr(lay.axis("y"), "title", y_title),
                    lambda: lay.axis("y").title, y_title)
        if x_scale:
            want = SCALE_TYPES.get(str(x_scale).lower())
            if want is None:
                raise OriginError("bad_scale", "未知刻度类型：%r" % x_scale,
                                  ["可选 %s" % sorted(SCALE_TYPES)])
            attempt("x_scale", lambda: setattr(lay, "xscale", x_scale), lambda: lay.xscale, want)
        if y_scale:
            want = SCALE_TYPES.get(str(y_scale).lower())
            if want is None:
                raise OriginError("bad_scale", "未知刻度类型：%r" % y_scale,
                                  ["可选 %s" % sorted(SCALE_TYPES)])
            attempt("y_scale", lambda: setattr(lay, "yscale", y_scale), lambda: lay.yscale, want)
        if xlim:
            attempt("xlim", lambda: lay.set_xlim(*_pair(xlim)), lambda: lay.xlim)
        if ylim:
            attempt("ylim", lambda: lay.set_ylim(*_pair(ylim)), lambda: lay.ylim)
        if xtick:
            attempt("xtick", lambda: lay.set_xlim(step=xtick), lambda: lay.xlim)
        if ytick:
            attempt("ytick", lambda: lay.set_ylim(step=ytick), lambda: lay.ylim)

        if series is not None and (color or symbol_kind or symbol_size or symbol_interior
                                   or line_width or transparency or fill_area):
            plots = _safe(lambda: lay.plot_list(), []) or []
            if not plots:
                failed.append({"op": "series", "error": "这一层里没有任何曲线（plot_list 为空）"})
            elif series >= len(plots):
                raise OriginError("bad_series", "series=%d 超出范围（本层 %d 条曲线）" % (series, len(plots)),
                                  ["origin_inspect 会返回每层的曲线数"])
            else:
                p = plots[series]
                if color:
                    attempt("p%d.color" % series, lambda: setattr(p, "color", color),
                            lambda: p.color)
                if line_width:
                    # set_cmd("w=...") reads back unchanged: it is a dialog command,
                    # not a property write. set_float('line.width') is the real one.
                    attempt("p%d.line.width" % series,
                            lambda w=float(line_width): p.set_float("line.width", w),
                            lambda: _round2(_safe(lambda: p.get_float("line.width"))),
                            round(float(line_width), 2))
                if symbol_kind:
                    attempt("p%d.symbol_kind" % series, lambda: setattr(p, "symbol_kind", symbol_kind),
                            lambda: p.symbol_kind, symbol_kind)
                if symbol_size:
                    attempt("p%d.symbol_size" % series, lambda: setattr(p, "symbol_size", symbol_size),
                            lambda: p.symbol_size)
                if symbol_interior:
                    attempt("p%d.symbol_interior" % series,
                            lambda: setattr(p, "symbol_interior", symbol_interior),
                            lambda: p.symbol_interior, symbol_interior)
                if transparency:
                    attempt("p%d.transparency" % series, lambda: setattr(p, "transparency", transparency),
                            lambda: p.transparency)
                if fill_area:
                    attempt("p%d.fill_area" % series, lambda: p.set_fill_area(9, 9))

        out = {"graph": gr.name, "layer": layer, "series": series,
               "applied": applied, "failed": failed,
               "proof_level": "verified" if applied and not failed else
                              ("unverified" if not applied else "partial")}
        if failed:
            out["next_actions"] = ["读回不一致的项改用 origin_labtalk 走 LabTalk，或 origin_view 目视确认"]
        return sanitize(out)

    return call(run)


def _pair(v):
    """Accept [begin,end] / [begin,end,step] / (begin,end)."""
    vals = list(v)
    while len(vals) < 3:
        vals.append(None)
    return vals[:3]


def annotate(graph, labels=(), lines=(), layer=0):
    """Text labels and reference lines in data coordinates."""
    if not labels and not lines:
        raise OriginError("bad_arguments", "labels 和 lines 至少给一个")

    def run():
        gr = _graph_of(origin(), graph)
        if gr is None:
            raise OriginError("graph_not_found", "图页句柄无效：%s" % graph)
        lay = gr[layer]
        done = []
        for item in labels or ():
            text = str(item.get("text", ""))
            x, y = item.get("x"), item.get("y")
            lab = _safe(lambda: lay.add_label(text, x, y))
            done.append({"kind": "label", "text": text, "at": [x, y], "ok": lab is not None})
        for item in lines or ():
            coords = [item.get(k) for k in ("x1", "y1", "x2", "y2")]
            ln = _safe(lambda: lay.add_line(*coords))
            done.append({"kind": "line", "coords": coords, "ok": ln is not None,
                         "width": _safe(lambda: ln.width) if ln is not None else None,
                         "type": _safe(lambda: ln.type) if ln is not None else None})
        ok_all = all(d["ok"] for d in done)
        return sanitize({"graph": gr.name, "layer": layer, "items": done,
                         "proof_level": "readback_only" if ok_all else "unverified",
                         "note": "标注没有值可读回，要确证请 origin_view 看图",
                         **({} if ok_all else {"next_actions": ["有项没加上：确认坐标落在轴范围内"]})})

    return call(run)


def legend_entries(graph="", layer=0, entries=None, position="", show=True):
    """Add-Modify the layer's legend, with readbacks.

    `legend -r` regenerates one row per plot using each Y column's long name
    (rendered from the %(i) tokens), which is what makes the names appear instead
    of the "111" that `legend 1` produces. Position is written as layer DATA
    coordinates because that is the only legend geometry that reads back.
    """
    if position and position.lower() not in LEGEND_POSITIONS:
        raise OriginError("bad_position", "图例位置不认识：%r" % position,
                          ["可用：%s" % ", ".join(sorted(LEGEND_POSITIONS))])

    def run():
        op = origin()
        gr = _graph_of(op, graph)
        if gr is None:
            raise OriginError("graph_not_found", "图页句柄无效：%s" % (graph or ""),
                              ["先 origin_plot 建图，或 origin_inspect 看现有图页"])
        try:
            lay = gr[int(layer)]
        except Exception:
            raise OriginError("bad_layer", "图页 %s 没有第 %d 层" % (gr.name, int(layer)))
        op.lt_exec("win -a %s;" % gr.name)
        _safe(lambda: lay.activate())
        lab = _safe(lambda: lay.label("Legend"))
        if lab is None:
            _safe(lambda: op.lt_exec("legend -r;"))
            lab = _safe(lambda: lay.label("Legend"))
        if lab is None:
            raise OriginError("legend_failed", "这一层里没有 Legend 对象，也建不出来",
                              ["确认层里已经画了曲线（origin_inspect 看 plots 数）"])
        applied, failed = [], []

        def attempt(desc, setter, getter=None, expect=None):
            try:
                setter()
            except Exception as exc:
                failed.append({"op": desc, "error": str(exc)[:120]})
                return
            got = _safe(getter) if getter else None
            ok = True if expect is None else (got == expect)
            (applied if ok else failed).append({"op": desc, "readback": got,
                                                **({} if ok else {"wanted": expect})})

        attempt("show", lambda: op.lt_exec("Legend.Show = %d;" % (1 if show else 0)),
                lambda: op.lt_int("Legend.Show"), 1 if show else 0)
        if entries:
            text = "\r".join(str(e) for e in entries)
            attempt("text", lambda: setattr(lab, "text", text),
                    lambda: str(_safe(lambda: lab.text, "")).replace("\r\n", "\r"), text)
        else:
            attempt("auto_from_columns", lambda: op.lt_exec("legend -r;"),
                    lambda: _safe(lambda: len(str(_safe(lambda: lab.text, "")).splitlines())))
        placed = None
        if position:
            fx, fy = LEGEND_POSITIONS[position.lower()]
            x0, x1 = _safe(lambda: lay.xlim[:2], (0, 1)) or (0, 1)
            y0, y1 = _safe(lambda: lay.ylim[:2], (0, 1)) or (0, 1)
            tx, ty = x0 + fx * (x1 - x0), y0 + fy * (y1 - y0)
            placed = [tx, ty]
            try:
                op.lt_exec("Legend.x = %g; Legend.y = %g;" % (tx, ty))
                # Origin round-trips these through page units, so compare with a
                # tolerance: 78.0 written comes back as 78.01.
                got = [_safe(lambda: op.lt_float("Legend.x")), _safe(lambda: op.lt_float("Legend.y"))]
                tol = max(0.05, 0.005 * max(abs(tx), abs(ty)))
                ok = all(g is not None and abs(g - w) <= tol for g, w in zip(got, (tx, ty)))
                (applied if ok else failed).append(
                    {"op": "position:%s" % position, "readback": got,
                     **({} if ok else {"wanted": [tx, ty]})})
            except Exception as exc:
                failed.append({"op": "position:%s" % position, "error": str(exc)[:120]})
        return sanitize({"graph": gr.name, "layer": int(layer),
                         "text": _safe(lambda: lab.text, ""),
                         "wanted_position": position, "anchor_data_coords": placed,
                         "applied": applied, "failed": failed,
                         "proof_level": "verified" if applied and not failed else
                                        ("unverified" if not applied else "partial"),
                         "note": "图例锚点是图例中心，坐标为该层的数据坐标"})

    return call(run)


def add_layer(graph, layer_type="right"):
    """Add a layer to an existing graph page (dual-Y, stacked panels).

    The layer's own name is the proof: Origin derives it from the position, so a
    right-Y layer reads back as 'RightY'. Filling it is left to origin_plot with
    layer=<index>, which is the only path measured to draw into a chosen layer.
    """
    if isinstance(layer_type, str) and layer_type.lower() in LAYER_TYPES:
        code = LAYER_TYPES[layer_type.lower()]
    elif isinstance(layer_type, int) and layer_type in LAYER_TYPES.values():
        code = layer_type
    else:
        raise OriginError("bad_layer_type", "layer_type 不认识：%r" % (layer_type,),
                          ["可用：%s" % ", ".join(sorted(LAYER_TYPES))])

    def run():
        gr = _graph_of(origin(), graph)
        if gr is None:
            raise OriginError("graph_not_found", "图页句柄无效：%s" % graph)
        before = _layer_count(gr)
        lay = _safe(lambda: gr.add_layer(code))
        after = _layer_count(gr)
        if lay is None and after <= before:
            raise OriginError("add_layer_failed", "加层失败（layer_type=%d）" % code,
                              ["layer_type 见 originlab.com/doc/X-Function/ref/layadd",
                               "可用名称：%s" % ", ".join(sorted(LAYER_TYPES))])
        made = _safe(lambda: lay.name, "") or ""
        return sanitize({"graph": _bind("gr", gr, gr.name), "graph_page": gr.name,
                         "layers_before": before, "layers_after": after,
                         "layer_type": code, "layer_type_name": LAYER_NAME_BY_CODE.get(code, "?"),
                         "layer_name_from_origin": made,
                         "layer_index": after - 1,
                         "proof_level": "verified" if made and made.endswith(
                             LAYER_NAME_BY_CODE.get(code, "?")) else "readback_only",
                         "next_actions": ["origin_plot 时给 graph 和 layer=%d 往这层画曲线" % (after - 1)]})

    return call(run)


def _layer_count(gr):
    n = 0
    while n < 16:
        if _safe(lambda i=n: gr[i]) is None:
            break
        n += 1
    return n


def view(graph, width=900):
    """Render the graph to a small PNG and hand the bytes back for visual check."""
    def run():
        import base64
        import tempfile
        op = origin()
        page = _graph_of(op, graph)
        if page is None:
            raise OriginError("graph_not_found", "没有可预览的图页；先 origin_inspect 看页面")
        # Unique name: a fixed path would let two dsh sessions overwrite each
        # other's preview mid-read.
        fd, tmp = tempfile.mkstemp(prefix="origin_bridge_view_", suffix=".png")
        os.close(fd)
        os.remove(tmp)
        # save_fig rejects a type that disagrees with the extension, and its
        # width only applies when a type is given, so try explicit then fall back.
        _safe(lambda: page.save_fig(tmp, "png", width=int(width)))
        if not os.path.isfile(tmp):
            _safe(lambda: page.save_fig(tmp))
        if not os.path.isfile(tmp):
            raise OriginError("preview_failed", "预览图没生成：%s" % tmp,
                              ["改用 origin_export 指定 ASCII 路径"])
        info = _verify_file(tmp)
        if not info["ok"]:
            raise OriginError("preview_suspect", "预览图异常：%s" % info)
        with open(tmp, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        _safe(lambda: os.remove(tmp))  # previews accumulate in %TEMP% otherwise
        return {"graph": _safe(lambda: page.name, ""), "mime": "image/png",
                "image_base64": b64, "bytes": info["bytes"], "dimensions": info["dimensions"]}

    return call(run)


LABTALK_DENY = ("doc -s", "doc -c", "saveas", "quit", "exitloop", "type -m", "openb", "run.x")


def labtalk(script, readback=(), numeric=()):
    """Escape hatch: run raw LabTalk, then read the variables you name.

    Destructive statements are refused because a mis-targeted command is the #1
    way these bridges corrupt a session, and because a bare script with no
    readback proves nothing.
    """
    if not str(script).strip():
        raise OriginError("bad_arguments", "script 不能为空")
    # Advisory guardrail, not a sandbox: it stops the model from wiping the
    # session by accident, not from trying hard. Whitespace is collapsed so
    # 'doc  -s' does not slip past the literal match.
    low = re.sub(r"\s+", " ", str(script).lower())
    blocked = [p for p in LABTALK_DENY if p in low]
    if blocked:
        raise OriginError("labtalk_denied", "这些命令不允许通过 origin_labtalk 执行：%s" % blocked,
                          ["关工程/退 Origin 请用 origin_exit 或 origin_save_project"])

    def run():
        op = origin()
        ret = op.lt_exec(str(script))
        strs = {}
        for name in readback or ():
            strs[str(name)] = _safe(lambda n=name: op.get_lt_str(n), None)
        nums = {}
        for name in numeric or ():
            def read(n=name):
                # lt_float returns nan for integer-typed LabTalk variables
                # (layer.showlegend, Legend.Show), which would make a successful
                # change look unverifiable.
                value = op.lt_float(n)
                if isinstance(value, float) and math.isnan(value):
                    return op.lt_int(n)
                return value
            nums[str(name)] = _safe(read, None)
        return sanitize({"script": str(script), "returned": bool(ret),
                         "readback": strs, "numeric": nums,
                         "proof_level": "verified" if (ret and (strs or nums)) else
                                        ("readback_only" if ret else "unverified"),
                         **({} if ret else {"next_actions": [
                             "LabTalk 返回 False：多半是命令本身报错或作用在不活动的窗口",
                             "先 win -a <页面短名>; 激活目标页再执行"]})})

    return call(run)


FIT_STATS = ("chisqr", "cod", "cor", "r", "adjr", "dof", "niter", "pts", "ssr", "rmse",
             "fitstatus", "func", "nfuncparams", "nderivparams", "confc", "confp")
FIT_PREFIXES = ("f", "s", "e", "u", "l", "ub", "lb", "ubon", "lbon", "ubx", "lbx", "d", "n")


def _trim_linear(res):
    p = res.get("Parameters") or {}
    st = (res.get("RegStats") or {}).get("C1") or {}

    def pv(name):
        d = p.get(name) or {}
        return {"value": d.get("Value"), "error": d.get("Error")}

    return {"equation": (res.get("Notes") or {}).get("Equation"),
            "slope": pv("Slope"), "intercept": pv("Intercept"),
            "r_squared": st.get("RSqCOD") or st.get("Rvalue"),
            "adj_r_squared": st.get("AdjRSq"), "n": st.get("N"),
            "dof": st.get("DOF"), "rmse": st.get("RMSESD")}


def _trim_nlfitsing(res):
    stats = {k: res[k] for k in FIT_STATS if k in res}
    params = {}
    for k, v in res.items():
        if k in stats or "_" in k:
            continue
        params[k] = v
    return {"parameters": params, "stats": stats}


def fit(worksheet, x=1, y=2, kind="linear", func="", fixed=None, starts=None,
        bounds=None, make_report=False, full=False):
    """Fit a curve. fixed/starts/bounds are {param_name: value} for NLFit.

    bounds value is [lo, hi] (either end may be null to leave it open).
    """
    ws = _resolve("ws", worksheet)
    if ws is None:
        raise OriginError("worksheet_not_found", "工作表句柄无效：%s" % worksheet)
    kind = str(kind).lower()
    if kind in FIT_PRESETS:
        func, kind = FIT_PRESETS[kind], "nlfitsing"
    if kind not in ("linear", "nlfitsing", "nlfit"):
        raise OriginError("bad_kind", "kind 只能是 linear、nlfitsing，或内置预设 %s"
                          % sorted(FIT_PRESETS))
    if kind == "nlfitsing" and str(func).lower() in FIT_PRESETS:
        func = FIT_PRESETS[str(func).lower()]

    def run():
        op = origin()
        xi, yi = _col_index(ws, x), _col_index(ws, y)
        if kind == "linear":
            lf = op.LinearFit()
            lf.set_data(ws, xi, yi)
            notes = []
            if starts and "Slope" in starts:
                notes.append({"fix_slope": _safe(lambda v=starts["Slope"]: lf.fix_slope(float(v)), "err") != "err"})
            if starts and "Intercept" in starts:
                notes.append({"fix_intercept": _safe(lambda v=starts["Intercept"]: lf.fix_intercept(float(v)), "err") != "err"})
            res = lf.result()
            if make_report:
                _safe(lf.report)
            body = res if full else _trim_linear(res)
            return sanitize({"kind": "linear", "constraints": notes,
                             "trimmed": not full, **({"result": body} if full else {"fit": body})})
        if not func:
            raise OriginError("bad_arguments", "非线性拟合必须给 func",
                              ["预设名：%s" % ", ".join(sorted(FIT_PRESETS)),
                               "或直接给 Origin 的函数名（区分大小写，如 Gauss 而不是 gauss1）"])
        try:
            nl = op.NLFit(func)
        except Exception as exc:
            raise OriginError("fit_function_unavailable", "函数 %r 不可用：%s" % (func, exc),
                              ["NLFit 需要 OriginPro 授权",
                               "本机实测可用的名字：%s" % ", ".join(sorted(set(FIT_PRESETS.values())))])
        nl.set_data(ws, xi, yi)
        notes = []
        for name, val in (starts or {}).items():
            notes.append({"set_param": name, "ok": _safe(lambda n=name, v=val: nl.set_param(n, float(v)), "err") != "err"})
        for name, val in (fixed or {}).items():
            notes.append({"fix_param": name, "ok": _safe(lambda n=name, v=val: nl.fix_param(n, float(v)), "err") != "err"})
        for name, pair in (bounds or {}).items():
            lo, hi = (pair + [None, None])[:2] if isinstance(pair, (list, tuple)) else (None, None)
            if lo is not None:
                _safe(lambda n=name, v=lo: nl.set_lbound(n, None, float(v)))
            if hi is not None:
                _safe(lambda n=name, v=hi: nl.set_ubound(n, None, float(v)))
            notes.append({"bounds": name, "lo": lo, "hi": hi})
        nl.fit()
        res = nl.result()
        if make_report:
            _safe(nl.report)
        _safe(nl.__del__)
        r2 = res.get("cod") if isinstance(res, dict) else None
        body = res if full else _trim_nlfitsing(res)
        return sanitize({"kind": "nlfitsing", "func": func, "r_squared": r2,
                         "fit_status": res.get("fitstatus") if isinstance(res, dict) else None,
                         "constraints": notes, "trimmed": not full,
                         **({"result": body} if full else {"fit": body})})

    return call(run, timeout=300)


def export(graph, path="", fmt="png", width=1600):
    fmt = str(fmt).lstrip(".").lower()
    if fmt not in ("png", "tif", "tiff", "svg", "pdf", "emf"):
        raise OriginError("bad_format", "fmt 只支持 png/tif/svg/pdf/emf，收到 %s" % fmt)

    def run():
        op = origin()
        page = _graph_of(op, graph)
        if page is None:
            raise OriginError("graph_not_found", "找不到图页：%s（项目里可能还没有图）" % (graph or ""))
        name = _safe(lambda: page.name, "Graph1")
        target_path = os.path.abspath(path or os.path.join(os.getcwd(), "%s.%s" % (name, fmt)))
        os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
        _safe(lambda: page.save_fig(target_path))
        if not os.path.isfile(target_path):
            raise OriginError("export_failed", "Origin 说导出了但文件不存在：%s" % target_path,
                              ["路径必须可写且最好是 ASCII", "先 origin_inspect 确认图页非空"])
        info = _verify_file(target_path)
        if not info["ok"]:
            raise OriginError("export_suspect", "导出文件过小或文件头不对：%s" % info,
                              ["图页可能是空的：origin_inspect 看 plots 数"])
        return sanitize(dict(info, channel="originpro.save_fig", graph=name))

    return call(run)


def _save_project(path=""):
    """Runs ON the COM thread. Call via save_project(); never from another run()."""
    def run():
        global _project_file
        op = origin()
        if path:
            target = os.path.abspath(path)
        elif _project_file:
            target = _project_file
        else:
            # Never default to the process CWD: under dsh that is the plugin's
            # node_modules folder, so an unnamed save would litter the install.
            target = os.path.join(_safe(lambda: op.path("u"), "") or os.getcwd(),
                                  time.strftime("origin_bridge_%Y%m%d_%H%M%S.opju"))
        if not target.lower().endswith((".opju", ".opj")):
            target += ".opju"
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)

        # op.save(file) is Origin's Save-As: once the project is bound to that
        # file, saving the same name again returns False without meaning failure.
        # Note originpro's path('p') is the project *folder*, so the bound file
        # has to be tracked here rather than asked from Origin.
        same_file = bool(_project_file) and os.path.normcase(_project_file) == os.path.normcase(target)
        mtime_before = os.path.getmtime(target) if os.path.isfile(target) else 0
        ok = bool(op.save()) if same_file else bool(op.save(target))

        if not os.path.isfile(target):
            raise OriginError("save_failed", "保存工程失败：%s (ok=%s)" % (target, ok),
                              ["确认目录可写、路径为 ASCII",
                               "换一个不冲突的文件名，或省略 path 用自动时间戳命名"])
        fresh = os.path.getmtime(target) > mtime_before
        if not ok and not (same_file and not fresh):
            raise OriginError("save_failed", "保存工程失败：%s (ok=%s)" % (target, ok),
                              ["该文件可能正被 Origin 占用（工程绑定后会加锁）",
                               "换一个不冲突的文件名，或省略 path 用自动时间戳命名"])
        info = _verify_file(target, must_be_image=False)
        if not info["ok"]:
            raise OriginError("save_suspect", "工程文件过小：%s" % info,
                              ["项目里得先有工作表或图页再保存"])
        _project_file = target
        out = dict(info, editable=True, saved=bool(ok),
                   note="可在 Origin 里继续编辑的 .opju，不是图片快照")
        if same_file and not ok:
            out["unchanged"] = True
            out["note"] = "项目已绑定在该文件上且无待写变更，文件保持原样"
        return sanitize(out)

    return run()


def save_project(path=""):
    return call(_save_project, path)


def open_project(path):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise OriginError("file_not_found", "工程文件不存在：%s" % path)

    def run():
        global _project_file
        op = origin()
        if not op.open(path):
            raise OriginError("open_failed", "Origin 拒绝打开这个工程：%s" % path,
                              ["确认文件是 .opju/.opj 且没被另一个 Origin 实例锁住",
                               "Origin 版本低于该工程保存版本时也打不开"])
        _project_file = path
        return sanitize({"opened": path, "open_pages": [p.name for p in op.pages()]})

    return call(run)


def inspect(graph=""):
    def run():
        op = origin()
        pages = [{"name": p.name, "kind": type(p).__name__,
                  "lname": _safe(lambda p=p: p.lname, "")} for p in op.pages()]
        out = {"pages": pages, "handles": sorted(_handles)}
        page = _graph_of(op, graph)
        if page is not None:
            layers = []
            i = 0
            while i < 8:
                lay = _safe(lambda i=i: page[i])
                if lay is None:
                    break
                layers.append({"layer": i,
                               "x_title": _safe(lambda l=lay: l.axis("x").title, ""),
                               "y_title": _safe(lambda l=lay: l.axis("y").title, ""),
                               "plots": _safe(lambda l=lay: len(l.plots()), 0)})
                i += 1
            out.update({"graph": _safe(lambda: page.name, ""), "layers": layers})
        return sanitize(out)

    return call(run)


def exit_origin(save_to=""):
    def run():
        global _op, _project_file
        op = origin()
        saved = _save_project(save_to)["path"] if save_to else ""
        _safe(op.exit)
        _handles.clear()
        _names.clear()
        _project_file = ""
        _op = None
        return sanitize({"exited": True, "saved": saved})

    return call(run)


def figure(source="", columns=None, x=1, y=1, plot_type="line", title="",
           x_title="", y_title="", fmt="png", width=1600, output_dir="",
           export_path="", project_path="", do_project=None,
           template="", legend=False):
    """One call: data in -> graph -> styled -> image + editable .opju on disk.

    Files only land on disk when the caller gives a destination (output_dir /
    export_path / project_path); otherwise Origin keeps the graph in its session
    and we say so instead of writing next to the server's CWD.
    """
    t0 = time.time()
    steps = []
    if source:
        info = import_file(source)
        steps.append({"step": "import", "rows": info["rows"], "source": info["source"],
                      "channel": info.get("channel", ""), "warnings": info.get("warnings", [])})
    elif columns:
        info = write_columns(columns)
        steps.append({"step": "write", "rows": info["rows"]})
    else:
        raise OriginError("bad_arguments", "figure 需要 source（文件路径）或 columns（内联数据）")

    p = plot(info["worksheet"], x=x, y=y, plot_type=plot_type, title=title, template=template)
    steps.append({"step": "plot", "graph": p["graph_page"], "type": plot_type,
                  "channel": p.get("channel", ""), "plots": p.get("plots_in_layer")})
    if x_title or y_title:
        st = style(p["graph"], x_title=x_title, y_title=y_title)
        steps.append({"step": "style", **st})
    else:
        st = {}
    lg = {}
    if legend:
        lg = legend_entries(p["graph"], entries=legend if isinstance(legend, list) else None)
        steps.append({"step": "legend", "proof_level": lg.get("proof_level")})

    out = {"data": info, "plot": p, "style": st, "legend": lg}
    deliver = {}
    if export_path or output_dir:
        base = export_path or os.path.join(output_dir, "%s.%s" % (p["graph_page"], fmt))
        e = export(p["graph"], base, fmt=fmt, width=width)
        deliver["image"] = e["path"]
        steps.append({"step": "export", "file": e["path"], "bytes": e["bytes"],
                      "dimensions": e.get("dimensions")})
        out["export"] = e
    if do_project or project_path or output_dir:
        proj = project_path or (os.path.join(
            output_dir, "%s_%s.opju" % (p["graph_page"], time.strftime("%H%M%S")))
            if output_dir else "")
        s = save_project(proj)
        deliver["project"] = s["path"]
        steps.append({"step": "save_project", "file": s["path"], "bytes": s["bytes"]})
        out["project"] = s
    elif not deliver:
        out["note"] = ("没给 output_dir/export_path/project_path，只建了图和曲线、没有落盘文件；"
                       "要交付物请至少给一个 output_dir")
    out["delivered"] = deliver
    out["proof_level"] = "verified" if deliver.get("image") else "readback_only"
    out["steps"] = steps
    out["duration_ms"] = int((time.time() - t0) * 1000)
    return out


# ------------------------------------------------------- matrix / 3D / heat maps

MATRIX_TEMPLATES = ("heat_map", "cmap", "mesh", "contline", "contgray", "3d",
                    "glwireface", "glwirefrm", "bar3d")


def matrix_plot(rows, template="heat_map", book_name="", graph_name="",
                xy_range=None, title="", export_path="", project_path=""):
    """Grid data in -> Origin matrix sheet -> a template-driven matrix graph.

    type='?' on add_plot is what lets the .otp template choose the plot kind: with
    an explicit type Origin drew contour lines on top of the Heat_Map template.
    """
    tpl = str(template or "heat_map").lower()
    if tpl not in MATRIX_TEMPLATES:
        raise OriginError("bad_template", "矩阵图模板不认识：%r" % template,
                          ["可用：%s" % ", ".join(sorted(MATRIX_TEMPLATES))])
    if not isinstance(rows, (list, tuple)) or not rows:
        raise OriginError("bad_arguments", "rows 必须是二维数值列表（矩阵网格）")
    widths = {len(r) for r in rows if isinstance(r, (list, tuple))}
    if len(widths) != 1 or not widths.pop():
        raise OriginError("bad_arguments", "矩阵每行宽度必须一致且非空")

    def run():
        import numpy as np
        op = origin()
        arr = np.array([[float(v) if v is not None else np.nan for v in r] for r in rows],
                       dtype=float)
        mb = op.new_book("m", book_name or "")
        ms = mb[0]
        ms.from_np(arr, mv=np.nan)
        if xy_range:
            xy = [float(v) for v in xy_range]
            if len(xy) != 4:
                raise OriginError("bad_arguments", "xy_range 要 4 个数：[x1,x2,y1,y2]")
            ms.xymap = xy
        mapped = _safe(lambda: list(ms.xymap), [])
        gr = op.new_graph(graph_name or "", template=tpl)
        if gr is None:
            raise OriginError("bad_template", "Origin 拒绝用模板 %r 建图页" % tpl)
        lay = gr[0]
        lay.add_plot(ms, colz=0, type="?")
        _safe(lay.rescale)
        if title:
            _safe(lambda: setattr(lay, "title", title))
        drawn = len(_safe(lambda: lay.plot_list(), []) or [])
        if drawn < 1:
            raise OriginError("plot_failed", "矩阵图里一条曲线都没有（模板 %s）" % tpl,
                              ["origin_inspect 看页面", "换 heat_map / cmap 模板试试"])
        out = {"matrix_book": _safe(lambda: mb.name, ""), "shape": list(arr.shape),
               "z_min": float(np.nanmin(arr)), "z_max": float(np.nanmax(arr)),
               "xymap": mapped, "template": tpl,
               "graph": _bind("gr", gr, gr.name), "graph_page": gr.name,
               "plots_in_layer": drawn,
               "xlim": _safe(lambda: list(lay.xlim)), "ylim": _safe(lambda: list(lay.ylim)),
               "zlim": _safe(lambda: list(lay.zlim)),
               "proof_level": "verified"}
        if export_path:
            out["export"] = _export_on_com_thread(gr, export_path)
        if project_path:
            out["project"] = _save_project(project_path)
        return sanitize(out)

    return call(run)


def _export_on_com_thread(page, path):
    """export() body, for reuse inside another run() that is already on the thread."""
    ext = os.path.splitext(path)[1].lower() or ".png"
    target = os.path.abspath(path)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    _safe(lambda: page.save_fig(target))
    if not os.path.isfile(target):
        raise OriginError("export_failed", "矩阵图导出没落盘：%s" % target)
    info = _verify_file(target, must_be_image=ext in IMAGE_MAGIC)
    if not info["ok"]:
        raise OriginError("export_suspect", "导出文件异常：%s" % info)
    return info


# ------------------------------------------------------------- page garbage

PAGE_KINDS = {"graph": ("GPage", "g"), "worksheet": ("WBook", "w"), "workbook": ("WBook", "w"),
              "matrix": ("MBook", "m"), "notes": ("Notes", "n")}


def close_pages(kind="graph", names=None, dry_run=False):
    """Close pages so a long dsh session does not choke Origin.

    Accumulated pages are what makes Origin's COM bridge start answering
    "无效指针"; this gives the model a way to reclaim them by name or by type.

    The proof is a fresh enumeration in its own COM call: is_open() only reports
    "not hidden/minimised", so it can never show that a page is gone.
    """
    from collections import Counter
    want = PAGE_KINDS.get(str(kind).lower())
    if kind and not want:
        raise OriginError("bad_kind", "kind 不认识：%r" % kind,
                          ["可用：%s" % ", ".join(sorted(PAGE_KINDS))])
    want_cls, want_letter = want or ("", "")
    keep = None if not names else [str(n) for n in names]

    def collect():
        op = origin()
        # op.pages() is a GENERATOR: iterating it twice silently yields nothing
        # the second time, which is how this first reported "no matching pages".
        pages_now = list(_safe(lambda: op.pages(want_letter), []) or [])
        counts = Counter(_safe(lambda p=p: p.name, "?") for p in pages_now)
        targets = []
        for p in pages_now:
            if want_cls and type(p).__name__ != want_cls:
                continue
            if keep and _safe(lambda p=p: p.name, "") not in keep and \
                    _safe(lambda p=p: p.lname, "") not in keep:
                continue
            targets.append(p)
        return targets, counts

    def destroy(targets):
        errors = {}
        for p in targets:
            try:
                p.destroy()
            except Exception as exc:
                errors[_safe(lambda p=p: p.name, "?")] = str(exc)[:160]
        return errors

    def enumerate_pages():
        op = origin()
        return [_safe(lambda p=p: p.name, "?")
                for p in list(_safe(lambda: op.pages(), []) or [])]

    targets, had = call(collect)
    wanted = [_safe(lambda p=p: p.name, "?") for p in targets]
    if not targets:
        return sanitize({"closed": [], "asked": [], "note": "没有匹配的页面"})
    if dry_run:
        return sanitize({"would_close": wanted, "dry_run": True})
    errors = call(destroy, targets)
    alive = call(enumerate_pages)
    for _ in range(10):
        now = Counter(alive)
        if all(now.get(n, 0) < had.get(n, 0) for n in wanted):
            break
        time.sleep(0.2)
        alive = call(enumerate_pages)
    now = Counter(alive)
    closed, expected = [], dict(had)
    for nm in wanted:
        if now.get(nm, 0) < expected.get(nm, 0):
            closed.append(nm)
            expected[nm] = now.get(nm, 0)
    for ref in [r for r, nm in _names.items() if nm and nm in closed]:
        _handles.pop(ref, None)
        _names.pop(ref, None)
    return sanitize({"asked": wanted, "closed": closed,
                     "refused": [n for n in wanted if n not in closed],
                     "errors": errors,
                     "remaining_pages": alive,
                     "proof_level": "verified" if len(closed) == len(wanted) else
                                    ("partial" if closed else "unverified"),
                     "note": "以关闭后重新枚举的页面名单为证据"})


# ------------------------------------------------- scientific chart presets

def _catalog():
    try:
        import templates_catalog
    except Exception as exc:
        raise OriginError("catalog_missing", "图表预设目录加载失败：%s" % exc)
    return templates_catalog


def chart_presets(category=""):
    cat = _catalog()
    items = cat.list_templates(category or None)
    return sanitize({"n_templates": len(items),
                     "categories": sorted({t["category"] for t in cat.TEMPLATES}),
                     "templates": items})


def chart_preset(name):
    cat = _catalog()
    tpl = cat.describe_template(name)
    if tpl is None:
        raise OriginError("preset_not_found", "没有这个图表预设：%r" % name,
                          ["origin_chart_presets 看全部 %d 个" % len(cat.TEMPLATES)])
    return sanitize(tpl)


def chart_check(name, data=None, columns=None):
    """Dry-run a preset against real data without touching Origin."""
    cat = _catalog()
    tpl = cat.describe_template(name)
    if tpl is None:
        raise OriginError("preset_not_found", "没有这个图表预设：%r" % name)
    if data and not columns:
        columns = [c["name"] for c in read_file(data)["columns"]]
    detected = list(columns or [])
    issues = []
    for req in tpl["required_columns"] or ():
        norm = str(req).lower().replace("_", "").replace(" ", "")
        if not any(norm in str(c).lower().replace("_", "").replace(" ", "") for c in detected):
            issues.append("缺少列：%s" % req)
    return sanitize({"status": "ok" if not issues else "warning", "template": name,
                     "required_columns": tpl["required_columns"],
                     "optional_columns": tpl.get("optional_columns") or [],
                     "columns_detected": detected, "issues": issues,
                     "suggested_fit": tpl.get("suggested_fit"),
                     "default_axis_titles": tpl.get("default_axis_titles"),
                     "default_plot_kind": tpl.get("default_plot_kind"),
                     "extra": tpl.get("extra", "")})


def chart_render(name, source="", columns=None, x=1, y=2, output_dir="",
                 x_title=None, y_title=None, title=None, plot_type=None,
                 with_legend=True, fmt="png", width=1600):
    """Render a scientific preset with the verified plot/style/legend path.

    Only the preset's metadata is taken from the catalog: axis titles, plot kind
    and the suggested fit. Its own render_template() calls op.new(), which would
    throw away whatever project the user has open in Origin.
    """
    checked = chart_check(name, data=source) if not columns else chart_check(name, columns=columns)
    preset = chart_preset(name)
    kind = str(plot_type or preset.get("default_plot_kind") or "line").lower()
    if kind not in PLOT_CODES:
        kind = "line"
    titles = preset.get("default_axis_titles") or {}
    xt = x_title if x_title is not None else titles.get("x", "")
    yt = y_title if y_title is not None else titles.get("y", "")
    out = figure(source=source, columns=columns, x=x, y=y, plot_type=kind,
                 title=title if title is not None else preset.get("description", "")[:60],
                 x_title=xt, y_title=yt, fmt=fmt, width=width,
                 output_dir=output_dir, legend=with_legend)
    out["preset"] = {"name": name, "category": preset.get("category", ""),
                     "plot_type": kind, "x_title": xt, "y_title": yt}
    out["check"] = checked
    if preset.get("suggested_fit"):
        out["hint"] = "这个预设建议拟合函数：%s（origin_fit kind=%s）" % (
            preset["suggested_fit"], preset["suggested_fit"])
    if preset.get("extra"):
        out["preset_note"] = preset["extra"]
    return out
