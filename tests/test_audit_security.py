# -*- coding: utf-8 -*-
"""审计轮修掉的安全缺陷的回归测试。

每一条都对应一个**已实证**的缺陷（修之前能真的复现），所以它们不是"覆盖代码"，
而是把踩过的坑钉住 —— 这类问题一旦回来，症状往往是静默的数据泄漏或数据损坏，
靠人工点界面很难发现。
"""
import os

import pytest

# ====== 路径包含性：UNC / 盘符 / 穿越 / 备用数据流 ======

class TestSafeJoin:
    """`safe_join` 是所有**来自前端或正文的路径参数**的唯一入口。

    历史缺陷：`attachments_get_path` 直接 `os.path.join(ATTACH_DIR, note_id, filename)`，
    正文里写 `![x](attachments/\\\\attacker\\share/x.png)` 时 join 会**丢弃 ATTACH_DIR**，
    交出一个纯 UNC 路径；下游 read_file_base64 的 realpath 一旦解析它就会发起对外 SMB
    连接（NetNTLM 泄漏），打开笔记即触发。所以必须在 realpath **之前**做字符串级拒绝。
    """

    def test_rejects_unc_and_drive_and_traversal(self, backend_mod):
        base = backend_mod.ATTACH_DIR
        bad_parts = [
            ('\\\\attacker\\share', 'x.png'),      # UNC（反斜杠）
            ('//attacker/share', 'x.png'),         # UNC（正斜杠）
            ('C:', 'x.png'),                       # 盘符
            ('..', 'x.png'),                       # 父目录
            ('..\\..\\..\\Windows', 'x.png'),      # 深层穿越
            ('ok', '..\\..\\evil.png'),            # 文件名穿越
            ('ok', 'a.png:stream'),                # NTFS 备用数据流
            ('ok', 'sub/x.png'),                   # 文件名里带分隔符
            ('ok', ''),                            # 空组件
            ('ok', None),                          # 非字符串
        ]
        for parts in bad_parts:
            assert backend_mod.safe_join(base, *parts) is None, \
                '这些形状必须被拒绝：%r' % (parts,)

    def test_accepts_normal_and_stays_inside_base(self, backend_mod):
        got = backend_mod.safe_join(backend_mod.ATTACH_DIR, 'note1', 'a.png')
        assert got is not None
        assert os.path.commonpath([got, os.path.realpath(backend_mod.ATTACH_DIR)]) == \
            os.path.realpath(backend_mod.ATTACH_DIR)

    def test_attachments_get_path_requires_a_real_note(self, api, backend_mod):
        nid = api.notes_create()['id']
        assert api.attachments_get_path(nid, 'a.png')          # 正常
        assert api.attachments_get_path('\\\\attacker\\s', 'x.png') is None
        assert api.attachments_get_path('C:\\Windows', 'a.png') is None
        assert api.attachments_get_path('no-such-note', 'a.png') is None
        assert api.attachments_get_path(nid, '..\\..\\evil.png') is None


class TestFileCopyToNote:
    """`file_copy_to_note` 的 note_id 来自前端，不可信。

    历史缺陷：`os.makedirs` + `copy2` 发生在任何校验之前，于是传一个绝对路径当 note_id
    （例如用户的启动目录）就能把文件写到 ATTACH_DIR 之外任意位置、扩展名还跟着源文件走 ——
    写进启动目录即可实现持久化。外键约束只是让 DB 插入失败，文件已经落盘了。
    """

    def test_rejects_non_note_id_and_writes_nothing_outside(self, api, backend_mod, tmp_path):
        src = tmp_path / 'evil.bat'
        src.write_text('@echo off\n', encoding='utf-8')
        escape = tmp_path / 'mnp_anywhere'

        assert 'error' in api.file_copy_to_note(str(src), str(escape), 'file')
        assert 'error' in api.file_copy_to_note(str(src), '..\\..\\mnp_escaped', 'file')
        assert 'error' in api.file_copy_to_note(str(src), 'no-such-note', 'file')
        assert not escape.exists(), '不许在 ATTACH_DIR 之外落盘'

    def test_accepts_a_real_note(self, api, backend_mod, tmp_path):
        src = tmp_path / 'real.txt'
        src.write_text('hi', encoding='utf-8')
        nid = api.notes_create()['id']
        got = api.file_copy_to_note(str(src), nid, 'file')
        assert got.get('storedPath')
        real = os.path.realpath(got['storedPath'])
        assert real.startswith(os.path.realpath(backend_mod.ATTACH_DIR) + os.sep)

    def test_extension_is_whitelisted(self, api, backend_mod, tmp_path):
        """扩展名会被拼进最终文件名，所以只保留"安全形状"。"""
        src = tmp_path / 'weird'
        src.write_text('x', encoding='utf-8')
        nid = api.notes_create()['id']
        got = api.file_copy_to_note(str(src), nid, 'file')
        assert got.get('filename', '').startswith('file_')
        assert os.path.sep not in got['filename']


# ====== 加密笔记的明文不许经派生索引泄漏 ======

class TestEncryptedDerivedLeak:
    """加密笔记**从不**参与派生（与"FTS body 恒空"同一条隐私约定）。

    历史缺陷：`_refresh_derived` 只在"自己去读 content"那条分支里查 password_hash，
    而 `convert_note_format` / `todo_toggle` 会把**解密后的明文**显式传进去（它们本来
    就在解锁态才跑得动）⇒ 整段守卫被跳过，明文待办与属性写进 note_todos / note_derived，
    之后锁定甚至重启都能从 todos_list / metrics_bulk 读出来。
    """

    BODY = ('---\nsecret_key: TOP-SECRET-PROP\n---\n\n'
            '# 机密\n\n- [ ] 秘密待办ABC @2026-09-25\n\n正文秘密内容\n')

    def _locked_encrypted_note(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': '机密笔记', 'content': self.BODY})
        assert api.note_set_password(nid, 'correct-horse-battery')
        return nid

    def test_format_conversion_does_not_leak_plaintext(self, api, backend_mod):
        nid = self._locked_encrypted_note(api)
        # 解锁态切格式（前端的「转成富文本」按钮，正常操作）
        assert api.convert_note_format(nid, 'delta')
        api.note_lock(nid)

        rows = backend_mod.conn.execute(
            'SELECT props_json, preview, todo_total FROM note_derived WHERE note_id = ?',
            (nid,)).fetchall()
        for r in rows:
            assert 'TOP-SECRET-PROP' not in (r['props_json'] or '')
            assert '秘密' not in (r['preview'] or '')
            assert not r['todo_total']
        todos = backend_mod.conn.execute(
            'SELECT text FROM note_todos WHERE note_id = ?', (nid,)).fetchall()
        assert not todos, '加密笔记的待办明细不许留在库里'

    def test_locked_reads_are_empty(self, api):
        nid = self._locked_encrypted_note(api)
        assert api.convert_note_format(nid, 'delta')
        api.note_lock(nid)

        assert api.notes_get(nid)['content'] == ''          # 正文确实锁住了
        assert api.todos_list('open')['items'] == []
        assert api.todos_list('done')['items'] == []
        assert api.todos_list('open')['counts']['open'] == 0
        assert api.metrics_bulk([nid]) == []                 # 第二道闸：查询侧也过滤
        assert [n['preview'] for n in api.notes_list(None) if n['id'] == nid] == ['']

    def test_stale_dirty_rows_are_cleaned_on_startup(self, backend_mod, api):
        """存量库里的脏行（修复前写进去的）必须被启动清理扫掉 —— 光靠"将来不写"不够。"""
        nid = self._locked_encrypted_note(api)
        # 手工伪造一条"修复前留下的"脏行
        backend_mod.conn.execute(
            "INSERT OR REPLACE INTO note_derived (note_id, word_count, props_json, preview) "
            "VALUES (?, 7, ?, ?)", (nid, '{"secret_key": "LEAK"}', '泄漏的摘要'))
        backend_mod.conn.execute(
            "INSERT OR REPLACE INTO note_todos (note_id, idx, text, due, done) "
            "VALUES (?, 0, '泄漏的待办', NULL, 0)", (nid,))
        backend_mod.conn.commit()
        assert api.metrics_bulk([nid]) == []   # 查询侧已经挡住

        backend_mod._purge_encrypted_derived_leaks()   # 启动时跑的那一步

        row = backend_mod.conn.execute(
            'SELECT props_json, preview FROM note_derived WHERE note_id = ?', (nid,)).fetchone()
        assert row['props_json'] == '{}' and row['preview'] == ''
        assert not backend_mod.conn.execute(
            'SELECT 1 FROM note_todos WHERE note_id = ?', (nid,)).fetchone()


# ====== 密码路径 ======

class TestPasswordPaths:
    def test_legacy_plaintext_password_note_cannot_be_rekeyed_blindly(self, api, backend_mod):
        """存量「明文密码」笔记（有 password_hash、enc_dek 为 NULL）不许用自选新密码改密码。

        历史缺陷：守卫只查 `enc_dek`，而这种笔记 enc_dek 正好是 NULL ⇒ 守卫被跳过，
        任意调用方用自选密码调 note_set_password 就成功，随后该笔记算作"已解锁"，
        原本被隐藏的正文直接被读出来。
        """
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': '旧笔记', 'content': 'LEGACY-SECRET-CONTENT\n'})
        # 手工造出"旧版本设过密码、还没懒迁移"的状态
        backend_mod.conn.execute(
            "UPDATE notes SET password_hash = ?, enc_dek = NULL WHERE id = ?",
            ('deadbeef' * 8, nid))
        backend_mod.conn.commit()
        backend_mod._unlocked_deks.pop(nid, None)

        assert api.note_set_password(nid, 'attacker-chosen-pw') is False
        assert backend_mod._unlocked_deks.get(nid) is None
        assert api.notes_get(nid)['content'] == '', '正文必须仍然锁着'

    def test_verify_hash_clamps_iterations(self, api, backend_mod):
        """迭代数存在库里，篡改成天文数字不许把"输一次密码"变成 CPU 卡死。

        `_unwrap_dek` 早就钳制了，`_verify_hash` 漏了 —— 而它还持有全局 _db_lock，
        真跑满会把所有桥调用一起拖住。
        """
        import time
        salt = 'ab' * 16
        stored = 'pbkdf2v2:%s:%d:%s' % (salt, 10 ** 12, 'cd' * 32)
        t = time.perf_counter()
        assert api._verify_hash('whatever', stored) is False
        assert time.perf_counter() - t < 5.0, '迭代数必须被钳到 MAX_PBKDF2_ITERATIONS'

    def test_remove_password_verifies_password(self, api, backend_mod):
        """对照：移除密码这条路径确实验了密码（别在改动中把它弄坏）。"""
        nid = api.notes_create()['id']
        body = '要保住的正文\n'
        api.notes_update(nid, {'title': 'T', 'content': body})
        assert api.note_set_password(nid, 'correct-horse-battery')
        api.note_lock(nid)
        assert api.note_remove_password(nid, 'wrong-password') is False
        assert api.notes_get(nid)['content'] == ''
        assert api.note_remove_password(nid, 'correct-horse-battery') is True
        assert api.notes_get(nid)['content'] == body


# ====== 备份 / 恢复 ======

class TestBackupAndRestoreHardening:
    """恢复是破坏性操作，闸门必须真的能挡住坏备份。

    历史缺陷：`PRAGMA integrity_check == 'ok'` 对**0 字节文件**也成立（SQLite 把空文件
    当合法空库），于是 0 字节的"备份"会被判合格、拿去覆盖当前库 —— 实测把 3 篇的库
    覆盖成 0 字节，还打印「恢复完成」。
    """

    def _make_backup(self, backend_mod, tmp_path):
        bdir = tmp_path / 'backups'
        bdir.mkdir(exist_ok=True)
        good = bdir / 'notes-good.db'
        import sqlite3
        c = sqlite3.connect(str(good))
        c.execute('CREATE TABLE notes (id TEXT PRIMARY KEY, title TEXT)')
        c.execute("INSERT INTO notes VALUES ('a', '甲')")
        c.commit()
        c.close()
        return good

    def test_backup_verdict_rejects_empty_and_wrong_schema(self, backend_mod, tmp_path):
        import restore
        good = self._make_backup(backend_mod, tmp_path)

        empty = tmp_path / 'backups' / 'notes-empty.db'
        empty.write_bytes(b'')
        ok, why = restore.backup_verdict(str(empty))
        assert not ok and '0 字节' in why

        import sqlite3
        wrong = tmp_path / 'backups' / 'notes-wrong.db'
        c = sqlite3.connect(str(wrong))
        c.execute('CREATE TABLE other (x TEXT)')
        c.commit()
        c.close()
        ok, why = restore.backup_verdict(str(wrong))
        assert not ok and 'notes 表' in why

        junk = tmp_path / 'backups' / 'notes-junk.db'
        junk.write_bytes('不是 SQLite'.encode('utf-8') * 40)
        assert not restore.backup_verdict(str(junk))[0]

        assert restore.backup_verdict(str(good))[0] is True

    def test_restore_refuses_bad_backup_and_leaves_db_untouched(self, backend_mod, tmp_path):
        import restore
        good = self._make_backup(backend_mod, tmp_path)
        # 当前库就是 fixture 建好的那个（tmp_path/notes.db），写 3 篇进去
        cur = tmp_path / 'notes.db'
        for _ in range(3):
            backend_mod.api.notes_create()
        backend_mod.conn.commit()
        before = restore.count_notes(str(cur))
        size_before = os.path.getsize(str(cur))
        assert before == 3

        empty = tmp_path / 'backups' / 'notes-empty2.db'
        empty.write_bytes(b'')
        ok, msg = restore.restore(str(empty), str(tmp_path), assume_yes=True, log=lambda *_: None)
        assert not ok and '完整性校验失败' in msg
        assert restore.count_notes(str(cur)) == 3, '坏备份不许动当前库'
        assert os.path.getsize(str(cur)) == size_before

        ok, msg = restore.restore(str(good), str(tmp_path), assume_yes=True, log=lambda *_: None)
        assert ok, msg
        assert restore.count_notes(str(cur)) == 1

    def test_latest_prefers_auto_backup_over_pre_restore_snapshot(self, backend_mod, tmp_path):
        """`--from latest` 只该挑自动备份，不该挑"恢复前快照"（它可能是失败恢复的半成品）。"""
        import restore
        self._make_backup(backend_mod, tmp_path)
        snap = tmp_path / 'backups' / 'pre-restore-20260101-000000.db'
        import sqlite3
        c = sqlite3.connect(str(snap))
        c.execute('CREATE TABLE notes (id TEXT PRIMARY KEY, title TEXT)')
        c.commit()
        c.close()

        got = restore.latest_backup(str(tmp_path))
        assert got is not None
        assert got['name'] == 'notes-good.db'


# ====== 列表摘要预计算（性能回归） ======

class TestPreviewPrecompute:
    """列表摘要改为保存时预计算（note_derived.preview）。

    为什么值得钉住：`notes_list` 以前把每篇的 content 全取出来现算摘要，800 篇的库实测
    310ms 里 223ms 花在摘要上（74%）。改成读一列之后是 24ms。这条测试保证
    「摘要与正文一致」和「派生行缺失时仍能兜底」两个性质不被破坏。
    """

    def test_preview_matches_content_and_follows_edits(self, api, backend_mod):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': '# 标题\n\n这是正文摘要内容\n'})
        row = [n for n in api.notes_list(None) if n['id'] == nid][0]
        assert '这是正文摘要内容' in row['preview']
        assert '#' not in row['preview'], 'Markdown 标记要被剥掉'

        api.notes_update(nid, {'content': '换成了完全不同的正文\n'})
        row = [n for n in api.notes_list(None) if n['id'] == nid][0]
        assert '完全不同的正文' in row['preview']
        assert '摘要内容' not in row['preview'], '改完正文摘要必须跟着变'

    def test_falls_back_when_derived_row_is_missing(self, api, backend_mod):
        """派生行缺失（回填整体失败 / 旧库还没轮到）时摘要不能变成空白。"""
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': '兜底也要能摘要\n'})
        backend_mod.conn.execute('DELETE FROM note_derived WHERE note_id = ?', (nid,))
        backend_mod.conn.commit()
        row = [n for n in api.notes_list(None) if n['id'] == nid][0]
        assert '兜底' in row['preview']

    def test_encrypted_note_preview_is_always_empty(self, api):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': '机密正文\n'})
        assert api.note_set_password(nid, 'correct-horse-battery')
        for locked in (False, True):
            if locked:
                api.note_lock(nid)
            row = [n for n in api.notes_list(None) if n['id'] == nid][0]
            assert row['preview'] == '', '加密笔记的摘要必须恒空（解锁与否都一样）'


class TestNotesReorder:
    """拖拽排序改为一次桥调用（`notes_reorder`）。

    以前前端是 `for (...) await notes_update(id, {sort_order})` —— 800 篇就是 800 次
    跨语言往返 + 800 个事务。这条测试除了钉住"顺序真的生效"，还钉住**算法与旧写法等价**
    （列表顶部取最大 sort_order），否则拖一次顺序会整体倒过来。
    """

    def _titles(self, api):
        return [n['title'] for n in api.notes_list(None)]

    def test_reorder_makes_the_list_follow_the_given_order(self, api, backend_mod):
        for t in ('甲', '乙', '丙'):
            nid = api.notes_create()['id']
            api.notes_update(nid, {'title': t})
        before = self._titles(api)
        assert sorted(before) == ['丙', '乙', '甲'], '先确认初始顺序与 sort_order 有关'

        ids = [n['id'] for n in api.notes_list(None)]
        new_order = [ids[2], ids[0], ids[1]]      # 把最后一篇拖到最上面
        assert api.notes_reorder(new_order) == 3
        after = [n['id'] for n in api.notes_list(None)]
        assert after == new_order, '列表顺序必须与传入顺序一致（顶部 = 最大 sort_order）'

    def test_reorder_ignores_unknown_ids_and_deleted_notes(self, api, backend_mod):
        a = api.notes_create()['id']
        api.notes_update(a, {'title': '留下的'})
        b = api.notes_create()['id']
        api.notes_update(b, {'title': '要删的'})
        api.notes_delete(b)
        # 混进不存在的 id 与已删除的 id：不许抛异常，也不许把删掉的笔记写活
        assert api.notes_reorder(['no-such-id', a, b]) == 3
        assert [n['id'] for n in api.notes_list(None)] == [a]

    def test_reorder_does_not_bump_updated_at(self, api, backend_mod):
        """纯排序不算"修改内容"：否则拖一次所有笔记的时间戳全被刷新（旧 notes_update 的约定）。"""
        ids = []
        for t in ('一', '二'):
            nid = api.notes_create()['id']
            api.notes_update(nid, {'title': t})
            ids.append(nid)
        before = {n['id']: n['updated_at'] for n in api.notes_list(None)}
        api.notes_reorder(list(reversed(ids)))
        after = {n['id']: n['updated_at'] for n in api.notes_list(None)}
        assert after == before, '排序不该改动 updated_at'

    def test_reorder_empty_is_a_noop(self, api):
        assert api.notes_reorder([]) == 0
        assert api.notes_reorder(None) == 0


# ====== 前端竞态：静态检查（这类问题没法用单测抓）======

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDERER = os.path.join(PROJECT_ROOT, 'renderer')


def _read_js(rel):
    with open(os.path.join(RENDERER, rel), encoding='utf-8') as f:
        return f.read()


class TestFrontendRaceGuards:
    """前端竞态守卫的静态检查。

    为什么用静态检查而不是行为测试：这些都是"异步迟到回写"的时序问题
    （350ms 定时器、连点两篇笔记、并发保存），要在无头浏览器里稳定复现得靠 sleep 赌时序，
    而本项目自己就写着"不要用加长 sleep 来修这类问题"。守卫的形状是**确定性**的，钉住形状即可。
    """

    def test_props_sync_timer_carries_note_identity_and_is_cancelled(self):
        """属性面板的防抖回写必须带笔记身份、并在关面板时取消。

        否则"敲完属性值 350ms 内切笔记"会让定时器在编辑器已经换过之后触发：
        syncFromRows() 读的是当前面板 DOM、writeProps() 改的是当前编辑器顶部 ——
        等于把上一篇的属性写进另一篇的 front-matter。
        """
        src = _read_js('js/app/24-properties.js')
        assert '_syncTimerNoteId' in src, '定时器必须记下"为哪篇笔记排的"'
        assert 'cancelScheduledSync' in src, '需要一个统一的取消入口'
        # 关面板时必须走取消（而不是只把引用置空）
        close_fn = src[src.index('export function closePropsEditor'):]
        close_fn = close_fn[:close_fn.index('\n}')]
        assert 'cancelScheduledSync()' in close_fn, 'closePropsEditor 必须取消排着的回写'
        # 触发时再校验一次身份
        assert 'state.activeNoteId !== forNote' in src, '定时器触发时要复核笔记身份'

    def test_select_note_claims_its_sequence_before_the_first_await(self):
        """`selectNote` 的请求序号必须在**第一个 await 之前**同步领取。

        放在 await 之后的话，并发时先发起的调用可能后恢复执行、反而领到更大的序号 ——
        迟到的旧响应就被当成"最新"，守卫形同虚设。
        """
        src = _read_js('js/app/03-notes.js')
        body = src[src.index('export async function selectNote'):]
        body = body[:body.index('\n}\n')]
        # 必须先剥掉 `//` 行注释：函数上方那段说明里就写着"await"，直接搜会搜到注释里的
        code = '\n'.join(l for l in body.splitlines() if not l.strip().startswith('//'))
        claim = code.index('++_selectSeq')
        first_await = code.index('await ')
        assert claim < first_await, \
            '序号领取必须早于函数体内第一个 await（否则并发的旧请求会拿到更大的序号）'
        assert 'seq !== _selectSeq' in code, '拿到响应后要丢弃过期的结果'

    def test_save_uses_a_serial_queue_and_checks_the_ack(self):
        """保存要串行成队列，且必须判返回值。

        队列：`if (state._savePromise) await state._savePromise` 在并发 ≥3 时失效
        （后来的调用者同时等同一个 promise，醒来各自覆盖引用，而旧 promise 的 finally
        会把正在飞行中的引用清成 null）。
        判返回值：后端拒绝写入时返回 None，不判就会显示"已保存"、基线前进，那次改动永久丢失。
        """
        src = _read_js('js/app/03-notes.js')
        body = src[src.index('export async function saveCurrentNote'):]
        body = body[:body.index('\n}\n')]
        assert '_saveChain' in body, '保存必须挂到串行队列尾'
        assert 'if (!ack)' in body, 'notes_update 的返回值必须判空（后端会返回 None 表示拒绝写入）'

    def test_search_filter_drives_the_list_from_data_not_dom(self):
        """搜索筛选的真相源必须是**数据**，不能是 DOM 上的类。

        两代实现都在修同一个坑：
          · 第一代：筛选只写在 `.note-item` 的 `hidden-by-search` 类上，列表一重建就丢
            （"筛选莫名失效、高亮却还在"）；
          · 第二代（窗口化渲染之后）：**必须**改成数据侧 —— 撑高块要按"过滤后的行数"算，
            靠给行加类的话，被隐藏的行会留下大片空白。
        所以现在：过滤结果写进 `state.searchMatched`，`listItems()` 据此取集合，
        窗口只渲染命中的行。
        """
        boot = _read_js('js/app/09-boot.js')
        assert 'state.searchMatched = matched' in boot, '匹配集合要写进 state 供窗口化取用'
        assert 'renderNoteList()' in boot[boot.index('export function applySearchToDom'):], \
            '筛选变化后要重建列表窗口'

        virt = _read_js('js/app/30-virtual-list.js')
        assert 'export function listItems' in virt, '窗口化必须从 listItems() 取集合'
        assert 'state.searchMatched' in virt, 'listItems 要读搜索命中集合'
        # 窗口化之后不该再靠 hidden-by-search 类来控制可见性
        notes = _read_js('js/app/03-notes.js')
        render_fn = notes[notes.index('export function renderNoteList'):]
        render_fn = render_fn[:render_fn.index('\n}\n')]
        assert 'renderWindow(' in render_fn, 'renderNoteList 必须走窗口化渲染'
        assert 'hidden-by-search' not in render_fn

    def test_select_all_uses_data_not_visible_dom(self):
        """批量「全选」必须按数据取集合。

        窗口化之后 DOM 里只有窗口内的十几行，照旧写法"全选"只会选到那十几行。
        """
        src = _read_js('js/app/12-bulk-actions.js')
        body = src[src.index('export function selectAllVisible'):]
        body = body[:body.index('\n}\n')]
        assert 'listItems()' in body, '全选要按 listItems()（与列表渲染同源）取集合'
        assert 'querySelectorAll' not in body, '不能再数 DOM 里可见的行'

    def test_drag_reorder_indexes_come_from_data(self):
        """拖拽排序的源/目标索引必须从数据里定位。

        窗口化之后 `dom.noteList.children` 里夹着上下撑高块，而且只含窗口内的行 ——
        `[...children].indexOf(item)` 既会被撑高块偏移，跨屏拖动还会算出完全错误的落点。
        """
        src = _read_js('js/app/07-formula-security-dnd.js')
        drag = src[src.index('====== 笔记列表拖拽排序 ======'):]
        drag = drag[:drag.index('====== 通用拖拽排序函数 ======')]
        # 先剥注释：那段说明里**故意**写了旧写法 `[...children].indexOf(item)` 作为反例
        code = '\n'.join(l for l in drag.splitlines() if not l.strip().startswith('//'))
        assert 'dom.noteList.children' not in code, '拖拽索引不能再用 DOM 位置'
        assert 'dataIndexOfItem' in code, '要按 data-note-id 在 state.notes 里定位'


# ====== 批量接口的 SQLite 变量上限 ======

class TestBatchChunking:
    """批量接口必须分块：SQLite 的绑定变量数有上限（默认 32766）。

    以前 `notes_move_many` / `metrics_bulk` / `notes_table` / 排除词过滤等把 id 数量
    原样展开成 `?,?,?…`，"全选 → 批量操作"在大库上就会抛 too many SQL variables，
    而界面只看到"操作失败"。这类 bug 在几十篇的测试库里**永远**不会暴露 ——
    所以这里的用例刻意造 >SQL_CHUNK 条数据，真的跨过块边界。
    """

    def _make_many(self, api, n):
        ids = []
        for i in range(n):
            nid = api.notes_create()['id']
            api.notes_update(nid, {'title': '笔记%03d' % i, 'content': '内容 %d\n' % i})
            ids.append(nid)
        return ids

    def test_move_many_across_chunk_boundary(self, api, backend_mod):
        n = backend_mod.SQL_CHUNK + 37          # 跨过块边界
        ids = self._make_many(api, n)
        nb = api.notebooks_create('大挪移')['id']
        moved = api.notes_move_many(ids, nb)
        assert moved == n, '应把全部 %d 篇都移过去（分块后每块都要算数）' % n
        assert sorted(r['id'] for r in api.notes_list(nb)) == sorted(ids)

    def test_metrics_bulk_across_chunk_boundary(self, api, backend_mod):
        n = backend_mod.SQL_CHUNK + 5
        ids = self._make_many(api, n)
        rows = api.metrics_bulk(ids)
        assert len(rows) == n, '分块后不能丢行'

    def test_table_view_across_chunk_boundary(self, api, backend_mod):
        n = backend_mod.SQL_CHUNK + 5
        ids = self._make_many(api, n)
        rows = api.notes_table(ids)
        assert len(rows) == n, '表格视图在分块后不能丢行'

    def test_exclude_filter_across_chunk_boundary(self, api, backend_mod):
        """`-排除词` 的过滤走 in-Python 比对 + 分批取正文，跨块时不能把整批都排掉。"""
        n = backend_mod.SQL_CHUNK + 5
        ids = self._make_many(api, n)
        api.notes_update(ids[0], {'content': '这篇有关键词 蓝鲸计划\n'})
        kept = api._filter_excluded(ids, ['蓝鲸计划'])
        assert len(kept) == n - 1, '只该排掉那一篇，不能因为分块把别的也误伤'
        assert ids[0] not in kept


class TestRobustnessGuards:
    """几处"输入不是预期类型时不该炸"的守卫。

    共同点：这些值最终都来自**正文/库**（Delta 行属性、表名插值），
    而不是开发者能控制的字面量。出问题时的症状往往不是崩溃，而是
    **异常消息里带着整串正文进 error.log**，或者一次巨量内存分配。
    """

    def test_weird_delta_header_does_not_raise_or_allocate_wildly(self, backend_mod):
        """`attributes.header` 必须是任意值都不炸、且夹紧到 [1,6]。

        历史缺陷：直接 `'#' * int(header)`。于是
          · 非数字 → ValueError，而异常消息里会带上**那串原始内容**（正文进日志）；
          · 大数字 → 一次性分配巨量字符串。
        与同文件 `_md_embed` 里 `int(style)` 的处理口径（try + 夹紧）对齐。
        """
        import json
        cases = [
            ('正文里的字符串', '身份证号 3301xxxxxxxxxxxxxx'),
            ('超大数字', '2000000000'),
            ('零与负数', '0'), ('负数', '-5'),
            ('浮点', '3.7'),
            ('字典', {'a': 1}),
        ]
        for label, header in cases:
            delta = json.dumps({'ops': [{'insert': '标题文字'},
                                        {'insert': '\n', 'attributes': {'header': header}}]},
                               ensure_ascii=False)
            out = backend_mod._delta_to_markdown(delta)      # 不抛异常
            assert out is not None, '%s：不该返回 None' % label
            assert len(out) < 1000, '%s：不该产生巨量输出' % label
            if out.strip():
                hashes = len(out.split(' ')[0])
                assert 1 <= hashes <= 6, '%s：标题级别必须夹在 1..6，实际 %d' % (label, hashes)

    def test_unknown_table_name_is_rejected(self, backend_mod):
        """`_next_sort_order` 的表名是插值进 SQL 的 —— 加断言把"只传白名单"钉住。"""
        import pytest as _pytest
        assert backend_mod._next_sort_order('notes') >= 0
        with _pytest.raises(AssertionError):
            backend_mod._next_sort_order('notes; DROP TABLE notes')
        with _pytest.raises(AssertionError):
            backend_mod._next_sort_order('sqlite_master')

    def test_no_inline_event_handlers_in_generated_html(self):
        """拼进 innerHTML 的 HTML 里不许出现内联事件处理器（`onerror=` / `onclick=` …）。

        封面以前是 `'<img ... onerror="this.style.display=...">'` —— 当前虽不可注入
        （只有预设值能写 cover_value），但那是"靠上游碰巧干净"而不是"这里安全"：
        内联处理器是 CSP 最该消失的一类东西，而且那串把手写标题拼进了 JS 字符串里。
        现在改成 `data-cover-fallback` + 容器上的**委托捕获监听**。

        判据只认 HTML 属性形态（`on<event>=` 出现在字符串/模板里），
        不误伤 JS 属性赋值（`img.onerror = fn`）与注释。
        """
        import re
        pattern = re.compile(r'''["'`][^"'`]*<[a-zA-Z][^"'`]*\son[a-z]+\s*=''')
        offenders = []
        for rel in ('js/app/08-appearance2.js', 'js/app/03-notes.js', 'js/app/02-editor.js',
                    'js/quill/quill-deco.js', 'js/quill/quill-blots.js'):
            src = _read_js(rel)
            code = '\n'.join(l for l in src.splitlines() if not l.strip().startswith('//'))
            for m in pattern.finditer(code):
                offenders.append('%s: %s' % (rel, m.group(0)[-60:]))
        assert not offenders, '生成 HTML 时用了内联事件处理器：\n' + '\n'.join(offenders)


    def test_note_appearance_sliders_debounce_instead_of_writing_per_input(self):
        """五个外观滑杆的 `input` 处理器里不许直接写库。

        以前每个 `input` 事件都 `await notes_update` → `notes_get` → `applyNoteBackground`，
        而最后那步要 `read_file_base64` 重读图片 + `analyzeImageColor` 整图分析。
        拖一次滑杆 = 几十次跨语言写库 + 几十次整图分析，而视觉效果其实只是三个 CSS 值。
        现在拖动中只改本层 CSS，落库走 `queueNoteField`（150ms 防抖），松手 `flushNoteTuning()`。
        """
        src = _read_js('js/app/04-appearance.js')
        ids = ['#note-bg-blur', '#note-ui-scrim', '#note-content-scrim',
               '#note-bg-opacity', '#note-bg-zoom']
        for sel in ids:
            at = src.index("$('%s').addEventListener('input'" % sel)
            body = src[at:]
            body = body[:body.index('\n});')]
            assert 'notes_update' not in body, \
                "%s 的 input 处理器还在直接写库（应为本地立即 + 防抖落库）" % sel
            assert 'queueNoteField(' in body, '%s 没有走防抖落库' % sel
        # 松手要立刻落库 + 做一次完整刷新
        assert 'export function flushNoteTuning' in src
        assert ".addEventListener('change', () => flushNoteTuning())" in src


class TestBridgeProjection:
    """桥接层的返回值投影：只回前端真会读的字段，不回整篇正文。

    为什么值得钉住：`content` 可能几十上百 KB，每次过桥都要序列化一遍；而这些接口
    （新建/复制/模板/捕获/恢复版本）前端**只读 id（恢复版本还读 title/format）**。
    ⚠️ 反过来的那一半同样重要：**后端内部**有 5 处**串联**使用 `notes_update` 的返回值，
    所以投影只能放在桥接层 —— 上一轮把投影写进后端 `notes_update`，结果
    "模板/捕获建出来的笔记丢掉 content"。下面两条测试就是那一课的两个方向。
    """

    def test_backend_internal_returns_stay_complete(self, backend_mod, tmp_path):
        """后端方法直接调用时必须返回完整笔记（含 content）—— 它们自己会串联使用。"""
        api = backend_mod.api
        tpl = api.template_create('周报', '# {{title}}\n\n本周内容\n')
        note = api.notes_create_from_template(tpl['id'], '第 1 周')
        assert 'content' in note, '后端内部调用必须拿得到 content（桥上才投影）'
        assert '本周内容' in note['content']
        assert note['format'] == 'md'

        api.notebooks_create('捕获测试')
        cap = api.capture_text('收件箱内容\n第二行\n')
        assert 'content' in cap and '收件箱内容' in cap['content'], '捕获也要拿到正文'

    def test_bridge_projection_keeps_what_frontend_reads(self, app_ns):
        """桥接层投影后：id 必在；恢复版本还要保住 title/format；正文不再过桥。"""
        bridge = app_ns['AppApi']
        ack = app_ns['_ack']
        full = {'id': 'n1', 'title': '标题', 'content': 'x' * 5000, 'format': 'md',
                'updated_at': '2026-01-01 00:00:00', 'created_at': '2026-01-01 00:00:00',
                'has_password': 0, 'bg_type': 'global', 'notebook_id': None}
        got = ack(full)
        assert got['id'] == 'n1'
        assert got['title'] == '标题' and got['format'] == 'md', \
            'versions_restore 之后前端要按 format 分流编辑器、要更新标题'
        assert 'content' not in got, '正文不该再过桥（这正是投影的目的）'
        assert len(str(got)) < len(str(full)) / 10

        # 非 dict（None / 错误 dict）必须原样返回：
        # 把 None 变成 {} 会让"后端拒绝"看起来像成功 —— 那正是丢数据那类 bug 的形态
        assert ack(None) is None
        assert ack(False) is False
        assert ack({'error': '笔记不存在'}) == {'error': '笔记不存在'}

        # 桥接方法确实用上了投影（抽查，防止有人顺手把 _ack 去掉）
        import inspect
        for name in ('notes_create', 'notes_duplicate', 'daily_note_open',
                     'capture_image', 'notes_create_from_template', 'versions_restore'):
            src = inspect.getsource(getattr(bridge, name))
            assert '_ack(' in src, '%s 没有走投影' % name

    def test_projection_never_hides_a_backend_refusal(self, app_ns):
        """投影不能把"拒绝"洗成"成功"：不存在的 id 必须仍然是假值。

        ⚠️ 这条测过一个真 bug：`_ack` 最初按字段白名单过一遍，于是后端返回的
        `{'error': '...'}` 被压成 `{}` —— 空 dict 在前端是**真值**，一次失败就成了"成功"。
        """
        import backend as _b
        bridge = app_ns['AppApi'](_b.api)
        assert not bridge.notes_duplicate('no-such-note')
        # 带 error 的 dict 必须原样透出（不是空 dict）
        got = app_ns['_ack']({'error': '笔记不存在'})
        assert got == {'error': '笔记不存在'}, 'error 不能被投影吃掉'


class TestUnlockTtl:
    """后端解锁缓存必须**自己会过期**。

    以前"闲置自动锁定"完全由前端驱动（它自己的 setInterval + `unlockedNotes`），
    后端那个字典**永不过期** —— 于是"解锁状态是后端安全边界"这句话在过期这件事上不成立：
    前端一旦漏了（脚本出错、窗口被挂起、用户直接改内存标志），密钥就在进程里一直留着。

    实现上要小心两件事，下面各有一条测试：
      · 惰性过期只在"有人读"时生效，所以还要能在轮询里主动扫（`reminder_check` 搭车）；
      · TTL 为 0（默认「关闭」）时**绝不能**清 —— 那会让默认配置下所有加密笔记突然锁上。
    """

    def test_entries_expire_lazily(self, backend_mod):
        api = backend_mod.api
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': '机密\n'})
        assert api.note_set_password(nid, 'correct-horse-battery')
        assert nid in backend_mod._unlocked_deks

        assert api.note_set_lock_ttl(1)                 # 1 分钟
        try:
            # 把时间戳往前拨 2 分钟 —— 不 sleep 也能验证过期语义
            backend_mod._unlocked_deks._stamps[nid] -= 120
            assert nid not in backend_mod._unlocked_deks, '读的时候就该发现过期'
            assert api.notes_get(nid)['content'] == '', '过期后正文必须读不到'
        finally:
            api.note_set_lock_ttl(0)

    def test_sweep_clears_expired_without_a_read(self, backend_mod):
        """没人读的时候靠 `sweep()` 主动清（搭在 30 秒提醒轮询上）。

        这条同时验证"搭车"真的接上了：`reminder_check` 里会调 sweep。
        """
        api = backend_mod.api
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': '机密\n'})
        assert api.note_set_password(nid, 'correct-horse-battery')
        api.note_set_lock_ttl(5)
        try:
            backend_mod._unlocked_deks._stamps[nid] -= 6 * 60
            assert api.reminder_check() is not None      # 轮询一次
            assert nid not in dict.keys(backend_mod._unlocked_deks), \
                'reminder_check 搭车清理没生效'
        finally:
            api.note_set_lock_ttl(0)

    def test_ttl_zero_means_never_expire(self, backend_mod):
        """默认「关闭」时不许清 —— 否则默认配置下加密笔记会突然自己锁上。"""
        api = backend_mod.api
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': 'T', 'content': '机密\n'})
        assert api.note_set_password(nid, 'correct-horse-battery')
        api.note_set_lock_ttl(0)
        backend_mod._unlocked_deks._stamps[nid] -= 10 * 24 * 3600     # 拨到 10 天前
        assert nid in backend_mod._unlocked_deks
        assert api.notes_get(nid)['content'] == '机密\n'

    def test_unlock_status_reports_what_is_still_unlocked(self, backend_mod):
        """前端靠这个接口与后端对齐（后端已过期而界面还显示明文 = 等于没锁）。"""
        api = backend_mod.api
        a = api.notes_create()['id']
        api.notes_update(a, {'title': 'A', 'content': '甲\n'})
        b = api.notes_create()['id']
        api.notes_update(b, {'title': 'B', 'content': '乙\n'})
        api.note_set_password(a, 'correct-horse-battery')
        api.note_set_password(b, 'correct-horse-battery')
        assert set(api.note_unlock_status()) >= {a, b}
        api.note_lock(a)
        assert a not in api.note_unlock_status()
        assert b in api.note_unlock_status()

    def test_setting_ttl_survives_garbage_input(self, backend_mod):
        api = backend_mod.api
        assert api.note_set_lock_ttl('abc') is False
        assert api.note_set_lock_ttl(None) is False
        assert api.note_set_lock_ttl(-5) is True         # 负数夹到 0 = 不过期
        assert backend_mod._unlocked_deks.ttl_minutes == 0


class TestScopeExportChunking:
    """范围导出（按笔记本 / 标签）要保留范围内的笔记、裁掉其余。

    ⚠️ 这里曾写错过一次：`DELETE ... WHERE id NOT IN (...)` **不能**像 `IN` 那样简单分块 ——
    每块只知道自己的集合，于是第二块会把第一块要保留的笔记当"不在我这一块里"删掉。
    正确做法是先算出"要删的 id 集合"再对删除集分块。这条用例就是那个 bug 的哨兵。
    """

    def test_export_by_notebook_keeps_all_in_scope_notes(self, api, backend_mod, tmp_path):
        import zipfile
        n = backend_mod.SQL_CHUNK + 20
        nb = api.notebooks_create('要导出的本')['id']
        inside = []
        for i in range(n):
            nid = api.notes_create(nb)['id']
            api.notes_update(nid, {'title': '本内%03d' % i, 'content': 'x\n'})
            inside.append(nid)
        for i in range(5):                      # 范围外的笔记
            nid = api.notes_create()['id']
            api.notes_update(nid, {'title': '本外%d' % i, 'content': 'y\n'})

        out = str(tmp_path / 'scope.zip')
        assert api.export_notes_zip(out, notebook_id=nb) > 0
        with zipfile.ZipFile(out) as zf:
            assert 'notes.db' in zf.namelist()

        # 解包出来的库必须**正好**只含范围内那 n 篇（一篇都不能少、也不能多）
        import sqlite3
        with zipfile.ZipFile(out) as zf:
            extracted = str(tmp_path / 'extracted.db')
            with open(extracted, 'wb') as f:
                f.write(zf.read('notes.db'))
        c = sqlite3.connect(extracted)
        try:
            titles = [r[0] for r in c.execute('SELECT title FROM notes ORDER BY title')]
        finally:
            c.close()
        assert len(titles) == n, \
            '按笔记本导出丢了笔记（%d/%d）—— NOT IN 分块写错时正是这个症状' % (len(titles), n)
        assert all(t.startswith('本内') for t in titles)
