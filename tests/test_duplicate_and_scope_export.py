# -*- coding: utf-8 -*-
"""复制笔记 + 按范围（笔记本/标签）导出。

两处都容易被"看起来对了"骗过去，所以断言落在**数据层的事实**上：
复制出来的图片能不能真的读到、导出的 zip 里那个 notes.db 能不能当库打开、范围外的笔记是否真的不在里面。
"""
import json
import os
import sqlite3
import zipfile

import pytest


def delta(text):
    return json.dumps({"ops": [{"insert": text + "\n"}]}, ensure_ascii=False)


def _png(tmp_path, name='i.png'):
    import base64
    p = tmp_path / name
    p.write_bytes(base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='))
    return str(p)


class TestDuplicate:
    def test_copies_content_and_appearance(self, api):
        src = api.notes_create()['id']
        api.notes_update(src, {'title': '高数笔记', 'content': delta('泰勒展开'),
                               'bg_type': 'color', 'bg_value': '#fff',
                               'paper_style': 'line', 'paper_color': 'white',
                               'cover_type': 'solid', 'cover_value': '#123456'})
        nb = api.notebooks_create('课程')['id']
        api.notes_update(src, {'notebook_id': nb})
        tid = api.tags_create('数学')['id']
        api.note_tags_set(src, [tid])

        dup = api.notes_duplicate(src)
        assert dup and dup['id'] != src
        assert dup['title'] == '高数笔记 副本'
        assert '泰勒展开' in dup['content']
        assert dup['notebook_id'] == nb, '笔记本应一起复制'
        assert dup['paper_style'] == 'line' and dup['cover_value'] == '#123456'
        assert [t['id'] for t in api.note_tags_get(dup['id'])] == [tid], '标签应一起复制'

    def test_copy_is_independent(self, api):
        src = api.notes_create()['id']
        api.notes_update(src, {'title': '甲', 'content': delta('原文')})
        dup = api.notes_duplicate(src)
        api.notes_update(dup['id'], {'content': delta('改副本')})
        assert '原文' in api.notes_get(src)['content'], '改副本不能影响原笔记'
        assert '改副本' in api.notes_get(dup['id'])['content']

    def test_not_pinned_or_favorite(self, api):
        src = api.notes_create()['id']
        api.notes_update(src, {'title': '甲', 'is_pinned': 1, 'is_favorite': 1})
        dup = api.notes_duplicate(src)
        assert not dup['is_pinned'] and not dup['is_favorite'], \
            '副本突然置顶/收藏会让人困惑'

    def test_appears_first_like_new_note(self, api):
        api.notes_create()
        src = api.notes_create()['id']
        api.notes_update(src, {'title': '最后创建的'})
        dup = api.notes_duplicate(src)
        assert api.notes_list()[0]['id'] == dup['id'], '副本应像新建笔记一样排在最前'

    def test_indexed_for_search(self, api):
        src = api.notes_create()['id']
        api.notes_update(src, {'title': '甲', 'content': delta('独一无二的词组甲乙丙')})
        dup = api.notes_duplicate(src)
        assert dup['id'] in set(api.notes_search('独一无二的词组')['ids']), \
            '副本必须进搜索索引，否则搜不到'

    def test_duplicates_attachments_and_files(self, api, tmp_path):
        """图片按 note_id 拼路径——文件不复制的话副本里就是破图"""
        src = api.notes_create()['id']
        info = api.file_copy_to_note(_png(tmp_path), src, 'image')
        assert info and info['filename']
        dup = api.notes_duplicate(src)
        atts = api.attachments_list(dup['id'])
        assert len(atts) == 1 and atts[0]['filename'] == info['filename']
        copied = api.attachments_get_path(dup['id'], info['filename'])
        assert os.path.isfile(copied), '附件文件必须真的复制过去'
        assert os.path.abspath(copied) != os.path.abspath(
            api.attachments_get_path(src, info['filename']))
        assert api.read_file_base64(copied), '副本的图片应该读得出来'

    def test_purge_original_keeps_copy_readable(self, api, tmp_path):
        """删掉原笔记后副本仍完好（附件目录是按 note_id 分开的）"""
        src = api.notes_create()['id']
        info = api.file_copy_to_note(_png(tmp_path), src, 'image')
        dup = api.notes_duplicate(src)
        api.notes_delete(src)
        api.notes_purge(src)
        assert os.path.isfile(api.attachments_get_path(dup['id'], info['filename']))
        assert api.read_file_base64(api.attachments_get_path(dup['id'], info['filename']))

    def test_missing_note_returns_none(self, api):
        assert api.notes_duplicate('no-such-id') is None

    def test_trashed_note_cannot_be_duplicated(self, api):
        src = api.notes_create()['id']
        api.notes_delete(src)
        assert api.notes_duplicate(src) is None

    def test_encrypted_locked_refused(self, api):
        src = api.notes_create()['id']
        api.notes_update(src, {'title': '机密', 'content': delta('秘密内容')})
        api.note_set_password(src, 'pass123456')
        api.note_lock(src)
        assert api.notes_duplicate(src) is None, '锁定态读不到正文，宁可不复制也别复制出密文'

    def test_encrypted_unlocked_copy_is_plaintext(self, api):
        src = api.notes_create()['id']
        api.notes_update(src, {'title': '机密', 'content': delta('秘密内容')})
        api.note_set_password(src, 'pass123456')       # 设完保持解锁
        dup = api.notes_duplicate(src)
        assert dup is not None
        assert '秘密内容' in api.notes_get(dup['id'])['content'], '副本应是可读明文'
        assert api.note_has_password(dup['id']) is False, '副本不带密码（想要就自己再设）'
        row = api.notes_list()
        assert all(n['id'] != dup['id'] or n['preview'] for n in row), '副本应有摘要'


class TestScopeExport:
    @pytest.fixture
    def seeded(self, api):
        nb1 = api.notebooks_create('课程A')['id']
        nb2 = api.notebooks_create('课程B')['id']
        tag = api.tags_create('重点')['id']
        a = api.notes_create()['id']
        api.notes_update(a, {'title': 'A1', 'content': delta('甲组内容'), 'notebook_id': nb1})
        b = api.notes_create()['id']
        api.notes_update(b, {'title': 'B1', 'content': delta('乙组内容'), 'notebook_id': nb2})
        c = api.notes_create()['id']
        api.notes_update(c, {'title': 'C1', 'content': delta('丙组内容'), 'notebook_id': nb1})
        api.note_tags_set(c, [tag])
        return {'nb1': nb1, 'nb2': nb2, 'tag': tag, 'a': a, 'b': b, 'c': c}

    def _zip_db(self, zip_path, tmp_path):
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            zf.extract('notes.db', str(tmp_path / 'unz'))
        return names, str(tmp_path / 'unz' / 'notes.db')

    def test_export_by_notebook(self, api, seeded, tmp_path):
        out = str(tmp_path / 'nb.zip')
        n = api.export_notes_zip(out, notebook_id=seeded['nb1'])
        assert n == 2
        names, db = self._zip_db(out, tmp_path)
        c = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        titles = sorted(r[0] for r in c.execute("SELECT title FROM notes").fetchall())
        c.close()
        assert titles == ['A1', 'C1'], '范围外的笔记不能出现'
        assert '导出说明.txt' in names, '包里要写清怎么恢复'

    def test_export_by_tag(self, api, seeded, tmp_path):
        out = str(tmp_path / 'tag.zip')
        assert api.export_notes_zip(out, tag_id=seeded['tag']) == 1
        _, db = self._zip_db(out, tmp_path)
        c = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        assert [r[0] for r in c.execute("SELECT title FROM notes").fetchall()] == ['C1']
        c.close()

    def test_exported_db_is_a_usable_library(self, api, seeded, tmp_path):
        """核心：导出的 notes.db 必须能当库打开（表结构/FTS 都在），而不是残缺文件"""
        out = str(tmp_path / 'nb.zip')
        api.export_notes_zip(out, notebook_id=seeded['nb1'])
        _, db = self._zip_db(out, tmp_path)
        c = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
        # 子表被 FK 级联清干净：范围外笔记的附件/提醒都不能残留
        assert c.execute("SELECT COUNT(*) FROM attachments").fetchone()[0] == 0
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'notes', 'tags', 'note_tags', 'reminders'} <= tables
        c.close()

    def test_attachments_only_for_scope(self, api, seeded, tmp_path):
        api.file_copy_to_note(_png(tmp_path, 'in.png'), seeded['a'], 'image')
        api.file_copy_to_note(_png(tmp_path, 'out.png'), seeded['b'], 'image')
        out = str(tmp_path / 'nb.zip')
        api.export_notes_zip(out, notebook_id=seeded['nb1'])
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert any(seeded['a'] in n for n in names), '范围内的附件要带上'
        assert not any(seeded['b'] in n for n in names), '范围外的附件不能带'

    def test_empty_scope_returns_zero(self, api, seeded, tmp_path):
        out = str(tmp_path / 'empty.zip')
        assert api.export_notes_zip(out, notebook_id='nope') == 0
        assert not os.path.exists(out), '没有笔记就不该产出空包'

    def test_trashed_notes_excluded(self, api, seeded, tmp_path):
        api.notes_delete(seeded['a'])
        out = str(tmp_path / 'nb.zip')
        assert api.export_notes_zip(out, notebook_id=seeded['nb1']) == 1

    def test_full_export_still_works(self, api, seeded, tmp_path):
        """改造 export_all_to_zip（抽出 _snapshot_db/_zip_dir）后行为不能变"""
        out = str(tmp_path / 'all.zip')
        assert api.export_all_to_zip(out) is True
        with zipfile.ZipFile(out) as zf:
            assert 'notes.db' in zf.namelist()
