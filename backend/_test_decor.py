# -*- coding: utf-8 -*-
"""临时验证：doc_decor 页眉/页脚（文字+图片）与页面水印 → PDF → 页图渲染检查"""
import base64
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from docx_builder import markdown_to_docx
from doc_decor import apply_decorations, normalize_watermark, normalize_hf
from pdf_builder import docx_to_pdf

OUT = Path(__file__).parent / "temp" / "_decor_test"
OUT.mkdir(parents=True, exist_ok=True)

MD = """# 题目 1

如图所示，一个质量为 $m$ 的物体放在倾角为 $\\theta$ 的斜面上，求支持力大小。

A. $mg\\sin\\theta$

B. $mg\\cos\\theta$
"""

from PIL import Image, ImageDraw


def make_img(color, w=400, h=200):
    img = Image.new("RGBA", (w, h), color)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, w - 10, h - 10], outline=(255, 255, 255, 255), width=8)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


WM_IMG = make_img((200, 30, 30, 255))
LOGO = make_img((30, 80, 200, 255), w=200, h=200)  # 方形 logo

CASES = [
    # (名称, 页眉, 页脚, 水印)
    ("text", {"text": "某某中学物理试卷"}, {"text": "第 1 页 共 1 页"},
     {"type": "text", "text": "内部资料", "size": 60, "opacity": 0.35, "angle": -45}),
    ("image", {"text": "带图页眉", "image": LOGO, "size": 60},
     {"text": "", "image": LOGO, "size": 40},
     {"type": "image", "image": WM_IMG, "size": 60, "opacity": 0.35, "angle": -45}),
]

for name, header_raw, footer_raw, wm_raw in CASES:
    md_path = OUT / f"test_{name}.md"
    docx_path = OUT / f"test_{name}.docx"
    pdf_path = OUT / f"test_{name}.pdf"
    md_path.write_text(MD, encoding="utf-8")
    markdown_to_docx(str(md_path), str(docx_path), str(OUT), "仿宋")

    header = normalize_hf(header_raw)
    footer = normalize_hf(footer_raw)
    wm = normalize_watermark(wm_raw)
    apply_decorations(str(docx_path), header, footer, wm, "仿宋")

    # XML 合法性检查（所有改动过的部件都能被解析）
    import zipfile, xml.dom.minidom
    with zipfile.ZipFile(docx_path) as z:
        names = z.namelist()
        for n in ("word/document.xml", "word/header1.xml", "word/footer1.xml",
                  "word/_rels/document.xml.rels", "[Content_Types].xml"):
            xml.dom.minidom.parseString(z.read(n))
        if "word/_rels/header1.xml.rels" in names:
            xml.dom.minidom.parseString(z.read("word/_rels/header1.xml.rels"))
        if "word/_rels/footer1.xml.rels" in names:
            xml.dom.minidom.parseString(z.read("word/_rels/footer1.xml.rels"))

    docx_to_pdf(str(docx_path), str(pdf_path))

    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
    pix.save(str(OUT / f"page_{name}.png"))
    print(f"[OK] {name}: {pdf_path.name} {doc.page_count} 页")
    doc.close()

print("全部通过")
