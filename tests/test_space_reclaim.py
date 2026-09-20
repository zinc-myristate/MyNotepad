# -*- coding: utf-8 -*-
"""数据库空间回收（VACUUM）单测。

背景（2026-09-20 实测）：图片外置迁移把正文里的 base64 挪到 attachments/ 后，
SQLite **不会自动归还**空闲页；历史版本快照反复写入再按上限删除同样持续堆积。
实测某真实库：文件 12.02 MB，其中空闲页 3046 页 = 11.90 MB（占 99%），有效数据仅 0.12 MB。

这里验证：阈值判定、回收效果、幂等性、以及「不丢数据」。
"""
import os
import sqlite3


def _bloat(db_path, rows=60, size=200_000):
    """往表里灌大字段再删掉，制造空闲页（模拟图片外置/版本快照的生命周期）"""
    c = sqlite3.connect(db_path)
    c.execute("CREATE TABLE IF NOT EXISTS _blob(id INTEGER PRIMARY KEY, payload TEXT)")
    for _ in range(3):
        c.executemany("INSERT INTO _blob(payload) VALUES (?)",
                      [('x' * size,) for _ in range(rows)])
        c.commit()
        c.execute("DELETE FROM _blob")
        c.commit()
    c.execute("DROP TABLE _blob")
    c.commit()
    c.close()


def test_space_stats_reports_free_pages(backend_mod, tmp_path):
    """space_stats 应正确报出页大小/页数/空闲页与占比"""
    db = str(tmp_path / 'notes.db')
    backend_mod.api.notes_create()
    _bloat(db)

    st = backend_mod.space_stats()
    assert st is not None, '应能读到统计'
    page_size, page_count, freelist, free_bytes, ratio = st
    assert page_size > 0 and page_count > 0
    assert freelist > 0, '制造了大字段删除，应存在空闲页'
    assert free_bytes == freelist * page_size
    assert 0.0 < ratio <= 1.0


def test_reclaim_space_shrinks_file_and_keeps_data(backend_mod, tmp_path):
    """空闲页占比超阈值时应回收，且数据一条不少、库仍完整"""
    api = backend_mod.api
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '回收测试', 'content': '{"ops":[{"insert":"hello\\n"}]}'})
    before_notes = api.notes_list()
    db = str(tmp_path / 'notes.db')
    size_before = os.path.getsize(db)
    _bloat(db)
    bloated = os.path.getsize(db)
    assert bloated > size_before, '应先被撑大'

    _ps, _pc, _fl, _free, ratio = backend_mod.space_stats()
    assert ratio >= backend_mod.reclaim_space.__defaults__[0], '空闲占比应已超阈值'
    did, before_b, after_b = backend_mod.reclaim_space()

    assert did is True, '超过阈值应执行回收'
    assert before_b == bloated
    assert after_b < before_b, '回收后文件应变小（%s -> %s）' % (before_b, after_b)
    assert after_b < bloated / 2, '应回收掉大部分空间'
    # 数据完整性
    assert backend_mod.check_integrity() is True
    after_notes = api.notes_list()
    assert len(after_notes) == len(before_notes)
    assert after_notes[0]['id'] == nid
    assert api.notes_get(nid)['title'] == '回收测试'
    # 空闲页应基本清空
    assert backend_mod.space_stats()[4] < 0.05


def test_reclaim_space_skips_when_not_needed(backend_mod, tmp_path):
    """空闲页占比低于阈值时必须跳过（不能每次启动都重写整个库）"""
    backend_mod.api.notes_create()
    assert backend_mod.space_stats()[4] < 0.30, '新库不应有大片空闲页'
    did, before_b, after_b = backend_mod.reclaim_space()
    assert did is False, '未超阈值不应执行 VACUUM'
    assert before_b == after_b


def test_reclaim_space_is_idempotent(backend_mod, tmp_path):
    """回收一次后再调用应跳过（幂等）"""
    db = str(tmp_path / 'notes.db')
    backend_mod.api.notes_create()
    _bloat(db)
    assert backend_mod.reclaim_space()[0] is True
    assert backend_mod.reclaim_space()[0] is False, '第二次应跳过'
    assert backend_mod.check_integrity() is True


def test_reclaim_space_respects_custom_threshold(backend_mod, tmp_path):
    """阈值可调：把阈值放到 1.0 以上时永不触发"""
    db = str(tmp_path / 'notes.db')
    backend_mod.api.notes_create()
    _bloat(db)
    did, before_b, after_b = backend_mod.reclaim_space(threshold=1.01)
    assert did is False, '阈值 >1 时不可能触发'
    assert before_b == after_b
    # 用 0 阈值则必然触发
    assert backend_mod.reclaim_space(threshold=0.0)[0] is True


def test_reclaim_space_missing_db_is_safe(tmp_path):
    """库文件不存在时不得抛错"""
    import importlib
    import sys
    os.environ['MYNOTEPAD_DATA_DIR'] = str(tmp_path / 'empty')
    os.makedirs(str(tmp_path / 'empty'), exist_ok=True)
    sys.modules.pop('backend', None)
    b = importlib.import_module('backend')
    try:
        did, before_b, after_b = b.reclaim_space(db_path=str(tmp_path / 'nope.db'))
        assert did is False and before_b == 0 and after_b == 0
        assert b.space_stats(str(tmp_path / 'nope.db')) is None
    finally:
        try:
            b.conn.close()
        except Exception:
            pass
        sys.modules.pop('backend', None)
