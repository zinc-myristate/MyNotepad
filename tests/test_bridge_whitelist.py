# -*- coding: utf-8 -*-
"""页面能调用的 Python 方法：按窗口的白名单（app.pyw 的 BRIDGE_* 与 expose_bridge）。

为什么不再传 js_api：pywebview 的分派（webview/util.py 的 js_bridge_call）把页面发来的函数名按 '.'
拆开，从 js_api 对象逐级 getattr，不查下划线，也不对照它公布的函数清单。以前 js_api=AppApi，而
AppApi.backend 就是整个后端 —— 页面里任何脚本调 `window.pywebview._jsApiCallback('backend.xxx', ...)`
都能直接调到后端的任意方法（包括私有的），还能顺着 `notes_list.__func__.__globals__` 摸到模块字典。
现在三个窗口都改用 window.expose 按名字登记，查不到的名字从 None 开始逐级 getattr，什么都摸不到。
"""
import ast
import inspect
import json
import os
import re

import pytest
from conftest import PROJECT_ROOT, load_app_partial, wait_for_js, wait_until

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')
APP_PYW = os.path.join(PROJECT_ROOT, 'app.pyw')
# 前端调用桥接的三种写法：window.pywebview.api.X / pywebview?.api?.X（就绪检查）/ api().X、api.X（别名）
CALL = re.compile(r'(?:pywebview\??\.api\??\.|\bapi\(\)\.|\bapi\??\.)([A-Za-z_]\w*)')


def _js_files(*parts):
    root = os.path.join(RENDERER, *parts)
    return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.endswith('.js')]


def _called(paths):
    names = set()
    for path in paths:
        names |= set(CALL.findall(open(path, encoding='utf-8').read()))
    return names


# 每个窗口的页面 = 它的 HTML + 它的模块图（主界面是 js/app + 共用的 js/shared、js/quill）
WINDOW_SOURCES = {
    'BRIDGE_MAIN': lambda: [os.path.join(RENDERER, 'index.html')]
    + _js_files('js', 'app') + _js_files('js', 'shared') + _js_files('js', 'quill'),
    'BRIDGE_CAPTURE_OVERLAY': lambda: [os.path.join(RENDERER, 'capture-overlay.html'),
                                       os.path.join(RENDERER, 'js', 'capture', 'overlay.js')],
    'BRIDGE_CAPTURE_MINI': lambda: [os.path.join(RENDERER, 'capture-mini.html'),
                                    os.path.join(RENDERER, 'js', 'capture', 'mini.js')],
}


def test_each_window_exposes_exactly_what_its_page_calls(app_ns):
    """多登记一个 = 白给页面一个能力；少登记一个 = 那个功能点了没反应（不报错，只在日志里）"""
    for whitelist, sources in WINDOW_SOURCES.items():
        names = app_ns[whitelist]
        assert len(names) == len(set(names)), '%s 里有重复' % whitelist
        called = _called(sources())
        assert set(names) == called, '%s：多登记 %s，漏登记 %s' % (
            whitelist, sorted(set(names) - called), sorted(called - set(names)))


def test_bridge_class_has_no_method_that_no_window_exposes(app_ns):
    """AppApi 的公开方法 = 三份白名单的并集。只给 Python 侧用的方法请加下划线，别挂在桥上"""
    public = {n for n, v in vars(app_ns['AppApi']).items() if callable(v) and not n.startswith('_')}
    exposed = set(app_ns['BRIDGE_MAIN']) | set(app_ns['BRIDGE_CAPTURE_OVERLAY']) | set(app_ns['BRIDGE_CAPTURE_MINI'])
    assert public == exposed, '没人登记的：%s；登记了却不存在的：%s' % (sorted(public - exposed), sorted(exposed - public))


def test_windows_are_created_without_js_api():
    """app.pyw 里每个 create_window 都不带 js_api，并且紧接着用 expose_bridge 登记"""
    tree = ast.parse(open(APP_PYW, encoding='utf-8').read())
    created, exposed = [], {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Attribute) and func.attr == 'create_window':
                created.append((node.targets[0].id, node.value))
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'expose_bridge':
            exposed[node.args[0].id] = node.args[2].id
    assert len(created) == 3, '窗口数变了（主窗 + 截图覆盖窗 + 迷你窗），实际 %d 个' % len(created)
    for var, call in created:
        assert not any(k.arg == 'js_api' for k in call.keywords), \
            '%s 又传了 js_api：pywebview 会从这个对象逐级 getattr，页面能摸到它挂着的一切' % var
        assert var in exposed, '%s 建好之后没有用 expose_bridge 登记，页面调不到任何方法' % var
    assert sorted(exposed.values()) == ['BRIDGE_CAPTURE_MINI', 'BRIDGE_CAPTURE_OVERLAY', 'BRIDGE_MAIN']


class _FakeWindow:
    def __init__(self):
        self.funcs = []

    def expose(self, *funcs):
        self.funcs.extend(funcs)


def test_expose_bridge_advertises_the_same_stubs_as_js_api(app_ns):
    """JS 存根的形参来自 inspect.getfullargspec：包装函数要和以前走 js_api 时一字不差"""
    api, fake = app_ns['api'], _FakeWindow()
    app_ns['expose_bridge'](fake, api, app_ns['BRIDGE_MAIN'])
    assert [f.__name__ for f in fake.funcs] == list(app_ns['BRIDGE_MAIN'])
    for func in fake.funcs:
        before = inspect.getfullargspec(getattr(api, func.__name__)).args[1:]   # js_api 路径去掉 self
        assert inspect.getfullargspec(func).args == before, func.__name__
    counts = next(f for f in fake.funcs if f.__name__ == 'notebook_counts')()
    assert counts == {'by_id': {}, 'uncategorized': 0, 'total': 0}, '包装函数要真的转到后端'


# ---------------- 真窗口：页面只调得到白名单 ----------------

def _run_window(make_window, actions):
    """和 test_ui_layout._run 一样，只是窗口由调用方按 app.pyw 的方式建（那边固定传 js_api）"""
    import threading

    import webview
    result = {}
    window = make_window()

    def runner():
        try:
            actions(window, result)
        except Exception as exc:                     # noqa: BLE001
            result['error'] = repr(exc)
        finally:
            try:
                window.destroy()
            except Exception:                        # noqa: BLE001
                pass

    threading.Thread(target=runner, daemon=True).start()
    webview.start()
    return result


RAW_CALLS = [
    'backend.notes_purge_all',                      # 以前：AppApi.backend 就是整个后端
    'backend._fts_rebuild',                         # 私有方法也一样
    'notes_list.__func__.__globals__.clear',        # 顺着函数摸到模块字典
    '_ack',
]


@pytest.mark.e2e
def test_page_can_only_call_whitelisted_methods(tmp_path, monkeypatch):
    """用和 app.pyw 一样的方式建窗口（不传 js_api + expose_bridge），再从页面发原始调用。

    把 app.pyw 改回 js_api=api 再跑这一条，`backend.notes_purge_all` 会当场清空回收站，测试变红。
    """
    import webview
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    trashed = backend.api.notes_create()['id']
    backend.api.notes_update(trashed, {'title': '回收站里的', 'content': '正文'})
    backend.api.notes_delete(trashed)
    kept = backend.api.notes_create()['id']
    backend.api.notes_update(kept, {'title': '留着的', 'content': '正文'})

    def make_window():
        window = webview.create_window('白名单', os.path.join(RENDERER, 'index.html'), width=1200, height=800)
        ns['expose_bridge'](window, ns['api'], ns['BRIDGE_MAIN'])
        return window

    def actions(window, result):
        assert wait_for_js(window, 'window.__app?.state.activeNoteId', kept)
        result['api'] = json.loads(window.evaluate_js('JSON.stringify(Object.keys(window.pywebview.api).sort())'))
        result['backend_visible'] = window.evaluate_js('typeof window.pywebview.api.backend')
        for i, name in enumerate(RAW_CALLS):
            window.evaluate_js('window.pywebview._jsApiCallback(%s, [], "raw%d")' % (json.dumps(name), i))
        # 回收站被清空就说明调到了；等一会儿确认它一直没被清空
        result['purged'] = bool(wait_until(lambda: not backend.api.notes_trash_list(), timeout=3))
        # 白名单里的方法照常可用（模块字典没被清掉）
        window.evaluate_js("window.__probe = null; window.pywebview.api.notes_list(null)"
                           ".then(r => window.__probe = r.length)")
        result['still_works'] = wait_for_js(window, 'window.__probe')

    result = _run_window(make_window, actions)
    assert 'error' not in result, result
    assert result['api'] == sorted(ns['BRIDGE_MAIN']), result['api']
    assert result['backend_visible'] == 'undefined', result
    assert result['purged'] is False, '页面绕过白名单调到了后端：%s' % result
    assert result['still_works'] == 1, result
    assert [n['id'] for n in backend.api.notes_trash_list()] == [trashed]
