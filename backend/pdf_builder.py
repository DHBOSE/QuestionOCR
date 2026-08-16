# -*- coding: utf-8 -*-
r"""
pdf_builder.py —— docx 转 PDF

Word 中生成的公式是 OMML，只有真正的排版引擎才能高保真渲染为 PDF，
因此不引入 LaTeX / HTML 重排版，直接驱动本机已安装的办公软件转换。

转换器按优先级依次尝试（全部失败则抛出带中文说明的 RuntimeError）：
1. Microsoft Word（COM 自动化，保真度最高）
2. WPS 文字（COM 自动化，接口与 Word 兼容）
3. LibreOffice（headless 命令行）

注意：转换在后台线程中执行，COM 需要 pythoncom.CoInitialize()；
使用 DispatchEx 新建独立实例，绝不干扰用户正在使用的 Word/WPS 窗口。
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# LibreOffice 常见安装位置
_LIBREOFFICE_CANDIDATES = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]

_CONVERT_TIMEOUT = 120  # 单次转换超时（秒）


class _ConverterUnavailable(Exception):
    """该转换器在本机不可用，尝试下一个。"""


# ---------------------------------------------------------------------------
# 转换器 1 / 2：Microsoft Word / WPS 文字（COM 自动化）
# ---------------------------------------------------------------------------
def _convert_with_com(docx_path: Path, pdf_path: Path, progid: str, log) -> None:
    """通过 COM 自动化驱动 Word / WPS 把 docx 另存为 PDF。"""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        raise _ConverterUnavailable("未安装 pywin32")

    pythoncom.CoInitialize()  # 后台线程使用 COM 前必须初始化
    app = None
    doc = None
    try:
        try:
            app = win32com.client.DispatchEx(progid)  # 独立实例，不碰用户窗口
        except Exception:
            raise _ConverterUnavailable(f"{progid} COM 不可用")
        app.Visible = False
        try:
            app.DisplayAlerts = 0  # 屏蔽激活 / 兼容提示弹窗
        except Exception:
            pass
        doc = app.Documents.Open(str(docx_path), ReadOnly=True)
        # 17 = wdExportFormatPDF；ExportAsFixedFormat 比 SaveAs2 更通用（WPS 也支持）
        doc.ExportAsFixedFormat(str(pdf_path), 17)
        doc.Close(False)
        doc = None
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()
    if not pdf_path.exists():
        raise RuntimeError(f"{progid} 转换结束但未生成 PDF 文件")


# ---------------------------------------------------------------------------
# 转换器 3：LibreOffice headless
# ---------------------------------------------------------------------------
def _find_soffice() -> str | None:
    for p in _LIBREOFFICE_CANDIDATES:
        if Path(p).exists():
            return p
    return shutil.which("soffice")


def _convert_with_libreoffice(docx_path: Path, pdf_path: Path, log) -> None:
    soffice = _find_soffice()
    if not soffice:
        raise _ConverterUnavailable("未检测到 LibreOffice")
    # 独立的用户配置目录：避免与用户正在运行的 LibreOffice 实例抢配置锁
    profile = Path(tempfile.mkdtemp(prefix="lo_profile_"))
    try:
        out_dir = pdf_path.parent
        result = subprocess.run(
            [
                soffice, "--headless", "--norestore",
                f"-env:UserInstallation=file:///{profile.as_posix()}",
                "--convert-to", "pdf:writer_pdf_Export",
                "--outdir", str(out_dir), str(docx_path),
            ],
            capture_output=True, text=True, timeout=_CONVERT_TIMEOUT,
            encoding="utf-8", errors="replace",
        )
        produced = out_dir / (docx_path.stem + ".pdf")
        if result.returncode != 0 or not produced.exists():
            raise RuntimeError(f"LibreOffice 转换失败：{result.stdout.strip()} {result.stderr.strip()}".strip())
        if produced != pdf_path:
            produced.replace(pdf_path)
    finally:
        shutil.rmtree(profile, ignore_errors=True)


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def docx_to_pdf(docx_path: str, pdf_path: str, log=logger.info) -> None:
    """
    把 docx 转换为 PDF，依次尝试 Word → WPS → LibreOffice。
    全部失败时抛出 RuntimeError（说明各转换器的失败原因）。
    """
    docx_path = Path(docx_path)
    pdf_path = Path(pdf_path)
    errors = []
    converters = [
        ("Microsoft Word", lambda: _convert_with_com(docx_path, pdf_path, "Word.Application", log)),
        ("WPS 文字", lambda: _convert_with_com(docx_path, pdf_path, "wps.Application", log)),
        ("WPS 文字（旧版接口）", lambda: _convert_with_com(docx_path, pdf_path, "KWPS.Application", log)),
        ("LibreOffice", lambda: _convert_with_libreoffice(docx_path, pdf_path, log)),
    ]
    for name, convert in converters:
        try:
            log(f"正在用 {name} 生成 PDF……")
            convert()
            log(f"PDF 生成成功（{name}）")
            return
        except _ConverterUnavailable as e:
            logger.info("%s 不可用：%s", name, e)
        except Exception as e:
            errors.append(f"{name}：{e}")
            logger.warning("%s 转换失败：%s", name, e)
    detail = "；".join(errors) if errors else "本机未安装 Word / WPS / LibreOffice"
    raise RuntimeError(f"无法生成 PDF（{detail}）。可改为导出 Word 格式。")
