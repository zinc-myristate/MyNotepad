"""导出时保存对话框里的默认文件名。

三处导出（导出笔记、导出 Markdown、按笔记本 / 标签导出）以前直接拿标题当默认文件名。真实的保存
对话框里（2026-11-18 实测）：标题有 `/` 时提示"路径不存在"（当成了文件夹），有 `?` 时按「保存」
没反应（当成了筛选条件），只能手动改名。现在都先过 `backend.safe_filename`：九个不许用的字符
换成长得一样的全角字符，控制字符去掉，设备名前面加 `_`。
"""
import ntpath
import os
import random
import re
import time

import pytest
from conftest import load_app_partial
from test_export_formats import _click_export, _run

FULLWIDTH = {'<': '＜', '>': '＞', ':': '：', '"': '＂', '/': '／', '\\': '＼', '|': '｜', '?': '？', '*': '＊'}
TITLE = '会议/纪要: 第 3 次?'
CLEANED = '会议／纪要： 第 3 次？'


# ---------------- 清理规则 ----------------

@pytest.mark.parametrize('char', sorted(FULLWIDTH))
def test_characters_windows_refuses_become_full_width(backend_mod, char):
    assert backend_mod.safe_filename('会议%s纪要' % char) == '会议%s纪要' % FULLWIDTH[char]


@pytest.mark.parametrize('title', ['会议纪要', '2026-11-18 周二', 'Weekly notes (draft)', '读书笔记：《三体》',
                                   '问题？回答！', 'C++ 指针', 'naïve café', '读书 📚✨', '版本 1.2.3',
                                   '.gitignore 说明', 'CONSOLE', 'COM10', 'NULL', 'CON 会议', '我的 CON'])
def test_ordinary_titles_are_kept_as_is(backend_mod, title):
    assert backend_mod.safe_filename(title) == title


def test_control_and_invisible_characters(backend_mod):
    f = backend_mod.safe_filename
    assert f('第一行\n第二行\t尾\r\n') == '第一行 第二行 尾'
    assert f('a\x00b\x1fc\x7fd\x85e') == 'abcde'
    assert f('笔记\u202eftd') == '笔记ftd'                # 不然对话框里显示成「笔记lmth.dtf」
    assert f('笔记\u2066x\u2069') == '笔记x'
    assert f('a\ud800b\udfffc') == 'abc'                  # 落单的代理项：Tk 交给 Windows 时会报错


@pytest.mark.parametrize('title, expected', [
    ('CON', '_CON'), ('con', '_con'), ('Nul.备份', '_Nul.备份'), ('aux .txt', '_aux .txt'),
    ('COM1', '_COM1'), ('lpt9', '_lpt9'), ('COM\xb9', '_COM\xb9'), ('CONIN$', '_CONIN$'), ('COM0', '_COM0'),
])
def test_device_names_get_a_prefix(backend_mod, title, expected):
    assert backend_mod.safe_filename(title) == expected


def test_ends_length_and_fallback(backend_mod):
    f = backend_mod.safe_filename
    assert f('  会议\u3000') == '会议'
    assert f('Mr.') == 'Mr'
    assert f('etc. . .') == 'etc'
    for empty in ('', '   ', '...', ' . ', '\n\t', '\u202e', None, 42, ['标题']):
        assert f(empty) == '未命名笔记', repr(empty)
    assert f('', fallback='笔记本') == '笔记本'
    assert f('汉' * 300) == '汉' * 120
    assert f('a' * 119 + ' .b') == 'a' * 119                # 截断截出来的空格和点也去掉
    assert f('CON.' + 'x' * 200) == '_CON.' + 'x' * 115     # 加了下划线也不超过 120 个字
    assert f('CON' + ' ' * 200 + 'x') == '_CON'             # 截断之后才成了设备名


_PIECES = (['CON', 'con', 'NUL', 'Com1', 'LPT\xb3', 'CONIN$', 'aux', ' ', '.', '..', ' .', '\u3000', '\xa0']
           + list(FULLWIDTH)
           + ['\t', '\n', '\r', '\x00', '\x1f', '\x7f', '\x85', '\u2028', '\u202e', '\u2067', '\ud800', '\udfff']
           + ['汉', '字', '📚', 'a', 'Z', '1', '_', '-', '＃', 'é'])


def test_random_titles_always_give_a_usable_name(backend_mod):
    """乱造的标题都得出能用的名字。判据是标准库 `ntpath.isreserved`（Python 自己整理的 Windows 规则：
    不许用的字符、控制字符、结尾的点和空格、设备名），不是照着实现再写一遍"""
    rng = random.Random(20261118)
    for _ in range(3000):
        n = rng.choice((rng.randint(0, 12), rng.randint(100, 260)))
        title = ''.join(rng.choice(_PIECES) for _ in range(n))
        name = backend_mod.safe_filename(title)
        assert name and isinstance(name, str), repr(title)
        assert not re.search('[\u202a-\u202e\u2066-\u2069\ud800-\udfff]', name), (title, name)
        assert not ntpath.isreserved(name), (title, name)
        for ext in ('html', 'txt', 'docx', 'xlsx', 'md', 'zip'):
            assert not ntpath.isreserved('%s.%s' % (name, ext)), (title, name)
        assert len((name + '.xlsx').encode('utf-16-le')) // 2 <= 255, (title, name)
        assert backend_mod.safe_filename(name) == name, (title, name)


def test_cleaned_names_are_written_to_disk_unchanged(backend_mod, tmp_path):
    """清理后的名字真能建出文件，磁盘上的名字和交给对话框的一字不差"""
    out = tmp_path / 'out'
    out.mkdir()
    expected = set()
    for title in (TITLE, '2026/11/18 会议', 'a<b>c|d*e"f\\g', 'CON', 'nul .txt', '标题\n第二行',
                  '笔记\u202eftd', '...', '  空白  ', '汉' * 300, '📚' * 50, 'Mr.'):
        for ext in ('html', 'docx'):
            name = '%s.%s' % (backend_mod.safe_filename(title), ext)
            (out / name).write_bytes(b'x')
            expected.add(name)
    assert set(os.listdir(out)) == expected


# ---------------- 桥接层：三处导出交给对话框的默认文件名 ----------------

@pytest.fixture
def dialog(monkeypatch):
    """换掉保存对话框：记下每次收到的参数，然后按「取消」"""
    calls = []
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: calls.append(kw) or '')
    return calls


def test_export_dialogs_get_a_usable_default_name(app_ns, dialog):
    import backend as _b
    bridge = app_ns['AppApi'](_b.api)
    for fmt in ('html', 'txt', 'docx', 'xlsx'):
        assert bridge.export_note(TITLE, '<p>x</p>', fmt) is None
    assert bridge.export_note('', '<p>x</p>', 'html') is None
    assert bridge.export_markdown('note-id', 'a<b>|c') is None
    assert bridge.export_markdown('note-id', '') is None
    # 按范围导出要范围里真有笔记才弹对话框（一篇都没有时直接说没有）
    notebook = _b.api.notebooks_create('项目/甲:乙')['id']
    tag = _b.api.tags_create('重点')['id']
    _b.api.note_tags_set(_b.api.notes_create(notebook)['id'], [tag])
    assert bridge.export_scope(notebook_id=notebook, label='项目/甲:乙') is None
    assert bridge.export_scope(tag_id=tag, label='') is None
    names = [kw['initialfile'] for kw in dialog]
    assert names[:5] == ['%s.%s' % (CLEANED, fmt) for fmt in ('html', 'txt', 'docx', 'xlsx')] + ['未命名笔记.html']
    assert names[5:7] == ['a＜b＞｜c.md', '未命名笔记.md']
    assert re.fullmatch(r'我的记事本-项目／甲：乙-\d{8}-\d{6}\.zip', names[7]), names[7]
    assert re.fullmatch(r'我的记事本-标签-\d{8}-\d{6}\.zip', names[8]), names[8]


def test_unknown_export_format_opens_no_dialog(app_ns, dialog):
    """格式也是文件名的一部分：后端只认四种，别的（包括不是字符串的）不弹对话框，直接说不支持"""
    import backend as _b
    bridge = app_ns['AppApi'](_b.api)
    for fmt in ('pdf', 'html/../x', '', None, ['html'], 'exe'):
        assert bridge.export_note('标题', '<p>x</p>', fmt) == {'ok': False, 'error': '不支持导出成这种格式'}
    assert dialog == []


def test_exports_are_written_under_the_default_name(app_ns, tmp_path, monkeypatch):
    """用户直接按「保存」（对话框原样交回默认文件名）时，各格式都写得出来，名字就是清理后的那个"""
    import backend as _b
    out = tmp_path / 'out'
    out.mkdir()
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: str(out / kw['initialfile']))
    bridge = app_ns['AppApi'](_b.api)
    notebook = _b.api.notebooks_create('项目/甲:乙')
    note_id = _b.api.notes_create(notebook['id'])['id']
    _b.api.notes_update(note_id, {'title': TITLE, 'content': '# 会议\n\n正文\n'})

    for fmt in ('html', 'txt', 'docx', 'xlsx'):
        path = str(out / ('%s.%s' % (CLEANED, fmt)))
        assert bridge.export_note(TITLE, '<h1>会议</h1><p>正文</p>', fmt) == {'ok': True, 'path': path}
        assert os.path.getsize(path) > 0, fmt
    path = str(out / (CLEANED + '.md'))
    assert bridge.export_markdown(note_id, TITLE) == {'ok': True, 'path': path}
    with open(path, encoding='utf-8') as f:
        assert '正文' in f.read()
    scope = bridge.export_scope(notebook_id=notebook['id'], label=notebook['name'])
    assert scope['count'] == 1
    zip_name = os.path.basename(scope['path'])
    assert re.fullmatch(r'我的记事本-项目／甲：乙-\d{8}-\d{6}\.zip', zip_name), zip_name
    assert sorted(os.listdir(out)) == sorted(['%s.%s' % (CLEANED, ext) for ext in ('html', 'txt', 'docx', 'xlsx', 'md')]
                                             + [zip_name])


# ---------------- e2e：从界面的导出按钮走一遍 ----------------

@pytest.mark.e2e
def test_export_buttons_hand_the_dialog_a_usable_name(tmp_path, monkeypatch):
    """标题带 `/` `:` `?` 的笔记和没有标题的笔记，各从导出面板导出 HTML 与 Markdown：默认文件名是清理后的标题，
    没有标题时两种都叫「未命名笔记」（Markdown 那条以前是「笔记.md」）。

    没有标题的那篇最先导出，并且先导出 Markdown：保存时空标题存成「未命名笔记」，从这篇切走一次
    （或者编辑器失焦）它的标题就不再是空的，页面传过去的后备名也就测不到了。"""
    ns = load_app_partial(monkeypatch, tmp_path)
    import backend
    titled = backend.api.notes_create()['id']
    backend.api.notes_update(titled, {'title': TITLE, 'content': '正文\n'})
    untitled = backend.api.notes_create()['id']
    backend.api.notes_update(untitled, {'title': '', 'content': '没有标题\n'})
    calls = []
    monkeypatch.setattr('tkinter.filedialog.asksaveasfilename', lambda **kw: calls.append(kw['initialfile']) or '')

    def actions(window, result):
        for note_id in (untitled, titled):
            for fmt in ('md', 'html'):
                before = len(calls)
                _click_export(window, note_id, 'md', '#md-preview', fmt, formulas=0)
                deadline = time.time() + 20
                while len(calls) == before and time.time() < deadline:
                    time.sleep(0.1)
        result['names'] = list(calls)

    r = _run(ns, actions)
    assert 'error' not in r, r.get('error')
    assert r['names'] == ['未命名笔记.md', '未命名笔记.html', CLEANED + '.md', CLEANED + '.html']
