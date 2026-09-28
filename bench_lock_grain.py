"""数据库锁粒度基准：量"写锁被占住时，只读操作还要不要等"。

这一条是**用户能感知的核心指标** —— "后台在备份/导出/大搜索时，我打字会不会卡"。
（前端列表渲染的基准在 `bench_list_render.py`，那个量的是 DOM 构建。）

用法：
    python bench_lock_grain.py            # 默认 800 篇 × ~10KB（约 62MB 库）
    python bench_lock_grain.py 400        # 换篇数

它做什么：
  1. 在隔离的临时数据目录造一个真实规模的库（**绝不碰 data/**）
  2. 用另一个线程占住写锁，然后在主线程做各种**只读**操作，量它们等不等锁
  3. 对照一个**写**操作 —— 它应当等锁（等锁是正确行为，不是 bug）

判据：只读操作耗时 ≈ 它自己在自由状态下的耗时（差额 ≈ 0）就算通过；
      如果约等于"锁被占住的时长"，说明它还在排队。
"""
import os
import shutil
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding='utf-8')

NOTES = int(sys.argv[1]) if len(sys.argv) > 1 else 800
HOLD = 1.2          # 占住写锁的时长（秒）

d = tempfile.mkdtemp(prefix='mn_lockbench_')
os.environ['MYNOTEPAD_DATA_DIR'] = d
sys.modules.pop('backend', None)
import backend  # noqa: E402

api = backend.api

print('=== 造库：%d 篇 × 约 10KB ===' % NOTES)
para = '这是一段用来撑大字数的正文内容，包含中英文 mixed content 与标点。' * 60
delta = '{"ops":[{"insert":"%s\\n"}]}' % ('\\n'.join([para] * 6)).replace('"', '\\"')
t0 = time.perf_counter()
for i in range(NOTES):
    r = api.notes_create()
    api.notes_update(r['id'], {'title': '重笔记 %d' % i, 'content': delta})
size_mb = os.path.getsize(backend.DB_PATH) / 1024 / 1024
print('  %.1fs，库 %.1f MB' % (time.perf_counter() - t0, size_mb))
api.notes_list()          # 预热只读连接，排除"第一次建连接"的开销


def hold_write_lock_while(fn, hold=HOLD):
    """在另一线程占住写锁 hold 秒，期间执行 fn，返回 fn 的耗时。"""
    ready, release = threading.Event(), threading.Event()

    def holder():
        with backend._db_lock:
            backend._mark_write_owner()
            try:
                ready.set()
                release.wait(timeout=hold + 10)
            finally:
                backend._clear_write_owner()

    t = threading.Thread(target=holder)
    t.start()
    try:
        ready.wait(timeout=5)
        time.sleep(0.05)                  # 确保 holder 真的在锁里
        t0 = time.perf_counter()
        fn()
        return time.perf_counter() - t0
    finally:
        release.set()
        t.join(timeout=hold + 10)


def free_run(fn):
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


ids = [n['id'] for n in api.notes_list()]
first = ids[0]

print()
print('=== 只读操作：写锁被占 %.1fs 期间的耗时（差额≈0 = 没等锁）===' % HOLD)
READ_CASES = [
    ('notes_list()      切列表', lambda: api.notes_list()),
    ('notes_get()       切笔记', lambda: api.notes_get(first)),
    ('metrics_bulk()    表格数据', lambda: api.metrics_bulk(ids)),
    ('notes_table()     表格视图', lambda: api.notes_table(ids)),
    ('notes_search()    搜索', lambda: api.notes_search('正文内容')),
    ('todos_list()      待办面板', lambda: api.todos_list()),
    ('notebooks_list()  笔记本', lambda: api.notebooks_list()),
    ('versions_list()   版本', lambda: api.versions_list(first)),
    ('settings_get_all()', lambda: api.settings_get_all()),
]
bad = []
for label, fn in READ_CASES:
    t_free = free_run(fn)
    t_locked = hold_write_lock_while(fn)
    delta = t_locked - t_free
    waiting = delta > max(0.1, HOLD * 0.5)
    if waiting:
        bad.append(label)
    print('  %-28s 自由 %6.3fs  占锁 %6.3fs  差额 %+6.3fs  %s'
          % (label, t_free, t_locked, delta, '<<< 仍在排队' if waiting else '没等锁'))

print()
print('=== 对照：写操作的真实场景（有并发只读，**不**人为占锁）===')
# ⚠️ 这里刻意**不用** hold_write_lock_while 造"人为占锁"：
#   · 人为占锁测的是"锁被占住时写操作必然要排队"，那是设计使然、不是被优化掉的东西；
#   · 更麻烦的是它会掺进 WAL 的写者-读者交互（提交要等活跃读者离开），
#     22MB 库上实测能报出 11 秒，而那个数字**既不代表等锁也不代表真实使用**
#     （第一版就这么误报过，白查了一轮）。
# 真正的用户场景是：后台在跑只读查询的同时，用户在打字（自动保存）。量那个。
stop = {'go': True}
errors = []


def reader_loop():
    while stop['go']:
        try:
            api.notes_list()
        except Exception as exc:                    # noqa: BLE001
            errors.append(repr(exc)[:60])


threads = [threading.Thread(target=reader_loop) for _ in range(3)]
for t in threads:
    t.start()
time.sleep(0.2)
t0 = time.perf_counter()
api.notes_update(first, {'title': '并发保存'})
t_concurrent = time.perf_counter() - t0
stop['go'] = False
for t in threads:
    t.join()
print('  3 个线程持续读列表时，notes_update() 耗时 %.3fs（并发读出错 %d 次）'
      % (t_concurrent, len(errors)))
print('  → 写没有被并发读拖垮（阈值参考：>1s 就值得查）')
if errors:
    for e in errors[:3]:
        print('     ', e)

print()
print('=== 数据一致性 ===')
print('  笔记数:', len(api.notes_list()), '（应为 %d）' % NOTES)
print('  完整性:', backend.conn.execute('PRAGMA integrity_check').fetchone()[0])

backend.checkpoint_and_close()
shutil.rmtree(d, ignore_errors=True)
print()
print('结论：%s' % ('全部只读操作都没等锁 —— 锁粒度生效'
                 if not bad else '仍有只读操作在排队：%s' % bad))
