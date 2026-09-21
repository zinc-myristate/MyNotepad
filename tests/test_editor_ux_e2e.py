# -*- coding: utf-8 -*-
"""第 8 轮「编辑器手感」的实机回归（无头 pywebview）。

这一轮四样东西都只能在真实浏览器里验：
  · 状态栏数字——要与后端派生索引**同口径**，是前端现算 vs 后端算，只能实测比对；
  · 查找替换——CodeMirror 的 markText/replaceRange 与光标、滚动的交互；
  · 大纲——从源码/Delta 抓标题并跳转；
  · 代码高亮——markdown-it 的 highlight 钩子与 Quill 的 syntax 模块各自真的产出 hljs 类名。

数字口径的断言刻意**不写死期望值**，而是拿 backend 的同名函数现算：写死常数只证明
"今天这两处相等"，拿后端现算才能在未来任何一方改动时立刻失败。
"""
import json
import threading
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, make_delta_note

RENDERER = PROJECT_ROOT + '/renderer'

MD_SAMPLE = (
    '# 标题一\n'
    '\n'
    '这是**正文**内容，含 `代码` 与 [链接](https://example.com)。\n'
    '\n'
    '- [ ] 待办一\n'
    '- [x] 待办二\n'
    '\n'
    '```js\n'
    'const a = 1;\n'
    '```\n'
)


def _run(ns, actions, wait_before=8):
    import webview
    result = {}
    window = webview.create_window('编辑器手感回归', RENDERER + '/index.html',
                                   js_api=ns['api'], width=1200, height=800)

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


def _md_note(api, title, text):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': title, 'content': text})
    return nid


def _focus_editor(window):
    window.evaluate_js("document.querySelector('.CodeMirror').CodeMirror.focus();")


def _ctrl_f(window):
    window.evaluate_js(
        "document.dispatchEvent(new KeyboardEvent('keydown',"
        "{key:'f', ctrlKey:true, bubbles:true, cancelable:true}));")


def _type_find(window, text):
    window.evaluate_js(
        "var i=document.getElementById('find-input'); i.value=%s;"
        "i.dispatchEvent(new Event('input',{bubbles:true}));" % json.dumps(text))


def _status_words(window):
    """状态栏第一个数字（没有选区时就是全文字数）"""
    txt = window.evaluate_js("document.getElementById('status-text').textContent") or ''
    return txt


def _first_number(text):
    import re
    m = re.search(r'(\d+)', text or '')
    return int(m.group(1)) if m else None


# ---------------- 状态栏：与后端派生索引同口径 ----------------

@pytest.mark.e2e
def test_status_bar_counts_match_backend(tmp_path, monkeypatch):
    """Markdown 笔记的状态栏字数/字符数必须与后端 note_derived 完全一致，且随输入实时变。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = _md_note(backend.api, '状态栏', MD_SAMPLE)
    # 期望值必须在跑界面**之前**取：跑完界面后编辑器里的改动已经防抖落库，
    # 那时再取 note_metrics 拿到的是"改后"的数字（第一版就是在这里把 25 和 31 比到一起的）。
    expect = backend.api.note_metrics(nid)

    def actions(window, result):
        time.sleep(1.5)
        result['note_format'] = window.evaluate_js("window.__app.state.noteFormat")
        result['visible'] = window.evaluate_js(
            "!document.getElementById('editor-status').classList.contains('hidden')")
        result['after_load'] = _status_words(window)
        result['fmt_label'] = window.evaluate_js(
            "document.getElementById('status-format').textContent")
        # 再输入一段：数字必须跟着变（未保存的输入也要算）
        window.evaluate_js("document.querySelector('.CodeMirror').CodeMirror.setValue(%s)"
                           % json.dumps(MD_SAMPLE + '\n新增一行 abc def\n'))
        time.sleep(0.6)
        result['after_edit'] = _status_words(window)
        result['edited_text'] = window.evaluate_js(
            "document.querySelector('.CodeMirror').CodeMirror.getValue()")
        # 选中一段 → 状态栏应额外显示"选中 N 字"
        window.evaluate_js(
            "var c=document.querySelector('.CodeMirror').CodeMirror; c.setSelection({line:0,ch:0},{line:0,ch:5});")
        time.sleep(0.4)
        result['selected'] = _status_words(window)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['note_format'] == 'md'
    assert r['visible'] is True, '状态栏应显示'
    assert r['fmt_label'] == 'Markdown'

    assert _first_number(r['after_load']) == expect['word_count'], \
        '状态栏字数 %r 与后端派生 %r 不一致' % (r['after_load'], expect['word_count'])
    assert str(expect['char_count']) + ' 字符' in r['after_load'], r['after_load']
    assert r['edited_text'] != MD_SAMPLE, '编辑器没拿到新内容'
    assert backend.api.notes_get(nid)['content'] == r['edited_text'], \
        '编辑器里的改动应照常防抖落库（统计的是未保存输入，但保存链路不能被破坏）'

    # 改动后的期望值用后端同一套函数现算（不写死常数）
    edited = r['edited_text']
    want_words = backend.count_words(backend.note_plain_text(edited, 'md'))
    assert _first_number(r['after_edit']) == want_words, \
        '输入后字数应为 %d，实际 %r' % (want_words, r['after_edit'])
    assert _first_number(r['after_load']) != want_words, '字数应随输入变化'
    assert r['selected'].startswith('选中'), '有选区时应显示选中字数：%r' % r['selected']


@pytest.mark.e2e
def test_status_bar_delta_note_matches_backend(tmp_path, monkeypatch):
    """富文本笔记同样有状态栏，且口径与后端一致（后端走 _delta_to_text 拼 op 文本）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = make_delta_note(backend, '富文本状态栏', '')
    plain = '中文 abc 123\n'
    backend.api.notes_update(nid, {'content': json.dumps(
        {'ops': [{'insert': plain}]})})

    def actions(window, result):
        time.sleep(1.5)
        result['note_format'] = window.evaluate_js("window.__app.state.noteFormat")
        result['label'] = window.evaluate_js(
            "document.getElementById('status-format').textContent")
        result['loaded'] = _status_words(window)
        # 用编辑器真实输入：setText 会走 text-change → 状态栏实时重算
        window.evaluate_js("window.__app.state.quill.setText('中文 abc 123 追加 xyz\\n')")
        time.sleep(0.6)
        result['typed'] = _status_words(window)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['note_format'] == 'delta'
    assert r['label'] == '富文本'
    expected = backend.count_words(plain)
    assert _first_number(r['loaded']) == expected, \
        '富文本状态栏字数 %r，后端 %r' % (r['loaded'], expected)
    want = backend.count_words('中文 abc 123 追加 xyz\n')
    assert _first_number(r['typed']) == want, '输入后应为 %d，实际 %r' % (want, r['typed'])


# ---------------- 查找 / 替换 ----------------

@pytest.mark.e2e
def test_markdown_find_and_replace_all(tmp_path, monkeypatch):
    """Ctrl+F → 高亮全部匹配 → 计数 → 全部替换（含真落库）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = _md_note(backend.api, '查找替换', '苹果 香蕉 苹果\n')

    def actions(window, result):
        time.sleep(1.5)
        _focus_editor(window)
        _ctrl_f(window)
        time.sleep(0.4)
        result['bar_visible'] = window.evaluate_js(
            "!document.getElementById('find-bar').classList.contains('hidden')")
        result['replace_row_visible'] = window.evaluate_js(
            "!document.getElementById('find-replace-row').classList.contains('hidden')")
        result['search_not_focused'] = window.evaluate_js(
            "document.activeElement === document.getElementById('search-input')")
        _type_find(window, '苹果')
        time.sleep(0.4)
        result['count'] = window.evaluate_js("document.getElementById('find-count').textContent")
        result['marks'] = window.evaluate_js("document.querySelectorAll('.cm-find-hit').length")
        # 下一个 → 选区应落在第二处
        window.evaluate_js("document.getElementById('find-next').click();")
        time.sleep(0.3)
        result['count_next'] = window.evaluate_js("document.getElementById('find-count').textContent")
        result['selected'] = window.evaluate_js(
            "document.querySelector('.CodeMirror').CodeMirror.getSelection()")
        # 全部替换
        window.evaluate_js(
            "document.getElementById('find-replace-input').value='梨';")
        window.evaluate_js("document.getElementById('find-replace-all').click();")
        time.sleep(0.4)
        result['source'] = window.evaluate_js(
            "document.querySelector('.CodeMirror').CodeMirror.getValue()")
        result['toast'] = window.evaluate_js(
            "(document.getElementById('save-toast')||{}).textContent || ''")
        time.sleep(1.2)          # 等 500ms 防抖落库

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['bar_visible'] is True, 'Ctrl+F 应打开查找条'
    assert r['replace_row_visible'] is True, 'Markdown 笔记应显示替换行'
    assert r['search_not_focused'] is False, '笔记内查找不该抢侧栏搜索框'
    assert r['count'] == '1/2', '计数应为 1/2，实际 %r' % r['count']
    assert r['marks'] == 2, '两处匹配都该有高亮标记，实际 %r' % r['marks']
    assert r['count_next'] == '2/2', r['count_next']
    assert r['selected'] == '苹果', '跳到下一处应选中匹配文字：%r' % r['selected']
    assert r['source'] == '梨 香蕉 梨\n', '全部替换结果不对：%r' % r['source']
    assert '已替换 2 处' in r['toast'], '应有替换结果提示：%r' % r['toast']
    assert backend.api.notes_get(nid)['content'] == '梨 香蕉 梨\n', '替换结果没有落库'


@pytest.mark.e2e
def test_delta_note_find_only_no_replace(tmp_path, monkeypatch):
    """富文本笔记：只查找跳转，替换入口直接隐藏，正文绝不被改动。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = make_delta_note(backend, '富文本查找', '')
    backend.api.notes_update(nid, {'content': json.dumps(
        {'ops': [{'insert': '苹果 香蕉 苹果\n'}]})})

    def actions(window, result):
        time.sleep(1.5)
        window.evaluate_js("window.__app.state.quill.focus();")
        _ctrl_f(window)
        time.sleep(0.4)
        result['bar_visible'] = window.evaluate_js(
            "!document.getElementById('find-bar').classList.contains('hidden')")
        result['replace_row_hidden'] = window.evaluate_js(
            "document.getElementById('find-replace-row').classList.contains('hidden')")
        _type_find(window, '苹果')
        time.sleep(0.4)
        result['count'] = window.evaluate_js("document.getElementById('find-count').textContent")
        result['selection'] = window.evaluate_js(
            "JSON.stringify(window.__app.state.quill.getSelection())")
        # 即便用脚本点到隐藏的"替换"按钮，也必须只是提示、不动正文
        window.evaluate_js(
            "document.getElementById('find-replace-input').value='梨';"
            "document.getElementById('find-replace-one').click();")
        time.sleep(0.4)
        result['toast'] = window.evaluate_js(
            "(document.getElementById('save-toast')||{}).textContent || ''")
        result['text'] = window.evaluate_js("window.__app.state.quill.getText()")
        window.evaluate_js("document.getElementById('find-close').click();")
        time.sleep(0.3)
        result['closed'] = window.evaluate_js(
            "document.getElementById('find-bar').classList.contains('hidden')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['bar_visible'] is True
    assert r['replace_row_hidden'] is True, '富文本笔记不该出现替换行'
    assert r['count'] == '1/2', r['count']
    sel = json.loads(r['selection'])
    assert sel and sel.get('length') == 2, '找到匹配应选中它：%r' % r['selection']
    assert '只支持查找' in r['toast'], '应提示富文本只支持查找：%r' % r['toast']
    assert r['text'].count('苹果') == 2, '正文不能被改动：%r' % r['text']
    assert r['closed'] is True, '关闭按钮应能收起查找条'


# ---------------- 大纲 ----------------

@pytest.mark.e2e
def test_outline_lists_headings_and_jumps(tmp_path, monkeypatch):
    """大纲：代码围栏里的 # 不算标题；点击条目跳到对应行。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    text = ('# 一级标题\n'
            '正文\n'
            '## 二级标题\n'
            '```\n'
            '# 这行在代码块里，不是标题\n'
            '```\n'
            '### 三级标题\n')
    _md_note(backend.api, '大纲', text)

    def actions(window, result):
        time.sleep(1.5)
        window.evaluate_js("document.getElementById('btn-outline').click();")
        time.sleep(0.5)
        result['visible'] = window.evaluate_js(
            "!document.getElementById('outline-drawer').classList.contains('hidden')")
        result['items'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#outline-list .outline-item .outline-text')]"
            ".map(e => e.textContent))")
        result['indent'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#outline-list .outline-item')]"
            ".map(e => e.style.paddingLeft))")
        # 点第二条（二级标题，第 3 行，0-based = 2）
        window.evaluate_js(
            "document.querySelectorAll('#outline-list .outline-item')[1].click();")
        time.sleep(0.4)
        result['cursor_line'] = window.evaluate_js(
            "document.querySelector('.CodeMirror').CodeMirror.getCursor().line")
        result['active'] = window.evaluate_js(
            "document.querySelectorAll('#outline-list .outline-item.active').length")
        # 关闭按钮
        window.evaluate_js("document.getElementById('outline-close').click();")
        time.sleep(0.3)
        result['closed'] = window.evaluate_js(
            "document.getElementById('outline-drawer').classList.contains('hidden')")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['visible'] is True, '大纲抽屉应打开'
    assert json.loads(r['items']) == ['一级标题', '二级标题', '三级标题'], r['items']
    indent = json.loads(r['indent'])
    assert indent[0] == '8px' and indent[1] != indent[0], '层级应有缩进：%r' % indent
    assert r['cursor_line'] == 2, '点击大纲应跳到第 3 行，实际 %r' % r['cursor_line']
    assert r['active'] == 1, '当前标题应高亮'
    assert r['closed'] is True


# ---------------- 代码高亮 ----------------

@pytest.mark.e2e
def test_markdown_preview_highlights_code(tmp_path, monkeypatch):
    """预览里的 ```js 代码块要真的带 hljs 类名（不是只有等宽字体）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md_note(backend.api, '高亮', '```js\nconst answer = 42;\n```\n')

    def actions(window, result):
        time.sleep(1.5)
        result['hljs_loaded'] = window.evaluate_js("!!window.hljs")
        result['pre_class'] = window.evaluate_js(
            "(document.querySelector('#md-preview pre')||{}).className || ''")
        result['keyword'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('#md-preview .hljs-keyword')]"
            ".map(e => e.textContent))")
        result['number'] = window.evaluate_js(
            "document.querySelectorAll('#md-preview .hljs-number').length")
        result['unknown_lang_ok'] = window.evaluate_js(
            "(function(){var c=document.querySelector('.CodeMirror').CodeMirror;"
            "return c ? true : false;})()")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['hljs_loaded'] is True, 'highlight.js 没有加载（顺序或权限问题）'
    assert 'hljs' in r['pre_class'], 'pre 标签应带 hljs 类：%r' % r['pre_class']
    assert 'const' in json.loads(r['keyword']), 'const 应被识别成关键字：%r' % r['keyword']
    assert r['number'] >= 1, '42 应被识别成数字'


@pytest.mark.e2e
def test_unknown_language_falls_back_to_plain(tmp_path, monkeypatch):
    """未知语言不能报错，要退化成纯文本代码块（宁可没颜色，不能白屏）。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md_note(backend.api, '未知语言', '```不存在的语言\nplain text\n```\n')

    def actions(window, result):
        time.sleep(1.5)
        result['text'] = window.evaluate_js(
            "(document.querySelector('#md-preview pre')||{}).textContent || ''")
        result['errors'] = window.evaluate_js("JSON.stringify(window.__appBoot.failed)")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert 'plain text' in r['text'], '未知语言也应渲染出原文：%r' % r['text']
    assert json.loads(r['errors']) == []


@pytest.mark.e2e
def test_quill_code_block_highlights_via_language_picker(tmp_path, monkeypatch):
    """富文本笔记：代码块 + 选语言 → Quill 的 syntax 模块产出 hljs 类名。

    这条同时验证「hljs 必须排在 quill.js 之前」：顺序错了，Quill 构造时就会抛
    "Syntax module requires highlight.js"，编辑器根本起不来。
    """
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = make_delta_note(backend, '富文本高亮', '')
    backend.api.notes_update(nid, {'content': json.dumps(
        {'ops': [{'insert': 'const answer = 42;\n', 'attributes': {'code-block': True}}]})})

    def actions(window, result):
        time.sleep(1.5)
        result['quill_ok'] = window.evaluate_js("!!window.__app.state.quill")
        result['container'] = window.evaluate_js(
            "document.querySelectorAll('.ql-editor .ql-code-block-container').length")
        # 代码块右上角的下拉就是 Quill syntax 模块挂的 <select class="ql-ui">
        result['picker'] = window.evaluate_js(
            "document.querySelectorAll('.ql-editor .ql-code-block-container select.ql-ui').length")
        window.evaluate_js(
            "(function(){var s=document.querySelector('.ql-editor .ql-code-block-container select.ql-ui');"
            "if(!s) return; s.value='javascript'; s.dispatchEvent(new Event('change'));})()")
        time.sleep(1.2)
        result['keyword'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('.ql-editor .hljs-keyword')]"
            ".map(e => e.textContent))")
        result['number'] = window.evaluate_js(
            "document.querySelectorAll('.ql-editor .hljs-number').length")

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['quill_ok'] is True, 'Quill 没起来（hljs 顺序错了会直接抛异常）'
    assert r['container'] == 1, '应有一个代码块容器：%r' % r['container']
    assert r['picker'] == 1, 'Quill 的 syntax 模块没有挂上语言下拉（模块没启用）'
    assert 'const' in json.loads(r['keyword']), \
        '选语言后应出现 hljs 关键字着色：%r' % r['keyword']
    assert r['number'] >= 1, '42 应被识别成数字'
