# -*- coding: utf-8 -*-
"""加密全链路（移植自历史 E2E 40 项）：envelope AES-GCM、后端解锁缓存、版本、迁移"""
import hashlib

import pytest

DELTA1 = '{"ops":[{"insert":"秘密内容\\n"}]}'
DELTA2 = '{"ops":[{"insert":"版本1\\n"}]}'


@pytest.fixture
def enc_note(api):
    """一篇已设密码（保持解锁态）的笔记"""
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': 'e2e测试', 'content': DELTA1})
    assert api.note_set_password(nid, 'pass123456')
    return nid


def raw(backend_mod, nid):
    return backend_mod.conn.execute(
        "SELECT content, password_hash, enc_dek FROM notes WHERE id=?", (nid,)).fetchone()


class TestSetPassword:
    def test_db_is_ciphertext(self, api, backend_mod, enc_note):
        row = raw(backend_mod, enc_note)
        assert row['content'].startswith(backend_mod.ENC_PREFIX)
        assert row['enc_dek'].startswith('dekv1:')
        assert row['password_hash'].startswith('pbkdf2v2:')

    def test_stays_unlocked_after_set(self, api, enc_note):
        assert api.notes_get(enc_note)['content'] == DELTA1

    def test_too_short_password_rejected(self, api):
        nid = api.notes_create()['id']
        assert api.note_set_password(nid, '12345') is False

    def test_overwrite_forbidden_when_locked(self, api, enc_note):
        api.note_lock(enc_note)
        assert api.note_set_password(enc_note, 'attacker123') is False


class TestLockUnlock:
    def test_locked_hides_content_even_if_frontend_lies(self, api, enc_note):
        api.note_lock(enc_note)
        g = api.notes_get(enc_note, True)  # 前端伪造 unlocked=True 无效
        assert g['is_encrypted'] is True and g['content'] == ''

    def test_locked_rejects_content_write(self, api, backend_mod, enc_note):
        api.note_lock(enc_note)
        api.notes_update(enc_note, {'content': '{"ops":[{"insert":"注入\\n"}]}', 'title': '新标题'})
        row = raw(backend_mod, enc_note)
        assert row['content'].startswith(backend_mod.ENC_PREFIX)  # 密文未被明文覆盖
        assert api.notes_get(enc_note)['title'] == '新标题'  # 标题仍可更新

    def test_wrong_password_rejected(self, api, enc_note):
        api.note_lock(enc_note)
        assert api.note_verify_password(enc_note, 'wrongpass') is False

    def test_unlock_restores_plaintext(self, api, enc_note):
        api.note_lock(enc_note)
        assert api.note_verify_password(enc_note, 'pass123456') is True
        assert api.notes_get(enc_note)['content'] == DELTA1

    def test_no_password_note_verify_passes(self, api):
        nid = api.notes_create()['id']
        assert api.note_verify_password(nid, 'anything') is True


class TestVersions:
    def test_version_encrypted_and_dedup(self, api, backend_mod, enc_note):
        vid = api.versions_create(enc_note, 'v1', DELTA2)
        assert vid
        vrow = backend_mod.conn.execute("SELECT content FROM versions WHERE id=?", (vid,)).fetchone()
        assert vrow['content'].startswith(backend_mod.ENC_PREFIX)
        # 同明文去重（密文含随机 nonce，需解密比较）
        assert api.versions_create(enc_note, 'v1', DELTA2) is None

    def test_version_get_restore(self, api, backend_mod, enc_note):
        vid = api.versions_create(enc_note, 'v1', DELTA2)
        assert api.versions_get(vid)['content'] == DELTA2
        restored = api.versions_restore(vid)
        assert restored['content'] == DELTA2
        assert raw(backend_mod, enc_note)['content'].startswith(backend_mod.ENC_PREFIX)

    def test_locked_versions_denied(self, api, enc_note):
        vid = api.versions_create(enc_note, 'v1', DELTA2)
        api.note_lock(enc_note)
        assert api.versions_get(vid, True)['content'] == ''
        assert api.versions_restore(vid, True) is None
        assert api.versions_create(enc_note, 'v2', '{"ops":[{"insert":"x\\n"}]}') is None


class TestChangeRemovePassword:
    def test_change_password_zero_reencrypt(self, api, backend_mod, enc_note):
        before = raw(backend_mod, enc_note)
        assert api.note_change_password(enc_note, 'pass123456', 'newpass789')
        after = raw(backend_mod, enc_note)
        assert after['content'] == before['content']      # 内容零重加密
        assert after['enc_dek'] != before['enc_dek']       # DEK 已重包裹
        api.note_lock(enc_note)
        assert api.note_verify_password(enc_note, 'pass123456') is False
        assert api.note_verify_password(enc_note, 'newpass789') is True

    def test_remove_password_back_to_plaintext(self, api, backend_mod, enc_note):
        api.versions_create(enc_note, 'v1', DELTA2)
        assert api.note_remove_password(enc_note, 'pass123456')
        row = raw(backend_mod, enc_note)
        assert not row['content'].startswith(backend_mod.ENC_PREFIX)
        assert row['password_hash'] is None and row['enc_dek'] is None
        vrow = backend_mod.conn.execute(
            "SELECT content FROM versions WHERE note_id=?", (enc_note,)).fetchone()
        assert not vrow['content'].startswith(backend_mod.ENC_PREFIX)


class TestLegacyMigration:
    def test_legacy_sha256_lazy_migration(self, api, backend_mod):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'content': '{"ops":[{"insert":"旧笔记\\n"}]}'})
        # 手工造旧裸 SHA-256 哈希 + 明文历史版本（模拟历史数据）
        backend_mod.conn.execute("UPDATE notes SET password_hash=? WHERE id=?",
                                 (hashlib.sha256('legacy0606'.encode()).hexdigest(), nid))
        backend_mod.conn.execute(
            "INSERT INTO versions (id, note_id, title, content) VALUES ('v-legacy', ?, 't', ?)",
            (nid, '{"ops":[{"insert":"旧版本\\n"}]}'))
        backend_mod.conn.commit()
        assert api.note_verify_password(nid, 'wrong12345') is False
        assert api.note_verify_password(nid, 'legacy0606') is True
        row = raw(backend_mod, nid)
        assert row['content'].startswith(backend_mod.ENC_PREFIX)      # 正文已迁移加密
        assert row['password_hash'].startswith('pbkdf2v2:')            # 哈希已升级
        assert row['enc_dek'].startswith('dekv1:')
        vrow = backend_mod.conn.execute(
            "SELECT content FROM versions WHERE id='v-legacy'").fetchone()
        assert vrow['content'].startswith(backend_mod.ENC_PREFIX)      # 版本已迁移加密
        assert api.notes_get(nid)['content'] == '{"ops":[{"insert":"旧笔记\\n"}]}'
        # 二次解锁正常
        api.note_lock(nid)
        assert api.note_verify_password(nid, 'legacy0606') is True
        assert api.notes_get(nid)['content'] == '{"ops":[{"insert":"旧笔记\\n"}]}'


class TestCryptoPrimitives:
    def test_aad_binds_note_id(self, backend_mod):
        import os as _os
        dek = _os.urandom(32)
        ct = backend_mod._encrypt_content(dek, '{"ops":[]}', 'note-1')
        assert backend_mod._decrypt_content(dek, ct, 'note-1') == '{"ops":[]}'
        assert backend_mod._decrypt_content(dek, ct, 'note-2') is None  # AAD 绑定

    def test_wrap_unwrap_dek(self, backend_mod):
        import os as _os
        dek = _os.urandom(32)
        wrapped = backend_mod._wrap_dek(dek, 'secret123')
        assert backend_mod._unwrap_dek(wrapped, 'secret123') == dek
        assert backend_mod._unwrap_dek(wrapped, 'wrong-pass') is None

    def test_unwrap_dek_caps_iterations(self, backend_mod, monkeypatch):
        """迭代数取自库里的字段：损坏或被篡改的库塞天文数字不能把解锁变成 CPU 卡死。

        PBKDF2 无法短路失败，迭代数多大就得算多久，所以解包时必须截断到上限。
        这里替换 _derive_kek 记录实参，避免真的花掉 10 亿次迭代的时间。
        """
        seen = {}

        def fake_derive(password, salt, iterations=backend_mod.PBKDF2_ITERATIONS):
            seen['iters'] = iterations
            raise ValueError('不真的计算')

        monkeypatch.setattr(backend_mod, '_derive_kek', fake_derive)
        enc = 'dekv1:%s:%d:%s' % ('00' * 32, 10 ** 9, 'AAAA')
        assert backend_mod._unwrap_dek(enc, 'whatever') is None
        assert seen['iters'] == backend_mod.MAX_PBKDF2_ITERATIONS

    def test_unwrap_dek_keeps_normal_iterations(self, backend_mod, monkeypatch):
        """正常范围内的迭代数必须原样使用（不能把合法库一起截断成错的）"""
        seen = {}

        def fake_derive(password, salt, iterations=backend_mod.PBKDF2_ITERATIONS):
            seen['iters'] = iterations
            raise ValueError('不真的计算')

        monkeypatch.setattr(backend_mod, '_derive_kek', fake_derive)
        enc = 'dekv1:%s:%d:%s' % ('00' * 32, 200000, 'AAAA')
        assert backend_mod._unwrap_dek(enc, 'whatever') is None
        assert seen['iters'] == 200000
