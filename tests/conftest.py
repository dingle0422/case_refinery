"""pytest fixtures。

让 ``case_refinery`` 顶级包可以作为 ``case_refinery.xxx`` 导入：从本测试目录
往上回溯 2 层（``tests -> case_refinery -> 仓库根``），把仓库根加入 ``sys.path``。
"""

from __future__ import annotations

import os
import sys


_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CASE_REFINERY_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_REPO_ROOT = os.path.abspath(os.path.join(_CASE_REFINERY_DIR, ".."))

if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
