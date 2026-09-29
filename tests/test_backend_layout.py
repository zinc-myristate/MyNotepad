# -*- coding: utf-8 -*-
"""backend 包结构本身的守卫（第 14 轮：backend.py → backend/ 包）。

## 为什么需要这一组

拆包是纯结构调整，**行为应该一字不变** —— 而"应该"两个字正是这类重构最容易骗过人的地方：
672 项测试全绿也可能漏掉某条只在特定运行方式下才走到的路径。

这里钉的是**包结构特有的**三件事，都是"测试环境天然盖住、真机才暴露"的类型：

1. **`DATA_DIR` 必须落在仓库根，不是 `backend/` 里面**。
   `_EXE_DIR` 原来写的是 `dirname(abspath(__file__))`，拆包后 `__file__` 从
   `…/backend.py` 变成了 `…/backend/__init__.py`，于是源码运行时数据目录会变成
   `…/backend/data`（**已实测踩到**）。测试全都设了 `MYNOTEPAD_DATA_DIR`、打包版走
   `sys.executable`，两边都盖住了它 —— 只有"源码直接 `python app.pyw`"才暴露，
   而那恰好是用户最常用的方式。所以必须**在子进程里、不设环境变量**验。
2. **子模块能被独立导入**（`import backend.crypto` 而不先 `import backend`）。
   打包（PyInstaller）靠 `__init__.py` 的 import 语句收集子模块，漏一个就是运行时才炸。
3. **re-export 的名字和对象身份**。外部有 62 个名字依赖 `backend.xxx`；拆包后它们由
   门面转出来。名字少了、或者某个可变状态被复制成两份，都是静默故障。
"""
import json
import os
import subprocess
import sys

from conftest import PROJECT_ROOT

# 注意：这个文件里的断言要能在"包被拆得更多"之后继续成立，所以只依赖
# backend 的公开契约（DATA_DIR 的落点、子模块可导入、名字存在），不锁具体文件名。
UNSET = 'MYNOTEPAD_DATA_DIR'


def _run_probe(code):
    """在**干净环境**（不设 MYNOTEPAD_DATA_DIR）的子进程里跑一小段探测代码。

    子进程的 cwd 设成仓库根，这样 `import backend` 走的就是源码那套解析 ——
    和用户 `python app.pyw` 完全一致。返回解析出来的 JSON。
    """
    env = {k: v for k, v in os.environ.items() if k != UNSET}
    env['PYTHONIOENCODING'] = 'utf-8'
    r = subprocess.run([sys.executable, '-c', code], cwd=PROJECT_ROOT, env=env,
                       capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert r.returncode == 0, '子进程失败:\n%s\n%s' % (r.stdout, r.stderr)
    # 取最后一个 JSON 值（前面可能有别的输出）。⚠️ 别忘了 `[]` / `{}` 也是合法 JSON ——
    # 只认 `{` 开头会让"探测结果为空"这一类**通过**的情况反而报错（第一版就踩了：
    # "门面没漏名字"返回 `[]`，解析器却找不到 JSON）。
    for line in reversed(r.stdout.strip().splitlines()):
        line = line.strip()
        if line[:1] in ('{', '['):
            return json.loads(line)
    raise AssertionError('子进程没输出 JSON:\n%s' % r.stdout)


def test_data_dir_is_the_repo_root_not_the_package_dir():
    """源码运行时数据目录必须是仓库根的 `data/`（打包版另走 sys.executable）。"""
    info = _run_probe(
        'import json, os, sys\n'
        'import backend\n'
        'print(json.dumps({\n'
        '    "data_dir": backend.DATA_DIR,\n'
        '    "db_path": backend.DB_PATH,\n'
        '    "package_dir": os.path.dirname(os.path.abspath(backend.__file__)),\n'
        '    "frozen": bool(getattr(sys, "frozen", False)),\n'
        '}))\n')
    assert info['frozen'] is False, '这个探测必须在源码模式下跑'
    pkg_dir = os.path.normcase(info['package_dir'])
    data_dir = os.path.normcase(os.path.normpath(info['data_dir']))
    expected = os.path.normcase(os.path.join(PROJECT_ROOT, 'data'))
    assert data_dir == expected, (
        'DATA_DIR 落错地方了。\n  实际: %s\n  期望: %s\n'
        '拆包时 `__file__` 多了一层目录，取 `_EXE_DIR` 要往上退一层（见 backend/__init__.py 的注释）。'
        % (info['data_dir'], os.path.join(PROJECT_ROOT, 'data')))
    assert not data_dir.startswith(pkg_dir + os.sep), \
        'DATA_DIR 落在 backend/ 包里面了：%s' % info['data_dir']
    assert os.path.normcase(os.path.normpath(info['db_path'])).startswith(expected), \
        'DB_PATH 也应该在 DATA_DIR 下：%s' % info['db_path']


def test_submodules_are_importable_on_their_own():
    """`import backend.X` 直接可用（打包收集与隔离测试都依赖这一点）。"""
    # 这个清单随拆包增长。**加新模块时记得登记** —— 漏登记不会报错，
    # 但"打包漏收子模块"这类事故就没人挡了（PyInstaller 靠 __init__ 的 import 收集，
    # 而那与"能独立 import"是两回事）。
    mods = ['paths', 'crypto', 'text']
    code = ('import json\n'
            'import importlib\n'
            'out = {}\n'
            'for m in %r:\n'
            '    mod = importlib.import_module("backend." + m)\n'
            '    out[m] = getattr(mod, "__file__", None) is not None\n'
            'print(json.dumps(out))\n' % mods)
    r = _run_probe(code)
    for m in mods:
        assert r.get(m) is True, 'backend.%s 不能独立导入（打包会漏掉它）' % m


def test_facade_reexports_the_names_external_code_uses():
    """门面必须转出外部依赖的模块级名字 —— 少一个就是 ImportError/AttributeError。"""
    # 这些名字来自"扫 app.pyw + tests/ + bench_*.py 得到的外部引用"（62 个里挑出的代表）
    names = [
        'api', 'conn', 'DB_PATH', 'DATA_DIR', 'ATTACH_DIR', 'BACKUP_DIR',
        'DERIVED_VERSION', 'ENC_PREFIX', 'FTS_AVAILABLE', 'SQL_CHUNK',
        'PBKDF2_ITERATIONS', 'MAX_PBKDF2_ITERATIONS', 'PREVIEW_MAX',
        '_db_lock', '_ctx_conn', '_ctx_kind', '_readonly_conn', '_in_write_path',
        '_mark_write_owner', '_clear_write_owner', 'close_readonly_conns',
        'checkpoint_and_close', 'backup_database', 'check_integrity', 'count_notes',
        'reclaim_space', 'space_stats', 'purge_expired_trash', 'migrate_images',
        'safe_join', '_is_under', '_safe_path_part',
        '_unlocked_deks', '_UnlockedDeks', '_wrap_dek', '_unwrap_dek',
        '_encrypt_content', '_decrypt_content',
        '_refresh_derived', '_preview_text', '_purge_encrypted_derived_leaks',
        '_fts_sync_from_row', '_delta_to_markdown', '_delta_to_text',
        'markdown_to_delta', '_markdown_to_text', 'note_plain_text',
        'parse_front_matter', 'count_words', 'extract_links', '_externalize_image',
        '_next_sort_order', '_parse_search_scope', 'WORD_RE',
    ]
    code = ('import json\n'
            'import backend\n'
            'names = %r\n'
            'print(json.dumps([n for n in names if not hasattr(backend, n)]))\n' % names)
    missing = _run_probe(code)
    assert missing == [], '门面漏了这些名字（外部引用会断）：%r' % missing


def test_mutable_unlock_state_is_not_duplicated(backend_mod):
    """解锁缓存只能有一份 —— 门面转出来的和子模块里的必须是同一个对象。

    这一条挡的是最阴的一类拆包事故：`from .crypto import unlocked_deks` 之后又
    `from .crypto import *`，或者某处重新绑定，于是"库里的解锁集合"和"crypto 看到的"
    变成两份 —— **不报错**，只在某个时刻表现为"明明解锁了却说没解锁"。
    """
    import backend.crypto as c
    assert backend_mod._unlocked_deks is c.unlocked_deks, \
        '解锁缓存出现了两份：门面 %r vs crypto 模块 %r' % (
            backend_mod._unlocked_deks, c.unlocked_deks)

    # 写进去要能从两边都看见（证明是同一个 dict，而不是"当前恰好相等"）
    backend_mod._unlocked_deks['__probe__'] = b'x' * 32
    try:
        assert '__probe__' in c.unlocked_deks
        assert c.unlocked_deks['__probe__'] == b'x' * 32
    finally:
        backend_mod._unlocked_deks.pop('__probe__', None)
    assert '__probe__' not in c.unlocked_deks
