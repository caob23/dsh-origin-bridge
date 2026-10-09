"""Checks for binio — the electrochemical-workstation .bin reader.

The synthetic case pins the format down without needing the user's data, and the
real case cross-checks every value against the instrument's own text export,
which is the only ground truth available for a vendor binary format.
"""

import glob
import os
import re
import struct
import sys

import binio
import originlab

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")
REAL = os.environ.get("ORIGIN_BRIDGE_REAL_BIN_DIR") or os.path.expanduser(
    os.path.join("~", "Desktop", "shiyan"))
HEADER = 1445
BASE = HEADER - binio.PARAM_BLOCK_BACK

fails = []


def check(label, cond, got=""):
    if cond:
        print("  ok   %s" % label)
    else:
        print("  FAIL %s  -> %r" % (label, got))
        fails.append(label)


def build(path, tag, name, params, values, segments_field=2):
    buf = bytearray(b"\x00" * HEADER)
    buf[0:4] = binio.MAGIC
    struct.pack_into("<I", buf, 4, segments_field)
    head = tag.encode("latin-1") + struct.pack("<I", len(name)) + name.encode("latin-1")
    buf[8:8 + len(head)] = head
    buf[8 + len(head):8 + len(head) + 6] = b"r0815\x00"
    total = len(values)
    struct.pack_into("<I", buf, 489, total)
    struct.pack_into("<I", buf, 497, total)      # the real headers repeat it 8 apart
    struct.pack_into("<f", buf, BASE + 0, params["init"])
    struct.pack_into("<f", buf, BASE + 8, params["high"])
    struct.pack_into("<f", buf, BASE + 12, params["low"])
    struct.pack_into("<f", buf, BASE + 28, float(params["segments"]))
    struct.pack_into("<f", buf, BASE + 32, params["interval"])
    struct.pack_into("<f", buf, BASE + 40, params.get("rate", 0.1))
    struct.pack_into("<f", buf, BASE + 44, params.get("quiet", 2.0))
    with open(path, "wb") as fh:
        fh.write(bytes(buf) + struct.pack("<%df" % total, *values))
    return path


print("=" * 60)
print("binio .bin reader checks")
print("=" * 60)

os.makedirs(FIX, exist_ok=True)
# A 20-leg CV between 0.1 and 0.13 V at 1 mV steps: 30 points per leg, 600 total.
params = {"init": 0.1, "high": 0.13, "low": 0.1, "segments": 20, "interval": 0.001}
truth_x, _ = binio.build_axis(params, 600)
truth_y = [i * 1e-6 for i in range(600)]
p = build(os.path.join(FIX, "synthetic_cv.bin"), "CV", "Cyclic Voltammetry", params, truth_y)
d = binio.read_bin(p)
check("technique and full name", d["technique"] == "CV" and
      d["technique_name"] == "Cyclic Voltammetry", (d["technique"], d["technique_name"]))
check("build tag", d["build"] == "r0815", d["build"])
check("point count", d["points"] == 600, d["points"])
check("header size", d["header_bytes"] == HEADER, d["header_bytes"])
check("method block read back", abs(d["method"]["high_E"] - 0.13) < 1e-6 and
      d["method"]["segments"] == 20 and abs(d["method"]["step"] - 0.001) < 1e-8,
      d["method"])
check("axis reconstructed", d["axis"] == "reconstructed", d["axis"])
check("axis matches the triangle point for point",
      all(abs(a - b) < 1e-6 for a, b in zip(d["series"]["x"], truth_x)),
      [d["series"]["x"][28:33], truth_x[28:33]])
check("turning point belongs to the next leg",
      abs(d["series"]["x"][30] - 0.13) < 1e-6 and abs(d["series"]["x"][29] - 0.129) < 1e-6,
      d["series"]["x"][28:32])
check("current column survives the float32 round trip",
      all(abs(a - b) <= 1e-9 + 1e-6 * abs(b) for a, b in zip(d["series"]["y"], truth_y)),
      (d["series"]["y"][:3], truth_y[:3]))
brief = originlab.read_file(p)
check("read_file strips the raw values", "series" not in brief and
      brief["proof_level"] == "verified", sorted(brief)[:6])

wrong = build(os.path.join(FIX, "synthetic_badaxis.bin"), "CV", "Cyclic Voltammetry",
              params, truth_y[:597])
dw = binio.read_bin(wrong)
check("point-count mismatch refuses to fake an axis",
      dw["axis"] != "reconstructed" and any("电势轴没重建" in w for w in dw["warnings"]),
      (dw["axis"], dw["warnings"]))

it = build(os.path.join(FIX, "synthetic_it.bin"), "i-t", "Amperometric i-t Curve",
           {"init": 0.5, "high": 0.5, "low": 0.0, "segments": 1, "interval": 0.1},
           [6e-5] * 50)
di = binio.read_bin(it)
check("i-t gets a time axis", di["axis"] == "uniform_from_interval" and
      abs(di["series"]["x"][10] - 1.0) < 1e-6, (di["axis"], di["series"]["x"][:3]))

p3 = os.path.join(FIX, "not_a_bin.bin")
with open(p3, "wb") as fh:
    fh.write(b"MZ\x90\x00" + b"\x00" * 2000)
try:
    binio.read_bin(p3)
    check("non-workstation .bin rejected", False, "no error raised")
except binio.BinFormatError as exc:
    check("non-workstation .bin rejected", "文件头" in str(exc), str(exc)[:60])


def txt_rows(fn):
    rows = []
    for ln in open(fn, encoding="latin-1").read().splitlines():
        m = re.match(r"^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*(?:[eE][-+]?\d+)?)\s*$", ln)
        if m:
            rows.append((float(m.group(1)), float(m.group(2))))
    return rows


if os.path.isdir(REAL):
    bins = sorted(glob.glob(os.path.join(REAL, "*.bin")))
    check("real folder has files", bool(bins), len(bins))
    for fn in bins:
        try:
            r = binio.read_bin(fn)
            check("real parses: %s" % os.path.basename(fn), r["points"] > 0 and
                  all(v == v for v in r["series"]["y"]), (r["technique"], r["points"]))
            twin = fn[:-4] + ".txt"
            if os.path.isfile(twin):
                ref = txt_rows(twin)
                n = min(len(ref), r["points"])
                badE = sum(1 for i in range(n) if r["series"]["x"] and
                           abs(r["series"]["x"][i] - ref[i][0]) > 5e-4)
                badI = sum(1 for i in range(n)
                           if abs(r["series"]["y"][i] - ref[i][1]) > 1e-3 * max(1e-6, abs(ref[i][1])))
                check("vs vendor export: %s" % os.path.basename(fn),
                      n > 0 and badE == 0 and badI == 0,
                      (n, badE, badI))
        except Exception as exc:
            check("real parses: %s" % os.path.basename(fn), False, repr(exc)[:90])
else:
    print("  skip  real sample folder not present (%s)" % REAL)

print()
if fails:
    print("FAILED (%d): %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("all binio checks passed")
