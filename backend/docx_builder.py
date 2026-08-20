# -*- coding: utf-8 -*-
"""
docx_builder.py —— 调用 Pandoc 将 Markdown 转为 Word (.docx)

- Markdown 中的 LaTeX 公式（$...$ / $$...$$）会被 Pandoc 自动转为 Word 原生
  OMML 公式，在 Word 中可直接编辑。
- 图片会以嵌入方式写入 docx。
- 优先使用系统 PATH 中的 pandoc；找不到时尝试项目内置的便携版
  （backend/pandoc/pandoc.exe）。
"""

import re
import shutil
import zipfile
from pathlib import Path

from procutil import run_quiet

# 可选中文字体（写入 docx 的 w:eastAsia 属性），与前端下拉框保持一致
AVAILABLE_FONTS = ["仿宋", "黑体", "楷体", "宋体", "微软雅黑", "等线"]
DEFAULT_FONT = "仿宋"

# 项目内置便携版 Pandoc 的候选路径（Windows / macOS / Linux）
_LOCAL_PANDOC_CANDIDATES = [
    Path(__file__).parent / "pandoc" / "pandoc.exe",      # Windows 便携版
    Path(__file__).parent / "pandoc" / "bin" / "pandoc",  # macOS / Linux 便携版
]


def find_pandoc() -> str | None:
    """查找可用的 pandoc 可执行文件路径，找不到返回 None。"""
    # 1. 系统 PATH
    sys_pandoc = shutil.which("pandoc")
    if sys_pandoc:
        return sys_pandoc
    # 2. 项目内置便携版
    for candidate in _LOCAL_PANDOC_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return None


def set_east_asian_font(docx_path: str, font_name: str) -> None:
    """
    后处理 docx：把 styles.xml 中所有样式的 w:rFonts 的 w:eastAsia 属性
    统一改为指定中文字体，从而控制全文中文显示字体。

    注意：Pandoc 模板样式带有 w:eastAsiaTheme="minorEastAsia/majorEastAsia"，
    按 OOXML 规则主题属性优先级高于字面 w:eastAsia，必须一并删除，
    否则 Word 仍显示主题中文字体（等线），设置无效。
    同时修改 theme1.xml 的主题中文字体作为双保险。公式（OMML）字体不受影响。
    """
    docx_path = Path(docx_path)
    with zipfile.ZipFile(docx_path, "r") as zin:
        items = {name: zin.read(name) for name in zin.namelist()}

    # 1. styles.xml：删除 eastAsiaTheme，写入字面 eastAsia 字体
    styles = items.get("word/styles.xml")
    if styles is not None:
        text = styles.decode("utf-8")

        def _fix_rfonts(m: "re.Match") -> str:
            tag = m.group(0)
            tag = re.sub(r'\s*w:eastAsiaTheme="[^"]*"', "", tag)
            if "w:eastAsia=" in tag:
                return re.sub(r'w:eastAsia="[^"]*"', f'w:eastAsia="{font_name}"', tag)
            return tag[:-2] + f' w:eastAsia="{font_name}"/>'

        text = re.sub(r"<w:rFonts\b[^>]*/>", _fix_rfonts, text)
        items["word/styles.xml"] = text.encode("utf-8")

    # 2. theme1.xml：把主题中文字体也改为指定字体（兜底，防止个别样式遗漏）
    theme = items.get("word/theme/theme1.xml")
    if theme is not None:
        t = theme.decode("utf-8")
        # 主题字体中 <a:ea typeface="..."/> 即 East Asian 字体
        t = re.sub(r'<a:ea typeface="[^"]*"', f'<a:ea typeface="{font_name}"', t)
        items["word/theme/theme1.xml"] = t.encode("utf-8")

    tmp_path = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items.items():
            zout.writestr(name, data)
    tmp_path.replace(docx_path)


def style_question_headings(docx_path: str) -> None:
    """
    后处理 docx：把 Heading 1/2 样式（"题目 N"标题）改为试卷风格——
    加粗、纯黑色、14pt。Pandoc 默认模板中标题是深蓝色 16pt 不加粗，
    不符合中文试卷排版习惯。
    """
    docx_path = Path(docx_path)
    with zipfile.ZipFile(docx_path, "r") as zin:
        items = {name: zin.read(name) for name in zin.namelist()}

    styles = items.get("word/styles.xml")
    if styles is None:
        return
    text = styles.decode("utf-8")

    def _fix_heading(m: "re.Match") -> str:
        block = m.group(0)
        # 颜色：去掉主题色属性，改为纯黑
        block = re.sub(r"<w:color\b[^>]*/>", '<w:color w:val="000000" />', block)
        # 字号：16pt → 14pt（w:sz 单位为半磅）
        block = re.sub(r'<w:sz w:val="\d+"', '<w:sz w:val="28"', block)
        block = re.sub(r'<w:szCs w:val="\d+"', '<w:szCs w:val="28"', block)
        # 加粗：rPr 里没有 w:b 则补上
        if "<w:b " not in block and "<w:b />" not in block and "<w:b/>" not in block:
            block = block.replace("<w:rPr>", "<w:rPr>\n      <w:b />", 1)
        return block

    text = re.sub(
        r'<w:style w:styleId="Heading[12]" w:type="paragraph">.*?</w:style>',
        _fix_heading, text, flags=re.DOTALL,
    )
    items["word/styles.xml"] = text.encode("utf-8")

    tmp_path = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items.items():
            zout.writestr(name, data)
    tmp_path.replace(docx_path)


def set_line_spacing(docx_path: str, line: int = 360) -> None:
    """
    后处理 docx：设置正文行距。line 单位为 1/240 倍（240=单倍，360=1.5 倍，
    480=双倍），lineRule=auto 表示倍数行距。
    做法：给 Normal 样式补一个带行距的 spacing，并给所有已有 spacing 但
    未设行距的样式补上相同行距（段前段后间距保持不变）。
    """
    docx_path = Path(docx_path)
    with zipfile.ZipFile(docx_path, "r") as zin:
        items = {name: zin.read(name) for name in zin.namelist()}

    styles = items.get("word/styles.xml")
    if styles is None:
        return
    text = styles.decode("utf-8")

    # 1. 所有已有 spacing 但未设行距的样式：补上行距属性
    def _add_line(m: "re.Match") -> str:
        tag = m.group(0)
        if "w:line=" in tag:
            return tag
        return tag[:-2] + f' w:line="{line}" w:lineRule="auto" />'

    text = re.sub(r"<w:spacing\b[^>]*/>", _add_line, text)

    # 2. Normal 样式没有 spacing（ Pandoc 默认模板如此 ）：补一个只有行距的
    def _fix_normal(m: "re.Match") -> str:
        block = m.group(0)
        if "<w:spacing" in block:
            return block
        return block.replace(
            "</w:style>",
            f'    <w:pPr>\n      <w:spacing w:line="{line}" w:lineRule="auto" />\n    </w:pPr>\n  </w:style>',
        )

    text = re.sub(
        r'<w:style [^>]*w:styleId="Normal"[^>]*>.*?</w:style>',
        _fix_normal, text, flags=re.DOTALL,
    )
    items["word/styles.xml"] = text.encode("utf-8")

    tmp_path = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items.items():
            zout.writestr(name, data)
    tmp_path.replace(docx_path)


def markdown_to_docx(
    md_path: str, docx_path: str, resource_dir: str, font_name: str = DEFAULT_FONT
) -> None:
    """
    调用 Pandoc 把 Markdown 文件转换为 docx，并按需设置中文字体。

    参数：
        md_path:      输入 Markdown 文件路径
        docx_path:    输出 docx 文件路径
        resource_dir: 图片等资源文件的搜索根目录（一般为任务临时目录）
        font_name:    中文字体（eastAsia），如 仿宋 / 黑体 / 楷体，默认仿宋

    异常：
        RuntimeError: 未安装 Pandoc 或转换失败时抛出，信息为中文说明。
    """
    pandoc = find_pandoc()
    if pandoc is None:
        raise RuntimeError(
            "未找到 Pandoc。请先安装 Pandoc（https://pandoc.org/installing.html），"
            "或将便携版解压到 backend/pandoc/ 目录后重试。"
        )

    cmd = [
        pandoc,
        str(md_path),
        "-f", "markdown+tex_math_dollars",  # 启用 $...$ 公式语法解析
        "-t", "docx",
        "-o", str(docx_path),
        "--resource-path", str(resource_dir),  # 让 Pandoc 能找到裁剪出的图片
    ]

    result = run_quiet(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise RuntimeError(f"Pandoc 转换失败：{result.stderr.strip() or '未知错误'}")

    if not Path(docx_path).exists():
        raise RuntimeError("Pandoc 执行完毕但未生成 docx 文件。")

    # 统一设置中文字体（不在白名单内则用默认字体，避免注入非法值）
    if font_name not in AVAILABLE_FONTS:
        font_name = DEFAULT_FONT
    set_east_asian_font(docx_path, font_name)

    # "题目 N"标题改为试卷风格（加粗、纯黑、14pt）
    style_question_headings(docx_path)

    # 正文 1.5 倍行距（试卷排版习惯，方便打印作答）
    set_line_spacing(docx_path)
