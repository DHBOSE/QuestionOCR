# -*- coding: utf-8 -*-
"""
converter.py —— Markdown 生成与拼接

功能：
- 把单张图片的识别结果（文字 / 公式 / 裁剪图片）重组为一道题的 Markdown
- 把多道题合并为一个完整的 Markdown 文件（题间用标题 + 分隔线区分）
"""

import logging
import os
import re
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# 插入 Word 的图片尺寸上限（等比缩放，宽不超过 6cm，高不超过 5cm）
MAX_IMG_WIDTH_CM = 6.0
MAX_IMG_HEIGHT_CM = 5.0

# 截图开头的题目序号（如 "9."、"9、"、"9．"、"(9)"、"（9）"、"第9题："）
# 分隔符后不能紧跟数字，避免误删以小数开头的题干（如 "9.5 N 的力"）
_QUESTION_NO_RE = re.compile(
    r"^\s*(?:第\s*\d{1,3}\s*题\s*[：:、.．]?\s*|[（(]\s*\d{1,3}\s*[)）]\s*|\d{1,3}\s*[.、．](?!\d)\s*)"
)

# 行首的选择题选项标记：A. / A、/ A．/ (A) / （A），后跟空白/数字/公式
_OPTION_LINE_RE = re.compile(r"^\s*[（(]?[A-F][)）]?[.、．](?=[\s\d$])")
# 半角句点形式（"A. "），需要转义防止 Pandoc 解析成自动编号列表
_ASCII_DOT_OPTION_RE = re.compile(r"^(\s*[（(]?[A-F][)）]?)\.(?=[\s\d$])")
# 行内的选项标记（"…的力 B. $…"），前为空白、后为空白/数字/公式，拆分为独立段落
_INLINE_OPTION_RE = re.compile(r"\s+([（(]?[A-F][)）]?[.、．])(?=[\s\d$（(])")
# 公式包裹的选项标记：$\mathrm{C . ~} 1 ~ 2 0 0$ → C. $1 ~ 2 0 0$
# （本地 OCR 常把"字母+句点"开头的选项整体识别成公式）
_MATH_OPTION_RE = re.compile(
    r"\$\s*(?:\\(?:mathrm|mathbf|bf|rm|text)\s*)?\{?\s*([A-F])\s*[.、．]\s*~?\s*\}?\s*(.*?)\$"
)


def _unwrap_math_option(m):
    """把公式开头的选项标记还原为纯文本，剩余内容保留为公式。"""
    rest = m.group(2).strip()
    return f"{m.group(1)}. ${rest}$" if rest else f"{m.group(1)}. "


def normalize_option_lines(md_text: str) -> str:
    """
    让选择题的每个选项在 Word 中单独占一行（独立段落）。

    处理：
    1. 解开公式包裹的选项标记（字母+句点开头才解，$F_{1}$ 等不受影响）
    2. 行内的 B./C./D. 标记前断开，选项各自独立成行
    3. 在选项行与前面的非空行之间补空行 → 每个选项成为独立段落
    4. 把行首 "A." 的半角句点转义为 "A\\."，防止 Pandoc fancy_lists
       把它解析成自动编号列表（OCR 缺项/错项时自动编号会与原题不符）
    """
    out = []
    for ln in md_text.split("\n"):
        ln = _MATH_OPTION_RE.sub(_unwrap_math_option, ln)
        # 行内选项标记前插入段落分隔（\s+ 要求前面有空白，行首标记不受影响）
        ln = _INLINE_OPTION_RE.sub("\n\n" + r"\1", ln)
        for sub in ln.split("\n"):
            if _OPTION_LINE_RE.match(sub):
                if out and out[-1].strip():
                    out.append("")  # 与前文隔开，独立成段
                sub = _ASCII_DOT_OPTION_RE.sub(lambda m: m.group(1) + r"\.", sub, count=1)
            out.append(sub)
    return "\n".join(out)


def restore_fill_blanks(md_text: str) -> str:
    r"""
    还原填空题留空处的下划线。

    背景：填空题的下划线在图片里是孤零零的横线，OCR 与多模态模型都容易
    直接丢掉（"可看作一个____（填"省力"…）"被识别成"可看作一个（填…）"）。
    启发式规则：紧跟"（填" / "(填" 的位置即留空处，若前面没有下划线则补上
    转义的 \_\_\_\_\_\_（转义防止 Pandoc 把连续下划线解析成加粗）。
    """
    def _sub(m):
        return (m.group(1) or r"\_\_\_\_\_\_") + "（填"
    return re.sub(r"(_{3,}|(?:\\_){3,})?\s*[（(]\s*填", _sub, md_text)


def strip_question_number(text: str) -> str:
    """
    去掉题目正文开头的序号，使内容顶格、与合并后的"题目 N"标题左对齐。
    只处理开头一处，正文中出现的数字不受影响。
    """
    return _QUESTION_NO_RE.sub("", text, count=1).lstrip()


def _image_size_attr(image_path: str) -> str:
    """
    根据图片像素宽高比计算 Markdown 图片尺寸属性，
    使图片等比缩放后宽 ≤ 6cm、高 ≤ 5cm。
    返回形如 {width=6cm height=3.2cm} 的属性串；读取失败时只限宽度。
    """
    try:
        with Image.open(image_path) as im:
            w_px, h_px = im.size
        if w_px <= 0 or h_px <= 0:
            raise ValueError("invalid size")
        ratio = h_px / w_px
        width_cm = MAX_IMG_WIDTH_CM
        height_cm = width_cm * ratio
        if height_cm > MAX_IMG_HEIGHT_CM:
            height_cm = MAX_IMG_HEIGHT_CM
            width_cm = height_cm / ratio
        return f"{{width={width_cm:.2f}cm height={height_cm:.2f}cm}}"
    except Exception:
        return f"{{width={MAX_IMG_WIDTH_CM:.2f}cm}}"


# 图注标签：甲 / 乙 / 丙 … / 图1 / 图 2 /（第 2 题图）
_CAPTION_RE = re.compile(r"^[（(]?\s*(?:图\s*\d{1,2}|[甲乙丙丁戊己庚辛])\s*[)）]?$")
_CAPTION_SEQ = "甲乙丙丁戊己庚辛"
# 带题号的图注：第 N 题图（混排插图的归属依据）
_CAPTION_QNUM_RE = re.compile(r"^[（(]?\s*第\s*(\d{1,3})\s*题\s*图\s*[)）]?$")


def _clean_caption_text(t: str) -> str:
    """清洗图注候选文本：去掉公式装饰（$ ~ {} \\命令 空白）。
    OCR 常把图注里的数字识别成小公式："（第 $~ 3$ 题图）" → "（第3题图）"。"""
    t = re.sub(r"\\[a-zA-Z]+", "", t)
    return re.sub(r"[$~{}\\\s]", "", t)


def _match_caption(text: str):
    """整段文字恰是一个图注标签时返回规范化标签，否则返回 None。"""
    t = _clean_caption_text((text or "").strip())
    m = _CAPTION_QNUM_RE.match(t)
    if m:
        return f"第{m.group(1)}题图"
    return t if _CAPTION_RE.match(t) else None


def _crop_caption_out(image_el: dict, cap_box: list) -> None:
    """
    图注被检测框包进插图底部时，把已裁图片的下沿收紧到图注上方，
    避免图注文字在图片里和 Markdown 图注重复出现。
    """
    b = image_el.get("box")
    path = image_el.get("image_path")
    if not b or not path or not cap_box:
        return
    cap_top = cap_box[1]
    if not (b[1] + 30 < cap_top < b[3] - 4):
        return  # 图注整体在图外（正常情况），无需处理
    try:
        with Image.open(path) as im:
            new_h = cap_top - b[1] - 4
            if 10 < new_h < im.height:
                im.crop((0, 0, im.width, new_h)).save(path)
                b[3] = cap_top - 4
    except OSError:
        pass


def tag_figure_captions(elements: list) -> None:
    """
    元素级图注配对（本地/混合模式）：独立的图注小文字块按位置配给插图——
    标签水平中心落在图 x 范围内、且紧跟图的下沿（或上沿）80px 内。
    配对成功后图注文字从 elements 移除，插图元素写入 "caption"。
    在识别后、生成 Markdown 前调用（api.py），跨题重分配依赖这里的 caption。
    表格裁图（fig_kind="table"）不参与图注配对。
    """
    images = [e for e in elements
              if e.get("type") == "image" and e.get("box") and e.get("fig_kind") != "table"]
    if not images:
        return
    for el in elements[:]:
        if el.get("type") == "image":
            continue
        label = _match_caption(el.get("text"))
        if not label or not el.get("box"):
            continue
        cx = (el["box"][0] + el["box"][2]) / 2
        best, best_gap = None, 81
        for im in images:
            if im.get("caption"):
                continue
            b = im["box"]
            if not (b[0] - 20 <= cx <= b[2] + 20):
                continue
            gap = min(abs(el["box"][1] - b[3]), abs(b[1] - el["box"][3]))
            if gap < best_gap:
                best, best_gap = im, gap
        if best is not None:
            best["caption"] = label
            elements.remove(el)
            _crop_caption_out(best, el.get("box"))  # 图注被框进图里时把它裁掉

    # 2) 大段文字尾部的图注行（API/混合模式，文字是一个无坐标的整体）：
    #    摘出尾部连续图注行，按阅读顺序分配给未配对插图。
    #    必须在生成 Markdown 前完成，跨题重分配（reassign_question_figures）依赖它。
    for el in elements:
        if el.get("type") == "image" or el.get("box"):
            continue
        lines = (el.get("text") or "").rstrip().split("\n")
        trailing = []
        while lines:
            s = lines[-1].strip()
            cap = _match_caption(s)
            if cap:
                trailing.insert(0, cap)
                lines.pop()
            elif not s:
                lines.pop()  # 跳过图注行之间的空行
            else:
                break
        if not trailing:
            continue
        free = [im for im in _reading_order(images) if not im.get("caption")]
        if len(trailing) <= len(free):
            for cap, im in zip(trailing, free):
                im["caption"] = cap
            el["text"] = "\n".join(lines)


def reassign_question_figures(elements_list: list, qnums: list) -> None:
    """
    跨题插图重分配：一页多题但插图混排在一起时（如两题的图并排放在中间），
    图注为"第 N 题图"的插图若与所在题号不符，搬到对应题目的元素列表。
    对不上任何题号的插图留在原地（安全兜底，不会比不搬更差）。
    elements_list: 每题元素列表；qnums: 每题题号（None 表示未知）。
    """
    by_num = {n: i for i, n in enumerate(qnums) if n is not None}
    if not by_num:
        return
    for i, elements in enumerate(elements_list):
        for el in elements[:]:
            if el.get("type") != "image":
                continue
            m = _CAPTION_QNUM_RE.match((el.get("caption") or "").strip())
            if not m:
                continue
            target = by_num.get(int(m.group(1)))
            if target is None or target == i:
                continue
            elements.remove(el)
            elements_list[target].append(el)
            logger.info("跨题插图重分配：%s 从第 %s 题移到第 %s 题",
                        m.group(0), qnums[i], qnums[target])

    # 图注可能被版面分析的大框吞进图里导致 OCR 丢失（如"（第 1 题图）"没被识别）。
    # 若本页存在"第 N 题图"样式的图注，落单的无注插图补默认图注"第{本题号}题图"
    referenced = set()
    for els in elements_list:
        for el in els:
            if el.get("type") != "image":
                continue
            m = _CAPTION_QNUM_RE.match((el.get("caption") or "").strip())
            if m:
                referenced.add(int(m.group(1)))
    if not referenced:
        return
    for i, els in enumerate(elements_list):
        n = qnums[i]
        if n is None or n in referenced:
            continue
        uncaptioned = [el for el in els if el.get("type") == "image" and not el.get("caption")]
        if len(uncaptioned) == 1:
            uncaptioned[0]["caption"] = f"第{n}题图"
            referenced.add(n)
            logger.info("补默认图注：第 %s 题图（原图注疑似被吞）", n)


def merge_figure_only_items(items: list) -> list:
    """
    把"整项只有插图没有文字"的识别项并入前一个识别项。

    PDF 换页 / 一页多题拆分可能把某题的插图单独切到下一项（整项只有图没有文字），
    单列一道"题"会让用户困惑，并入上一题更符合直觉；若图注带"第 N 题图"，
    合并后仍会由跨题重分配搬到正确的题。
    仅处理前后都是识别结果（elements）的情况；首项是纯图时无前项可并，保留原样。
    items: [{"kind": "elements"|"preset", "elements": [...], ...}]，按处理顺序排列。
    返回 (合并后的列表, 合并发生的次数)。
    """
    out = []
    merges = 0
    for r in items:
        has_text = r["kind"] != "elements" or any(
            el.get("type") != "image" and str(el.get("text", "")).strip()
            for el in r["elements"]
        )
        if (not has_text and out and out[-1]["kind"] == "elements"
                and r["kind"] == "elements"):
            out[-1]["elements"] = out[-1]["elements"] + r["elements"]
            merges += 1
            continue
        out.append(r)
    return out, merges


def _reading_order(images: list) -> list:
    """
    按阅读顺序排列插图：先按行（纵向重叠超过一半算同一行），行内按 x。
    缺 box 的保持原相对顺序排最后。
    """
    def _key(im):
        box = im.get("box")
        return (0, box[1], box[0]) if box else (1, 0, 0)

    boxed = sorted((im for im in images if im.get("box")), key=lambda im: (im["box"][1], im["box"][0]))
    rows = []
    for im in boxed:
        b = im["box"]
        placed = False
        for row in rows:
            # 与行内首个元素纵向区间重叠超过其高度一半 → 同一行
            rb = row[0]["box"]
            overlap = min(b[3], rb[3]) - max(b[1], rb[1])
            if overlap > 0.5 * min(b[3] - b[1], rb[3] - rb[1]):
                row.append(im)
                placed = True
                break
        if not placed:
            rows.append([im])
    ordered = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda im: im["box"][0]))
    ordered.extend(im for im in images if not im.get("box"))
    return ordered


def _pair_captions(text_items: list, images: list):
    """
    把图注标签与插图配对并写入 images[i]["caption"]，配上的图注从文字中移除。
    1. 独立图注小元素（本地 OCR 把"甲"识别成单独小块）：按位置配对——
       标签水平中心落在图 x 范围内、且紧跟图的下沿（或上沿）；
    2. 大段文字尾部的独立图注行（API/混合模式）：按阅读顺序分配给未配对插图；
    3. 有图注的插图按图注顺序排序；序列中缺注的插图按顺序推断补齐。
    """
    # 1) 位置配对
    for item in text_items[:]:
        label = _match_caption(item[0])
        if not label or not item[1]:
            continue
        cx = (item[1][0] + item[1][2]) / 2
        best, best_gap = None, 81  # 纵向间距阈值（像素）
        for im in images:
            if im["caption"] or not im.get("box") or im.get("fig_kind") == "table":
                continue
            b = im["box"]
            if not (b[0] - 20 <= cx <= b[2] + 20):
                continue
            gap = min(abs(item[1][1] - b[3]), abs(b[1] - item[1][3]))
            if gap < best_gap:
                best, best_gap = im, gap
        if best is not None:
            best["caption"] = label
            text_items.remove(item)

    # 2) 尾部图注行按阅读顺序分配
    for item in text_items:
        lines = item[0].rstrip().split("\n")
        trailing = []
        while lines:
            s = lines[-1].strip()
            if _match_caption(s):
                trailing.insert(0, s)
                lines.pop()
            elif not s:
                lines.pop()  # 跳过图注行之间的空行
            else:
                break
        if not trailing:
            continue
        free = [im for im in _reading_order(images)
                if not im["caption"] and im.get("fig_kind") != "table"]
        if len(trailing) <= len(free):
            for label, im in zip(trailing, free):
                im["caption"] = label
            item[0] = "\n".join(lines)

    # 3) 排序与缺注推断：已配对图注是"甲乙丙…"前缀时，剩余插图按序补齐
    captioned = [im for im in images if im["caption"]]
    if not captioned:
        return
    singles = [im["caption"] for im in captioned if im["caption"] and im["caption"] in _CAPTION_SEQ]
    if singles:
        ordered = sorted(
            images,
            key=lambda im: (_CAPTION_SEQ.index(im["caption"])
                            if im["caption"] and im["caption"] in _CAPTION_SEQ
                            else len(_CAPTION_SEQ)),
        )
        images[:] = ordered
        next_idx = 0
        for im in images:
            if im["caption"] and im["caption"] in _CAPTION_SEQ:
                next_idx = _CAPTION_SEQ.index(im["caption"]) + 1
            elif (not im["caption"] and im.get("fig_kind") != "table"
                  and next_idx < len(_CAPTION_SEQ)):
                im["caption"] = _CAPTION_SEQ[next_idx]
                next_idx += 1


def elements_to_markdown(elements: list, md_dir: str) -> str:
    """
    将一道题的元素列表转换为 Markdown 文本。

    规则：
    - 题干文字、公式按原顺序排在前
    - 裁剪出的图片以 ![图注](相对路径){尺寸属性} 排在下方，
      图片等比缩放，宽不超过 6cm、高不超过 5cm
    - 短标签图注（甲 / 乙 / 图1 等）会与插图配对：优先按位置（标签在图正下方）
      配对，配对不到的再取大段文字尾部的独立图注行按阅读顺序分配；
      有图注的插图按图注顺序排列，缺注的按序列推断补齐（甲之后是乙）
    参数 md_dir 为最终 Markdown 文件所在目录，用于计算图片的相对路径。
    """
    text_items = []   # [文字, box]（保持顺序）
    images = []       # {"path", "box", "caption"}
    number_stripped = False  # 题目序号只从第一段文字开头剥离一次

    for el in elements:
        if el["type"] == "image":
            # caption 可能已在识别阶段由 tag_figure_captions 配好（含跨题搬来的图）
            images.append({"path": el["image_path"], "box": el.get("box"),
                           "caption": el.get("caption"),
                           "fig_kind": el.get("fig_kind")})
        else:
            text = el["text"]
            if not number_stripped and text.strip():
                # 去掉截图开头的题目序号（9. / 9、 / (9) / 第9题 等），内容顶格
                text = strip_question_number(text)
                number_stripped = True
            text_items.append([text, el.get("box")])

    if images:
        _pair_captions(text_items, images)

    text_lines = [t for t, _ in text_items]
    image_lines = []
    for im in images:
        # 计算相对于 Markdown 文件的路径，保证 Pandoc 能找到图片
        rel_path = os.path.relpath(im["path"], md_dir).replace("\\", "/")
        alt = im["caption"] or ""
        image_lines.append(f"![{alt}]({rel_path}){_image_size_attr(im['path'])}")

    parts = []
    if text_lines:
        # 文字与公式之间用空行隔开，避免 Pandoc 误解析；
        # 选项分行 / 填空还原在这里做一遍（预览即可见），合并时会再做一遍（幂等）
        parts.append(restore_fill_blanks(normalize_option_lines("\n\n".join(text_lines))))
    if image_lines:
        parts.append("\n\n".join(image_lines))
    return "\n\n".join(parts)


def merge_questions(question_mds: list, md_path: str, title_prefix: str = "题目") -> str:
    """
    将多道题的 Markdown 合并写入一个文件。
    每道题前加"{title_prefix} N"标题（二级标题，Word 中为加粗黑色样式），
    题间用分隔线区分。
    返回合并后的 Markdown 完整文本。
    """
    title_prefix = (title_prefix or "题目").strip() or "题目"
    blocks = []
    for idx, qmd in enumerate(question_mds, start=1):
        # 选项分行 / 填空还原放在合并时（预览编辑之后），用户怎么改都生效
        blocks.append(f"## {title_prefix} {idx}\n\n{restore_fill_blanks(normalize_option_lines(qmd))}")

    merged = "\n\n---\n\n".join(blocks) + "\n"

    md_path = Path(md_path)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(merged, encoding="utf-8")
    return merged
