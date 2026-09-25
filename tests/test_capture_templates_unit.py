# -*- coding: utf-8 -*-
"""第十轮「捕获与模板」的第二批单测：截图坐标换算、裁剪、模板建笔记、桥接与界面接线。

为什么截图这一块要单独测：**坐标算错是这一轮唯一"用户会立刻看出来但代码不会报错"的地方**
（框了 A 区域，裁出来是 B 区域）。所以把最危险的一段抽成纯函数
（`desktop.map_selection_to_image`）在这里逐条钉死，实机那部分才剩下"窗口摆得对不对"。

配套的实机测试见 tests/test_capture_windows_e2e.py（真浏览器里跑覆盖窗与迷你窗的页面）。
"""
import json
import os

import pytest
from conftest import PROJECT_ROOT

import desktop

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')


# ---------------- 截图坐标换算（纯函数） ----------------

def test_map_selection_scales_css_to_physical():
    """本机典型场景：客户区 3072×1920 物理，视口 1536×960 CSS（200% 缩放）→ 2 倍。"""
    box = desktop.map_selection_to_image(
        (100, 50, 200, 100), (1536, 960), (0, 0, 3072, 1920), (0, 0), (3072, 1920))
    assert box == (200, 100, 400, 200)


def test_map_selection_carries_client_origin():
    """覆盖窗不在屏幕左上（副屏在左边时原点可能是负数）：物理原点要带着走。"""
    box = desktop.map_selection_to_image(
        (0, 0, 10, 10), (100, 100), (200, 100, 200, 200), (0, 0), (400, 300))
    assert box == (200, 100, 20, 20)

    box2 = desktop.map_selection_to_image(
        (10, 10, 10, 10), (100, 100), (-1280, 0, 1280, 720), (-1920, 0), (3840, 1080))
    # 比例 = 1280/100 = 12.8；x = -1280 + 10*12.8 - (-1920) = 768
    assert box2 == (768, 72, 128, 72), '副屏在左侧时不能拿 (0,0) 当左上角'


def test_map_selection_uses_view_origin_not_screen_origin():
    """显示图就是整屏图（裁剪失败的回退路径）时，原点换成整图原点，公式必须跟着换。"""
    box = desktop.map_selection_to_image(
        (0, 0, 10, 10), (100, 100), (200, 100, 200, 200), (200, 100), (400, 300))
    assert box == (0, 0, 20, 20)


def test_map_selection_clamps_into_image():
    """选区拖到窗口外一点点（或窗口比屏幕大）：裁进图内，绝不返回越界矩形。"""
    box = desktop.map_selection_to_image(
        (-50, -50, 200, 200), (100, 100), (0, 0, 100, 100), (0, 0), (100, 100))
    assert box == (0, 0, 100, 100)


def test_map_selection_rejects_bad_input():
    good = ((0, 0, 10, 10), (100, 100), (0, 0, 100, 100), (0, 0), (100, 100))
    assert desktop.map_selection_to_image(*good) == (0, 0, 10, 10)
    # 参数缺失 / 尺寸为 0 / 选区小到 1 像素以内 → None（调用方据此提示"选区太小"）
    assert desktop.map_selection_to_image(None, good[1], good[2], good[3], good[4]) is None
    assert desktop.map_selection_to_image((0, 0, 10, 10), (0, 0), good[2], good[3], good[4]) is None
    assert desktop.map_selection_to_image((0, 0, 1, 1), (1000, 1000), good[2], good[3], good[4]) is None
    assert desktop.map_selection_to_image(*('' for _ in range(5))) is None


def test_map_selection_matches_js_side_viewport_contract():
    """JS 上报的是 CSS px 与 innerWidth/innerHeight（spike 实测视口常是半个像素）。

    视口 761.5 × 450（客户区 1523 × 900 物理，200% 缩放）→ 正好 2 倍，
    所以 CSS 的 400,200 变成图片里的 800,400；比例只能由**客户区宽度**算出来。
    """
    box = desktop.map_selection_to_image(
        (400, 200, 100, 50), (761.5, 450), (0, 0, 1523, 900), (0, 0), (1523, 900))
    assert box == (800, 400, 200, 100)


# ---------------- 裁剪 / 抓屏 ----------------

def test_crop_png_cuts_exact_box(tmp_path):
    from PIL import Image
    src = os.path.join(str(tmp_path), 'src.png')
    dst = os.path.join(str(tmp_path), 'dst.png')
    img = Image.new('RGB', (100, 80), (255, 255, 255))
    img.paste((10, 20, 30), (10, 10, 30, 30))
    img.save(src)
    assert desktop.crop_png(src, (10, 10, 20, 20), dst)
    with Image.open(dst) as out:
        assert out.size == (20, 20)
        assert out.getpixel((5, 5)) == (10, 20, 30)


def test_crop_png_bad_input_returns_false(tmp_path):
    assert desktop.crop_png(os.path.join(str(tmp_path), 'nope.png'), (0, 0, 5, 5),
                            os.path.join(str(tmp_path), 'o.png')) is False


def test_virtual_screen_rect_is_sane():
    rect = desktop.virtual_screen_rect()
    if rect is None:
        pytest.skip('取不到虚拟屏幕（非 Windows）')
    x, y, w, h = rect
    assert w > 0 and h > 0
    assert abs(x) < 100000 and abs(y) < 100000


# ---------------- 模板 → 笔记 ----------------

def test_notes_create_from_template_renders_and_titles(api, backend_mod):
    tpl = api.template_create('会议记录', '# {{title}}\n\n{{date}}\n')
    note = api.notes_create_from_template(tpl['id'], '周一例会')
    assert note['title'] == '周一例会'
    assert '# 周一例会' in note['content']
    assert note['format'] == 'md'
    # 标题留空 → 回落成模板名（捕获菜单里点模板名就是这条路径）
    note2 = api.notes_create_from_template(tpl['id'], '')
    assert note2['title'] == '会议记录'
    assert '# 会议记录' in note2['content']
    assert note2['id'] != note['id']


def test_notes_create_from_template_falls_back_to_notebook(api, backend_mod):
    tpl = api.template_create('速记', '正文')
    note = api.notes_create_from_template(tpl['id'], None, notebook_name='速记本')
    nbs = {nb['name']: nb['id'] for nb in api.notebooks_list()}
    assert note['notebook_id'] == nbs['速记本']
    # 不传笔记本就不建笔记本
    before = len(api.notebooks_list())
    api.notes_create_from_template(tpl['id'], 'x')
    assert len(api.notebooks_list()) == before


def test_notes_create_from_template_missing_template_still_creates(api, backend_mod):
    """模板在别处被删了：宁可建一篇空笔记，也不要一个"点了没反应"的按钮。"""
    note = api.notes_create_from_template('no-such-template', '标题')
    assert note and note['id']
    assert note['title'] == '标题'
    assert (note['content'] or '') == ''


# ---------------- 桥接与界面接线（静态） ----------------

def _read(rel):
    with open(os.path.join(PROJECT_ROOT, rel), encoding='utf-8') as fh:
        return fh.read()


def test_app_bridges_for_capture_and_templates():
    src = _read('app.pyw')
    for name in ('capture_begin', 'capture_overlay_info', 'capture_overlay_ready',
                 'capture_commit', 'capture_mini_open', 'capture_mini_submit',
                 'capture_mini_close', 'notes_create_from_template',
                 'set_capture_hotkey_enabled'):
        assert 'def %s(' % name in src, '缺少桥接方法 %s' % name
    # 两个窗口都用真实页面（不是内联 HTML：那两个页面的 JS 要能在 e2e 里单独跑）
    assert "os.path.join(BASE_DIR, 'renderer', 'capture-overlay.html')" in src
    assert "os.path.join(BASE_DIR, 'renderer', 'capture-mini.html')" in src
    # 覆盖窗必须**不透明**且 frameless：WebView2 的 transparent=True 实测无效
    assert 'transparent=' not in src.split('def _capture_begin')[1].split('def _')[0]


def test_capture_hotkey_is_off_by_default_and_separate_id():
    src = _read('app.pyw')
    assert "CAPTURE_HOTKEY_KEY = 'capture_hotkey'" in src
    assert "(backend_api.settings_get(CAPTURE_HOTKEY_KEY) or '0') == '1'" in src, '热键默认必须是关'
    desktop_src = _read('desktop.py')
    assert 'VK_S = 0x53' in desktop_src
    assert 'hotkey_id' in desktop_src, '两个热键必须用不同的 id（否则互相顶掉）'


def test_main_window_dom_and_wiring():
    html = _read('renderer/index.html')
    for el in ('btn-capture', 'capture-menu', 'cap-mini', 'cap-clipboard', 'cap-screenshot',
               'cap-daily', 'capture-tpl-list', 'cap-tpl-manage', 'templates-drawer',
               'tpl-list', 'tpl-name', 'tpl-title', 'tpl-content', 'tpl-preview',
               'tpl-create-note', 'tpl-insert', 'tpl-delete', 'tpl-new', 'templates-close',
               'chk-capture-hotkey', 'row-capture-hotkey'):
        assert 'id="%s"' % el in html, 'index.html 缺少 #%s' % el
    # 变量按钮与后端支持的那几个变量对得上（多一个少一个都会让用户写不出东西）
    import backend
    for var in ('date', 'time', 'weekday', 'title', 'datetime'):
        assert '{{%s}}' % var in html, '变量按钮缺少 %s' % var


def test_new_modules_are_imported_and_initialised():
    """模块被 import 但没有 init → 代码永不执行（静态检查抓不到的那一类"半死"）。"""
    main = _read('renderer/js/app/00-main.js')
    boot = _read('renderer/js/app/09-boot.js')
    for mod in ('27-templates.js', '28-capture.js'):
        assert mod in main, '%s 没有被入口 import' % mod
    for fn in ('initTemplates', 'initCapture'):
        assert fn in boot and ('%s()' % fn) in boot, '%s 没有在 09-boot 里调用' % fn
    # 三个右侧抽屉互斥（新增抽屉必须通知另外两个）
    for rel in ('renderer/js/app/22-outline.js', 'renderer/js/app/25-links.js'):
        assert 'myapp:templates-opened' in _read(rel), '%s 没有参与抽屉互斥' % rel
    assert 'myapp:templates-opened' in _read('renderer/js/app/27-templates.js')


def test_capture_menu_is_closed_on_startup_and_has_styles():
    html = _read('renderer/index.html')
    assert 'id="capture-menu" class="capture-menu hidden"' in html, '菜单初始必须是收起的'
    css = _read('renderer/style.css')
    for sel in ('.capture-menu', '.capture-item', '.templates-drawer', '.tpl-textarea',
                '.tpl-preview', '.sidebar-footer-wrap'):
        assert sel in css, 'style.css 缺少 %s' % sel
    cap_css = _read('renderer/capture.css')
    for sel in ('.ov-shot', '.ov-sel', '.ov-handle', '.ov-mag', '.ov-bar', '.mini-input',
                '.mini-save'):
        assert sel in cap_css, 'capture.css 缺少 %s' % sel
    # 无边框迷你窗靠 pywebview 的拖动区类名（easy_drag=False：整窗可拖会让选文字变成拖窗口）
    mini_html = _read('renderer/capture-mini.html')
    assert 'pywebview-drag-region' in mini_html


def test_calendar_today_opens_daily_note():
    src = _read('renderer/js/app/08-appearance2.js')
    body = src.split('async function onCalendarDateClick')[1].split('async function')[0]
    assert 'daily_note_open' in body, '点"今天"必须走每日笔记（幂等 + 套日记模板）'
    assert 'dateStr === todayStr' in body, '只有"今天"走每日笔记，其它日期保持原样'


def test_capture_overlay_reports_viewport_and_css_rect():
    """覆盖窗只上报 CSS px + 视口，物理换算全在 Python（避免两处各算一遍）。"""
    src = _read('renderer/js/capture/overlay.js')
    assert 'capture_commit(' in src
    assert 'window.innerWidth' in src and 'window.innerHeight' in src
    assert 'capture_overlay_info' in src and 'capture_overlay_ready' in src
    # 参数个数也要对得上（桥接侧少一个参数 = 整次截图静默作废，实测踩到过）
    assert 'capture_overlay_info(window.innerWidth, window.innerHeight)' in src
    # Enter 的默认动作：有正在编辑的笔记就插入，否则存为新笔记
    assert "info.note_id" in src
    mini = _read('renderer/js/capture/mini.js')
    assert 'capture_mini_submit' in mini and 'ctrlKey' in mini
    assert 'Escape' in mini


def test_bridge_signatures_accept_the_arguments_js_passes(app_ns):
    """**桥接方法的参数个数**必须容得下 JS 实参——少一个就是 TypeError + 功能静默失效。

    为什么专门测这条：`capture_overlay_info` 从"不带参数"改成"带视口"时，
    `_capture_overlay_info` 改了、AppApi 上的桥接却漏改，结果覆盖窗每次都拿不到冻屏图、
    直接取消——现象是"点截图没反应"。而 e2e 用的是假桥接，抓不到这种不一致。
    """
    import inspect
    expected = {
        'capture_begin': 1,          # capture_begin(activeNoteId)
        'capture_overlay_info': 2,   # capture_overlay_info(innerWidth, innerHeight)
        'capture_overlay_ready': 0,
        'capture_commit': 3,         # capture_commit(action, rect, viewport)
        'capture_mini_open': 0,
        'capture_mini_submit': 2,    # capture_mini_submit(text, keepOpen)
        'capture_mini_close': 0,
        'clipboard_capture': 0,
        'daily_note_open': 0,
        'capture_text': 1,
        'capture_image': 3,          # capture_image(path, title, body) —— 第 11 轮加了 body
        'template_render': 2,
        'template_update': 2,
        'template_create': 2,
        'notes_create_from_template': 3,
        'file_copy_to_note': 3,
        # 第 11 轮：OCR
        'ocr_languages': 0,
        'ocr_recognize': 2,
        'ocr_pick_image': 0,
        'ocr_clipboard_image': 0,
        'ocr_release': 1,
    }
    api_cls = app_ns['AppApi']
    bad = []
    for name, need in expected.items():
        sig = inspect.signature(getattr(api_cls, name))
        pos = [p for p in sig.parameters.values()
               if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        have = len(pos) - 1          # 减掉 self
        if have < need:
            bad.append('%s 只能收 %d 个参数，JS 会传 %d 个' % (name, have, need))
    assert not bad, '桥接签名与前端调用不一致：\n' + '\n'.join(bad)


def test_capture_bridge_exposed_for_cross_window_calls():
    src = _read('renderer/js/app/28-capture.js')
    assert 'window.__capture' in src
    assert 'insertImage' in src and 'afterExternalCapture' in src
    # 富文本笔记插图片走既有 embed 路径（不重复复制附件）
    assert 'insertImageResult' in src
    assert 'attachments/' in src or 'applyMarkdownAction' in src


def test_templates_module_uses_backend_renderer_for_preview():
    """预览必须走后端 template_render：前端自己拼一遍就是第二个真相源。"""
    src = _read('renderer/js/app/27-templates.js')
    assert 'template_render' in src
    assert 'notes_create_from_template' in src
    # 注释里提到 `{{date}}` 没问题，代码里出现 `{{` 就是在自己拼变量了
    code = '\n'.join(line for line in src.splitlines() if not line.strip().startswith('//'))
    assert '{{' not in code, '前端不应自己实现变量替换（否则预览与真实结果会分叉）'


def test_capture_pages_load_their_module():
    for page, mod in (('capture-overlay.html', 'js/capture/overlay.js'),
                      ('capture-mini.html', 'js/capture/mini.js')):
        html = _read(os.path.join('renderer', page))
        assert 'type="module" src="%s"' % mod in html
        assert 'capture.css' in html
    # 两个窗口页都不该出现 emoji 当图标（与主界面同一条约定）
    for page in ('capture-overlay.html', 'capture-mini.html'):
        html = _read(os.path.join('renderer', page))
        for ch in ('🖼', '📎', '✅', '❌', '📅', '🔔', '✕', '×'):
            assert ch not in html, '%s 里出现了文字/emoji 图标 %r' % (page, ch)


def test_templates_table_has_no_leftover_ui_state():
    """模板内容不许留在前端持久化里：模板是数据库里的一张表，刷新页面必须还在。"""
    src = _read('renderer/js/app/27-templates.js')
    assert 'localStorage' not in src
    assert 'templates_list' in src and 'template_update' in src


def test_json_payload_passed_to_main_window_is_serialisable():
    """跨窗口调用用的是 json.dumps 拼 JS：payload 必须是能序列化的普通对象。"""
    src = _read('app.pyw')
    seg = src.split('def _capture_commit')[1].split('def _capture_end')[0]
    assert 'json.dumps(result.get(\'saved\') or {})' in seg
    json.dumps({'id': 'x', 'filename': 'a.png', 'storedPath': 'C:/x/a.png'})


# ---------------- 第 11 轮：识别文字（OCR）的接线 ----------------

def test_ocr_module_wired_into_frontend():
    main = _read('renderer/js/app/00-main.js')
    boot = _read('renderer/js/app/09-boot.js')
    assert '29-ocr.js' in main, 'OCR 模块没有被入口 import'
    assert 'initOcr' in boot and 'initOcr()' in boot, 'initOcr 没有在 09-boot 里调用'
    core = _read('renderer/js/app/01-core.js')
    assert "'ocr-panel'" in core, '新面板必须登记进 ALL_PANEL_IDS（否则"关闭所有面板"漏掉它）'


def test_ocr_panel_dom_and_entries():
    html = _read('renderer/index.html')
    for el in ('ocr-panel', 'ocr-thumb', 'ocr-lang', 'ocr-status', 'ocr-retry',
               'ocr-text', 'ocr-hint', 'ocr-insert', 'ocr-copy', 'ocr-note', 'ocr-cancel',
               'cap-ocr-file', 'cap-ocr-clipboard'):
        assert 'id="%s"' % el in html, 'index.html 缺少 #%s' % el
    css = _read('renderer/style.css')
    for sel in ('.ocr-dialog', '.ocr-thumb', '.ocr-lang', '.ocr-status', '.ocr-text',
                '.ocr-actions'):
        assert sel in css, 'style.css 缺少 %s' % sel
    # 图标必须走图标库（UI 里不许出现 emoji 当图标）
    icons = _read('renderer/js/shared/icons.js')
    assert 'ocr:' in icons
    js = _read('renderer/js/app/29-ocr.js')
    assert 'ICONS.ocr' in js
    # 四个入口都要接上：截图工具条 / 选文件 / 剪贴板 / 图片右键
    assert 'openFromCapture' in js and 'cap-ocr-file' in js
    assert 'cap-ocr-clipboard' in js and 'ocr_clipboard_image' in js
    assert 'contextmenu' in js and 'data-md-src' in js
    overlay = _read('renderer/js/capture/overlay.js')
    assert 'ocr:' in overlay and '识别文字' in overlay
    assert 'data-act="ocr"' in _read('renderer/capture-overlay.html')


def test_ocr_insert_keeps_image_and_puts_text_below():
    """用户选定的行为：图保留、文字接在它下面（两条路各验一次）。"""
    js = _read('renderer/js/app/29-ocr.js')
    assert "'image-text'" in js, '图片+文字一起插要走 image-text 动作'
    assert 'insertAfterExistingImage' in js, '图片已在笔记里时要插在它下面'
    actions = _read('renderer/js/app/17-markdown-actions.js')
    assert "case 'image-text'" in actions
    # 富文本那条路：插完图片再把文字插到 embed 后面
    assert 'insertImageResult' in js and 'insertText' in js


def test_ocr_panel_reports_languages_and_remembers_choice():
    js = _read('renderer/js/app/29-ocr.js')
    assert 'ocr_languages' in js, '语言下拉必须列本机真实可用的引擎'
    assert "LANG_KEY = 'ocr_language'" in js, '语言选择要记住（下次直接可用）'
    assert 'settings_set' in js and 'settings_get' in js


def test_ocr_selftest_entry_for_packaged_diagnostics():
    """打包版要能一句话自检（`MyNotepad.exe --ocr-selftest <图>`）"""
    app_src = _read('app.pyw')
    assert "'--ocr-selftest' in sys.argv" in app_src
    assert 'selftest_from_argv' in app_src
    ocr_src = _read('ocr.py')
    assert '--out' in ocr_src and '.ocr.json' in ocr_src
