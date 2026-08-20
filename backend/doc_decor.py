# -*- coding: utf-8 -*-
r"""
doc_decor.py —— docx 版面装饰：页眉 / 页脚 / 页面水印

在 Pandoc 生成的 docx 上做 XML 级后处理（与 docx_builder 同一思路，直接改 zip 内 XML）：

- 页眉 / 页脚：新建 word/header1.xml、word/footer1.xml 部件，居中文字，
  也可带一张图片（如校徽 logo，内嵌 DrawingML 图片，大小可调）。
- 页面水印：在页眉中放入一个“衬于文字下方”的 VML 浮动图形（Word 官方水印做法），
  因此每页都会出现；PDF 由 docx 转换而来，水印自然带入。
  - 文字水印：先用 PIL 按所选字体渲染成透明底 PNG（VML textpath 中文会叠字，已弃用），
    不透明度直接烘焙进 alpha 通道。
  - 图片水印：PIL 把不透明度烘焙进 PNG alpha。
  两者统一走 VML imagedata 插入，支持 大小 / 不透明度 / 角度。

实测要点：VML 图形不带 <v:shapetype> 定义、不加 filled 属性时渲染最可靠。
"""

import base64
import io
import os
import re
import zipfile
from pathlib import Path

# 装饰图片在 word/media 下的固定文件名
_WM_IMAGE_NAME = "s2q_watermark.png"
_HEADER_IMAGE_NAME = "s2q_header_img.png"
_FOOTER_IMAGE_NAME = "s2q_footer_img.png"

# 参数边界（前端滑块与后端校验保持一致）
_SIZE_MIN, _SIZE_MAX = 10, 100          # 相对大小（%）
_OPACITY_MIN, _OPACITY_MAX = 0.05, 1.0  # 不透明度（仅水印）
_ANGLE_MIN, _ANGLE_MAX = -90.0, 90.0    # 旋转角度（度，仅水印）
_TEXT_MAXLEN = 30                        # 水印文字长度上限
_HF_TEXT_MAXLEN = 100                    # 页眉 / 页脚文字长度上限


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _decode_data_url(data_url: str) -> bytes | None:
    """解析 data:image/png|jpeg;base64,... 为图片字节，非法返回 None。"""
    m = re.match(r"^data:image/(png|jpe?g);base64,(.+)$", data_url or "", re.DOTALL)
    if not m:
        return None
    try:
        return base64.b64decode(m.group(2))
    except Exception:
        return None


def normalize_watermark(raw: dict | None) -> dict | None:
    """
    校验并规整前端传来的水印参数，返回 None 表示不加水印。
    输入字段：type(none/text/image) / text / image(dataURL) / size / opacity / angle
    """
    if not isinstance(raw, dict):
        return None
    wm_type = str(raw.get("type") or "none")
    if wm_type not in ("text", "image"):
        return None

    wm = {
        "type": wm_type,
        "size": _clamp(float(raw.get("size") or 50), _SIZE_MIN, _SIZE_MAX),
        "opacity": _clamp(float(raw.get("opacity") or 0.3), _OPACITY_MIN, _OPACITY_MAX),
        "angle": _clamp(float(raw.get("angle") if raw.get("angle") is not None else -45),
                        _ANGLE_MIN, _ANGLE_MAX),
    }
    if wm_type == "text":
        text = str(raw.get("text") or "").strip()[:_TEXT_MAXLEN]
        if not text:
            return None
        wm["text"] = text
    else:
        image_bytes = _decode_data_url(str(raw.get("image") or ""))
        if image_bytes is None:
            return None
        wm["image_bytes"] = image_bytes
    return wm


def normalize_hf(raw) -> dict | None:
    """
    校验并规整页眉 / 页脚参数，返回 None 表示不加。
    兼容两种输入：纯字符串（仅文字）或 { text, image(dataURL), size } 对象。
    返回：{ text, image_bytes|None, size(10-100) }
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()[:_HF_TEXT_MAXLEN]
        return {"text": text, "image_bytes": None, "size": 50} if text else None
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()[:_HF_TEXT_MAXLEN]
        size = int(_clamp(float(raw.get("size") or 50), _SIZE_MIN, _SIZE_MAX))
        image_bytes = _decode_data_url(str(raw.get("image") or ""))
        if not text and image_bytes is None:
            return None
        return {"text": text, "image_bytes": image_bytes, "size": size}
    return None


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


# ---------------------------------------------------------------------------
# 水印图形（VML）生成
# ---------------------------------------------------------------------------
# 水印段落模板。
# 实测：不要带 <v:shapetype> 定义——带上之后 Word 会把 imagedata 填充渲染成
# 近乎透明的鬼影（原因不明，但无 shapetype 时图片与文字水印均正常），
# 也不要给图形加 filled 属性，保持与验证通过的形态一致。
_VML_SHAPE_WRAP = (
    '<w:p><w:pPr><w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
    '<w:r><w:pict>{shape}</w:pict></w:r></w:p>'
)


# 浮动定位公共 style：页面居中、衬于文字下方（z-index 为负）
def _float_style(width_pt: float, height_pt: float, angle: float) -> str:
    return (
        "position:absolute;margin-left:0;margin-top:0;"
        f"width:{width_pt:.1f}pt;height:{height_pt:.1f}pt;rotation:{angle:g};"
        "z-index:-251654144;"
        "mso-position-horizontal:center;mso-position-horizontal-relative:margin;"
        "mso-position-vertical:center;mso-position-vertical-relative:margin"
    )


# 中文字体名 → Windows 字体文件（用于把文字水印渲染为图片）
_FONT_FILES = {
    "仿宋": "simfang.ttf",
    "黑体": "simhei.ttf",
    "楷体": "simkai.ttf",
    "宋体": "simsun.ttc",
    "微软雅黑": "msyh.ttc",
    "等线": "Deng.ttf",
}
_FONT_FALLBACKS = ["msyh.ttc", "simsun.ttc", "simhei.ttf", "arial.ttf"]


def _find_font_file(font_name: str) -> str | None:
    fonts_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
    for name in [_FONT_FILES.get(font_name), *_FONT_FALLBACKS]:
        if not name:
            continue
        p = fonts_dir / name
        if p.exists():
            return str(p)
    return None


def _prepare_text_watermark_image(wm: dict, font_name: str) -> tuple[bytes, float, float]:
    """
    文字水印：用 PIL 把文字渲染成透明底 PNG（浅灰色、不透明度烘焙进 alpha），
    之后与图片水印共用同一条 imagedata 插入路径（该路径已验证在 Word/WPS 正常）。
    返回 (png_bytes, width_pt, height_pt)。
    """
    from PIL import Image, ImageDraw, ImageFont

    text = wm["text"]
    font_px = max(24, int(wm["size"] * 2.2))  # 50% → 110px，保证渲染清晰度
    font_file = _find_font_file(font_name)
    font = ImageFont.truetype(font_file, font_px) if font_file else ImageFont.load_default()

    # 量取文字实际包围盒，紧贴文字建图（留少量边距防裁切）
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    bbox = probe.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = max(4, int(font_px * 0.2))
    img = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=(150, 150, 150, 255))

    # 不透明度烘焙进 alpha 通道
    alpha = img.getchannel("A").point(lambda a: int(a * wm["opacity"]))
    img.putalpha(alpha)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    width_pt = wm["size"] * 5.0
    height_pt = width_pt * img.height / img.width
    return buf.getvalue(), width_pt, height_pt


def _prepare_watermark_image(wm: dict) -> tuple[bytes, float, float]:
    """
    图片水印预处理：把不透明度烘焙进 PNG alpha 通道。
    返回 (png_bytes, width_pt, height_pt)——按图片原始宽高比和相对大小算出版面尺寸。
    """
    from PIL import Image

    img = Image.open(io.BytesIO(wm["image_bytes"])).convert("RGBA")
    alpha = img.getchannel("A").point(lambda a: int(a * wm["opacity"]))
    img.putalpha(alpha)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    width_pt = wm["size"] * 5.0
    ratio = img.height / img.width if img.width else 1.0
    height_pt = width_pt * ratio
    return buf.getvalue(), width_pt, height_pt


def _image_watermark_shape(width_pt: float, height_pt: float, angle: float) -> str:
    """
    图片水印：VML imagedata，引用页眉部件关系中的 rIdWm。
    注意：不带 shapetype 定义、不加 filled 属性（实测这样渲染最可靠）。
    """
    return (
        '<v:shape id="S2QWatermark" o:spid="_x0000_s1025" type="#_x0000_t75" '
        f'style="{_float_style(width_pt, height_pt, angle)}" '
        'o:allowincell="f" stroked="f">'
        '<v:imagedata r:id="rIdWm" o:title="s2q_watermark"/>'
        "<w10:wrap type=\"none\"/><w10:anchorlock/>"
        "</v:shape>"
    )


# ---------------------------------------------------------------------------
# 页眉 / 页脚部件 XML
# ---------------------------------------------------------------------------
def _prepare_hf_image(spec: dict) -> tuple[bytes, float, float]:
    """
    页眉 / 页脚图片：统一转 PNG（保留原图不透明度），
    大小参数 10-100 映射为图片高度 6-60pt，宽度按原图宽高比。
    返回 (png_bytes, width_pt, height_pt)。
    """
    from PIL import Image

    img = Image.open(io.BytesIO(spec["image_bytes"])).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    height_pt = spec["size"] * 0.6
    ratio = img.width / img.height if img.height else 1.0
    width_pt = height_pt * ratio
    return buf.getvalue(), width_pt, height_pt


def _inline_image_run(rid: str, name: str, width_pt: float, height_pt: float) -> str:
    """内嵌图片 run（DrawingML wp:inline），尺寸单位为 EMU（1pt = 12700 EMU）。"""
    cx = int(width_pt * 12700)
    cy = int(height_pt * 12700)
    return (
        '<w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="901" name="{_xml_escape(name)}"/>'
        '<wp:cNvGraphicFramePr/>'
        "<a:graphic>"
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        "<pic:pic>"
        "<pic:nvPicPr>"
        f'<pic:cNvPr id="0" name="{_xml_escape(name)}"/><pic:cNvPicPr/>'
        "</pic:nvPicPr>"
        "<pic:blipFill>"
        f'<a:blip r:embed="{rid}"/>'
        "<a:stretch><a:fillRect/></a:stretch>"
        "</pic:blipFill>"
        "<pic:spPr>"
        f'<a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        "</pic:spPr>"
        "</pic:pic>"
        "</a:graphicData>"
        "</a:graphic>"
        "</wp:inline>"
        "</w:drawing></w:r>"
    )


def _hf_paragraph(spec: dict, font_name: str, img_rid: str | None = None,
                  img_dims: tuple | None = None) -> str:
    """页眉 / 页脚段落：右对齐（页眉即在每页右上角），图片（可选）+ 文字（可选），文字 9pt。"""
    runs = []
    if img_rid and img_dims:
        runs.append(_inline_image_run(img_rid, "s2q_hf.png", *img_dims))
        if spec["text"]:
            # 图片与文字之间空两格
            runs.append('<w:r><w:t xml:space="preserve">  </w:t></w:r>')
    if spec["text"]:
        runs.append(
            f'<w:r><w:rPr><w:rFonts w:eastAsia="{_xml_escape(font_name)}"/>'
            '<w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr>'
            f'<w:t xml:space="preserve">{_xml_escape(spec["text"])}</w:t></w:r>'
        )
    return '<w:p><w:pPr><w:jc w:val="right"/></w:pPr>' + "".join(runs) + "</w:p>"


_HEADER_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:v="urn:schemas-microsoft-com:vml" '
    'xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:w10="urn:schemas-microsoft-com:office:word" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">{body}</w:hdr>'
)

_FOOTER_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">{body}</w:ftr>'
)


def _image_rels_xml(entries: list) -> bytes:
    """生成部件关系文件：entries 为 [(rId, media文件名), ...] 的图片关系列表。"""
    body = "".join(
        f'<Relationship Id="{rid}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
        f'Target="media/{target}"/>'
        for rid, target in entries
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{body}</Relationships>"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def apply_decorations(
    docx_path: str,
    header: dict | None = None,
    footer: dict | None = None,
    watermark: dict | None = None,
    font_name: str = "仿宋",
) -> None:
    """
    给 docx 加页眉 / 页脚 / 页面水印（按需组合，全部为空则不动文件）。

    参数：
        docx_path: 目标 docx（原地修改）
        header:    normalize_hf 规整后的页眉参数（文字和/或图片），None 不加
        footer:    normalize_hf 规整后的页脚参数，None 不加
        watermark: normalize_watermark 规整后的水印参数，None 不加
        font_name: 页眉页脚与水印文字使用的中文字体
    """
    need_header = header is not None or watermark is not None
    need_footer = footer is not None
    if not need_header and not need_footer:
        return

    docx_path = Path(docx_path)
    with zipfile.ZipFile(docx_path, "r") as zin:
        items = {name: zin.read(name) for name in zin.namelist()}

    document = items.get("word/document.xml")
    doc_rels = items.get("word/_rels/document.xml.rels")
    content_types = items.get("[Content_Types].xml")
    if document is None or doc_rels is None or content_types is None:
        raise RuntimeError("docx 结构不完整，无法添加页眉页脚")

    doc_text = document.decode("utf-8")
    rels_text = doc_rels.decode("utf-8")
    ct_text = content_types.decode("utf-8")

    def ensure_png_content_type():
        nonlocal ct_text
        if 'Extension="png"' not in ct_text:
            ct_text = ct_text.replace(
                "</Types>",
                '<Default Extension="png" ContentType="image/png"/></Types>',
            )

    # document.xml.rels 中已占用的 rId，向后取新号
    used = [int(m) for m in re.findall(r'Id="rId(\d+)"', rels_text)]
    next_rid = (max(used) + 1) if used else 1

    sect_refs = ""  # 待插入 sectPr 的引用

    # ---- 页眉（可含页面水印图形 + 图片 + 文字）----
    if need_header:
        body_parts = []
        header_rels = []  # 页眉部件的图片关系

        if watermark is not None:
            # 文字 / 图片水印统一渲染为半透明 PNG，走同一条 imagedata 插入路径
            if watermark["type"] == "text":
                png, w_pt, h_pt = _prepare_text_watermark_image(watermark, font_name)
            else:
                png, w_pt, h_pt = _prepare_watermark_image(watermark)
            items[f"word/media/{_WM_IMAGE_NAME}"] = png
            header_rels.append(("rIdWm", _WM_IMAGE_NAME))
            body_parts.append(
                _VML_SHAPE_WRAP.format(shape=_image_watermark_shape(w_pt, h_pt, watermark["angle"]))
            )
            ensure_png_content_type()

        if header is not None:
            img_rid = img_dims = None
            if header["image_bytes"] is not None:
                png, w_pt, h_pt = _prepare_hf_image(header)
                items[f"word/media/{_HEADER_IMAGE_NAME}"] = png
                header_rels.append(("rIdImg", _HEADER_IMAGE_NAME))
                img_rid, img_dims = "rIdImg", (w_pt, h_pt)
                ensure_png_content_type()
            body_parts.append(_hf_paragraph(header, font_name, img_rid, img_dims))
        else:
            body_parts.append("<w:p/>")  # 只有水印时页眉正文留一个空段落

        if header_rels:
            items["word/_rels/header1.xml.rels"] = _image_rels_xml(header_rels)
        items["word/header1.xml"] = _HEADER_TEMPLATE.format(body="".join(body_parts)).encode("utf-8")
        rid = f"rId{next_rid}"
        next_rid += 1
        rels_text = rels_text.replace(
            "</Relationships>",
            '<Relationship Id="{rid}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" '
            'Target="header1.xml"/></Relationships>'.format(rid=rid),
        )
        if 'PartName="/word/header1.xml"' not in ct_text:
            ct_text = ct_text.replace(
                "</Types>",
                '<Override PartName="/word/header1.xml" ContentType='
                '"application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>',
            )
        sect_refs += f'<w:headerReference w:type="default" r:id="{rid}"/>'

    # ---- 页脚（可含图片 + 文字）----
    if need_footer:
        img_rid = img_dims = None
        if footer["image_bytes"] is not None:
            png, w_pt, h_pt = _prepare_hf_image(footer)
            items[f"word/media/{_FOOTER_IMAGE_NAME}"] = png
            items["word/_rels/footer1.xml.rels"] = _image_rels_xml([("rIdImg", _FOOTER_IMAGE_NAME)])
            img_rid, img_dims = "rIdImg", (w_pt, h_pt)
            ensure_png_content_type()

        items["word/footer1.xml"] = _FOOTER_TEMPLATE.format(
            body=_hf_paragraph(footer, font_name, img_rid, img_dims)
        ).encode("utf-8")
        rid = f"rId{next_rid}"
        next_rid += 1
        rels_text = rels_text.replace(
            "</Relationships>",
            '<Relationship Id="{rid}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" '
            'Target="footer1.xml"/></Relationships>'.format(rid=rid),
        )
        if 'PartName="/word/footer1.xml"' not in ct_text:
            ct_text = ct_text.replace(
                "</Types>",
                '<Override PartName="/word/footer1.xml" ContentType='
                '"application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/></Types>',
            )
        sect_refs += f'<w:footerReference w:type="default" r:id="{rid}"/>'

    # ---- document.xml：把引用插到 sectPr 最前面（schema 要求引用在 pgSz 等之前）----
    m = re.search(r"<w:sectPr\b[^>]*>", doc_text)
    if m is None:
        raise RuntimeError("docx 中未找到节属性（sectPr），无法添加页眉页脚")
    doc_text = doc_text[: m.end()] + sect_refs + doc_text[m.end():]

    items["word/document.xml"] = doc_text.encode("utf-8")
    items["word/_rels/document.xml.rels"] = rels_text.encode("utf-8")
    items["[Content_Types].xml"] = ct_text.encode("utf-8")

    tmp_path = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in items.items():
            zout.writestr(name, data)
    tmp_path.replace(docx_path)
