# -*- coding: utf-8 -*-
"""数据库定期备份：24h 判定、backup API、滚动 7 份、失败静默"""
import os
import time


def test_first_backup_created(api, backend_mod, tmp_path):
    api.notes_create()
    dest = backend_mod.backup_database()
    assert dest and os.path.exists(dest)
    # 备份可打开且含数据
    import sqlite3
    c = sqlite3.connect(dest)
    assert c.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    c.close()


def test_no_duplicate_within_24h(api, backend_mod):
    api.notes_create()
    first = backend_mod.backup_database()
    assert first
    assert backend_mod.backup_database() is None  # 24h 内不重复


def test_rolling_keep_7(api, backend_mod, tmp_path):
    api.notes_create()
    bdir = backend_mod.BACKUP_DIR
    os.makedirs(bdir, exist_ok=True)
    # 伪造 9 个旧备份（mtime 拨回 48h 前，触发新备份 + 滚动清理）
    old = time.time() - 48 * 3600
    for i in range(9):
        p = os.path.join(bdir, f'notes-2026010{i}-000000.db')
        open(p, 'wb').close()
        os.utime(p, (old, old))
    dest = backend_mod.backup_database()
    assert dest
    remaining = sorted(f for f in os.listdir(bdir) if f.startswith('notes-'))
    assert len(remaining) == 7
    assert os.path.basename(dest) in remaining  # 最新的保住了
    assert remaining == sorted(remaining)


def test_missing_db_silent(backend_mod, tmp_path):
    backend_mod.conn.close()
    os.remove(backend_mod.DB_PATH)
    assert backend_mod.backup_database() is None  # 静默返回，不抛异常
