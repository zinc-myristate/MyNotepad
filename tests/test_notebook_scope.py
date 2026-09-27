# -*- coding: utf-8 -*-
"""第 12 轮：笔记本范围（"在新笔记本里新建，不该和别的笔记混在一起"）。

用户报的现象：侧栏选着「原神」(0)，点「＋ 新建笔记」之后列表里冒出**全部**笔记、
新笔记还不在原神里。根因两条，这一组测试把两条都钉死：

1. `notes_create()` 不收笔记本参数 → 新笔记永远落「未分类」；
2. 前端的笔记本筛选是"拉全量 → 覆盖列表"，`loadNotes()` 有 20+ 处调用，
   任何一处都会把筛选冲成全量（新建只是最容易碰到的那一处）。

修法：范围过滤下沉到 `notes_list(notebook_id)`（谁调用都只会拿到那一本），
创建接口收 `notebook_id`。于是"混在一起"不可能再靠某处忘记重筛而复现。
"""
import datetime
import os
import re

import pytest
from conftest import PROJECT_ROOT


def _mk(api, title, notebook_id=None):
    """建一篇指定标题/笔记本的笔记（走公开接口，不直接写库）"""
    nid = api.notes_create(notebook_id)['id']
    api.notes_update(nid, {'title': title})
    return nid


# ----- 列表范围 -----

class TestNotesListScope:
    def test_none_means_all(self, api):
        nb = api.notebooks_create('原神')['id']
        _mk(api, 'A', nb)
        _mk(api, 'B')
        _mk(api, 'C', nb)
        assert len(api.notes_list()) == 3
        assert len(api.notes_list(None)) == 3

    def test_scope_returns_only_that_notebook(self, api):
        nb1 = api.notebooks_create('原神')['id']
        nb2 = api.notebooks_create('日记')['id']
        _mk(api, 'A', nb1)
        _mk(api, 'B', nb2)
        _mk(api, 'C')
        got = api.notes_list(nb1)
        assert [n['title'] for n in got] == ['A']
        assert all(n['notebook_id'] == nb1 for n in got), '范围里混进了别的笔记本的笔记'

    def test_empty_string_means_uncategorized(self, api):
        """'' = 未分类（IS NULL）。前端「无（全部笔记）」传的就是空串。"""
        nb = api.notebooks_create('原神')['id']
        _mk(api, 'A', nb)
        _mk(api, 'B')
        assert [n['title'] for n in api.notes_list('')] == ['B']

    def test_scope_keeps_list_ordering(self, api):
        """范围过滤不能把排序弄丢：置顶 → sort_order → 最近更新"""
        nb = api.notebooks_create('原神')['id']
        a = _mk(api, 'A', nb)
        _mk(api, 'B', nb)
        api.notes_update(a, {'is_pinned': 1})
        assert [n['title'] for n in api.notes_list(nb)] == ['A', 'B']

    def test_scope_excludes_trash(self, api):
        nb = api.notebooks_create('原神')['id']
        nid = _mk(api, 'A', nb)
        api.notes_delete(nid)
        assert api.notes_list(nb) == []

    def test_unknown_notebook_id_is_empty_not_all(self, api):
        """给个不存在的 id 必须是空列表 —— 回退成全量就等于又把范围冲掉了"""
        _mk(api, 'A')
        assert api.notes_list('no-such-notebook') == []


# ----- 创建归属 -----

class TestNotesCreateScope:
    def test_create_lands_in_given_notebook(self, api):
        nb = api.notebooks_create('原神')['id']
        note = api.notes_create(nb)
        assert note['notebook_id'] == nb
        assert [n['id'] for n in api.notes_list(nb)] == [note['id']]

    def test_create_without_notebook_is_uncategorized(self, api):
        """「全部笔记」视角下没有当前笔记本可跟随 → 未分类（保持旧行为，不会乱跑）"""
        assert api.notes_create()['notebook_id'] is None
        assert api.notes_create(None)['notebook_id'] is None

    def test_create_with_stale_notebook_id_falls_back(self, api):
        """笔记本已被删掉时退回未分类 —— 否则这篇笔记谁也看不见"""
        nb = api.notebooks_create('临时')['id']
        api.notebooks_delete(nb)
        assert api.notes_create(nb)['notebook_id'] is None

    def test_new_note_shows_up_in_scope_only(self, api):
        """★ 用户报的那一幕：在「原神」里新建 → 列表只有原神，别的笔记一篇都不许混进来"""
        nb = api.notebooks_create('原神')['id']
        _mk(api, 'MATS1192')
        _mk(api, 'Genshin')
        _mk(api, '背景实验', nb)
        before = api.notes_list(nb)
        assert len(before) == 1
        note = api.notes_create(nb)
        after = api.notes_list(nb)
        assert len(after) == 2, '新建后列表被冲成了全量（这正是"混在一起"）'
        assert note['id'] in [n['id'] for n in after]
        assert len(api.notes_list()) == 4, '未分类里那两篇仍然在（只是不该出现在原神视角里）'

    def test_update_empty_notebook_id_means_uncategorized(self, api):
        """「移动到 → 无（全部笔记）」传 ''，必须落成 NULL：存空串会让笔记在任何范围里都消失"""
        nb = api.notebooks_create('原神')['id']
        nid = _mk(api, 'A', nb)
        api.notes_update(nid, {'notebook_id': ''})
        assert api.notes_list(nb) == []
        assert [n['id'] for n in api.notes_list('')] == [nid]


# ----- 计数（下拉里的「N 篇」不能靠前端数） -----

class TestNotebookCounts:
    def test_counts_split_by_notebook_and_uncategorized(self, api):
        nb1 = api.notebooks_create('原神')['id']
        nb2 = api.notebooks_create('日记')['id']
        _mk(api, 'A', nb1)
        _mk(api, 'B', nb1)
        _mk(api, 'C', nb2)
        _mk(api, 'D')
        got = api.notebook_counts()
        assert got['by_id'][nb1] == 2
        assert got['by_id'][nb2] == 1
        assert got['uncategorized'] == 1
        assert got['total'] == 4

    def test_counts_ignore_trash(self, api):
        nb = api.notebooks_create('原神')['id']
        nid = _mk(api, 'A', nb)
        api.notes_delete(nid)
        got = api.notebook_counts()
        assert got['by_id'].get(nb, 0) == 0
        assert got['total'] == 0

    def test_counts_match_scoped_list_length(self, api):
        """计数与列表必须同口径 —— 徽章显示 (0) 而列表列出 6 篇就是这次那张截图"""
        nb = api.notebooks_create('原神')['id']
        _mk(api, 'A', nb)
        _mk(api, 'B')
        _mk(api, 'C')
        counts = api.notebook_counts()
        assert counts['by_id'][nb] == len(api.notes_list(nb))
        assert counts['total'] == len(api.notes_list())


# ----- 其它创建入口的归属 -----

class TestOtherCreateEntrances:
    def test_template_create_honours_notebook_id(self, api):
        nb = api.notebooks_create('原神')['id']
        tpl = api.template_create('会议', '正文 {{title}}')
        note = api.notes_create_from_template(tpl['id'], '周一例会', None, nb)
        assert note['notebook_id'] == nb
        assert note['title'] == '周一例会'

    def test_template_notebook_id_wins_over_name(self, api):
        """两个都传时以 id 为准（前端第 12 轮传的是 id；按名字找/建是给旧调用方的）"""
        nb = api.notebooks_create('原神')['id']
        tpl = api.template_create('会议', 'x')
        note = api.notes_create_from_template(tpl['id'], 'T', '速记本', nb)
        assert note['notebook_id'] == nb
        assert not any(x['name'] == '速记本' for x in api.notebooks_list()), \
            'id 优先时不该顺手建出名字那本'

    def test_template_notebook_name_still_works(self, api):
        """旧路径（按名字找或建）不能被改坏：捕获类入口还用它"""
        tpl = api.template_create('速记', 'x')
        note = api.notes_create_from_template(tpl['id'], 'T', '速记本')
        nbs = {n['name']: n['id'] for n in api.notebooks_list()}
        assert note['notebook_id'] == nbs['速记本']

    def test_link_create_honours_notebook(self, api):
        nb = api.notebooks_create('原神')['id']
        note = api.notes_create_from_link('新链接目标', nb)
        assert note['notebook_id'] == nb
        assert note['title'] == '新链接目标'


# ----- 标签 × 笔记本叠加 -----

class TestTagAndNotebookCompose:
    def test_by_tag_is_scoped(self, api):
        """在「原神」里点一个标签，别的笔记本的同标签笔记不许倒进来"""
        nb = api.notebooks_create('原神')['id']
        other = _mk(api, '别的本里的同名标签笔记')
        mine = _mk(api, '原神里的', nb)
        tag = api.tags_create('重要')['id']
        api.note_tags_set(other, [tag])
        api.note_tags_set(mine, [tag])
        assert len(api.notes_by_tag(tag)) == 2
        scoped = api.notes_by_tag(tag, nb)
        assert [n['id'] for n in scoped] == [mine]
        assert scoped[0]['notebook_id'] == nb, '列表项要带 notebook_id（前端据此判断归属）'

    def test_by_tag_without_notebook_unchanged(self, api):
        a = _mk(api, 'A')
        tag = api.tags_create('t')['id']
        api.note_tags_set(a, [tag])
        assert len(api.notes_by_tag(tag)) == 1


# ----- 日历：跨笔记本找"那天建的笔记" -----

class TestNotesCreatedOn:
    def test_finds_notes_across_notebooks(self, api):
        """列表只有当前那一本，日历必须能查到别的笔记本里那天建的笔记，
        否则点日历会以为那天没写过，一遍遍重复建当天笔记。"""
        nb = api.notebooks_create('日记')['id']
        _mk(api, '今天写的', nb)
        _mk(api, '未分类里的')
        today = datetime.date.today().isoformat()
        titles = [n['title'] for n in api.notes_created_on(today)]
        assert '今天写的' in titles and '未分类里的' in titles

    def test_other_day_is_empty(self, api):
        _mk(api, 'A')
        assert api.notes_created_on('1999-01-01') == []

    def test_excludes_trash(self, api):
        nid = _mk(api, 'A')
        api.notes_delete(nid)
        today = datetime.date.today().isoformat()
        assert api.notes_created_on(today) == []


@pytest.mark.parametrize('meth,args', [
    ('notes_list', (None,)),
    ('notebook_counts', ()),
    ('notes_created_on', ('2026-01-01',)),
])
def test_scope_apis_are_json_friendly(app_ns, meth, args):
    """桥接方法必须存在且能吃前端会传的参数（少一个参数 = TypeError + 功能静默失效）"""
    import inspect
    fn = getattr(app_ns['AppApi'], meth, None) or getattr(app_ns['api'], meth)
    sig = inspect.signature(fn)
    pos = [p for p in sig.parameters.values()
           if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    assert len(pos) - 1 >= len(args), '%s 收不下前端会传的 %d 个参数' % (meth, len(args))


# ----- 前端接线（纯静态：不启浏览器也能挡住"某条刷新路径忘了带范围"） -----

APP_JS = os.path.join(PROJECT_ROOT, 'renderer', 'js', 'app')


def _read(name):
    with open(os.path.join(APP_JS, name), encoding='utf-8') as f:
        return f.read()


class TestFrontendScopeWiring:
    def test_every_notes_list_call_carries_the_scope(self):
        """★ 本轮的核心回归防线：**每一条刷新路径**都必须带上当前笔记本。

        以前是前端 setNotes(全量) 覆盖，于是 20+ 处 loadNotes 里任何一处都能把筛选冲掉；
        现在范围在后端，唯一会漏的写法就是"调 notes_list() 忘了传参数"。
        唯一允许不传的是 Ctrl+P 的全库索引（跨笔记本跳转要用全量），且必须在 11-quick-switch.js 里。
        """
        offenders = []
        for fn in sorted(os.listdir(APP_JS)):
            if not fn.endswith('.js'):
                continue
            src = _read(fn)
            # 逐个找调用点，取它后面一小段原文判断实参（不用正则配括号：实参里还有一层括号）
            for m in re.finditer(r'\.notes_list\(', src):
                arg = src[m.end():m.end() + 40]
                if arg.lstrip().startswith(')'):
                    if fn != '11-quick-switch.js':
                        offenders.append(fn)
                elif 'getCurrentNotebookId()' not in arg and 'nbId' not in arg:
                    offenders.append('%s（notes_list(%s…）' % (fn, arg.split(')')[0][:24]))
        assert not offenders, \
            '这些调用没带当前笔记本范围（会把筛选冲成全量）：%s' % offenders

    def test_create_paths_pass_the_scope(self):
        create = _read('03-notes.js')
        assert 'notes_create(getCurrentNotebookId())' in create, \
            '「＋ 新建笔记」必须落进当前笔记本'
        cal = _read('08-appearance2.js')
        assert 'notes_create(getCurrentNotebookId())' in cal, '日历新建同样要跟随当前笔记本'
        assert 'notes_created_on' in cal, '日历找"当天笔记"必须跨笔记本查（state.notes 只有当前那一本）'
        assert "notes.filter(n => (n.created_at" not in cal, \
            '日历不能再用 state.notes 找当天笔记（笔记在别的笔记本里时会漏，于是重复建当天笔记）'
        tpl = _read('27-templates.js')
        assert 'notes_create_from_template(templateId, title || null, null, getCurrentNotebookId())' in tpl
        links = _read('25-links.js')
        assert 'notes_create_from_link(name, getCurrentNotebookId())' in links

    def test_scope_write_points_are_the_known_three(self):
        """笔记本范围只有三个写入点，且全在 09-boot.js（范围的主人）里。

        多一个写入点就多一处能写出"列表与编辑区不一致"的地方；这条测试逼着后来者
        把新的切换需求接到 `setNotebookScope` 上，而不是自己赋值。
        """
        boot = _read('09-boot.js')
        writes = sorted(set(re.findall(r'currentNotebookId\s*=(?!=)\s*[^;]+;', boot)))
        assert writes == [
            'currentNotebookId = nbId || null;',   # setNotebookScope：切换视角
            'currentNotebookId = null;',           # 笔记本已不存在 → 回收成"全部笔记"
            'currentNotebookId = target;',         # revealAndSelectNote：跟着笔记走
        ], '笔记本范围的写入点变了：%s（新需求请走 setNotebookScope）' % writes
        for fn in sorted(os.listdir(APP_JS)):
            if fn.endswith('.js') and fn != '09-boot.js':
                assert 'currentNotebookId =' not in _read(fn), \
                    '%s 直接改写了笔记本范围（应调 setNotebookScope）' % fn

    def test_tag_filter_composes_with_notebook(self):
        shell = _read('05-shell.js')
        assert 'const nbId = getCurrentNotebookId();' in shell, '标签重套时必须取当前笔记本'
        assert 'notes_by_tag(currentTagFilter, nbId)' in shell, '标签筛选必须叠加笔记本范围'
        assert 'notes_list(nbId)' in shell

    def test_quick_switch_uses_full_index_and_reveals(self):
        qs = _read('11-quick-switch.js')
        assert 'notes_list()' in qs, 'Ctrl+P 的候选来自全库（跨笔记本跳转）'
        assert 'revealAndSelectNote' in qs, '跳到别的笔记本时视角要跟过去'

    def test_jump_paths_reveal_the_note(self):
        """会打开"可能不在当前笔记本里"的笔记的入口，都必须走 revealAndSelectNote"""
        for fn, why in [
            ('18-todo-panel.js', '待办面板是跨笔记的'),
            ('06-versions-reminders.js', '提醒可能指向别的笔记本'),
            ('25-links.js', '双链目标可能在别的笔记本'),
            ('29-ocr.js', 'OCR 存的新笔记固定落收件箱'),
            ('28-capture.js', '捕获固定落收件箱/日记'),
            ('08-appearance2.js', '今日日记固定落日记本'),
        ]:
            assert 'revealAndSelectNote' in _read(fn), '%s（%s）没走 revealAndSelectNote' % (fn, why)

    def test_bulk_actions_settle_the_editor(self):
        """批量移动/删除可能把当前打开的那篇弄出范围 —— 编辑区必须跟着收尾，不能停在它上面"""
        bulk = _read('12-bulk-actions.js')
        assert 'hideEditorUI' in bulk and 'verifyAndSelectNote' in bulk

    def test_counts_come_from_backend_not_from_state_notes(self):
        """范围过滤后前端手里只有当前那一本，下拉里再按 notebook_id 数一遍必然是假数。

        （搜索兜底里的 `state.notes.filter(标题)` 不算：那是在自己手里这一本里找标题。）
        """
        boot = _read('09-boot.js')
        assert 'notebook_counts()' in boot
        assert '_counts.by_id' in boot, '下拉每本的篇数应来自后端计数'
        assert 'notebook_id===nb.id' not in boot, '下拉计数不能再用 state.notes 数（只剩当前那一本）'
