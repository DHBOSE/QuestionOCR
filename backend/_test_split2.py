# -*- coding: utf-8 -*-
import sys, io, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"E:\PICTURE-TO-WORD\Screenshot2QuestionWord\backend")
from pathlib import Path
from splitter import split_image

out = r"E:\PICTURE-TO-WORD\_split_test_out"
shutil.rmtree(out, ignore_errors=True)
parts = split_image(r"E:\PICTURE-TO-WORD\_synthetic_two_questions.png", out, log=print)
print("拆分结果:", parts)
for p, qnum in parts:
    from PIL import Image
    print(p, "题号:", qnum, Image.open(p).size)

# 单题页面应原样返回
parts2 = split_image(r"E:\PICTURE-TO-WORD\必刷题第四页第六题.png", out, log=print)
print("单题页结果:", parts2)
