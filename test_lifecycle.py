"""Checks for the Origin instance-lifecycle layer: classification and reclaim.

These run without Origin: the process table and taskkill are stubbed, so the
safety property that matters -- a windowed instance is never killed -- is
asserted against a synthetic set of instances.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import originlab  # noqa: E402

fails = []


def check(label, cond, got=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s  -> %r" % (label, got))
        fails.append(label)


class Fake(object):
    """Stands in for the process table and taskkill."""

    def __init__(self, rows):
        self.rows = rows
        self.killed = []
        self.forced = []

    def process_rows(self):
        return [dict(r) for r in self.rows]

    def terminate(self, pid, force=False):
        (self.forced if force else self.killed).append(pid)
        if force:
            self.rows = [r for r in self.rows if r["pid"] != pid]
        return None

    def sleep(self, _seconds):
        return None


def with_fake(rows, fn):
    fake = Fake(rows)
    saved = (originlab._process_rows, originlab._terminate, originlab._close_pid,
             originlab.time.sleep, set(originlab._started_pids))
    originlab._process_rows = fake.process_rows
    originlab._terminate = fake.terminate
    originlab._close_pid = lambda pid: (fake.terminate(pid), fake.terminate(pid, force=True),
                                        "force_closed")[2]
    originlab.time.sleep = fake.sleep
    try:
        return fn(fake)
    finally:
        originlab._process_rows, originlab._terminate, originlab._close_pid = saved[0], saved[1], saved[2]
        originlab.time.sleep = saved[3]
        originlab._started_pids.clear()
        originlab._started_pids.update(saved[4])


BACK = {"pid": 1001, "started": "2026-10-10T12:00:00", "window": 0}
BACK2 = {"pid": 1002, "started": "2026-10-10T12:05:00", "window": 0}
FRONT = {"pid": 2001, "started": "2026-10-10T11:00:00", "window": 131072}

print("instances() 分类")
rows = [dict(BACK), dict(BACK2), dict(FRONT)]


def _classify(_fake):
    originlab._started_pids.add(1002)
    info = originlab.instances()
    check("后台两个", [r["pid"] for r in info["background"]] == [1001, 1002], info["background"])
    check("前台一个", [r["pid"] for r in info["foreground"]] == [2001], info["foreground"])
    check("count 对", info["count"] == 3, info["count"])
    back_by_pid = {r["pid"]: r for r in info["background"]}
    check("标出我们起的", back_by_pid[1002]["ours"] and not back_by_pid[1001]["ours"],
          {p: b["ours"] for p, b in back_by_pid.items()})
    check("前台窗口标记为 foreground", info["foreground"][0]["foreground"] is True)


with_fake(rows, _classify)

print("reclaim() 只动无窗口的")


def _reclaim_closes_back(_fake):
    out = originlab.reclaim()
    check("关掉了两个后台", sorted(_fake.forced) == [1001, 1002], _fake.forced)
    check("没碰前台 2001", 2001 not in _fake.killed and 2001 not in _fake.forced,
          (_fake.killed, _fake.forced))
    check("left_running 里是前台", out["left_running"] == [2001], out["left_running"])
    check("通知文案提到窗口", "有窗口" in out["notify"], out["notify"])


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _reclaim_closes_back)


def _reclaim_dry_run(_fake):
    out = originlab.reclaim(close_background=False)
    check("预览模式没杀任何进程", not _fake.killed and not _fake.forced, (_fake.killed, _fake.forced))
    check("预览模式列出了待关的", sorted(out["skipped"]) == [1001, 1002], out.get("skipped"))


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _reclaim_dry_run)


def _reclaim_all_background(_fake):
    out = originlab.reclaim()
    check("全是后台时通知说没有前台", "没有发现前台窗口" in out["notify"], out["notify"])
    check("left_running 为空", out["left_running"] == [], out["left_running"])


with_fake([dict(BACK)], _reclaim_all_background)

print("_release_on_exit() 只回收自己起的")


def _atexit_scope(_fake):
    originlab._started_pids.add(1002)
    killed = []
    originlab._close_pid = lambda pid: killed.append(pid) or "closed"
    originlab._release_on_exit()
    check("只关了 1002", killed == [1002], killed)
    check("1001 是别人的孤儿，没在退出时动它", 1001 not in killed, killed)


with_fake([dict(BACK), dict(BACK2), dict(FRONT)], _atexit_scope)


def _atexit_noop(_fake):
    originlab._started_pids.clear()
    killed = []
    originlab._close_pid = lambda pid: killed.append(pid) or "closed"
    originlab._release_on_exit()
    check("自己没起实例时什么都不做", killed == [], killed)


with_fake([dict(BACK), dict(FRONT)], _atexit_noop)

print("探测失败时不能谎报干净")


def _probe_unavailable(_fake):
    originlab._process_rows = lambda: None          # powershell 不可用 / 超时
    killed = []
    originlab._close_pid = lambda pid: killed.append(pid) or "closed"

    info = originlab.instances()
    check("instances 报告探测失败", info.get("probe") == "unavailable", info)
    check("instances 不假装数量为 0", info.get("count") is None, info.get("count"))
    check("instances 给了下一步建议", bool(info.get("next_actions")), info.get("next_actions"))

    out = originlab.reclaim()
    check("reclaim 什么都没杀", not killed, killed)
    check("reclaim 标出探测失败", out.get("probe") == "unavailable", out)
    check("reclaim 不说『已回收全部』", "没关" in out.get("notify", "")
          or "探测失败" in out.get("notify", ""), out.get("notify"))

    originlab._started_pids.add(1002)
    originlab._release_on_exit()
    check("退出钩子在探测失败时不动手", killed == [], killed)


with_fake([dict(BACK), dict(FRONT)], _probe_unavailable)

print("掉线重连（call 层）")
_real_reset = originlab._reset_connection

check("SystemError 算掉线", originlab._dead_app(
    SystemError("<built-in function ApplicationBase_Save> returned a result with an exception set")))
check("originpro 报的连接失败算掉线", originlab._dead_app(
    originlab.OriginError("connection_error", "连不上 Origin 自动化服务器："
                          "<built-in function ApplicationBase_Path> returned a result"
                          " with an exception set")))
check("参数错误不算掉线", not originlab._dead_app(
    originlab.OriginError("bad_column", "列号从 1 开始，收到 0")))
check("被包成 internal_error 的 SystemError 也算掉线", originlab._dead_app(
    originlab.OriginError("internal_error",
                          "SystemError: <built-in function ApplicationBase_Save> returned a "
                          "result with an exception set")))
check("普通 ValueError 不算掉线", not originlab._dead_app(ValueError("识别不出分隔符")))

tries = []


def _dies_once(*a, **k):
    tries.append(1)
    if len(tries) == 1:
        raise SystemError("<built-in function ApplicationBase_Save> returned a result "
                          "with an exception set")
    return "recovered"


resets = []
originlab._reset_connection = lambda: resets.append(1) or True
try:
    check("掉线后自动重连并完成任务", originlab.call(_dies_once) == "recovered", tries)
    check("只重试一次", len(tries) == 2 and len(resets) == 1, (tries, resets))

    tries[:] = []
    resety = []
    originlab._reset_connection = lambda: resety.append(1) or False
    try:
        originlab.call(_dies_once)
        check("重连不可用时不无限重试", False, "本该抛错")
    except SystemError as exc:
        check("重连不可用时把原始错误交出去", "ApplicationBase_Save" in str(exc), str(exc))
finally:
    originlab._reset_connection = _real_reset

print("server 工具注册")
import server  # noqa: E402

names = [t["name"] for t in server.TOOLS]
check("origin_instances 已注册", "origin_instances" in names, names[-3:])
check("origin_reclaim 已注册", "origin_reclaim" in names, names[-3:])
check("工具总数 27", len(names) == 27, len(names))
check("工具名唯一", len(set(names)) == len(names))
for tool in server.TOOLS:
    if tool["name"] in ("origin_instances", "origin_reclaim"):
        check("%s 有描述和 schema" % tool["name"],
              bool(tool.get("description")) and isinstance(tool.get("inputSchema"), dict))

print()
if fails:
    print("%d 项失败: %s" % (len(fails), ", ".join(fails)))
    raise SystemExit(1)
print("all lifecycle checks passed")
