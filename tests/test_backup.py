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
    # 判据只数**备份文件本身**（`.db`）。以前这里数的是所有 `notes-*`，而 WAL 模式下
    # 备份过程会留下 `notes-<时间戳>.db.part-wal` / `.part-shm` 两个伴生文件，
    # 一份备份被数成三份、7 撑成 9 —— 这条断言因此红过（那次红是对的：伴生文件确实
    # 该被清掉，见下面那条独立断言）。
    remaining = sorted(f for f in os.listdir(bdir)
                       if f.startswith('notes-') and f.endswith('.db'))
    assert len(remaining) == 7
    assert os.path.basename(dest) in remaining  # 最新的保住了
    assert remaining == sorted(remaining)


def test_backup_leaves_no_wal_sidecar_files(api, backend_mod, tmp_path):
    """备份完成后，备份目录里不许留下 `-wal` / `-shm` 伴生文件。

    为什么单独钉一条：WAL 模式下 `sqlite3.connect(不存在的文件)` 会顺手生成
    `<name>-wal` / `<name>-shm`，而 `os.replace(tmp, dest)` 只搬主文件 ——
    两个伴生文件就留在备份目录里当垃圾（实测见过 0 字节的 `.part-wal`
    和 32KB 的 `.part-shm`）。它们不会损坏数据，但会让"保留 7 份"的目录越堆越脏，
    而且 `restore.py --list` 得靠过滤才能不出错。
    """
    api.notes_create()
    dest = backend_mod.backup_database()
    assert dest
    leftovers = [f for f in os.listdir(backend_mod.BACKUP_DIR)
                 if f.startswith('notes-') and not f.endswith('.db')]
    assert leftovers == [], '备份目录里不该有非 .db 的 notes-* 残留：%r' % leftovers


def test_missing_db_silent(backend_mod, tmp_path):
    backend_mod.conn.close()
    os.remove(backend_mod.DB_PATH)
    assert backend_mod.backup_database() is None  # 静默返回，不抛异常
