"""Render one PNG per real workstation file from cases/realdata/shiyan.

The data is the user's own lab export and is gitignored on purpose: this script
is the "真题" harness, the files themselves never ship with the plugin.

    python render_realdata.py            # 全部文件，一个一张图
    python render_realdata.py --only cv  # 只跑名字里含 cv 的
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import smoke_test as sm  # noqa: E402

DATA = os.path.join(HERE, "cases", "realdata", "shiyan")
OUT = os.path.join(HERE, "out", "realdata")
PY = os.path.abspath(os.path.join(HERE, ".venv", "Scripts", "python.exe"))
SAFE = re.compile(r"[^\w.-]+")


def safe(name):
    stem, ext = os.path.splitext(name)
    return "%s__%s" % (SAFE.sub("_", stem) or "file", (ext.lstrip(".") or "dat").lower())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--keep-pages", action="store_true",
                    help="不清理 Origin 页面（默认每 3 个文件清一次，避免攒页面卡死）")
    opts = ap.parse_args()

    if not os.path.isdir(DATA):
        print("没有 %s：把工作站导出文件放到这里再跑" % DATA)
        return 2
    os.makedirs(OUT, exist_ok=True)
    files = [os.path.join(DATA, f) for f in sorted(os.listdir(DATA))
             if os.path.splitext(f)[1].lower() in (".txt", ".dat", ".csv", ".tsv", ".bin")]
    if opts.only:
        files = [p for p in files if opts.only.lower() in os.path.basename(p).lower()]

    rpc = sm.Rpc(PY, sm.SERVER)
    rpc.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "realdata", "version": "1"}})
    rpc.notify("notifications/initialized")

    rows = []
    for n, path in enumerate(files, 1):
        name = os.path.basename(path)
        head = rpc.tool("origin_read_file", {"path": path}, timeout=300)
        if not head.get("ok") and os.path.splitext(name)[1].lower() != ".bin":
            rows.append((name, "读取失败", head.get("code"), ""))
            print("%-26s 读取失败 %s" % (name, head.get("code")))
            continue
        target = os.path.join(OUT, safe(name) + ".png").replace("\\", "/")
        out_dir = os.path.dirname(target)
        fig = rpc.tool("origin_figure", {"source": path, "x": 1, "y": 2,
                                         "title": os.path.splitext(name)[0],
                                         "export_path": target,
                                         "project_path": os.path.join(
                                             OUT, safe(name) + ".opju").replace("\\", "/")},
                       timeout=900)
        if not fig.get("ok") and fig.get("code") in ("bad_column", "sheet_empty"):
            fig = rpc.tool("origin_figure", {"source": path, "x": 1, "y": 1,
                                             "title": os.path.splitext(name)[0],
                                             "export_path": target}, timeout=900)
        if fig.get("ok"):
            img = (fig.get("delivered") or {}).get("image", "")
            size = os.path.getsize(img) if img and os.path.isfile(img) else 0
            rows.append((name, "出图", "%d 行 / %d 字节" % (head.get("rows") or 0, size), img))
            print("%-26s 出图 %s 行 -> %s" % (name, head.get("rows"), os.path.basename(img)))
        else:
            rows.append((name, "失败", fig.get("code"), str(fig.get("message"))[:60]))
            print("%-26s 失败 %s %s" % (name, fig.get("code"), str(fig.get("message"))[:60]))
        if not opts.keep_pages and n % 3 == 0:
            rpc.tool("origin_close_pages", {"kind": "graph"}, timeout=300)
            rpc.tool("origin_close_pages", {"kind": "workbook"}, timeout=300)

    try:
        rpc.p.kill()
    except Exception:
        pass
    print("\n共 %d 个文件，出图 %d 张，失败 %d 个，图片在 %s"
          % (len(files), sum(1 for r in rows if r[1] == "出图"),
             sum(1 for r in rows if r[1] != "出图"), OUT))
    try:
        import originlab
        originlab.reclaim()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
