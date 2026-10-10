"""Robust ASCII data reader for Origin-bound scientific files.

Why not just originpro's from_file: Origin's ASCII import is fussy about
multi-row headers, '#'/'//' comment lines and space-aligned columns, and it
fails by producing an empty sheet rather than an error. Why not the original
the old read_dat: it splits on TAB only, so space- or comma-aligned .dat
files silently turn into a single NaN column.

This reader sniffs the delimiter, handles BOM and GBK, skips comment lines, and
understands the common "long name / units" two-row header.
"""

import csv
import os
import re

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "cp1252", "latin-1")
COMMENT_PREFIXES = ("#", "//", "!", ";")
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eEdD][+-]?\d+)?$")
_DELIMS = ("\t", ",", ";", "|")


def _is_number(token):
    t = token.strip().replace("d", "E").replace("D", "E")
    return bool(t) and bool(_NUM.match(t))


def _pick_name_row(head, width):
    """The long-name row is the one with the most distinct tokens.

    Instrument dumps often start with a repeated filler row ("Sample Sample
    Sample") or a caption, so taking head[0] blindly names every column 'Sample'.
    """
    best_i, best_score = 0, None
    for i, row in enumerate(head):
        toks = [row[c].strip() for c in range(width)]
        uniq = len({t for t in toks if t})
        score = uniq * 10 + sum(1 for t in toks if t)
        if uniq <= 1 and width > 1:
            score -= 50
        if best_score is None or score > best_score:
            best_i, best_score = i, score
    return best_i


def read_text(path):
    """Decode a data file, trying the encodings that actually occur in labs."""
    last = None
    for enc in ENCODINGS:
        try:
            with open(path, "r", encoding=enc, newline="") as fh:
                return fh.read(), enc
        except (UnicodeDecodeError, LookupError) as exc:
            last = exc
    raise ValueError("无法解码 %s（试过 %s），最后错误：%s" % (path, "/".join(ENCODINGS), last))


def sniff_delimiter(rows):
    """Pick the delimiter that maximises the number of consistent numeric columns."""
    body = [r for r in rows if r.strip() and not r.lstrip().startswith(COMMENT_PREFIXES)]
    if not body:
        return None
    best, best_score = None, (-1, -1)
    for delim in _DELIMS:
        grid = [next(csv.reader([line], delimiter=delim)) for line in body]
        widths = {len(g) for g in grid}
        if not widths:
            continue
        width = max(widths)
        if width < 2:
            continue
        # score: how many columns are numeric in >80% of rows, tie-broken by width
        hits = 0
        for col in range(width):
            vals = [g[col] for g in grid if len(g) > col]
            if vals and sum(1 for v in vals if _is_number(v)) >= 0.8 * len(vals):
                hits += 1
        score = (hits, width)
        if score > best_score:
            best, best_score = delim, score
    if best is not None:
        return best
    # fall back to whitespace alignment
    grid = [line.split() for line in body]
    width = max(len(g) for g in grid)
    if width >= 2:
        return None
    raise ValueError("识别不出分隔符，也没有多列空白对齐的数据")


def parse(path, comment=None):
    """Return {columns:[{name,units,values}], rows, delimiters, encoding, warnings}."""
    text, enc = read_text(path)
    comments = COMMENT_PREFIXES if comment is None else (comment,)
    raw_lines = text.splitlines()
    kept = [ln for ln in raw_lines if ln.strip() and not ln.lstrip().startswith(comments)]
    if not kept:
        raise ValueError("文件里只有注释或空行：%s" % path)

    warnings = []
    delim = sniff_delimiter(kept)
    if delim is None:
        grid = [ln.split() for ln in kept]
        warnings.append("按空白对齐解析（无显式分隔符）")
    else:
        grid = [next(csv.reader([ln], delimiter=delim)) for ln in kept]

    width = max(len(r) for r in grid)
    if len({len(r) for r in grid}) > 1:
        warnings.append("行长度不一致，已按最宽 %d 列补齐" % width)
    grid = [r + [""] * (width - len(r)) for r in grid]

    # split leading header rows (rows that are mostly non-numeric) from data
    head, body = [], []
    for i, row in enumerate(grid):
        numeric = sum(1 for c in row if _is_number(c))
        if numeric >= max(1, int(0.6 * width)):
            body = grid[i:]
            break
        head.append(row)
    else:
        raise ValueError("找不到数值数据行：%s（只有表头？）" % path)

    k = _pick_name_row(head, width) if head else 0
    longs = [head[k][c].strip() for c in range(width)] if head else [""] * width
    if k + 1 < len(head):
        units = [head[k + 1][c].strip() for c in range(width)]
    elif k > 0:
        units = [head[k - 1][c].strip() for c in range(width)]
    else:
        units = [""] * width
    extra = len(head) - 2 if len(head) > 2 else max(0, len(head) - 1)
    if extra:
        warnings.append("忽略了 %d 行额外表头（只取长名/单位两行）" % extra)

    columns = []
    for c in range(width):
        vals = []
        for row in body:
            cell = row[c].strip()
            if not cell or cell.upper() in ("NA", "NAN", "-", "--"):
                vals.append(None)
                continue
            try:
                vals.append(float(cell.replace("d", "E").replace("D", "E")))
            except ValueError:
                vals.append(None)
        live = sum(1 for v in vals if v is not None)
        if live == 0:
            warnings.append("第 %d 列 %r 解析后全为空，已跳过" % (c + 1, longs[c] or "col%d" % (c + 1)))
            continue
        columns.append({
            "index": c + 1,
            "name": longs[c] or "col%d" % (c + 1),
            "units": units[c] or "",
            "values": vals,
            "numeric_rows": live,
        })
    if not columns:
        raise ValueError("解析后没有任何有效数值列：%s" % path)
    return {
        "path": os.path.abspath(path),
        "encoding": enc,
        "delimiter": delim or "whitespace",
        "rows": len(body),
        "header_rows": len(head),
        "columns": columns,
        "warnings": warnings,
    }
