# -*- coding: utf-8 -*-
r"""
recognizer.py —— 题目截图识别封装（Pix2Text + UniMERNet 双引擎）

处理流程：
1. Pix2Text 做版面分析 + 文字 OCR + 公式初识别（pix2tex）
2. 对每个公式的 LaTeX 做小瑕疵清理（cfrac→frac、operatorname{s i n}→sin 等）
3. 用 Pandoc（texmath）逐个校验：能被解析为数学式的保留为可编辑公式
4. 校验失败的公式：裁剪原图区域，交给 UniMERNet-small 二次识别，再次清理校验
5. 仍然失败的才裁剪为图片兜底（保证 Word 里不会出现乱码 LaTeX）

所有处理均在本地完成，不调用任何远程 API。
"""

import re
import os
import logging
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from procutil import run_quiet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
# 公式图片裁剪边距（meta 中的公式框略偏右，左侧多补一些）
CROP_PADDING = 8
CROP_LEFT_EXTRA = 28
# 是否启用 UniMERNet 对失败公式进行二次识别（模型目录见下）
USE_UNIMERNET_FALLBACK = True
UNIMERNET_MODEL_DIR = Path(__file__).parent / "models" / "unimernet_small"
# 最终仍无法识别时，是否把公式裁剪为图片（False 则保留原始 LaTeX 文本）
FORMULA_IMAGE_FALLBACK = True
# 复杂公式被替换为图片后，原文位置的占位提示
FORMULA_PLACEHOLDER = "【公式见下图】"

# 常见数学函数名（用于把 \operatorname{sin} 规范回 \sin）
_MATH_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc",
    "arcsin", "arccos", "arctan",
    "sinh", "cosh", "tanh",
    "log", "ln", "lg", "lim", "max", "min", "sup", "inf",
}

_p2t_instance = None      # Pix2Text 单例
_unimernet = None         # UniMERNet 单例 (model, vis_processor)
_pandoc_path = None       # pandoc 路径缓存
_layout_parser = None     # 版面分析器单例（混合模式专用，不做 OCR，加载很快）


# ---------------------------------------------------------------------------
# 模型加载
# ---------------------------------------------------------------------------
def get_pix2text():
    """获取 Pix2Text 单例；首次调用时初始化（模型缺失会自动下载）。"""
    global _p2t_instance
    if _p2t_instance is None:
        logger.info("正在初始化 Pix2Text（首次运行会自动下载模型，请保持网络畅通）……")
        from pix2text import Pix2Text

        _p2t_instance = Pix2Text.from_config()
        logger.info("Pix2Text 初始化完成。")
    return _p2t_instance


def get_unimernet():
    """
    获取 UniMERNet-small 单例（公式二次识别引擎）。
    模型文件需已放置在 backend/models/unimernet_small/。
    """
    global _unimernet
    if _unimernet is None:
        import argparse
        import torch

        # 兼容 transformers 4.46：unimernet 的解码器只支持 eager 注意力
        from transformers import VisionEncoderDecoderConfig

        _orig = VisionEncoderDecoderConfig.from_pretrained.__func__

        @classmethod
        def _eager_from_pretrained(cls, *args, **kwargs):
            cfg = _orig(cls, *args, **kwargs)
            cfg._attn_implementation = "eager"
            for sub in ("encoder", "decoder"):
                if hasattr(cfg, sub):
                    getattr(cfg, sub)._attn_implementation = "eager"
            return cfg

        VisionEncoderDecoderConfig.from_pretrained = _eager_from_pretrained

        from unimernet.common.config import Config
        import unimernet.tasks as tasks
        from unimernet.processors import load_processor

        cfg_path = UNIMERNET_MODEL_DIR / "infer.yaml"
        logger.info("正在加载 UniMERNet 公式识别模型……")
        args = argparse.Namespace(cfg_path=str(cfg_path), options=None)
        cfg = Config(args)
        task = tasks.setup_task(cfg)
        model = task.build_model(cfg).to("cpu")
        model.eval()
        vis_processor = load_processor(
            "formula_image_eval",
            cfg.config.datasets.formula_rec_eval.vis_processor.eval,
        )
        _unimernet = (model, vis_processor, torch)
        logger.info("UniMERNet 加载完成。")
    return _unimernet


def unimernet_recognize(pil_img: Image.Image) -> str:
    """用 UniMERNet 识别单张公式图片，返回 LaTeX；失败返回空串。"""
    try:
        model, vis_processor, torch = get_unimernet()
        tensor = vis_processor(pil_img.convert("RGB")).unsqueeze(0)
        with torch.no_grad():
            out = model.generate({"image": tensor})
        return out["pred_str"][0].strip()
    except Exception as e:
        logger.warning("UniMERNet 识别失败：%s", e)
        return ""


# ---------------------------------------------------------------------------
# LaTeX 清理与校验
# ---------------------------------------------------------------------------
def cleanup_latex(latex: str) -> str:
    r"""
    清理公式 LaTeX 的常见小瑕疵，使其能被 Pandoc(texmath) 正确解析：
    - \cfrac / \dfrac → \frac（Word 公式对连分式支持差）
    - \operatorname{s i n} → \sin（字母被拆开的情况）
    - 去掉 \! 等强制间距符；\lrcorner 多为顿号误识别
    """
    text = latex.strip()

    # \operatorname{s i n} → 去掉花括号内空格 → \operatorname{sin}
    def _fix_operatorname(m):
        content = re.sub(r"\s+", "", m.group(1))
        return rf"\operatorname{{{content}}}"

    text = re.sub(r"\\operatorname\s*\{([^}]*)\}", _fix_operatorname, text)

    # \operatorname{sin} → \sin（已知函数名）
    def _to_function(m):
        name = m.group(1)
        return rf"\{name}" if name in _MATH_FUNCTIONS else m.group(0)

    text = re.sub(r"\\operatorname\{([a-zA-Z]+)\}", _to_function, text)

    # 连分式/显示分式统一为 \frac
    text = text.replace(r"\cfrac", r"\frac").replace(r"\dfrac", r"\frac")

    # 清理强制间距符
    text = text.replace(r"\!", "").replace(r"\,", " ").replace(r"\;", " ")

    # 物理题中顿号常被误识别为 \lrcorner
    text = text.replace(r"\lrcorner", "、")

    # \stackrel{a}{b} 也按分式处理（UniMERNet 偶尔输出该形式）
    text = re.sub(r"\\stackrel\s*\{", r"\\frac{", text)

    return text.strip()


def _find_pandoc() -> str | None:
    """查找 pandoc（复用 docx_builder 的逻辑，避免循环导入）。"""
    global _pandoc_path
    if _pandoc_path is None:
        from docx_builder import find_pandoc

        _pandoc_path = find_pandoc() or ""
    return _pandoc_path or None


def pandoc_accepts(latex: str, display: bool = False) -> bool:
    """
    用 Pandoc（texmath）校验 LaTeX 能否被解析为数学式。
    能解析 → True（转成 Word 后是可编辑公式）；否则 False。
    pandoc 不可用时返回 True（不拦截）。
    """
    pandoc = _find_pandoc()
    if not pandoc:
        return True
    wrapped = f"$${latex}$$" if display else f"${latex}$"
    try:
        result = run_quiet(
            [pandoc, "-f", "markdown+tex_math_dollars", "-t", "native"],
            input=wrapped, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        return "Math" in result.stdout
    except Exception:
        return True


# ---------------------------------------------------------------------------
# 裁剪工具
# ---------------------------------------------------------------------------
def _bbox_from_position(position) -> tuple:
    """position（4 点坐标或 x1,y1,x2,y2）→ (x1, y1, x2, y2) 整数边界框。"""
    pts = np.array(position).reshape(-1, 2)
    x1, y1 = pts.min(axis=0)
    x2, y2 = pts.max(axis=0)
    return int(x1), int(y1), int(x2), int(y2)


def _dedup_figure_boxes(boxes: list) -> list:
    """
    插图检测框去重：版面分析对插图带常输出重叠/嵌套框
    （整带一个大框 + 各单图小框，或同一张图的重复框）。
    规则：按面积升序保留，若与已保留框的交集超过自身面积 40% 则丢弃
    （小框先保留 → 大框因高度重叠被丢；重复框留小的）。
    """
    def area(b):
        return max(1, (b[2] - b[0]) * (b[3] - b[1]))

    kept = []
    for b in sorted(boxes, key=area):
        dup = False
        for k in kept:
            ix = max(0, min(b[2], k[2]) - max(b[0], k[0]))
            iy = max(0, min(b[3], k[3]) - max(b[1], k[1]))
            # 与两者中较小的面积比：大框包含小框（嵌套）也算重复
            if ix * iy > 0.4 * min(area(b), area(k)):
                dup = True
                break
        if not dup:
            kept.append(b)
    return kept


def _rect_subtract(big: list, smalls: list) -> list:
    """big 矩形减去 smalls 覆盖的区域，返回剩余的不重叠矩形列表（左/右/上/下四条切分）。"""
    rem = [big]
    for s in smalls:
        new = []
        for r in rem:
            ix1, iy1 = max(r[0], s[0]), max(r[1], s[1])
            ix2, iy2 = min(r[2], s[2]), min(r[3], s[3])
            if ix1 >= ix2 or iy1 >= iy2:
                new.append(r)
                continue
            if r[0] < ix1:
                new.append([r[0], r[1], ix1, r[3]])       # 左条
            if ix2 < r[2]:
                new.append([ix2, r[1], r[2], r[3]])       # 右条
            if r[1] < iy1:
                new.append([ix1, r[1], ix2, iy1])         # 上条
            if iy2 < r[3]:
                new.append([ix1, iy2, ix2, r[3]])         # 下条
        rem = new
    return rem


def _split_by_grid_edges(rect: list, edge_boxes: list, min_band: int = 40) -> list:
    """
    按网格线切分剩余区域：多图排版通常是网格（甲乙一行、丙丁一行），
    已保留小图框的边缘就是网格线。区域偏高时用水平切线（上下堆叠），
    偏宽时用垂直切线（左右并排）。切出的薄条（多为图注带）并入相邻条。
    """
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w >= h:  # 偏宽 → 垂直切线
        cuts = sorted({c for b in edge_boxes for c in (b[0], b[2])
                       if rect[0] + 8 < c < rect[2] - 8})
        bands, prev = [], rect[0]
        for c in cuts + [rect[2]]:
            bands.append([prev, rect[1], c, rect[3]])
            prev = c
        # 窄条并入左邻（切线对齐误差产生的细缝）
        out = []
        for b in bands:
            if b[2] - b[0] < min_band and out:
                out[-1][2] = b[2]
            elif b[2] - b[0] >= 12:
                out.append(b)
    else:  # 偏高 → 水平切线
        cuts = sorted({c for b in edge_boxes for c in (b[1], b[3])
                       if rect[1] + 8 < c < rect[3] - 8})
        bands, prev = [], rect[1]
        for c in cuts + [rect[3]]:
            bands.append([rect[0], prev, rect[2], c])
            prev = c
        # 薄条并入上方一条（图注通常在图下方）
        out = []
        for b in bands:
            if b[3] - b[1] < min_band and out:
                out[-1][3] = b[3]
            elif b[3] - b[1] >= 12:
                out.append(b)
    return out or [rect]


def recover_dropped_figures(dropped_boxes: list, kept_boxes: list,
                            min_area_ratio: float = 0.08, min_side: int = 40) -> list:
    """
    回收被去重丢弃的大框中未被小框覆盖的区域（防丢图）。

    大框被丢是因为与小框重叠，但大框里可能还有小框没覆盖到的图
    （典型：四宫格插图只检测出两幅小图 + 一个整带大框，丢掉大框就丢了另两幅）。
    对大框做矩形减法得到未覆盖区域，再按网格线（小框边缘）切分成单图。
    """
    recovered = []
    for d in dropped_boxes:
        d_area = max(1, (d[2] - d[0]) * (d[3] - d[1]))
        overlapping = [
            k for k in kept_boxes
            if min(d[2], k[2]) > max(d[0], k[0]) and min(d[3], k[3]) > max(d[1], k[1])
        ]
        for r in _rect_subtract(d, overlapping):
            rw, rh = r[2] - r[0], r[3] - r[1]
            if rw < min_side or rh < min_side:
                continue
            if rw * rh < min_area_ratio * d_area:
                continue
            recovered.extend(_split_by_grid_edges(r, overlapping))
    # 多个大框可能回收出互相重叠的区域，再去重一次
    return _dedup_figure_boxes(recovered)


def _crop_and_save(src_img: Image.Image, box, out_dir: str, name: str, left_extra: int = None) -> str:
    """
    按边界框从原图裁剪（带边距）并保存，返回保存路径。
    left_extra 为左侧额外补偿（公式检测框右偏而设）；版面分析出的插图框
    是准确的，传 0 避免把左侧文字带进插图。
    """
    if left_extra is None:
        left_extra = CROP_LEFT_EXTRA
    x1, y1, x2, y2 = box
    w, h = src_img.size
    x1 = max(0, x1 - CROP_PADDING - left_extra)
    y1 = max(0, y1 - CROP_PADDING)
    x2 = min(w, x2 + CROP_PADDING)
    y2 = min(h, y2 + CROP_PADDING)
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    save_path = Path(out_dir) / name
    src_img.crop((x1, y1, x2, y2)).save(save_path)
    return str(save_path)


def resolve_formula(src_img, box, out_dir, name, raw_latex, display, log):
    """
    单个公式的完整处理管线：
    pix2tex 结果 → 清理 → pandoc 校验 → (失败) UniMERNet 重识别 → 清理 → 校验
    → (仍失败) 图片兜底。

    返回 dict：
      {"kind": "latex", "text": "...", "display": bool}   可编辑公式
      {"kind": "image", "path": "..."}                    图片兜底
    """
    # 1. pix2tex 结果清理 + 校验
    latex = cleanup_latex(raw_latex)
    if latex and pandoc_accepts(latex, display):
        return {"kind": "latex", "text": latex, "display": display}

    # 2. UniMERNet 二次识别
    if USE_UNIMERNET_FALLBACK and UNIMERNET_MODEL_DIR.exists():
        x1, y1, x2, y2 = box
        crop_img = src_img.crop((
            max(0, x1 - CROP_PADDING - CROP_LEFT_EXTRA),
            max(0, y1 - CROP_PADDING),
            min(src_img.width, x2 + CROP_PADDING),
            min(src_img.height, y2 + CROP_PADDING),
        ))
        alt = cleanup_latex(unimernet_recognize(crop_img))
        if alt and pandoc_accepts(alt, display):
            log("  公式经 UniMERNet 二次识别成功")
            return {"kind": "latex", "text": alt, "display": display}

    # 3. 图片兜底
    if FORMULA_IMAGE_FALLBACK:
        path = _crop_and_save(src_img, box, out_dir, name)
        log(f"  公式无法可靠识别，已转为图片：{name}")
        return {"kind": "image", "path": path}

    # 兜底关闭时保留原始 LaTeX（可能在 Word 中显示为文本）
    return {"kind": "latex", "text": latex or raw_latex, "display": display}


# ---------------------------------------------------------------------------
# 版面分析（混合模式专用：只检测插图位置，不做 OCR，速度快）
# ---------------------------------------------------------------------------
_layout_lock = threading.Lock()  # 并行识别时保护版面分析单例（ONNX 会话非线程安全）


def get_layout_parser():
    """获取 DocLayout-YOLO 版面分析器单例（模型文件与 Pix2Text 共用，已下载）。"""
    global _layout_parser
    if _layout_parser is None:
        from pix2text.doc_yolo_layout_parser import DocYoloLayoutParser

        logger.info("正在加载版面分析模型（DocLayout-YOLO）……")
        _layout_parser = DocYoloLayoutParser(device="cpu")
        logger.info("版面分析模型加载完成。")
    return _layout_parser


def detect_figure_crops(image_path: str, image_out_dir: str, log=logger.info,
                        skip_tables: bool = False) -> list:
    """
    仅做版面分析，裁剪题目中的插图 / 表格区域，返回 image 元素列表。
    供「混合模式」使用：API 负责文字与公式，本地负责插图定位
    （qwen-vl-max 的 bbox 定位不够准，本地版面模型反而可靠）。
    skip_tables：API 已输出 Markdown 表格时跳过表格裁图，避免一表两出。
    """
    parser = get_layout_parser()
    # 多图并行识别时，版面分析单例会被多个混合模式线程并发调用，加锁串行（仅约 2 秒）
    with _layout_lock:
        results, _ = parser.parse(str(image_path))

    src_img = Image.open(image_path).convert("RGB")
    Path(image_out_dir).mkdir(parents=True, exist_ok=True)

    elements = []
    raw = []  # (box, kind)
    for item in results:
        item_type = str(item.get("type", "")).lower()
        if item_type not in ("figure", "table"):
            continue
        # 优先取 box，没有则用 position 四点坐标换算
        box = item.get("box")
        if box is not None:
            b = [int(v) for v in list(box)[:4]]
        elif item.get("position") is not None:
            b = list(_bbox_from_position(item["position"]))
        else:
            continue
        raw.append((b, item_type))

    if skip_tables and any(t == "table" for _, t in raw):
        log("  API 已输出 Markdown 表格，跳过本地表格裁图")
        raw = [(b, t) for b, t in raw if t != "table"]

    # 重叠/嵌套框去重（插图带大框 + 单图小框的情况只留小框）
    raw_boxes = [b for b, _ in raw]
    boxes = _dedup_figure_boxes(raw_boxes)
    if len(boxes) < len(raw_boxes):
        log(f"  插图检测框去重：{len(raw_boxes)} → {len(boxes)}")

    # 回收被丢大框中未被小框覆盖的区域（四宫格只检测出两幅小图时防丢图）
    dropped = [b for b in raw_boxes if b not in boxes]
    recovered = recover_dropped_figures(dropped, boxes) if dropped else []
    if recovered:
        log(f"  回收大框未覆盖区域，补裁 {len(recovered)} 幅插图")

    # 按阅读位置排序编号（去重保留顺序是按面积的，不直观）
    def _kind_of(b):
        for rb, t in raw:
            if rb == b:
                return t
        return "figure"

    counter = 0
    for x1, y1, x2, y2 in sorted(boxes + recovered, key=lambda b: (b[1], b[0])):
        counter += 1
        name = f"figure_{counter}.png"
        # 版面分析的检测框是准的，不需要公式框的左侧补偿
        crop_path = _crop_and_save(src_img, (x1, y1, x2, y2), image_out_dir, name, left_extra=0)
        elements.append({"type": "image", "text": "", "image_path": crop_path,
                         "box": [x1, y1, x2, y2], "fig_kind": _kind_of([x1, y1, x2, y2])})
        log(f"  本地版面分析裁剪插图：{name}")

    if not elements:
        log("  本地版面分析未检测到插图区域")
    return elements


# ---------------------------------------------------------------------------
# 主识别流程
# ---------------------------------------------------------------------------
def _rebuild_text_element(el, src_img, image_out_dir, image_counter, log):
    """
    处理 TEXT 元素：利用 meta 中的逐公式位置信息，
    每个公式走 resolve_formula 管线，文字与简单公式保留、
    失败公式转图片。

    返回 (重建后的文字元素或 None, 新增的图片元素列表)。
    """
    meta = el.meta or []
    parts = []          # 重建文字（按阅读顺序）
    images = []         # 兜底图片元素
    prev_line = None

    # meta 中的 position 是相对于元素自身的坐标，需要加上元素在原图中的偏移
    el_box = getattr(el, "box", None)
    offset_x = int(el_box[0]) if el_box is not None else 0
    offset_y = int(el_box[1]) if el_box is not None else 0

    for entry in meta:
        entry_type = entry.get("type", "text")
        text = (entry.get("text") or "").strip()
        if not text:
            continue

        line_no = entry.get("line_number")
        # 换行：meta 顺序即阅读顺序，行号变化时换行
        if prev_line is not None and line_no != prev_line:
            parts.append("\n")
        prev_line = line_no

        if entry_type in ("embedding", "isolated"):
            display = entry_type == "isolated"
            x1, y1, x2, y2 = _bbox_from_position(entry["position"])
            box = (x1 + offset_x, y1 + offset_y, x2 + offset_x, y2 + offset_y)
            image_counter[0] += 1
            result = resolve_formula(
                src_img, box, image_out_dir,
                f"formula_{image_counter[0]}.png", text, display, log,
            )
            if result["kind"] == "latex":
                wrapper = "$${}$$" if result["display"] else "${}$"
                parts.append(wrapper.format(result["text"]))
            else:
                images.append({"type": "image", "text": "", "image_path": result["path"]})
                parts.append(FORMULA_PLACEHOLDER)
        else:
            parts.append(text)

    rebuilt = " ".join(parts).replace(" \n ", "\n").replace("\n ", "\n").strip()
    text_el = {"type": "text", "text": rebuilt, "image_path": None,
               "box": [int(v) for v in el_box[:4]] if el_box is not None else None} if rebuilt else None
    return text_el, images


def recognize_question(image_path: str, image_out_dir: str, log=logger.info) -> dict:
    """
    识别单张题目截图。

    参数：
        image_path:   题目截图路径
        image_out_dir: 裁剪图片（题目插图 / 兜底公式图）保存目录
        log:          日志回调函数（用于向前端推送进度）

    返回：
        {"elements": [{"type": "text" | "formula" | "image", ...}, ...]}
    """
    p2t = get_pix2text()

    image_path = str(image_path)
    log(f"开始识别：{os.path.basename(image_path)}")

    # recognize_page：一次性完成版面分析、文字识别、公式初识别
    page = p2t.recognize_page(image_path)
    page_elements = page.elements if hasattr(page, "elements") else list(page)

    src_img = Image.open(image_path).convert("RGB")
    Path(image_out_dir).mkdir(parents=True, exist_ok=True)

    elements = []
    image_counter = [0]  # 兜底公式图片计数

    # 插图框预去重：插图带大框 + 单图小框的重叠/嵌套情况只留小框
    _fig_raw = []
    for _it in page_elements:
        _t = getattr(getattr(_it, "type", ""), "name", str(getattr(_it, "type", ""))).upper()
        if _t in ("FIGURE", "TABLE"):
            _b = getattr(_it, "box", None)
            _fig_raw.append([int(v) for v in _b[:4]] if _b is not None
                            else list(_bbox_from_position(_it["position"])))
    _fig_kept = _dedup_figure_boxes(_fig_raw)
    if len(_fig_kept) < len(_fig_raw):
        log(f"  插图检测框去重：{len(_fig_raw)} → {len(_fig_kept)}")
    # 回收被丢大框中未被小框覆盖的区域（四宫格只检测出部分小图时防丢图）
    _fig_recovered = recover_dropped_figures(
        [b for b in _fig_raw if b not in _fig_kept], _fig_kept
    ) if len(_fig_kept) < len(_fig_raw) else []

    for item in page_elements:
        raw_type = getattr(item, "type", "")
        item_type = getattr(raw_type, "name", str(raw_type)).upper()
        text = (getattr(item, "text", "") or "").strip()

        if item_type in ("FIGURE", "TABLE"):
            # 图片块 / 表格块：直接裁剪保存（被去重丢弃的框跳过）
            box = getattr(item, "box", None)
            if box is not None:
                x1, y1, x2, y2 = (int(v) for v in box[:4])
            else:
                x1, y1, x2, y2 = _bbox_from_position(item["position"])
            if [x1, y1, x2, y2] not in _fig_kept:
                continue
            image_counter[0] += 1
            name = f"figure_{image_counter[0]}.png"
            crop_path = _crop_and_save(src_img, (x1, y1, x2, y2), image_out_dir, name)
            elements.append({"type": "image", "text": "", "image_path": crop_path,
                             "box": [x1, y1, x2, y2],
                             "fig_kind": "table" if item_type == "TABLE" else "figure"})
            log(f"  裁剪出图片块：{name}")

        elif "FORMULA" in item_type:
            # 独立公式块
            if not text:
                continue
            box = getattr(item, "box", None)
            box = tuple(int(v) for v in box[:4]) if box is not None else _bbox_from_position(item["position"])
            image_counter[0] += 1
            name = f"formula_{image_counter[0]}.png"
            result = resolve_formula(src_img, box, image_out_dir, name, text, True, log)
            if result["kind"] == "latex":
                latex = result["text"]
                if "$" not in latex:
                    latex = f"$${latex}$$"
                elements.append({"type": "formula", "text": latex, "image_path": None,
                                 "box": list(box)})
            else:
                elements.append({"type": "image", "text": "", "image_path": result["path"],
                                 "box": list(box)})

        elif item_type in ("ABANDONED", "IGNORED"):
            # 页眉页脚等被丢弃区域，跳过
            continue

        else:
            # 普通文字元素：逐个公式走识别管线
            if getattr(item, "meta", None):
                text_el, formula_images = _rebuild_text_element(
                    item, src_img, image_out_dir, image_counter, log
                )
                if text_el:
                    elements.append(text_el)
                # 兜底图片紧跟在该段文字之后（最终位于题目下方）
                elements.extend(formula_images)
            elif text:
                box = getattr(item, "box", None)
                elements.append({"type": "text", "text": text, "image_path": None,
                                 "box": [int(v) for v in box[:4]] if box is not None else None})

    # 补充裁剪回收的区域（去重时丢的大框里未被小框覆盖的图）
    for _rb in _fig_recovered:
        image_counter[0] += 1
        name = f"figure_{image_counter[0]}.png"
        crop_path = _crop_and_save(src_img, tuple(_rb), image_out_dir, name, left_extra=0)
        elements.append({"type": "image", "text": "", "image_path": crop_path,
                         "box": list(_rb), "fig_kind": "figure"})
        log(f"  回收大框未覆盖区域，补裁插图：{name}")

    image_count = sum(1 for e in elements if e["type"] == "image")
    log(f"识别完成：共 {len(elements)} 个元素（含 {image_count} 张图片）")
    return {"elements": elements}
