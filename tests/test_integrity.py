# -*- coding: utf-8 -*-
"""启动自检 + 死代码回归测试"""


def test_integrity_healthy(backend_mod):
    assert backend_mod.check_integrity(backend_mod.DB_PATH) is True


def test_integrity_corrupt(tmp_path):
    import backend
    bad = tmp_path / 'garbage.db'
    bad.write_bytes(b'this is not a sqlite database at all' * 10)
    assert backend.check_integrity(str(bad)) is False


def test_integrity_unopenable(tmp_path):
    # 注意：sqlite 对不存在的路径会新建空库（integrity 为 ok），测无法打开的情况（目录路径）
    import backend
    assert backend.check_integrity(str(tmp_path)) is False


def test_chem_struct_panel_removed():
    """死代码回归：化学结构式面板不得复活"""
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, 'renderer', 'index.html'), encoding='utf-8').read()
    core = open(os.path.join(root, 'renderer', 'js', 'app', '01-core.js'), encoding='utf-8').read()
    assert 'chem-struct-panel' not in html
    assert 'chem-struct-panel' not in core
    assert 'smiles' not in html
