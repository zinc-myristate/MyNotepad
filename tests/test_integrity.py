# -*- coding: utf-8 -*-
"""启动自检 + 死代码回归测试"""

import os
import sqlite3


def test_integrity_healthy(backend_mod):
    assert backend_mod.check_integrity(backend_mod.DB_PATH) is True


def test_integrity_missing_file_is_false(tmp_path):
    """库文件不存在 → False（不该"放行"）。

    ⚠️ 判据必须用 isfile：早先写的是 `os.path.exists`，而**目录**也满足 exists，
    于是传目录时 sqlite 抛 "unable to open database file"，再被"瞬时错误"判定放行，
    变成"传一个目录却返回 True"（实测踩到）。
    """
    import backend
    assert backend.check_integrity(str(tmp_path / 'nope.db')) is False
    assert backend.check_integrity(str(tmp_path)) is False, \
        '目录不是库文件，必须返回 False'


def test_integrity_locked_db_is_not_reported_as_corrupt(backend_mod):
    """**库被锁住 ≠ 库损坏**：这时必须放行（True），否则会误导用户去用备份覆盖好库。

    为什么这条重要：`app.pyw` 拿 False 就弹
    "数据库完整性检查失败…可尝试从 data/backups/ 恢复最近备份" 并退出。
    如果"另一个实例正持锁 / 杀软在扫 / 刚崩溃留下的锁"也被报成损坏，
    用户可能真拿旧备份把好库覆盖掉 —— 那才是真的数据丢失。
    所以取舍是**宁可漏报，不可误报**（漏报由后续读写报错兜底）。
    """
    import backend
    db = backend_mod.DB_PATH
    locker = sqlite3.connect(db, timeout=0.1)
    locker.execute('BEGIN EXCLUSIVE')          # 独占锁，模拟"暂时打不开"
    try:
        assert backend_mod.check_integrity() is True, \
            '被锁住的库不该被判成损坏（那会让用户以为要恢复备份）'
    finally:
        locker.rollback()
        locker.close()
    # 锁释放后仍然正常
    assert backend_mod.check_integrity() is True


def test_integrity_corrupt_after_retries_is_false(backend_mod):
    """真损坏必须返回 False（重试几次之后仍不行）。

    与上一条配对：瞬时错误放行、真损坏拦下 —— 两个方向都要有测试，
    否则把 `return True` 一写到底也能"全绿"。
    """
    import backend
    real = backend_mod.DB_PATH
    bad = real + '.corrupt'
    try:
        with open(bad, 'wb') as f:
            f.write(b'this is not a sqlite database at all' * 20)
        assert backend_mod.check_integrity(bad) is False
    finally:
        try:
            os.remove(bad)
        except OSError:
            pass


def test_integrity_corrupt(tmp_path):
    import backend
    bad = tmp_path / 'garbage.db'
    bad.write_bytes(b'this is not a sqlite database at all' * 10)
    assert backend.check_integrity(str(bad)) is False


def test_integrity_unopenable(tmp_path):
    # 注意：sqlite 对不存在的路径会新建空库（integrity 为 ok），测无法打开的情况（目录路径）
    import backend
    assert backend.check_integrity(str(tmp_path)) is False


def test_check_integrity_resolves_db_path_lazily(backend_mod):
    """`check_integrity()` 不传参时必须检查**当前**的库，而不是"导入那一刻"的库。

    踩过的坑（第 14 轮拆出 backup.py 时）：原本签名的默认值是 `db_path=DB_PATH`，
    而**默认值在函数定义时求值** —— 于是它被冻结成第一次导入的路径。拆包后
    `inspect.signature` 直接显示默认值是**上一个测试的 temp 路径**：
    `check_integrity(db_path='C:\\...\\pytest-12\\test_foo0\\notes.db')`。

    为什么单测当时全绿：一个测试进程里 `DB_PATH` 只在 import 时定一次，
    默认值恰好等于它，看起来完全正常。只有"import 之后路径会变"的场景才暴露。
    """
    import inspect

    from backend import backup

    # ① 静态判据：默认值必须是 None（惰性），不能是某个具体路径
    default = inspect.signature(backup.check_integrity).parameters['db_path'].default
    assert default is None, (
        'check_integrity 的 db_path 默认值被冻结成了 %r —— 默认值在定义时就求值，\n'
        '换过数据目录之后它会去检查**旧的**库。必须写成 db_path=None + 体内 `db_path or DB_PATH`。'
        % (default,))

    # ② 行为判据：改动 DB_PATH 之后，不传参的调用要跟着走
    import os
    import shutil

    original = backup.DB_PATH
    probe = original + '.probe'
    try:
        # 先放一份**完好**的副本：应判 True
        shutil.copyfile(original, probe)
        backup.DB_PATH = probe
        assert backend_mod.check_integrity() is True, \
            '换了 DB_PATH 之后 check_integrity() 应该检查新路径'

        # 再把它写坏：同一个调用必须变成 False —— 这才证明它看的是**当前** DB_PATH。
        # （不能拿"不存在的路径"当反例：sqlite 会新建空库，空库的 integrity 是 ok，
        #   那样即使 bug 还在也会通过 —— 这是我第一版写错的地方。）
        with open(probe, 'wb') as f:
            f.write(b'this is not a sqlite database at all' * 20)
        assert backend_mod.check_integrity() is False, \
            ('把当前 DB_PATH 指向的库写坏之后仍返回 True —— 说明它检查的不是当前路径，\n'
             '而是导入时冻结的那个（默认值求值时机的问题）。')
    finally:
        backup.DB_PATH = original
        try:
            os.remove(probe)
        except OSError:
            pass


def test_chem_struct_panel_removed():
    """死代码回归：化学结构式面板不得复活"""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, 'renderer', 'index.html'), encoding='utf-8').read()
    core = open(os.path.join(root, 'renderer', 'js', 'app', '01-core.js'), encoding='utf-8').read()
    assert 'chem-struct-panel' not in html
    assert 'chem-struct-panel' not in core
    assert 'smiles' not in html
