"""Regenerate icon.svg / icon.png — the plugin's own artwork.

Deliberately NOT matplotlib/PIL: the venv only carries originpro + numpy, and a
dependency just for one icon would be silly. numpy draws, the stdlib writes the
PNG (zlib + struct). The SVG is written from the same geometry so the card icon
and the README glyph stay identical.

Design: three big shapes so it survives being rendered at 24-32 px inside a dsh
plugin card -- a plot frame, a tilted loop that reads both as the "O" of Origin
and as a hysteresis/Voltammetry loop, and one amber sample point. Original
artwork; no instrument vendor's logo, wordmark, or product shape is referenced.
"""

import os
import struct
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BG = (11, 27, 43)           # ink navy
AXIS = (176, 196, 214)      # cool light grey
LOOP = (57, 214, 195)       # teal
DOT = (247, 178, 74)        # amber
SIZE = 512
SS = 2                      # supersample factor for anti-aliasing

# All geometry in unit coordinates (0..1 of the tile), shared by both renderers.
AX = {"x0": 0.20, "y0": 0.80, "x1": 0.84, "y1": 0.20}      # the L frame
AX_W = 0.055                                                # frame stroke
LOOP_C = (0.545, 0.475)                                     # loop centre
LOOP_R = (0.255, 0.155)                                     # semi-axes
LOOP_TILT = -24.0                                           # degrees
LOOP_W = 0.072                                              # loop stroke
DOT_AT = 0.175                                              # param along the loop
DOT_R = 0.052


def loop_points(n=400):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    x = LOOP_R[0] * np.cos(t)
    y = LOOP_R[1] * np.sin(t)
    a = np.deg2rad(LOOP_TILT)
    return (LOOP_C[0] + x * np.cos(a) - y * np.sin(a),
            LOOP_C[1] + x * np.sin(a) + y * np.cos(a))


def dot_center():
    xs, ys = loop_points()
    i = int(DOT_AT * len(xs)) % len(xs)
    return xs[i], ys[i]


def _band(dist, half, edge):
    """Anti-aliased stroke mask from a distance field."""
    return np.clip((half - dist) / edge + 0.5, 0, 1)


def _seg_distance(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    len2 = vx * vx + vy * vy or 1e-12
    t = np.clip((wx * vx + wy * vy) / len2, 0, 1)
    return np.hypot(wx - t * vx, wy - t * vy)


def draw(scale):
    n = SIZE * scale
    u, v = np.meshgrid((np.arange(n) + 0.5) / n, (np.arange(n) + 0.5) / n)
    edge = 1.6 * scale / n

    # rounded-square background
    r = 0.22
    dx = np.maximum(0.08 - u, u - 0.92)
    dy = np.maximum(0.08 - v, v - 0.92)
    corner = np.hypot(np.clip(dx, 0, None), np.clip(dy, 0, None))
    inside = 1 - np.clip(np.maximum(np.maximum(dx, dy), corner) - r + 0.5 * edge, 0, edge) / edge
    px = np.zeros((n, n, 4))
    for c, val in enumerate(BG):
        px[..., c] = val * inside
    px[..., 3] = inside * 255

    def paint(mask, rgb):
        for c in range(3):
            px[..., c] = px[..., c] * (1 - mask) + rgb[c] * mask
        px[..., 3] = np.maximum(px[..., 3], mask * 255 * inside)

    # the L frame: two capsules joined at the origin corner
    ax0, ay0, ax1, ay1 = AX["x0"], AX["y0"], AX["x1"], AX["y1"]
    d_frame = np.minimum(_seg_distance(u, v, ax0, ay0, ax1, ay0),
                         _seg_distance(u, v, ax0, ay0, ax0, ay1))
    paint(_band(d_frame, AX_W / 2, edge), AXIS)

    # the loop, as a distance field to its polyline
    xs, ys = loop_points()
    dist = np.full((n, n), np.inf)
    for i in range(len(xs)):
        j = (i + 1) % len(xs)
        dist = np.minimum(dist, _seg_distance(u, v, xs[i], ys[i], xs[j], ys[j]))
    paint(_band(dist, LOOP_W / 2, edge), LOOP)

    # one sample point sitting on the loop
    cx, cy = dot_center()
    paint(_band(np.hypot(u - cx, v - cy), DOT_R, edge), DOT)

    return px.reshape(SIZE, scale, SIZE, scale, 4).mean(axis=(1, 3)).astype(np.uint8)


def write_png(path, px):
    raw = b"".join(b"\x00" + row.tobytes() for row in px)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


def write_svg(path):
    """The same geometry, as vectors."""
    cx, cy = LOOP_C[0] * 512, LOOP_C[1] * 512
    rx, ry = LOOP_R[0] * 512, LOOP_R[1] * 512
    dx, dy = dot_center()
    a = 0.08 * 512
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <rect x="0" y="0" width="512" height="512" rx="113" fill="#0B1B2B"/>
  <path d="M {AX['x0'] * 512:.1f} {AX['y0'] * 512:.1f} H {AX['x1'] * 512:.1f}
           M {AX['x0'] * 512:.1f} {AX['y0'] * 512:.1f} V {AX['y1'] * 512:.1f}"
        stroke="#B0C4D6" stroke-width="{AX_W * 512:.1f}" stroke-linecap="round" fill="none"/>
  <ellipse cx="{cx:.1f}" cy="{cy:.1f}" rx="{rx:.1f}" ry="{ry:.1f}"
           transform="rotate({LOOP_TILT} {cx:.1f} {cy:.1f})" fill="none"
           stroke="#39D6C3" stroke-width="{LOOP_W * 512:.1f}"/>
  <circle cx="{dx * 512:.1f}" cy="{dy * 512:.1f}" r="{DOT_R * 512:.1f}" fill="#F7B24A"/>
</svg>
"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(svg)


def main():
    write_png(os.path.join(ROOT, "icon.png"), draw(SS))
    write_svg(os.path.join(ROOT, "icon.svg"))
    size = os.path.getsize(os.path.join(ROOT, "icon.png"))
    print("wrote icon.png (%d bytes, dsh limit 262144) and icon.svg" % size)


if __name__ == "__main__":
    main()
