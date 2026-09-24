# -*- coding: utf-8 -*-
"""界面一致性守卫：图标与按钮必须沿用既有设计语言（不再出现"文字符号当图标"）。

背景（用户反馈）：第 8/9 轮新增的关闭/翻页/入口按钮用了文字符号（`×` `↑` `↓`）与自造按钮类，
而应用里既有的 20 个面板关闭按钮是统一的一份 SVG ✕，侧栏按钮是"图标 + 文字"。
这类不一致**不会报错**，只会让人觉得界面东一块西一块，所以用静态测试钉住约定——
新加元素时当场失败，而不是等用户看出来。

实测出来的约定（改约定先改这里）：
  · 所有 `<svg>` 用 `viewBox="0 0 24 24"`，显式给 width/height
  · 描边图标 `fill="none" stroke="currentColor"`；**描边宽度按尺寸分两档**：
    小图标（≤20px）用 2 或 2.5（关闭 ✕ 是 2.5），大装饰图标（≥22px，空状态/导出面板）用 1.5
  · 关闭/取消一律是 `.btn-close-panel` + SVG ✕（唯一允许的文字例外：数学符号面板里的 `×` 本身就是要插入的乘号）
  · 动作按钮复用既有类（`.btn-secondary` / `.btn-close-panel` / `.status-btn` / `.btn-new-note`），
    不自己发明
  · **动态生成的界面图标走 `shared/icons.js` 的 ICONS**，不要在 JS 里内联 `<svg>` 字符串
    （静态 HTML 工具栏是例外：Quill 工具栏本来就是内联 SVG）
  · **UI 图标一律不用 emoji**：用户第二次反馈才发现，第 8/9 轮只统一了当轮的按钮，
    更早的 Markdown 工具栏（☑ 🔗 🖼 📎）与提醒 Toast（🔔 🕐 ✕ 📋）还在用 emoji。
    下面是**内容**而非图标、允许保留 emoji 的清单。
"""
import os
import re

from conftest import PROJECT_ROOT

RENDERER = os.path.join(PROJECT_ROOT, 'renderer')

# 唯一允许的文字 `×`：数学符号面板里它就是"乘号"这个要插入的字符
TEXT_GLYPH_ALLOWED = ('math-sym-btn',)

# emoji 属于**内容/语法**而非图标的清单（文件 → 允许的行内特征）
EMOJI_CONTENT_ALLOWED = {
    'renderer/js/app/02-editor.js': ['emojis:', "cat: '"],          # 表情选择器的数据
    'renderer/js/quill/quill-deco.js': ["html:'", 'cat:'],          # 贴纸/印章数据
    'renderer/js/app/17-markdown-actions.js': ['[📎'],               # 写进正文的附件链接标签（后端靠 📎 识别）
    'renderer/index.html': ['todo-hint', '- [ ] 事项'],             # 待办语法提示（要用户照抄的字符）
    'renderer/js/app/09-boot.js': ['console.log'],                  # 控制台提示
}

# 明确禁止出现在 UI 里的"图标类 emoji"
ICON_EMOJI = '🔗🖼📎☑❝▦🔔🕐📋📅⚠🔒✂🗑⭐📌🔖✔✓✕'


def _html():
    return open(os.path.join(RENDERER, 'index.html'), encoding='utf-8').read()


def _css():
    return open(os.path.join(RENDERER, 'style.css'), encoding='utf-8').read()


def test_every_svg_uses_the_shared_viewbox_and_size_aware_stroke():
    html = _html()
    tags = re.findall(r'<svg\b[^>]*>', html, re.S)
    assert len(tags) > 60, '图标数量异常少（%d）：是不是解析错了' % len(tags)
    for tag in tags:
        assert 'viewBox="0 0 24 24"' in tag, '图标必须用统一的 24 viewBox：%s' % tag
        size = re.search(r'width="(\d+)"', tag)
        assert size and 'height="' in tag, '图标必须显式给尺寸：%s' % tag
        if 'stroke="currentColor"' not in tag:
            continue
        assert 'fill="none"' in tag, '描边图标必须 fill="none"：%s' % tag
        sw = re.search(r'stroke-width="([\d.]+)"', tag)
        assert sw, '描边图标必须显式写 stroke-width：%s' % tag
        width = int(size.group(1))
        if width >= 22:
            assert sw.group(1) == '1.5', '大图标（%dpx）用 1.5 的细描边：%s' % (width, tag)
        else:
            assert sw.group(1) in ('2', '2.5'), \
                '小图标（%dpx）描边只有 2 与 2.5 两档，实际 %s' % (width, sw.group(1))


def test_close_buttons_contain_svg_not_text():
    html = _html()
    found = 0
    for tag, body in re.findall(
            r'(<button\b[^>]*class="[^"]*btn-close-panel[^"]*"[^>]*>)(.*?)</button>', html, re.S):
        if 'btn-secondary' in tag:
            continue          # 「返回」这类带文字的次级按钮只是顺手复用了关闭按钮的排版
        found += 1
        assert '<svg' in body, '关闭按钮必须是 SVG ✕，不能是文字：%r' % body[:40]
        assert '×' not in body and '✕' not in body, '关闭按钮不允许用文字符号：%r' % body[:40]
    assert found >= 20, '面板关闭按钮应当有 20+ 个（含本轮新增的 4 个），实际 %d' % found


def test_no_text_glyph_buttons_outside_the_symbol_panel():
    """查找条与多选条的 ↑ ↓ × 曾经是文字；现在都应当是 SVG。

    注意区分两类 `×`：
      · **关闭控件**（面板/抽屉/提示条）→ 标准 SVG ✕
      · **行内删除**（标签 chip 的 `.tag-remove`、笔记本的 `.notebook-delete-btn`、属性行的
        `.prop-del`）→ 沿用既有的文字 `×`（标签 chip 早就这么做，属于另一种既有约定）
    """
    for line in _html().splitlines():
        if any(skip in line for skip in TEXT_GLYPH_ALLOWED):
            continue
        for bad in ('>↑<', '>↓<', '>×<'):
            assert bad not in line, '还有文字符号当图标的按钮：%s' % line.strip()[:80]
    # JS 里动态生成的关闭控件也不能用文字 ×
    boot = open(os.path.join(RENDERER, 'js', 'app', '09-boot.js'), encoding='utf-8').read()
    assert "close.textContent = '×'" not in boot, '提示条的关闭按钮应当也是 SVG ✕'


def test_custom_button_classes_are_gone():
    """自造的按钮类已删除：能复用既有类就别再造一套。"""
    html, css = _html(), _css()
    for cls in ('find-btn', 'prop-btn', 'prop-btn-primary'):
        assert 'class="' + cls not in html, 'index.html 里还有 %s' % cls
        assert not re.search(r'\.' + cls + r'\s*\{', css), 'style.css 里还留着 .%s 的定义' % cls


def test_new_action_buttons_reuse_existing_classes():
    html = _html()
    for sel, cls in (
        ('btn-table-csv', 'btn-secondary'),
        ('table-view-close', 'btn-close-panel'),
        ('find-replace-one', 'btn-secondary'),
        ('find-replace-all', 'btn-secondary'),
        ('find-prev', 'btn-close-panel'),
        ('find-next', 'btn-close-panel'),
        ('find-close', 'btn-close-panel'),
        ('outline-close', 'btn-close-panel'),
        ('links-close', 'btn-close-panel'),
        ('btn-prop-add', 'btn-secondary'),
        ('btn-prop-done', 'btn-secondary'),
        ('btn-outline', 'status-btn'),
        ('btn-links', 'status-btn'),
    ):
        m = re.search(r'<button\b[^>]*id="' + sel + r'"[^>]*class="([^"]*)"', html)
        assert m, '找不到按钮 #%s' % sel
        assert cls in m.group(1), '#%s 应当用 .%s，实际 %r' % (sel, cls, m.group(1))


def test_new_buttons_all_have_icons_where_the_app_uses_icons():
    """状态栏的「大纲 / 链接」是"图标 + 文字"（与侧栏按钮同规格），不能只剩文字。"""
    html = _html()
    for sel in ('btn-outline', 'btn-links'):
        m = re.search(r'<button\b[^>]*id="' + sel + r'"[^>]*>(.*?)</button>', html, re.S)
        assert m, '找不到 #%s' % sel
        assert '<svg width="14" height="14"' in m.group(1), '#%s 缺少 14×14 图标' % sel


# ---------------- 第二轮：UI 图标里不允许出现 emoji ----------------

def _ui_files():
    """界面文件（渲染到用户眼前的）：内容型模块不在其列，它们本来就有 emoji 数据。"""
    names = ['index.html']
    app = os.path.join(RENDERER, 'js', 'app')
    for name in sorted(os.listdir(app)):
        if name.endswith('.js'):
            names.append('js/app/' + name)
    names.append('js/quill/quill-bubbles.js')
    return names


def test_no_emoji_used_as_ui_icon():
    """UI 图标一律 SVG，不许用 emoji（用户第二轮反馈的直接原因）。

    允许保留的 emoji 只在"内容/语法"位置，逐条列在 EMOJI_CONTENT_ALLOWED 里——
    要放宽必须先往那张表里写明理由，不允许悄悄塞回来。
    """
    offences = []
    for rel in _ui_files():
        path = os.path.join(RENDERER, rel)
        if not os.path.isfile(path):
            continue
        allowed_marks = EMOJI_CONTENT_ALLOWED.get('renderer/' + rel.replace('\\', '/'), [])
        for i, line in enumerate(open(path, encoding='utf-8').read().splitlines(), 1):
            bad = [c for c in line if c in ICON_EMOJI]
            if not bad:
                continue
            if any(mark in line for mark in allowed_marks):
                continue
            offences.append('%s:%d %s  %s' % (rel, i, ''.join(bad), line.strip()[:70]))
    assert not offences, '这些地方的 UI 图标还是 emoji：\n' + '\n'.join(offences)


def test_dynamic_icons_come_from_the_registry():
    """JS 里动态生成的图标走 shared/icons.js，别在模块里内联 `<svg>`。

    理由：图标库存在之前就被绕过了一次（第 8/9 轮内联 SVG），结果同一个 ✕ 有了三份实现。
    静态 HTML 工具栏不在此列——Quill 工具栏本来就是内联 SVG，Markdown 工具栏与它保持一致。
    """
    app = os.path.join(RENDERER, 'js', 'app')
    offenders = []
    for name in sorted(os.listdir(app)):
        if not name.endswith('.js'):
            continue
        src = open(os.path.join(app, name), encoding='utf-8').read()
        if '<svg' in src:
            offenders.append(name)
    assert not offenders, ('这些模块里有内联 <svg>，请用 ICONS.xxx（shared/icons.js）：%s' % offenders)


def test_icons_registry_entries_follow_the_style_rules():
    src = open(os.path.join(RENDERER, 'js', 'shared', 'icons.js'), encoding='utf-8').read()
    tags = re.findall(r"'(?:[^']*)':\s*'<svg\b[^>]*>", src) + re.findall(r'<svg\b[^>]*>', src)
    assert len(tags) >= 18, '图标库条目太少（%d）：是不是解析错了' % len(tags)
    for tag in tags:
        assert 'viewBox="0 0 24 24"' in tag, '图标库必须统一 24 viewBox：%s' % tag
        assert 'stroke="currentColor"' in tag and 'fill="none"' in tag or 'fill="currentColor"' in tag, \
            '图标库条目必须显式声明描边/填充：%s' % tag
        sw = re.search(r'stroke-width="([\d.]+)"', tag)
        if sw:
            assert sw.group(1) in ('1', '2', '2.5', '3'), '描边档位异常：%s' % tag
