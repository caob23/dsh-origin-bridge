"""The case bank: human-style prompts + expected outcomes for all 27 tools.

Grouped by side-effect level, because that is what decides how a case may be
run and what it leaves behind:

    L0  read-only, never touches Origin            (pure parsing / preset metadata)
    L1  read-only, goes through COM                (status / inspect / view)
    L2  mutates the Origin session, writes nothing to disk
    L3  writes files to disk                       (incl. the composite figure / chart_render)
    L4  process level or destructive               (exit / reclaim / raw LabTalk)

Each case is a prompt a real person might type plus the assertion the runner
checks. `{name}` in args resolves from a context the runner fills in as it
goes: datasets from cases/data.py, plus handles that earlier cases `save`.

`.bin` workstation samples are out of scope for this bank on purpose.
"""

import math

CASES = []


def case(level, tool, prompt, args=None, ok=True, code=None, has=(), eq=(), save=None, note="",
         min=(), msg=()):
    CASES.append({"level": level, "tool": tool, "prompt": prompt, "args": dict(args or {}),
                  "ok": ok, "code": code, "has": list(has), "eq": list(eq),
                  "save": dict(save or {}), "note": note, "min": list(min), "msg": list(msg)})
    return CASES[-1]


# ------------------------------------------------------------------ L0 read-only
# origin_read_file: pure parsing, the widest surface of "dirty real-world files".
for key, prompt, has in (
    ("dat", "帮我看看这个 .dat 是什么分隔符、几行数据", ["columns", "rows", "delimiter"]),
    ("units", "这个文件表头有三行，长名和单位分开写着，能认出列名吗", ["columns", "header_rows"]),
    ("csv", "读一下这个 csv，别导入，只告诉我列情况", ["columns", "delimiter"]),
    ("semi", "这堆 .txt 是分号隔开的，你看看能不能自动认出来", ["delimiter", "rows"]),
    ("spaced", "这个文件是用空格对齐的，不是 tab，能解析吗", ["columns", "delimiter"]),
    ("multi", "表头占了好几行，第一行还有个 run 标记，你看它认不认得出列名", ["columns", "header_rows"]),
    ("na", "这列里有 NA 和减号表示缺值，解析出来多少个有效点", ["rows", "warnings"]),
    ("comment", "开头有 # 注释行，读的时候会不会当成数据", ["rows", "warnings"]),
    ("gbk", "这个文件是中文单位、GBK 编码的，你读读看会不会乱码", ["encoding", "columns"]),
    ("bom", "文件开头有 BOM，列名会不会带个怪字符", ["columns", "encoding"]),
    ("dup", "有两列重名，解析结果里怎么区分", ["columns"]),
    ("huge", "这个文件行数比较多，读一下大概多少行", ["rows"]),
):
    case("L0", "origin_read_file", prompt, {"path": "{%s}" % key}, has=has)

case("L0", "origin_read_file", "只有一列数据的文件它能读吗", {"path": "{onecol}"}, ok=False,
     msg=["一列"], note="要说清是只有一列，不能推给『识别不出分隔符』让人去查编码")

case("L0", "origin_read_file", "把 sample.dat 的列信息给我", {"path": "{dat}", "comment": "run"},
     has=["columns"])
case("L0", "origin_read_file", "这个路径下的文件存在吗：C:/nope/not_here.dat", {"path": "C:/nope/not_here.dat"},
     ok=False, note="文件不存在必须报错，不能静默返回空表")
case("L0", "origin_read_file", "我给了个文件夹路径，你看看它怎么处理", {"path": "{dir}"}, ok=False)
case("L0", "origin_read_file", "空文件会返回什么？别崩就行", {"path": "{empty}"}, ok=False)
case("L0", "origin_read_file", "这文件里一个数字都没有", {"path": "{text}"}, ok=False)

# 仪器导出：前面几十行参数说明，真正的表头紧贴数据上方，单位写在名字里 "Potential/V"。
case("L0", "origin_read_file", "这是工作站导出的 txt，开头一大段参数，能找到真正的表头吗",
     {"path": "{chi}"}, eq=[("rows", 40), ("delimiter", ","),
                            ("columns.0.name", "Potential"), ("columns.0.units", "V"),
                            ("columns.1.name", "Current"), ("columns.1.units", "A")],
     note="不能把第一行的日期当成列名")
case("L0", "origin_read_file", "那个工作站 txt 的扫描速率和仪器型号是多少",
     {"path": "{chi}"}, has=["metadata"],
     eq=[("metadata.Instrument Model", "CHI660E")])

# Preset metadata: 40 scientific chart presets, pure Python.
case("L0", "origin_chart_presets", "支持哪些科研图表预设？全列出来", has=["templates", "n_templates"])
for cat in ("spectroscopy", "electrochemistry", "thermal", "distribution",
            "categorical", "relationship", "medical", "3d"):
    case("L0", "origin_chart_presets", "%s 类有哪些预设" % cat, {"category": cat},
         min=[("templates", 1)])
case("L0", "origin_chart_presets", "有机器学习类的预设吗", {"category": "does_not_exist"},
     ok=False, code="bad_category", note="不存在的分类要报错并列出可用分类，不能返回空列表当成功")
case("L0", "origin_chart_presets", "分类名我可能写错，带空格的行不行", {"category": " spectroscopy "},
     min=[("templates", 1)], note="分类应容忍首尾空格，不能静默返回 0 个")

for name, ask in (
    ("xps", "XPS 那个预设要哪些列"),
    ("xrd", "XRD 预设的默认轴标题是什么"),
    ("cv", "循环伏安预设建议用什么拟合"),
    ("eis", "EIS 预设画的是什么图"),
    ("uv_vis", "UV-Vis 预设默认图类型"),
    ("tga_dtg", "TGA-DTG 预设要几列"),
    ("kaplan_meier", "生存分析那个预设的说明给我看看"),
    ("bland_altman", "Bland-Altman 预设需要哪些列"),
    ("roc_curve", "ROC 预设是画啥的"),
    ("histogram", "直方图预设的默认参数"),
):
    case("L0", "origin_chart_preset", ask, {"name": name},
         has=["required_columns", "default_axis_titles", "default_plot_kind"])
case("L0", "origin_chart_preset", "有个叫 not_a_preset 的预设吗", {"name": "not_a_preset"}, ok=False)

for name, cols, complete in (
    ("xps", ["binding_energy", "intensity"], True),
    ("xps", ["binding_energy"], False),
    ("cv", ["potential", "current"], True),
    ("cv", ["voltage", "i"], False),
    ("uv_vis", ["wavelength", "absorbance"], True),
    ("tga", ["temperature", "mass_percent"], True),
    ("dsc", ["temperature", "heat_flow"], True),
    ("eis", ["z_real", "z_imag"], True),
    ("xrd", ["two_theta", "intensity"], True),
    ("ftir", ["wavenumber", "intensity"], True),
):
    # chart_check is a dry-run diagnostic: it reports missing columns as a
    # warning rather than failing, so the assertion is on `status`, not on `ok`.
    case("L0", "origin_chart_check",
         "我数据里有 %s 这些列，够不够用 %s 预设" % ("/".join(cols), name),
         {"name": name, "columns": cols},
         eq=[("status", "ok" if complete else "warning")],
         note="" if complete else "缺列要写进 issues，不能报成没问题")
case("L0", "origin_chart_check", "列信息我给成字典形式的吧",
     {"name": "xps", "data": {"binding_energy": [1, 2], "intensity": [3, 4]}},
     eq=[("status", "ok")], note="data 传字典应当按列名吃下，不能抛 TypeError")
case("L0", "origin_chart_check", "data 传成列名列表",
     {"name": "xps", "data": ["binding_energy", "intensity"]}, eq=[("status", "ok")])
case("L0", "origin_chart_check", "data 给个真实文件路径",
     {"name": "xps", "data": "{units}"}, has=["columns_detected"])
case("L0", "origin_chart_check", "data 给个不存在的路径",
     {"name": "xps", "data": "C:/nope.dat"}, ok=False)
case("L0", "origin_chart_check", "data 给个数字",
     {"name": "xps", "data": 42}, ok=False, code="bad_arguments")
case("L0", "origin_chart_check", "啥列都没给，它怎么说",
     {"name": "xps", "columns": []}, eq=[("status", "warning")],
     note="空列表要报出所有缺列")
case("L0", "origin_chart_check", "预设名不存在时检查会咋样",
     {"name": "nope_not_here", "columns": ["a"]}, ok=False)

# ------------------------------------------------------------------ L1 read-only
# These go through COM but change nothing, so they are the safe bulk.
for ask in ("现在连上 origin 了吗", "origin 是什么版本", "状态看一下",
            "现在项目里开了哪些页面", "python 是哪个解释器，路径给我",
            "确认一下 origin 活着", "连接正常吗？我刚才崩了一次",
            "看看有没有卡在弹窗上", "origin 装在哪", "再说一次现在的状态"):
    case("L1", "origin_status", ask, has=["connected", "open_pages"])

# Bootstrap handles the rest of the bank depends on.
case("L2", "origin_import", "把这条曲线导进来", {"path": "{dat}"},
     has=["worksheet", "rows", "columns"], save={"ws": "worksheet"})
case("L2", "origin_import", "这份也导进来，多列的", {"path": "{wide}"},
     has=["worksheet"], save={"wsw": "worksheet"})
case("L2", "origin_write", "我直接给你数据，你建成表：t 和 v 两列",
     {"columns": "{columns}"}, has=["worksheet"], save={"wsi": "worksheet"})

for ask in ("现在有几张图？都列出来", "图里有几个层、几条曲线", "看看那个图页的情况",
            "工作簿里都有啥", "我刚导入的表现在还在吗", "列一下所有页面",
            "这张图的曲线数对吗", "帮我确认图层索引", "句柄都还有效吗",
            "图页名字是什么", "再查一次页面列表", "inspect 一下这张图"):
    case("L1", "origin_inspect", ask, has=["pages", "handles"])
case("L1", "origin_inspect", "看看这张图的细节", {"graph": "{gr}"}, has=["pages"])
case("L1", "origin_inspect", "句柄 gr-999 还在吗", {"graph": "gr-999"}, ok=False,
     note="坏句柄要报错，不能回落到活动页")

for ask in ("这张图现在长啥样，截给我看", "图渲染出来我看看", "缩小一点再截图",
            "原图给我看一眼", "看看成图效果", "把这张图画布缩到 400 宽",
            "确认图例有没有挡住曲线", "再截一次", "这个图有符号吗", "颜色对不对，看图"):
    case("L1", "origin_view", ask, {"graph": "{gr}", "width": 320}, has=["ok"])
case("L1", "origin_view", "看一张不存在的图", {"graph": "gr-9999"}, ok=False)

# ------------------------------------------------------------------ L2 in-memory
IMPORT_FILES = ["dat", "units", "csv", "semi", "spaced", "multi", "na", "comment",
                "gbk", "bom", "dup", "wide", "fit", "onecol", "chi"]
for key in IMPORT_FILES:
    case("L2", "origin_import", "导入 %s 这个文件" % key, {"path": "{%s}" % key},
         has=["worksheet", "rows"])
case("L2", "origin_import", "导入并把工作簿改名叫 BOOK_A", {"path": "{dat}", "book_name": "BOOK_A"},
     has=["name", "worksheet"])
case("L2", "origin_import", "路径打错了：C:\\temp\\curve22.dat", {"path": "C:\\temp\\curve22.dat"}, ok=False)
case("L2", "origin_import", "导入一个空文件", {"path": "{empty}"}, ok=False)
case("L2", "origin_import", "导入一个只有文字没有数字的文件", {"path": "{text}"},
     has=["warnings"], min=[("warnings", 1)],
     note="Origin 会把散文当成 1 行文本收进来，必须给出警告而不是报成功")

case("L2", "origin_write", "内联建表，两列各 5 个点",
     {"columns": {"x": [1, 2, 3, 4, 5], "y": [2, 4, 6, 8, 10]}}, has=["worksheet", "rows"])
case("L2", "origin_write", "宽表：表头 x/y1/y2，三行数据",
     {"headers": ["x", "y1", "y2"], "rows": [[1, 2, 3], [4, 5, 6], [7, 8, 9]]},
     has=["worksheet", "cols"])
case("L2", "origin_write", "用我这份 csv 的内容建表", {"columns": "{wide_columns}"}, has=["worksheet"])
case("L2", "origin_write", "表名就叫 WIDE 吧", {"columns": "{wide_columns}", "book_name": "WIDE"},
     has=["name"])
case("L2", "origin_write", "两列长度不一样，一个 3 一个 2",
     {"columns": {"x": [1, 2, 3], "y": [1, 2]}}, ok=False, note="长度不齐必须拒收")
case("L2", "origin_write", "列里混了文字", {"columns": {"x": [1, "abc", 3], "y": [1, 2, 3]}}, ok=False)
case("L2", "origin_write", "啥都不给就建表", {"columns": {}}, ok=False)
case("L2", "origin_write", "只有表头没有数据行", {"headers": ["a", "b"], "rows": []}, ok=False)
case("L2", "origin_write", "行里列数比表头多", {"headers": ["a"], "rows": [[1, 2], [3, 4]]}, ok=False)
case("L2", "origin_write", "数字给成字符串能容忍吗", {"columns": {"x": ["1", "2"], "y": ["3", "4"]}},
     note="数值字符串应当可以转，转不了要明确报错")
case("L2", "origin_write", "负数和科学计数法", {"columns": {"x": [-1.5, 2e-3], "y": [1e6, -3.25]}},
     has=["rows"])

for pt in ("line", "scatter", "line_symbol", "column"):
    case("L2", "origin_plot", "用 %s 方式画这张表" % pt, {"worksheet": "{ws}", "x": 1, "y": 2,
                                                        "plot_type": pt}, has=["graph"])
case("L2", "origin_plot", "把第一条曲线画出来", {"worksheet": "{ws}", "x": 1, "y": 2},
     has=["graph", "graph_page", "proof_level"], save={"gr": "graph"})
case("L2", "origin_plot", "四条 y 一起画到一张图上",
     {"worksheet": "{wsw}", "x": 1, "y": [2, 3, 4, 5]}, has=["plots_in_layer"])
case("L2", "origin_plot", "用 linesymb 模板画", {"worksheet": "{ws}", "x": 1, "y": 2,
                                                "template": "linesymb"}, has=["graph", "template"])
case("L2", "origin_plot", "图页名字叫 MYGRAPH", {"worksheet": "{ws}", "x": 1, "y": 2,
                                                "graph_name": "MYGRAPH"}, has=["graph_page"])
case("L2", "origin_plot", "标题写「充放电曲线」", {"worksheet": "{ws}", "x": 1, "y": 2,
                                                 "title": "充放电曲线"}, has=["graph"])
case("L2", "origin_plot", "画到已有那张图的第 1 层", {"worksheet": "{ws}", "x": 1, "y": 2,
                                                    "graph": "{gr}", "layer": 1}, ok=False,
     note="第 1 层还不存在，应当明确拒绝而不是静默画到别处")
case("L2", "origin_plot", "句柄瞎写一个 ws-42", {"worksheet": "ws-42", "x": 1, "y": 2},
     ok=False, code="worksheet_not_found")
case("L2", "origin_plot", "不给 y 列", {"worksheet": "{ws}", "x": 1}, has=["graph"],
     note="y 有默认值 2，省略是合法的")
case("L2", "origin_plot", "y 给成空列表", {"worksheet": "{ws}", "x": 1, "y": []}, ok=False)
case("L2", "origin_plot", "模板名我乱写的", {"worksheet": "{ws}", "x": 1, "y": 2,
                                            "template": "not_a_template"},
     ok=False, code="bad_template")
case("L2", "origin_plot", "画完再画一次同样的，看看会不会重复", {"worksheet": "{ws}", "x": 1, "y": 2},
     has=["graph"])
case("L2", "origin_plot", "x 列号写成 0", {"worksheet": "{ws}", "x": 0, "y": 2},
     ok=False, code="bad_column", note="0 会被当成第 1 列，静默错位")
case("L2", "origin_plot", "y 列号写成 0", {"worksheet": "{ws}", "x": 1, "y": [0]},
     ok=False, code="bad_column")

for lt in ("right", "top", "bottom"):
    case("L2", "origin_layer", "加一个 %s 层" % lt, {"graph": "{gr}", "layer_type": lt},
         has=["layer_index", "layers_after"])
case("L2", "origin_layer", "再加一个右 Y 轴", {"graph": "{gr}", "layer_type": "right"}, has=["ok"])
case("L2", "origin_layer", "层类型写错成 triple", {"graph": "{gr}", "layer_type": "triple"}, ok=False)
case("L2", "origin_layer", "在不存在的图上加层", {"graph": "gr-9999", "layer_type": "right"}, ok=False)
case("L2", "origin_layer", "双 Y 轴常用，加一个", {"graph": "{gr}", "layer_type": "right"},
     has=["layer_type_name"])
case("L2", "origin_layer", "加层后确认层数变了", {"graph": "{gr}", "layer_type": "top"},
     eq=[("layer_type_name", "TopX")])
case("L2", "origin_layer", "再来一个 bottom", {"graph": "{gr}", "layer_type": "bottom"}, has=["ok"])
case("L2", "origin_layer", "不指定层类型", {"graph": "{gr}"}, has=["layer_type_name"],
     note="默认 right，Origin 那边叫 RightY")
case("L2", "origin_layer", "图句柄给空字符串", {"graph": "", "layer_type": "right"},
     has=["layer_index"], note="空句柄回落到当前活动图页，不能崩")

STYLE_CASES = [
    ("横轴标题写 Time (s)", {"x_title": "Time (s)"}),
    ("纵轴标题 Signal (mV)", {"y_title": "Signal (mV)"}),
    ("两个轴标题一起写", {"x_title": "t / s", "y_title": "I / mA"}),
    ("横轴改对数", {"x_scale": "log"}),
    ("纵轴改对数", {"y_scale": "log"}),
    ("X 轴范围设成 0 到 25", {"xlim": [0, 25]}),
    ("Y 轴范围 0 到 100", {"ylim": [0, 100]}),
    ("横轴主刻度步长 5", {"xtick": 5}),
    ("纵轴刻度步长 20", {"ytick": 20}),
    ("第一条曲线改红色", {"series": 0, "color": "red"}),
    ("线宽加到 3", {"series": 0, "line_width": 3.0}),
    ("符号换成三角", {"series": 0, "symbol_kind": 3}),
    ("符号放大到 12", {"series": 0, "symbol_size": 12.0}),
    ("符号空心", {"series": 0, "symbol_interior": 1}),
    ("透明度 40", {"series": 0, "transparency": 40.0}),
    ("把曲线下方填充", {"series": 0, "fill_area": 1}),
    ("颜色用十六进制", {"series": 0, "color": "#2E86AB"}),
    ("轴标题给空字符串清空", {"x_title": ""}),
    ("范围反着写 25 到 0", {"xlim": [25, 0]}),
    ("范围只给一个数", {"xlim": [5]}),
    ("刻度给 0 表示自动", {"xtick": 0}),
    ("颜色名写错成 bluuu", {"series": 0, "color": "bluuu"}),
    ("同时改标题和线宽", {"x_title": "E / V", "series": 0, "line_width": 2.0}),
    ("整层改样式不指定 series", {"layer": 0, "x_title": "Layer title"}),
]
for ask, args in STYLE_CASES:
    case("L2", "origin_style", ask, dict({"graph": "{gr}"}, **args), has=["applied", "failed"],
         note="失败项要进 failed 列表，不能表面成功")
case("L2", "origin_style", "两条曲线的图，把第二条改蓝色",
     {"graph": "{gr2}", "series": 1, "color": "blue"}, has=["applied", "failed"])
case("L2", "origin_style", "改第 9 条曲线（这张图只有两条）",
     {"graph": "{gr2}", "series": 9, "color": "red"}, ok=False, code="bad_series")
case("L2", "origin_style", "透明度 40 用在第二条曲线",
     {"graph": "{gr2}", "series": 1, "transparency": 40.0}, has=["applied", "failed"])
case("L2", "origin_style", "改一张不存在的图", {"graph": "gr-9999", "x_title": "x"}, ok=False)
case("L2", "origin_style", "图句柄不给", {"x_title": "x"}, ok=False)

# Positions come from the engine's own table so the bank cannot drift from it.
import originlab as _ol

for pos in sorted(_ol.LEGEND_POSITIONS):
    case("L2", "origin_legend", "图例放 %s" % pos, {"graph": "{gr}", "position": pos},
         min=[("applied", 1)])
for bogus in ("corner", "middle_right", "outside", "RIGHT-IN", "none"):
    case("L2", "origin_legend", "图例位置写成 %s" % bogus, {"graph": "{gr}", "position": bogus},
         ok=False, code="bad_position")
case("L2", "origin_legend", "把图例关掉", {"graph": "{gr}", "show": False},
     eq=[("proof_level", "verified")], note="这条决定预览图有没有图例")
case("L2", "origin_legend", "重新显示图例", {"graph": "{gr}", "show": True}, has=["applied"])
case("L2", "origin_legend", "图例文字我指定：['样品', '拟合']",
     {"graph": "{gr}", "entries": ["样品", "拟合"]}, has=["text"])
case("L2", "origin_legend", "位置写个不存在的 corner", {"graph": "{gr}", "position": "corner"}, ok=False,
     code="bad_position")
case("L2", "origin_legend", "图例自动按列名生成", {"graph": "{gr}"}, has=["applied"])

case("L2", "origin_annotate", "在 (5, 20) 标一个「起始」",
     {"graph": "{gr}", "labels": [{"text": "起始", "x": 5, "y": 20}]}, has=["items"])
case("L2", "origin_annotate", "加一条 x=12 的竖参考线",
     {"graph": "{gr}", "lines": [{"x": 12}]}, has=["items"])
case("L2", "origin_annotate", "加一条 y=50 的横线", {"graph": "{gr}", "lines": [{"y": 50}]}, has=["items"])
case("L2", "origin_annotate", "同时加三个标注",
     {"graph": "{gr}", "labels": [{"text": "a", "x": 2, "y": 5}, {"text": "b", "x": 8, "y": 20},
                                  {"text": "c", "x": 18, "y": 60}]}, has=["items"])
case("L2", "origin_annotate", "标注文字用中文带单位",
     {"graph": "{gr}", "labels": [{"text": "峰值 82 mV", "x": 22, "y": 82}]}, has=["items"])
case("L2", "origin_annotate", "标注和线一起给",
     {"graph": "{gr}", "labels": [{"text": "阈值", "x": 10, "y": 30}], "lines": [{"x": 10}]},
     has=["items"])
case("L2", "origin_annotate", "坐标超出轴范围会怎样",
     {"graph": "{gr}", "labels": [{"text": "out", "x": 999, "y": 999}]},
     note="允许放但应能读回；不能静默丢")
case("L2", "origin_annotate", "labels 给成字符串不是列表",
     {"graph": "{gr}", "labels": "text"}, ok=False)
case("L2", "origin_annotate", "空列表", {"graph": "{gr}", "labels": []}, ok=False,
     code="bad_arguments")
case("L2", "origin_annotate", "lines 给成字符串列表", {"graph": "{gr}", "lines": ["测试"]},
     ok=False, code="bad_arguments")
case("L2", "origin_annotate", "lines 里啥坐标都不给", {"graph": "{gr}", "lines": [{"note": "x"}]},
     ok=False, code="bad_arguments")
case("L2", "origin_annotate", "在不存在的图上标注", {"graph": "gr-9999", "labels": [{"text": "x", "x": 1, "y": 1}]},
     ok=False)
case("L2", "origin_annotate", "标注在第 1 层", {"graph": "{gr}", "layer": 1,
                                              "labels": [{"text": "L1", "x": 3, "y": 3}]})
case("L2", "origin_annotate", "什么都不给", {"graph": "{gr}"}, ok=False)

for tpl in ("heat_map", "cmap", "mesh", "contline", "contgray", "3d", "bar3d"):
    case("L2", "origin_matrix", "把这个矩阵画成 %s" % tpl,
         {"rows": "{matrix_rows}", "template": tpl}, has=["graph", "shape"])
case("L2", "origin_matrix", "只建矩阵不画图", {"rows": "{matrix_rows}"}, has=["matrix_book"])
case("L2", "origin_matrix", "矩阵书写成 MAT1", {"rows": "{matrix_rows}", "book_name": "MAT1"},
     has=["matrix_book"])
case("L2", "origin_matrix", "行列不齐的矩阵（第一行 3 个第二行 2 个）",
     {"rows": [[1, 2, 3], [4, 5]]}, ok=False, note="ragged 输入必须拒收")
case("L2", "origin_matrix", "空矩阵", {"rows": []}, ok=False)
case("L2", "origin_matrix", "单行矩阵", {"rows": [[1, 2, 3]]}, has=["shape"])
case("L2", "origin_matrix", "含负值和零", {"rows": [[-1, 0], [0, 1]], "template": "cmap"}, has=["ok"])
case("L2", "origin_matrix", "指定 xy 范围 [0, 10, 0, 5]",
     {"rows": "{matrix_rows}", "xy_range": [0, 10, 0, 5]}, has=["xlim", "ylim"])
case("L2", "origin_matrix", "模板名乱写", {"rows": "{matrix_rows}", "template": "nope"}, ok=False)
case("L2", "origin_matrix", "矩阵标题写「阻抗分布」", {"rows": "{matrix_rows}", "title": "阻抗分布"},
     has=["ok"])

for kind in ("linear", "poly3", "expdecay", "gauss", "lorentz", "logistic", "boltzmann",
             "sine", "power", "cubic"):
    case("L2", "origin_fit", "用 %s 拟合一下" % kind,
         {"worksheet": "{wsf}", "x": 1, "y": 2, "kind": kind}, has=["fit"])
case("L2", "origin_fit", "线性拟合，把参数报告也生成",
     {"worksheet": "{wsf}", "x": 1, "y": 2, "kind": "linear", "make_report": True}, has=["fit"])
case("L2", "origin_fit", "要完整的拟合结果不要裁剪",
     {"worksheet": "{wsf}", "x": 1, "y": 2, "kind": "linear", "full": True}, has=["result"],
     note="full=True 时整份报告放在 result 里，不再是裁剪过的 fit")
case("L2", "origin_fit", "自定义函数 y=a*exp(-b*x)+c",
     {"worksheet": "{wsf}", "x": 1, "y": 2, "func": "a*exp(-b*x)+c",
      "starts": {"a": 1, "b": 0.1, "c": 0}}, ok=False, code="fit_function_unavailable",
     note="NLFit 只认 Origin 里已定义的函数名，表达式不支持，必须明确拒绝")
case("L2", "origin_fit", "内置函数 ExpDec1 不给 kind",
     {"worksheet": "{wsf}", "x": 1, "y": 2, "func": "ExpDec1"},
     has=["fit", "note"], eq=[("kind", "nlfitsing")])
case("L2", "origin_fit", "固定 y0=0 只拟合 A1 和 t1",
     {"worksheet": "{wsf}", "x": 1, "y": 2, "func": "ExpDec1", "fixed": {"y0": 0}},
     has=["fit", "constraints"])
case("L2", "origin_fit", "给参数加上下界",
     {"worksheet": "{wsf}", "x": 1, "y": 2, "func": "ExpDec1", "bounds": {"A1": [0, 100]}},
     has=["constraints"])
case("L2", "origin_fit", "kind 写 linear 但给了 func",
     {"worksheet": "{wsf}", "x": 1, "y": 2, "kind": "linear", "func": "Gauss"},
     has=["fit", "note"], eq=[("kind", "nlfitsing")])
case("L2", "origin_fit", "拟合表里第 3 列", {"worksheet": "{wsw}", "x": 1, "y": 3}, has=["fit"])
case("L2", "origin_fit", "点太少能不能拟合", {"worksheet": "{wsf}", "x": 1, "y": 2}, has=["fit"])
case("L2", "origin_fit", "函数名不存在的拟合", {"worksheet": "{wsf}", "x": 1, "y": 2,
                                              "func": "wat*wat"}, ok=False,
     code="fit_function_unavailable")
case("L2", "origin_fit", "拟合不存在的表", {"worksheet": "ws-9999", "x": 1, "y": 2}, ok=False)
case("L2", "origin_fit", "列号给成 0", {"worksheet": "{wsf}", "x": 0, "y": 2},
     ok=False, code="bad_column")
case("L2", "origin_fit", "kind 和 func 都不给", {"worksheet": "{wsf}", "x": 1, "y": 2},
     note="应当走默认或明确报错，不能崩")

case("L2", "origin_formula", "加一列算 y 的两倍", {"worksheet": "{ws}", "col": 3,
                                                 "formula": "col(2)*2"}, has=["computed_rows"])
case("L2", "origin_formula", "派生列名叫 ratio", {"worksheet": "{ws}", "col": 3, "formula": "col(2)/10",
                                                "label": "ratio"}, has=["columns"])
case("L2", "origin_formula", "给派生列加单位 mA", {"worksheet": "{ws}", "col": 3, "formula": "col(2)",
                                                "units": "mA"}, has=["ok"])
case("L2", "origin_formula", "用 i 索引做线性序列", {"worksheet": "{ws}", "col": 3,
                                                  "formula": "i*0.5"}, has=["last"])
case("L2", "origin_formula", "写个 sin 公式", {"worksheet": "{ws}", "col": 3,
                                             "formula": "sin(col(1))"}, has=["first", "last"])
case("L2", "origin_formula", "公式引用不存在的列", {"worksheet": "{ws}", "col": 7,
                                                 "formula": "col(99)"},
     ok=False, code="formula_no_effect")
case("L2", "origin_formula", "公式是乱码中文", {"worksheet": "{ws}", "col": 8, "formula": "算一下"},
     ok=False, code="formula_no_effect")
case("L2", "origin_formula", "不给公式", {"worksheet": "{ws}", "col": 3}, ok=False)
case("L2", "origin_formula", "在不存在的表上加列", {"worksheet": "ws-9999", "col": 3,
                                                 "formula": "col(2)"}, ok=False)
case("L2", "origin_formula", "覆盖已有的第 2 列", {"worksheet": "{ws}", "col": 2,
                                                "formula": "col(2)+1"}, has=["computed_rows"])
case("L2", "origin_formula", "再算一次同样的公式（幂等）", {"worksheet": "{ws}", "col": 3,
                                                       "formula": "col(2)*2"}, has=["ok"])
case("L2", "origin_formula", "列号给 0", {"worksheet": "{ws}", "col": 0, "formula": "col(2)"},
     ok=False, code="bad_column", note="0 不能落到第 1 列上，会覆盖原始数据")
case("L2", "origin_formula", "列号给 -1", {"worksheet": "{ws}", "col": -1, "formula": "col(2)"},
     ok=False, code="bad_column")

for ask, args, want_ok in (
        ("关掉所有图页", {"kind": "graph"}, True),
        ("把图页都清了", {"kind": "graph", "dry_run": True}, True),
        ("干跑一下看看会关哪些", {"dry_run": True}, True),
        ("工作表也清一下", {"kind": "workbook", "dry_run": True}, True),
        ("矩阵页清掉", {"kind": "matrix", "dry_run": True}, True),
        ("只关指定名字的页", {"names": ["Graph1"], "dry_run": True}, True),
        ("关一个不存在的页名", {"names": ["NOPE"], "dry_run": True}, True),
        ("页面类型写错", {"kind": "nonsense"}, False),
        ("全清（不分类型）", {"dry_run": True}, True),
        ("实际关闭图页", {"kind": "graph"}, True),
        ("关完再确认还剩什么", {"dry_run": True}, True),
        ("再清一次（应该没东西可关了）", {"kind": "graph"}, True),
        ("names 给空列表", {"names": [], "dry_run": True}, True),
        ("重复关闭同一批页", {"names": ["Graph1"]}, True)):
    case("L2", "origin_close_pages", ask, args, ok=want_ok, has=["ok"])

case("L2", "origin_labtalk", "读一下当前活动层的 showlegend",
     {"script": "layer.showlegend = layer.showlegend;", "numeric": ["layer.showlegend"]},
     has=["numeric"], note="整数型 LabTalk 变量必须能读回，不能是 nan/None")
case("L2", "origin_labtalk", "把图例显示打开", {"script": "layer.showlegend = 1;",
                                              "numeric": ["layer.showlegend"]}, has=["numeric"])
case("L2", "origin_labtalk", "读一个字符串变量", {"script": "str1$ = \"abc\";", "readback": ["str1$"]},
     has=["readback"])
case("L2", "origin_labtalk", "跑个无害的 ls 看看返回", {"script": "doc -a;"}, has=["ok"])
case("L2", "origin_labtalk", "空脚本", {"script": ""}, ok=False)
case("L2", "origin_labtalk", "只有空格", {"script": "    "}, ok=False)
for bad, why in (("doc -s;", "关工程"), ("close;", "关页"), ("quit;", "退 Origin"),
                 ("system dir;", "起外部进程"), ("run.section;", "跑脚本块")):
    case("L2", "origin_labtalk", "执行 %s" % bad, {"script": bad}, ok=False,
         code="labtalk_denied", note="破坏性命令必须被黑名单拦下：%s" % why)
case("L2", "origin_labtalk", "把危险命令写成大写带多余空格", {"script": "DOC   -s  ;"}, ok=False,
     code="labtalk_denied", note="黑名单要折叠空白并忽略大小写")
case("L2", "origin_labtalk", "读一个不存在的变量", {"script": "xx = 1;", "numeric": ["nosuchvar"]},
     has=["ok"])
case("L2", "origin_labtalk", "脚本里带中文注释", {"script": "// 中文注释\nyy = 2;",
                                               "numeric": ["yy"]}, has=["numeric"])

# ------------------------------------------------------------------ L3 writes files
for fmt, ext in (("png", "png"), ("tif", "tif"), ("svg", "svg"), ("pdf", "pdf"),
                 ("emf", "emf"), ("jpg", "jpg")):
    case("L3", "origin_export", "导出成 %s" % fmt,
         {"graph": "{gr}", "path": "{dir}/out." + ext, "fmt": fmt}, has=["path", "bytes", "head"])
for w in (400, 800, 1200, 1600, 2400):
    case("L3", "origin_export", "导出 PNG，宽度 %d" % w,
         {"graph": "{gr}", "path": "{dir}/w%d.png" % w, "fmt": "png", "width": w}, has=["dimensions"])
case("L3", "origin_export", "不给路径，让它自己起名", {"graph": "{gr}"}, has=["path"])
case("L3", "origin_export", "导出到不存在的深层目录",
     {"graph": "{gr}", "path": "{dir}/nope/deep/x.png"},
     note="应当自动建目录或明确报错，不能写出半个文件")
case("L3", "origin_export", "格式写成 tiff 两个 f", {"graph": "{gr}", "path": "{dir}/a.tiff",
                                                  "fmt": "tiff"}, has=["ok"])
case("L3", "origin_export", "格式写 docx", {"graph": "{gr}", "fmt": "docx"}, ok=False)
case("L3", "origin_export", "导出不存在的图", {"graph": "gr-9999", "path": "{dir}/x.png"}, ok=False)
case("L3", "origin_export", "同一个文件导出两次（覆盖）",
     {"graph": "{gr}", "path": "{dir}/twice.png", "fmt": "png"}, has=["bytes"])
case("L3", "origin_export", "路径里有中文", {"graph": "{gr}", "path": "{dir}/图表.png", "fmt": "png"},
     note="中文路径历史上不稳，至少要如实报告失败")
case("L3", "origin_export", "宽度给 0", {"graph": "{gr}", "path": "{dir}/z.png", "width": 0},
     note="非法宽度不能静默当成默认值")

for ask in ("保存工程", "存成 opju 我回头继续改", "存到 out 目录", "保存一下当前项目",
            "存盘，文件名 proj1.opju", "再存一次覆盖掉", "存成 .opj 老格式",
            "保存到中文文件名", "保存并确认文件头是 Origin 的", "存盘后还能编辑吗"):
    case("L3", "origin_save_project", ask, {"path": "{dir}/proj.opju"},
         has=["path", "bytes", "head", "editable"])
case("L3", "origin_save_project", "不给路径，存到默认位置", {}, has=["saved"])
case("L3", "origin_save_project", "存到一个不能写的路径", {"path": "C:\\Windows\\nope.opju"}, ok=False)

FIG_ARGS = [
    ("一步出图：导入 sample 然后画图导出", {"source": "{dat}", "x": 1, "y": 2,
                                          "export_path": "{dir}/fig1.png"}),
    ("用内联数据一步出图", {"columns": "{columns}", "x": 1, "y": 2,
                        "export_path": "{dir}/fig2.png"}),
    ("出图带轴标题", {"source": "{dat}", "x": 1, "y": 2, "x_title": "Time (s)",
                   "y_title": "Signal (mV)", "export_path": "{dir}/fig3.png"}),
    ("出图要图例", {"source": "{dat}", "x": 1, "y": 2, "legend": True,
                 "export_path": "{dir}/fig4.png"}),
    ("出图不要图例", {"source": "{dat}", "x": 1, "y": 2, "legend": False,
                   "export_path": "{dir}/fig5.png"}),
    ("用模板出图", {"source": "{dat}", "x": 1, "y": 2, "template": "linesymb",
                 "export_path": "{dir}/fig6.png"}),
    ("四条曲线一次出图", {"source": "{wide}", "x": 1, "y": [2, 3, 4, 5],
                      "export_path": "{dir}/fig7.png"}),
    ("出图同时存工程", {"source": "{dat}", "x": 1, "y": 2, "output_dir": "{dir}"}),
    ("只要工程不要图", {"source": "{dat}", "x": 1, "y": 2, "project_path": "{dir}/only.opju",
                     "do_project": True}),
    ("指定图标题", {"source": "{dat}", "x": 1, "y": 2, "title": "循环曲线",
                 "export_path": "{dir}/fig8.png"}),
    ("导出 svg 格式", {"source": "{dat}", "x": 1, "y": 2, "fmt": "svg",
                    "export_path": "{dir}/fig9.svg"}),
    ("宽度 2000 出图", {"source": "{dat}", "x": 1, "y": 2, "width": 2000,
                     "export_path": "{dir}/fig10.png"}),
    ("工作站 txt 一步出图（前面几十行参数要跳过）",
     {"source": "{chi}", "x": 1, "y": 2, "export_path": "{dir}/chi.png", "title": "LSV"}),
    ("什么都不给", {}, False),
    ("只给 source 不给输出位置", {"source": "{dat}", "x": 1, "y": 2}, True),
    ("source 和 columns 都不给但给了 x/y", {"x": 1, "y": 2}, False),
    ("source 指向不存在的文件", {"source": "C:/nope.dat", "export_path": "{dir}/x.png"}, False),
    ("列号越界", {"source": "{dat}", "x": 1, "y": 9, "export_path": "{dir}/x.png"}, False),
    ("重复出同样的图（覆盖同名文件）", {"source": "{dat}", "x": 1, "y": 2,
                             "export_path": "{dir}/fig1.png"}, True),
]
for entry in FIG_ARGS:
    # Entries may omit the expectation, which means "should succeed".
    case("L3", "origin_figure", entry[0], entry[1],
         ok=entry[2] if len(entry) > 2 else True)

PRESET_DATA = {
    "xps": {"binding_energy": [round(284 + i * 0.4, 2) for i in range(12)],
            "intensity": [round(800 + 60 * math.sin(i / 2.0)) for i in range(12)]},
    "xrd": {"two_theta": [round(20 + i * 1.5, 2) for i in range(12)],
            "intensity": [round(120 + 900 * math.exp(-((i - 5) ** 2) / 3.0)) for i in range(12)]},
    "cv": {"potential": [round(-0.2 + i * 0.04, 3) for i in range(12)],
           "current": [round(1e-4 * math.sin(i / 2.0), 8) for i in range(12)]},
    "uv_vis": {"wavelength": [200 + 10 * i for i in range(12)],
               "absorbance": [round(0.1 + 0.05 * math.sin(i / 3.0), 4) for i in range(12)]},
    "tga": {"temperature": [30 + 25 * i for i in range(12)],
            "mass_percent": [round(100 - 0.8 * i - 1.5 * (i > 6), 3) for i in range(12)]},
    "dsc": {"temperature": [25 + 5 * i for i in range(12)],
            "heat_flow": [round(0.2 * math.exp(-((i - 6) ** 2) / 4.0), 5) for i in range(12)]},
    "histogram": {"value": [round(abs(3.0 * math.sin(i)), 3) for i in range(24)]},
    "bland_altman": {"method1": [round(10 + i * 0.7, 2) for i in range(12)],
                     "method2": [round(10.4 + i * 0.66, 2) for i in range(12)]},
}

for name in ("xps", "xrd", "cv", "uv_vis", "tga", "dsc", "histogram", "bland_altman"):
    case("L3", "origin_chart_render", "用 %s 预设一步出图" % name,
         {"name": name, "columns": PRESET_DATA[name], "output_dir": "{dir}"},
         note="每个预设配自己那套列名，不能拿一份数据喂所有预设")
case("L3", "origin_chart_render", "预设出图带图例",
     {"name": "xps", "columns": "{xps}", "output_dir": "{dir}", "with_legend": True}, has=["ok"])
case("L3", "origin_chart_render", "预设出图不要图例",
     {"name": "xps", "columns": "{xps}", "output_dir": "{dir}", "with_legend": False}, has=["ok"])
case("L3", "origin_chart_render", "自定义轴标题",
     {"name": "xps", "columns": "{xps}", "output_dir": "{dir}",
      "x_title": "BE / eV", "y_title": "Intensity"}, has=["ok"])
case("L3", "origin_chart_render", "从文件出图", {"name": "xps", "source": "{xps_file}",
                                             "output_dir": "{dir}"})
case("L3", "origin_chart_render", "指定 x/y 列号", {"name": "xps", "source": "{xps_file}",
                                                "x": 1, "y": 2, "output_dir": "{dir}"})
case("L3", "origin_chart_render", "三行表头的文件（列名解析不吃 Long Name 块）",
     {"name": "xps", "source": "{units}", "output_dir": "{dir}"}, ok=False,
     code="preset_columns_mismatch",
     note="units.dat 的多行表头被当成数据列名，拒画比硬套预设诚实；这是已知限制")
case("L3", "origin_chart_render", "导出 pdf", {"name": "xps", "columns": "{xps}",
                                            "output_dir": "{dir}", "fmt": "pdf"})
case("L3", "origin_chart_render", "宽度 3000", {"name": "xps", "columns": "{xps}",
                                             "output_dir": "{dir}", "width": 3000})
case("L3", "origin_chart_render", "列不够，只有 binding_energy",
     {"name": "xps", "columns": {"binding_energy": [1, 2, 3]}, "output_dir": "{dir}"}, ok=False,
     note="缺列必须拒绝，不能画半张图")
case("L3", "origin_chart_render", "预设名不存在",
     {"name": "not_a_preset", "columns": "{xps}", "output_dir": "{dir}"}, ok=False)
case("L3", "origin_chart_render", "数据完全对不上（cv 数据喂 xps 预设）",
     {"name": "xps", "columns": "{cv}", "output_dir": "{dir}"}, ok=False)
case("L3", "origin_chart_render", "不给输出目录", {"name": "xps", "columns": "{xps}"},
     note="应当说明没有落盘，或写到明确位置")
case("L3", "origin_chart_render", "图标题自定义", {"name": "xps", "columns": "{xps}",
                                                "output_dir": "{dir}", "title": "Survey"}, has=["ok"])
case("L3", "origin_chart_render", "换个 plot_type", {"name": "xps", "columns": "{xps}",
                                                  "output_dir": "{dir}", "plot_type": "line_symbol"},
     has=["ok"])

# ------------------------------------------------------------------ L4 process level
for ask in ("现在有几个 origin 实例", "有没有没窗口的残留实例", "实例清点一下",
            "后台跑着的 origin 有几个", "看看是不是我起的实例占着端口",
            "任务管理器里那种没窗口的 origin 列出来", "实例数会不会超上限",
            "再清点一次", "有前台窗口的实例有几个", "现在 started_by_us 是谁",
            "探测结果里 probe 字段是什么", "确认没有孤儿实例"):
    case("L4", "origin_instances", ask, has=["count", "probe"],
         eq=[("probe", "ok")])
for ask in ("先看看会回收哪些，别真动手", "预览一下回收", "close_background 传 false",
            "确认预览不会杀进程", "dry run 之后再清点一次"):
    case("L4", "origin_reclaim", ask, {"close_background": False}, has=["closed", "left_running"])
for ask in ("回收残留实例", "把没窗口的实例关掉", "再回收一次（应该没东西了）",
            "回收后确认数量为 0", "重复回收是否幂等", "关掉后台实例再清点",
            "再执行一次回收", "回收完看状态", "回收后确认前台窗口没被误杀"):
    case("L4", "origin_reclaim", ask, has=["probe", "notify"])

case("L4", "origin_exit", "先存一份再关 origin", {"save_to": "{dir}/bye.opju"}, has=["ok"])
case("L4", "origin_status", "关完之后确认连接状态", has=["connected"])
case("L4", "origin_import", "退出之后再导入，看它会不会自动重连", {"path": "{dat}"}, has=["worksheet"])
case("L4", "origin_exit", "直接关，不保存", {}, has=["ok"])
case("L4", "origin_exit", "已经关了再关一次（幂等）", {}, note="重复退出不应崩")
case("L4", "origin_instances", "退出后清点实例", has=["count"])
case("L4", "origin_reclaim", "退出后回收一次残留", has=["probe"])
case("L4", "origin_reclaim", "把桥自己起的后台实例全关掉", has=["closed", "probe"])
case("L4", "origin_status", "实例被关掉后第一次调用要自己接上", has=["connected"],
     note="COM 指针失效时桥必须丢弃缓存重连，不能一路 connection_error")
case("L4", "origin_import", "掉线之后重新导入数据", {"path": "{dat}"}, has=["worksheet", "rows"],
     save={"ws": "worksheet"})
case("L4", "origin_plot", "掉线之后重新画图", {"worksheet": "{ws}", "x": 1, "y": 2},
     has=["graph"], save={"gr": "graph"})
case("L4", "origin_status", "最后确认连接恢复可用", has=["connected"])
case("L4", "origin_exit", "退出并存盘到新名字", {"save_to": "{dir}/final.opju"}, has=["ok"])
case("L4", "origin_open", "把刚存的工程重新打开", {"path": "{dir}/final.opju"}, has=["ok"])
case("L4", "origin_open", "打开不存在的工程", {"path": "C:/nope.opju"}, ok=False)
case("L4", "origin_open", "打开一个 .txt 说它是工程", {"path": "{semi}"}, ok=False)
case("L4", "origin_open", "路径给空", {"path": ""}, ok=False)
case("L4", "origin_open", "再打开一次同一个工程", {"path": "{dir}/final.opju"}, has=["ok"])

for ask, args, want in (
    ("打开 out 里那个 proj.opju", {"path": "{dir}/proj.opju"}, True),
    ("用相对路径打开工程", {"path": "proj.opju"}, False),
    ("打开中文文件名的工程", {"path": "{dir}/中文工程.opju"}, False),
    ("把刚才那个工程再打开一次看看页面数", {"path": "{dir}/final.opju"}, True),
    ("打开一个只有表头的 .dat 当工程", {"path": "{units}"}, False),
    ("路径给成目录", {"path": "{dir}"}, False),
    ("打开后确认能继续导入数据", {"path": "{dir}/final.opju"}, True),
):
    case("L4", "origin_open", ask, args, ok=want)

for ask, args, want in (
    ("退出并存盘到 bye2.opju", {"save_to": "{dir}/bye2.opju"}, True),
    ("退出后马上再退出一次", {}, True),
    ("退出，不保存", {}, True),
    ("退出后看状态", {}, True),
    ("退出并存到中文路径", {"save_to": "{dir}/退出工程.opju"}, True),
    ("第三次退出（幂等检查）", {}, True),
    ("退出后确认还能重新连上干活", {}, True),
):
    case("L4", "origin_exit", ask, args, ok=want, has=["ok"])

case("L4", "origin_status", "全部退出用例跑完，最后确认连接", has=["connected", "open_pages"])
case("L4", "origin_instances", "收尾清点：还有几个实例", has=["count", "probe"])
