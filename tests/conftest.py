# -*- coding: utf-8 -*-
"""pytest 基建：临时数据目录隔离的 backend fixture + app.pyw 局部加载 helper"""
import importlib
import os
import sys
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

APP_PYW = os.path.join(PROJECT_ROOT, 'app.pyw')
APP_SPLIT_MARKER = '# ====== 创建窗口 ======'


def parse_e2e_shard(spec):
    """E2E_SHARD 的值：'k/n'（第 k 份，共 n 份，k 从 1 起）→ (k, n)；空 → None；写错了抛 UsageError"""
    spec = (spec or '').strip()
    if not spec:
        return None
    k, sep, n = spec.partition('/')
    if not (sep and k.isdigit() and n.isdigit() and 1 <= int(k) <= int(n)):
        raise pytest.UsageError('E2E_SHARD 要写成「第几份/共几份」，比如 1/3，现在是 %r' % spec)
    return int(k), int(n)


def split_e2e_shard(items, shard):
    """按收集顺序把 E2E 用例轮流分成 n 份，返回 (留下的, 去掉的)；不是 E2E 的用例都留下。

    轮流分而不是按文件切：同一个文件里的用例耗时差不多，轮流分下来每份的耗时比较平均
    """
    k, n = shard
    keep, drop = [], []
    index = 0
    for item in items:
        if item.get_closest_marker('e2e') is None:
            keep.append(item)
            continue
        (keep if index % n == k - 1 else drop).append(item)
        index += 1
    return keep, drop


def pytest_collection_modifyitems(config, items):
    """CI 把 E2E 拆成几个并行任务（ci.yml 的 matrix），每个任务设 E2E_SHARD=k/n，只跑自己那一份。

    不拆的时候 E2E 一个任务要 22 分钟，整轮 CI 28 分钟。每个任务的收集顺序一样，各算各的也分得一致
    """
    shard = parse_e2e_shard(os.environ.get('E2E_SHARD'))
    if shard is None:
        return
    keep, drop = split_e2e_shard(items, shard)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


@pytest.fixture(autouse=True)
def launched(monkeypatch):
    """跑测试时绝不真的把东西交给系统打开：浏览器、邮件客户端、附件的默认程序都算。

    `os.startfile` 是这些路的共同出口（backend.file_open、desktop.open_external_url，还有
    pywebview 自己处理新窗口请求时走的 webbrowser.open）。这里换成只记账，对所有用例生效；
    要看交出去了什么的用例把这个 fixture 写进参数，拿到的就是那份记录。
    """
    calls = []
    monkeypatch.setattr(os, 'startfile', lambda target, *args, **kwargs: calls.append(target),
                        raising=False)
    return calls


def _purge_backend_modules():
    """把 `backend` 与**它的全部子模块**从 sys.modules 里清掉。

    ⚠️ 只 pop `backend` 是不够的（第 14 轮拆包时发现）：子模块是**独立**的 sys.modules
    条目，不会被父包的 pop 带走。对不读环境变量的子模块（paths / crypto / text）无所谓，
    但 `backend.db` 在**导入时**就按 `MYNOTEPAD_DATA_DIR` 定下 `DB_PATH` ——
    残留的话第二个测试会拿到**上一个测试的临时目录**，表现为随机的"串目录"失败，
    而且报错点离原因很远。所以这里按前缀清干净。
    """
    for name in [m for m in list(sys.modules) if m == 'backend' or m.startswith('backend.')]:
        sys.modules.pop(name, None)


@pytest.fixture
def backend_mod(tmp_path, monkeypatch):
    """全新导入 backend 模块，数据目录指向 pytest 临时目录（绝不碰真实数据）。

    不能用 importlib.reload：模块级 conn 和 _locked 二次包装会出问题，必须 pop 后重新 import。
    """
    monkeypatch.setenv('MYNOTEPAD_DATA_DIR', str(tmp_path))
    _purge_backend_modules()
    backend = importlib.import_module('backend')
    assert backend.DATA_DIR == str(tmp_path), '数据目录未被环境变量覆盖，拒绝继续（防止误碰真实数据）'
    yield backend
    try:
        backend.conn.close()  # Windows 文件锁：不关掉 tmp_path 清不掉
    except Exception:
        pass
    _purge_backend_modules()


@pytest.fixture
def api(backend_mod):
    return backend_mod.api


def make_delta_note(backend, title='', content='', notebook_id=None):
    """建一篇**显式 delta 格式**的笔记。

    第 6 轮起 notes_create 默认 'md'（Markdown 文本），凡是测试 Quill 行为
    （工具栏、Delta 行内格式、嵌图、贴纸…）的用例都必须显式声明 format='delta'，
    否则内容会被 Markdown 编辑器接管，断言看到的是空编辑器。
    """
    note = backend.api.notes_create()
    nid = note['id']
    backend.conn.execute("UPDATE notes SET format = 'delta' WHERE id = ?", (nid,))
    backend.conn.commit()
    fields = {'title': title, 'content': content}
    if notebook_id:
        fields['notebook_id'] = notebook_id
    backend.api.notes_update(nid, fields)
    return nid


def load_app_partial(monkeypatch, tmp_path):
    """加载 app.pyw 的「创建窗口之前」部分（含图像处理函数与 AppApi/api），不启动 GUI。

    ⚠️ 这一段里含 `Thread(target=_startup_maintenance, daemon=True).start()`
    （回收站超期清理 + 备份 + 空间回收）。测试里**必须让它只构造、不启动**：
    那条后台线程会拿 backend 的模块级 sqlite 连接（`check_same_thread=False`）直接读写，
    而 fixture 同时在换临时库、关连接 —— 实测在 CI 上触发原生崩溃
    （`Windows fatal exception: access violation`，栈顶是 `backend.purge_expired_trash`）。
    进程被 abort 后 pytest 缓冲的失败详情全部丢失，只剩一串进度点，排查成本极高。

    ⚠️ 拦的是 **`Thread.start` 方法**，不是 `threading.Thread` 这个类本身。`desktop.py` 里
    `class GlobalHotkey(threading.Thread)` / `class ReminderWatcher(threading.Thread)`
    是 **import 时**求值基类的，而这个 import 正落在下面 exec 的窗口内：早先"换掉整个
    Thread 类"的写法会让它们永久继承那个假基类 —— `GlobalHotkey.start()` 变成空操作，
    `join()` 抛 `cannot join thread before it is started`（全量跑时 desktop 已被导入过，
    掩盖了它；单跑 `test_window_persistence.py` 才暴露）。
    """
    monkeypatch.setenv('MYNOTEPAD_DATA_DIR', str(tmp_path))
    _purge_backend_modules()
    src = open(APP_PYW, encoding='utf-8').read()
    parts = src.split(APP_SPLIT_MARKER)
    assert len(parts) == 2, 'app.pyw 缺少「创建窗口」分节标记，conftest 需要同步更新'
    ns = {'__name__': 'app_partial', '__file__': APP_PYW}

    import threading as _threading

    real_start = _threading.Thread.start

    def _start_unless_maintenance(self):
        """放行其它线程，只让「启动维护线程」这一次 start() 变成空操作"""
        if getattr(getattr(self, '_target', None), '__name__', '') == '_startup_maintenance':
            return None
        return real_start(self)

    try:
        _threading.Thread.start = _start_unless_maintenance
        exec(compile(parts[0], 'app.pyw', 'exec'), ns)
    finally:
        _threading.Thread.start = real_start
    return ns


def load_capture_session(ns, window):
    """把 app.pyw 的截图会话那一段加载进 `load_app_partial` 的命名空间，`window` 是主窗。

    这一段在「创建窗口」标记之后，`load_app_partial` 不执行；里面都是定义，单独 exec 没有副作用。
    按标题找窗口的两个函数要换掉：隔离数据目录下没有单实例互斥，开着别的实例时 `_window_visible`
    会问到别的进程，`_on_loaded` 会把人家的窗口拉到前台。
    """
    src = open(APP_PYW, encoding='utf-8').read()
    section = src[src.index('def _show_window'):src.index('def _mini_open')]
    exec(compile(section, 'app.pyw', 'exec'), ns)
    ns.update({'window': window, '_window_visible': lambda: True, '_on_loaded': lambda: None})


@pytest.fixture
def app_ns(tmp_path, monkeypatch):
    ns = load_app_partial(monkeypatch, tmp_path)
    yield ns
    try:
        import backend as _b
        _b.conn.close()
    except Exception:
        pass
    _purge_backend_modules()


class WaitTimeout:
    """`wait_for_js` 等一个具体值、到点还没等到时的返回值。

    - **假值**：`assert wait_for_js(window, "….style.display", 'flex')` 超时就失败。以前超时返回最后取到的
      `'none'`，也是真值，这条断言写了等于没写（2026-11-18 撤掉「外观」菜单的绑定做变异检查时抓到）。
    - `==` 按最后取到的值比：`wait_for_js(…) == 'flex'`、先存进结果字典再整个比，这些写法不受影响。
    - repr 带上要等的值、最后取到的值和表达式，断言失败时一眼看出差在哪。
    - 它不是数据：要对结果做 `json.loads`、切片、拼字符串的，先判断（`if not v`）或者先断言它。
    """

    __slots__ = ('value', 'expected', 'expression', 'timeout')

    def __init__(self, value, expected, expression, timeout):
        self.value = value
        self.expected = expected
        self.expression = expression
        self.timeout = timeout

    def __bool__(self):
        return False

    def __eq__(self, other):
        if isinstance(other, WaitTimeout):
            other = other.value
        return self.value == other

    __hash__ = None

    def __repr__(self):
        expression = ' '.join(str(self.expression).split())
        if len(expression) > 160:
            expression = expression[:157] + '...'
        return '<wait_for_js 超时：等了 %g 秒没等到 %r，最后取到 %r｜%s>' % (
            self.timeout, self.expected, self.value, expression)


def wait_for_js(window, expression, expected=None, timeout=20.0, interval=0.25):
    """轮询 JS 表达式，直到等于 expected（expected=None 时直到取到真值）或超时。

    等到了返回取到的值本身。超时不抛异常，让调用方的 assert 给出原本的报错信息：
    - expected=None：返回最后一次取到的值。它本来就是假值，`assert wait_for_js(…)` 照样失败。
    - 给了 expected：返回 `WaitTimeout`（假值，`==` 按最后取到的值比，repr 写明等的是什么、取到的是什么）。
      所以 `assert wait_for_js(…, 'flex')` 和 `assert wait_for_js(…, 'flex') == 'flex'` 都对。
      例外：expected 本身是假值（`''`、`0`、`False`）时，等到了返回的也是假值，只能写 `== 值`。

    ⚠️ 为什么必须有它：无头 WebView2 在 CI 上比开发机慢得多，而 e2e 里大量用
    "sleep 固定秒数 + 一次性取值"。那等于把"断言行为"变成"断言机器有多快"——实测
    `test_format_badge_e2e.py::test_badge_converts_to_markdown_then_restores` 就在 CI 上
    偶发读到还没刷新完的 'MD'（本地怎么跑都过、上一轮 CI 也是绿的）。
    等条件成立再断言才是稳的写法；**不要**用加长 sleep 来"修"这类问题。
    """
    deadline = time.time() + timeout
    while True:
        value = window.evaluate_js(expression)
        if expected is None:
            if value:
                return value
        elif value == expected:
            return value
        if time.time() >= deadline:
            if expected is None:
                return value
            return WaitTimeout(value, expected, expression, timeout)
        time.sleep(interval)


def wait_until(predicate, timeout=20.0, interval=0.05):
    """`wait_for_js` 的 Python 版：条件在后端或假桥接上时用它。

    只有「等到真值」这一种：超时返回最后取到的值，它本来就是假值，`assert wait_until(…)` 会失败。
    要等具体值，让 predicate 自己比（`wait_until(lambda: f() == 值)`）。
    """
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value or time.time() >= deadline:
            return value
        time.sleep(interval)


BOOT_STATE = ("window.__bootError ? 'error: ' + window.__bootError"
              " : ((window.__bootTrace || []).indexOf('ready') >= 0 ? 'ready' : '')")


def wait_app_ready(window, timeout=60.0):
    """等主界面启动完：09-boot.js 最后在 `window.__bootTrace` 里记的 'ready'
    （笔记列表、第一篇笔记、侧栏的视图 / 标签 / 笔记本、启动提示都好了）。

    以前各个 E2E 文件开窗后固定 sleep 6–8 秒：本机多数时候白等五六秒，全量 E2E 一大半时间花在这儿；
    机器忙的时候又不够（2026-11-22 分份跑时有一条就这么红过）。启动出错时马上把 __bootError 报出来，不等满超时
    """
    state = wait_for_js(window, BOOT_STATE, timeout=timeout)
    if state != 'ready':
        raise AssertionError('主界面 %s 秒内没有启动完：%r' % (timeout, state))
