# -*- coding: utf-8 -*-
"""只读方法清单的守卫（纯静态 + 一个运行时行为检查）。

## 这一组测试是为什么来的

为了让"读不再和打字抢同一把锁"，`Api` 的 46 个只读方法被挂上 `_locked_readonly`，
它们跑在**独立连接**上（`_readonly_conn()`，用 SQLite 的 `mode=ro` 打开）。
这条路上有两个方向都可能出事，而且是**两个方向的静默失败**：

1. **写方法被错标成只读** ⇒ `mode=ro` 会拒绝写入（`attempt to write a readonly database`），
   功能当场坏掉。第一版把 `note_metrics` 标成只读就踩了这个：它体内调 `_refresh_derived`，
   而后者有 `INSERT OR REPLACE`。
2. **只读方法里的写入被静默丢弃** ⇒ 如果拿掉 `mode=ro` 这层硬保证，错标的写入会
   既不报错也不生效（独立连接不提交）= 数据丢失。

所以这里用**传递闭包**（含私有辅助函数）判定，而不是只看方法体自己：
   只读方法 → 它调的每个函数 → 再往下 → 任何一处出现写 SQL 就算失败。
`metrics_bulk` / `notes_table` 这类会调到 `_chunked`、`_load_props` 的方法正是靠
传递判定才安全的（那些辅助函数只读）。

另一个方向：**哪些方法"看起来是写"但其实不碰数据库**（如 `export_table_csv` 只是拼 CSV），
留在只读清单里没问题 —— 这条不额外断言，因为"更保守"不会坏事。
"""
import ast
import os
import re
import sys
import threading
import time

import pytest
from conftest import PROJECT_ROOT

BACKEND = os.path.join(PROJECT_ROOT, 'backend.py')

# 写 SQL 的判据：**必须在 `.execute(...)` / `.executescript(...)` 的实参里**，
# 而不是"字符串里出现 INSERT 这几个字"。
#
# 为什么换判据：Delta 的 `{"ops":[{"insert": "..."}]}` 里那个 `insert` 是 Quill 的操作名，
# 不是 SQL；它在源码里换行排版后经常正好落在行首，于是"按行首动词匹配"会误判 ——
# 实测 `notes_list` / `notes_search` / `note_links` / `export_note_markdown` /
# `saved_searches_list` / `notebook_id_by_name` 六个只读方法全被误报。
# 而"有写 SQL"在代码里必然表现为一次 execute 调用，这个判据不会漏也不会误。
WRITE_VERB = re.compile(r'^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP|VACUUM|REINDEX)\b',
                        re.I | re.M)
EXECUTORS = ('execute', 'executescript', 'executemany')
TXN_SQL = re.compile(r'\b(commit|rollback)\s*\(', re.I)


def _parse():
    src = open(BACKEND, encoding='utf-8').read()
    tree = ast.parse(src)
    api = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Api')
    return src, tree, api


def _sql_strings(node):
    """函数体里**作为 execute 实参**的字符串，排除 docstring。

    判据是"这次调用真的在发 SQL"，而不是"字符串里出现了 INSERT 这几个字"（见文件头
    关于 Delta `insert` 的说明）。
    """
    doc_node = None
    if (node.body and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)):
        doc_node = node.body[0]

    out = []
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else '')
        if name not in EXECUTORS:
            continue
        for arg in list(n.args) + [k.value for k in n.keywords]:
            # 直接量、以及 f-string / 拼接里的每一段，都算
            for sub in ast.walk(arg):
                if (isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                        and sub is not doc_node):
                    out.append(sub.value)
    return out


def _calls(node, known):
    """函数体里对 `known` 集合里名字的调用（self.x(...) 或裸 x(...)）。"""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Attribute) and f.attr in known:
                out.add(f.attr)
            elif isinstance(f, ast.Name) and f.id in known:
                out.add(f.id)
    return out


def _all_functions(tree):
    """{名字: 节点}，含 Api 的私有方法与模块级函数（公开方法另存）。"""
    mod = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            mod.setdefault(n.name, n)
    return mod


def _writes_transitively(name, funcs, cache):
    """`name` 及其调用链上有没有写 SQL / 事务控制（带缓存，防环）。"""
    if name in cache:
        return cache[name]
    node = funcs.get(name)
    if node is None:
        cache[name] = False
        return False
    cache[name] = False          # 先占位，断开环
    lits = _sql_strings(node)
    if any(WRITE_VERB.search(s) for s in lits):
        cache[name] = True
        return True
    # 事务控制也说明它在写路径上（有 commit/rollback 就必然有东西要提交）
    raw = '\n'.join(n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str))
    if TXN_SQL.search(raw):
        cache[name] = True
        return True
    for callee in _calls(node, set(funcs)):
        if callee != name and _writes_transitively(callee, funcs, cache):
            cache[name] = True
            return True
    return False


class TestReadonlyMethodClassification:
    """静态：只读清单里的每个方法，其调用链上都不许有写。"""

    def test_readonly_methods_have_no_write_in_their_call_graph(self):
        src, tree, api = _parse()
        funcs = _all_functions(tree)
        names = backend_readonly_names()
        assert names, '读不到 _READONLY_METHODS（下面的解析逻辑过时了？）'

        offenders = []
        for name in sorted(names):
            if name not in funcs:
                continue
            cache = {}
            if _writes_transitively(name, funcs, cache):
                offenders.append(name)
        assert offenders == [], (
            '这些方法被标成只读，但调用链里有写 SQL —— 放到 mode=ro 的独立连接上会报\n'
            '“attempt to write a readonly database”。请把它们从 _READONLY_METHODS 里移出：\n  %s'
            % offenders)

    def test_readonly_names_all_exist_on_api(self):
        """清单里写了、但 Api 上不存在的名字 = 拼写错误（会静默退化成走写锁）。"""
        src, tree, api = _parse()
        actual = {n.name for n in api.body
                  if isinstance(n, ast.FunctionDef) and not n.name.startswith('_')}
        missing = sorted(backend_readonly_names() - actual)
        assert missing == [], '只读清单里有 Api 上不存在的名字：%r' % missing

    def test_note_metrics_is_not_readonly(self):
        """`note_metrics` 是写路径（惰性回填派生行）—— 单独钉一条，防止有人"顺手"加回去。

        它踩过一次：分类时只看了方法体，没看它调的 `_refresh_derived` 里有 INSERT OR REPLACE。
        """
        assert 'note_metrics' not in backend_readonly_names()


def backend_readonly_names():
    """从 backend.py 源码里取出 `_READONLY_METHODS` 的成员（不 import，避免副作用）。"""
    src = open(BACKEND, encoding='utf-8').read()
    m = re.search(r'_READONLY_METHODS\s*=\s*frozenset\(\((.*?)\)\)', src, re.S)
    assert m, '没找到 _READONLY_METHODS 的定义'
    return set(re.findall(r"'([A-Za-z_][A-Za-z0-9_]*)'", m.group(1)))


class TestReadonlyConnectionRuntime:
    """运行时行为：只读连接真的独立、真的只读、且可见性正确。"""

    def test_readonly_conn_is_independent_and_rejects_writes(self, backend_mod):
        ro = backend_mod._readonly_conn()
        assert ro is not backend_mod.conn, '只读连接必须是另一个连接'
        import sqlite3
        with pytest.raises(sqlite3.OperationalError) as ei:
            ro.execute("INSERT INTO notes (id, title) VALUES ('probe', 'x')")
        assert 'readonly' in str(ei.value).lower()
        backend_mod.close_readonly_conns()

    def test_owner_table_tracks_write_context(self, backend_mod):
        """`_ctx_kind()` 在锁外是 read、登记归属后是 write。

        这一条挡的是最阴的错法：上下文判反了会让**写路径内部**的读方法改用独立连接，
        于是看不到同事务里未提交的数据（例如刚插入的附件行）。
        第一版就是用 `RLock.acquire(blocking=False)` 判定的 —— 它在**自己已持锁**时
        返回 False，于是判定永远反着来。
        """
        assert backend_mod._ctx_kind() == 'read'
        backend_mod._mark_write_owner()
        try:
            assert backend_mod._in_write_path() is True
            assert backend_mod._ctx_kind() == 'write'
            assert backend_mod._ctx_conn() is backend_mod.conn, '写路径里必须用写连接'
        finally:
            backend_mod._clear_write_owner()
        assert backend_mod._ctx_kind() == 'read'
        assert backend_mod._ctx_conn() is not backend_mod.conn

    def test_readonly_call_does_not_wait_for_the_write_lock(self, backend_mod):
        """只读方法不该被写锁挡住 —— 这是整件事的目的。"""
        api = backend_mod.api
        api.notes_create()
        held = threading.Event()
        release = threading.Event()

        def hold_write_lock():
            with backend_mod._db_lock:
                backend_mod._mark_write_owner()
                try:
                    held.set()
                    release.wait(timeout=5)
                finally:
                    backend_mod._clear_write_owner()

        t = threading.Thread(target=hold_write_lock)
        t.start()
        try:
            assert held.wait(timeout=5), '写线程没能拿到锁'
            t0 = time.perf_counter()
            api.notes_list()          # 只读：不该排队
            elapsed = time.perf_counter() - t0
            assert elapsed < 0.3, '只读方法被写锁挡住了（耗时 %.3fs）' % elapsed
        finally:
            release.set()
            t.join(timeout=5)

    def test_nested_read_inside_write_sees_uncommitted_rows(self, backend_mod):
        """写路径内部调只读方法时，必须能看到**未提交**的行。

        这是 `_ctx_conn()` 存在的唯一理由。用 `_externalize_image`（会 INSERT 一行附件）
        造出"未提交"状态，再在写锁里调 `attachments_list` —— 用写连接才看得见。
        """
        api = backend_mod.api
        nid = api.notes_create()['id']
        png = ('data:image/png;base64,'
               'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/'
               'q842iQAAAABJRU5ErkJggg==')
        backend_mod._externalize_image(nid, png)

        # 锁外（只读连接）也应该看得见：_externalize_image 自己提交了
        assert len(api.attachments_list(nid)) == 1, \
            '_externalize_image 应当自己提交，否则独立只读连接看不到这一行'

        # 再验"未提交"那条路：手工插一行不提交，然后在写锁里读
        with backend_mod._db_lock:
            backend_mod._mark_write_owner()
            try:
                backend_mod.conn.execute(
                    "INSERT INTO attachments (id, note_id, filename, original_name, file_size,"
                    " mime_type, type) VALUES ('probe-aid', ?, 'probe.png', 'probe.png', 1,"
                    " 'image/png', 'image')", (nid,))
                # 写路径里读：应看到 2 行（含刚插入未提交的那行）
                assert len(api.attachments_list(nid)) == 2, \
                    '写路径里的读必须用写连接，才看得到未提交的数据'
            finally:
                backend_mod.conn.rollback()
                backend_mod._clear_write_owner()
        backend_mod.close_readonly_conns()
