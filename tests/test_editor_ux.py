# -*- coding: utf-8 -*-
"""第 8 轮「编辑器手感」的静态守卫（无需浏览器，属于默认单测）。

这一轮加的三样东西有个共同特征：**坏掉时不会报错，只会悄悄不生效**——
highlight.js 没排到 quill.js 前面 → 构造 Quill 抛异常；状态栏的剥离规则和后端
漂移一个字符 → 数字对不上；查找条没有 DOM 节点 → 点了没反应。所以静态守卫在这里
不是形式主义，而是唯一能在秒级发现「接线漏了」的手段。

其中 `test_stats_rules_mirror_backend` 是把前端正则**与 backend.py 的正则对象逐字比对**：
两份实现分别用 JS / Python 写，靠人眼"看着一样"迟早会漂移，所以直接拿后端的
`.pattern` 当基准去前端源码里找。
"""
import os
import re

import pytest
from conftest import PROJECT_ROOT

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')
APP = os.path.join(RENDERER, 'js', 'app')


def _read(rel):
    return open(os.path.join(RENDERER, rel), encoding='utf-8').read()


def _app(name):
    return open(os.path.join(APP, name), encoding='utf-8').read()


# ---------------- highlight.js ----------------

def test_highlightjs_vendored_and_loaded_before_quill():
    """hljs 必须在仓库里、被 index.html 引用，且**排在 quill.js 之前**。

    Quill 的 syntax 模块在加载那一刻就把 window.hljs 抄进 Q.DEFAULTS；
    顺序反了 → 构造 Quill 抛 "Syntax module requires highlight.js"（整个编辑器起不来）。
    """
    lib = os.path.join(RENDERER, 'vendor', 'highlight.min.js')
    assert os.path.isfile(lib), 'highlight.min.js 不在仓库里'
    body = open(lib, encoding='utf-8', errors='replace').read()
    assert 'hljs' in body and len(body) > 50_000, 'highlight.min.js 看起来不是完整的库'
    assert 'highlight.js' in body.lower() or 'Highlight.js' in body, '缺少库标识（版权/版本）'

    html = _read('index.html')
    classic = html.split('var CLASSIC = [')[1].split('];')[0]
    assert 'highlight.min.js' in classic, 'highlight.min.js 没有被加载'
    assert classic.index('highlight.min.js') < classic.index("'quill.js'"), \
        'highlight.js 必须排在 quill.js 之前（Quill 在加载时就抓走 window.hljs）'


def test_no_leftover_vendor_files():
    """下载了但没用上的库不能留在 vendor 里（会让人以为它在生效）。"""
    stale = os.path.join(RENDERER, 'vendor', 'codemirror-searchcursor.js')
    assert not os.path.isfile(stale), '未使用的 codemirror-searchcursor.js 应已删除'
    assert 'codemirror-searchcursor' not in _read('index.html')


def test_hljs_classes_mapped_to_theme_variables():
    """hljs 产出的类名必须映射到 --hl-* 变量，且深色主题要覆盖一遍。

    刻意不 vendor 官方的 github.min.css：它把颜色写死，与本应用 4 套浅色主题 +
    深色主题对不上；映射到变量才能跟着主题走。
    """
    css = _read('style.css')
    for cls in ('hljs-keyword', 'hljs-string', 'hljs-comment', 'hljs-number',
                'hljs-title', 'hljs-type', 'hljs-meta', 'hljs-variable',
                'hljs-addition', 'hljs-deletion'):
        assert '.' + cls in css, '缺少配色规则：.%s' % cls
    assert css.count('var(--hl-keyword)') >= 1
    assert re.search(r'body\[data-theme="dark"\]\s*\{[^}]*--hl-keyword', css), \
        '深色主题必须覆盖 --hl-*（否则浅色配色落在深底上）'


def test_preview_and_quill_highlight_wired():
    """markdown-it 挂 highlight 钩子；Quill 开 syntax 且用 window.hljs 做前置判断。"""
    render = _app('14-markdown-render.js')
    assert 'highlight: highlightCode' in render, 'markdown-it 没挂 highlight 钩子'
    assert "highlight(code, { language: lang, ignoreIllegals: true })" in render
    assert "'hljs'" in render, 'class 白名单没放行 hljs（会被消毒剥掉 → 高亮失效）'

    editor = _app('02-editor.js')
    assert re.search(r'syntax:\s*!!window\.hljs', editor), \
        'Quill 的 syntax 模块必须由 window.hljs 是否存在来决定（hljs 缺失时不能开）'


# ---------------- 状态栏 / 查找替换 / 大纲：DOM 与接线 ----------------

@pytest.mark.parametrize('node_id', [
    'editor-status', 'status-text', 'status-format', 'btn-outline',
    'find-bar', 'find-input', 'find-count', 'find-case', 'find-prev', 'find-next',
    'find-close', 'find-replace-row', 'find-replace-input', 'find-replace-one',
    'find-replace-all', 'outline-drawer', 'outline-list', 'outline-close',
])
def test_new_dom_nodes_exist(node_id):
    """模块里 getElementById / $('#x') 拿的东西必须真的在 index.html 里。"""
    assert 'id="%s"' % node_id in _read('index.html'), 'index.html 缺少 #%s' % node_id


def test_modules_are_imported_and_initialised():
    """三个新模块必须被入口 import，并在启动时初始化（孤儿模块 = 代码永不执行）。"""
    entry = _app('00-main.js')
    boot = _app('09-boot.js')
    for name in ('21-status-bar.js', '22-outline.js', '23-find-bar.js'):
        assert "import './%s'" % name in entry, '00-main.js 没有 import %s' % name
    for fn in ('initStatusBar', 'initOutline', 'initFindBar'):
        assert fn + '(' in boot, '09-boot.js 没有调用 %s' % fn


def test_editors_notify_status_bar_outline_and_find():
    """两种编辑器（Quill / CodeMirror）输入后都要刷新状态栏、大纲、查找条。"""
    for name in ('02-editor.js', '13-markdown-editor.js'):
        src = _app(name)
        assert 'updateStatusBar()' in src, '%s 没有刷新状态栏' % name
        assert 'refreshOutline()' in src, '%s 没有刷新大纲' % name
        assert 'refreshFindIfOpen()' in src, '%s 没有在内容变化后重算查找' % name


def test_ctrl_f_opens_find_bar_inside_editor():
    """焦点在编辑器里时 Ctrl+F 打开笔记内查找；在别处仍然聚焦侧栏搜索。"""
    shell = _app('05-shell.js')
    assert 'openFindBar(true)' in shell, 'Ctrl+F 没有接到笔记内查找'
    assert "dom.searchInput.focus()" in shell, '焦点不在编辑器时仍应聚焦侧栏搜索'


def test_delta_notes_only_find_not_replace():
    """富文本笔记只查找不替换：改动 Delta 结构要动自定义 blot，风险远大于收益。"""
    find = _app('23-find-bar.js')
    assert "state.noteFormat !== 'md'" in find
    assert find.count("showToast('富文本笔记只支持查找") == 2, \
        'replaceCurrent / replaceAll 都该对富文本给出明确提示（而不是静默不动）'


def test_no_active_note_hides_editor_extras():
    """删除/锁定笔记后编辑区隐藏，状态栏/查找条/大纲不能留在屏幕上显示旧内容。"""
    core = _app('01-core.js')
    assert "document.body.classList.add('no-active-note')" in core
    assert "document.body.classList.remove('no-active-note')" in core
    css = _read('style.css')
    assert 'body.no-active-note .editor-status' in css
    assert 'body.no-active-note .find-bar' in css
    assert 'body.no-active-note .outline-drawer' in css


def test_status_bar_occupies_its_own_grid_row():
    """状态栏是 .editor-container 网格的最后一行；行数不改的话它会盖住编辑区。

    第 9 轮在标签栏后插入了属性行 → 行数 6 → 7，状态栏从第 6 行挪到第 7 行。
    """
    css = _read('style.css')
    assert 'grid-template-rows: auto auto auto auto auto 1fr auto' in css
    assert re.search(r'\.editor-status\s*\{[^}]*grid-row:\s*7', css)


# ---------------- 统计口径必须与后端逐字符一致 ----------------

def test_stats_rules_mirror_backend():
    """前端剥离规则 = 后端 `_markdown_to_text` 的正则对象（逐字符比对，不靠人眼）。

    为什么值得这么严：状态栏实时算的是**未保存**的输入，只能在前端现算；
    但侧栏字数来自后端派生索引。两边规则差一个字符，用户就会看到
    "列表里 123 字、状态栏 156 字"这种没法解释的现象。
    """
    import backend

    # JS 正则字面量里 `/` 必须写成 `\/`，比对前先还原（只有这一处差异）
    js = _app('21-status-bar.js').replace('<\\/', '</')
    assert backend.WORD_RE.pattern in js, \
        '字数口径漂移：前端应使用与 backend.WORD_RE 相同的 %r' % backend.WORD_RE.pattern
    assert backend._MD_IMAGE.pattern in js, '图片剥离规则与后端不一致'
    assert backend._MD_LINK.pattern in js, '链接剥离规则与后端不一致'
    assert backend._MD_TAG.pattern in js, '内嵌 HTML 剥离规则与后端不一致'
    assert backend._MD_INLINE.pattern in js, '行内标记剥离规则与后端不一致'
    assert backend._MD_BLOCK.pattern in js, '块级前缀剥离规则与后端不一致'
    # script/style 的整段匹配：后端用 re.S 的 `.*?`，JS 里只能写 [\s\S]*?（语义相同）
    assert backend._MD_SCRIPT.pattern.replace('.*?', '[\\s\\S]*?') in js, \
        'script/style 剥离规则与后端不一致'
    # 第 9 轮：属性（front-matter）不算正文，两边必须同时剥。
    # 状态栏那份镜像不需要捕获组（它只做替换），所以比对时把括号一起去掉。
    assert backend._FM_RE.pattern.replace('(.*?)', '[\\s\\S]*?') in js, \
        'front-matter 剥离规则与后端不一致（只改一边会让字数又分叉）'
    for ent, _ch in backend._MD_ENTITIES:
        assert ent in js, '实体解码缺少 %s（与后端 _MD_ENTITIES 不一致）' % ent
    # `&amp;` 必须最后解，否则 `&amp;lt;` 会被解成 `<`
    assert js.index("['&lt;'") < js.index("['&amp;'")


def test_char_count_ignores_whitespace_like_backend():
    """字符数不含空白，且按**码点**计数（Python 的 len 语义），不然 emoji 会被算成 2。"""
    js = _app('21-status-bar.js')
    assert r"text.replace(/\s+/g, '')" in js
    assert '[...text.replace' in js, '字符数应按码点计数（与 Python len 对齐）'
