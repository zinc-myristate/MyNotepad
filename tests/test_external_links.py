# -*- coding: utf-8 -*-
"""笔记里的链接怎么被打开：只有网页、邮件、电话链接会交给系统，别的地址一律不理。

出口是 `os.startfile`，也就是 ShellExecute：给它什么它就"打开"什么，本地程序、网络共享上的
文件都照开。通到它的路有三条：

  · Markdown 预览里点链接 → 桥接 `open_url`
  · 页面请求开新窗口（target=_blank、window.open、中键点链接）→ pywebview 的 NewWindowRequested
  · 双击附件 → `file_open`（只认附件目录，见 test_audit_security.py）

历史缺陷有两个。预览里的链接以前一律交给 `file_open`，而它只认附件目录里的文件，所以网页链接
点了没有任何反应。pywebview 默认把新窗口请求里的地址原样交给 `webbrowser.open`：页面里的脚本
`window.open('ms-settings:privacy')` 不经确认就把系统设置打开了，别的程序注册的协议也一样
（对照实测；`file:` 地址 WebView2 自己会拦，到不了 pywebview）。
"""
import os
import re
import sys
import threading
import time
import types

import pytest
from conftest import APP_PYW, PROJECT_ROOT, load_app_partial, wait_for_js, wait_until

import desktop

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')

ALLOWED = [
    'https://example.com',
    'http://example.com/a/b?c=1&d=%E4%B8%AD#frag',
    'HTTPS://EXAMPLE.COM/Path',
    'https://例子.测试/路径',
    'https://user:pw@example.com:8443/x%20y',
    'https://[::1]:8080/x',
    'mailto:someone@example.com',
    'mailto:someone@example.com?subject=hi%20there',
    'tel:+86-10-1234-5678',
    'tel:10086',
]

REFUSED = [
    # 本地程序和文件
    'file:///C:/Windows/System32/calc.exe',
    'file://attacker/share/evil.exe',
    'C:\\Windows\\System32\\calc.exe',
    'C:/Windows/System32/calc.exe',
    'calc.exe',
    # 网络共享
    '\\\\attacker\\share\\evil.exe',
    '//attacker/share/evil.exe',
    # 别的协议：每一个都对应一个系统里注册过的处理程序
    'javascript:alert(1)',
    'vbscript:msgbox(1)',
    'data:text/html,<script>alert(1)</script>',
    'ftp://example.com/x',
    'ms-settings:privacy',
    'search-ms:query=x',
    'shell:startup',
    'sms:10086',
    # 长得像网址，其实不是
    'https:\\\\evil.example.com',
    'https:/example.com',
    'https:example.com',
    'https://',
    'https:///nohost',
    'https://[bad/x',
    'http://exa mple.com',
    'http://example.com/"x',
    'http://example.com/<x>',
    'http://example.com/a\x00b',
    'http://example.com/a\nb',
    'https://example.com/`whoami`',
    'https://example.com/' + 'a' * 3000,
    # 邮件和电话
    'mailto:',
    'mailto://example.com',
    'tel:',
    'tel:123;ext=4 /c calc',
    'tel:abc',
    # 不是字符串
    '', '   ', None, 42, b'https://example.com', ['https://example.com'],
]


@pytest.mark.parametrize('url', ALLOWED)
def test_safe_external_url_lets_web_mail_and_phone_through(url):
    assert desktop.safe_external_url(url) == url


@pytest.mark.parametrize('url', REFUSED, ids=[repr(u)[:40] for u in REFUSED])
def test_safe_external_url_refuses_everything_else(url):
    assert desktop.safe_external_url(url) is None


def test_safe_external_url_trims_surrounding_whitespace():
    assert desktop.safe_external_url('  https://example.com/a \n') == 'https://example.com/a'


def test_open_external_url_launches_only_what_passes(launched):
    assert desktop.open_external_url('https://example.com/a') is True
    assert desktop.open_external_url('mailto:someone@example.com') is True
    assert launched == ['https://example.com/a', 'mailto:someone@example.com']

    del launched[:]
    for url in REFUSED:
        assert desktop.open_external_url(url) is False, '不该交给系统：%r' % (url,)
    assert launched == []


def test_open_external_url_reports_a_failed_launch(monkeypatch):
    """系统里没有能打开它的程序时 os.startfile 抛 OSError：返回 False，前端据此提示。"""
    def no_handler(target):
        raise OSError(1155, '没有应用程序与此操作的指定文件有关联')

    monkeypatch.setattr(os, 'startfile', no_handler)
    assert desktop.open_external_url('mailto:someone@example.com') is False


def test_bridge_open_url_uses_the_same_gate(app_ns, launched):
    api = app_ns['api']
    assert 'open_url' in app_ns['BRIDGE_MAIN']
    assert api.open_url('https://example.com/a?b=1') is True
    assert api.open_url('file:///C:/Windows/System32/calc.exe') is False
    assert api.open_url('\\\\attacker\\share\\evil.exe') is False
    assert launched == ['https://example.com/a?b=1']


# ---------------- 新窗口请求 ----------------

class _FakeNewWindowArgs:
    def __init__(self, uri):
        self.uri, self.handled = uri, False

    def set_Handled(self, value):
        self.handled = value

    def get_Uri(self):
        return self.uri


def _fake_edgechromium(monkeypatch, **attrs):
    """用假的 webview.platforms.edgechromium 顶替真的：单测不加载 .NET，也不动真的类。"""
    import webview.platforms as platforms
    fake = types.ModuleType('webview.platforms.edgechromium')
    for name, value in attrs.items():
        setattr(fake, name, value)
    monkeypatch.setitem(sys.modules, 'webview.platforms.edgechromium', fake)
    monkeypatch.setattr(platforms, 'edgechromium', fake, raising=False)
    return fake


def test_new_window_requests_go_through_the_same_gate(app_ns, monkeypatch, launched):
    class EdgeChrome:
        def on_new_window_request(self, sender, args):
            raise AssertionError('pywebview 原来的处理不该再被调到')

    fake = _fake_edgechromium(monkeypatch, EdgeChrome=EdgeChrome)
    assert app_ns['harden_new_window_requests']() is True

    browser = fake.EdgeChrome()
    requests = [_FakeNewWindowArgs(uri) for uri in (
        'https://example.com/a', 'file:///C:/Windows/System32/calc.exe',
        'file://attacker/share/evil.exe', 'mailto:someone@example.com', 'ms-settings:privacy')]
    for args in requests:
        browser.on_new_window_request(None, args)

    assert all(args.handled is True for args in requests), '不标成已处理，WebView2 会自己弹一个新窗口'
    assert launched == ['https://example.com/a', 'mailto:someone@example.com']


@pytest.mark.parametrize('attrs', [{}, {'EdgeChrome': type('EdgeChrome', (), {})}],
                         ids=['no-class', 'no-handler'])
def test_new_window_hardening_gives_up_quietly_when_pywebview_changes(app_ns, monkeypatch, attrs,
                                                                      tmp_path):
    """换不成就保持原样、留一条日志：不能因为 pywebview 改了内部结构，应用就起不来。"""
    _fake_edgechromium(monkeypatch, **attrs)
    assert app_ns['harden_new_window_requests']() is False
    assert '没能接管新窗口请求' in (tmp_path / 'error.log').read_text(encoding='utf-8')


def test_app_takes_over_new_window_requests_before_the_loop_starts():
    """EdgeChrome 在 WebView2 初始化完成时订阅事件，拿的是那时类上的方法：要在 webview.start 之前换。"""
    src = open(APP_PYW, encoding='utf-8').read()
    main = src[src.index('# ====== 创建窗口 ======'):]
    assert main.count('webview.start(') == 2
    assert main.index('\nharden_new_window_requests()\n') < main.index('webview.start(')


# ---------------- 前端接线（静态） ----------------

def _render_js():
    return open(os.path.join(RENDERER, 'js', 'app', '14-markdown-render.js'), encoding='utf-8').read()


def test_preview_links_are_routed_by_scheme_not_handed_to_file_open():
    src = _render_js()
    body = src[src.index('export function bindPreviewLinks'):]
    body = body[:body.index('\n}\n')]
    assert 'api.open_url(href)' in body
    assert 'file_open' not in body, '网页链接交给 file_open 等于点了没反应（它只认附件目录）'
    assert 'ev.preventDefault()' in body, '不拦默认行为，整个窗口会跳到那个地址去'
    assert 'showToast(' in body, '打不开要说一声'


def test_sanitizer_keeps_href_only_for_web_and_mail():
    """消毒给哪些地址留 href，决定了预览里哪些链接点得动。file: 不在其中。"""
    src = _render_js()
    hook = src[src.index("if (node.tagName === 'A')"):]
    hook = hook[:hook.index('\n  });')]
    kept = re.search(r"!/\^\((.*?)\)/i\.test\(href\)", hook)
    assert kept, '没找到 href 的协议判断'
    assert sorted(kept.group(1).split('|')) == ['https?:', 'mailto:']


# ---------------- e2e：真窗口 ----------------

def _run(factory, actions, wait_before=0):
    import webview
    result = {}
    window = factory()

    def runner():
        try:
            time.sleep(wait_before)
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


@pytest.mark.e2e
def test_clicking_preview_links_opens_web_and_mail_only(tmp_path, monkeypatch, launched):
    """预览里点链接：网页和邮件交给系统；本地文件、别的协议没有 href，点了不会有任何东西被打开。"""
    import webview
    from test_markdown_editor import _set_source, _wait_editor_ready
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    backend.api.notes_create()
    source = ('[网页](https://example.com/a?b=1)\n\n'
              '<https://example.org/auto>\n\n'
              '[邮件](mailto:someone@example.com)\n\n'
              '<a href="file:///C:/Windows/System32/calc.exe">本地程序</a>\n\n'
              '<a href="\\\\attacker\\share\\evil.exe">网络共享</a>\n\n'
              '<a href="ms-settings:privacy">别的协议</a>\n\n'
              '<a href="https://">没有主机名</a>\n')

    def factory():
        return webview.create_window('链接回归', os.path.join(RENDERER, 'index.html'),
                                     js_api=ns['api'], width=1200, height=800)

    def click(window, text):
        return window.evaluate_js(
            "(function(){var a=[].slice.call(document.querySelectorAll('#md-preview a'))"
            ".filter(function(x){return x.textContent===%r;})[0];if(!a)return 'missing';"
            "a.click();return a.hasAttribute('href')?'href':'no-href';})()" % text)

    def toast(window):
        return window.evaluate_js(
            "(function(){var t=document.getElementById('save-toast');"
            "return t&&t.style.opacity==='1'?t.textContent:'';})()") or ''

    def actions(window, result):
        _wait_editor_ready(window)
        _set_source(window, source)
        wait_for_js(window, "document.querySelectorAll('#md-preview a').length", expected=7)
        location = window.evaluate_js('location.href')

        result['web'] = click(window, '网页')
        wait_until(lambda: len(launched) >= 1, timeout=5)
        result['auto'] = click(window, 'https://example.org/auto')
        wait_until(lambda: len(launched) >= 2, timeout=5)
        result['mail'] = click(window, '邮件')
        wait_until(lambda: len(launched) >= 3, timeout=5)
        result['toast_after_good_links'] = toast(window)

        result['dead'] = [click(window, t) for t in ('本地程序', '网络共享', '别的协议')]
        result['no_host'] = click(window, '没有主机名')
        result['toast'] = wait_for_js(
            window, "(function(){var t=document.getElementById('save-toast');"
                    "return t&&t.style.opacity==='1'?t.textContent:'';})()", timeout=5)
        time.sleep(0.5)                                  # 给不该发生的打开留一点时间露头
        result['stayed'] = window.evaluate_js('location.href') == location

    r = _run(factory, actions)
    assert 'error' not in r, r.get('error')
    assert (r['web'], r['auto'], r['mail']) == ('href', 'href', 'href')
    assert launched == ['https://example.com/a?b=1', 'https://example.org/auto',
                        'mailto:someone@example.com'], launched
    assert r['toast_after_good_links'] == '', '打开成功不该弹提示'
    assert r['dead'] == ['no-href'] * 3, '这几种地址消毒时就该摘掉 href：%s' % r['dead']
    assert r['no_host'] == 'href'
    assert '打不开' in r['toast'], '后端拒绝的链接要给一句提示，不能还是点了没反应'
    assert r['stayed'], '点链接把应用窗口自己带走了'


@pytest.mark.e2e
def test_new_window_requests_from_a_real_page_are_filtered(tmp_path, monkeypatch, launched):
    """真的 WebView2：页面用 window.open 和 target=_blank 请求开新窗口，只有网页地址被交给系统。

    不加固时 `ms-settings:privacy` 会原样到 webbrowser.open（对照实测）；`file:` 那几个
    WebView2 自己就拦了，留在这里是为了哪天它不拦了也照样过不去。
    """
    import webview
    from webview.platforms import edgechromium
    ns = load_app_partial(monkeypatch, tmp_path)
    # 先登记"用完换回去"，再让应用把处理函数换掉：别的用例拿到的还是 pywebview 原来的类
    monkeypatch.setattr(edgechromium.EdgeChrome, 'on_new_window_request',
                        edgechromium.EdgeChrome.on_new_window_request)
    assert ns['harden_new_window_requests']() is True

    page = ('<a id="web" target="_blank" href="https://example.com/blank">web</a>'
            '<a id="exe" target="_blank" href="file:///C:/Windows/System32/calc.exe">exe</a>'
            '<a id="unc" target="_blank" href="file://attacker/share/evil.exe">unc</a>')

    def factory():
        return webview.create_window('新窗口请求', html=page, width=420, height=320)

    def actions(window, result):
        wait_for_js(window, "!!document.getElementById('unc')")
        window.evaluate_js("document.getElementById('exe').click();"
                           "document.getElementById('unc').click();"
                           "window.open('file:///C:/Windows/System32/calc.exe');"
                           "window.open('ms-settings:privacy');"
                           "document.getElementById('web').click();"
                           "window.open('https://example.com/opened');")
        result['seen'] = wait_until(lambda: len(launched) >= 2, timeout=8)
        time.sleep(0.5)                                  # 被拦下的那几个要是漏了，这时也该到了

    r = _run(factory, actions, wait_before=1)
    assert 'error' not in r, r.get('error')
    assert sorted(launched) == ['https://example.com/blank', 'https://example.com/opened'], launched
