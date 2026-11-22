# -*- coding: utf-8 -*-
"""三个页面的内容安全策略（CSP）：脚本只认本地文件和按哈希放行的那段内联加载器。

2026-11-22 前 script-src 带着 'unsafe-inline'：万一有内容绕过 DOMPurify 混进页面，<img onerror=…> 这类
内联事件属性照样会执行。主页面唯一的内联脚本是启动加载器（刻意内联：第三方库注入失败时要整页重试），
现在按 sha256 只放行它一段；两个截图窗口的页面本来就没有内联脚本。

哈希必须和加载器一字不差：改了加载器（哪怕一个空格）没重算哈希，浏览器就拒绝执行它，整个应用起不来，
而且没有任何报错能看到。所以这里在单测里就核对，不等到 E2E。
"""
import base64
import hashlib
import os
import re

import pytest
from conftest import PROJECT_ROOT

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')
PAGES = ['index.html', 'capture-overlay.html', 'capture-mini.html']


def _page(name):
    with open(os.path.join(RENDERER, name), encoding='utf-8', newline='') as fh:
        return fh.read()


def _policy(html):
    """meta 里的 CSP → {指令: [来源…]}"""
    m = re.search(r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"', html)
    assert m, '页面里没有 CSP'
    policy = {}
    for part in m.group(1).split(';'):
        words = part.split()
        if words:
            policy[words[0]] = words[1:]
    return policy


def _inline_scripts(html):
    """不带 src 的 <script> 的正文（HTML 解析时 CRLF 会变成 LF，哈希按 LF 算）"""
    return [body.replace('\r\n', '\n') for body in re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)]


def _sha256(text):
    return "'sha256-%s'" % base64.b64encode(hashlib.sha256(text.encode('utf-8')).digest()).decode('ascii')


@pytest.mark.parametrize('name', PAGES)
def test_scripts_are_only_local_files_and_hashed_inline_ones(name):
    script_src = _policy(_page(name))['script-src']
    assert "'unsafe-inline'" not in script_src and "'unsafe-eval'" not in script_src, script_src
    assert [s for s in script_src if not s.startswith("'sha256-")] == ["'self'"], script_src


@pytest.mark.parametrize('name', PAGES)
def test_every_inline_script_is_allowed_by_its_hash_and_no_hash_is_stale(name):
    html = _page(name)
    want = sorted(_sha256(body) for body in _inline_scripts(html))
    have = sorted(s for s in _policy(html)['script-src'] if s.startswith("'sha256-"))
    assert have == want, ('内联脚本改过之后要更新 CSP 里的哈希（%s 的 script-src），现在应当是 %s；'
                          '对不上的话浏览器拒绝执行它，应用起不来' % (name, ' '.join(want) or '（不需要哈希）'))


def test_the_main_page_has_exactly_the_boot_loader_inline():
    bodies = _inline_scripts(_page('index.html'))
    assert len(bodies) == 1 and 'reload' in bodies[0], '主页面应当只有启动加载器这一段内联脚本'


@pytest.mark.parametrize('name', PAGES)
def test_no_inline_event_handler_attributes(name):
    """内联事件属性（onclick= 之类）在这套 CSP 下不会执行：页面里写了就是一个点了没反应的按钮"""
    html = re.sub(r'<!--.*?-->', '', _page(name), flags=re.S)
    assert not re.findall(r'<[^>]*\son[a-z]+\s*=', html), name


@pytest.mark.parametrize('name', PAGES)
def test_the_network_stays_closed(name):
    """"不联网"靠的那几条不能在改策略时顺手放开"""
    policy = _policy(_page(name))
    assert policy['default-src'] == ["'self'"]
    for directive in ('connect-src', 'object-src', 'base-uri', 'form-action'):
        assert policy.get(directive) == ["'none'"], (name, directive, policy.get(directive))
