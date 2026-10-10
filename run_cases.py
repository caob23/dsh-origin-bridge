"""Run the case bank against the real stdio server and report.

    python run_cases.py --list            # 只打印题库，不跑
    python run_cases.py --levels L0,L1    # 只跑只读层（安全、快）
    python run_cases.py                   # 全跑
    python run_cases.py --keep-data       # 保留生成的数据文件（默认跑完删除）

The bank itself is validated first: every case's argument names are checked
against the live tools/list schema, so a typo in a case cannot masquerade as a
product bug.
"""

import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "cases"))

import catalogue  # noqa: E402
import data as case_data  # noqa: E402
import server as bridge_server  # noqa: E402
import smoke_test as sm  # noqa: E402

DATA_DIR = os.path.join(HERE, "cases", "data_out")
HOUSEKEEPING_EVERY = 25


PLACEHOLDER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
WHOLE = re.compile(r"^\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")


def resolve(value, ctx):
    """Substitute {name} placeholders from ctx anywhere in a case's args.

    A value that is exactly one placeholder resolves to the stored object as-is,
    so {"columns": "{columns}"} passes a dict, not a JSON string. Unknown names
    are rejected before substitution: doing it afterwards would trip over the
    braces inside JSON values that were just inserted.
    """
    if isinstance(value, str):
        whole = WHOLE.match(value)
        if whole:
            if whole.group(1) not in ctx:
                raise KeyError("题库里引用了未知的占位符 %s" % value)
            return ctx[whole.group(1)]
        for name in PLACEHOLDER.findall(value):
            if name not in ctx:
                raise KeyError("题库里引用了未知的占位符 {%s}" % name)
        return PLACEHOLDER.sub(lambda m: str(ctx[m.group(1)]), value)
    if isinstance(value, list):
        return [resolve(v, ctx) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, ctx) for k, v in value.items()}
    return value


def schema_check(cases):
    """Report cases whose argument names are not in the live tool schema."""
    props = {t["name"]: set(((t.get("inputSchema") or {}).get("properties") or {}).keys())
             for t in bridge_server.TOOLS}
    bad = []
    for idx, c in enumerate(cases):
        if c["tool"] not in props:
            bad.append((idx, c["tool"], "工具不存在"))
            continue
        for key in c["args"]:
            if key not in props[c["tool"]]:
                bad.append((idx, c["tool"], "参数 %r 不在 schema 里" % key))
    return bad


def bootstrap(rpc, ctx):
    """Create the handles the bank assumes; not counted as cases.

    Closing pages invalidates every stored handle, so this is also the
    housekeeping step: it re-establishes all of them, never just some.
    """
    imp = rpc.tool("origin_import", {"path": ctx["dat"]}, timeout=180)
    ctx["ws"] = str(imp["worksheet"])
    wide = rpc.tool("origin_import", {"path": ctx["wide"]}, timeout=180)
    ctx["wsw"] = str(wide["worksheet"])
    inl = rpc.tool("origin_write", {"columns": ctx["columns"]}, timeout=180)
    ctx["wsi"] = str(inl["worksheet"])
    fit = rpc.tool("origin_import", {"path": ctx["fit"]}, timeout=180)
    ctx["wsf"] = str(fit["worksheet"])
    p = rpc.tool("origin_plot", {"worksheet": ctx["ws"], "x": 1, "y": 2}, timeout=180)
    ctx["gr"] = str(p["graph"])
    two = rpc.tool("origin_plot", {"worksheet": ctx["wsw"], "x": 1, "y": [2, 3]}, timeout=180)
    ctx["gr2"] = str(two["graph"])
    # Cases that address layer 1 of {gr} must not depend on whichever layer-adding
    # case happened to run earlier, so every batch starts with a two-layer page.
    rpc.tool("origin_layer", {"graph": ctx["gr"], "layer_type": "right"}, timeout=180)


def rehousekeep(rpc, ctx):
    """Close accumulated pages, then rebuild every handle the bank points at."""
    rpc.tool("origin_close_pages", {"kind": "graph"}, timeout=180)
    rpc.tool("origin_close_pages", {"kind": "workbook"}, timeout=180)
    rpc.tool("origin_close_pages", {"kind": "matrix"}, timeout=180)
    bootstrap(rpc, ctx)
    print("  -- 清理页面并重建句柄: ws=%s wsw=%s wsi=%s wsf=%s gr=%s gr2=%s"
          % (ctx["ws"], ctx["wsw"], ctx["wsi"], ctx["wsf"], ctx["gr"], ctx["gr2"]))


def where_died(rpc, resp):
    """Dump the live handle table when a case blames a handle, to tell
    'evicted from the table' apart from 'object still bound but page destroyed'."""
    code = resp.get("code")
    if code not in ("graph_not_found", "worksheet_not_found", "sheet_empty"):
        return
    try:
        ins = rpc.tool("origin_inspect", {}, timeout=180)
        print("        现场 handles=%s pages=%s"
              % (ins.get("handles"), [p.get("name") for p in (ins.get("pages") or [])]))
    except Exception as exc:
        print("        现场取不到：%s" % exc)


def dig(resp, path):
    """Look up "columns.0.name" so a case can assert on nested values."""
    cur = resp
    for part in str(path).split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def run(cases, rpc, ctx, verbose):
    import originlab
    results, failures = [], []
    since_housekeeping = 0
    for idx, c in enumerate(cases):
        # Housekeeping has to happen BEFORE the placeholders are resolved: closing
        # pages evicts the handles, and a case that resolved "gr-43" first would
        # then blame the product for a handle this script destroyed.
        if c["level"] in ("L2", "L3"):
            since_housekeeping += 1
        if since_housekeeping >= HOUSEKEEPING_EVERY:
            rehousekeep(rpc, ctx)
            since_housekeeping = 0
        try:
            args = resolve(c["args"], ctx)
        except KeyError as exc:
            failures.append((idx, c, str(exc)))
            continue
        try:
            resp = rpc.tool(c["tool"], args, timeout=240)
        except Exception as exc:  # transport died: the whole run is suspect
            failures.append((idx, c, "调用异常 %s: %s" % (type(exc).__name__, exc)))
            break
        if c["tool"] == "origin_close_pages" and resp.get("closed"):
            # Destroying a page invalidates every handle the bank points at, and
            # the next case would blame the product for a stale handle.
            rehousekeep(rpc, ctx)
        if resp.get("code") in ("origin_unavailable", "connection_error"):
            # The bridge dropped and rebuilt its connection; every handle this bank
            # holds died with it, so rebuild before the next case or the whole tail
            # fails for one wedge.
            print("  -- #%s 掉线/坏状态(%s)，重建句柄表后继续" % (idx, resp.get("code")))
            rehousekeep(rpc, ctx)
        problems = []
        got_ok = resp.get("ok") is True
        if c["ok"] and not got_ok:
            problems.append("预期成功，实际 ok=%r code=%r msg=%r"
                            % (resp.get("ok"), resp.get("code"), str(resp.get("message"))[:120]))
        if not c["ok"] and got_ok:
            problems.append("预期失败，实际却成功了（静默失败的反面：静默成功）")
        if c["code"] and resp.get("code") != c["code"]:
            problems.append("错误码应为 %s，实际 %s" % (c["code"], resp.get("code")))
        for key in c["has"]:
            if key not in resp or resp[key] is None:
                problems.append("返回里缺字段 %r" % key)
        for key, want in c["eq"]:
            got = dig(resp, key)
            if got != want:
                problems.append("字段 %r 应为 %r，实际 %r" % (key, want, got))
        for key, floor in c.get("min", []):
            got = resp.get(key)
            if not isinstance(got, (list, dict, str)) or len(got) < floor:
                problems.append("字段 %r 至少要有 %d 项，实际 %r" % (key, floor, got))
        for frag in c.get("msg", []):
            if frag not in str(resp.get("message", "")):
                problems.append("错误信息里应当出现 %r，实际 %r"
                                % (frag, str(resp.get("message"))[:120]))
        if not problems:
            for name, key in c["save"].items():
                if resp.get(key):
                    ctx[name] = str(resp[key])
            results.append((idx, c, "pass"))
            if verbose:
                print("  ok   #%-3d %-20s %s" % (idx, c["tool"], c["prompt"][:44]))
        else:
            results.append((idx, c, "fail"))
            failures.append((idx, c, "; ".join(problems)))
            print("  FAIL #%-3d %-20s %s\n        %s"
                  % (idx, c["tool"], c["prompt"][:44], "; ".join(problems)))
            where_died(rpc, resp)
    return results, failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="L0,L1,L2,L3,L4")
    ap.add_argument("--tool", default="")
    ap.add_argument("--list", action="store_true", help="只打印题库")
    ap.add_argument("--keep-data", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    opts = ap.parse_args()

    levels = {l.strip() for l in opts.levels.split(",") if l.strip()}
    cases = [c for c in catalogue.CASES
             if c["level"] in levels and (not opts.tool or c["tool"] == opts.tool)]

    if opts.list:
        by_tool = {}
        for c in cases:
            by_tool.setdefault((c["level"], c["tool"]), []).append(c)
        for (level, tool), items in sorted(by_tool.items()):
            print("\n%s  %-22s %d 例" % (level, tool, len(items)))
            for c in items:
                print("    - %s" % c["prompt"])
        print("\n题库共 %d 例，覆盖 %d 个工具" % (len(cases), len({c["tool"] for c in cases})))
        return 0

    bad = schema_check(cases)
    if bad:
        print("题库与 schema 不符，先修题库：")
        for idx, tool, why in bad[:20]:
            print("  #%-3d %-20s %s" % (idx, tool, why))
        return 2

    shutil.rmtree(DATA_DIR, ignore_errors=True)
    built = case_data.build(DATA_DIR)
    ctx = dict(built)
    ctx["dir"] = DATA_DIR.replace("\\", "/")

    rpc = sm.Rpc(sm.PY, sm.SERVER)
    rpc.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "casebank", "version": "1"}})
    rpc.notify("notifications/initialized")
    bootstrap(rpc, ctx)

    try:
        results, failures = run(cases, rpc, ctx, opts.verbose)
    finally:
        try:
            rpc.p.kill()
        except Exception:
            pass

    per_tool = {}
    per_level = {}
    for idx, c, state in results:
        per_tool.setdefault(c["tool"], [0, 0])[0] += state == "pass"
        per_tool[c["tool"]][1] += state == "fail"
        per_level.setdefault(c["level"], [0, 0])[0] += state == "pass"
        per_level[c["level"]][1] += state == "fail"

    print("\n=== 题库执行汇总 ===")
    for level in sorted(per_level):
        ok, bad_n = per_level[level]
        print("  %s  %d 通过 / %d 失败" % (level, ok, bad_n))
    print("\n  每个工具的用例数（要求 ≥10）：")
    missing = []
    for name in sorted(per_tool):
        ok, bad_n = per_tool[name]
        flag = "" if (ok + bad_n) >= 10 else "   <-- 不足 10"
        print("    %-22s %3d 通过 %2d 失败%s" % (name, ok, bad_n, flag))
        if (ok + bad_n) < 10:
            missing.append(name)
    tools_in_bank = {c["tool"] for c in cases}
    not_covered = sorted({t["name"] for t in bridge_server.TOOLS} - tools_in_bank)
    total = len(results)
    print("\n  测试了 %d 次：%d 通过 / %d 失败" % (total, total - len(failures), len(failures)))
    if not_covered:
        print("  未覆盖的工具：", ", ".join(not_covered))
    if missing:
        print("  用例数不足 10 的工具：", ", ".join(missing))

    if not opts.keep_data:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        print("  生成的数据文件已删除（题库保留）")
    try:
        import originlab
        originlab.reclaim()
    except Exception:
        pass
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
