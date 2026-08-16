# -*- coding: utf-8 -*-
r"""
splitter.py —— 一页多题自动拆分

原理：
1. 只裁剪页面**左侧边条**（题号出现的位置）做轻量 OCR，比整页识别快得多；
2. 在 OCR 结果的逐 token 位置信息里找题号（6. / 7、/ （8）/ 第9题），
   容忍 OCR 把题号当公式识别的形式（如 {\bf7 .}），并带小数保护（9.5 不算题号）；
3. 题号必须**严格递增**才采信（防正文里的数字误检）；
4. 按题号行的 y 坐标把整页横向切成单题子图，每张子图走现有识别管线，
   自然成为独立的"题目 N"。

任何一步失败都静默回退为"整页按单题处理"，不影响主流程。
"""

import logging
import re
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# 左侧边条宽度：页宽的 18%，夹在 [150, 280] 像素之间
STRIP_WIDTH_RATIO = 0.18
STRIP_MIN_WIDTH = 150
STRIP_MAX_WIDTH = 280
# 切分线提到题号行上方的边距（像素），避免把题号行切进上一题
# （大号彩色美术字题号的字形可能比 OCR 文本框高出 25px 以上，边距要留足，
#  宁可多带一点上一题的尾部空白）
SPLIT_TOP_MARGIN = 36
# 相邻题号的最小纵向间距（占页高比例），用于去除同一行的重复命中
MIN_GAP_RATIO = 0.03

# OCR 把题号当公式时的装饰字符：{\bf7 .} → 7 .
_DECOR_RE = re.compile(r"\\[a-zA-Z]+|[{}$\\~]")
# 普通形式：6. / 7、/ 8．/ 9（小数保护：9.5 / 9．5 不匹配）
_QNUM_RE = re.compile(r"^\s*(\d{1,3})(?:\s*[.、．])?(?![\d.、．])")
# 中文形式：第9题 / （9）/ (9)（"第 N 题图"是图注，不是题号，必须排除）
_QNUM_CN_RE = re.compile(r"^\s*(?:第\s*(\d{1,3})\s*题(?!\s*图)\s*[：:、.．]?\s*|[（(]\s*(\d{1,3})\s*[)）](?!\s*题\s*图))")


def _extract_question_number(text: str):
    """从一段 OCR 文本开头提取题号；不是题号返回 None。"""
    t = _DECOR_RE.sub("", text or "").strip()
    m = _QNUM_CN_RE.match(t)
    if m:
        return int(m.group(1) or m.group(2))
    m = _QNUM_RE.match(t)
    if m:
        return int(m.group(1))
    return None


def find_question_split_ys(image_path: str, work_dir: str, log=logger.info):
    """
    在页面左侧边条中找题号，返回 [(题号, 题首行 y 坐标)]（按 y 升序，含第一题）。
    少于 2 个有效题号时返回 None（按单题处理）。
    """
    from recognizer import get_pix2text, _bbox_from_position

    img = Image.open(image_path)
    W, H = img.size
    strip_w = min(STRIP_MAX_WIDTH, max(STRIP_MIN_WIDTH, int(W * STRIP_WIDTH_RATIO)))
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    strip_path = Path(work_dir) / "_strip.png"
    img.crop((0, 0, strip_w, H)).save(strip_path)

    p2t = get_pix2text()
    page = p2t.recognize_page(str(strip_path))
    elements = page.elements if hasattr(page, "elements") else list(page)

    hits = []  # (题号或 None=乱码候选, 绝对 y, 是否乱码候选)
    for el in elements:
        box = getattr(el, "box", None)
        if box is not None:
            el_y = int(box[1])
        elif getattr(el, "position", None) is not None:
            el_y = _bbox_from_position(el.position)[1]
        else:
            continue
        for entry in getattr(el, "meta", None) or []:
            pos = entry.get("position")
            if pos is None:
                continue
            # 题号只会出现在段落首行，正文换行顶格的数字（如"40 cm"）直接排除
            if entry.get("line_number") not in (None, 0):
                continue
            rel_y = min(float(p[1]) for p in pos)
            rel_x = min(float(p[0]) for p in pos)
            text = entry.get("text") or ""
            if "题图" in text:
                continue  # "（第 N 题图）"是插图图注，不是题号
            num = _extract_question_number(text)
            if num is not None:
                # meta position 是相对元素框的坐标，加元素偏移得整页坐标
                hits.append((num, el_y + rel_y, False))
            elif (entry.get("type") in ("embedding", "isolated") and "[" in text
                  and rel_x < strip_w * 0.4):
                # 大号彩色题号可能被公式识别吞掉，随出处标签"[2026 …"一起变成
                # 乱码（如 \mathbb{C} [ \; 2 0 2 6），记为乱码题号候选。
                # isolated（独立公式块）同样可能是被吞的题号，如 \mathbb{Z [}…
                hits.append((None, el_y + rel_y, True))

    # 按 y 排序后构建连续题号序列：
    # 1. 与上一题号纵向间距过小的视为同一行（如题号同行的出处标签乱码），跳过；
    # 2. 题号必须连续递增（容忍 OCR 漏掉一个号），正文里的数字（如"40"）被滤掉；
    # 3. 乱码候选按顺序推断为上一题号 + 1。
    hits.sort(key=lambda h: h[1])
    seq = []
    for num, y, is_garbled in hits:
        if seq and y - seq[-1][1] <= H * MIN_GAP_RATIO:
            continue
        if num is None:
            num = (seq[-1][0] + 1) if seq else 1
        elif seq and not (seq[-1][0] < num <= seq[-1][0] + 2):
            continue
        seq.append((num, y))
        logger.info("拆题检测：题号 %d @ y=%d%s", num, int(y),
                    "（乱码推断）" if is_garbled else "")

    if len(seq) < 2:
        return None
    return seq


def split_image(image_path: str, work_dir: str, log=logger.info) -> list:
    """
    若页面包含多道题，横向切成单题子图，返回 [(子图路径, 题号)]；
    否则返回 [(原图路径, None)]。任何异常都回退为整页单题。
    题号用于跨题插图重分配（图注"第 N 题图"归属判定）。
    """
    try:
        seq = find_question_split_ys(image_path, work_dir, log)
    except Exception as e:
        log(f"  拆题检测失败，按单题处理（{e}）")
        return [(str(image_path), None)]

    if not seq:
        return [(str(image_path), None)]

    ys = [y for _, y in seq]
    img = Image.open(image_path).convert("RGB")
    W, H = img.size
    bounds = [0] + [max(1, int(y) - SPLIT_TOP_MARGIN) for y in ys[1:]] + [H]
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    parts = []
    for i in range(len(bounds) - 1):
        sub = img.crop((0, bounds[i], W, bounds[i + 1]))
        p = Path(work_dir) / f"part_{i + 1}.png"
        sub.save(p)
        parts.append((str(p), seq[i][0]))
    log(f"  该页检测到 {len(parts)} 道题，已自动拆分")
    return parts
