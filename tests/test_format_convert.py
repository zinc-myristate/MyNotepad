# -*- coding: utf-8 -*-
"""格式转换（双轨搬家）：转换、回退、内容守恒校验、加密兼容。"""
import json

import pytest


def _delta(*ops):
    return json.dumps({'ops': list(ops)}, ensure_ascii=False)


def _make_delta_note(backend, content, title='笔记'):
    """建一篇 delta 笔记（内容为 Delta JSON）"""
    nid = backend.api.notes_create()['id']
    backend.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
    backend.conn.commit()
    backend.api.notes_update(nid, {'title': title, 'content': content})
    return nid


class TestFormatInfo:
    def test_reports_format_and_backup(self, api):
        nid = api.notes_create()['id']
        info = api.note_format_info(nid)
        assert info['format'] == 'md' and info['has_delta_backup'] is False
        assert info['can_convert'] is True

    def test_locked_encrypted_note_cannot_convert(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'content': '正文\n'})
        api.note_set_password(nid, 'pw123456')
        api.note_lock(nid)
        info = api.note_format_info(nid)
        assert info['encrypted'] and info['locked'] and info['can_convert'] is False
        assert api.convert_note_format(nid, 'delta')['ok'] is False


class TestConvertDeltaToMarkdown:
    def test_converts_and_keeps_backup(self, backend_mod, api):
        # 注意 Quill 的约定：**行属性挂在含换行的那个 op 上**（不是文字 op），
        # 行内属性（颜色/加粗）才挂在文字 op 上——构造测试数据时必须照这个来。
        content = _delta({'insert': '标题'},
                         {'insert': '\n', 'attributes': {'header': 1}},
                         {'insert': '红色', 'attributes': {'color': '#B8844A'}},
                         {'insert': '\n'})
        nid = _make_delta_note(backend_mod, content)
        r = api.convert_note_format(nid, 'md')
        assert r['ok'], r
        note = api.notes_get(nid)
        assert note['format'] == 'md'
        assert note['content'].startswith('# 标题')
        assert 'color: #B8844A' in note['content']
        # 原始 Delta 备份必须还在（可无损回退）
        backup = backend_mod.conn.execute(
            'SELECT delta_backup FROM notes WHERE id = ?', (nid,)).fetchone()['delta_backup']
        assert json.loads(backup) == json.loads(content)

    def test_creates_version_snapshot(self, backend_mod, api):
        content = _delta({'insert': '转换前的内容\n'})
        nid = _make_delta_note(backend_mod, content)
        before = len(api.versions_list(nid))
        assert api.convert_note_format(nid, 'md')['ok']
        versions = api.versions_list(nid)
        assert len(versions) == before + 1, '转换前必须留历史版本快照'

    def test_plain_text_is_preserved(self, backend_mod, api):
        content = _delta({'insert': '甲乙丙丁戊己庚辛\n'})
        nid = _make_delta_note(backend_mod, content)
        api.convert_note_format(nid, 'md')
        assert '甲乙丙丁戊己庚辛' in api.notes_get(nid)['content']

    def test_search_index_follows_conversion(self, backend_mod, api):
        nid = _make_delta_note(backend_mod, _delta({'insert': '蓝鲸计划书\n'}))
        assert nid in api.notes_search('蓝鲸计划书')['ids']
        api.convert_note_format(nid, 'md')
        assert nid in api.notes_search('蓝鲸计划书')['ids'], '转换后搜索索引必须同步重建'
        assert '蓝鲸计划书' in [n for n in api.notes_list() if n['id'] == nid][0]['preview']

    def test_same_format_is_noop(self, api):
        nid = api.notes_create()['id']
        r = api.convert_note_format(nid, 'md')
        assert r['ok'] and r.get('unchanged') is True


class TestContentGuard:
    def test_refuses_conversion_that_loses_text(self, backend_mod, api, monkeypatch):
        """内容守恒校验：转换器一旦丢字，必须**拒绝转换**而不是悄悄写坏笔记"""
        nid = _make_delta_note(backend_mod, _delta({'insert': '这句话一个字都不能少\n'}))
        monkeypatch.setattr(backend_mod, '_delta_to_markdown', lambda *a, **k: '别的东西\n')
        r = api.convert_note_format(nid, 'md')
        assert r['ok'] is False and '丢内容' in r['error']
        note = api.notes_get(nid)
        assert note['format'] == 'delta', '拒绝转换时格式不能变'
        assert '一个字都不能少' in note['content']

    def test_allows_extra_characters(self, backend_mod):
        """新格式里多出字符（表格分隔行、📎 前缀、HTML 标签）不算丢内容"""
        assert backend_mod.Api._is_subsequence('表格单元', '| 表格单元 |\n| --- |')
        assert backend_mod.Api._is_subsequence('附件', '[📎 附件](x.pdf)')
        assert backend_mod.Api._is_subsequence('红', '<span style="color: #f00">红</span>')

    def test_detects_reordering(self, backend_mod):
        assert not backend_mod.Api._is_subsequence('甲乙', '乙甲')
        assert not backend_mod.Api._is_subsequence('甲乙', '甲')


class TestRoundtripAndRestore:
    def test_md_back_to_delta_then_restore_is_lossless(self, backend_mod, api):
        content = _delta({'insert': '原文', 'attributes': {'bold': True}},
                         {'insert': '内容\n'})
        nid = _make_delta_note(backend_mod, content)
        assert api.convert_note_format(nid, 'md')['ok']

        # 有损方向：md → delta（由 Markdown 重新生成 Delta）
        assert api.convert_note_format(nid, 'delta')['ok']
        assert api.notes_get(nid)['format'] == 'delta'
        assert '原文' in backend_mod.note_plain_text(api.notes_get(nid)['content'], 'delta')

        # 无损回退：直接用转换前的原始 Delta
        r = api.restore_delta_backup(nid)
        assert r['ok'], r
        restored = api.notes_get(nid)
        assert json.loads(restored['content']) == json.loads(content), '回退必须逐字节还原'
        assert api.note_format_info(nid)['has_delta_backup'] is False

    def test_restore_without_backup_fails_cleanly(self, api):
        nid = api.notes_create()['id']
        r = api.restore_delta_backup(nid)
        assert r['ok'] is False and '没有可还原' in r['error']

    def test_restore_creates_snapshot(self, backend_mod, api):
        nid = _make_delta_note(backend_mod, _delta({'insert': '原始\n'}))
        api.convert_note_format(nid, 'md')
        api.notes_update(nid, {'content': '改过的内容\n'})
        before = len(api.versions_list(nid))
        api.restore_delta_backup(nid)
        assert len(api.versions_list(nid)) == before + 1, '回退前也要留快照'


class TestEncryptedConversion:
    def test_unlocked_encrypted_note_converts_and_stays_encrypted(self, backend_mod, api):
        content = _delta({'insert': '机密内容\n'})
        nid = _make_delta_note(backend_mod, content)
        api.note_set_password(nid, 'pw123456')
        r = api.convert_note_format(nid, 'md')
        assert r['ok'], r
        raw = backend_mod.conn.execute(
            'SELECT content, delta_backup, format FROM notes WHERE id = ?', (nid,)).fetchone()
        assert raw['format'] == 'md'
        assert raw['content'].startswith(backend_mod.ENC_PREFIX), '转换后仍必须是密文'
        assert raw['delta_backup'].startswith(backend_mod.ENC_PREFIX), '备份也要加密'
        assert '机密内容' in api.notes_get(nid)['content'], '解锁后应能读到明文'

    def test_encrypted_note_locked_conversion_blocked(self, api, backend_mod):
        nid = _make_delta_note(backend_mod, _delta({'insert': '机密\n'}))
        api.note_set_password(nid, 'pw123456')
        api.note_lock(nid)
        r = api.convert_note_format(nid, 'md')
        assert r['ok'] is False and '解锁' in r['error']

    @pytest.mark.parametrize('target', ['md', 'delta', 'html'])
    def test_invalid_target_rejected(self, api, target):
        nid = api.notes_create()['id']
        r = api.convert_note_format(nid, target)
        assert (r['ok'] is False) == (target == 'html')
