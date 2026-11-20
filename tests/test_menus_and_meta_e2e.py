"""界面一轮（2026-11-18）：两个弹出菜单、标题行里的「+ 标签」「+ 属性」、面板关闭的淡出、12px 下限。

- Markdown 工具栏不常用的四个动作收进「插入」菜单，侧栏的主题 / 背景 / 图标收进「外观」菜单：
  开合与键盘在 renderer/js/shared/menu.js，选了一项要先收起菜单、把焦点放回按钮，再跑那一项自己的点击处理
  （面板记下的打开者才是看得见的按钮，关面板时焦点回得来）
- 没有标签、没有属性时那两行不占地方；属性面板开着时属性行一直在（不然第一个 chip 出来时正在打字的输入框会往下跳）
- 关面板：先出栈（键盘、焦点、#app 的 inert 立刻归还），画面上淡出 120ms 再藏起来；这期间又打开就不藏
- 字号：CSS 里不再写小于 12px 的字，只留贴纸的美术字和待办勾选框里的对勾
"""
import json
import os
import re
import time

import pytest
from conftest import PROJECT_ROOT, load_app_partial, make_delta_note, wait_for_js
from test_ui_layout import _run

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')
LOAD_CORE = "import('./js/app/01-core.js').then(m => window.core = m)"
KEY = ("window.key = (key, opts={}) => (opts.on || document.activeElement).dispatchEvent("
       "new KeyboardEvent('keydown', {key, bubbles:true, cancelable:true, ...opts}));")
CM = "document.querySelector('#md-editor .CodeMirror').CodeMirror"


def _md_note(api, title, content):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': title, 'content': content})
    return nid


def _select(window, note_id, fmt='md'):
    window.evaluate_js("document.querySelector('#note-list .note-item[data-note-id=\"%s\"]').click()" % note_id)
    assert wait_for_js(window, "window.__app.state.activeNoteId === %s && window.__app.state.noteFormat === %s"
                               " && !window.__app.state.isLoading" % (json.dumps(note_id), json.dumps(fmt)))


# ---------------- 「插入」菜单 ----------------

@pytest.mark.e2e
def test_insert_menu_keyboard_dismissal_and_insertion(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    nid = _md_note(backend.api, '插入菜单', 'hello\n')

    def actions(window, result):
        assert wait_for_js(window, 'window.__app?.state.activeNoteId', nid) == nid
        assert wait_for_js(window, "!!document.querySelector('#md-editor .CodeMirror')")
        window.evaluate_js(KEY + "window.seenEsc = 0; document.addEventListener('keydown',"
                           " e => { if (e.key === 'Escape') seenEsc++; });")
        menu_open = "!document.getElementById('md-insert-menu').classList.contains('hidden')"
        focused = "document.activeElement.dataset.md || document.activeElement.id"
        expanded = "document.getElementById('md-insert-btn').getAttribute('aria-expanded')"

        window.evaluate_js("document.getElementById('md-insert-btn').focus(); document.activeElement.click()")
        result['open'] = [window.evaluate_js(menu_open), window.evaluate_js(expanded), window.evaluate_js(focused),
                          window.evaluate_js("document.getElementById('md-insert-menu').getAttribute('role')"),
                          window.evaluate_js("document.activeElement.getAttribute('role')")]
        moves = []
        for key in ('ArrowDown', 'ArrowUp', 'ArrowUp', 'Home', 'End', 'ArrowDown'):
            window.evaluate_js("key('%s')" % key)
            moves.append(window.evaluate_js(focused))
        result['moves'] = moves
        # 菜单贴着按钮往下开，而且整个在窗口里（等入场动画播完再量：它带 translateY / scale）
        assert wait_for_js(window, "document.getElementById('md-insert-menu').getAnimations().length === 0")
        result['placed'] = window.evaluate_js("""(() => {
          const b = document.getElementById('md-insert-btn').getBoundingClientRect();
          const m = document.getElementById('md-insert-menu').getBoundingClientRect();
          return m.top >= b.bottom && m.top - b.bottom < 10 && m.left >= 0 && m.right <= innerWidth
                 && m.bottom <= innerHeight;
        })()""")

        # Esc：只收菜单（不往下传），焦点回到按钮
        window.evaluate_js("key('Escape')")
        result['esc'] = [window.evaluate_js(menu_open), window.evaluate_js(expanded),
                         window.evaluate_js(focused), window.evaluate_js('window.seenEsc')]
        # Tab：同样收起、焦点回到按钮
        window.evaluate_js("document.activeElement.click()")
        window.evaluate_js("key('Tab')")
        result['tab'] = [window.evaluate_js(menu_open), window.evaluate_js(focused)]
        # 再点一下按钮：开 → 关
        window.evaluate_js("document.getElementById('md-insert-btn').click()")
        window.evaluate_js("document.getElementById('md-insert-btn').click()")
        result['toggle'] = window.evaluate_js(menu_open)
        # 点别处、窗口变大小：收起
        window.evaluate_js("document.getElementById('md-insert-btn').click();"
                           "document.getElementById('editor-area').dispatchEvent("
                           "new PointerEvent('pointerdown', {bubbles: true}))")
        result['outside'] = window.evaluate_js(menu_open)
        window.evaluate_js("document.getElementById('md-insert-btn').click(); window.dispatchEvent(new Event('resize'))")
        result['resize'] = window.evaluate_js(menu_open)

        # 选一项：插在光标处，菜单收起
        window.evaluate_js(CM + ".setCursor({line: 1, ch: 0})")
        window.evaluate_js("document.getElementById('md-insert-btn').click()")
        window.evaluate_js("document.querySelector('#md-insert-menu [data-md=\"table\"]').click()")
        result['inserted'] = [window.evaluate_js(menu_open), window.evaluate_js(CM + ".getValue()")]
        window.evaluate_js("document.getElementById('md-insert-btn').click()")
        window.evaluate_js("document.querySelector('#md-insert-menu [data-md=\"divider\"]').click()")
        result['divider'] = window.evaluate_js(CM + ".getValue()")

    r = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in r, r
    assert r['open'] == [True, 'true', 'codeblock', 'menu', 'menuitem'], r['open']
    assert r['moves'] == ['table', 'codeblock', 'attachment', 'codeblock', 'attachment', 'codeblock'], r['moves']
    assert r['placed'], '菜单要贴着按钮往下开，不出窗口'
    assert r['esc'] == [False, 'false', 'md-insert-btn', 0], r['esc']
    assert r['tab'] == [False, 'md-insert-btn'], r['tab']
    assert r['toggle'] is False
    assert r['outside'] is False and r['resize'] is False, r
    assert r['inserted'][0] is False
    assert r['inserted'][1].startswith('hello\n') and '| 列一 | 列二 |' in r['inserted'][1], r['inserted'][1]
    assert '<hr class="divider-1">' in r['divider'], r['divider']


# ---------------- 「外观」菜单 ----------------

@pytest.mark.e2e
def test_appearance_menu_opens_each_panel_and_focus_comes_back(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md_note(backend.api, '外观', '正文\n')

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app?.state.activeNoteId)')
        window.evaluate_js(KEY)
        result['footer'] = window.evaluate_js(
            "JSON.stringify([...document.querySelectorAll('.sidebar-footer .btn-sidebar-footer')].map(b => b.id))")
        result['moved'] = window.evaluate_js(
            "['btn-theme', 'btn-background', 'btn-change-icon'].every("
            "id => document.getElementById(id).closest('#appearance-menu'))")
        rounds = {}
        for item, panel in (('btn-theme', 'theme-panel'), ('btn-background', 'background-panel')):
            window.evaluate_js("document.getElementById('btn-appearance').focus(); document.activeElement.click()")
            # 侧栏底部放不下就往上开，整个菜单在窗口里（等入场动画播完再量）
            assert wait_for_js(window, "document.getElementById('appearance-menu').getAnimations().length === 0")
            inside = window.evaluate_js("""(() => {
              const m = document.getElementById('appearance-menu').getBoundingClientRect();
              return m.height > 0 && m.top >= 0 && m.bottom <= innerHeight && m.left >= 0 && m.right <= innerWidth;
            })()""")
            window.evaluate_js("document.getElementById('%s').focus(); document.activeElement.click()" % item)
            opened = wait_for_js(window, "document.getElementById('%s').style.display" % panel, 'flex')
            menu_hidden = window.evaluate_js("document.getElementById('appearance-menu').classList.contains('hidden')")
            window.evaluate_js("key('Escape')")
            # 记成真假：超时时 opened 是 WaitTimeout（假值，== 按最后取到的值比）
            rounds[item] = [inside, opened == 'flex', menu_hidden, window.evaluate_js('document.activeElement.id')]
        result['rounds'] = rounds
        # 一次只开一个菜单
        window.evaluate_js("document.getElementById('md-insert-btn').click(); document.getElementById('btn-appearance').click()")
        result['one_at_a_time'] = window.evaluate_js(
            "[document.getElementById('md-insert-menu').classList.contains('hidden'),"
            " document.getElementById('appearance-menu').classList.contains('hidden'),"
            " document.getElementById('md-insert-btn').getAttribute('aria-expanded')]")

    r = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in r, r
    footer = json.loads(r['footer'])
    assert len(footer) == 7 and 'btn-appearance' in footer, footer
    assert r['moved'] is True
    for item, got in r['rounds'].items():
        assert got == [True, True, True, 'btn-appearance'], (item, got)
    assert r['one_at_a_time'] == [True, False, 'false'], r['one_at_a_time']


# ---------------- 标签行、属性行 ----------------

LAYOUT = """JSON.stringify((() => {
  const box = id => { const el = document.getElementById(id); const r = el.getBoundingClientRect();
                      return getComputedStyle(el).display === 'none' ? null : [Math.round(r.top), Math.round(r.bottom)]; };
  return {title: box('title-row'), tags: box('tag-bar'), props: box('prop-block'), toolbar: box('md-toolbar')};
})())"""


@pytest.mark.e2e
def test_empty_tag_and_property_rows_take_no_space(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    bare = _md_note(backend.api, '什么都没有', '正文\n')
    full = _md_note(backend.api, '有标签有属性', '---\n状态: 进行中\n---\n\n正文\n')
    tag = backend.api.tags_create('项目')['id']
    backend.api.note_tags_set(full, [tag])

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app?.state.activeNoteId)')
        _select(window, full)
        assert wait_for_js(window, "document.querySelectorAll('#tag-list .tag-chip').length", 1) == 1
        result['full'] = json.loads(window.evaluate_js(LAYOUT))
        _select(window, bare)
        assert wait_for_js(window, "document.getElementById('tag-bar').classList.contains('hidden')")
        result['bare'] = json.loads(window.evaluate_js(LAYOUT))
        # 「+ 属性」：属性行出现、面板打开、直接给一个空行，焦点在它的键名框里
        window.evaluate_js("document.getElementById('btn-prop-edit').click()")
        result['opened'] = window.evaluate_js(
            "[document.getElementById('prop-block').classList.contains('hidden'),"
            " document.getElementById('prop-editor').classList.contains('hidden'),"
            " document.querySelectorAll('#prop-rows .prop-row').length,"
            " document.activeElement.classList.contains('prop-key'),"
            " document.getElementById('btn-prop-edit').getAttribute('aria-expanded')]")
        # 面板开着、还没写属性时去正文里打字：属性行和面板都得留着（面板是属性行的子元素）
        window.evaluate_js(CM + ".focus(); " + CM + ".replaceRange('x', {line: 0, ch: 0})")
        time.sleep(0.6)
        result['typing_body'] = window.evaluate_js(
            "[document.getElementById('prop-block').classList.contains('hidden'),"
            " document.getElementById('prop-editor').classList.contains('hidden')]")
        # 正文变了、焦点又不在面板里，面板按正文重建：那个没写键名的空行不在正文里，跟着没了，再加一行
        window.evaluate_js("document.getElementById('btn-prop-add').click()")
        assert window.evaluate_js("document.activeElement.classList.contains('prop-key')")
        top_before = window.evaluate_js("document.activeElement.getBoundingClientRect().top")
        window.evaluate_js("(el => { el.value = '来源'; el.dispatchEvent(new Event('input', {bubbles: true})); })"
                           "(document.activeElement)")
        assert wait_for_js(window, "document.getElementById('prop-chips').textContent.includes('来源')")
        # 第一个 chip 出来了，正在打字的输入框不能跳
        result['jump'] = window.evaluate_js("document.activeElement.getBoundingClientRect().top") - top_before
        window.evaluate_js("document.getElementById('btn-prop-done').click()")
        result['done'] = window.evaluate_js(
            "[document.getElementById('prop-block').classList.contains('hidden'),"
            " document.getElementById('btn-prop-edit').getAttribute('aria-expanded')]")
        result['source'] = window.evaluate_js(CM + ".getValue()")
        # 打开又什么都不写就关掉：这一行重新消失，正文不变
        _select(window, full)
        _select(window, bare)
        window.evaluate_js(CM + ".setValue('正文\\n')")
        assert wait_for_js(window, "document.getElementById('prop-block').classList.contains('hidden')")
        window.evaluate_js("document.getElementById('btn-prop-edit').click(); document.getElementById('btn-prop-done').click()")
        result['empty_close'] = [window.evaluate_js("document.getElementById('prop-block').classList.contains('hidden')"),
                                 window.evaluate_js(CM + ".getValue()")]
        # 删掉最后一个标签：标签行跟着消失
        _select(window, full)
        assert wait_for_js(window, "document.querySelectorAll('#tag-list .tag-chip').length", 1) == 1
        window.evaluate_js("document.querySelector('#tag-list .tag-remove').click()")
        result['untagged'] = bool(wait_for_js(window, "document.getElementById('tag-bar').classList.contains('hidden')"))

    r = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in r, r
    full, bare = r['full'], r['bare']
    assert full['tags'] and full['props'], full
    assert bare['tags'] is None and bare['props'] is None, bare
    # 什么都没有的笔记：工具栏紧挨着标题行；有标签有属性的笔记比它多出的就是那两行
    assert abs(bare['toolbar'][0] - bare['title'][1]) <= 1, bare
    extra = (full['tags'][1] - full['tags'][0]) + (full['props'][1] - full['props'][0])
    assert abs((full['toolbar'][0] - bare['toolbar'][0]) - extra) <= 1, (full, bare)
    assert r['opened'] == [False, False, 1, True, 'true'], r['opened']
    assert r['typing_body'] == [False, False], r['typing_body']
    assert abs(r['jump']) < 1, '加第一个属性时输入框跳了 %rpx' % r['jump']
    assert r['done'] == [False, 'false'], r['done']
    assert r['source'].startswith('---\n来源:'), r['source']
    assert r['empty_close'] == [True, '正文\n'], r['empty_close']
    assert r['untagged'] is True


# ---------------- 面板关闭的淡出 ----------------

@pytest.mark.e2e
def test_panel_close_fades_out_but_releases_the_workspace_at_once(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    _md_note(backend.api, '面板', '正文\n')
    state = ("(p => [core.hasOpenPanel(), document.getElementById('app').inert, p.style.display,"
             " p.classList.contains('is-closing'), p.inert, getComputedStyle(p).pointerEvents])"
             "(document.getElementById('theme-panel'))")
    # 关面板和读状态必须在同一次调用里：两次 evaluate_js 之间可能就过了 120ms（CI 上红过，面板已经藏了）。
    # 「减少动态效果」也在这里定死：CI 的 Windows 上要是开着，关面板本来就直接藏，测不到淡出。
    # 替身只在这一次调用里，别的代码碰不到
    close = ("((p, reduce) => { const real = window.matchMedia;"
             " window.matchMedia = q => ({matches: reduce && /reduce/.test(q), media: q});"
             " try { core.closePanel(p); } finally { window.matchMedia = real; }"
             " return %s; })(document.getElementById('theme-panel'), %%s)" % state)

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app?.state.activeNoteId)')
        window.evaluate_js(LOAD_CORE)
        assert wait_for_js(window, 'Boolean(window.core)')
        panel = "document.getElementById('theme-panel')"
        window.evaluate_js("document.getElementById('btn-appearance').focus(); core.openPanel(%s)" % panel)
        result['closing'] = window.evaluate_js(close % 'false')
        result['focus'] = window.evaluate_js('document.activeElement.id')
        t0 = time.time()
        assert wait_for_js(window, "%s.style.display" % panel, 'none', timeout=3) == 'none'
        result['hidden_after'] = round(time.time() - t0, 2)
        result['closed'] = window.evaluate_js(state)
        # 淡出的那一下又打开：不能被之前排好的定时器藏掉
        window.evaluate_js("core.openPanel(%s); %s; core.openPanel(%s)" % (panel, close % 'false', panel))
        time.sleep(0.4)
        result['reopened'] = window.evaluate_js(state)
        window.evaluate_js(close % 'false')
        assert wait_for_js(window, "%s.style.display" % panel, 'none', timeout=3) == 'none'
        # 减少动态效果：直接藏
        window.evaluate_js("core.openPanel(%s)" % panel)
        result['reduced'] = window.evaluate_js(close % 'true')

    r = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in r, r
    assert r['closing'] == [False, False, 'flex', True, True, 'none'], r['closing']
    assert r['focus'] == 'btn-appearance'
    assert r['hidden_after'] < 1.5, r['hidden_after']
    assert r['closed'] == [False, False, 'none', False, False, 'auto'], r['closed']
    assert r['reopened'] == [True, True, 'flex', False, False, 'auto'], r['reopened']
    assert r['reduced'] == [False, False, 'none', False, False, 'auto'], r['reduced']


# ---------------- 12px 下限 ----------------

SMALL_TEXT = """JSON.stringify((() => {
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
    if (!r.width || !r.height || cs.visibility === 'hidden' || cs.display === 'none') continue;
    if (el.closest('.CodeMirror, .ql-editor, .md-preview')) continue;      // 正文里的字号是内容，不归界面管
    if (![...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())) continue;
    const size = parseFloat(cs.fontSize);
    if (size < 12) out.push((el.id || el.className || el.tagName) + ' ' + size);
  }
  return out;
})())"""


@pytest.mark.e2e
def test_no_visible_interface_text_under_12px(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    md = _md_note(backend.api, '有标签有属性', '---\n状态: 进行中\n---\n\n# 小标题\n\n正文\n')
    backend.api.note_tags_set(md, [backend.api.tags_create('项目')['id']])
    rich = make_delta_note(backend, '富文本', json.dumps({'ops': [{'insert': '正文\n'}]}))

    def actions(window, result):
        assert wait_for_js(window, 'Boolean(window.__app?.state.activeNoteId)')
        found = {}
        _select(window, md)
        time.sleep(0.5)
        found['md'] = json.loads(window.evaluate_js(SMALL_TEXT))
        _select(window, rich, 'delta')
        time.sleep(0.5)
        found['rich'] = json.loads(window.evaluate_js(SMALL_TEXT))
        for opener, panel in (('btn-calendar', 'calendar-panel'), ('btn-todos', 'todo-panel'),
                              ('btn-tag-manager', 'tag-manager-panel')):
            window.evaluate_js("document.getElementById('%s').click()" % opener)
            assert wait_for_js(window, "document.getElementById('%s').style.display" % panel, 'flex') == 'flex'
            time.sleep(0.4)
            found[panel] = json.loads(window.evaluate_js(SMALL_TEXT))
            window.evaluate_js("document.querySelector('#%s .btn-close-panel').click()" % panel)
            assert wait_for_js(window, "document.getElementById('%s').style.display" % panel, 'none') == 'none'
        result['found'] = found

    r = _run(ns, actions, wait_before=0, wait_after=0)
    assert 'error' not in r, r
    assert all(v == [] for v in r['found'].values()), r['found']


# 小于 12px 只剩这几处，都不是界面文字：贴纸是插进正文的美术字（尺寸跟着贴纸走），
# 对勾画在 16px 的勾选框里
SMALL_FONT_ALLOWED = (
    '.sticker-date .sticker-date-month',
    '.sticker-stamp',
    '.ql-editor li[data-list="checked"]::before',
)


def _small_fonts(path):
    """返回 (选择器, 行) 列表：CSS 里写死的小于 12px 的字号（font-size 和 font 简写都算）"""
    text = open(path, encoding='utf-8').read()
    found = []
    for m in re.finditer(r'font(?:-size)?\s*:\s*([^;}]*)', text):
        size = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)px', m.group(1))
        if not size or float(size.group(1)) >= 12:
            continue
        head = text.rfind('{', 0, m.start())
        prev = max(text.rfind('}', 0, head), text.rfind('*/', 0, head))
        selector = ' '.join(text[prev + 1:head].replace('*/', '').split())
        found.append((selector, text.count('\n', 0, m.start()) + 1))
    return found


def test_css_has_no_interface_text_under_12px():
    bad = []
    for name in ('style.css', 'workspace.css', 'capture.css'):
        for selector, line in _small_fonts(os.path.join(RENDERER, name)):
            if not any(selector.endswith(allowed) for allowed in SMALL_FONT_ALLOWED):
                bad.append('%s:%d %s' % (name, line, selector))
    assert bad == [], bad
    # 页面里拼出来的行内样式也算（笔记本下拉里的篇数以前是 10px）
    for folder, _dirs, files in os.walk(os.path.join(RENDERER, 'js')):
        for name in files:
            if name.endswith('.js'):
                code = open(os.path.join(folder, name), encoding='utf-8').read()
                for m in re.finditer(r'font-size\s*:\s*(\d+(?:\.\d+)?)px', code):
                    assert float(m.group(1)) >= 12, '%s 里有 %s' % (name, m.group(0))
