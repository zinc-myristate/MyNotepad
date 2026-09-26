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


def make_delta_note(backend, title='', content='', notebook_id=None):
    """建一篇**显式 delta 格式**的笔记。

    第 6 轮起 notes_create 默认 'md'（Markdown 文本），凡是测试 Quill 行为
    （工具栏、Delta 行内格式、嵌图、贴纸…）的用例都必须显式声明 format='delta'，
    否则内容会被 Markdown 编辑器接管，断言看到的是空编辑器。
    """
    note = backend.api.notes_create()
    nid = note['id']
    backend.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
    backend.conn.commit()
    fields = {'title': title, 'content': content}
    if notebook_id:
        fields['notebook_id'] = notebook_id
    backend.api.notes_update(nid, fields)
    return nid


def load_app_partial(monkeypatch, tmp_path):
    """加载 app.pyw 的「创建窗口之前」部分（含图像处理函数与 AppApi/api），不启动 GUI。

    ⚠️ 这一段里含 `Thread(target=_startup_maintenance, daemon=True).start()`
    （回收站超期清理 + 备份 + 空间回收）。测试里**必须让它只构造、不启动**：
    那条后台线程会拿 backend 的模块级 sqlite 连接（`check_same_thread=False`）直接读写，
    而 fixture 同时在换临时库、关连接 —— 实测在 CI 上触发原生崩溃
    （`Windows fatal exception: access violation`，栈顶是 `backend.purge_expired_trash`）。
    进程被 abort 后 pytest 缓冲的失败详情全部丢失，只剩一串进度点，排查成本极高。
    """
    monkeypatch.setenv('MYNOTEPAD_DATA_DIR', str(tmp_path))
    sys.modules.pop('backend', None)
    src = open(APP_PYW, encoding='utf-8').read()
    parts = src.split(APP_SPLIT_MARKER)
    assert len(parts) == 2, 'app.pyw 缺少「创建窗口」分节标记，conftest 需要同步更新'
    ns = {'__name__': 'app_partial', '__file__': APP_PYW}

    import threading as _threading

    real_thread = _threading.Thread

    class _NotStartedThread(real_thread):
        """保留构造行为，start() 变成空操作（测试里不允许有后台维护线程）"""

        def start(self):
            return None

    try:
        _threading.Thread = _NotStartedThread
        exec(compile(parts[0], 'app.pyw', 'exec'), ns)
    finally:
        _threading.Thread = real_thread
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
