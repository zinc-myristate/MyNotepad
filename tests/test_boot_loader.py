# -*- coding: utf-8 -*-
"""前端启动健壮性回归（无头 pywebview，默认跳过）。

背景（2026-09-20 实修）：WebView2 偶发拉取失败个别 <script>（实测 ~1/20），浏览器静默
跳过失败脚本。当时 debounce 定义在 utils.js 却被 6 个模块在顶层调用 → utils.js 一挂，
03-notes.js 整段不执行（「新建笔记」的监听器注册在该文件最后一行）→ 按钮无反应；
09-boot.js 的 initApp() 也不执行 → Quill 主题类没加上、内置按钮图标全丢，塌成 4×4 小点。

修复后 index.html 末尾改为「顺序注入 + 失败重试」的加载器，并为 debounce/escapeHtml/
formatFileSize 提供兜底实现。本文件锁死这两条：
  1. 正常情况下加载器跑完（__appBoot.done）且未标记任何失败；
  2. 人为让 utils.js 连续失败两次时，仍能完整启动、工具栏正常、点「新建笔记」可用。
"""
import json
import os
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial

pytestmark = pytest.mark.e2e

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')
INDEX = os.path.join(RENDERER, 'index.html')

# 拦截 head.appendChild：让 utils.js 前 N 次插入直接派发 error，逼出加载器的重试路径
INTERCEPT_TMPL = """<script>
window.__utilsRequests = [];
(function(){
  var origAppend = document.head.appendChild.bind(document.head);
  var hits = 0, failFirst = %d;
  document.head.appendChild = function(node){
    if (node && node.tagName === 'SCRIPT' && node.src &&
        node.src.indexOf('js/shared/utils.js') >= 0) {
      hits++;
      window.__utilsRequests.push(node.src.split('/').pop());
      if (hits <= failFirst) {
        setTimeout(function(){ if (node.onerror) node.onerror(new Event('error')); }, 20);
        return node;                        // 不真正加载，模拟拉取失败
      }
    }
    return origAppend(node);
  };
})();
</script>"""

ANCHOR = '<script>\n// 关键工具函数的兜底实现'

PROBE = r"""JSON.stringify({
  boot: window.__appBoot || null,
  utilsRequests: window.__utilsRequests || [],
  tbClass: document.getElementById('editor-toolbar').className,
  boldSvg: !!document.querySelector('#editor-toolbar .ql-bold svg'),
  debounceType: typeof debounce,
  escapeHtml: (typeof escapeHtml === 'function') ? escapeHtml('<b>x</b>') : 'MISSING',
  notes: document.querySelectorAll('.note-item').length
})"""


def _make_variant(fail_first, name):
    """生成一份注入了故障的 index.html 副本（放同目录，相对路径不变）"""
    with open(INDEX, encoding='utf-8') as f:
        html = f.read()
    assert ANCHOR in html, 'index.html 结构变了：找不到兜底实现锚点，请同步本测试'
    path = os.path.join(RENDERER, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html.replace(ANCHOR, (INTERCEPT_TMPL % fail_first) + ANCHOR))
    return path


def _launch(ns, window_html, actions, wait_before=8):
    """启动无头窗口执行 actions(window)（与 test_frontend_e2e 同构）"""
    import webview
    window = webview.create_window("启动健壮性", window_html,
                                   js_api=ns['api'], width=1000, height=700)

    def runner():
        try:
            time.sleep(wait_before)
            actions(window)
        finally:
            try:
                window.destroy()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()
    webview.start()


def _probe(ns, fail_first):
    """在注入 fail_first 次 utils.js 故障的页面上跑完整探针"""
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': 'T', 'content': '{"ops":[{"insert":"x\\n"}]}'})

    variant = _make_variant(fail_first, '_e2e_fail%s.html' % fail_first)
    out = {}
    try:
        def actions(window):
            time.sleep(3)
            out['probe'] = window.evaluate_js(PROBE)
            out['before'] = window.evaluate_js(
                "document.querySelectorAll('.note-item').length")
            window.evaluate_js("document.getElementById('btn-new-note').click()")
            time.sleep(3)
            out['after'] = window.evaluate_js(
                "document.querySelectorAll('.note-item').length")

        _launch(ns, variant, actions)
    finally:
        try:
            os.remove(variant)
        except OSError:
            pass
    return json.loads(out['probe']), out['before'], out['after']


def test_loader_completes_on_clean_start(tmp_path, monkeypatch):
    """无故障时：加载器跑完、无失败标记、依赖可用"""
    ns = load_app_partial(monkeypatch, tmp_path)
    probe, before, after = _probe(ns, 0)

    assert probe['boot'] and probe['boot'].get('done') is True, '加载器应跑完'
    assert probe['boot'].get('failed') == [], '干净启动不应有失败记录'
    assert probe['utilsRequests'] == ['utils.js'], \
        'utils.js 应只被请求一次（未重试）：%r' % (probe['utilsRequests'],)
    assert probe['debounceType'] == 'function'
    assert probe['escapeHtml'] == '&lt;b&gt;x&lt;/b&gt;', 'escapeHtml 应可正常转义'
    assert 'ql-toolbar' in probe['tbClass'], 'Quill 主题类应已应用'
    assert probe['boldSvg'] is True, '内置按钮应有 SVG 图标'
    assert int(after) > int(before), '「新建笔记」应可用'


@pytest.mark.parametrize('fail_first', [1, 2])
def test_recovers_when_utils_js_fails_to_load(tmp_path, monkeypatch, fail_first):
    """utils.js 连续失败 1~2 次时仍必须完整启动（本次 bug 的核心回归）"""
    ns = load_app_partial(monkeypatch, tmp_path)
    probe, before, after = _probe(ns, fail_first)

    log = probe['utilsRequests']
    assert len(log) == fail_first + 1, \
        '应重试到成功：期望 %d 次请求，实际 %r' % (fail_first + 1, log)
    assert log[0] == 'utils.js', '首次请求不带参数'
    assert 'retry=' in log[1], '重试应带 ?retry=N 绕开缓存'
    assert probe['boot'].get('done') is True, '重试成功后应完整跑完'
    assert probe['boot'].get('failed') == [], '不应残留失败记录：%r' % (probe['boot'],)
    assert probe['debounceType'] == 'function', 'utils.js 最终应加载成功'
    assert 'ql-toolbar' in probe['tbClass'], '工具栏主题类应已应用（图标不再丢失）'
    assert probe['boldSvg'] is True, '内置按钮图标必须存在'
    assert int(after) > int(before), '「新建笔记」必须可用'


def test_shim_keeps_app_alive_when_utils_never_loads(tmp_path, monkeypatch):
    """utils.js 彻底加载不上时：兜底实现保证应用仍能启动，而不是整体半死"""
    ns = load_app_partial(monkeypatch, tmp_path)
    probe, before, after = _probe(ns, 99)   # 永远失败

    assert 'js/shared/utils.js' in (probe['boot'].get('failed') or []), \
        '应记录失败文件：%r' % (probe['boot'],)
    assert len(probe['utilsRequests']) >= 4, '应重试到上限：%r' % (probe['utilsRequests'],)
    assert probe['boot'].get('done') is True, \
        '单文件失败不应中断整条加载链（否则后面的脚本永不执行）'
    # 兜底生效：其余模块照常执行
    assert probe['debounceType'] == 'function', '兜底 debounce 应可用'
    assert 'ql-toolbar' in probe['tbClass'], '工具栏仍应初始化'
    assert probe['boldSvg'] is True, '内置按钮图标仍应存在'
    assert int(after) > int(before), '「新建笔记」仍应可用（原先正是这里失效）'
