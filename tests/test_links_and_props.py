# -*- coding: utf-8 -*-
"""第 9 轮「知识网络」的单测：属性(front-matter) / 双链索引 / prop: 搜索 / 表格数据源。

两条设计主张在这里被锁死：
  1. **正文是属性的唯一真相**——属性只存在正文的 front-matter 里，`props_json` 是派生；
  2. **双链解析放在查询时**——`note_links` 只存标题归一，改标题后反向链接必须自动跟着修正
     （物化 target_id 的方案在这里会立刻露馅，`test_rename_target_keeps_backlinks` 就是抓它的）。
"""
import csv
import json
import os

import pytest
from conftest import make_delta_note


def _md(api, title, content):
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': title, 'content': content})
    return nid


FM_BLOCK = ('---\n'
            '状态: 进行中\n'
            '截止: 2026-10-01\n'
            '重要: true\n'
            '标签: [甲, 乙]\n'
            '等级: 3\n'
            '---\n')
BODY = ('\n'
        '# 标题\n'
        '\n'
        '正文内容 [[目标笔记#小节一|点这里]] 与 [[不存在的笔记]] 还有 [[#本小节]]\n')
FM_DOC = FM_BLOCK + BODY


# ---------------- 属性：正文是唯一真相 ----------------

def test_front_matter_is_not_treated_as_content(api, backend_mod):
    """属性不算正文：字数、摘要、FTS 都不该带上它（否则列表摘要开头就是一堆 `key: value`）。

    最强的断言不是"字数等于某个常数"，而是：**带上 front-matter 的处理结果与只用正文
    逐字符相同**——常数会随正文改动失效，这个不会。
    """
    plain = backend_mod.note_plain_text(FM_DOC, 'md')
    assert '状态' not in plain and '2026-10-01' not in plain
    assert plain == backend_mod.note_plain_text(BODY, 'md')
    assert backend_mod.count_words(plain) == backend_mod.count_words(
        backend_mod.note_plain_text(BODY, 'md'))
    snippet = backend_mod._preview_text(FM_DOC, fmt='md')
    assert not snippet.startswith('状态'), snippet
    assert '正文内容' in snippet


def test_front_matter_affects_nothing_when_absent(api, backend_mod):
    """没有 front-matter 的正文一个字都不能变（剥离规则必须只对开头那一段生效）。"""
    body = '# 标题\n\n---\n\n分隔线下面的内容\n'
    assert backend_mod.note_plain_text(body, 'md') == backend_mod._markdown_to_text(body)


def test_props_json_comes_from_body(api, backend_mod):
    nid = _md(api, '属性笔记', FM_DOC)
    props = json.loads(api.note_metrics(nid)['props_json'])
    assert props == {'状态': '进行中', '截止': '2026-10-01', '重要': 'true',
                     '标签': ['甲', '乙'], '等级': '3'}


def test_derived_version_bumped_for_links(api, backend_mod):
    """版本号是存量库重建 note_links 的唯一开关，改了它就要意识到会触发一次全库回填。

    锁的是"它是个能被回填逻辑比较的版本串"，不是某个具体数字 —— 第一版把 '2' 写死，
    于是第 13 轮为了 note_derived.preview 把版本提到 '3' 时，这条测试红了却什么也没说明。
    真正要防的回归是"有人把版本号删了/改成 None"，那会让回填每次都跑或永远不跑。
    """
    ver = backend_mod.DERIVED_VERSION
    assert isinstance(ver, str) and ver.strip(), '派生版本号必须是非空字符串'
    assert ver.isdigit() and int(ver) >= 2, \
        '第 9 轮引入 note_links 时版本是 2，之后只增不减（降版本号不会触发回填）'


# ---------------- 双链：抽取与解析 ----------------

def test_extract_links_shapes(api, backend_mod):
    links = backend_mod.extract_links(FM_DOC, 'md')
    assert [(x[1], x[3], x[4]) for x in links] == [
        ('目标笔记', '小节一', '点这里'),
        ('不存在的笔记', None, None),
        ('', '本小节', None),
    ]
    assert [x[0] for x in links] == [0, 1, 2]


def test_extract_links_same_for_both_formats(api, backend_mod):
    """同一内容的 md 与 delta 抽出的链接必须一致（与字数/待办同一条口径约定）。"""
    ops = [{'insert': '见 '}, {'insert': '目标', 'attributes': {'bold': True}},
           {'insert': ' 与 [[其它笔记#小节]]\n'}]
    got = backend_mod.extract_links(json.dumps({'ops': ops}), 'delta')
    assert [(x[1], x[3]) for x in got] == [('其它笔记', '小节')]


def test_extract_links_ignores_empty(api, backend_mod):
    assert backend_mod.extract_links('a [[]] b [[|别名]] c\n', 'md') == []


def test_link_index_and_backlinks(api, backend_mod):
    tgt = _md(api, '目标笔记', '# 目标\n\n## 小节一\n正文\n')
    src = _md(api, '源笔记', FM_DOC)
    data = api.note_links(src)
    assert [i['title'] for i in data['outgoing']] == ['目标笔记', '不存在的笔记', '']
    assert data['outgoing'][0]['target']['id'] == tgt
    assert data['outgoing'][1]['target'] is None
    assert [i['title'] for i in data['missing']] == ['不存在的笔记']
    assert data['outgoing'][2]['same_note'] is True
    back = api.note_links(tgt)
    assert [i['id'] for i in back['backlinks']] == [src]
    assert back['backlinks'][0]['heading'] == '小节一'
    assert '目标笔记#小节一' in back['backlinks'][0]['context']


def test_resolve_link_rules(api, backend_mod):
    a = _md(api, '同名', '甲')
    b = _md(api, '同名', '乙')
    hit = api.notes_resolve_link('  同名  ')
    assert hit['matches'] == 2 and hit['id'] in (a, b)
    assert api.notes_resolve_link('没有这篇') is None
    assert api.notes_resolve_link('') is None


def test_rename_target_keeps_backlinks(api, backend_mod):
    """改标题后反向链接必须仍然有效——这是"查询时解析"相对"物化 target_id"的关键收益。"""
    tgt = _md(api, '目标笔记', '正文')
    src = _md(api, '源笔记', '见 [[目标笔记]]\n')
    assert len(api.note_links(tgt)['backlinks']) == 1
    api.notes_update(tgt, {'title': '改名后的笔记'})
    assert api.note_links(tgt)['backlinks'] == []          # 旧名字已经没人指向
    assert [i['title'] for i in api.note_links(src)['missing']] == ['目标笔记']
    api.notes_update(src, {'content': '见 [[改名后的笔记]]\n'})
    assert [i['id'] for i in api.note_links(tgt)['backlinks']] == [src]


def test_trashed_and_deleted_targets(api, backend_mod):
    tgt = _md(api, '目标', '正文')
    src = _md(api, '源', '见 [[目标]]\n')
    api.notes_delete(tgt)                                   # 软删：链接视为"尚未创建"
    assert [i['title'] for i in api.note_links(src)['missing']] == ['目标']
    api.notes_restore(tgt)
    assert api.note_links(src)['missing'] == []


def test_create_from_link(api, backend_mod):
    src = _md(api, '源', '见 [[新笔记]]\n')
    created = api.notes_create_from_link('新笔记')
    assert created['title'] == '新笔记' and created['format'] == 'md'
    assert api.note_links(src)['missing'] == []
    assert created['id'] in [i['target']['id'] for i in api.note_links(src)['outgoing']]


def test_encrypted_notes_never_index_links(api, backend_mod):
    """加密笔记不参与派生（与待办/字数同一条隐私约定）：密文里扫不出链接，行必须清干净。"""
    tgt = _md(api, '目标', '正文')
    src = _md(api, '源', '见 [[目标]]\n')
    assert api.note_links(tgt)['backlinks']
    api.note_set_password(src, 'pw123456')
    assert api.note_links(src) == {'outgoing': [], 'backlinks': [], 'missing': []}
    assert api.note_links(tgt)['backlinks'] == []


# ---------------- prop: 搜索 ----------------

def test_prop_search_syntax(api, backend_mod):
    nid = _md(api, '属性笔记', FM_DOC)
    other = _md(api, '普通笔记', '没有属性\n')
    assert api.notes_search('prop:状态')['ids'] == [nid]
    assert api.notes_search('prop:状态=进行中')['ids'] == [nid]
    assert api.notes_search('prop:状态=已完成')['ids'] == []
    assert api.notes_search('prop:标签~甲')['ids'] == [nid]
    assert api.notes_search('prop:等级>2')['ids'] == [nid]
    assert api.notes_search('prop:等级>5')['ids'] == []
    assert api.notes_search('prop:截止<2026-12-31')['ids'] == [nid]
    assert api.notes_search('has:prop')['ids'] == [nid]
    assert api.notes_search('-prop:状态=进行中')['ids'] == [other]
    assert api.notes_search('prop:状态=进行中 正文')['ids'] == [nid]
    # 不认识的写法不吞掉：整串当自由文本搜，搜不到就是搜不到（不该报错）
    assert api.notes_search('prop:')['ids'] == []


def test_prop_search_keeps_encrypted_notes_for_negation(api, backend_mod):
    """只有取反条件时，读不到属性的笔记要**保留**（无法证明它违反，宁可不排除不误删）。"""
    plain = _md(api, '明文', '---\n状态: 进行中\n---\n\n正文\n')
    cipher = _md(api, '加密的', '正文\n')
    api.note_set_password(cipher, 'pw123456')
    assert api.notes_search('prop:状态=进行中')['ids'] == [plain]
    got = api.notes_search('-prop:状态=进行中')['ids']
    assert cipher in got, got


# ---------------- 表格数据源 ----------------

def test_notes_table_fields_and_order(api, backend_mod):
    nb = api.notebooks_create('课程')['id']
    tag = api.tags_create('数学')['id']
    a = _md(api, '甲', FM_DOC)
    b = make_delta_note(backend_mod, '乙', json.dumps({'ops': [{'insert': '乙的正文\n'}]}))
    api.notes_update(a, {'notebook_id': nb})
    api.note_tags_set(b, [tag])
    rows = api.notes_table([b, a])          # 顺序必须跟着传入的 id（列表顺序 = 表格顺序）
    assert [r['id'] for r in rows] == [b, a]
    assert rows[0]['notebook'] == '' and rows[0]['tags'] == ['数学']
    assert rows[0]['props'] == {}
    assert rows[1]['notebook'] == '课程'
    assert rows[1]['props']['状态'] == '进行中'
    assert rows[1]['word_count'] == backend_mod.count_words(backend_mod.note_plain_text(FM_DOC, 'md'))
    assert set(rows[1]) >= {'id', 'title', 'format', 'updated_at', 'todo_open', 'link_count'}


def test_notes_table_hides_encrypted_content(api, backend_mod):
    nid = _md(api, '加密的', '---\n状态: 进行中\n---\n\n正文\n')
    api.note_set_password(nid, 'pw123456')
    row = api.notes_table([nid])[0]
    assert row['encrypted'] is True
    assert row['props'] == {} and row['word_count'] == 0
    assert 'password_hash' not in row and 'content' not in row


def test_export_table_csv(tmp_path, api, backend_mod):
    a = _md(api, '甲', FM_DOC)
    b = _md(api, '乙', '没有属性\n')
    out = os.path.join(str(tmp_path), 't.csv')
    assert api.export_table_csv([a, b], out) == 2
    raw = open(out, 'rb').read()
    assert raw.startswith(b'\xef\xbb\xbf'), 'CSV 必须带 BOM，否则 Excel 打开中文乱码'
    with open(out, encoding='utf-8-sig', newline='') as fh:
        rows = list(csv.reader(fh))
    header = rows[0]
    assert header[:4] == ['标题', '笔记本', '标签', '更新时间']
    for key in ('状态', '截止', '标签', '重要', '等级'):
        assert key in header, header
    first = dict(zip(header, rows[1], strict=False))
    assert first['标题'] == '甲' and first['状态'] == '进行中' and first['标签'] == '甲/乙'


def test_export_table_csv_empty(api, tmp_path):
    out = os.path.join(str(tmp_path), 'empty.csv')
    assert api.export_table_csv([], out) == 0
    assert not os.path.exists(out), '没有数据就不该留下一个空文件'


# ---------------- props_json 与表格/搜索共用同一份解析 ----------------

@pytest.mark.parametrize('doc,expect', [
    ('---\na: 1\nb:\n  - x\n  - y\n---\n\n正文\n', {'a': '1', 'b': ['x', 'y']}),
    ('---\na: [x, y]\nb: "带 空格"\n---\n正文\n', {'a': ['x', 'y'], 'b': '带 空格'}),
    ('没有 front-matter', {}),
])
def test_parse_front_matter_shapes(api, backend_mod, doc, expect):
    assert backend_mod.parse_front_matter(doc) == expect
