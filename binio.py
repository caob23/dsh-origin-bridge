"""Reader for electrochemical-workstation binary exports (.bin).

The files this targets start with 80 F2 1B 00, carry a technique tag (CV / LSV /
IMP / i-t) plus its long name and a build string ("r0815"), and end with one
flat array of little-endian float32. The total element count is stored twice as
adjacent u32 fields inside the header, which is what makes the header length
derivable instead of guessed.

What is NOT claimed here: which of the two arrays is potential and which is
current. Nothing in the header identifies them, and on the sample set both
arrays often sit in the same numeric band, so guessing would silently mislabel
axes. The reader returns the arrays in file order plus the technique name and
lets the caller (or origin_view) decide.
"""

import os
import struct

MAGIC = b"\x80\xf2\x1b\x00"
MAX_HEADER = 2400


class BinFormatError(ValueError):
    pass


def _u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0]


def _technique(buf):
    """The short tag is not NUL padded: 'CV' is 2 bytes, 'LSV'/'IMP'/'i-t' 3, and
    the u32 name length starts immediately after it, so locate that boundary."""
    for p in range(8, 15):
        if p + 4 > len(buf):
            break
        n = _u32(buf, p)
        if not (4 <= n <= 40) or p + 4 + n > len(buf):
            continue
        raw = buf[p + 4:p + 4 + n]
        if all(32 <= c < 127 for c in raw):
            tag = buf[8:p].decode("latin-1").strip().rstrip("\x00")
            build = ""
            at = buf.find(b"r0", p + 4 + n, p + 4 + n + 200)
            if at >= 0:
                end = buf.find(b"\x00", at, at + 16)
                if end > at:
                    build = buf[at:end].decode("latin-1")
            return tag, raw.decode("latin-1"), build
    return buf[8:12].split(b"\x00")[0].decode("latin-1").strip(), "", ""


def _find_stream(buf):
    """Return (total_floats, data_offset) from the duplicated u32 length field."""
    size = len(buf)
    for off in range(16, min(1400, size - 12)):
        n = _u32(buf, off)
        if n != _u32(buf, off + 8) or not (2 <= n <= (size - off) // 4):
            continue
        start = size - 4 * n
        if start <= off + 12 or start > MAX_HEADER:
            continue
        vals = struct.unpack_from("<%df" % n, buf, start)
        if any(v != v or abs(v) > 1e15 for v in vals):
            continue
        return n, start
    raise BinFormatError(
        "找不到成对出现的 float32 计数域（文件可能是别的 .bin 格式，或已被截断）")


PARAM_BLOCK_BACK = 600          # the method block sits 600 bytes before the data
P = {"init": 0, "high": 8, "low": 12, "segments": 28, "interval": 32,
     "rate": 40, "quiet": 44}


def _params(buf, header):
    base = header - PARAM_BLOCK_BACK
    if base < 16 or base + 48 > len(buf):
        return {}
    f = struct.Struct("<f")
    out = {}
    for key, off in P.items():
        raw = f.unpack_from(buf, base + off)[0]
        out[key] = raw if raw == raw and abs(raw) < 1e12 else 0.0
    out["segments"] = int(round(out["segments"]))
    return out


def build_axis(params, n, positive_first=True):
    """Triangle wave between low and high, stepping by the sample interval.

    Each leg emits its own step count and the switching potential belongs to the
    NEXT leg, which is what reproduces the vendor's own text export point for
    point (verified against ecsa100.txt: 600 points, reversals every 30).
    """
    init, high, low = params["init"], params["high"], params["low"]
    step = params["interval"]
    if not (high > low > -1e9) or step <= 0:
        return None, 0
    out = []
    e = init
    d = 1 if positive_first else -1
    guard = 0
    while len(out) < n and guard < 100000:
        guard += 1
        limit = high if d > 0 else low
        steps = int(round(abs(limit - e) / step))
        if steps <= 0:
            d = -d
            continue
        for k in range(steps):
            if len(out) >= n:
                break
            out.append(e)
            e = limit if k == steps - 1 else e + d * step
        d = -d
    return out, guard


def expected_points(params, positive_first=True):
    """Points the declared method produces, used to confirm the sweep polarity."""
    init, high, low = params["init"], params["high"], params["low"]
    step = params["interval"]
    if high <= low or step <= 0:
        return 0
    total = 0
    e = init
    d = 1 if positive_first else -1
    for _ in range(max(1, int(params["segments"]))):
        limit = high if d > 0 else low
        steps = int(round(abs(limit - e) / step))
        if steps <= 0:
            d = -d
            limit = high if d > 0 else low
            steps = int(round(abs(limit - e) / step))
        total += steps
        e = limit
        d = -d
    return total


def read_bin(path):
    """Decode a workstation .bin: the measured channel plus a reconstructed axis.

    The file holds ONE float32 array -- the measured channel (current in
    amperes, or impedance for EIS). The potential axis is not stored; it is
    regenerated from the method block in the header. That reconstruction is
    checked against the declared point count, so a wrong guess degrades to "no
    axis" instead of silently plotting against the wrong X.
    """
    with open(path, "rb") as fh:
        buf = fh.read()
    if not buf.startswith(MAGIC):
        raise BinFormatError("文件头不是 %s，这不是电化学工作站的 .bin" % MAGIC.hex())
    tag, name, build = _technique(buf)
    nseg = _u32(buf, 4)
    total, start = _find_stream(buf)
    y = list(struct.unpack_from("<%df" % total, buf, start))
    params = _params(buf, start)
    warnings = []
    axis, axis_name, axis_kind = None, "", "none"

    if tag in ("CV", "LSV") and params:
        fwd = expected_points(params, True) == total
        rev = expected_points(params, False) == total
        if fwd or rev:
            axis, _ = build_axis(params, total, positive_first=fwd)
            axis_name, axis_kind = "E / V", "reconstructed"
        else:
            warnings.append("按头部方法参数应有 %d 个点，文件里是 %d 个，电势轴没重建"
                            % (expected_points(params, True), total))
    elif tag in ("i-t", "CA", "CP") and params.get("interval"):
        axis = [i * params["interval"] for i in range(total)]
        axis_name, axis_kind = "t / s", "uniform_from_interval"
    else:
        warnings.append("%s 的 X 轴不在这个文件里（EIS 的频率表是另一套参数）" % (tag or "?",))

    if not name:
        warnings.append("头部没解析出技术全称，只有短标签 %r" % tag)

    def summary(values, label):
        if not values:
            return None
        return {"name": label, "n": len(values), "first": values[0], "last": values[-1],
                "min": min(values), "max": max(values)}

    return {
        "path": os.path.abspath(path),
        "format": "electrochem-bin",
        "magic": MAGIC.hex(),
        "technique": tag,
        "technique_name": name or tag,
        "build": build,
        "header_segments_field": nseg,
        "header_bytes": start,
        "points": total,
        "method": {"init_E": params.get("init"), "high_E": params.get("high"),
                   "low_E": params.get("low"), "segments": params.get("segments"),
                   "step": params.get("interval"), "scan_rate_V_per_s": params.get("rate"),
                   "quiet_time_s": params.get("quiet")},
        "axis": axis_kind,
        "x": summary(axis, axis_name),
        "y": summary(y, "I / A" if tag != "IMP" else "Z / ohm"),
        "warnings": warnings,
        "series": {"x": axis, "y": y},
        "note": ("测量值只有一列（电流 A）；X 轴由头部方法参数重建，"
                 "axis=reconstructed 表示重建点数与文件点数完全吻合。"),
    }
