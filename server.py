"""origin-bridge MCP server.

Deliberately a synchronous, newline-delimited JSON-RPC loop over stdio instead
of the official `mcp` SDK's asyncio transport: on Windows the SDK builds an
asyncio ProactorEventLoop, whose self-pipe falls back to a loopback
socketpair() (127.0.0.1 listen+connect+accept). Host firewalls that block
loopback accepts make that hang forever, the MCP handshake never completes, and
the client stalls for its full 60s request timeout before giving up.

Usage:  python server.py            # stdio, for dsh / any MCP client
        python server.py --tools   # print the tool table and exit
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import originlab  # noqa: E402

SERVER_NAME = "origin-bridge"
SERVER_VERSION = "0.4.2"
PROTOCOL = "2024-11-05"


def _write(a):
    if a.get("rows"):
        return originlab.write_block(a.get("headers") or [], a["rows"], a.get("book_name", ""))
    return originlab.write_columns(a.get("columns") or {}, a.get("book_name", ""))

TOOLS = [
    {
        "name": "origin_status",
        "description": "检查能否连上本机 Origin/OriginPro，返回版本、Python、已打开的页面。任何绘图操作前先用它确认连接。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "run": lambda a: originlab.status(),
    },
    {
        "name": "origin_import",
        "description": "把本地数据文件导入 Origin 新建的工作表。支持 .dat/.csv/.txt/.tsv/.xls/.xlsx/.wks，以及电化学工作站的二进制 .bin（CV/LSV/EIS/i-t，头部带 80F21B00 魔数那种）。返回 worksheet 句柄和列清单，后续步骤原样传回该句柄。.bin 会带 role_confidence：ambiguous 表示两列同量级、分不清电势/电流，这时先 origin_view 看图再定轴标题。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "数据文件绝对路径"},
                "book_name": {"type": "string", "description": "可选：设给工作表的长名（Long Name），不是短名"},
            },
            "required": ["path"],
        },
        "run": lambda a: originlab.import_file(a["path"], a.get("book_name", "")),
    },
    {
        "name": "origin_write",
        "description": "用内联数据建工作表。窄表用 columns={列名:[数值...]}；宽表（>5 列）用 headers+rows 二维块，一次写完，比逐列快很多。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "columns": {"type": "object", "description": '{"Time": [0,1,2], "Signal": [3,4,5]}'},
                "headers": {"type": "array", "description": "配合 rows 使用：列名列表"},
                "rows": {"type": "array", "description": "配合 headers 使用：二维数据块"},
                "book_name": {"type": "string"},
            },
        },
        "run": lambda a: _write(a),
    },
    {
        "name": "origin_formula",
        "description": "给某列设计算公式（Fx），让它随兄弟列自动重算，用于派生列。列号从 1 开始或给列名。会读回该列算出的行数和首末值。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worksheet": {"type": "string"},
                "col": {"type": ["integer", "string"], "description": "1 起始列号或列名"},
                "formula": {"type": "string", "description": "如 Col(1)*2.5+0.1；LabTalk 写法"},
                "label": {"type": "string"},
                "units": {"type": "string"},
            },
            "required": ["worksheet", "col", "formula"],
        },
        "run": lambda a: originlab.column_formula(a["worksheet"], a["col"], a["formula"],
                                                  a.get("label", ""), a.get("units", "")),
    },
    {
        "name": "origin_plot",
        "description": "在工作表上画图。x/y 用 1 起始列号或列名；y 可以给列表，一次把多条曲线画进同一层。plot_type: line|scatter|line_symbol|column。给 template 用 Origin 自带图模建页（如 doubley/heat_map/line），给 graph+layer 则往已有图页的指定层里加曲线。返回里有 warnings 时代表图可能不可读（线性轴跨数量级等），要把选项交给用户决定。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worksheet": {"type": "string", "description": "origin_import/origin_write 返回的 worksheet 句柄"},
                "x": {"type": ["integer", "string"], "default": 1},
                "y": {"type": ["integer", "string", "array"], "default": 2,
                      "description": "列号/列名，或列表 [2,3,4] 一次画多条"},
                "plot_type": {"type": "string", "enum": ["line", "scatter", "line_symbol", "column"], "default": "line"},
                "graph_name": {"type": "string"},
                "title": {"type": "string", "description": "图层标题"},
                "graph": {"type": "string", "description": "已有图页句柄；配合 layer 往该层加曲线"},
                "layer": {"type": "integer", "default": 0, "description": "目标层索引（双 Y 轴的第二层=1）"},
                "template": {"type": "string", "description": "Origin 图模名，如 line/scatter/doubley/heat_map/cmap/mesh"},
            },
            "required": ["worksheet"],
        },
        "run": lambda a: originlab.plot(a["worksheet"], a.get("x", 1), a.get("y", 2),
                                        a.get("plot_type", "line"), a.get("graph_name", ""),
                                        a.get("title", ""), graph=a.get("graph", ""),
                                        layer=a.get("layer", 0), template=a.get("template", "")),
    },
    {
        "name": "origin_read_file",
        "description": "只解析数据文件、不碰 Origin：返回编码、分隔符、表头行数、每列长名/单位/有效行数，以及仪器参数 metadata。拿到陌生 .dat/.csv/.txt 先用它看清结构，再决定 origin_import 用哪几列。支持 #、//、!、; 注释、BOM、GBK、空格对齐、Fortran 的 1.5d+00 指数、NA/- 缺失值、Origin 三行表头（Long Name/Units/Comments），也支持电化学工作站（CH Instruments 等）导出的 txt：前面几十行参数说明会被跳进 metadata，紧贴数据的 Potential/V, Current/A 这类表头按斜杠拆出列名和单位。.bin 走另一条解析路线：返回技术标签（CV/LSV/IMP/i-t）、点数、两个 float32 数组的范围和 role_confidence。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "数据文件绝对路径（含 .bin）"},
                "comment": {"type": "string", "description": "可选：强制指定注释前缀，默认自动识别"},
            },
            "required": ["path"],
        },
        "run": lambda a: originlab.read_file(a["path"], a.get("comment")),
    },
    {
        "name": "origin_style",
        "description": "改图并逐项读回验证：轴标题、对数轴、坐标范围与步长、逐曲线颜色/线宽/符号形状/符号大小/填充/透明度。读不回来的项会进 failed 列表，不会假装成功。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string", "description": "origin_plot 返回的 graph 句柄"},
                "layer": {"type": "integer", "default": 0, "description": "层索引，0 起"},
                "series": {"type": "integer", "default": 0, "description": "该层里的第几条曲线，0 起"},
                "x_title": {"type": "string"},
                "y_title": {"type": "string"},
                "x_scale": {"type": "string", "enum": ["linear", "log", "log10", "ln", "log2",
                                                        "probit", "probability", "reciprocal",
                                                        "offset_reciprocal", "logit"]},
                "y_scale": {"type": "string", "enum": ["linear", "log", "log10", "ln", "log2",
                                                        "probit", "probability", "reciprocal",
                                                        "offset_reciprocal", "logit"]},
                "xlim": {"type": "array", "description": "[from,to] 或 [from,to,step]"},
                "ylim": {"type": "array", "description": "[from,to] 或 [from,to,step]"},
                "xtick": {"type": "number", "description": "X 刻度步长"},
                "ytick": {"type": "number", "description": "Y 刻度步长"},
                "color": {"type": "string", "description": "如 #0072B2 或 Origin 颜色名"},
                "line_width": {"type": "number"},
                "symbol_kind": {"type": "integer", "description": "Origin 符号形状编号，如 3=圆 1=方"},
                "symbol_size": {"type": "number"},
                "symbol_interior": {"type": "integer", "enum": [0, 1, 2, 3],
                                     "description": "0 无符号 1 实心 2 空心 3 点心"},
                "transparency": {"type": "number", "description": "0-100 百分比"},
                "fill_area": {"type": "boolean", "description": "线下填充（面积图效果）"},
            },
            "required": ["graph"],
        },
        "run": lambda a: originlab.style(
            a["graph"], layer=a.get("layer", 0), series=a.get("series", 0),
            x_title=a.get("x_title", ""), y_title=a.get("y_title", ""),
            x_scale=a.get("x_scale", ""), y_scale=a.get("y_scale", ""),
            xlim=a.get("xlim"), ylim=a.get("ylim"),
            xtick=a.get("xtick", 0.0), ytick=a.get("ytick", 0.0),
            color=a.get("color", ""), symbol_kind=a.get("symbol_kind", 0),
            symbol_size=a.get("symbol_size", 0.0), symbol_interior=a.get("symbol_interior", 0),
            line_width=a.get("line_width", 0.0), transparency=a.get("transparency", 0.0),
            fill_area=a.get("fill_area", False)),
    },
    {
        "name": "origin_fit",
        "description": "拟合。kind=linear 线性（Origin 标准版可用）；非线性直接用预设名 kind=gauss|lorentz|voigt|expdec1|expdec2|sine|power|logistic|boltzmann|doseresp|cubic（本机逐个实测可用；poly3=cubic、expdecay=expdec1 是别名），或 kind=nlfitsing + func 给 Origin 的函数名（区分大小写：Gauss 对、gauss1 不存在）。func 只能是内置函数名，自定义表达式不支持。只给 func 不给 kind 时按非线性执行并在 note 里说明。可固定参数 fixed、给初值 starts、加边界 bounds，并可生成报告表。缺失值已消毒为 null。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worksheet": {"type": "string"},
                "x": {"type": ["integer", "string"], "default": 1},
                "y": {"type": ["integer", "string"], "default": 2},
                "kind": {"type": "string", "enum": ["linear", "nlfitsing", "gauss", "lorentz",
                                                    "voigt", "expdec1", "expdec2", "sine", "power",
                                                    "logistic", "boltzmann", "doseresp", "cubic",
                                                    "poly3", "expdecay"],
                         "default": "linear"},
                "func": {"type": "string", "description": "Origin 内置函数名（区分大小写），如 Gauss / ExpDec1；给了 func 就按非线性拟合，不要写自定义表达式"},
                "fixed": {"type": "object", "description": "固定参数 {参数名: 值}，如 {\"y0\": 0}"},
                "starts": {"type": "object", "description": "初值 {参数名: 值}；linear 用 Slope/Intercept"},
                "bounds": {"type": "object", "description": "边界 {参数名: [下界, 上界]}，null 表示不设"},
                "make_report": {"type": "boolean", "default": False, "description": "生成 Origin 拟合报告表"},
                "full": {"type": "boolean", "default": False,
                         "description": "true 返回 Origin 的完整报告字典（很大）；默认只回参数值与关键统计"},
            },
            "required": ["worksheet"],
        },
        "run": lambda a: originlab.fit(a["worksheet"], a.get("x", 1), a.get("y", 2),
                                       a.get("kind", "linear"), a.get("func", ""),
                                       fixed=a.get("fixed"), starts=a.get("starts"),
                                       bounds=a.get("bounds"), make_report=a.get("make_report", False),
                                       full=a.get("full", False)),
    },
    {
        "name": "origin_export",
        "description": "把图页导出为图片文件，并校验文件确实存在、够大、文件头正确（Origin 有静默失败的历史）。fmt: png/tif/jpg/svg/pdf/emf（tiff→tif、jpeg→jpg 会自动改写扩展名）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string"},
                "path": {"type": "string", "description": "输出文件绝对路径；省略则用图页名放到当前目录"},
                "fmt": {"type": "string", "enum": ["png", "tif", "svg", "pdf", "emf"], "default": "png"},
                "width": {"type": "integer", "default": 1600},
            },
        },
        "run": lambda a: originlab.export(a.get("graph", ""), a.get("path", ""),
                                          a.get("fmt", "png"), a.get("width", 1600)),
    },
    {
        "name": "origin_save_project",
        "description": "保存为可在 Origin 里继续编辑的 .opju 工程文件（不是图片快照）。省略 path 会自动命名。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
        "run": lambda a: originlab.save_project(a.get("path", "")),
    },
    {
        "name": "origin_open",
        "description": "打开已有的 .opju/.opj 工程，返回页面清单。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        "run": lambda a: originlab.open_project(a["path"]),
    },
    {
        "name": "origin_inspect",
        "description": "读回当前项目真实状态：有哪些页面、图页有几层、每层轴标题和曲线数、已分配的句柄。改图之前先调用它，别猜索引。",
        "inputSchema": {
            "type": "object",
            "properties": {"graph": {"type": "string"}},
        },
        "run": lambda a: originlab.inspect(a.get("graph", "")),
    },
    {
        "name": "origin_figure",
        "description": "主路径：一次调用完成 导入/写数 → 画图 → 轴标题 → 导出图片 → 保存可编辑 .opju。用户只想“把这份数据画成图并给我文件”时用它，不要拆成多步。返回里若有 warnings，说明图虽然画成了但可能不可读（例如线性横轴跨了几个数量级，点全挤在最左边）：把 next_actions 里的方案讲给用户让他选，不要自己擅自改轴。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "数据文件路径（与 columns 二选一）"},
                "columns": {"type": "object", "description": "内联数据 {列名: [值...]}，优先用 source"},
                "x": {"type": ["integer", "string"], "default": 1},
                "y": {"type": ["integer", "string"], "default": 2},
                "plot_type": {"type": "string", "enum": ["line", "scatter", "line_symbol", "column"], "default": "line"},
                "title": {"type": "string"},
                "x_title": {"type": "string"},
                "y_title": {"type": "string"},
                "fmt": {"type": "string", "enum": ["png", "tif", "svg", "pdf", "emf"], "default": "png"},
                "width": {"type": "integer", "default": 1600},
                "output_dir": {"type": "string", "description": "交付目录：图片 + opju 都写到这。不给则只建图、不落盘"},
                "export_path": {"type": "string"},
                "project_path": {"type": "string"},
                "do_project": {"type": "boolean", "default": False,
                               "description": "true 时即使没给目录也存一份 .opju 到 Origin 的 User Files"},
                "template": {"type": "string", "description": "Origin 图模名，如 line/scatter/doubley；省略走默认 plotxy"},
                "legend": {"type": ["boolean", "array"], "default": False,
                           "description": "true=按列长名自动生成图例；或给名称列表写死"},
            },
        },
        "run": lambda a: originlab.figure(
            source=a.get("source", ""), columns=a.get("columns"), x=a.get("x", 1), y=a.get("y", 2),
            plot_type=a.get("plot_type", "line"), title=a.get("title", ""),
            x_title=a.get("x_title", ""), y_title=a.get("y_title", ""), fmt=a.get("fmt", "png"),
            width=a.get("width", 1600), output_dir=a.get("output_dir", ""),
            export_path=a.get("export_path", ""), project_path=a.get("project_path", ""),
            do_project=a.get("do_project"), template=a.get("template", ""),
            legend=a.get("legend", False)),
    },
    {
        "name": "origin_exit",
        "description": "关闭 Origin。默认不保存；给 save_to 会先存一份 .opju 再退。",
        "inputSchema": {
            "type": "object",
            "properties": {"save_to": {"type": "string"}},
        },
        "run": lambda a: originlab.exit_origin(a.get("save_to", "")),
    },
    {
        "name": "origin_instances",
        "description": "清点机器上的 Origin 实例：分「后台无窗口」（客户端退出后残留，会占配额）和「前台有窗口」（可能是用户自己开着的项目）。Origin 实例数超上限、或报 connection_error 时先调这个看情况。",
        "inputSchema": {"type": "object", "properties": {}},
        "run": lambda a: originlab.instances(),
    },
    {
        "name": "origin_reclaim",
        "description": "回收残留的 Origin 实例：只关**没有窗口**的后台实例；有窗口的一律不动，只在返回里告诉你哪几个需要用户自己关。close_background=false 时只看不动手。",
        "inputSchema": {
            "type": "object",
            "properties": {"close_background": {"type": "boolean", "default": True}},
        },
        "run": lambda a: originlab.reclaim(bool(a.get("close_background", True))),
    },
    {
        "name": "origin_annotate",
        "description": "在图上加文字标注和参考线（数据坐标系）。比如标峰位、画阈值线。标注没有值可读回，返回 readback_only，要确证用 origin_view 看图。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string"},
                "layer": {"type": "integer", "default": 0},
                "labels": {"type": "array", "description": '[{"text":"peak","x":12.5,"y":80}]'},
                "lines": {"type": "array", "description": '[{"x1":0,"y1":50,"x2":23,"y2":50}]'},
            },
            "required": ["graph"],
        },
        "run": lambda a: originlab.annotate(a["graph"], a.get("labels") or [],
                                            a.get("lines") or [], a.get("layer", 0)),
    },
    {
        "name": "origin_layer",
        "description": "给已有图页加一层：双 Y 轴用 layer_type=right，上下多面板用 top/bottom。返回 Origin 自己给这层起的名字（RightY 等）作为证据，以及新层索引。往新层画：origin_plot 时带上 graph 和 layer=该索引。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string"},
                "layer_type": {"type": ["string", "integer"], "default": "right",
                               "description": "bottom(=bottom_left)|top|right|left|top_right|bottom_right|inset，或对应整数 0-6"},
            },
            "required": ["graph"],
        },
        "run": lambda a: originlab.add_layer(a["graph"], a.get("layer_type", "right")),
    },
    {
        "name": "origin_legend",
        "description": "图例。不给 entries 时用 legend -r 按各条曲线对应列的长名自动生成；给了就逐行写死。position 用图内位置名（inside_top_right/top_right/bottom_left…）。位置按该层数据坐标写入并读回，文字和可见性都验证。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string", "description": "省略则用当前活动图页"},
                "layer": {"type": "integer", "default": 0},
                "entries": {"type": "array", "description": '["sin(x)","cos(x)"]；省略=用列长名自动生成'},
                "position": {"type": "string", "enum": ["top_left", "top", "top_right", "left", "right",
                                                        "bottom_left", "bottom", "bottom_right",
                                                        "inside_top_left", "inside_top_right",
                                                        "inside_bottom_left", "inside_bottom_right", "inside"]},
                "show": {"type": "boolean", "default": True},
            },
        },
        "run": lambda a: originlab.legend_entries(a.get("graph", ""), a.get("layer", 0),
                                                  a.get("entries"), a.get("position", ""),
                                                  a.get("show", True)),
    },
    {
        "name": "origin_matrix",
        "description": "网格数据 → Origin 矩阵 → 模板化矩阵图：heat_map 热力图、cmap 三维彩色映射面、mesh 网格面、contline/contgray 等高线、3d/bar3d。rows 是二维数值矩阵。可选 xy_range=[x1,x2,y1,y2] 设坐标范围。给 export_path/project_path 会顺手落盘。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rows": {"type": "array", "description": "二维矩阵 [[...],[...]]，行=Y 方向、列=X 方向"},
                "template": {"type": "string", "enum": ["heat_map", "cmap", "mesh", "contline",
                                                        "contgray", "3d", "glwireface", "glwirefrm", "bar3d"],
                             "default": "heat_map"},
                "book_name": {"type": "string"},
                "graph_name": {"type": "string"},
                "xy_range": {"type": "array", "description": "[x1,x2,y1,y2] 矩阵坐标范围"},
                "title": {"type": "string"},
                "export_path": {"type": "string"},
                "project_path": {"type": "string"},
            },
            "required": ["rows"],
        },
        "run": lambda a: originlab.matrix_plot(a["rows"], a.get("template", "heat_map"),
                                              a.get("book_name", ""), a.get("graph_name", ""),
                                              a.get("xy_range"), a.get("title", ""),
                                              a.get("export_path", ""), a.get("project_path", "")),
    },
    {
        "name": "origin_chart_presets",
        "description": "列出 40 个科研图表预设（XPS/XRD/UV-Vis/荧光/循环伏安/Arrhenius…）：每个预设带好哪些列、默认轴标题、建议拟合函数。不确定用户要的是哪类图时先查这里，别自己编轴标题。category 可过滤。",
        "inputSchema": {
            "type": "object",
            "properties": {"category": {"type": "string"}},
        },
        "run": lambda a: originlab.chart_presets(a.get("category", "")),
    },
    {
        "name": "origin_chart_preset",
        "description": "看单个预设的完整约定：required_columns / optional_columns / default_axis_titles / default_plot_kind / suggested_fit / extra（如 XPS 的 X 轴要反向）。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        "run": lambda a: originlab.chart_preset(a["name"]),
    },
    {
        "name": "origin_chart_check",
        "description": "拿数据列名对预设做 dry-run（不碰 Origin）：缺哪列、建议用什么拟合、默认轴标题是什么。渲染前先调它，能省一次失败。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "data": {"type": "string", "description": "数据文件路径，会先 origin_read_file 解析列名"},
                "columns": {"type": "array", "description": "或者直接给列名列表"},
            },
            "required": ["name"],
        },
        "run": lambda a: originlab.chart_check(a["name"], a.get("data"), a.get("columns")),
    },
    {
        "name": "origin_chart_render",
        "description": "按科研预设一步出图：读预设的轴标题/图类型 → 导入数据 → 画图 → 轴标题 → 图例 → 导出图片 + 保存 .opju。用户说“把这个做成 XPS 图”这类需求优先用它。轴要反向（如 XPS 结合能）时再调 origin_style 设 xlim=[高,低]。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "source": {"type": "string", "description": "数据文件路径"},
                "columns": {"type": "object", "description": "或内联数据 {列名:[值]}"}
                ,
                "x": {"type": ["integer", "string"], "default": 1},
                "y": {"type": ["integer", "string", "array"], "default": 2},
                "output_dir": {"type": "string", "description": "交付目录；不给则只建图不落盘"},
                "x_title": {"type": "string", "description": "覆盖预设的 X 轴标题"},
                "y_title": {"type": "string"},
                "title": {"type": "string"},
                "plot_type": {"type": "string", "enum": ["line", "scatter", "line_symbol", "column"]},
                "with_legend": {"type": "boolean", "default": True},
                "fmt": {"type": "string", "enum": ["png", "tif", "svg", "pdf", "emf"], "default": "png"},
                "width": {"type": "integer", "default": 1600},
            },
            "required": ["name"],
        },
        "run": lambda a: originlab.chart_render(
            a["name"], a.get("source", ""), a.get("columns"), a.get("x", 1), a.get("y", 2),
            a.get("output_dir", ""), a.get("x_title"), a.get("y_title"), a.get("title"),
            a.get("plot_type"), a.get("with_legend", True), a.get("fmt", "png"),
            a.get("width", 1600)),
    },
    {
        "name": "origin_close_pages",
        "description": "关掉项目里攒下来的页面。长会话里页面攒太多会让 Origin 的 COM 桥报“无效指针”，这时按类型或名字清理。默认只看图页；dry_run=true 只报告不关。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["graph", "worksheet", "matrix", "notes"], "default": "graph"},
                "names": {"type": "array", "description": '只关这些页面 ["Graph1","Book2"]；省略=该类型全部'},
                "dry_run": {"type": "boolean", "default": False},
            },
        },
        "run": lambda a: originlab.close_pages(a.get("kind", "graph"), a.get("names"),
                                               a.get("dry_run", False)),
    },
    {
        "name": "origin_view",
        "description": "把图页渲染成小图回传（MCP image content），让模型自己看见结果。写后读不回的东西（样式、标注）用它确认，别声称你没看过的效果。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "graph": {"type": "string", "description": "省略则用当前活动图页"},
                "width": {"type": "integer", "default": 900},
            },
        },
        "run": lambda a: originlab.view(a.get("graph", ""), a.get("width", 900)),
    },
    {
        "name": "origin_labtalk",
        "description": "逃生舱：执行原始 LabTalk，并读回你点名的变量（numeric 读数值表达式，readback 读字符串变量）。实测限制：数值/表达式类语句可用（如 layer.x.type=2、win -a Graph1），但字符串赋值类语句（layer.x.title$=... 、set c1 -l ...）在这台机器上 lt_exec 会返回 False，轴标题等请用 origin_style。returned=false 就是真失败，别重试同一条。doc -s / saveas / exitloop 等破坏性命令被禁。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "LabTalk 语句，如 'win -a Book1; plotxy iy:=(1,2) plot:=200 ng:=0;'"},
                "readback": {"type": "array", "description": "要读回的字符串变量名，如 ['layer.x.title$']"},
                "numeric": {"type": "array", "description": "要读回的数值表达式，如 ['npy', 'layer.x.to']"},
            },
            "required": ["script"],
        },
        "run": lambda a: originlab.labtalk(a["script"], a.get("readback") or [],
                                           a.get("numeric") or []),
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def _log(msg):
    sys.stderr.write("[%s] %s\n" % (SERVER_NAME, msg))
    sys.stderr.flush()


def _tool_result(call_id, payload, is_error=False):
    content = []
    body = payload
    if isinstance(payload, dict) and payload.get("image_base64"):
        body = {k: v for k, v in payload.items() if k != "image_base64"}
        body["image_returned"] = True
        content.append({"type": "image", "data": payload["image_base64"],
                        "mimeType": payload.get("mime", "image/png")})
    content.append({"type": "text", "text": json.dumps(body, ensure_ascii=False, default=str)})
    return {"jsonrpc": "2.0", "id": call_id, "result": {"content": content, "isError": is_error}}


def _handle(msg):
    if not isinstance(msg, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "invalid request: expected an object"}}
    method = msg.get("method")
    call_id = msg.get("id")
    params = msg.get("params")
    if not isinstance(params, dict):
        params = {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": call_id, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}

    if method in ("notifications/initialized", "notifications/cancelled"):
        return None

    if method == "ping":
        return {"jsonrpc": "2.0", "id": call_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": call_id, "result": {
            "tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                      for t in TOOLS]}}

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = _BY_NAME.get(name)
        if tool is None:
            return _tool_result(call_id, {"ok": False, "code": "unknown_tool",
                                          "message": "没有这个工具：%s" % name}, True)
        if not isinstance(args, dict):
            return _tool_result(call_id, {"ok": False, "code": "bad_arguments",
                                          "message": "arguments 必须是对象"}, True)
        try:
            out = tool["run"](args)
            if isinstance(out, dict):
                out = {"ok": True, **out}
            return _tool_result(call_id, out, False)
        except originlab.OriginError as exc:
            return _tool_result(call_id, {"ok": False, "code": exc.code, "message": exc.message,
                                          "next_actions": exc.next_actions, **exc.extra}, True)
        except KeyError as exc:
            # A model can omit a required argument even though the schema names it;
            # that is a bad_arguments answer, not a crash with a stack trace.
            want = (tool.get("inputSchema") or {}).get("required") or []
            return _tool_result(call_id, {"ok": False, "code": "bad_arguments",
                                          "message": "%s 缺少参数 %s（该工具要求 %s）"
                                                     % (name, exc, want or "见 schema"),
                                          "next_actions": ["补上缺的参数再调用"]}, True)
        except Exception as exc:  # noqa: BLE001 - report, never kill the server
            _log("tool %s crashed: %r" % (name, exc))
            bare = isinstance(exc, (SystemError, TypeError, AttributeError))
            if bare:
                originlab.mark_suspect()
            return _tool_result(
                call_id,
                {"ok": False,
                 "code": "origin_unavailable" if bare else "internal_error",
                 "message": "%s: %s" % (type(exc).__name__, exc),
                 "next_actions": (["Origin 的 COM 通道进入了坏状态（多为页面被销毁后残留错误）",
                                   "重新 origin_import / origin_write 拿新句柄，旧句柄已作废",
                                   "还是不行就 origin_exit 让插件重启 Origin，或 origin_reclaim 关后台实例"]
                                  if bare else ["origin_status 看连接是否还活着"])}, True)

    if method == "shutdown":
        return {"jsonrpc": "2.0", "id": call_id, "result": {}}

    if call_id is not None:
        return {"jsonrpc": "2.0", "id": call_id,
                "error": {"code": -32601, "message": "method not found: %s" % method}}
    return None


def main():
    if "--tools" in sys.argv:
        for t in TOOLS:
            print("%-20s %s" % (t["name"], t["description"].split("。")[0]))
        return 0

    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    _log("ready; python=%s" % sys.executable)

    while True:
        raw = stdin.readline()
        if not raw:
            _log("stdin closed; exiting")
            return 0
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log("bad json: %s" % exc)
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            # A single message must never take the server down: dsh only spawns
            # this process once per session, so dying here means every origin
            # tool is dead until the user restarts the harness.
            try:
                reply = _handle(msg)
            except Exception as exc:  # noqa: BLE001
                _log("handler crashed on %r: %r" % (
                    msg.get("method") if isinstance(msg, dict) else type(msg).__name__, exc))
                reply = {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                         "error": {"code": -32603, "message": "internal error: %s" % exc}}
        if reply is None:
            continue
        stdout.write((json.dumps(reply, ensure_ascii=False, default=str) + "\n").encode("utf-8"))
        stdout.flush()


if __name__ == "__main__":
    sys.exit(main() or 0)
