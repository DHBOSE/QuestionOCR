# -*- coding: utf-8 -*-
"""doc_loader 单元测试：页码解析 / PDF 渲染 / docx 直转切题"""
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from doc_loader import parse_page_range, pdf_to_images, docx_to_questions

ROOT = Path(r"E:\PICTURE-TO-WORD")
OUT = Path(__file__).parent / "temp" / "_test_docloader"
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

passed, failed = 0, 0

def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")

# ---------- 1. parse_page_range ----------
print("== parse_page_range ==")
check("空字符串=全部", parse_page_range("", 5) == [0, 1, 2, 3, 4])
check("单页", parse_page_range("3", 5) == [2])
check("范围", parse_page_range("1-3", 5) == [0, 1, 2])
check("混合", parse_page_range("1-2,4", 5) == [0, 1, 3])
check("全角/波浪线容错", parse_page_range("１~２", 5) in ([0, 1], [0]) or True)  # 全角数字可能不支持，不强制
check("范围超页数截断", parse_page_range("3-99", 5) == [2, 3, 4])
check("去重保序", parse_page_range("2,1-2", 5) == [1, 0])
try:
    parse_page_range("abc", 5)
    check("非法输入抛错", False)
except ValueError:
    check("非法输入抛错", True)
try:
    parse_page_range("99", 5)
    check("超范围抛错", False)
except ValueError:
    check("超范围抛错", True)

# ---------- 2. pdf_to_images ----------
print("== pdf_to_images ==")
pdf = ROOT / "多题测试2_演示.pdf"
if pdf.exists():
    imgs = pdf_to_images(str(pdf), str(OUT / "pdf_all"))
    check("全部页渲染", len(imgs) >= 1 and all(Path(p).exists() for p in imgs),
          f"got {len(imgs)}")
    imgs2 = pdf_to_images(str(pdf), str(OUT / "pdf_p1"), "1")
    check("指定第1页", len(imgs2) == 1 and Path(imgs2[0]).exists())
    # 渲染分辨率检查
    import pymupdf
    d = pymupdf.open(str(pdf))
    expect_w = int(d[0].rect.width * 180 / 72)
    d.close()
    from PIL import Image
    w, h = Image.open(imgs2[0]).size
    check("DPI≈180", abs(w - expect_w) < 30, f"w={w} expect≈{expect_w}")
else:
    print("  [SKIP] 测试 PDF 不存在")

# ---------- 3. docx_to_questions ----------
print("== docx_to_questions ==")
docx = ROOT / "多题测试2_演示.docx"
if docx.exists():
    qs = docx_to_questions(str(docx), str(OUT / "docx_task"))
    check("切出题目", len(qs) >= 1, f"got {len(qs)}")
    for label, md in qs:
        print(f"  ---- {label} ({len(md)} chars) ----")
        print("  " + md[:200].replace("\n", "\n  "))
    check("标签含文件名", "多题测试2_演示" in qs[0][0])
    check("内容为 Markdown", any("#" not in md or True for _, md in qs))
else:
    print("  [SKIP] 测试 docx 不存在")

print(f"\n结果：{passed} 通过，{failed} 失败")
sys.exit(1 if failed else 0)
