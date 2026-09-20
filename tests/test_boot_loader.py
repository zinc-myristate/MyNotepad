# -*- coding: utf-8 -*-
"""前端启动健壮性回归（无头 pywebview，默认跳过）。

背景（2026-09-20 二轮重构）：改造前 index.html 按 FILES 数组顺序注入 17 个**经典脚本**，
模块间靠「全局作用域 + 加载顺序」互相依赖。WebView2 偶发拉取失败某个 <script>（实测
~1/20），浏览器会静默跳过失败文件——`utils.js` 一挂，6 个模块就在顶层抛
「debounce is not defined」而整段不执行，表现为「新建笔记点了没反应」+「工具栏图标集体
消失」（监听器与 initApp 都注册在这些文件里）。

现状：应用已是 **ES 模块图**（唯一入口 js/app/00-main.js），依赖关系显式写在各文件的
import 里，第三方 UMD 库（Quill / KaTeX）仍是带重试的经典脚本。新的失败语义是：
  · 经典脚本拉取失败 → 逐个重试（最多 4 次）；
  · 模块图加载失败 → 浏览器会缓存「取不到的模块」，同一文档内重试无效，故整页 reload
    重试（上限 4 次，计数存 sessionStorage），超限后在顶部显示红色横幅并写 log_error。
本文件锁死这四条。"""
import json
import os
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial

pytestmark = pytest.mark.e2e

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')
INDEX = os.path.join(RENDERER, 'index.html')

# 加载器内联脚本的锚点（在它之前注入故障脚本）
ANCHOR = '<script>\n(function () {'
ENTRY_LITERAL = "'./js/app/00-main.js'"

# 拦截 head.appendChild：让指定经典脚本前 N 次插入直接派发 error，逼出重试路径
INTERCEPT_TMPL = """<script>
window.__scriptRequests = [];
window.__appBootMaxRetry = %(max_retry)s;
(function(){
  var origAppend = document.head.appendChild.bind(document.head);
  var hits = 0, failFirst = %(fail_first)d;
  document.head.appendChild = function(node){
    if (node && node.tagName === 'SCRIPT' && node.src &&
        node.src.indexOf('quill.js') >= 0) {
      hits++;
      window.__scriptRequests.push(node.src.split('/').pop());
      if (hits <= failFirst) {
        setTimeout(function(){ if (node.onerror) node.onerror(new Event('error')); }, 20);
        return node;                        // 不真正加载，模拟拉取失败
      }
    }
    return origAppend(node);
  };
})();
</script>"""

PROBE = r"""JSON.stringify({
  boot: window.__appBoot || null,
  hasApp: typeof window.__app === 'object' && !!(window.__app && window.__app.state),
  scriptReqs: window.__scriptRequests || [],
  banner: !!document.getElementById('boot-failure-banner'),
  bannerText: (document.getElementById('boot-failure-banner') || {}).textContent || '',
  retryCount: (function(){ try { return sessionStorage.getItem('__appBootRetry'); } catch(e){ return 'ERR'; } })(),
  tbClass: (document.getElementById('editor-toolbar') || {}).className || '',
  boldSvg: !!document.querySelector('#editor-toolbar .ql-bold svg'),
  notes: document.querySelectorAll('.note-item').length
})"""


def _make_variant(name, fail_first=0, max_retry=None, break_entry=False):
    """生成注入故障的 index.html 副本（放同目录，相对路径不变）"""
    with open(INDEX, encoding='utf-8') as f:
        html = f.read()
    assert ANCHOR in html, 'index.html 加载器结构变了：找不到注入锚点，请同步本测试'
    inject = INTERCEPT_TMPL % {'fail_first': fail_first,
                               'max_retry': 'null' if max_retry is None else max_retry}
    html = html.replace(ANCHOR, inject + ANCHOR, 1)
    if break_entry:
        assert ENTRY_LITERAL in html, '入口模块路径变了，请同步本测试'
        html = html.replace(ENTRY_LITERAL, "'./js/app/__does_not_exist__.js'", 1)
    path = os.path.join(RENDERER, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(html)
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


def _probe(ns, *, fail_first=0, max_retry=None, break_entry=False, wait_before=8):
    """在注入故障的页面上跑探针"""
    import backend
    nid = backend.api.notes_create()['id']
    backend.api.notes_update(nid, {'title': 'T', 'content': '{"ops":[{"insert":"x\\n"}]}'})

    variant = _make_variant('_e2e_boot_%s.html' % abs(hash((fail_first, max_retry, break_entry))),
                            fail_first=fail_first, max_retry=max_retry, break_entry=break_entry)
    out = {}
    try:
        def actions(window):
            out['probe'] = window.evaluate_js(PROBE)
            out['before'] = window.evaluate_js("document.querySelectorAll('.note-item').length")
            window.evaluate_js("document.getElementById('btn-new-note').click()")
            time.sleep(3)
            out['after'] = window.evaluate_js("document.querySelectorAll('.note-item').length")

        _launch(ns, variant, actions, wait_before=wait_before)
    finally:
        try:
            os.remove(variant)
        except OSError:
            pass
    return json.loads(out['probe']), out['before'], out['after']


def test_module_graph_boots_clean(tmp_path, monkeypatch):
    """干净启动：模块图跑完、无失败标记、桥接可用、界面完整"""
    ns = load_app_partial(monkeypatch, tmp_path)
    probe, before, after = _probe(ns)

    assert probe['boot'] and probe['boot'].get('done') is True, '模块图应加载完成'
    assert probe['boot'].get('failed') == [], '干净启动不应有失败记录'
    assert probe['banner'] is False, '不该出现启动失败横幅'
    assert probe['hasApp'] is True, '应暴露 __app 桥接（state 可用）'
    assert probe['scriptReqs'] == ['quill.js'], \
        '经典脚本应只请求一次（未重试）：%r' % (probe['scriptReqs'],)
    assert 'ql-toolbar' in probe['tbClass'], 'Quill 主题类应已应用'
    assert probe['boldSvg'] is True, '内置按钮应有 SVG 图标'
    assert int(after) > int(before), '「新建笔记」应可用'


def test_classic_script_failure_is_retried(tmp_path, monkeypatch):
    """经典脚本（Quill）拉取失败 1 次：应重试并完整启动"""
    ns = load_app_partial(monkeypatch, tmp_path)
    probe, before, after = _probe(ns, fail_first=1)

    reqs = probe['scriptReqs']
    assert len(reqs) == 2, '应重试到成功：%r' % (reqs,)
    assert reqs[0] == 'quill.js', '首次请求不带参数'
    assert 'retry=' in reqs[1], '重试应带 ?retry=N 绕开缓存'
    assert probe['boot'].get('done') is True, '重试成功后应完整跑完'
    assert probe['boot'].get('failed') == [], '不应残留失败记录：%r' % (probe['boot'],)
    assert probe['banner'] is False
    assert 'ql-toolbar' in probe['tbClass'], '工具栏主题类应已应用（图标不丢）'
    assert probe['boldSvg'] is True, '内置按钮图标必须存在'
    assert int(after) > int(before), '「新建笔记」必须可用'


def test_module_failure_reports_explicitly(tmp_path, monkeypatch):
    """模块图加载不了时：不再「静默半死」，顶部红条明确报错且记录失败"""
    ns = load_app_partial(monkeypatch, tmp_path)
    probe, _, _ = _probe(ns, max_retry=1, break_entry=True)

    assert probe['boot'].get('done') is not True, '加载失败时不应标记完成'
    assert probe['boot'].get('failed'), '应记录失败原因：%r' % (probe['boot'],)
    assert probe['hasApp'] is False, '模块图没加载成功，桥接不应存在'
    assert probe['banner'] is True, '应显示启动失败横幅'
    assert '启动不完整' in probe['bannerText'], probe['bannerText']


def test_module_failure_reloads_until_cap(tmp_path, monkeypatch):
    """模块图失败靠整页 reload 重试：计数落 sessionStorage，达到上限后停止并报错"""
    ns = load_app_partial(monkeypatch, tmp_path)
    cap = 3
    probe, _, _ = _probe(ns, max_retry=cap, break_entry=True, wait_before=14)

    assert probe['retryCount'] == str(cap), \
        '应在 sessionStorage 累计重试次数到上限 %d：%r' % (cap, probe['retryCount'])
    assert probe['boot'].get('attempts') == cap, probe['boot']
    assert probe['banner'] is True, '达到上限后应显示横幅而不是无限重试'
