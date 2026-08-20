# -*- coding: utf-8 -*-
"""
procutil.py —— 静默子进程工具

后端以 pythonw.exe 无窗口方式在后台运行，但 pandoc / soffice 等是控制台程序：
Windows 会给每个控制台子进程新分配一个控制台窗口，表现为黑色窗口不断闪现
（识别时每个公式都要调一次 pandoc 校验，闪得尤其多）。

统一从这里发起子进程：Windows 下加 CREATE_NO_WINDOW，彻底不弹窗；
macOS / Linux 没有该标志，自动退化为普通 subprocess.run。
"""

import subprocess
import sys

# Windows 专用创建标志：子进程不分配新的控制台窗口
_EXTRA_KWARGS: dict = {}
if sys.platform == "win32":
    _EXTRA_KWARGS["creationflags"] = subprocess.CREATE_NO_WINDOW


def run_quiet(*args, **kwargs) -> subprocess.CompletedProcess:
    """用法与 subprocess.run 完全相同，但 Windows 下不弹黑色控制台窗口。"""
    kwargs.update(_EXTRA_KWARGS)
    return subprocess.run(*args, **kwargs)
