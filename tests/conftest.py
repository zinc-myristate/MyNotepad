# -*- coding: utf-8 -*-
"""pytest 基建：临时数据目录隔离的 backend fixture + app.pyw 局部加载 helper"""
import importlib
import os
import sys
import time

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

    ⚠️ 拦的是 **`Thread.start` 方法**，不是 `threading.Thread` 这个类本身。`desktop.py` 里
    `class GlobalHotkey(threading.Thread)` / `class ReminderWatcher(threading.Thread)`
    是 **import 时**求值基类的，而这个 import 正落在下面 exec 的窗口内：早先"换掉整个
    Thread 类"的写法会让它们永久继承那个假基类 —— `GlobalHotkey.start()` 变成空操作，
    `join()` 抛 `cannot join thread before it is started`（全量跑时 desktop 已被导入过，
    掩盖了它；单跑 `test_window_persistence.py` 才暴露）。
    """
    monkeypatch.setenv('MYNOTEPAD_DATA_DIR', str(tmp_path))
    sys.modules.pop('backend', None)
    src = open(APP_PYW, encoding='utf-8').read()
    parts = src.split(APP_SPLIT_MARKER)
    assert len(parts) == 2, 'app.pyw 缺少「创建窗口」分节标记，conftest 需要同步更新'
    ns = {'__name__': 'app_partial', '__file__': APP_PYW}

    import threading as _threading

    real_start = _threading.Thread.start

    def _start_unless_maintenance(self):
        """放行其它线程，只让「启动维护线程」这一次 start() 变成空操作"""
        if getattr(getattr(self, '_target', None), '__name__', '') == '_startup_maintenance':
            return None
        return real_start(self)

    try:
        _threading.Thread.start = _start_unless_maintenance
        exec(compile(parts[0], 'app.pyw', 'exec'), ns)
    finally:
        _threading.Thread.start = real_start
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


def wait_for_js(window, expression, expected=None, timeout=20.0, interval=0.25):
    """轮询 JS 表达式，直到等于 expected（expected=None 时直到取到真值）或超时。

    返回**最后一次取到的值**，超时也返回 —— 让调用方的 assert 给出原本的报错信息，
    而不是抛一个与真因无关的超时异常。

    ⚠️ 为什么必须有它：无头 WebView2 在 CI 上比开发机慢得多，而 e2e 里大量用
    "sleep 固定秒数 + 一次性取值"。那等于把"断言行为"变成"断言机器有多快"——实测
    `test_format_badge_e2e.py::test_badge_converts_to_markdown_then_restores` 就在 CI 上
    偶发读到还没刷新完的 'MD'（本地怎么跑都过、上一轮 CI 也是绿的）。
    等条件成立再断言才是稳的写法；**不要**用加长 sleep 来"修"这类问题。
    """
    deadline = time.time() + timeout
    while True:
        value = window.evaluate_js(expression)
        if expected is None:
            if value:
                return value
        elif value == expected:
            return value
        if time.time() >= deadline:
            return value
        time.sleep(interval)
