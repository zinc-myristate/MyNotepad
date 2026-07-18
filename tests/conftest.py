# -*- coding: utf-8 -*-
"""pytest 基建：临时数据目录隔离的 backend fixture + app.pyw 局部加载 helper"""
import importlib
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP_PYW = os.path.join(PROJECT_ROOT, 'app.pyw')
APP_SPLIT_MARKER = '# ====== 创建窗口 ======'


@pytest.fixture
def backend_mod(tmp_path, monkeypatch):
    """全新导入 backend 模块，数据目录指向 pytest 临时目录（绝不碰真实数据）。

    不能用 importlib.reload：模块级 conn 和 _locked 二次包装会出问题，必须 pop 后重新 import。
    """
    monkeypatch.setenv('MYNOTEPAD_DATA_DIR', str(tmp_path))
    sys.modules.pop('backend', None)
    backend = importlib.import_module('backend')
    assert backend.DATA_DIR == str(tmp_path), '数据目录未被环境变量覆盖，拒绝继续（防止误碰真实数据）'
    yield backend
    try:
        backend.conn.close()  # Windows 文件锁：不关掉 tmp_path 清不掉
    except Exception:
        pass
    sys.modules.pop('backend', None)


@pytest.fixture
def api(backend_mod):
    return backend_mod.api


def load_app_partial(monkeypatch, tmp_path):
    """加载 app.pyw 的「创建窗口之前」部分（含图像处理函数与 AppApi/api），不启动 GUI。"""
    monkeypatch.setenv('MYNOTEPAD_DATA_DIR', str(tmp_path))
    sys.modules.pop('backend', None)
    src = open(APP_PYW, encoding='utf-8').read()
    parts = src.split(APP_SPLIT_MARKER)
    assert len(parts) == 2, 'app.pyw 缺少「创建窗口」分节标记，conftest 需要同步更新'
    ns = {'__name__': 'app_partial', '__file__': APP_PYW}
    exec(compile(parts[0], 'app.pyw', 'exec'), ns)
    return ns


@pytest.fixture
def app_ns(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    yield ns
    try:
        import backend as _b
        _b.conn.close()
    except Exception:
        pass
    sys.modules.pop('backend', None)
