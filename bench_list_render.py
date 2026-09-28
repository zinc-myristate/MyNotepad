# -*- coding: utf-8 -*-
"""列表渲染基准：量清楚"前端建 DOM"到底多少毫秒，再决定要不要上虚拟滚动。

放在项目根而不是 tests/：它不是测试（没有断言），不该被 pytest 收集。
跑法：python bench_list_render.py [笔记篇数]

测量方式：`window.__app.loadNotes()` 是"取数据 + 建列表"的完整路径，而它内部那次
`notes_list()` 的耗时我可以在 Python 侧单独量（同一份数据、同一台机器），
两者相减就是**前端建 DOM** 的开销 —— 不需要把 renderNoteList 暴露成全局。
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tests'))

N = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
tmp = tempfile.mkdtemp(prefix='bench_list_')
os.environ['MYNOTEPAD_DATA_DIR'] = tmp
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

sys.modules.pop('backend', None)
from conftest import PROJECT_ROOT, load_app_partial  # noqa: E402

import backend  # noqa: E402

RENDERER = PROJECT_ROOT + '/renderer'


def seed(api):
    t0 = time.perf_counter()
    for i in range(N):
        nid = api.notes_create()['id']
        api.notes_update(nid, {'title': '第 %04d 篇笔记' % i,
                               'content': '# 标题\n\n这是第 %d 篇的正文内容，用来把列表填满。\n' % i})
    return time.perf_counter() - t0


class _MP:
    """极简 monkeypatch：load_app_partial 需要一个带 setenv 的对象。"""
    def setenv(self, k, v):
        os.environ[k] = v


seed(backend.api)

# 后端单项耗时（作为对照）
t = time.perf_counter()
rows = backend.api.notes_list(None)
backend_ms = (time.perf_counter() - t) * 1000

ns = load_app_partial(_MP(), tmp)
import webview  # noqa: E402

result = {}
window = webview.create_window('列表渲染基准', RENDERER + '/index.html',
                               js_api=ns['api'], width=1200, height=800)


def runner():
    try:
        time.sleep(8)
        result['rows'] = window.evaluate_js("window.__app.state.notes.length")
        result['nodes'] = window.evaluate_js(
            "document.getElementById('note-list').getElementsByTagName('*').length")
        # 完整路径：取数据 + 建列表。
        # ⚠️ pywebview 的 evaluate_js **不会 await Promise**（直接返回 `{}`），
        # 所以这里用"启动 + 轮询完成标志"，用挂钟时间量总耗时。
        window.evaluate_js("""
(() => {
  window.__benchDone = false;
  window.__benchT0 = performance.now();
  window.__app.loadNotes().then(() => { window.__benchDone = true; });
  return true;
})()
""")
        deadline = time.time() + 30
        inner = None
        while time.time() < deadline:
            if window.evaluate_js("!!window.__benchDone"):
                inner = window.evaluate_js("Math.round(performance.now() - window.__benchT0)")
                break
            time.sleep(0.05)
        result['full_ms'] = inner
        # 滚动 20 屏（同步改 scrollTop，量的是布局/绘制压力）
        result['scroll_ms'] = window.evaluate_js("""
(() => {
  const list = document.getElementById('note-list');
  const t0 = performance.now();
  for (let i = 0; i < 20; i++) list.scrollTop = i * 600;
  list.scrollTop = 0;
  return Math.round(performance.now() - t0);
})()
""")
        # 单行更新 100 次（置顶/收藏走的路径）
        result['single_ms'] = window.evaluate_js("""
(() => {
  const id = window.__app.state.notes[0].id;
  const item = document.querySelector('.note-item[data-note-id="' + id + '"]');
  if (!item) return -1;
  const timeEl = item.querySelector('.note-item-time');
  const t0 = performance.now();
  for (let i = 0; i < 100; i++) {
    item.classList.toggle('active', i % 2 === 0);
    if (timeEl) timeEl.textContent = '2026-09-28 12:00';
  }
  return Math.round(performance.now() - t0);
})()
""")
        # 整表重建（置顶/收藏/搜索过滤后走的就是它）：清空 + 重新渲染
        result['rebuild_ms'] = window.evaluate_js("""
(() => {
  const dom = window.__app.dom;
  const t0 = performance.now();
  dom.noteList.innerHTML = '';
  document.dispatchEvent(new CustomEvent('myapp:bench-rebuild'));
  return Math.round(performance.now() - t0);
})()
""")
    except Exception as e:
        result['error'] = repr(e)
    finally:
        try:
            window.destroy()
        except Exception:
            pass


threading.Thread(target=runner, daemon=True).start()
webview.start()

print('\n===== 列表渲染基准（%d 篇）=====' % N)
if 'error' in result:
    print('出错：', result['error'])
else:
    full = result.get('full_ms')
    print('后端 notes_list          : %.1f ms' % backend_ms)
    print('前端 loadNotes 全程      : %s ms' % full)
    if isinstance(full, (int, float)):
        print('  → 其中前端建 DOM 约    : %.1f ms' % (full - backend_ms))
    print('列表行数 / 列表内节点数  : %s / %s' % (result.get('rows'), result.get('nodes')))
    print('滚动 20 屏              : %s ms' % result.get('scroll_ms'))
    print('单行更新 100 次          : %s ms' % result.get('single_ms'))
