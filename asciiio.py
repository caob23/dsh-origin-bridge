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


_NAME_KEYWORDS = ("long name", "longname", "name", "display", "l")
_UNIT_KEYWORDS = ("units", "unit", "u")
# "Potential/V", "电流 (mA)", "Z'/ohm": instruments pack the unit into the name.
_NAME_UNIT = re.compile(r"^(?P<name>[^/\(\[]+?)\s*(?:/\s*|\(|\[)\s*(?P<units>[^)\]]+?)\s*[\)\]]?$")
_PAIR = re.compile(r"^\s*(?P<key>[^=:]{2,40}?)\s*[=:]\s*(?P<value>.+?)\s*$")


def _row_cells(row, width):
    return [str(row[c] if c < len(row) else "").strip() for c in range(width)]


def _all_names(row, width):
    cells = _row_cells(row, width)
    return bool(cells) and all(cells) and not any(_is_number(c) for c in cells)


def _units_like(row, width):
    """A units row is short text ("s", "mV", "-") with no name-like slashes."""
    cells = [c for c in _row_cells(row, width) if c]
    return bool(cells) and not any(_is_number(c) for c in cells) \
        and all(len(c) <= 6 and "/" not in c and "(" not in c for c in cells)


def split_name_units(text):
    m = _NAME_UNIT.match(str(text).strip())
    if not m:
        return str(text).strip(), ""
    return m.group("name").strip(), m.group("units").strip()


def _pick_name_row(head, width):
    """Which header row carries the column long names?

    Origin's own exports use a labelled block (Long Name / Units / Comments), so
    that row wins. Instrument dumps (CH Instruments .txt and friends) put a page
    of settings first and the column line immediately above the data, so the last
    all-text row is the name row. Taking head[0] blindly named every column
    "Jan. 31" / 2011 15:12:13 on those files.
    """
    for i, row in enumerate(head):
        cells = _row_cells(row, width)
        if cells and cells[0].lower() in _NAME_KEYWORDS and _all_names(row, width):
            return i
    if head and _all_names(head[-1], width):
        # "Time Signal Flag" followed by "s mV -": the short row is the units row.
        if len(head) >= 2 and _all_names(head[-2], width) and _units_like(head[-1], width):
            return len(head) - 2
        return len(head) - 1
    if len(head) >= 2 and _all_names(head[-2], width) and _units_like(head[-1], width):
        return len(head) - 2
    best_i, best_score = 0, None
    for i, row in enumerate(head):
        toks = _row_cells(row, width)
        uniq = len({t for t in toks if t})
        score = uniq * 10 + sum(1 for t in toks if t)
        if uniq <= 1 and width > 1:
            score -= 50
        if best_score is None or score > best_score:
            best_i, best_score = i, score
    return best_i


def read_metadata(head, k):
    """Key = Value / Key: Value lines above the column row, kept for provenance."""
    meta = {}
    for row in head[:k]:
        line = ", ".join(c for c in row if c).strip()
        m = _PAIR.match(line)
        if m and not _is_number(m.group("key")):
            meta.setdefault(m.group("key").strip(), m.group("value"))
        if len(meta) >= 40:
            break
    return meta


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
    if width == 1:
        # "识别不出分隔符" would send the reader chasing encoding or comment
        # prefixes; the real reason is that a single column cannot make a plot.
        raise ValueError("文件只有一列数据：至少需要两列（x 和 y）才能解析成数据表")
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
    raw_names = _row_cells(head[k], width) if head else [""] * width
    if k + 1 < len(head):
        units = _row_cells(head[k + 1], width)
    elif k > 0 and head[k - 1] and head[k - 1][0].strip().lower() in _UNIT_KEYWORDS:
        units = _row_cells(head[k - 1], width)
    else:
        units = [""] * width
    names = []
    for c in range(width):
        nm, un = split_name_units(raw_names[c])
        names.append(nm)
        if un and not units[c]:
            units[c] = un
    raw_names = names
    metadata = read_metadata(head, k)
    extra = k if metadata else max(0, len(head) - 2)
    if extra:
        warnings.append("忽略了 %d 行文件说明（仪器参数已放进 metadata）" % extra)

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
            warnings.append("第 %d 列 %r 解析后全为空，已跳过" % (c + 1, raw_names[c] or "col%d" % (c + 1)))
            continue
        columns.append({
            "index": c + 1,
            "name": raw_names[c] or "col%d" % (c + 1),
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
        "metadata": metadata,
        "warnings": warnings,
    }
