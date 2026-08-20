# -*- coding: utf-8 -*-
r"""
doc_loader.py —— Word / PDF 文件直识别

两条路线：
- PDF：每页渲染成高清图片（PyMuPDF），走现有图片识别管线
  （试卷 PDF 的文字层公式是字体排版，抽出来是乱码，渲染成图走 OCR 才能拿到 LaTeX）；
- Word（.docx）：默认 Pandoc 直转 Markdown（文字公式零误差，OMML 公式自动转 LaTeX，
  内嵌图片提取到任务目录），再按题号切成一道道题；
  用户也可选 OCR 模式（docx → PDF → 页图 → 图片管线），用于"Word 里嵌的是题目截图"。

任何一步失败都抛带中文说明的 RuntimeError，由上层转为友好错误提示。
"""

import logging
import os
import re
from pathlib import Path

from procutil import run_quiet

logger = logging.getLogger(__name__)

PDF_RENDER_DPI = 180        # 页图渲染分辨率（兼顾识别精度与速度）
MAX_PDF_PAGES = 20          # 单次最多处理页数（防整本上传跑半小时）

# 题号行：1. / 1、/ 1．/ 1\.（pandoc 转义形式）/ 第1题 / （1）/ 4[2026江苏…]（题号紧跟年份来源标注）
_QUESTION_START_RE = re.compile(
    r"^\s*(?:第\s*\d{1,3}\s*题|\d{1,3}\s*\\?[.、．](?!\d)|[（(]\s*\d{1,3}\s*[)）]"
    r"|\d{1,3}\s*\\?\[(?:19|20)\d{2})"
)
# 本工具导出的 Word 再导入时，"## 题目 N" / "## 第 N 题" 标题行是导出产物，切题时丢弃
_EXPORT_TITLE_RE = re.compile(r"^\s*#{1,6}\s*(?:题目\s*\d+|第\s*\d+\s*题)\s*$")


def parse_page_range(spec: str, total: int) -> list:
    """
    解析页码范围字符串（"1-3,5,7-9"，1 起始）为 0 起始页码列表。
    空字符串 = 全部页。非法段抛 ValueError。
    """
    spec = (spec or "").strip()
    if not spec:
        return list(range(total))
    pages = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d{1,4})(?:\s*[-–~]\s*(\d{1,4}))?", part)
        if not m:
            raise ValueError(f"页码范围格式错误：{part!r}（正确示例：1-5,8）")
        a = int(m.group(1))
        b = int(m.group(2) or a)
        if a < 1 or b < a:
            raise ValueError(f"页码范围非法：{part!r}")
        pages.extend(range(a - 1, min(b, total)))
    # 去重保序
    seen, out = set(), []
    for p in pages:
        if 0 <= p < total and p not in seen:
            seen.add(p)
            out.append(p)
    if not out:
        raise ValueError("页码范围超出文档页数")
    return out


def pdf_to_images(pdf_path: str, out_dir: str, page_spec: str = "", log=logger.info) -> list:
    """把 PDF 指定页渲染成 PNG，返回页图路径列表（按页码顺序）。"""
    import pymupdf

    pdf_path = str(pdf_path)
    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        raise RuntimeError(f"PDF 打开失败：{e}")
    if doc.needs_pass:
        doc.close()
        raise RuntimeError("PDF 已加密，请先解除密码保护后再上传。")

    try:
        pages = parse_page_range(page_spec, doc.page_count)
    except ValueError as e:
        doc.close()
        raise RuntimeError(str(e))
    if len(pages) > MAX_PDF_PAGES:
        pages = pages[:MAX_PDF_PAGES]
        log(f"  PDF 页数较多，本次只处理前 {MAX_PDF_PAGES} 页")

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    paths = []
    zoom = PDF_RENDER_DPI / 72.0
    matrix = pymupdf.Matrix(zoom, zoom)
    for pno in pages:
        pix = doc[pno].get_pixmap(matrix=matrix, alpha=False)
        p = Path(out_dir) / f"page_{pno + 1:03d}.png"
        pix.save(str(p))
        paths.append(str(p))
    log(f"  PDF 已渲染 {len(paths)} 页为图片（{PDF_RENDER_DPI} DPI）")
    doc.close()
    return paths


def docx_to_pdf_pages(docx_path: str, out_dir: str, log=logger.info) -> list:
    """OCR 模式：docx → PDF（Word/WPS/LibreOffice 回退链）→ 页图。"""
    from pdf_builder import docx_to_pdf

    docx_path = str(docx_path)
    pdf_path = Path(out_dir) / "_docx2pdf.pdf"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    log("  Word OCR 模式：先转换为 PDF……")
    docx_to_pdf(docx_path, str(pdf_path), log=log)
    return pdf_to_images(str(pdf_path), out_dir, "", log=log)


def docx_to_questions(docx_path: str, task_dir: str, extract_name: str = "direct", log=logger.info) -> list:
    """
    直转模式：pandoc 把 docx 转成 Markdown（OMML 公式 → LaTeX，内嵌图片提取到
    任务目录下，Markdown 中引用相对 task_dir 的路径），再按题号行切成题目列表。
    extract_name 为媒体提取子目录名（同一任务传多个 Word 时用不同名字防重名覆盖）。
    返回 [(题目标签, Markdown)]；全文没有题号结构时整篇作为一道题。
    """
    from docx_builder import find_pandoc

    pandoc = find_pandoc()
    if pandoc is None:
        raise RuntimeError("未找到 Pandoc，无法解析 Word 文件。")

    docx_path = str(docx_path)
    task_dir = str(task_dir)
    extract_dir = Path(task_dir) / extract_name  # pandoc 会在其下再建 media/ 子目录
    extract_dir.mkdir(parents=True, exist_ok=True)
    md_path = extract_dir / "_docx_direct.md"
    cmd = [
        pandoc, "-f", "docx", "-t", "markdown",
        "--extract-media", str(extract_dir),  # 图片提取为 extract_dir/media/...
        "-o", str(md_path), docx_path,
    ]
    proc = run_quiet(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not md_path.exists():
        raise RuntimeError(f"Word 解析失败：{proc.stderr.strip()[:300] or '未知错误'}")

    md_text = md_path.read_text(encoding="utf-8")

    # pandoc 以 --extract-media 的入参原样写图片引用；传入的是绝对路径时，
    # md 里就是带反斜杠的绝对路径，前端预览与后续合成 docx 都无法使用，
    # 统一改写成相对 task_dir 的路径（前端 /task-files 与 pandoc 资源路径都基于任务目录）
    media_dir = extract_dir / "media"

    def _fix_image_ref(m: re.Match) -> str:
        alt, target = m.group(1), m.group(2).strip()
        if target.lower().startswith(("http://", "https://", "data:")):
            return m.group(0)
        name = re.split(r"[/\\]", target)[-1]
        cand = media_dir / name
        if name and cand.exists():
            rel = os.path.relpath(cand, task_dir).replace("\\", "/")
            return f"![{alt}]({rel})"
        return m.group(0)

    md_text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _fix_image_ref, md_text)

    # 按题号行切题
    blocks, buf = [], []
    for ln in md_text.split("\n"):
        if _EXPORT_TITLE_RE.match(ln):
            continue  # 本工具导出的"## 题目 N"标题行，导入时丢弃（合成时会重新加标题）
        if _QUESTION_START_RE.match(ln) and any(s.strip() for s in buf):
            blocks.append("\n".join(buf))
            buf = [ln]
        else:
            buf.append(ln)
    if any(s.strip() for s in buf):
        blocks.append("\n".join(buf))

    # 去掉空块与纯媒体块
    blocks = [b.strip() for b in blocks if b.strip()]
    if not blocks:
        raise RuntimeError("Word 文档内容为空或无法识别。")
    log(f"  Word 直转完成：切出 {len(blocks)} 道题（公式已转 LaTeX）")
    return [(f"{Path(docx_path).name} 第 {i} 题", b) for i, b in enumerate(blocks, 1)]
