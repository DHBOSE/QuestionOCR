# -*- coding: utf-8 -*-
"""回归测试：选项行内拆分 / 填空还原 / 图注配对"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"E:\PICTURE-TO-WORD\Screenshot2QuestionWord\backend")
from converter import normalize_option_lines, restore_fill_blanks, elements_to_markdown
from converter import merge_figure_only_items

fails = []
def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail if not cond else "")
    if not cond:
        fails.append(name)

# 1) 选项全部挤一行 + C 被公式包裹 + A. 后无空格（真实 OCR 形态）
raw = r"A.600 N的力 B. $7 5 0$ N的力 $\mathrm{C . ~} 1 ~ 2 0 0$ N的力 D. $2 \ 0 0 0$ N的力"
out = normalize_option_lines(raw)
print(out)
lines = [l for l in out.split("\n") if l.strip()]
check("选项拆成4行", len(lines) == 4, f"got {len(lines)}: {lines}")
check("A转义", lines[0].startswith(r"A\."))
check("B独立", lines[1].startswith(r"B\."))
check("C解包公式", lines[2].startswith(r"C\.") and "$1 ~ 2 0 0$" in lines[2])
check("D独立", lines[3].startswith(r"D\."))

# 2) 不应误伤：$F_{1}$、正文 "A 点"、独立公式行
safe = r"拉力为 $F_{1}$，$A$ 点应远离 $O$ 点"
out2 = normalize_option_lines(safe)
check("公式未受影响", out2 == safe, out2)

# 3) 幂等：二次处理结果不变
check("幂等", normalize_option_lines(out) == out)

# 4) 填空还原
blank_in = "船桨可看作一个 （填“省力”“费力”或“等臂”)杠杆。$A$ 点应\n（填“靠近”或“远离”）"
blank_out = restore_fill_blanks(blank_in)
print(blank_out)
check("补两个空", blank_out.count(r"\_\_\_\_\_\_（填") == 2, blank_out)
already = "可看作一个 ______（填“省力”）"
check("已有下划线不重复补", restore_fill_blanks(already) == "可看作一个 ______（填“省力”）",
      restore_fill_blanks(already))
check("幂等2", restore_fill_blanks(blank_out) == blank_out)

# 5) 图注配对（本地模式形态：甲是独立小元素，乙缺失，插图顺序颠倒）
els = [
    {"type": "text", "text": "5[2026陕西延安期末]如图甲所示……", "image_path": None, "box": [15, 0, 565, 293]},
    {"type": "image", "text": "", "image_path": r"E:\PICTURE-TO-WORD\多题测试图.png", "box": [286, 299, 460, 455]},
    {"type": "image", "text": "", "image_path": r"E:\PICTURE-TO-WORD\多题测试图.png", "box": [118, 328, 260, 447]},
    {"type": "text", "text": "甲\n", "image_path": None, "box": [172, 452, 211, 498]},
]
md = elements_to_markdown(els, r"E:\PICTURE-TO-WORD")
print(md)
check("甲图注配对", "![甲](" in md)
check("乙图注推断", "![乙](" in md)
check("甲图排在乙图前", md.index("![甲](") < md.index("![乙]("))
check("独立甲行已移除", "\n甲\n" not in md and "甲\n" not in md.split("![")[0].replace("图甲所示", ""))

# 6) 图注在大段文字尾部（API/混合模式形态）
els2 = [
    {"type": "text", "text": "5[2026陕西延安期末]如图甲所示……远离）点一些。\n\n甲\n\n乙", "image_path": None, "box": None},
    {"type": "image", "text": "", "image_path": r"E:\PICTURE-TO-WORD\多题测试图.png", "box": [286, 299, 460, 455]},
    {"type": "image", "text": "", "image_path": r"E:\PICTURE-TO-WORD\多题测试图.png", "box": [118, 328, 260, 447]},
]
md2 = elements_to_markdown(els2, r"E:\PICTURE-TO-WORD")
print(md2)
check("API形态甲乙配对", "![甲](" in md2 and "![乙](" in md2)
check("API形态甲在乙前", md2.index("![甲](") < md2.index("![乙]("))
check("尾部图注行已摘除", "\n甲" not in md2.split("!")[0] and "\n乙" not in md2.split("!")[0])

# 7) 无图注时不乱排（保持原顺序，caption 为空）
els3 = [
    {"type": "text", "text": "题干", "image_path": None, "box": None},
    {"type": "image", "text": "", "image_path": r"E:\PICTURE-TO-WORD\多题测试图.png", "box": [10, 10, 50, 50]},
]
md3 = elements_to_markdown(els3, r"E:\PICTURE-TO-WORD")
check("无图注图片无alt", "![](" in md3, md3)

# 8) 跨题插图重分配：第2题含两张图，右图图注"第3题图"应搬到第3题
from converter import tag_figure_captions, reassign_question_figures
q2_els = [
    {"type": "text", "text": "2[2026陕西渭南期末]如图所示……", "image_path": None, "box": [13, 0, 700, 330]},
    {"type": "image", "text": "", "image_path": "f_r.png", "box": [391, 342, 684, 570]},
    {"type": "image", "text": "", "image_path": "f_l.png", "box": [98, 408, 330, 570]},
    {"type": "text", "text": "(第 $2$ 题图）", "image_path": None, "box": [130, 575, 240, 610]},
    {"type": "text", "text": "（第 $~ 3$ 题图）", "image_path": None, "box": [470, 575, 580, 610]},
]
q3_els = [
    {"type": "text", "text": "3[2025陕西汉中期末]如图是……", "image_path": None, "box": [13, 0, 700, 300]},
]
tag_figure_captions(q2_els)
tag_figure_captions(q3_els)
left = next(e for e in q2_els if e.get("image_path") == "f_l.png")
right_moved = next((e for e in q2_els if e.get("image_path") == "f_r.png"), None)
check("左图配注第2题图", left.get("caption") == "第2题图", str(left.get("caption")))
check("右图配注第3题图", right_moved and right_moved.get("caption") == "第3题图",
      str(right_moved and right_moved.get("caption")))
check("图注文字已移除", not any(e.get("text", "").find("题图") >= 0 for e in q2_els))
reassign_question_figures([q2_els, q3_els], [2, 3])
check("右图已搬到第3题", any(e.get("image_path") == "f_r.png" for e in q3_els)
      and not any(e.get("image_path") == "f_r.png" for e in q2_els))
check("左图留在第2题", any(e.get("image_path") == "f_l.png" for e in q2_els))

# 9) 题号对不上时插图原地保留
q5_els = [{"type": "image", "text": "", "image_path": "x.png", "box": [0, 0, 10, 10], "caption": "第9题图"}]
reassign_question_figures([q5_els], [5])
check("对不上题号原地保留", len(q5_els) == 1)

# 10) 混合模式形态：图注在文字大段尾部（无坐标），按阅读顺序配对后再搬移
q2_blob = [
    {"type": "text", "text": "2[2026陕西渭南期末]如图所示……正确的是（ ）\nA. F1 最小\nB. F2 最小\n\n（第 2 题图）\n\n（第 3 题图）", "image_path": None, "box": None},
    {"type": "image", "text": "", "image_path": "f_r.png", "box": [391, 342, 684, 570]},
    {"type": "image", "text": "", "image_path": "f_l.png", "box": [98, 408, 330, 570]},
]
q3_blob = [{"type": "text", "text": "3[2025陕西汉中期末]……", "image_path": None, "box": None}]
tag_figure_captions(q2_blob)
tag_figure_captions(q3_blob)
l2 = next(e for e in q2_blob if e.get("image_path") == "f_l.png")
r2 = next(e for e in q2_blob if e.get("image_path") == "f_r.png")
check("尾部图注左图配第2题图", l2.get("caption") == "第2题图", str(l2.get("caption")))
check("尾部图注右图配第3题图", r2.get("caption") == "第3题图", str(r2.get("caption")))
check("尾部图注从大段摘除", "题图" not in q2_blob[0]["text"])
reassign_question_figures([q2_blob, q3_blob], [2, 3])
check("混合形态右图搬到第3题", any(e.get("image_path") == "f_r.png" for e in q3_blob)
      and not any(e.get("image_path") == "f_r.png" for e in q2_blob))

# 11) 纯图项并入上一题（PDF 换页把插图切到下一页的场景）
_text = lambda t: {"type": "text", "text": t, "image_path": None, "box": None}
_img = lambda p: {"type": "image", "text": "", "image_path": p, "box": [0, 0, 10, 10]}
items = [
    {"kind": "elements", "elements": [_text("题干"), _img("a.png")], "qnum": 1},
    {"kind": "elements", "elements": [_img("b.png")], "qnum": None},   # 纯图页
    {"kind": "elements", "elements": [_text("下一题")], "qnum": 2},
]
merged, n = merge_figure_only_items(items)
check("纯图项并入上一题", n == 1 and len(merged) == 2, f"merges={n} len={len(merged)}")
check("插图并入第1项", any(e.get("image_path") == "b.png" for e in merged[0]["elements"]))

# 12) 首项是纯图时无前项可并，保留原样
items2 = [{"kind": "elements", "elements": [_img("x.png")], "qnum": None}]
merged2, n2 = merge_figure_only_items(items2)
check("首项纯图保留", n2 == 0 and len(merged2) == 1)

# 13) 直转（preset）项不参与合并，纯图识别项也不并入 preset 项
items3 = [
    {"kind": "preset", "md": "直转题目 ![](media/a.png)", "qnum": None},
    {"kind": "elements", "elements": [_img("y.png")], "qnum": None},
]
merged3, n3 = merge_figure_only_items(items3)
check("preset 项不并入", n3 == 0 and len(merged3) == 2)

# 14) 大框回收：四宫格插图只检测出两幅小图 + 整带大框（杠杆题真实形态）
from recognizer import recover_dropped_figures
kept_boxes = [[21, 77, 170, 202], [22, 232, 178, 338]]  # 甲 / 丙
dropped_boxes = [[20, 75, 427, 362]]                     # 整带大框（含乙丁）
rec = recover_dropped_figures(dropped_boxes, kept_boxes)
check("回收出右列区域", any(r[0] >= 160 and r[2] > 400 for r in rec), str(rec))
check("右列被网格线切成两条", len(rec) == 2, str(rec))
if len(rec) == 2:
    rec_s = sorted(rec, key=lambda b: b[1])
    check("上条含乙图（含图注带）", rec_s[0][1] <= 80 and rec_s[0][3] >= 220, str(rec_s[0]))
    check("下条含丁图", rec_s[1][1] >= 220 and rec_s[1][3] >= 360, str(rec_s[1]))

# 15) 小框完全覆盖大框时不产生回收
rec2 = recover_dropped_figures([[0, 0, 100, 100]], [[0, 0, 100, 100]])
check("完全覆盖无回收", rec2 == [], str(rec2))

# 16) 表格裁图（fig_kind="table"）不参与图注配对与缺注推断
els16 = [
    {"type": "text", "text": "题干文字\n\n甲", "image_path": None, "box": None},
    {"type": "image", "text": "", "image_path": "fig.png", "box": [10, 10, 100, 100]},
    {"type": "image", "text": "", "image_path": "tbl.png", "box": [10, 200, 300, 400],
     "fig_kind": "table"},
]
tag_figure_captions(els16)
fig16 = next(e for e in els16 if e.get("image_path") == "fig.png")
tbl16 = next(e for e in els16 if e.get("image_path") == "tbl.png")
check("图注配给插图", fig16.get("caption") == "甲", str(fig16.get("caption")))
check("表格不配图注", tbl16.get("caption") is None, str(tbl16.get("caption")))

print()
print("结果:", "全部通过" if not fails else f"失败 {len(fails)} 项: {fails}")
sys.exit(1 if fails else 0)
