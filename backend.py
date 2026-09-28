"""
我的记事本 - Python 后端
处理数据库、文件操作，暴露 API 给前端
"""
import base64
import csv
import functools
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# 数据目录：优先存 exe 旁边（便携模式，拷到 U 盘/其他电脑数据一起走）
# MYNOTEPAD_DATA_DIR 环境变量可覆盖（测试用临时目录隔离，避免碰真实数据）
_EXE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
_ENV_DATA_DIR = os.environ.get('MYNOTEPAD_DATA_DIR')
DATA_DIR = _ENV_DATA_DIR or os.path.join(_EXE_DIR, "data")

# 文件大小限制
MAX_IMAGE_SIZE = 50 * 1024 * 1024    # 图片最大 50MB
MAX_ATTACH_SIZE = 200 * 1024 * 1024  # 附件最大 200MB
MAX_ICON_SIZE = 10 * 1024 * 1024     # 图标图片最大 10MB

# 如果 AppData 里有旧数据，迁移过来（测试环境变量覆盖数据目录时禁止迁移）
_OLD_APP_DATA = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'MyNotepad')
if not _ENV_DATA_DIR and os.path.exists(_OLD_APP_DATA) and _OLD_APP_DATA != DATA_DIR:
    import shutil as _shutil
    try:
        _old_db_path = os.path.join(_OLD_APP_DATA, 'notes.db')
        if os.path.exists(_old_db_path):
            import sqlite3 as _sql
            _old = _sql.connect(_old_db_path)
            _old_count = _old.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            _old.close()
            if _old_count > 0 and not os.path.exists(os.path.join(DATA_DIR, 'notes.db')):
                if not os.path.exists(DATA_DIR):
                    _shutil.copytree(_OLD_APP_DATA, DATA_DIR)
                else:
                    _shutil.copy2(_old_db_path, os.path.join(DATA_DIR, 'notes.db'))
                    _old_attach = os.path.join(_OLD_APP_DATA, 'attachments')
                    if os.path.exists(_old_attach):
                        _shutil.copytree(_old_attach, os.path.join(DATA_DIR, 'attachments'), dirs_exist_ok=True)
    except Exception:
        pass

DB_PATH = os.path.join(DATA_DIR, "notes.db")
ATTACH_DIR = os.path.join(DATA_DIR, "attachments")

# 清理旧 WAL 文件（切换到 DELETE 模式后的残留）
for _f in ['notes.db-wal', 'notes.db-shm']:
    _fp = os.path.join(DATA_DIR, _f)
    if os.path.exists(_fp):
        try: os.remove(_fp)
        except Exception: pass

# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(ATTACH_DIR, exist_ok=True)

# 崩溃兜底日志（进程级异常钩子，app.pyw import backend 即生效）
import applog

applog.init(DATA_DIR)

# ====== 数据库初始化 ======
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA journal_mode=DELETE")
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("PRAGMA synchronous=FULL")

conn.executescript("""
    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '未命名笔记',
        content TEXT DEFAULT '', bg_type TEXT DEFAULT 'global',
        bg_value TEXT DEFAULT NULL, bg_opacity REAL DEFAULT 1.0,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS attachments (
        id TEXT PRIMARY KEY, note_id TEXT NOT NULL,
        filename TEXT NOT NULL, original_name TEXT NOT NULL,
        mime_type TEXT, file_size INTEGER,
        type TEXT NOT NULL DEFAULT 'file',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS reminders (
        id TEXT PRIMARY KEY,
        note_id TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        remind_at TEXT NOT NULL,
        repeat_type TEXT DEFAULT 'none',
        repeat_interval INTEGER DEFAULT 1,
        is_completed INTEGER DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_attachments_note ON attachments(note_id);
    CREATE INDEX IF NOT EXISTS idx_notes_sort ON notes(sort_order);
    CREATE INDEX IF NOT EXISTS idx_reminders_note ON reminders(note_id);
    CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(remind_at);
""")

# 兼容旧数据库：添加新字段
try: conn.execute("ALTER TABLE notes ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN notebook_id TEXT")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN reminder_time TEXT")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN reminder_task_name TEXT")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN password_hash TEXT")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN bg_zoom INTEGER DEFAULT 100")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN bg_pos_x REAL DEFAULT 50")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN bg_pos_y REAL DEFAULT 50")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN paper_style TEXT DEFAULT 'none'")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN paper_color TEXT DEFAULT 'white'")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN cover_type TEXT DEFAULT 'none'")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN cover_value TEXT DEFAULT ''")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN enc_dek TEXT")  # 内容加密：被密码包裹的 DEK，NULL=未启用
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN deleted_at TEXT")  # 回收站：软删除时间，NULL=正常
except Exception: pass
# 正文格式：'delta'（Quill Delta JSON，历史笔记）或 'md'（Markdown 文本，新笔记默认）。
# 双轨并存：**唯一的格式判据**就是这个字段——摘要/搜索索引/导出/编辑器分流全看它。
try: conn.execute("ALTER TABLE notes ADD COLUMN format TEXT NOT NULL DEFAULT 'delta'")
except Exception: pass
# 从 Delta 转成 Markdown 时留下的原始正文（回滚用；NULL = 没转换过）
try: conn.execute("ALTER TABLE notes ADD COLUMN delta_backup TEXT")
except Exception: pass
# 索引必须在 ALTER 之后单独建（不能合并进 executescript 的 CREATE TABLE 流程）
try: conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_deleted ON notes(deleted_at)")
except Exception: pass
# 标签表
conn.executescript("""
    CREATE TABLE IF NOT EXISTS tags (
        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        color TEXT DEFAULT '#7D8A6E',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS note_tags (
        note_id TEXT NOT NULL, tag_id TEXT NOT NULL,
        PRIMARY KEY (note_id, tag_id),
        FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE,
        FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS notebooks (
        id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '未分类',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    CREATE TABLE IF NOT EXISTS versions (
        id TEXT PRIMARY KEY, note_id TEXT NOT NULL,
        title TEXT, content TEXT DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_versions_note ON versions(note_id);
""")
# 历史版本也要记格式：否则「Markdown 笔记恢复一个 Delta 版本」会得到一篇读不出来的正文
try: conn.execute("ALTER TABLE versions ADD COLUMN format TEXT NOT NULL DEFAULT 'delta'")
except Exception: pass
# 笔记本表扩展字段（必须在 CREATE TABLE 之后）
try: conn.execute("ALTER TABLE notebooks ADD COLUMN color TEXT DEFAULT '#7D8A6E'")
except Exception: pass
try: conn.execute("ALTER TABLE notebooks ADD COLUMN cover_type TEXT DEFAULT 'color'")
except Exception: pass
try: conn.execute("ALTER TABLE notebooks ADD COLUMN default_paper TEXT DEFAULT 'none'")
except Exception: pass
# 自定义背景图的两个调节项（2026-09-22）：模糊给花图降噪，界面不透明度给界面层加薄纱。
# 默认值刻意取 0 与 0.3：模糊默认关（不改观感），薄纱默认 30%（不动它也比之前清楚）。
try: conn.execute("ALTER TABLE notes ADD COLUMN bg_blur REAL DEFAULT 0")
except Exception: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN ui_scrim REAL DEFAULT 0.3")
except Exception: pass
# 正文薄纱（2026-09-26）：路线 B 的沉浸式把正文区留成全透明，花图下的正文必然有一半读不清，
# 所以给正文区也开一个滑杆。**刻意不给默认值**：NULL = 没单独设过 → 跟随全局设置，
# 这样存量笔记不会凭空多出一层薄纱（全局那份默认 30%，用户拉一下才有笔记级的值）。
try: conn.execute("ALTER TABLE notes ADD COLUMN content_scrim REAL")
except Exception: pass
# ====== 派生索引（第 7 轮）：正文的"可查询侧面" ======
# 为什么单独建表而不是每次现扫：待办聚合 / todo:/has: 搜索 / 字数统计 / 表格视图 / OCR
# 都要读正文的派生信息，各扫一遍既慢又容易口径不一致。这里统一在**保存时**一次算好。
conn.executescript("""
    CREATE TABLE IF NOT EXISTS note_derived (
        note_id TEXT PRIMARY KEY,
        word_count INTEGER NOT NULL DEFAULT 0,
        char_count INTEGER NOT NULL DEFAULT 0,
        todo_total INTEGER NOT NULL DEFAULT 0,
        todo_open INTEGER NOT NULL DEFAULT 0,
        todo_next_due TEXT,
        has_attachment INTEGER NOT NULL DEFAULT 0,
        has_reminder INTEGER NOT NULL DEFAULT 0,
        has_formula INTEGER NOT NULL DEFAULT 0,
        has_code INTEGER NOT NULL DEFAULT 0,
        link_count INTEGER NOT NULL DEFAULT 0,
        props_json TEXT NOT NULL DEFAULT '{}',
        preview TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
        FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_derived_todo ON note_derived(todo_open, todo_next_due);
    -- 待办明细：面板直接读它，写回时按 idx + 文本校验定位（正文被改过就拒绝，不猜）
    CREATE TABLE IF NOT EXISTS note_todos (
        note_id TEXT NOT NULL,
        idx INTEGER NOT NULL,
        text TEXT NOT NULL DEFAULT '',
        due TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (note_id, idx),
        FOREIGN KEY (note_id) REFERENCES notes(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_todos_due ON note_todos(done, due);
    -- 双链索引（第 9 轮）：源笔记里出现的每个 [[标题#小节|别名]] 一行。
    -- 刻意**只存标题归一（target_key）不存 target_id**：标题随时会改，物化就等于埋一个
    -- 必然过期的索引（改完标题反向链接全断）。解析放到查询时 JOIN notes。
    CREATE TABLE IF NOT EXISTS note_links (
        src_note_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        target_raw TEXT NOT NULL DEFAULT '',
        target_key TEXT NOT NULL DEFAULT '',
        heading TEXT,
        alias TEXT,
        PRIMARY KEY (src_note_id, ordinal),
        FOREIGN KEY (src_note_id) REFERENCES notes(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_links_target ON note_links(target_key);
    -- 模板（第 10 轮）：新建笔记时可套用，支持 {{date}} {{time}} {{weekday}} {{title}}
    CREATE TABLE IF NOT EXISTS templates (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
    -- 保存的搜索：侧栏"视图"区的数据源
    CREATE TABLE IF NOT EXISTS saved_searches (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        query TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
    );
""")
# 兼容旧数据库：派生表补 preview 列（列表摘要改为保存时预计算，见 _refresh_derived）。
# 列默认空串 + DERIVED_VERSION 变更 ⇒ 启动回填会把存量笔记的摘要补齐。
try: conn.execute("ALTER TABLE note_derived ADD COLUMN preview TEXT NOT NULL DEFAULT ''")
except Exception: pass
conn.commit()

# 默认设置
for k, v in [('theme', 'white'), ('bg_type', 'color'), ('bg_value', ''), ('bg_opacity', '1.0'),
             ('bg_blur', '0'), ('ui_scrim', '0.3'), ('content_scrim', '0.3')]:
    conn.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (k, v))
conn.commit()

# ====== 迁移旧提醒数据（notes 表 → reminders 表）======
def _migrate_old_reminders():
    """将 notes 表中的旧提醒迁移到新的 reminders 表，并清理旧计划任务
    使用独立数据库连接，避免干扰主连接的事务状态"""
    mig_conn = None
    try:
        mig_conn = sqlite3.connect(DB_PATH)
        mig_conn.row_factory = sqlite3.Row
        old = mig_conn.execute(
            "SELECT id, title, reminder_time, reminder_task_name FROM notes WHERE reminder_time IS NOT NULL"
        ).fetchall()
        if not old:
            return
        migrated = 0
        for row in old:
            existing = mig_conn.execute(
                "SELECT COUNT(*) as c FROM reminders WHERE note_id = ? AND remind_at = ?",
                (row['id'], row['reminder_time'])
            ).fetchone()
            if existing and existing['c'] > 0:
                continue
            rid = str(uuid.uuid4())
            mig_conn.execute(
                "INSERT INTO reminders (id, note_id, content, remind_at) VALUES (?, ?, ?, ?)",
                (rid, row['id'], row['title'], row['reminder_time'])
            )
            # 删除旧的 Windows 计划任务
            task_name = row['reminder_task_name'] or f"MyNotepad_Reminder_{row['id'][:8]}"
            # 安全校验：确保只删除我们自己的计划任务
            if not task_name.startswith('MyNotepad_Reminder_'):
                task_name = f"MyNotepad_Reminder_{row['id'][:8]}"
            try:
                import subprocess
                subprocess.run(['schtasks', '/delete', '/tn', task_name, '/f'],
                               capture_output=True, timeout=5)
            except Exception:
                pass
            migrated += 1
        if migrated > 0:
            mig_conn.execute("UPDATE notes SET reminder_time = NULL, reminder_task_name = NULL WHERE reminder_time IS NOT NULL")
        mig_conn.commit()
    except Exception as e:
        print(f"迁移旧提醒数据失败: {e}")
    finally:
        if mig_conn:
            try: mig_conn.close()
            except Exception: pass

# 清理所有残留的 MyNotepad_Reminder_* 计划任务
def _cleanup_all_scheduled_tasks():
    """清理所有 MyNotepad 相关的 Windows 计划任务"""
    try:
        import subprocess
        result = subprocess.run(
            ['schtasks', '/query', '/fo', 'CSV', '/nh'],
            capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if 'MyNotepad_Reminder_' in line:
                parts = line.split(',')
                if parts:
                    task_name = parts[0].strip('"')
                    if task_name.startswith('MyNotepad_Reminder_'):
                        subprocess.run(['schtasks', '/delete', '/tn', task_name, '/f'],
                                       capture_output=True, timeout=5)
    except Exception:
        pass

_migrate_old_reminders()
if not _ENV_DATA_DIR:  # 测试环境不碰系统计划任务
    _cleanup_all_scheduled_tasks()

# ====== 内容加密（envelope 信封加密） ======
# 每篇加密笔记有一个随机 DEK（数据密钥）加密正文和历史版本；
# DEK 被「密码派生的 KEK」包裹后存库（notes.enc_dek）。
# 改密码只需重新包裹 32 字节 DEK，内容零重加密。
# 解锁状态在后端进程内存（_unlocked_deks），前端传来的 unlocked 标志不再作为安全依据。

_db_lock = threading.RLock()   # 保护全局 conn 与解锁缓存（pywebview 每个 JS 调用运行在独立线程）


class _UnlockedDeks(dict):
    """note_id -> DEK bytes 的解锁缓存（会话级，随进程消亡）。

    **带后端 TTL**：为什么必须有 —— 以前"闲置自动锁定"完全由前端驱动
    （`07-formula-security-dnd.js` 的 setInterval + 自己的 `unlockedNotes`），
    后端这个字典**永不过期**。于是"解锁状态是后端安全边界"这句话在**过期**这件事上不成立：
    前端一旦漏了（脚本出错、窗口被挂起、用户直接改内存标志），密钥就在进程里一直留着。

    实现方式：读的时候顺手把过期的删掉（惰性过期），所以 25 处 `get()` / `in` 调用点
    一行都不用改。**刻意不碰 `__setitem__`**：写入是"刚解锁/刚设完密码"，它当然要刷新时间戳。
    ⚠️ 惰性过期只在"有人读"时生效：只挂机不操作时字典里会留着过期项 —— 那由
    `_sweep_unlocked_deks()` 在既有的 30 秒提醒轮询里顺带清（不新开线程）。
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ttl_minutes = 0          # 0 = 不启用（与前端默认「关闭」一致）
        self._stamps = {}             # note_id -> 最近一次解锁/访问的 monotonic 时间

    # ----- 时间戳维护 -----
    def __setitem__(self, key, value):
        self._stamps[key] = time.monotonic()
        super().__setitem__(key, value)

    def __delitem__(self, key):
        self._stamps.pop(key, None)
        super().__delitem__(key)

    def pop(self, key, *args):
        self._stamps.pop(key, None)
        return super().pop(key, *args)

    def clear(self):
        self._stamps.clear()
        return super().clear()

    # ----- 惰性过期 -----
    def _expired(self, key):
        if not self.ttl_minutes:
            return False
        ts = self._stamps.get(key)
        if ts is None:
            return False
        return (time.monotonic() - ts) > self.ttl_minutes * 60

    def _prune(self, key):
        if key in dict.keys(self) and self._expired(key):
            dict.__delitem__(self, key)
            self._stamps.pop(key, None)
            applog.get_logger().info('解锁已过期（闲置 %d 分钟）：%s', self.ttl_minutes, key)

    def get(self, key, default=None):
        self._prune(key)
        return super().get(key, default)

    def __contains__(self, key):
        self._prune(key)
        return super().__contains__(key)

    def __getitem__(self, key):
        self._prune(key)
        return super().__getitem__(key)

    # ----- 主动清理（提醒轮询里调用）-----
    def sweep(self):
        """清掉全部已过期的项，返回被清掉的 note_id 列表。"""
        gone = []
        for key in list(dict.keys(self)):
            if self._expired(key):
                gone.append(key)
                dict.__delitem__(self, key)
                self._stamps.pop(key, None)
        if gone:
            applog.get_logger().info('解锁过期清理：%d 篇', len(gone))
        return gone


_unlocked_deks = _UnlockedDeks()   # note_id -> DEK bytes

PBKDF2_ITERATIONS = 600000     # OWASP 2023
MAX_PBKDF2_ITERATIONS = 6_000_000  # 解包 DEK 时接受的最大迭代数（当前值的 10 倍）。
                                   # 迭代数存在库里，损坏或被篡改的库可以塞个天文数字，让
                                   # 「输入密码解锁」变成纯 CPU 卡死（PBKDF2 无法短路失败）。
                                   # 留足未来上调迭代数的空间，同时把最坏耗时限制在当前
                                   # 解锁成本的 10 倍以内。
ENC_PREFIX = 'encv1:'          # 密文标记；明文是 Delta JSON（{ 开头）或空串，不会冲突

def _derive_kek(password, salt, iterations=PBKDF2_ITERATIONS):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)

def _wrap_dek(dek, password):
    """用密码包裹 DEK，返回存库格式 dekv1:<salt_hex>:<iters>:<b64(nonce+ct)>"""
    kek_salt = os.urandom(32)
    kek = _derive_kek(password, kek_salt)
    nonce = os.urandom(12)
    ct = AESGCM(kek).encrypt(nonce, dek, None)
    return f"dekv1:{kek_salt.hex()}:{PBKDF2_ITERATIONS}:{base64.b64encode(nonce + ct).decode()}"

def _unwrap_dek(enc_dek, password):
    """解包 DEK；密码错误或格式损坏返回 None"""
    try:
        tag, salt_hex, iters, blob = enc_dek.split(':')
        if tag != 'dekv1':
            return None
        kek = _derive_kek(password, bytes.fromhex(salt_hex),
                          min(int(iters), MAX_PBKDF2_ITERATIONS))
        raw = base64.b64decode(blob)
        return AESGCM(kek).decrypt(raw[:12], raw[12:], None)
    except Exception:
        return None

def _encrypt_content(dek, plaintext, note_id):
    """明文 -> encv1:<b64(nonce+ct)>；AAD 绑定 note_id，防止密文跨笔记移植"""
    nonce = os.urandom(12)
    ct = AESGCM(dek).encrypt(nonce, (plaintext or '').encode('utf-8'), note_id.encode('utf-8'))
    return ENC_PREFIX + base64.b64encode(nonce + ct).decode()

def _decrypt_content(dek, stored, note_id):
    """encv1 密文 -> 明文；非密文原样返回（迁移期兼容）；解密失败返回 None"""
    if not stored or not stored.startswith(ENC_PREFIX):
        return stored or ''
    try:
        raw = base64.b64decode(stored[len(ENC_PREFIX):])
        return AESGCM(dek).decrypt(raw[:12], raw[12:], note_id.encode('utf-8')).decode('utf-8')
    except Exception:
        return None

# ====== 数据库定期备份 ======
BACKUP_DIR = os.path.join(DATA_DIR, "backups")

def _next_sort_order(table='notes'):
    """下一个排序值 = 当前最大值 + 1。

    **不要写 `.fetchone()['m'] or -1`**：MAX 为 0 时 `0 or -1` 得到 -1，新行又拿到 0，
    于是多行 sort_order 撞在一起，`ORDER BY sort_order DESC` 退化成同秒内随机——
    「新建的排最前」「副本排最前」都会时灵时不灵。None（空表）才当 -1。
    """
    # 表名是**插值**进 SQL 的（占位符不能用于标识符）。当前 6 个调用点传的都是字面量、
    # 函数名带 `_` 不上桥，所以安全性靠"没人乱传"维持 —— 加一条断言把这条约定钉住，
    # 免得将来有人把它接到带参数的地方去。
    assert table in ('notes', 'notebooks', 'tags', 'templates', 'saved_searches'), \
        '表名只接受白名单字面量：%r' % (table,)
    row = conn.execute("SELECT MAX(sort_order) AS m FROM %s" % table).fetchone()
    m = row['m'] if row and row['m'] is not None else -1
    return int(m) + 1


_DURATION_RE = re.compile(r'^(\d+)([dwm])$')
_DATE_OPS = {'>': '>', '<': '<', '>=': '>=', '<=': '<=', '=': '='}

# SQLite 的绑定变量数有上限（默认 32766）。批量接口把 id 数量原样展开成 `?,?,?…`，
# 而"全选 → 批量操作"在大库上突破这个数完全可能（超了会抛 too many SQL variables，
# 界面只看到"操作失败"）。分块取值留足余量：每块 500 个，顺便让单条 SQL 的解析也更快。
SQL_CHUNK = 500


def _chunked(items, size=SQL_CHUNK):
    """把列表切成若干块（用于 IN (...) 批量查询/更新）。"""
    items = list(items or [])
    for i in range(0, len(items), size):
        yield items[i:i + size]


_SCOPE_FLAGS = {'pinned': 'n.is_pinned = 1', 'favorite': 'n.is_favorite = 1',
                'encrypted': "COALESCE(n.password_hash, '') != ''"}
_SCOPE_HAS = {
    'attachment': 'd.has_attachment = 1',
    'reminder': 'd.has_reminder = 1',
    'todo': 'd.todo_total > 0',
    'formula': 'd.has_formula = 1',
    'code': 'd.has_code = 1',
    'link': 'd.link_count > 0',
    'prop': "COALESCE(d.props_json, '{}') NOT IN ('', '{}')",
}


def _parse_date_value(value):
    """`2026-01-01` / `today` / `yesterday` / `7d` → (iso 日期, 是否相对时长)。

    相对时长与绝对日期的**语义不同**（这点必须记住）：
      · 绝对日期：`created:<2026-01-01` = 早于那天
      · 相对时长：`updated:<7d` = **最近 7 天**（用户的心智是"多久以内"，
        写成"早于 7 天前"会得到完全相反的结果——第一版实现就踩了这个坑）
    """
    v = (value or '').strip().lower()
    today = datetime.now().date()
    if v in ('today', '今天'):
        return today.isoformat(), True
    if v in ('yesterday', '昨天'):
        return (today - timedelta(days=1)).isoformat(), True
    m = _DURATION_RE.match(v)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {'d': 1, 'w': 7, 'm': 30}[unit]
        return (today - timedelta(days=days)).isoformat(), True
    try:
        return datetime.strptime(v, '%Y-%m-%d').date().isoformat(), False
    except ValueError:
        return None, False


def _parse_search_scope(q):
    """把查询拆成 (自由文本, 范围字典)。

    支持：`tag:数学` / `notebook:课程A`（或 `nb:`）/ `in:trash` / `title:关键词` /
    `is:pinned|favorite|encrypted` / `has:attachment|reminder|todo|formula|code|link|prop` /
    `todo:open|done` / `created:>2026-01-01`（也支持 today/yesterday/7d）/ `updated:<7d` /
    `prop:状态` / `prop:状态=进行中` / `prop:截止>2026-10-01` / `prop:标签~关键词` /
    `-排除词`（可多个）。任何前缀都可以加 `-` 取反。

    不认识的前缀（例如用户真想搜 "a:b"）原样留在自由文本里，不做吞掉。
    """
    scope = {'exclude': []}
    words = []
    for tok in (q or '').split():
        negate = False
        body = tok
        if tok.startswith('-') and len(tok) > 1:
            negate = True
            body = tok[1:]
        if ':' in body:
            key, _, val = body.partition(':')
            key_l = key.lower()
            handled = True
            if key_l == 'tag' and val:
                scope.setdefault('tag', []).append((val, negate))
            elif key_l in ('notebook', 'nb') and val:
                scope['notebook'] = (val, negate)
            elif key_l == 'in' and val.lower() in ('trash', '回收站'):
                scope['trash'] = not negate
            elif key_l == 'title' and val:
                scope['title'] = (val, negate)
            elif key_l == 'is' and val.lower() in _SCOPE_FLAGS:
                scope.setdefault('is', []).append((val.lower(), negate))
            elif key_l == 'has' and val.lower() in _SCOPE_HAS:
                scope.setdefault('has', []).append((val.lower(), negate))
            elif key_l == 'todo' and val.lower() in ('open', 'done', '未完成', '已完成'):
                scope['todo'] = (val.lower(), negate)
            elif key_l == 'prop' and val:
                # 运算符**必须显式出现**才会走第一个分支：第一版把它写成可选，于是惰性量词
                # 在 `状态=进行中` 上直接停在"状"，把剩下的当成值（`prop:` 搜索全部失效）。
                pm = re.match(r'^([^=<>!~]+?)\s*(>=|<=|!=|=|>|<|~)\s*(.*)$', val)
                if pm and pm.group(1).strip():
                    pkey, pop, pval = pm.group(1).strip(), pm.group(2), pm.group(3).strip()
                else:
                    pm = re.match(r'^([^=<>!~]+?)\s*$', val)
                    pkey, pop, pval = (pm.group(1).strip() if pm else ''), None, ''
                if pkey:
                    if pop == '!=':            # `!=` 就是"等于"再取反
                        pop, negate = '=', not negate
                    scope.setdefault('prop', []).append((pkey, pop, pval, negate))
                else:
                    handled = False
            elif key_l in ('created', 'updated') and val:
                m = re.match(r'^(>=|<=|>|<|=)?(.*)$', val)
                op = _DATE_OPS.get(m.group(1) or '=', '=')
                iso, is_duration = _parse_date_value(m.group(2))
                if iso:
                    # 相对时长一律解释成"最近 N"（见 _parse_date_value 的说明）
                    scope[key_l] = ('>=' if is_duration else op, iso, negate)
                else:
                    handled = False
            else:
                handled = False
            if handled:
                continue
        if negate:
            scope['exclude'].append(body)
        else:
            words.append(tok)
    scope = {k: v for k, v in scope.items() if v not in ([], None, '')}
    return ' '.join(words), scope


def _scope_where(scope):
    """范围字典 → (WHERE 片段列表, 参数列表)，全部作用在 `notes n LEFT JOIN note_derived d` 上"""
    where = ["n.deleted_at IS " + ("NOT NULL" if scope.get('trash') else "NULL")]
    params = []
    if scope.get('tag'):
        # 标签层级：标签名含 `/` 即层级，`tag:父` 自动包含 `父/子`
        for name, negate in scope['tag']:
            sub = ("n.id IN (SELECT nt.note_id FROM note_tags nt JOIN tags t ON t.id = nt.tag_id "
                   "WHERE t.id = ? OR t.name = ? OR t.name LIKE ? ESCAPE '\\')")
            where.append(('NOT ' if negate else '') + sub)
            # 子标签那条是 LIKE，用户输入里的 % _ \ 必须转义（与下面 title: 同一条口径）：
            # 不转义时 `tag:%` 会命中**所有**层级标签，标签名带反斜杠则子标签静默失配。
            esc = name.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            params += [name, name, esc + '/%']
    if scope.get('notebook'):
        name, negate = scope['notebook']
        sub = ("(n.notebook_id = ? OR n.notebook_id IN (SELECT id FROM notebooks WHERE name = ?))")
        where.append(('NOT ' if negate else '') + sub)
        params += [name, name]
    if scope.get('title'):
        val, negate = scope['title']
        where.append("n.title " + ("NOT " if negate else "") + "LIKE ? ESCAPE '\\'")
        params.append('%' + val.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%')
    for flag, negate in scope.get('is', []):
        where.append(('NOT ' if negate else '') + '(' + _SCOPE_FLAGS[flag] + ')')
    for flag, negate in scope.get('has', []):
        where.append(('NOT ' if negate else '') + '(' + _SCOPE_HAS[flag] + ')')
    if scope.get('todo'):
        val, negate = scope['todo']
        expr = 'd.todo_open > 0' if val in ('open', '未完成') else 'd.todo_open = 0 AND d.todo_total > 0'
        where.append(('NOT ' if negate else '') + '(' + expr + ')')
    for key, col in (('created', 'created_at'), ('updated', 'updated_at')):
        if scope.get(key):
            op, iso, negate = scope[key]
            where.append('NOT (date(n.%s) %s ?)' % (col, op) if negate
                         else 'date(n.%s) %s ?' % (col, op))
            params.append(iso)
    return where, params


def _scope_note_ids(scope):
    """按范围取候选笔记 id"""
    where, params = _scope_where(scope)
    rows = conn.execute(
        "SELECT n.id FROM notes n LEFT JOIN note_derived d ON d.note_id = n.id WHERE "
        + ' AND '.join(where), params).fetchall()
    return [r['id'] for r in rows]


def _trash_title_ids(text):
    """回收站里的笔记已被移出 FTS 索引，正文搜索只能退化为标题 LIKE（够用来找回来删掉的那篇）"""
    esc = text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    rows = conn.execute(
        "SELECT id FROM notes WHERE deleted_at IS NOT NULL AND title LIKE ? ESCAPE '\\'",
        ('%' + esc + '%',)).fetchall()
    return [r['id'] for r in rows]


def _md_inline_escape(text):
    """把纯文本里的 Markdown 元字符转义，避免正文里的 * 和 _ 被当成格式"""
    out = []
    for ch in text or '':
        if ch in '\\`*_{}[]()#+-.!|':
            out.append('\\' + ch)
        else:
            out.append(ch)
    return ''.join(out)


# 字体/字号/颜色在 Markdown 里靠**白名单内嵌 HTML** 保留（第 6 轮决策）：
# Obsidian/Typora 能渲染；Joplin 官方说明会丢 HTML —— 这是刻意接受的取舍（见 README「已知限制」）。
_MD_FONT_CLASSES = {'serif': 'md-font-serif', 'monospace': 'md-font-mono', 'cursive': 'md-font-hand'}
_MD_FONT_BY_CLASS = {v: k for k, v in _MD_FONT_CLASSES.items()}
_MD_COLOR_RE = re.compile(r'^(#[0-9a-fA-F]{3,8}|rgba?\([\d\s.,%]+\))$')
_MD_SIZE_RE = re.compile(r'^\d{1,3}(px|em|rem|%)$')
_MD_SPAN_RE = re.compile(r'^<span([^>]*)>', re.I)
_MD_STYLE_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.I)
_MD_CLASS_RE = re.compile(r'class\s*=\s*"([^"]*)"', re.I)
_MD_HR_RE = re.compile(r'^<hr[^>]*class\s*=\s*"([^"]*)"', re.I)
_MD_TAG_RE = re.compile(r'</?(?:u|sup|sub|mark|span|hr)\b[^>]*>', re.I)
_MD_ATX_IMAGE = re.compile(r'^!\[([^\]]*)\]\(([^)]+)\)$')
_MD_MATH_BLOCK_RE = re.compile(r'^\$\$(.+?)\$\$$')
_MD_MATH_INLINE_RE = re.compile(r'\$([^$\n]+?)\$')


def _style_attrs(style_text):
    """白名单 style → Delta 属性（只认 color / font-size，其余一律忽略）"""
    out = {}
    for decl in (style_text or '').split(';'):
        if ':' not in decl:
            continue
        prop, _, val = decl.partition(':')
        prop, val = prop.strip().lower(), val.strip()
        if prop == 'color' and _MD_COLOR_RE.match(val):
            out['color'] = val
        elif prop == 'font-size' and _MD_SIZE_RE.match(val):
            out['size'] = val
    return out


def _delta_to_md_attrs(attrs):
    """(文本, 行内属性) → Markdown 行内文本；富文本属性用白名单 HTML 包住"""
    text, attrs = attrs
    s = _md_inline_escape(text)
    if attrs.get('code'):
        s = '`' + text + '`'
    else:
        if attrs.get('bold'):
            s = '**' + s + '**'
        if attrs.get('italic'):
            s = '*' + s + '*'
        if attrs.get('strike'):
            s = '~~' + s + '~~'
        if attrs.get('underline'):
            s = '<u>' + s + '</u>'
        script = attrs.get('script')
        if script in ('super', 'sub'):
            tag = 'sup' if script == 'super' else 'sub'
            s = '<%s>%s</%s>' % (tag, s, tag)
        font = attrs.get('myfont') or attrs.get('font')
        if font in _MD_FONT_CLASSES:
            s = '<span class="%s">%s</span>' % (_MD_FONT_CLASSES[font], s)
        styles = []
        color = attrs.get('color')
        if color and _MD_COLOR_RE.match(str(color)):
            styles.append('color: %s' % color)
        size = attrs.get('size')
        if size and _MD_SIZE_RE.match(str(size)):
            styles.append('font-size: %s' % size)
        if attrs.get('background'):
            styles.append('background-color: %s' % attrs['background'])
        if styles:
            s = '<span style="%s">%s</span>' % ('; '.join(styles), s)
    if attrs.get('link'):
        s = '[%s](%s)' % (s, attrs['link'])
    return s


def _md_inline(pieces):
    """(文本, 行内属性) 列表 → Markdown 行内文本"""
    return ''.join(_delta_to_md_attrs(p) for p in pieces)


def _embed_text_fallback(value, depth=0):
    """未知 embed 的兜底：递归把里面的字符串抽出来，宁可丢结构也不能丢字"""
    if depth > 4:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return ' '.join(_embed_text_fallback(v, depth + 1) for v in value.values())
    if isinstance(value, list):
        return ' '.join(_embed_text_fallback(v, depth + 1) for v in value)
    return ''


def _md_embed(kind, value, note_id=None, resolve_attachment=None):
    """嵌入对象 → Markdown 片段（图片用占位，真正的路径由调用方替换）"""
    value = value if isinstance(value, dict) else {}
    if kind == 'image':
        name = value.get('filename') or value.get('storedPath') or ''
        return '\x00IMG:%s\x00' % name
    if kind == 'math-formula':
        latex = value.get('latex') or value.get('formula') or ''
        return '$$%s$$' % latex if value.get('display') else '$%s$' % latex
    if kind == 'attachment':
        name = value.get('filename') or ''
        label = value.get('originalName') or name or '附件'
        if resolve_attachment:
            href = resolve_attachment(name) or ''
        elif note_id and name:
            href = 'attachments/%s/%s' % (note_id, name)
        else:
            href = ''
        return '[📎 %s](%s)' % (label, href) if href else '📎 %s' % label
    if kind == 'divider':
        style = value.get('style') or value.get('type') or 1
        try:
            cls = 'divider-%d' % max(1, min(12, int(style)))
        except (TypeError, ValueError):
            cls = 'divider-1'
        # 不要再包换行：行本身由 Delta 的 \n op 负责，自己加换行会在往返时每次多一个空行
        return '<hr class="%s">' % cls
    if kind in ('sticker', 'stamp'):
        emoji = value.get('emoji') or value.get('text') or value.get('char') or '🔖'
        return '<span class="md-sticker">%s</span>' % emoji
    guess = _embed_text_fallback(value).strip()
    return guess


# Quill 的表格是一组**带行/单元格属性的文本行**：同一行的多行共享 table=<行id>，
# 单元格之间用 table-cell 区分。这里按行分组拼成 GFM 表格。
_MD_TABLE_ROW_ATTR = 'table'
_MD_TABLE_CELL_ATTR = 'table-cell'
_MD_TABLE_LINE_ATTR = 'table-cell-line'


def _rows_to_gfm_table(rows):
    """[[cell, cell, ...], ...] → GFM 表格文本（首行当表头）"""
    if not rows:
        return ''
    width = max(len(r) for r in rows)
    def fmt(cells):
        padded = list(cells) + [''] * (width - len(cells))
        return '| ' + ' | '.join(c.replace('|', '\\|').replace('\n', ' ') for c in padded) + ' |'
    out = [fmt(rows[0]), '| ' + ' | '.join(['---'] * width) + ' |']
    out += [fmt(r) for r in rows[1:]]
    return '\n'.join(out)


def _delta_to_markdown(content, resolve_image=None, note_id=None, resolve_attachment=None):
    """Quill Delta JSON → Markdown（标题/列表/待办/引用/代码块/表格 + 行内格式 + 嵌入对象）。

    Quill 的行属性挂在**含换行符的那个 op** 上，所以按 op 切行、用换行所在 op 的属性
    作为整行属性。图片需要调用方提供 resolve_image(文件名) → 相对路径（导出时会把图复制到
    同级 assets 目录），拿不到路径就退化成文件名文本。
    """
    if not content:
        return ''
    try:
        data = json.loads(content)
    except Exception:
        return content or ''
    ops = data.get('ops', []) if isinstance(data, dict) else data
    if not isinstance(ops, list):
        return ''

    lines, cur = [], {'pieces': [], 'attrs': {}}
    for op in ops:
        if not isinstance(op, dict):
            continue
        ins, attrs = op.get('insert'), op.get('attributes') or {}
        if isinstance(ins, dict):
            kind = next(iter(ins))
            cur['pieces'].append(
                (_md_embed(kind, ins[kind], note_id, resolve_attachment), {'raw': True}))
            continue
        if not isinstance(ins, str):
            continue
        parts = ins.split('\n')
        for i, part in enumerate(parts):
            if part:
                cur['pieces'].append((part, attrs))
            if i < len(parts) - 1:
                cur['attrs'] = attrs
                lines.append(cur)
                cur = {'pieces': [], 'attrs': {}}
    if cur['pieces']:
        lines.append(cur)

    out = []
    code_buf = []          # 连续的多行代码块要合并成一个 ``` 块
    table_rows, table_cell, table_cell_lines = [], [], []

    def _flush_code():
        if code_buf:
            out.append('```\n' + '\n'.join(code_buf) + '\n```')
            code_buf.clear()

    def _flush_cell():
        if table_cell_lines:
            table_cell.append('\n'.join(table_cell_lines))
            table_cell_lines.clear()

    def _flush_table():
        _flush_cell()
        if table_cell:
            table_rows.append(list(table_cell))
            table_cell.clear()
        if table_rows:
            out.append(_rows_to_gfm_table(table_rows))
            table_rows.clear()

    for line in lines:
        attrs = line['attrs'] or {}
        body = ''.join(t if a.get('raw') else _md_inline([(t, a)])
                       for t, a in line['pieces'])
        if attrs.get(_MD_TABLE_ROW_ATTR):
            _flush_code()
            _flush_cell()
            cell_id = attrs.get(_MD_TABLE_CELL_ATTR)
            if table_rows and table_cell and cell_id != getattr(_flush_table, '_last_cell', None):
                pass
            table_cell_lines.append(body)
            # 同一行内换单元格：table-cell 变化即分格
            prev = getattr(_flush_table, '_cell', None)
            if prev is None:
                _flush_table._cell = cell_id
            elif cell_id != prev:
                _flush_cell()
                _flush_table._cell = cell_id
            continue
        _flush_table()
        _flush_table._cell = None
        if attrs.get('code-block'):
            # Quill 把多行代码块编码成**一个** op（内部含换行）并带 code-block 属性，
            # 所以按行看会看到连续多行都带该属性——必须合并成一个围栏块。
            code_buf.append(''.join(t for t, _ in line['pieces']))
            continue
        _flush_code()
        prefix = ''
        header = attrs.get('header')
        lst = attrs.get('list')
        if header:
            # header 来自 Delta 行属性，可能是**任意类型/任意内容**（手写 JSON、外部导入的
            # 库、被改坏的正文都会出现奇怪的值）。直接 int() 有两个后果：
            #   ① 非数字抛 ValueError，而异常消息里会带上**这串原始内容** ——
            #      applog 会把 message 记进 error.log，等于把正文写进日志；
            #   ② 数字很大时 `'#' * huge` 直接分配巨量字符串。
            # 与同文件 `_md_embed` 里 `int(style)` 的口径对齐：try + 夹紧到 [1, 6]。
            try:
                level = max(1, min(6, int(header)))
            except (TypeError, ValueError):
                level = 1
            prefix = '#' * level + ' '
        elif lst == 'ordered':
            prefix = '1. '
        elif lst == 'bullet':
            prefix = '- '
        elif lst == 'checked':
            prefix = '- [x] '
        elif lst == 'unchecked':
            prefix = '- [ ] '
        elif attrs.get('blockquote'):
            prefix = '> '
        out.append(prefix + body)
    _flush_table()
    _flush_code()
    md = '\n'.join(out).rstrip() + '\n'
    if resolve_image:
        def _sub(m):
            path = resolve_image(m.group(1))
            return ('![](%s)' % path) if path else _md_inline_escape(m.group(1))
        md = re.sub('\x00IMG:(.*?)\x00', _sub, md)
    else:
        md = re.sub('\x00IMG:(.*?)\x00', lambda m: _md_inline_escape(m.group(1)), md)
    return md


_MD_HEADING = re.compile(r'^(#{1,6})\s+(.*)$')
_MD_UL = re.compile(r'^\s*[-*+]\s+(.*)$')
_MD_TODO = re.compile(r'^\s*[-*+]\s+\[([ xX])\]\s*(.*)$')
_MD_OL = re.compile(r'^\s*\d+[.)]\s+(.*)$')
_MD_QUOTE = re.compile(r'^\s*>\s?(.*)$')
_MD_FENCE = re.compile(r'^\s*```')
_MD_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$')


def _md_split_table_row(line):
    cells = line.strip().strip('|').split('|')
    return [c.strip() for c in cells]


def _md_merge(base, extra):
    """合并属性（extra 覆盖 base）；值一律是标量，便于直接当 Delta attributes 用"""
    out = dict(base)
    out.update(extra)
    return out


def _md_parse_inline(text, base=None, _depth=0):
    """Markdown 行内 → Delta ops。

    支持：**粗** / *斜* / ~~删~~ / `码` / [文字](链接) / $公式$，
    以及本应用导出的白名单内嵌 HTML（<u>/<sup>/<sub>/<mark>/<span style|class>）。

    属性用**扁平字典**在递归里传递（不是就地压栈）：早期版本把 `**粗**` 交给
    递归调用后只在自己这层压栈，结果子调用产生的 ops 完全没带上 bold ——
    「加粗转一圈就没了」，且往返测试才发现。现在把当前属性直接传给子调用。
    """
    if _depth > 8:
        return [{'insert': text}]
    ops, i, n = [], 0, len(text)
    base_attrs = dict(base or {})
    buf = []

    def flush(extra=None):
        if not buf:
            return
        attrs = _md_merge(base_attrs, extra or {})
        ops.append({'insert': ''.join(buf), 'attributes': attrs} if attrs
                   else {'insert': ''.join(buf)})
        buf.clear()

    while i < n:
        ch = text[i]
        if ch == '\\' and i + 1 < n:
            buf.append(text[i + 1]); i += 2; continue
        if ch == '$':
            m = _MD_MATH_INLINE_RE.match(text, i)
            if m and m.group(1).strip():
                flush()
                ops.append({'insert': {'math-formula': {'latex': m.group(1).strip(),
                                                        'display': False}}})
                i = m.end(); continue
        close = re.match(r'</(u|sup|sub|mark|span)>', text[i:], re.I)
        if close:
            # 闭合标签由递归层处理（子调用不会看到它），这里只是兜底：当普通文本
            buf.append(text[i:i + close.end()]); i += close.end(); continue
        if text[i:i + 3].lower() == '<u>':
            end = re.search(r'</u>', text[i:], re.I)
            if end:
                flush()
                ops.extend(_md_parse_inline(text[i + 3:i + end.start()],
                                            _md_merge(base_attrs, {'underline': True}), _depth + 1))
                i += end.end(); continue
        m = re.match(r'<(sup|sub)>(.*?)</\1>', text[i:], re.I | re.S)
        if m:
            flush()
            tag = m.group(1).lower()
            ops.extend(_md_parse_inline(m.group(2), _md_merge(
                base_attrs, {'script': 'super' if tag == 'sup' else 'sub'}), _depth + 1))
            i += m.end(); continue
        m = re.match(r'<mark>(.*?)</mark>', text[i:], re.I | re.S)
        if m:
            flush()
            ops.extend(_md_parse_inline(m.group(1),
                                        _md_merge(base_attrs, {'background': '#FFF3A3'}), _depth + 1))
            i += m.end(); continue
        m = _MD_SPAN_RE.match(text[i:])
        if m:
            attrs = {}
            style = _MD_STYLE_RE.search(m.group(1) or '')
            if style:
                attrs.update(_style_attrs(style.group(1)))
            cls = _MD_CLASS_RE.search(m.group(1) or '')
            if cls:
                for name in cls.group(1).split():
                    if name in _MD_FONT_BY_CLASS:
                        attrs['myfont'] = _MD_FONT_BY_CLASS[name]
            end = re.search(r'</span>', text[i:], re.I)
            if end:
                flush()
                ops.extend(_md_parse_inline(text[i + m.end():i + end.start()],
                                            _md_merge(base_attrs, attrs), _depth + 1))
                i += end.end(); continue
            i += m.end(); continue
        if ch == '`':
            end = text.find('`', i + 1)
            if end > 0:
                flush()
                ops.append({'insert': text[i + 1:end],
                            'attributes': _md_merge(base_attrs, {'code': True})})
                i = end + 1; continue
        if text.startswith('~~', i):
            end = text.find('~~', i + 2)
            if end > 0:
                flush()
                ops.extend(_md_parse_inline(text[i + 2:end],
                                            _md_merge(base_attrs, {'strike': True}), _depth + 1))
                i = end + 2; continue
        if text.startswith('**', i):
            end = text.find('**', i + 2)
            if end > 0:
                flush()
                ops.extend(_md_parse_inline(text[i + 2:end],
                                            _md_merge(base_attrs, {'bold': True}), _depth + 1))
                i = end + 2; continue
        if ch in '*_':
            end = text.find(ch, i + 1)
            if end > i + 1:
                flush()
                ops.extend(_md_parse_inline(text[i + 1:end],
                                            _md_merge(base_attrs, {'italic': True}), _depth + 1))
                i = end + 1; continue
        m = _MD_ATX_IMAGE.match(text[i:])
        if m:
            flush()
            src = m.group(2)
            name = src.split('/')[-1]
            ops.append({'insert': {'image': {'filename': name, 'storedPath': src}}})
            i += m.end(); continue
        if ch == '[':
            m = re.match(r'\[(.*?)\]\((.*?)\)', text[i:])
            if m:
                flush()
                href, label = m.group(2), m.group(1)
                if href.startswith('attachments/') and label.startswith('📎'):
                    ops.append({'insert': {'attachment': {
                        'filename': href.split('/')[-1],
                        'originalName': label.replace('📎', '').strip() or href.split('/')[-1],
                        'storedPath': href}}})
                else:
                    ops.append({'insert': label,
                                'attributes': _md_merge(base_attrs, {'link': href})})
                i += m.end(); continue
        buf.append(ch); i += 1
    flush()
    return ops or [{'insert': ''}]


def markdown_to_delta(md_text):
    """Markdown → Quill Delta JSON（标题/列表/待办/引用/代码块/表格/公式 + 行内格式）。

    只做常用子集：解析不出来的行当普通段落，不会丢内容（宁可少格式，不可少字）。
    """
    lines = (md_text or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
    ops, i = [], 0
    while i < len(lines):
        line = lines[i]
        if _MD_FENCE.match(line):
            i += 1
            block = []
            while i < len(lines) and not _MD_FENCE.match(lines[i]):
                block.append(lines[i]); i += 1
            i += 1
            ops.append({'insert': '\n'.join(block) + '\n',
                        'attributes': {'code-block': True}})
            continue
        m = _MD_HR_RE.match(line.strip())
        if m:
            style = 1
            cm = re.match(r'divider-(\d+)', m.group(1) or '')
            if cm:
                style = int(cm.group(1))
            ops.append({'insert': {'divider': {'style': style}}})
            ops.append({'insert': '\n'})
            i += 1; continue
        if line.strip() in ('---', '***', '___'):
            ops.append({'insert': '\n'})       # 水平线：Quill 里退化为空行
            i += 1; continue
        # GFM 表格：表头 + 分隔行 + 数据行 → Quill 表格属性行
        if ('|' in line and i + 1 < len(lines) and _MD_TABLE_SEP.match(lines[i + 1])):
            header = _md_split_table_row(line)
            i += 2
            rows = [header]
            while i < len(lines) and '|' in lines[i] and lines[i].strip():
                rows.append(_md_split_table_row(lines[i]))
                i += 1
            import uuid as _uuid
            for row in rows:
                row_id = _uuid.uuid4().hex[:8]
                for c, cell in enumerate(row):
                    cell_id = '%s-%d' % (row_id, c)
                    ops.extend(_md_parse_inline(cell))
                    ops.append({'insert': '\n', 'attributes': {
                        'table': row_id, 'table-cell': cell_id, 'table-cell-line': 'last'}})
            continue
        m = _MD_MATH_BLOCK_RE.match(line.strip())
        if m:
            ops.append({'insert': {'math-formula': {'latex': m.group(1).strip(), 'display': True}}})
            ops.append({'insert': '\n'})
            i += 1; continue
        m = _MD_HEADING.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(2)))
            ops.append({'insert': '\n', 'attributes': {'header': len(m.group(1))}})
            i += 1; continue
        m = _MD_TODO.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(2)))
            ops.append({'insert': '\n',
                        'attributes': {'list': 'checked' if m.group(1).lower() == 'x' else 'unchecked'}})
            i += 1; continue
        m = _MD_UL.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(1)))
            ops.append({'insert': '\n', 'attributes': {'list': 'bullet'}})
            i += 1; continue
        m = _MD_OL.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(1)))
            ops.append({'insert': '\n', 'attributes': {'list': 'ordered'}})
            i += 1; continue
        m = _MD_QUOTE.match(line)
        if m:
            ops.extend(_md_parse_inline(m.group(1)))
            ops.append({'insert': '\n', 'attributes': {'blockquote': True}})
            i += 1; continue
        if line.strip():
            ops.extend(_md_parse_inline(line))
        ops.append({'insert': '\n'})
        i += 1
    return json.dumps({'ops': ops}, ensure_ascii=False)


def backup_database():
    """启动时调用（app.pyw 后台线程）：距最新备份 >24h 才备份，保留最近 7 份。

    用 sqlite3 backup API 而非文件复制：DELETE journal 模式写入瞬间有 -journal
    残留，直接 copy 可能撕裂；backup API 页级一致且遇写入自动重启。
    返回备份文件路径；未到期或失败返回 None。

    写盘顺序：先写 `.part` → 校验 → os.replace 成正式名。为什么不直接写正式名：
    `sqlite3.connect(dest)` **在 backup 之前就把文件建出来了**，backup 一旦抛错就留下一个
    0 字节的 `notes-<现在>.db`。它的名字最新、mtime 是"现在"，于是接下来 24h 都不会再备份，
    滚动裁剪时还会把最旧的一份**好**备份挤掉；恢复工具那边也会把它当候选（见 restore.py）。
    """
    try:
        if not os.path.exists(DB_PATH):
            return None
        os.makedirs(BACKUP_DIR, exist_ok=True)
        existing = sorted(
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith('notes-') and f.endswith('.db')
        )
        if existing:
            latest = os.path.join(BACKUP_DIR, existing[-1])
            if (datetime.now().timestamp() - os.path.getmtime(latest)) < 24 * 3600:
                return None  # 24h 内已有备份
        dest = os.path.join(BACKUP_DIR, datetime.now().strftime('notes-%Y%m%d-%H%M%S.db'))
        tmp = dest + '.part'
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
            src = sqlite3.connect(DB_PATH)
            dst = sqlite3.connect(tmp)
            try:
                with dst:
                    src.backup(dst)
            finally:
                src.close()
                dst.close()
            # 校验过才改名：宁可这次没备份，也不要留个"看起来最新"的坏备份
            if not check_integrity(tmp):
                raise OSError('备份完整性校验失败')
            if count_notes(tmp) is None:
                raise OSError('备份里读不出笔记数')
            os.replace(tmp, dest)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            raise
        # 滚动保留最近 7 份（文件名含时间戳，字典序即时间序）
        all_backups = sorted(
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith('notes-') and f.endswith('.db') and not f.endswith('.part')
        )
        for old in all_backups[:-7]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except OSError:
                pass
        return dest
    except Exception:
        applog.get_logger().exception("数据库备份失败")
        return None

def check_integrity(db_path=DB_PATH):
    """独立连接跑 PRAGMA integrity_check；损坏返回 False。启动自检用，绝不进模块级执行。"""
    try:
        c = sqlite3.connect(db_path)
        try:
            return c.execute("PRAGMA integrity_check").fetchone()[0] == 'ok'
        finally:
            c.close()
    except Exception:
        return False


def count_notes(db_path):
    """只读数一下某个库里有几篇笔记；文件不存在/打不开返回 None。

    必须用 `mode=ro` URI：`sqlite3.connect` 会顺手创建空文件——探测别的数据目录
    不该留下任何副作用（启动提示用它比较两份数据的笔记数）。
    """
    if not db_path or not os.path.exists(db_path):
        return None
    try:
        c = sqlite3.connect('file:%s?mode=ro' % db_path.replace('?', '%3f'), uri=True)
        try:
            return c.execute(
                "SELECT COUNT(*) FROM notes WHERE deleted_at IS NULL").fetchone()[0]
        finally:
            c.close()
    except Exception:
        return None


def space_stats(db_path=None):
    """返回 (page_size, page_count, freelist_count, 空闲页字节数, 空闲占比)。

    空闲页（freelist）是 SQLite 删除/改写大字段后**不会自动归还文件系统**的页。
    本应用有两个持续制造空闲页的来源：
      1) 图片外置迁移——把正文里的 base64 挪到 attachments/，文本从 MB 级缩到几十字节；
      2) 历史版本快照反复写入再按 50 条上限删除。
    实测某库：文件 12.02 MB，其中空闲页 3046 页 = 11.90 MB（占 99%），有效数据仅 0.12 MB。
    """
    path = db_path or DB_PATH
    if not os.path.exists(path):
        return None   # 必须先判断：sqlite3.connect 会顺手创建空文件，不能让它有副作用
    try:
        c = sqlite3.connect(path)
        try:
            page_size = c.execute("PRAGMA page_size").fetchone()[0]
            page_count = c.execute("PRAGMA page_count").fetchone()[0]
            freelist = c.execute("PRAGMA freelist_count").fetchone()[0]
        finally:
            c.close()
    except Exception:
        return None
    free_bytes = freelist * page_size
    ratio = (freelist / page_count) if page_count else 0.0
    return page_size, page_count, freelist, free_bytes, ratio


def reclaim_space(threshold=0.30, db_path=None):
    """空闲页占比超过 threshold 时 VACUUM 回收磁盘空间（启动维护线程调用）。

    调用时机在 backup_database() **之前**：先有一份页级一致的备份兜底，再压缩。
    VACUUM 需要重写整个库，必须独占——所以独立连接 + 全局锁。
    返回 (是否执行, 执行前字节, 执行后字节)；无需回收或失败返回 (False, size, size)。
    """
    path = db_path or DB_PATH
    if not os.path.exists(path):
        return False, 0, 0
    before = os.path.getsize(path)
    st = space_stats(path)
    if st is None:
        return False, before, before
    _ps, _pc, _fl, _free, ratio = st
    if ratio < threshold:
        return False, before, before

    with _db_lock:
        try:
            c = sqlite3.connect(path)
            try:
                c.execute("PRAGMA foreign_keys=OFF")
                c.execute("VACUUM")          # 触发隐式提交；若被事务挡住则 repair 分支处理
                c.commit()
            finally:
                c.close()
        except Exception:
            # 「VACUUM cannot run from within a transaction」兜底：用 isolation_level=None
            # 的连接（无隐式事务）再试一次。修复连接泄漏（try/finally 释放）。
            applog.get_logger().exception("VACUUM 失败，尝试 autocommit 重试")
            try:
                c2 = sqlite3.connect(path, isolation_level=None)
                try:
                    c2.execute("VACUUM")
                finally:
                    c2.close()
            except Exception:
                applog.get_logger().exception("数据库空间回收失败")
                return False, before, before
    after = os.path.getsize(path)
    try:
        applog.get_logger().info(
            "数据库空间回收：%.1f MB -> %.1f MB（空闲页占比 %.0f%%）",
            before / 1048576, after / 1048576, ratio * 100)
    except Exception:
        pass
    return True, before, after


# ====== FTS5 全文搜索（trigram，中文可用） ======
def _probe_fts5():
    """探测当前 sqlite3 是否支持 FTS5 + trigram（打包后的 DLL 可能不同，运行时判定）"""
    try:
        c = sqlite3.connect(':memory:')
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
        c.close()
        return True
    except Exception:
        return False

FTS_AVAILABLE = _probe_fts5()
FTS_VERSION = '1'   # 索引结构版本：变更时改此值触发全量重建

def _delta_to_text(content):
    """Quill Delta JSON → 纯文本（搜索索引用）；解析失败回退原串；截断 100KB"""
    if not content:
        return ''
    try:
        data = json.loads(content)
        ops = data.get('ops', []) if isinstance(data, dict) else data
        parts = []
        for op in ops:
            ins = op.get('insert') if isinstance(op, dict) else None
            if isinstance(ins, str):
                parts.append(ins)   # 跳过图片等 embed dict
        text = ''.join(parts)
    except Exception:
        text = content
    return text[:100_000]

_MD_SCRIPT = re.compile(r'<(script|style)\b.*?</\1>', re.I | re.S)
_MD_IMAGE = re.compile(r'!\[([^\]]*)\]\([^)]*\)')
_MD_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')
_MD_TAG = re.compile(r'<[^>]+>')
_MD_INLINE = re.compile(r'`{1,3}|~{2}|\*{1,3}')
# 行内标记**直接删掉**，不能替换成空格：`这是**正文**内容` 若变成「这是正文 内容」，
# 用户搜「正文内容」就永远搜不到（FTS trigram 匹配的是连续子串）。
# 下划线故意不处理：CommonMark 里词内下划线本就不是强调（`my_var` 不该变成 `myvar`）。
_MD_BLOCK = re.compile(
    r'^\s*>+\s*'                               # 引用
    r'|^\s*[-*+]\s+(\[[ xX]\]\s*)?'            # 列表 / 待办
    r'|^\s*\d+[.)]\s+'                         # 有序列表
    r'|^#{1,6}\s+', re.M)                      # 标题
_MD_ENTITIES = (('&nbsp;', ' '), ('&lt;', '<'), ('&gt;', '>'),
                ('&quot;', '"'), ('&#39;', "'"), ('&amp;', '&'))


def _markdown_to_text(md):
    """Markdown → 纯文本（搜索索引 / 列表摘要用）。

    顺序有讲究：先摘掉 script/style 整段，再把图片/链接换成它们的文字，最后才去标签——
    反过来会把 `<img alt="说明">` 的尖括号内容切成半截。
    """
    if not md:
        return ''
    # front-matter（属性）不是正文：摘要、FTS、字数都不该带上它。
    # 注意 _FM_RE 定义在下面一点（函数体里引用模块级名字，运行时才解析，没问题）。
    md = _FM_RE.sub('', md, count=1)
    text = _MD_SCRIPT.sub(' ', md)
    text = _MD_IMAGE.sub(lambda m: m.group(1), text)
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = _MD_TAG.sub(' ', text)              # 白名单内嵌 HTML（span/u/hr…）只留文字
    text = _MD_INLINE.sub('', text)            # 行内标记不留空格（否则会切断连续子串）
    text = _MD_BLOCK.sub(' ', text)            # 块级前缀换成空格，避免把两行粘成一个词
    for ent, ch in _MD_ENTITIES:               # &amp; 必须最后解，否则 &amp;lt; 会解成 <
        text = text.replace(ent, ch)
    return text[:100_000]


def note_plain_text(content, fmt='delta'):
    """按 format 取正文纯文本 —— 所有需要「读正文」的地方都走这里，别再各自判断"""
    return _markdown_to_text(content) if fmt == 'md' else _delta_to_text(content)


PREVIEW_MAX = 120      # 列表摘要最长字符数（列表里只有一行，再多也是被省略号截掉）

def _preview_text(content, limit=PREVIEW_MAX, fmt='delta'):
    """正文 → 单行摘要（列表显示用）。Markdown 笔记剥掉标记，Delta 笔记走原逻辑。

    与 _delta_to_text 的区别：解析失败返回**空串**而不是原始 JSON——摘要位置显示一坨
    `{"ops":[...` 比不显示更糟。取够 limit*2 个字符就停，不必遍历全部 ops。
    """
    if not content:
        return ''
    if fmt == 'md':
        return ' '.join(_markdown_to_text(content).split())[:limit]
    try:
        data = json.loads(content)
    except Exception:
        return ''
    ops = data.get('ops', []) if isinstance(data, dict) else data
    if not isinstance(ops, list):
        return ''
    parts, total = [], 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        ins = op.get('insert')
        if isinstance(ins, str):
            parts.append(ins)
            total += len(ins)
            if total >= limit * 2:
                break
    return ' '.join(''.join(parts).split())[:limit]   # 折叠换行与连续空白

def _fts_sync(note_id, title, plain_content, fmt='delta'):
    """重写单条 FTS 行。plain_content=None（加密笔记）时 body 恒空——索引绝不落加密明文。"""
    if not FTS_AVAILABLE:
        return
    try:
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
        body = note_plain_text(plain_content, fmt) if plain_content is not None else ''
        conn.execute("INSERT INTO notes_fts (note_id, title, body) VALUES (?, ?, ?)",
                     (note_id, title or '', body))
    except Exception:
        applog.get_logger().exception("FTS 同步失败")

def _fts_sync_from_row(note_id):
    """从 notes 表当前行重建 FTS 行（加密笔记 body 空，明文笔记提取正文）"""
    row = conn.execute(
        "SELECT title, content, password_hash, format FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    if not row:
        return
    _fts_sync(note_id, row['title'], None if row['password_hash'] else row['content'],
              row['format'] or 'delta')

def _fts_delete(note_id):
    if not FTS_AVAILABLE:
        return
    try:
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
    except Exception:
        pass

# 建表 + 版本门控全量回填
if FTS_AVAILABLE:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts "
            "USING fts5(note_id UNINDEXED, title, body, tokenize='trigram')")
        _ver = conn.execute("SELECT value FROM settings WHERE key='fts_version'").fetchone()
        if not _ver or _ver['value'] != FTS_VERSION:
            conn.execute("DELETE FROM notes_fts")
            # 双保险：软删笔记的 FTS 行先清掉（防止版本号变更时残留）
            conn.execute("DELETE FROM notes_fts WHERE note_id IN (SELECT id FROM notes WHERE deleted_at IS NOT NULL)")
            for _r in conn.execute("SELECT id, title, content, password_hash FROM notes WHERE deleted_at IS NULL").fetchall():
                _body = '' if _r['password_hash'] else _delta_to_text(_r['content'])
                conn.execute("INSERT INTO notes_fts (note_id, title, body) VALUES (?, ?, ?)",
                             (_r['id'], _r['title'] or '', _body))
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fts_version', ?)",
                         (FTS_VERSION,))
        conn.commit()
    except Exception:
        applog.get_logger().exception("FTS 初始化失败，降级为 LIKE 搜索")
        FTS_AVAILABLE = False


# ====== 派生索引：一次解析算出所有"可查询侧面" ======
# 格式判据只有 notes.format；md 走行扫描，delta 走 ops 遍历，两者产出口径必须一致
# （测试里用"同一篇内容两种格式算出的指标应相同"来锁死这一点）。
_TODO_RE = re.compile(r'^\s*[-*+]\s+\[([ xX])\]\s+(.*)$')
# 待办日期标记：Obsidian Tasks 的 `📅 2026-09-25`，以及第 12 轮加的 ASCII 别名 `@2026-09-25`。
# 为什么要别名：界面提示里教用户"照抄 📅"等于在 UI 里塞一个 emoji（本项目 UI 图标一律 SVG），
# 可它又确实是语法本体、换成图标就没法照抄了 —— 别名让提示可以完全不带 emoji。
# 两种写法共用这一条正则（解析、剥离、勾选写回的文本校验全都走它），口径不会分叉。
_DUE_RE = re.compile(r'(?:📅|@)\s*(\d{4}-\d{2}-\d{2})')
_LINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]*)?\]\]')
_FM_RE = re.compile(r'^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)', re.S)
# '2'：第 9 轮加入 note_links（双链索引）→ 版本号一变，启动时会为存量笔记重建索引
# '3'：列表摘要改为保存时预计算（note_derived.preview）→ 存量笔记需要回填一次
DERIVED_VERSION = '3'
WORD_RE = re.compile(r'[\u4e00-\u9fff]|[A-Za-z0-9_]+')


def count_words(text):
    """混合中英文字数：中日韩字符按字计，拉丁/数字按词计（与主流编辑器的口径接近）"""
    return len(WORD_RE.findall(text or ''))


def _unquote(value):
    """去掉一层成对引号（YAML 值里最常见的修饰）"""
    v = (value or '').strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        return v[1:-1]
    return v


def parse_front_matter(md_text):
    """极简 YAML front-matter 解析（只认 `key: value` 与 `- 列表`）。

    第 9 轮做完整属性系统时会扩展这里；现在先把 props_json 填上，表格视图/筛选才有的用。
    """
    m = _FM_RE.match(md_text or '')
    if not m:
        return {}
    props, key = {}, None
    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith('#'):
            continue
        if raw.lstrip().startswith('- ') and key:
            props.setdefault(key, [])
            if isinstance(props[key], list):
                props[key].append(_unquote(raw.lstrip()[2:].strip()))
            continue
        if ':' not in raw:
            continue
        k, _, v = raw.partition(':')
        key = k.strip()
        v = v.strip()
        if not v:
            props[key] = []          # 可能是块式列表，下一行开始收集
        elif v.startswith('[') and v.endswith(']'):
            props[key] = [_unquote(x) for x in v[1:-1].split(',') if x.strip()]
        else:
            props[key] = v.strip('"\'')
    return props


# ====== 双链 [[标题#小节|别名]]（第 9 轮）======
# 单独一条正则而不是复用 _LINK_RE：那条只管计数，口径已被第 7 轮的搜索测试锁死；
# 这里要把 标题 / 小节 / 别名 三段拆开，改 _LINK_RE 等于顺手改掉别人的口径。
_WIKILINK_RE = re.compile(r'\[\[([^\]\|#]*)(?:#([^\]\|]+))?(?:\|([^\]]*))?\]\]')


def extract_links(content, fmt):
    """正文 → [(序号, 标题原文, 标题归一, 小节, 别名)]（双链索引的数据源）。

    走 note_plain_text 而不是原始正文：md 与 delta 用同一条口径（与 link_count 一致）。
    `[[#小节]]`（同篇内跳转）标题为空 → target_key 记空串，反向链接查询会跳过它。
    """
    out = []
    for m in _WIKILINK_RE.finditer(note_plain_text(content, fmt)):
        raw = (m.group(1) or '').strip()
        heading = (m.group(2) or '').strip() or None
        alias = (m.group(3) or '').strip() or None
        if not raw and not heading:
            continue
        out.append((len(out), raw, raw.lower(), heading, alias))
    return out


def _link_context(content, fmt, title, heading, span=42):
    """反向链接的上下文片段（命中处 ±span 字，换行压成空格）。

    读的是**明文**正文；加密笔记由调用方挡住（根本读不到正文）。
    """
    plain = note_plain_text(content, fmt)
    needle = '[[' + (title or '') + (('#' + heading) if heading else '')
    at = plain.find(needle)
    if at < 0:
        return ''
    start, end = max(0, at - span), min(len(plain), at + len(needle) + span)
    body = plain[start:end].replace('\n', ' ').strip()
    return ('…' if start else '') + body + ('…' if end < len(plain) else '')


# ====== 属性（front-matter）查询（第 9 轮）======
def _prop_norm(key):
    return (key or '').strip().lower()


def _load_props(props_json):
    try:
        data = json.loads(props_json or '{}')
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _prop_match(props, key, op, val):
    """单个属性条件。键名大小写不敏感；值是列表时"任一项命中"即算命中。

    op 为 None 表示只要求键存在（`prop:状态`）。比较运算在"两边都能转成数字"时按数字，
    否则按字符串——ISO 日期的字典序就是时间序，所以 `prop:截止>2026-10-01` 天然成立。
    """
    if not props:
        return False
    target = None
    for k, v in props.items():
        if _prop_norm(k) == _prop_norm(key):
            target = v
            break
    else:
        return False
    if op is None:
        return True
    values = target if isinstance(target, list) else [target]
    if op == '~':
        return any(val.lower() in str(v).lower() for v in values)
    if op == '=':
        return any(str(v).strip().lower() == val.lower() for v in values)
    for v in values:
        a, b = str(v).strip(), val
        try:
            x, y = float(a), float(b)
        except ValueError:
            x, y = a, b
        if op == '>' and x > y:
            return True
        if op == '<' and x < y:
            return True
        if op == '>=' and x >= y:
            return True
        if op == '<=' and x <= y:
            return True
    return False


def _filter_props(ids, conds):
    """属性后置过滤（与 `-排除词` 同一条路：候选已被范围收窄，且不必依赖 SQLite 的 JSON1
    扩展与键名转义）。

    加密 / 读不到属性的笔记：**含正向条件时会被排除**（它确实无法满足），
    **只有取反条件时保留**（我们无法证明它违反，宁可不排除也不误删——同排除词的政策）。
    """
    if not conds or not ids:
        return ids
    props = {}
    for chunk in _chunked(ids):          # 分块：ids 可能上千，SQLite 变量数有上限
        marks = ','.join('?' * len(chunk))
        for r in conn.execute(
                f"SELECT note_id, props_json FROM note_derived WHERE note_id IN ({marks})",
                list(chunk)):
            props[r['note_id']] = _load_props(r['props_json'])
    has_positive = any(not negate for _k, _o, _v, negate in conds)
    out = []
    for i in ids:
        p = props.get(i)
        if p is None or (not p and not has_positive):
            out.append(i)
            continue
        ok = True
        for key, op, val, negate in conds:
            hit = _prop_match(p, key, op, val)
            if negate:
                hit = not hit
            if not hit:
                ok = False
                break
        if ok:
            out.append(i)
    return out


# ====== 模板与快速捕获（第 10 轮）======
WEEKDAY_CN = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')
TEMPLATE_VAR_RE = re.compile(r'\{\{\s*(date|time|weekday|title|datetime)\s*\}\}')
DAILY_NOTEBOOK = '日记'
INBOX_NOTEBOOK = '收件箱'


def render_template(text, title=''):
    """把模板里的变量换掉（只认这几个，不认识的 `{{...}}` 原样留着）。

    为什么不做通用模板引擎：模板是用户手写的 Markdown，语法越少越不容易和正文打架；
    替换只在"用模板建笔记"的那一刻发生一次，之后就是普通正文。
    """
    now = datetime.now()
    values = {
        'date': now.strftime('%Y-%m-%d'),
        'time': now.strftime('%H:%M'),
        'weekday': WEEKDAY_CN[now.weekday()],
        'datetime': now.strftime('%Y-%m-%d %H:%M'),
        'title': title or '',
    }
    return TEMPLATE_VAR_RE.sub(lambda m: values.get(m.group(1), m.group(0)), text or '')


def _find_or_create_notebook(name):
    """按名字找笔记本，没有就建一个（每日笔记/收件箱都靠它保证幂等）"""
    row = conn.execute("SELECT id FROM notebooks WHERE name = ?", (name,)).fetchone()
    if row:
        return row['id']
    nid = str(uuid.uuid4())
    conn.execute("INSERT INTO notebooks (id, name, sort_order) VALUES (?,?,?)",
                 (nid, name, _next_sort_order('notebooks')))
    return nid


def _first_line_title(text, limit=50):
    """捕获内容 → 标题：取第一行非空文本，太长就截断（列表里显示得下）"""
    for line in (text or '').splitlines():
        s = line.strip().lstrip('#').strip()
        if s:
            return s[:limit] + ('…' if len(s) > limit else '')
    return '未命名笔记'


def _todo_lines_md(md_text):
    """Markdown → [(idx, text, due, done)]"""
    out = []
    for line in (md_text or '').splitlines():
        m = _TODO_RE.match(line)
        if not m:
            continue
        body = m.group(2).strip()
        dm = _DUE_RE.search(body)
        due = dm.group(1) if dm else None
        text = _DUE_RE.sub('', body).strip()
        out.append((len(out), text, due, 1 if m.group(1).lower() == 'x' else 0))
    return out


def _todo_lines_delta(content):
    """Quill Delta → [(idx, text, due, done)]（行属性挂在含换行符的 op 上）"""
    try:
        data = json.loads(content or '{}')
    except Exception:
        return []
    ops = data.get('ops', []) if isinstance(data, dict) else data
    if not isinstance(ops, list):
        return []
    lines, buf = [], []
    for op in ops:
        if not isinstance(op, dict):
            continue
        ins, attrs = op.get('insert'), op.get('attributes') or {}
        if not isinstance(ins, str):
            continue
        parts = ins.split('\n')
        for i, part in enumerate(parts):
            buf.append(part)
            if i < len(parts) - 1:
                lines.append((''.join(buf), attrs))
                buf = []
    out = []
    for text, attrs in lines:
        lst = attrs.get('list')
        if lst not in ('checked', 'unchecked'):
            continue
        body = text.strip()
        dm = _DUE_RE.search(body)
        due = dm.group(1) if dm else None
        out.append((len(out), _DUE_RE.sub('', body).strip(), due, 1 if lst == 'checked' else 0))
    return out


def derive_metrics(content, fmt, note_id=None):
    """正文 → 派生指标（不含附件/提醒这两个要查表的字段，由 _refresh_derived 补）"""
    plain = note_plain_text(content, fmt)
    # 统计前去掉日期标记（📅 或 @）：否则 md（剥标记）与 delta（保留标记）算出的字数会不一致
    plain_for_count = _DUE_RE.sub('', plain)
    todos = _todo_lines_md(content) if fmt == 'md' else _todo_lines_delta(content)
    open_todos = [t for t in todos if not t[3]]
    dues = sorted(t[2] for t in open_todos if t[2])
    has_formula = ('$' in (content or '')) if fmt == 'md' else ('math-formula' in (content or ''))
    has_code = ('```' in (content or '')) if fmt == 'md' else ('code-block' in (content or ''))
    return {
        'word_count': count_words(plain_for_count),
        # 字符数**不含空白**：否则 md 里被剥掉的 `- [ ] ` 标记会留下空格，
        # 与同内容的 delta 笔记算出的字符数对不上（两种格式的口径必须统一）
        'char_count': len(re.sub(r'\s+', '', plain_for_count)),
        'todo_total': len(todos),
        'todo_open': len(open_todos),
        'todo_next_due': dues[0] if dues else None,
        'has_formula': 1 if has_formula else 0,
        'has_code': 1 if has_code else 0,
        'link_count': len(_LINK_RE.findall(plain)),
        'props_json': json.dumps(parse_front_matter(content) if fmt == 'md' else {},
                                 ensure_ascii=False),
        # 列表摘要也在这里算：列表每次刷新都要给**每一篇**算一行摘要，而 _markdown_to_text
        # 是六趟正则扫全文 —— 800 篇 2.7KB 的库实测光算摘就要 223ms（占 notes_list 总耗时 74%）。
        # 保存时算一次（内容已经在手上），列表就只是读一列。
        'preview': _preview_text(content, fmt=fmt),
        'todos': todos,
    }


def _refresh_derived(note_id, content=None, fmt=None):
    """重算并落库一篇笔记的派生指标 + 待办明细（保存链路里调用）。

    加密笔记**从不**参与派生：待办明细 / 字数 / 属性 / 摘要放进索引就等于把明文侧面落库，
    与「FTS body 恒空」是同一条隐私约定（解锁与否都不例外）。

    ⚠️ 密码判定必须在这里、在入口处做，**不能**只放在"自己去读 content"的分支里。
    历史教训：原先只有 `content is None` 那条分支查 `password_hash`，而
    `convert_note_format` / `todo_toggle` 会把**解密后的明文**显式传进来（它们本来
    就在解锁态才跑得动），于是整段守卫被跳过 —— 加密笔记的明文待办与属性照样写进
    note_todos / note_derived，之后锁定甚至重启都能从 todos_list / metrics_bulk 读出，
    搜索还能拿 has:prop / todo:open 当"内容存在性"预言机（实测复现）。
    所以：只要这行有 password_hash，无论调用方传了什么，一律按空内容处理。
    """
    try:
        row = conn.execute("SELECT content, format, password_hash FROM notes WHERE id = ?",
                           (note_id,)).fetchone()
        if not row:
            return
        if row['password_hash']:
            content, fmt = '', row['format'] or 'delta'
        elif content is None or fmt is None:
            content, fmt = row['content'], row['format'] or 'delta'
        metrics = derive_metrics(content, fmt, note_id)
        preview = metrics.pop('preview')
        todos = metrics.pop('todos')
        has_att = 1 if conn.execute("SELECT 1 FROM attachments WHERE note_id = ? LIMIT 1",
                                    (note_id,)).fetchone() else 0
        has_rem = 1 if conn.execute("SELECT 1 FROM reminders WHERE note_id = ? LIMIT 1",
                                    (note_id,)).fetchone() else 0
        conn.execute(
            "INSERT OR REPLACE INTO note_derived (note_id, word_count, char_count, todo_total, "
            "todo_open, todo_next_due, has_attachment, has_reminder, has_formula, has_code, "
            "link_count, props_json, preview, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, datetime('now','localtime'))",
            (note_id, metrics['word_count'], metrics['char_count'], metrics['todo_total'],
             metrics['todo_open'], metrics['todo_next_due'], has_att, has_rem,
             metrics['has_formula'], metrics['has_code'], metrics['link_count'],
             metrics['props_json'], preview))
        conn.execute("DELETE FROM note_todos WHERE note_id = ?", (note_id,))
        for idx, text, due, done in todos:
            conn.execute("INSERT INTO note_todos (note_id, idx, text, due, done) VALUES (?,?,?,?,?)",
                         (note_id, idx, text, due, done))
        # 双链索引与正文同事务重建；加密笔记 content 上面已被置空 → 行被删干净
        conn.execute("DELETE FROM note_links WHERE src_note_id = ?", (note_id,))
        for ordinal, raw, key, heading, alias in extract_links(content, fmt):
            conn.execute(
                "INSERT INTO note_links (src_note_id, ordinal, target_raw, target_key, heading, alias) "
                "VALUES (?,?,?,?,?,?)", (note_id, ordinal, raw, key, heading, alias))
    except Exception:
        applog.get_logger().exception("派生索引更新失败")   # 派生失败不能影响保存


def _derived_backfill():
    """启动回填（版本门控，与 FTS 回填同一套模式）"""
    try:
        ver = conn.execute("SELECT value FROM settings WHERE key='derived_version'").fetchone()
        if ver and ver['value'] == DERIVED_VERSION:
            return
        # 不传 content/format，让 _refresh_derived 自己去读那一行 —— 它必须亲自查
        # password_hash 才能保证加密笔记不落明文侧面（见该函数的说明）。
        for r in conn.execute("SELECT id FROM notes WHERE deleted_at IS NULL").fetchall():
            _refresh_derived(r['id'])
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('derived_version', ?)",
                     (DERIVED_VERSION,))
        conn.commit()
    except Exception:
        applog.get_logger().exception("派生索引回填失败")


def _purge_encrypted_derived_leaks():
    """清掉加密笔记残留的派生明文（修复前的旁路留下的脏行）。

    为什么不能只靠"保存时清空"：那只能保证**将来**不写进去，存量库里的明文待办/属性/
    摘要会一直躺在 note_todos / note_derived 里，用户锁定或重启后照样能读出来。
    每次启动跑一遍，代价是两条带索引的 UPDATE，不扫描正文。
    """
    try:
        cur = conn.execute(
            "DELETE FROM note_todos WHERE note_id IN "
            "(SELECT id FROM notes WHERE COALESCE(password_hash, '') <> '')")
        n_todos = cur.rowcount
        cur = conn.execute(
            "UPDATE note_derived SET word_count = 0, char_count = 0, todo_total = 0, "
            "todo_open = 0, todo_next_due = NULL, has_formula = 0, has_code = 0, "
            "link_count = 0, props_json = '{}', preview = '' "
            "WHERE note_id IN (SELECT id FROM notes WHERE COALESCE(password_hash, '') <> '')")
        n_derived = cur.rowcount
        conn.commit()
        if n_todos or n_derived:
            applog.get_logger().warning(
                "清理加密笔记的派生明文残留：%d 条待办、%d 行指标", n_todos, n_derived)
    except Exception:
        applog.get_logger().exception("清理加密笔记派生残留失败")


_derived_backfill()
_purge_encrypted_derived_leaks()

# ====== 图片外置迁移（base64 内嵌 → attachments 引用） ======
# 幂等三重保障：LIKE 预筛（已 dict 化行不命中）+ 确定性 sha 文件名 + 失败保原串下次重试；
# 写库前先做 premig- 快照（不参与 notes-* 滚动保留），一键回滚凭据。
# 加密笔记 content 是 encv1: 密文，LIKE 天然不命中自动跳过（文档化：加密笔记图片暂不外置）。

def _snapshot_db(prefix):
    """sqlite backup API 快照，命名 <prefix><ts>.db（不参与 7 份滚动保留）"""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        dest = os.path.join(BACKUP_DIR, f"{prefix}{datetime.now():%Y%m%d-%H%M%S}.db")
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        return dest
    except Exception:
        applog.get_logger().exception("迁移快照失败")
        return None


def _externalize_image(note_id, data_uri):
    """data URI → attachments 文件 + 行。幂等：确定性文件名（sha256 前 12 位），存在即跳过。
    返回 (aid, fname, dest) 或 None（失败保留原串，下次重试）"""
    try:
        header, b64 = data_uri.split(',', 1)
        mime = header[len('data:'):-len(';base64')]
        ext_map = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif',
                   'image/webp': '.webp', 'image/bmp': '.bmp', 'image/svg+xml': '.svg'}
        ext = ext_map.get(mime, '.png')
        raw = base64.b64decode(b64)
        if not raw:
            return None
        fname = f"img_{hashlib.sha256(raw).hexdigest()[:12]}{ext}"
        # note_id 也算信任边界之外的值（它来自笔记行；而"解压外部导出的 zip 即可当库打开"
        # 是本项目的用法之一 —— 伪造的库里放一行 id 带 `..` 的笔记就能把文件写到别处）。
        note_dir = safe_join(ATTACH_DIR, note_id)
        if not note_dir:
            return None
        dest = safe_join(ATTACH_DIR, note_id, fname)
        if not dest:
            return None
        if not os.path.isfile(dest):
            os.makedirs(note_dir, exist_ok=True)
            with open(dest, 'wb') as f:
                f.write(raw)
        row = conn.execute("SELECT id FROM attachments WHERE note_id=? AND filename=?",
                           (note_id, fname)).fetchone()
        if row:
            aid = row['id']
        else:
            aid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO attachments (id, note_id, filename, original_name, file_size, mime_type, type) "
                "VALUES (?,?,?,?,?,?, 'image')",
                (aid, note_id, fname, fname, len(raw), mime))
        return aid, fname, dest
    except Exception:
        applog.get_logger().exception("图片外置失败")
        return None


def migrate_images():
    """存量 Delta 图片外置（notes + versions）。返回迁移行数；无变更返回 0（不产生快照）。"""
    with _db_lock:
        targets = []
        for tbl in ('notes', 'versions'):
            try:
                if tbl == 'notes':
                    rows = conn.execute(
                        "SELECT id, content FROM notes WHERE content LIKE '%data:image%'").fetchall()
                else:
                    # versions 行需要同时取 note_id（附件归属笔记而非版本）
                    rows = conn.execute(
                        "SELECT id, note_id, content FROM versions WHERE content LIKE '%data:image%'").fetchall()
            except Exception:
                continue
            for row in rows:
                try:
                    delta = json.loads(row['content'])
                except Exception:
                    continue  # HTML/旧格式：跳过
                if not isinstance(delta, dict) or not isinstance(delta.get('ops'), list):
                    continue
                note_id = row['note_id'] if tbl == 'versions' else row['id']
                new_ops, changed = [], False
                for op in delta['ops']:
                    ins = op.get('insert')
                    v = ins.get('image') if isinstance(ins, dict) else None
                    if isinstance(v, str) and v.startswith('data:image'):
                        r = _externalize_image(note_id, v)
                        if r is None:
                            new_ops.append(op)  # 失败保底：保留原串，下次重试
                            continue
                        aid, fname, dest = r
                        new_ops.append({'insert': {'image': {'id': aid, 'filename': fname, 'storedPath': dest}}})
                        changed = True
                    else:
                        new_ops.append(op)
                if changed:
                    targets.append((tbl, row['id'], json.dumps({'ops': new_ops}, ensure_ascii=False)))
        if not targets:
            return 0
        # 快照先于写库：迁移不可逆，给一键回滚凭据
        _snapshot_db('premig-')
        for tbl, row_id, content in targets:
            conn.execute(f"UPDATE {tbl} SET content=? WHERE id=?", (content, row_id))
        conn.commit()
        return len(targets)


def _purge_note_files(note_id):
    """彻底删除前的磁盘清理：专属背景图副本 + 附件目录（与笔记同生死的文件）"""
    row = conn.execute("SELECT bg_value FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row and row['bg_value']:
        try:
            bg_dir = os.path.realpath(os.path.join(DATA_DIR, 'backgrounds'))
            real = os.path.realpath(row['bg_value'])
            if os.path.dirname(real) == bg_dir and os.path.isfile(real):
                os.remove(real)
        except OSError:
            pass
    # 删附件目录同样要校验：note_id 来自笔记行，而"解压外部导出的 zip 即可当库打开"是本项目
    # 的用法之一 —— 伪造的库里放一行 id 带 `..` 的回收站笔记，清空回收站就会 rmtree 到别处。
    note_attach = safe_join(ATTACH_DIR, note_id)
    if note_attach and os.path.exists(note_attach):
        shutil.rmtree(note_attach, ignore_errors=True)


def purge_expired_trash(days=30):
    """回收站超期清理：deleted_at 超过 days 天的笔记彻底删除（启动后台线程调用，带锁）"""
    with _db_lock:
        n = 0
        for r in conn.execute(
                "SELECT id FROM notes WHERE deleted_at IS NOT NULL AND "
                "deleted_at < datetime('now','localtime', ?)", (f'-{days} days',)).fetchall():
            _purge_note_files(r['id'])
            conn.execute("DELETE FROM notes WHERE id = ?", (r['id'],))
            _fts_delete(r['id'])
            _unlocked_deks.pop(r['id'], None)
            n += 1
        if n:
            conn.commit()
        return n


def _parse_remind_at(s):
    """解析提醒时间；容错旧数据缺秒格式，解析失败返回 None（调用方自行兜底）"""
    if not s:
        return None
    try:
        return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(s, '%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return None


# ====== 导出给前端的 API 类 ======
class Api:
    # ----- 笔记 -----
    def notes_list(self, notebook_id=None):
        """笔记列表。`notebook_id=None` = 全量；给了 id 就**只回该笔记本的笔记**（'' = 未分类）。

        为什么过滤要放在后端（第 12 轮）：笔记本筛选以前是前端「拉全量 → 覆盖 state.notes」，
        于是**任何一次刷新都会把筛选冲掉**（复制笔记 / 回收站恢复 / 待办勾选 / OCR 落库 /
        模板新建 … 20+ 处调用 loadNotes 的路径），现象就是"在原神里新建一篇，
        列表却变回全部笔记混在一起"。范围交给后端后，只要调用方带上同一个 notebook_id，
        列表就永远只有那一本，"混在一起"这类回归不可能再靠某处忘记重筛而复现。
        """
        # 排序：置顶 → 手动排序（sort_order，拖拽写入）→ 最近更新。新建笔记 sort_order=max+1，
        # 在 DESC 下自然排最前，与旧的「仅 updated_at」行为一致；未拖拽过的存量笔记 sort_order
        # 全为 0，退化为 updated_at DESC（兼容旧行为）。
        #
        # preview：列表里显示的一行正文摘要。摘要由保存链路预计算进 note_derived.preview，
        # 这里只读一列 —— **不把 content 从库里取出来**（既避免大 payload，也避免把加密笔记
        # 的密文送出去，更避免每次刷新都为每一篇重跑一遍 Markdown 剥标记：
        # 800 篇 2.7KB 的库实测那样会花 223ms，占本函数总耗时的 74%）。
        # 加密笔记（无论是否已解锁）一律给空串——列表渲染不参与解锁流程，摘要留给解锁后的编辑区。
        # LEFT JOIN + 兜底：派生行缺失（FTS/派生回填曾整体失败、或笔记是在旧版本里建的而回填
        # 还没轮到）时退回现算，绝不因为少一行就把摘要显示成空白。
        sql = ("SELECT n.id, n.title, n.bg_type, n.bg_value, n.bg_opacity, n.is_pinned, "
               "n.is_favorite, n.notebook_id, n.sort_order, n.created_at, n.updated_at, "
               "n.password_hash, n.format, d.preview AS preview "
               "FROM notes n LEFT JOIN note_derived d ON d.note_id = n.id "
               "WHERE n.deleted_at IS NULL")
        params = []
        if notebook_id is not None:
            if notebook_id == '':
                sql += " AND n.notebook_id IS NULL"
            else:
                sql += " AND n.notebook_id = ?"
                params.append(notebook_id)
        sql += " ORDER BY n.is_pinned DESC, n.sort_order DESC, n.updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        out = []
        missing = []          # 缺派生行、需要现算摘要的笔记（正常情况恒为空）
        for r in rows:
            d = dict(r)
            stored_preview = d.pop('preview', None)
            has_pwd = bool(d.pop('password_hash', None))
            d['has_password'] = 1 if has_pwd else 0
            if has_pwd:
                d['preview'] = ''      # 加密笔记一律空摘要（与 FTS body 同一条隐私约定）
            elif stored_preview is not None:
                # 注意判 `is not None` 而不是真值：派生行存在但摘要是空串，表示"这篇确实没有
                # 正文可摘要"（空笔记/纯图片笔记）—— 那时不该进兜底分支白算一遍。
                d['preview'] = stored_preview
            else:
                d['preview'] = ''
                missing.append(d)
            out.append(d)
        if missing:
            # 兜底：派生行缺失（派生回填曾整体失败、或笔记是旧版本建的而回填还没轮到）时现算。
            # **一次批量取回**而不是每篇一条 SQL —— 派生表整体为空时（800 篇实测）逐篇查会让
            # 本函数从 310ms 涨到 575ms，比优化前还慢。
            ids = [d['id'] for d in missing]
            by_id = {}
            for i in range(0, len(ids), 500):        # 分块：SQLite 变量数有上限
                chunk = ids[i:i + 500]
                marks = ','.join('?' * len(chunk))
                for fr in conn.execute(
                        "SELECT id, content, format FROM notes WHERE id IN (%s)" % marks, chunk):
                    by_id[fr['id']] = fr
            for d in missing:
                fr = by_id.get(d['id'])
                d['preview'] = _preview_text((fr['content'] if fr else '') or '',
                                             fmt=(fr['format'] if fr else 'delta') or 'delta')
        return out

    def notes_created_on(self, date_str):
        """某一天**创建**的笔记（跨笔记本，不受当前笔记本筛选影响），日历面板用它。

        为什么要单独一个查询：列表按笔记本过滤后，前端手里的 state.notes 只有当前那一本，
        靠它 filter(created_at.startsWith(日期)) 必然漏掉「日记」「收件箱」里的笔记 ——
        点日历会以为那天没写过，于是一遍遍重复建当天笔记。
        """
        rows = conn.execute(
            "SELECT id, title, notebook_id, created_at FROM notes "
            "WHERE deleted_at IS NULL AND substr(created_at, 1, 10) = ? "
            "ORDER BY created_at",
            (date_str,)).fetchall()
        return [dict(r) for r in rows]

    # ----- 正文格式转换（Delta ↔ Markdown，双轨的"搬家"通道）-----
    def _fmt_plain(self, content, fmt):
        return note_plain_text(content, fmt)

    @staticmethod
    def _is_subsequence(old_text, new_text):
        """旧正文的可见字符是否**按顺序**出现在新正文里（内容守恒校验）。

        为什么用子序列而不是相等：Markdown 会引入额外字符（表格分隔行、`📎` 前缀、
        HTML 标签的文字…），要求相等必然误报；而"一个字都不能少、顺序不能乱"恰好是
        转换必须保住的底线。空白一律忽略（缩进/换行在两种格式里本来就会变）。
        """
        old = [c for c in (old_text or '') if not c.isspace()]
        if not old:
            return True
        new = [c for c in (new_text or '') if not c.isspace()]
        it = iter(new)
        return all(any(c == n for n in it) for c in old)

    def note_format_info(self, note_id):
        """给前端决定"格式徽标"的文案：当前格式、是否有可还原的原始富文本、能否转换"""
        row = conn.execute(
            "SELECT format, delta_backup, password_hash FROM notes "
            "WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone()
        if not row:
            return None
        has_pwd = bool(row['password_hash'])
        locked = has_pwd and note_id not in _unlocked_deks
        return {
            'format': row['format'] or 'delta',
            'has_delta_backup': bool(row['delta_backup']),
            'encrypted': has_pwd,
            'locked': locked,
            'can_convert': not locked,
        }

    def convert_note_format(self, note_id, target):
        """把一篇笔记在 Delta ↔ Markdown 之间转换。

        安全设计（转换会改写正文，必须留足后路）：
          1. 先建**历史版本快照**（用户能在「历史版本」里回到转换前）；
          2. 转成 Markdown 时把原 Delta 存进 notes.delta_backup（可一键还原原始富文本）；
          3. **内容守恒校验**：原正文的可见字符必须按顺序出现在新正文里，否则拒绝转换、
             原样返回错误（宁可不让转，也不能悄悄丢字）；
          4. 单事务提交，异常回滚。

        返回 {'ok': True, ...} / {'ok': False, 'error': ...}
        """
        if target not in ('md', 'delta'):
            return {'ok': False, 'error': '目标格式不合法'}
        row = conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL",
                           (note_id,)).fetchone()
        if not row:
            return {'ok': False, 'error': '笔记不存在'}
        note = dict(row)
        cur = note.get('format') or 'delta'
        if cur == target:
            return {'ok': True, 'format': target, 'unchanged': True}
        dek = None
        if note.get('password_hash'):
            dek = _unlocked_deks.get(note_id)
            if dek is None:
                return {'ok': False, 'error': '加密笔记需先解锁再转换格式'}
            content = _decrypt_content(dek, note['content'] or '', note_id)
        else:
            content = note['content'] or ''
        if content.startswith(ENC_PREFIX) or content is None:
            return {'ok': False, 'error': '正文无法解密，拒绝转换'}

        try:
            if target == 'md':
                new_content = _delta_to_markdown(
                    content, note_id=note_id,
                    resolve_image=lambda name: 'attachments/%s/%s' % (note_id, name))
            else:
                new_content = markdown_to_delta(content)
        except Exception:
            applog.get_logger().exception('格式转换失败')
            return {'ok': False, 'error': '转换过程出错，笔记未改动'}

        old_plain = self._fmt_plain(content, cur)
        new_plain = self._fmt_plain(new_content, target)
        if not self._is_subsequence(old_plain, new_plain):
            return {'ok': False, 'error': '转换会丢内容，已中止（笔记未改动）'}

        self.versions_create(note_id, note['title'], content)   # 先留快照
        store = _encrypt_content(dek, new_content, note_id) if dek is not None else new_content
        backup = None
        if target == 'md':
            # 原 Delta 留一份（加密笔记同样用 DEK 加密，AAD 是 note_id，可直接拷回）
            backup = _encrypt_content(dek, content, note_id) if dek is not None else content
        else:
            # 转回 Delta 时**保留**原有的原始正文备份：那是唯一的无损回退路径，
            # 只有显式调用 restore_delta_backup 才清掉它（用户可能只是想"取回富文本"再看一眼）。
            backup = note.get('delta_backup')
        try:
            conn.execute(
                "UPDATE notes SET content = ?, format = ?, delta_backup = ?, "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (store, target, backup, note_id))
            conn.commit()
        except Exception:
            conn.rollback()
            applog.get_logger().exception('格式转换写库失败')
            return {'ok': False, 'error': '写库失败，笔记未改动'}
        _fts_sync_from_row(note_id)
        _refresh_derived(note_id, new_content, target)
        return {'ok': True, 'format': target,
                'plain_len': len(new_plain), 'backup_saved': backup is not None}

    def restore_delta_backup(self, note_id):
        """还原转换前的原始 Delta（无损回退）。没有备份时返回错误。"""
        row = conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL",
                           (note_id,)).fetchone()
        if not row:
            return {'ok': False, 'error': '笔记不存在'}
        note = dict(row)
        backup = note.get('delta_backup')
        if not backup:
            return {'ok': False, 'error': '这篇笔记没有可还原的原始富文本'}
        if note.get('password_hash') and note_id not in _unlocked_deks:
            return {'ok': False, 'error': '加密笔记需先解锁'}
        self.versions_create(note_id, note['title'], note['content'] or '')
        try:
            conn.execute(
                "UPDATE notes SET content = ?, format = 'delta', delta_backup = NULL, "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (backup, note_id))
            conn.commit()
        except Exception:
            conn.rollback()
            applog.get_logger().exception('还原原始富文本失败')
            return {'ok': False, 'error': '写库失败，笔记未改动'}
        _fts_sync_from_row(note_id)
        _refresh_derived(note_id)
        return {'ok': True, 'format': 'delta'}

    # notes_get 的字段投影。列表/滑杆这类调用方只要元数据，不该把整篇正文再传一遍。
    _NOTE_META_FIELDS = ('id', 'title', 'updated_at', 'created_at', 'format',
                         'has_password', 'is_encrypted', 'is_pinned', 'is_favorite',
                         'notebook_id', 'sort_order')

    def notes_get(self, note_id, unlocked=False, fields=None):
        """获取笔记详情。加密笔记仅当后端已解锁（DEK 在缓存）时返回明文内容。
        unlocked 参数保留兼容旧前端调用，但不再作为安全依据。回收站中的笔记返回 None。

        `fields`：只回这些列（外加 has_password / is_encrypted 两个状态位）。
        给它是因为**保存链路每次自动保存都会调本函数**（notes_update 末尾 return self.notes_get），
        默认返回里带着整篇 content —— 46KB 的笔记序列化出来 151KB，每 500ms 过一趟
        跨语言桥；拖动外观滑杆（不透明度/模糊/缩放/位置）更是每次移动都来一次，而前端
        **一处都没用到**这个返回值（18 个调用点全是 await 完就丢）。给投影就把这一趟省掉。
        fields=None 保持旧行为（全字段），既有调用方与测试不受影响。
        """
        r = conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone()
        if not r:
            return None
        note = dict(r)
        has_pwd = bool(note.get('password_hash'))
        # 永远不向前端返回密码哈希和密钥材料
        note.pop('password_hash', None)
        note.pop('enc_dek', None)
        note['has_password'] = has_pwd
        if has_pwd:
            dek = _unlocked_deks.get(note_id)
            pt = _decrypt_content(dek, note['content'], note_id) if dek else None
            if pt is None:
                # 未解锁（或解密失败），隐藏内容
                note['content'] = ''
                note['is_encrypted'] = True
                note['title'] = note.get('title', '未命名笔记')  # 标题可以显示
            else:
                note['content'] = pt
                note['is_encrypted'] = False
        else:
            note['is_encrypted'] = False
        if fields is not None:
            keep = set(fields) | {'has_password', 'is_encrypted'}
            note = {k: v for k, v in note.items() if k in keep}
        return note

    def notes_duplicate(self, note_id):
        """复制一篇笔记（含附件文件与标签）。

        复制：正文、标签、笔记本、背景/纸张/封面等外观字段。
        不复制：历史版本（那是原笔记的历史，副本从零开始更合理）、提醒（会造成双份打扰）、
        置顶/收藏（副本突然插到最前面会让人困惑）、密码（副本默认明文——见下）。
        加密笔记**只在已解锁时**才允许复制：锁定态读不到正文，与其复制出一串密文，
        不如明确拒绝；复制出来的副本是明文（用户想加密可以自己再设一次密码）。
        返回新笔记；源笔记不存在、或加密未解锁时返回 None。
        """
        row = conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL",
                           (note_id,)).fetchone()
        if not row:
            return None
        src = dict(row)
        content = src.get('content') or ''
        if src.get('password_hash'):
            dek = _unlocked_deks.get(note_id)
            if dek is None:
                return None
            content = _decrypt_content(dek, content, note_id)
            if content is None or content.startswith(ENC_PREFIX):
                return None      # 解不开就不要复制出乱码
        nid = str(uuid.uuid4())
        max_order = _next_sort_order('notes')
        title = (src.get('title') or '未命名笔记') + ' 副本'
        fmt = src.get('format') or 'delta'
        conn.execute(
            "INSERT INTO notes (id, title, content, bg_type, bg_value, bg_opacity, bg_zoom, "
            "bg_pos_x, bg_pos_y, notebook_id, paper_style, paper_color, cover_type, cover_value, "
            "sort_order, format) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, title, content, src.get('bg_type'), src.get('bg_value'), src.get('bg_opacity'),
             src.get('bg_zoom'), src.get('bg_pos_x'), src.get('bg_pos_y'), src.get('notebook_id'),
             src.get('paper_style'), src.get('paper_color'), src.get('cover_type'),
             src.get('cover_value'), max_order, fmt)
        )
        # 标签一起复制
        for r in conn.execute("SELECT tag_id FROM note_tags WHERE note_id = ?", (note_id,)).fetchall():
            conn.execute("INSERT OR IGNORE INTO note_tags (note_id, tag_id) VALUES (?, ?)",
                         (nid, r['tag_id']))
        # 附件：行 + 文件都要复制。正文里的图片按 note_id 拼路径，不复制文件副本就会指向原笔记目录
        atts = conn.execute("SELECT * FROM attachments WHERE note_id = ?", (note_id,)).fetchall()
        if atts:
            src_dir = os.path.join(ATTACH_DIR, note_id)
            dst_dir = os.path.join(ATTACH_DIR, nid)
            if os.path.isdir(src_dir):
                os.makedirs(dst_dir, exist_ok=True)
            for a in atts:
                try:
                    shutil.copy2(os.path.join(src_dir, a['filename']),
                                 os.path.join(dst_dir, a['filename']))
                except OSError:
                    continue          # 文件缺失就只留行，别让整次复制失败
                conn.execute(
                    "INSERT INTO attachments (id, note_id, filename, original_name, mime_type, "
                    "file_size, type) VALUES (?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), nid, a['filename'], a['original_name'], a['mime_type'],
                     a['file_size'], a['type'])
                )
        _fts_sync(nid, title, content, fmt)
        _refresh_derived(nid, content, fmt)
        conn.commit()
        return self.notes_get(nid)

    def notes_create(self, notebook_id=None):
        # 新笔记默认 Markdown（第 6 轮起的双轨策略）；历史笔记保持 delta，按需转换
        # notebook_id：当前笔记本（前端把关）——「在『原神』里新建」就该落在原神里，
        # 落「未分类」再让用户手动移一次，正是"新建的混在一起"的一半原因。
        # 传进来的 id 若指向已删除的笔记本，退回未分类（否则这篇笔记会谁也看不见）
        if notebook_id and not conn.execute(
                "SELECT 1 FROM notebooks WHERE id = ?", (notebook_id,)).fetchone():
            notebook_id = None
        nid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO notes (id, title, content, sort_order, format, notebook_id) "
            "VALUES (?, '未命名笔记', '', ?, 'md', ?)",
            (nid, _next_sort_order('notes'), notebook_id or None)
        )
        _fts_sync(nid, '未命名笔记', '', 'md')
        _refresh_derived(nid, '', 'md')
        conn.commit()
        return self.notes_get(nid)

    def notes_update(self, note_id, fields):
        allowed = {'title', 'content', 'bg_type', 'bg_value', 'bg_opacity', 'bg_zoom', 'bg_pos_x', 'bg_pos_y', 'bg_blur', 'ui_scrim', 'content_scrim', 'sort_order', 'is_pinned', 'is_favorite', 'paper_style', 'paper_color', 'cover_type', 'cover_value', 'notebook_id'}
        # 回收站中的笔记不可更新
        if not conn.execute("SELECT 1 FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone():
            return None
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return None
        # 空串 = 未分类（前端「无（全部笔记）」传的就是 ''）：统一存 NULL。
        # 存成空串的话，这篇笔记既不在任何笔记本里、也不在"未分类"（IS NULL）里 ——
        # 按笔记本筛选时它永远不会出现，等于凭空消失。
        if 'notebook_id' in updates and not updates['notebook_id']:
            updates['notebook_id'] = None
        fts_needs = 'title' in updates or 'content' in updates  # 在加密剥除前判定
        # 加密笔记：内容写入必须已在后端解锁（不信任前端状态），解锁则加密后入库
        if 'content' in updates:
            row = conn.execute("SELECT password_hash FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row and row['password_hash']:
                dek = _unlocked_deks.get(note_id)
                if dek is None:
                    updates.pop('content')  # 未解锁，拒绝写入内容
                    if not updates:
                        return None
                else:
                    updates['content'] = _encrypt_content(dek, updates['content'], note_id)
        if set(updates) != {'sort_order'}:
            # 纯排序更新不算修改内容：不 bump updated_at，否则拖拽后所有笔记时间戳被刷新
            updates["updated_at"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [note_id]
        conn.execute(f"UPDATE notes SET {sets} WHERE id = ?", vals)
        if fts_needs:
            _fts_sync_from_row(note_id)  # 从库中当前行重建（加密笔记 body 恒空）
        _refresh_derived(note_id)        # 派生索引与正文同事务更新，绝不出现"正文变了指标没变"
        conn.commit()
        return self.notes_get(note_id)

    def notes_delete(self, note_id):
        """软删除：只置 deleted_at（移入回收站），不删文件不 rmtree，FK 子表全部保留。
        回收站「彻底删除」走 notes_purge；30 天超期由 purge_expired_trash 兜底。"""
        if not conn.execute("SELECT 1 FROM notes WHERE id = ? AND deleted_at IS NULL", (note_id,)).fetchone():
            return False
        conn.execute("UPDATE notes SET deleted_at = datetime('now','localtime') WHERE id = ?", (note_id,))
        _fts_delete(note_id)  # 移出搜索索引
        conn.commit()
        _unlocked_deks.pop(note_id, None)
        return True

    def notes_trash_list(self):
        """回收站列表：标题 + 删除时间，按删除时间倒序"""
        return [dict(r) for r in conn.execute(
            "SELECT id, title, deleted_at FROM notes WHERE deleted_at IS NOT NULL "
            "ORDER BY deleted_at DESC").fetchall()]

    def notes_restore(self, note_id):
        """恢复：清 deleted_at + 重建搜索索引"""
        if not conn.execute("SELECT 1 FROM notes WHERE id = ? AND deleted_at IS NOT NULL", (note_id,)).fetchone():
            return False
        conn.execute("UPDATE notes SET deleted_at = NULL WHERE id = ?", (note_id,))
        _fts_sync_from_row(note_id)
        conn.commit()
        return True

    def notes_purge(self, note_id):
        """彻底删除：清文件 + DELETE 行级联（FK 清 attachments/reminders/versions/note_tags）"""
        if not conn.execute("SELECT 1 FROM notes WHERE id = ? AND deleted_at IS NOT NULL", (note_id,)).fetchone():
            return False
        _purge_note_files(note_id)
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        _fts_delete(note_id)
        conn.commit()
        _unlocked_deks.pop(note_id, None)
        return True

    def notes_purge_all(self):
        """清空回收站"""
        ids = [r['id'] for r in conn.execute("SELECT id FROM notes WHERE deleted_at IS NOT NULL").fetchall()]
        for nid in ids:
            self.notes_purge(nid)
        return len(ids)

    # ----- 批量操作（多选）-----
    # 都走**一个**桥调用而不是让前端循环 N 次：N 次跨语言往返在大批量下明显卡顿，
    # 而且失败一半时前端很难给出准确反馈。
    def notes_delete_many(self, note_ids):
        """批量移入回收站。逐条复用 notes_delete，保证 FTS/解锁缓存/查询过滤等副作用一致。"""
        n = 0
        for nid in (note_ids or []):
            if nid and self.notes_delete(nid):
                n += 1
        return n

    def notes_move_many(self, note_ids, notebook_id=None):
        """批量移动到笔记本（notebook_id=None/'' → 移出到未分类）。返回改动行数。"""
        ids = [i for i in (note_ids or []) if i]
        if not ids:
            return 0
        n = 0
        for chunk in _chunked(ids):      # 分块：全选上千篇时不能一次展开成上千个占位符
            marks = ','.join('?' * len(chunk))
            cur = conn.execute(
                "UPDATE notes SET notebook_id = ?, updated_at = datetime('now','localtime') "
                "WHERE id IN (%s) AND deleted_at IS NULL" % marks,
                [notebook_id or None] + chunk)
            n += cur.rowcount
        conn.commit()
        return n

    def notes_reorder(self, note_ids):
        """按给定顺序重写 sort_order（拖拽排序用）。**一个事务、一次桥调用**。

        为什么必须有这个接口：前端以前是 `for (i...) await notes_update(id, {sort_order})`
        —— 800 篇就是 800 次跨语言往返 + 800 个事务。这违背本项目自己的批量原则
        （`12-bulk-actions.js` 开头就写着"N 次跨语言往返在大批量下明显卡顿"），拖拽这条路径当初漏了。

        `note_ids` 是**按列表从上到下**的顺序（前端 state.notes 的顺序）。列表排序是
        `is_pinned DESC, sort_order DESC, updated_at DESC`，所以顶部要拿**最大**的 sort_order，
        于是写成 `n - 1 - i`；这与旧前端逐条写入时的算法**完全一致**，顺序不会变。

        只更新真正在库里的行（不存在的 id 静默跳过），整体一个事务：要么全成、要么全不动，
        不会出现"拖到一半断了，顺序半新半旧"。
        """
        ids = [i for i in (note_ids or []) if i]
        if not ids:
            return 0
        live = {r['id'] for r in conn.execute(
            "SELECT id FROM notes WHERE deleted_at IS NULL").fetchall()}
        n = len(ids)
        try:
            for i, nid in enumerate(ids):
                if nid not in live:
                    continue
                conn.execute("UPDATE notes SET sort_order = ? WHERE id = ?", (n - 1 - i, nid))
        except Exception:
            conn.rollback()
            raise
        conn.commit()
        return n

    def notes_add_tag_many(self, note_ids, tag_id):
        """批量打同一个标签（已关联的自动跳过）。返回新增关联数。"""
        ids = [i for i in (note_ids or []) if i]
        if not ids or not tag_id:
            return 0
        # 标签必须真实存在：note_tags.tag_id 有外键，传个不存在的 id 会直接抛 IntegrityError
        if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone():
            return 0
        live = []
        for chunk in _chunked(ids):
            marks = ','.join('?' * len(chunk))
            live += [r['id'] for r in conn.execute(
                "SELECT id FROM notes WHERE id IN (%s) AND deleted_at IS NULL" % marks,
                chunk).fetchall()]
        added = 0
        for nid in live:
            cur = conn.execute(
                "INSERT OR IGNORE INTO note_tags (note_id, tag_id) VALUES (?, ?)", (nid, tag_id))
            added += cur.rowcount
        conn.commit()
        return added

    def notes_search(self, query):
        """全文搜索：≥3 字符且 FTS5 可用走 trigram；否则 LIKE 回退（标题 + 明文笔记正文）。
        加密笔记任何情况下只搜标题。

        支持范围前缀：`tag:数学` / `notebook:课程A`（或 `nb:`）/ `in:trash`，可与关键词组合。
        返回 {'ids': [...], 'title_hits': [...], 'snippets': {note_id: 片段}}
        snippets 是命中处 ±40 字的明文片段，供列表显示「为什么这条命中了」并高亮关键词；
        标题命中（正文里找不到关键词）时退化为正文开头。加密笔记恒为空串——密文/明文都不过桥。
        """
        q = (query or '').strip()
        if not q:
            return {'ids': [], 'title_hits': [], 'snippets': {}}
        text, scope = _parse_search_scope(q)
        if scope:
            return self._search_in_scope(text, scope, q)
        return self._search_plain(q)

    def _search_in_scope(self, text, scope, raw):
        """范围搜索：先拿范围内的候选，再与关键词命中集求交（无关键词则整个范围都算命中）"""
        scoped = _scope_note_ids(scope)
        if not text:
            ids = scoped
            hits = []
        elif scope.get('trash'):
            # 回收站不在 FTS 索引里，只能用标题 LIKE
            found = set(_trash_title_ids(text))
            ids = [i for i in scoped if i in found]
            hits = list(ids)
        else:
            res = self._search_plain(text)
            found = set(res['ids'])
            ids = [i for i in scoped if i in found]
            hits = [i for i in ids if i in set(res['title_hits'])]
        ids = self._filter_excluded(ids, scope.get('exclude'))
        ids = _filter_props(ids, scope.get('prop'))
        return {'ids': ids, 'title_hits': [i for i in hits if i in set(ids)],
                'snippets': self._snippets_for(ids, text or '')}

    def _filter_excluded(self, ids, exclude):
        """排除词（`-词`）：对候选逐个查正文。

        为什么放在范围搜索里做后置过滤而不是塞进 FTS：候选集通常很小（范围已经收窄），
        而后置过滤能同时覆盖"纯排除词"（没有关键词）这种 FTS 表达不了的情况。
        加密笔记读不到正文——**保留**它（宁可不排除，也不误删用户的笔记）。
        """
        if not exclude or not ids:
            return ids
        out = []
        texts = {}
        for chunk in _chunked(ids):      # 分块：全库搜索的候选集可能上千
            marks = ','.join('?' * len(chunk))
            for r in conn.execute(
                    f"SELECT id, content, format, password_hash FROM notes WHERE id IN ({marks})",
                    list(chunk)).fetchall():
                if r['password_hash']:
                    texts[r['id']] = None
                    continue
                texts[r['id']] = note_plain_text(r['content'], r['format'] or 'delta').lower()
        lowered = [w.lower() for w in exclude]
        for i in ids:
            t = texts.get(i)
            if t is None or not any(w in t for w in lowered):
                out.append(i)
        return out

    def _search_plain(self, q):
        """不带范围前缀的关键词搜索（原逻辑）"""
        if FTS_AVAILABLE and len(q) >= 3:
            try:
                phrase = '"' + q.replace('"', '""') + '"'
                title_ids = [r['note_id'] for r in conn.execute(
                    "SELECT note_id FROM notes_fts WHERE notes_fts MATCH ?",
                    ('title:' + phrase,)).fetchall()]
                all_ids = [r['note_id'] for r in conn.execute(
                    "SELECT note_id FROM notes_fts WHERE notes_fts MATCH ?",
                    (phrase,)).fetchall()]
                return {'ids': all_ids, 'title_hits': title_ids,
                        'snippets': self._snippets_for(all_ids, q)}
            except Exception:
                applog.get_logger().exception("FTS 查询失败，回退 LIKE")
        # LIKE 回退：<3 字符（中文双字常态）/ FTS 不可用 / FTS 查询异常
        esc = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like = f'%{esc}%'
        rows = conn.execute(
            "SELECT id, (title LIKE ? ESCAPE '\\') AS th FROM notes "
            "WHERE deleted_at IS NULL AND (title LIKE ? ESCAPE '\\' "
            "   OR (COALESCE(password_hash, '') = '' AND content LIKE ? ESCAPE '\\'))",
            (like, like, like)).fetchall()
        ids = [r['id'] for r in rows]
        return {'ids': ids,
                'title_hits': [r['id'] for r in rows if r['th']],
                'snippets': self._snippets_for(ids, q)}

    @staticmethod
    def _snippets_for(note_ids, q, width=40):
        """给命中结果生成正文片段（只在命中集上取，通常只有几条，成本可忽略）"""
        if not note_ids:
            return {}
        out = {}
        ql = q.lower()
        rows = []
        for chunk in _chunked(note_ids):     # 分块：搜索命中的候选可能上千
            marks = ','.join('?' * len(chunk))
            rows += conn.execute(
                f"SELECT id, content, password_hash, format FROM notes WHERE id IN ({marks})",
                list(chunk)).fetchall()
        for r in rows:
            if r['password_hash']:
                out[r['id']] = ''      # 加密笔记不外泄任何正文片段
                continue
            text = ' '.join(note_plain_text(r['content'], r['format'] or 'delta').split())
            i = text.lower().find(ql)
            if i < 0:
                out[r['id']] = text[:width * 2]        # 标题命中：给正文开头
                continue
            start = max(0, i - width)
            end = min(len(text), i + len(q) + width)
            out[r['id']] = ('…' if start > 0 else '') + text[start:end] + ('…' if end < len(text) else '')
        return out

    # ----- 派生指标 / 待办（第 7 轮） -----
    def note_metrics(self, note_id):
        """单篇的派生指标（字符数/待办数/链接数…），面板与表格视图用"""
        _refresh_derived(note_id)
        r = conn.execute("SELECT * FROM note_derived WHERE note_id = ?", (note_id,)).fetchone()
        return dict(r) if r else None

    # ====== 模板（第 10 轮）======
    def templates_list(self):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM templates ORDER BY sort_order, name").fetchall()]

    def template_create(self, name, content=''):
        tid = str(uuid.uuid4())
        conn.execute("INSERT INTO templates (id, name, content, sort_order) VALUES (?,?,?,?)",
                     (tid, (name or '').strip() or '新模板', content or '',
                      _next_sort_order('templates')))
        conn.commit()
        return self.template_get(tid)

    def template_get(self, template_id):
        row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        return dict(row) if row else None

    def template_update(self, template_id, fields):
        allowed = {k: v for k, v in (fields or {}).items() if k in ('name', 'content', 'sort_order')}
        if not allowed:
            return self.template_get(template_id)
        sets = ', '.join('%s = ?' % k for k in allowed)
        conn.execute("UPDATE templates SET %s WHERE id = ?" % sets,
                     list(allowed.values()) + [template_id])
        conn.commit()
        return self.template_get(template_id)

    def template_delete(self, template_id):
        conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
        conn.commit()
        return True

    def template_render(self, template_id, title=''):
        """模板 → 渲染后的正文（变量替换一次；模板不存在返回空串）"""
        row = conn.execute("SELECT content FROM templates WHERE id = ?", (template_id,)).fetchone()
        return render_template(row['content'], title) if row else ''

    def notes_create_from_template(self, template_id, title=None, notebook_name=None, notebook_id=None):
        """用模板新建一篇笔记：变量在**这一刻**替换一次，之后它就是一普通篇笔记。

        前端两处入口（捕获菜单里点模板名 / 模板抽屉里的「用模板新建」）共用这一条路径——
        差别只是标题从哪来：菜单里直接用模板名，抽屉里听输入框的。

        归属优先级：notebook_id（当前笔记本，第 12 轮起前端会传）> notebook_name（按名字找或建）。
        """
        row = conn.execute("SELECT name, content FROM templates WHERE id = ?",
                           (template_id,)).fetchone()
        name = (title or '').strip()
        if not name and row:
            name = (row['name'] or '').strip()
        content = render_template(row['content'], name) if row else ''
        if not notebook_id:
            notebook_id = _find_or_create_notebook(notebook_name) if notebook_name else None
        note = self.notes_create(notebook_id=notebook_id)
        fields = {}
        if content:
            fields['content'] = content
        if name:
            fields['title'] = name
        return self.notes_update(note['id'], fields) if fields else note

    def notebook_id_by_name(self, name):
        """给前端用：拿到（或直接创建）某个名字的笔记本 id"""
        nid = _find_or_create_notebook(name)
        conn.commit()
        return nid

    # ====== 每日笔记 / 快速捕获（第 10 轮）======
    def daily_note_open(self):
        """打开"今天"的笔记：已存在就返回它，不存在才建（连点两次不会生成两篇）。

        标题用 `2026-09-22 周二`（排序即日期序，一眼能看出是哪天）；放在「日记」笔记本；
        若存在名为「日记」的模板就套用它（没有就建空白笔记）。
        """
        now = datetime.now()
        title = '%s %s' % (now.strftime('%Y-%m-%d'), WEEKDAY_CN[now.weekday()])
        row = conn.execute(
            "SELECT id FROM notes WHERE title = ? AND deleted_at IS NULL "
            "ORDER BY created_at LIMIT 1", (title,)).fetchone()
        if row:
            return self.notes_get(row['id'])
        notebook_id = _find_or_create_notebook(DAILY_NOTEBOOK)
        tpl = conn.execute("SELECT content FROM templates WHERE name = ? LIMIT 1",
                           (DAILY_NOTEBOOK,)).fetchone()
        note = self.notes_create()
        content = render_template(tpl['content'], title) if tpl else ''
        if content:
            self.notes_update(note['id'], {'content': content})
        return self.notes_update(note['id'], {'title': title, 'notebook_id': notebook_id})

    def capture_text(self, text, notebook_name=None):
        """快速捕获一段纯文本 → 收件箱里的一篇新笔记（第一行当标题）。

        为什么不再追问用户放哪：捕获的价值就是"不打断"，先收进来、之后再整理。
        """
        body = (text or '').strip()
        if not body:
            return None
        notebook_id = _find_or_create_notebook(notebook_name or INBOX_NOTEBOOK)
        note = self.notes_create()
        self.notes_update(note['id'], {'content': body})
        return self.notes_update(note['id'], {
            'title': _first_line_title(body), 'notebook_id': notebook_id})

    def capture_image(self, src_path, title=None, body=None):
        """把一张图（截图）存成收件箱里的新笔记，并把图复制进附件目录。

        走的是既有的附件机制（`attachments/<note_id>/`），所以导出/备份/复制笔记全都照常可用。

        `body` 用于第 11 轮的「识别文字」：文字接在图片下面（图保留，方便回头核对识别得对不对）；
        给了 body 又没给 title 时，标题取正文第一行——比"截图 2026-09-25 22:15"有用得多。
        """
        try:
            if not src_path or not os.path.isfile(src_path):
                return None
            notebook_id = _find_or_create_notebook(INBOX_NOTEBOOK)
            extra = (body or '').strip()
            name = title or (_first_line_title(extra) if extra
                             else ('截图 %s' % datetime.now().strftime('%Y-%m-%d %H:%M')))
            note = self.notes_create()
            saved = self.file_copy_to_note(src_path, note['id'], 'image')
            if not saved or not saved.get('filename'):
                return None
            rel = 'attachments/%s/%s' % (note['id'], saved['filename'])
            content = '![%s](%s)\n' % (name, rel)
            if extra:
                content += '\n' + extra + '\n'
            self.notes_update(note['id'], {'content': content})
            return self.notes_update(note['id'], {'title': name, 'notebook_id': notebook_id})
        except Exception:
            applog.get_logger().exception("截图捕获失败")
            return None

    def note_links(self, note_id):
        """一篇笔记的链接关系：指向 / 反向链接 / 指向尚不存在的笔记。

        **解析放在查询时**（标题 → 笔记），不物化 target_id：标题随时会改，
        物化就等于给自己埋一个必然过期的索引——改完标题所有反向链接一起断。
        """
        row = conn.execute("SELECT title FROM notes WHERE id = ?", (note_id,)).fetchone()
        title_key = (row['title'] or '').strip().lower() if row else ''
        outgoing, missing = [], []
        for r in conn.execute("SELECT * FROM note_links WHERE src_note_id = ? ORDER BY ordinal",
                              (note_id,)).fetchall():
            item = {'title': r['target_raw'], 'heading': r['heading'], 'alias': r['alias'],
                    'index': r['ordinal'], 'same_note': not r['target_key'], 'target': None}
            if r['target_key']:
                t = conn.execute(
                    "SELECT id, title FROM notes WHERE lower(trim(title)) = ? AND deleted_at IS NULL "
                    "ORDER BY updated_at DESC LIMIT 1", (r['target_key'],)).fetchone()
                if t:
                    item['target'] = {'id': t['id'], 'title': t['title']}
            if item['target'] is None and not item['same_note']:
                missing.append(item)
            outgoing.append(item)
        backlinks = []
        if title_key:
            for r in conn.execute(
                    "SELECT l.target_raw, l.heading, l.alias, n.id AS src_id, n.title AS src_title, "
                    "n.updated_at AS src_updated, n.content AS src_content, n.format AS src_format, "
                    "n.password_hash AS src_pw "
                    "FROM note_links l JOIN notes n ON n.id = l.src_note_id "
                    "WHERE l.target_key = ? AND n.deleted_at IS NULL "
                    "ORDER BY n.updated_at DESC", (title_key,)).fetchall():
                context = '' if r['src_pw'] else _link_context(
                    r['src_content'], r['src_format'] or 'delta', r['target_raw'], r['heading'])
                backlinks.append({'id': r['src_id'], 'title': r['src_title'],
                                  'updated_at': r['src_updated'], 'heading': r['heading'],
                                  'alias': r['alias'], 'context': context})
        return {'outgoing': outgoing, 'backlinks': backlinks, 'missing': missing}

    def notes_resolve_link(self, title):
        """标题 → 笔记。多条同名时取**最近更新**的那条，并把命中数回报给前端提示。"""
        key = (title or '').strip().lower()
        if not key:
            return None
        rows = conn.execute(
            "SELECT id, title FROM notes WHERE lower(trim(title)) = ? AND deleted_at IS NULL "
            "ORDER BY updated_at DESC", (key,)).fetchall()
        if not rows:
            return None
        return {'id': rows[0]['id'], 'title': rows[0]['title'], 'matches': len(rows)}

    def notes_create_from_link(self, title, notebook_id=None):
        """点「未创建的链接」→ 建一篇同名 Markdown 笔记。

        刻意走 notes_create + notes_update 两条既有路径而不是自己拼 INSERT：
        排序值 / FTS / 派生索引都在里面，少走一步就多一处会漏的地方。
        notebook_id = 当前笔记本（双链是从"这一本里的某篇"长出来的，跟着它走最自然）。
        """
        name = (title or '').strip() or '未命名笔记'
        note = self.notes_create(notebook_id=notebook_id)
        return self.notes_update(note['id'], {'title': name}) or note

    def notes_table(self, note_ids=None):
        """表格视图的数据源：**一次**桥调用返回所有行的完整字段。

        为什么不让前端逐篇查：N 次跨语言往返在大库下明显卡顿，且"表格 = 列表所见"
        这件事应该由调用方传 id 列表来保证（前端传的就是当前筛选后的那批 id）。
        加密笔记只回标题等元信息（派生数据本来就是空的），**绝不下发 password_hash**。
        """
        if note_ids is None:
            note_ids = [r['id'] for r in conn.execute(
                "SELECT id FROM notes WHERE deleted_at IS NULL "
                "ORDER BY is_pinned DESC, sort_order DESC").fetchall()]
        if not note_ids:
            return []
        rows = {}
        sql = ("SELECT n.id, n.title, n.format, n.created_at, n.updated_at, n.is_pinned, "
               "n.is_favorite, n.password_hash, nb.name AS notebook, "
               "COALESCE(d.word_count, 0) AS word_count, COALESCE(d.char_count, 0) AS char_count, "
               "COALESCE(d.todo_open, 0) AS todo_open, COALESCE(d.todo_total, 0) AS todo_total, "
               "d.todo_next_due, COALESCE(d.link_count, 0) AS link_count, "
               "COALESCE(NULLIF(d.props_json, ''), '{}') AS props_json "
               "FROM notes n LEFT JOIN note_derived d ON d.note_id = n.id "
               "LEFT JOIN notebooks nb ON nb.id = n.notebook_id "
               "WHERE n.deleted_at IS NULL AND n.id IN (%s)")
        res = []
        for chunk in _chunked(note_ids):    # 分块：表格视图可以一次要看全库
            marks = ','.join('?' * len(chunk))
            res += conn.execute(sql % marks, list(chunk)).fetchall()
        for r in res:
            rows[r['id']] = {
                'id': r['id'], 'title': r['title'], 'notebook': r['notebook'] or '',
                'format': r['format'] or 'delta', 'created_at': r['created_at'],
                'updated_at': r['updated_at'], 'is_pinned': r['is_pinned'],
                'is_favorite': r['is_favorite'], 'encrypted': bool(r['password_hash']),
                'word_count': r['word_count'], 'char_count': r['char_count'],
                'todo_open': r['todo_open'], 'todo_total': r['todo_total'],
                'todo_next_due': r['todo_next_due'], 'link_count': r['link_count'],
                'props': {} if r['password_hash'] else _load_props(r['props_json']),
                'tags': [],
            }
        for chunk in _chunked(note_ids):        # 分块：标签查询同样要跟着分块
            marks = ','.join('?' * len(chunk))
            for r in conn.execute(
                    f"SELECT nt.note_id, t.name FROM note_tags nt JOIN tags t ON t.id = nt.tag_id "
                    f"WHERE nt.note_id IN ({marks}) ORDER BY t.name", list(chunk)).fetchall():
                if r['note_id'] in rows:
                    rows[r['note_id']]['tags'].append(r['name'])
        return [rows[i] for i in note_ids if i in rows]

    def export_table_csv(self, note_ids, save_path):
        """表格 → CSV（utf-8-sig：带 BOM，Excel 双击打开中文不乱码）。

        属性列取所有行的键并集（排序后），加密笔记的属性列留空。返回写入的行数。
        """
        try:
            rows = self.notes_table(note_ids)
            if not rows:
                return 0
            keys = sorted({k for r in rows for k in (r.get('props') or {})})
            with open(save_path, 'w', encoding='utf-8-sig', newline='') as fh:
                writer = csv.writer(fh)
                writer.writerow(['标题', '笔记本', '标签', '更新时间', '字数',
                                 '待办(未完成/总数)', '最近到期', '已加密'] + keys)
                for r in rows:
                    vals = [r['title'], r['notebook'], '/'.join(r['tags']), r['updated_at'],
                            r['word_count'], '%d/%d' % (r['todo_open'], r['todo_total']),
                            r['todo_next_due'] or '', '是' if r['encrypted'] else '']
                    for k in keys:
                        v = (r.get('props') or {}).get(k, '')
                        vals.append('/'.join(str(x) for x in v) if isinstance(v, list) else v)
                    writer.writerow(vals)
            return len(rows)
        except Exception:
            applog.get_logger().exception("导出表格 CSV 失败")
            return None

    def metrics_bulk(self, note_ids=None):
        """批量指标（表格视图用；不传就全部）。

        加密笔记一律排除：它们的派生行在 _refresh_derived 里会被清空，但**历史脏行**可能
        还在（修复前的版本会把明文指标写进去），而且锁定态也不该给出"字数/属性/待办"这类
        正文侧面。这一层过滤是第二道闸，不依赖派生表本身是否干净。
        """
        if note_ids:
            rows = []
            for chunk in _chunked(note_ids):   # 分块：表格视图可能一次要看全库
                marks = ','.join('?' * len(chunk))
                rows += conn.execute(
                    f"SELECT d.* FROM note_derived d JOIN notes n ON n.id = d.note_id "
                    f"WHERE n.deleted_at IS NULL AND COALESCE(n.password_hash, '') = '' "
                    f"AND d.note_id IN ({marks})", list(chunk)).fetchall()
        else:
            rows = conn.execute(
                "SELECT d.* FROM note_derived d JOIN notes n ON n.id = d.note_id "
                "WHERE n.deleted_at IS NULL AND COALESCE(n.password_hash, '') = ''").fetchall()
        return [dict(r) for r in rows]

    def todos_list(self, scope='open'):
        """跨笔记待办：scope = open | today | overdue | week | nodue | done

        只返回**未加密**笔记的待办。加密笔记不参与派生（见 _refresh_derived），但这条
        SQL 过滤是必须的第二道闸：修复前的版本存在旁路，明文待办会留在 note_todos 里
        （存量库的脏行不会自己消失），光靠"派生时清空"挡不住历史数据。
        """
        today = datetime.now().strftime('%Y-%m-%d')
        week = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
        where = ["n.deleted_at IS NULL", "COALESCE(n.password_hash, '') = ''"]
        params = []
        if scope == 'done':
            where.append("t.done = 1")
        else:
            where.append("t.done = 0")
            if scope == 'today':
                where.append("t.due = ?")
                params.append(today)
            elif scope == 'overdue':
                where.append("t.due IS NOT NULL AND t.due < ?")
                params.append(today)
            elif scope == 'week':
                where.append("t.due IS NOT NULL AND t.due <= ?")
                params.append(week)
            elif scope == 'nodue':
                where.append("t.due IS NULL")
        rows = conn.execute(
            "SELECT t.note_id, t.idx, t.text, t.due, t.done, n.title AS note_title, "
            "n.updated_at AS note_updated "
            "FROM note_todos t JOIN notes n ON n.id = t.note_id WHERE " + " AND ".join(where) +
            " ORDER BY (t.due IS NULL), t.due ASC, n.updated_at DESC, t.idx ASC", params).fetchall()
        items = [dict(r) for r in rows]
        counts = {}
        for key, (due_sql, due_params) in {
            'open': ("", []),
            'today': ("AND t.due = ?", [today]),
            'overdue': ("AND t.due IS NOT NULL AND t.due < ?", [today]),
            'week': ("AND t.due IS NOT NULL AND t.due <= ?", [week]),
            'nodue': ("AND t.due IS NULL", []),
        }.items():
            counts[key] = conn.execute(
                "SELECT COUNT(*) FROM note_todos t JOIN notes n ON n.id = t.note_id "
                "WHERE n.deleted_at IS NULL AND COALESCE(n.password_hash, '') = '' "
                "AND t.done = 0 " + due_sql, due_params).fetchone()[0]
        counts['done'] = conn.execute(
            "SELECT COUNT(*) FROM note_todos t JOIN notes n ON n.id = t.note_id "
            "WHERE n.deleted_at IS NULL AND COALESCE(n.password_hash, '') = '' "
            "AND t.done = 1").fetchone()[0]
        return {'items': items, 'counts': counts}

    def todo_toggle(self, note_id, idx, expected_text=''):
        """勾选/取消一条待办（按「第 idx 个待办」定位，写回原文）。

        为什么要校验文本：正文可能在面板打开后被改过，此时按位置翻会翻错。
        校验不一致就**拒绝**并让前端刷新，绝不猜。写回**不建历史版本**（勾待办不是编辑行为，
        每次都建版本会把 50 条历史塞满）。
        """
        row = conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL",
                           (note_id,)).fetchone()
        if not row:
            return {'ok': False, 'error': '笔记不存在'}
        note = dict(row)
        fmt = note.get('format') or 'delta'
        dek = None
        if note.get('password_hash'):
            dek = _unlocked_deks.get(note_id)
            if dek is None:
                return {'ok': False, 'error': '加密笔记需先解锁'}
            content = _decrypt_content(dek, note['content'] or '', note_id)
        else:
            content = note['content'] or ''
        if fmt == 'md':
            lines = content.splitlines(keepends=True)
            seen = -1
            hit = None
            for i, line in enumerate(lines):
                m = _TODO_RE.match(line)
                if not m:
                    continue
                seen += 1
                if seen == idx:
                    hit = (i, m)
                    break
            if hit is None:
                return {'ok': False, 'error': '待办已不存在，请刷新'}
            i, m = hit
            cur_text = _DUE_RE.sub('', m.group(2)).strip()
            if expected_text and cur_text != expected_text.strip():
                return {'ok': False, 'error': '正文已变化，请刷新后重试'}
            done_now = 0 if m.group(1).lower() == 'x' else 1
            lines[i] = _TODO_RE.sub(
                lambda mm: mm.group(0).replace('[%s]' % mm.group(1),
                                               '[x]' if done_now else '[ ]', 1),
                lines[i], count=1)
            new_content = ''.join(lines)
        else:
            try:
                data = json.loads(content)
            except Exception:
                return {'ok': False, 'error': '正文无法解析'}
            ops = data.get('ops', []) if isinstance(data, dict) else data
            seen = -1
            target = None
            for op in ops:
                if not isinstance(op, dict):
                    continue
                attrs = op.get('attributes') or {}
                if attrs.get('list') in ('checked', 'unchecked'):
                    seen += 1
                    if seen == idx:
                        target = op
                        break
            if target is None:
                return {'ok': False, 'error': '待办已不存在，请刷新'}
            cur_text = _DUE_RE.sub('', (target.get('insert') or '').strip()).strip()
            if expected_text and cur_text != expected_text.strip():
                return {'ok': False, 'error': '正文已变化，请刷新后重试'}
            done_now = 0 if target['attributes'].get('list') == 'checked' else 1
            target['attributes']['list'] = 'checked' if done_now else 'unchecked'
            new_content = json.dumps(data if isinstance(data, dict) else {'ops': ops},
                                     ensure_ascii=False)
        store = _encrypt_content(dek, new_content, note_id) if dek is not None else new_content
        conn.execute("UPDATE notes SET content = ?, updated_at = datetime('now','localtime') "
                     "WHERE id = ?", (store, note_id))
        conn.commit()
        _fts_sync_from_row(note_id)
        _refresh_derived(note_id, new_content, fmt)
        conn.commit()
        return {'ok': True, 'done': done_now, 'note_id': note_id, 'idx': idx}

    # ----- 标签：重命名 / 合并 -----
    def tags_rename(self, tag_id, new_name):
        """重命名标签（层级靠名字里的 `/`，所以重命名就等于改层级）"""
        name = (new_name or '').strip()
        if not name:
            return {'ok': False, 'error': '标签名不能为空'}
        exists = conn.execute("SELECT id FROM tags WHERE name = ? AND id != ?",
                              (name, tag_id)).fetchone()
        if exists:
            return {'ok': False, 'error': '同名标签已存在，请用「合并」把两者并起来'}
        if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (tag_id,)).fetchone():
            return {'ok': False, 'error': '标签不存在'}
        conn.execute("UPDATE tags SET name = ? WHERE id = ?", (name, tag_id))
        conn.commit()
        return {'ok': True, 'tag': self.tags_get(tag_id)}

    def tags_merge(self, src_id, dst_id):
        """把 src 标签合并到 dst：关联转移后删掉 src（比"重命名"更常用）"""
        if src_id == dst_id:
            return {'ok': False, 'error': '不能合并到自己'}
        for tid in (src_id, dst_id):
            if not conn.execute("SELECT 1 FROM tags WHERE id = ?", (tid,)).fetchone():
                return {'ok': False, 'error': '标签不存在'}
        moved = conn.execute("SELECT COUNT(*) FROM note_tags WHERE tag_id = ?",
                             (src_id,)).fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO note_tags (note_id, tag_id) "
                     "SELECT note_id, ? FROM note_tags WHERE tag_id = ?", (dst_id, src_id))
        conn.execute("DELETE FROM note_tags WHERE tag_id = ?", (src_id,))
        conn.execute("DELETE FROM tags WHERE id = ?", (src_id,))
        conn.commit()
        return {'ok': True, 'moved': moved, 'tag': self.tags_get(dst_id)}

    # ----- 保存的搜索 -----
    def saved_searches_list(self):
        rows = conn.execute("SELECT * FROM saved_searches ORDER BY sort_order, created_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d['count'] = len(self.notes_search(r['query'])['ids'])
            except Exception:
                d['count'] = None
            out.append(d)
        return out

    def saved_search_create(self, name, query):
        name = (name or '').strip() or (query or '').strip()
        query = (query or '').strip()
        if not query:
            return {'ok': False, 'error': '查询内容不能为空'}
        sid = str(uuid.uuid4())
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM saved_searches").fetchone()[0]
        conn.execute("INSERT INTO saved_searches (id, name, query, sort_order) VALUES (?,?,?,?)",
                     (sid, name, query, order))
        conn.commit()
        return {'ok': True, 'id': sid, 'name': name, 'query': query}

    def saved_search_update(self, sid, fields):
        allowed = {k: v for k, v in (fields or {}).items() if k in ('name', 'query', 'sort_order')}
        if not allowed:
            return {'ok': False, 'error': '没有可更新的字段'}
        sets = ', '.join('%s = ?' % k for k in allowed)
        conn.execute("UPDATE saved_searches SET %s WHERE id = ?" % sets,
                     list(allowed.values()) + [sid])
        conn.commit()
        return {'ok': True}

    def saved_search_delete(self, sid):
        conn.execute("DELETE FROM saved_searches WHERE id = ?", (sid,))
        conn.commit()
        return {'ok': True}

    # ----- 附件 -----
    def attachments_list(self, note_id):
        rows = conn.execute(
            "SELECT * FROM attachments WHERE note_id = ? ORDER BY created_at",
            (note_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def attachments_get_path(self, note_id, filename):
        """附件在磁盘上的绝对路径。

        这两个参数来自**笔记正文里的图片链接**（`![](attachments/<note_id>/<文件名>)`），
        而正文可以来自导入的外部 .md —— 属于不可信输入，必须校验：
        历史缺陷是直接 `os.path.join(ATTACH_DIR, note_id, filename)`，于是正文里写
        `![x](attachments/\\\\attacker\\share/x.png)` 时 os.path.join 会**丢弃 ATTACH_DIR**，
        交出一个纯 UNC 路径；下游 read_file_base64 的 realpath 一旦解析它就发起对外 SMB 连接
        （NetNTLM 泄露），打开笔记即触发、不需要点击。
        """
        if not note_id or not conn.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone():
            return None
        return safe_join(ATTACH_DIR, note_id, filename)

    # ----- 文件操作 -----
    def file_copy_to_note(self, source_path, note_id, file_type):
        """把外部文件复制进某篇笔记的附件目录。

        `note_id` 来自前端（必要时来自正文），不可信：历史缺陷是 `os.makedirs` + `copy2`
        发生在任何校验之前，于是传一个绝对路径当 note_id（如 Startup 目录）就能把文件写到
        ATTACH_DIR 之外任意位置、扩展名还跟着源文件走 —— 写进启动目录即可持久化。
        现在：note_id 必须是库里真实存在的笔记，且落地路径必须真的在 ATTACH_DIR 内。
        """
        # note_id 必须是真实笔记（顺带挡掉 `..`、绝对路径、UNC 这几种形状）
        if not note_id or not conn.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone():
            return {"error": "笔记不存在"}
        note_dir = safe_join(ATTACH_DIR, note_id)
        if not note_dir:
            return {"error": "附件目录不合法"}
        # 文件大小检查
        try:
            fsize = os.path.getsize(source_path)
            max_size = MAX_IMAGE_SIZE if file_type == 'image' else MAX_ATTACH_SIZE
            if fsize > max_size:
                max_mb = max_size // (1024*1024)
                return {"error": f"文件超过 {max_mb}MB 限制"}
        except OSError:
            return {"error": "无法读取文件"}
        os.makedirs(note_dir, exist_ok=True)

        # 扩展名只保留"安全形状"（白名单字符 + 长度），它会被拼进最终文件名
        ext = os.path.splitext(source_path)[1].lower()
        if not re.fullmatch(r'\.[a-z0-9]{1,10}', ext or ''):
            ext = ''
        new_name = f"{'img' if file_type == 'image' else 'file'}_{uuid.uuid4().hex}{ext}"
        dest = safe_join(ATTACH_DIR, note_id, new_name)
        if not dest:
            return {"error": "附件文件名不合法"}
        shutil.copy2(source_path, dest)

        size = os.path.getsize(dest)
        original = os.path.basename(source_path)

        aid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO attachments (id, note_id, filename, original_name, file_size, type) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (aid, note_id, new_name, original, size, file_type)
        )
        conn.commit()

        return {
            "id": aid, "filename": new_name, "original_name": original,
            "file_size": size, "storedPath": dest,
            "formattedSize": _format_size(size)
        }

    def file_open(self, file_path):
        """安全检查：规范路径后验证在附件目录内"""
        try:
            real = os.path.realpath(os.path.normpath(file_path))
        except (ValueError, TypeError):
            return False
        att_real = os.path.realpath(ATTACH_DIR)
        if not real.startswith(att_real + os.sep) and real != att_real:
            return False
        if not os.path.isfile(real):
            return False
        os.startfile(real)
        return True

    def file_get_note_attachment_path(self, note_id, filename):
        return os.path.join(ATTACH_DIR, note_id, filename)

    # ----- 设置 -----
    def settings_get(self, key):
        r = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return r['value'] if r else None

    def settings_set(self, key, value):
        conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, str(value)))
        conn.commit()
        return True

    def settings_get_all(self):
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r['key']: r['value'] for r in rows}

    # ----- 背景图片 -----
    def file_pick_background_path(self):
        """返回背景图片的 file:// URL"""
        return None  # 由前端 JS 通过文件输入处理

    # ----- 临时文件（用于粘贴图片） -----
    def save_temp_image(self, bytes_list, filename):
        """保存粘贴的图片到临时文件（大小限制 50MB）"""
        import tempfile
        if len(bytes_list) > MAX_IMAGE_SIZE:
            return None
        # 清理文件名：去除路径成分，只保留基础文件名
        safe_name = os.path.basename(filename)
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"notepad_temp_{uuid.uuid4().hex}_{safe_name}")
        # 确保最终路径在临时目录内
        if not os.path.realpath(temp_path).startswith(os.path.realpath(temp_dir) + os.sep):
            return None
        with open(temp_path, 'wb') as f:
            f.write(bytes(bytes_list))
        return temp_path

    # ----- 标签 -----
    def tags_list(self):
        """全部标签 + 每个标签的**在用笔记数**（回收站里的不计）。

        带 `/` 的标签名就是层级（`项目/子项目`），前端按 `/` 缩进展示；搜索时
        `tag:项目` 会自动包含子标签（见 _scope_where）。
        """
        rows = conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM note_tags nt JOIN notes n ON n.id = nt.note_id "
            "            WHERE nt.tag_id = t.id AND n.deleted_at IS NULL) AS note_count "
            "FROM tags t ORDER BY t.name").fetchall()
        return [dict(r) for r in rows]

    def tags_create(self, name, color='#7D8A6E'):
        tid = str(uuid.uuid4())
        conn.execute("INSERT INTO tags (id, name, color) VALUES (?, ?, ?)", (tid, name, color))
        conn.commit()
        return self.tags_get(tid)

    def tags_get(self, tid):
        r = conn.execute("SELECT * FROM tags WHERE id = ?", (tid,)).fetchone()
        return dict(r) if r else None

    def tags_delete(self, tid):
        conn.execute("DELETE FROM tags WHERE id = ?", (tid,))
        conn.commit()
        return True

    def note_tags_get(self, note_id):
        rows = conn.execute(
            "SELECT t.* FROM tags t JOIN note_tags nt ON t.id = nt.tag_id WHERE nt.note_id = ? ORDER BY t.name",
            (note_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def note_tags_set(self, note_id, tag_ids):
        conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
        for tid in tag_ids:
            conn.execute("INSERT OR IGNORE INTO note_tags (note_id, tag_id) VALUES (?, ?)", (note_id, tid))
        conn.commit()
        return self.note_tags_get(note_id)

    def notes_by_tag(self, tag_id, notebook_id=None):
        """某标签下的笔记；给了 notebook_id 就再叠加笔记本范围（`''` = 未分类）。

        标签与笔记本是两个独立筛选，**必须能叠加**：否则在「原神」里点一个标签，
        列表会把别的笔记本的同标签笔记也倒进来 —— 又变回"混在一起"。
        """
        sql = (
            "SELECT n.id, n.title, n.bg_type, n.bg_value, n.bg_opacity, n.is_pinned, n.is_favorite, "
            "n.notebook_id, n.sort_order, n.created_at, n.updated_at, "
            "CASE WHEN n.password_hash IS NOT NULL AND n.password_hash != '' THEN 1 ELSE 0 END AS has_password "
            "FROM notes n JOIN note_tags nt ON n.id = nt.note_id "
            "WHERE nt.tag_id = ? AND n.deleted_at IS NULL"
        )
        params = [tag_id]
        if notebook_id is not None:
            if notebook_id == '':
                sql += " AND n.notebook_id IS NULL"
            else:
                sql += " AND n.notebook_id = ?"
                params.append(notebook_id)
        sql += " ORDER BY n.is_pinned DESC, n.sort_order DESC, n.updated_at DESC"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ----- 笔记本 -----
    def notebooks_list(self):
        rows = conn.execute("SELECT * FROM notebooks ORDER BY sort_order, created_at").fetchall()
        return [dict(r) for r in rows]

    def notebook_counts(self):
        """笔记数的唯一来源：每本多少篇 + 未分类 + 总数（不含回收站）。

        列表按笔记本过滤后，前端手里只有当前那一本的笔记，**再也不能**靠 state.notes 数出
        "全部笔记有几篇、别的笔记本有几篇"（下拉里那些 `N 篇` 会全变成假的）。所以计数一律
        由这里给：一次 GROUP BY，前端只在刷新笔记本栏时拉一次。
        """
        rows = conn.execute(
            "SELECT notebook_id, COUNT(*) AS n FROM notes "
            "WHERE deleted_at IS NULL GROUP BY notebook_id").fetchall()
        by_id = {r['notebook_id']: r['n'] for r in rows if r['notebook_id']}
        uncategorized = sum(r['n'] for r in rows if not r['notebook_id'])
        return {'by_id': by_id, 'uncategorized': uncategorized,
                'total': uncategorized + sum(by_id.values())}

    def notebooks_create(self, name='新建笔记本'):
        nid = str(uuid.uuid4())
        conn.execute("INSERT INTO notebooks (id, name, sort_order) VALUES (?, ?, ?)",
                     (nid, name, _next_sort_order('notebooks')))
        conn.commit()
        return dict(conn.execute("SELECT * FROM notebooks WHERE id = ?", (nid,)).fetchone())

    def notebooks_update(self, nid, fields):
        allowed = {'name', 'sort_order', 'color', 'cover_type', 'default_paper'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates: return None
        sets = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE notebooks SET {sets} WHERE id = ?", list(updates.values()) + [nid])
        conn.commit()
        return dict(conn.execute("SELECT * FROM notebooks WHERE id = ?", (nid,)).fetchone())

    def notebooks_delete(self, nid):
        # 将该笔记本下的笔记移到未分类
        conn.execute("UPDATE notes SET notebook_id = NULL WHERE notebook_id = ?", (nid,))
        conn.execute("DELETE FROM notebooks WHERE id = ?", (nid,))
        conn.commit()
        return True

    # ----- 历史版本 -----
    def versions_list(self, note_id):
        rows = conn.execute(
            "SELECT id, title, created_at FROM versions WHERE note_id = ? ORDER BY created_at DESC LIMIT 50",
            (note_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def versions_get(self, vid, unlocked=False):
        """获取版本详情。父笔记加密时仅在后端已解锁后返回解密内容（unlocked 参数保留兼容，不作依据）。"""
        r = conn.execute("SELECT * FROM versions WHERE id = ?", (vid,)).fetchone()
        if not r:
            return None
        ver = dict(r)
        # 检查父笔记是否加密
        parent = conn.execute("SELECT password_hash FROM notes WHERE id = ?", (ver['note_id'],)).fetchone()
        if parent and parent['password_hash']:
            dek = _unlocked_deks.get(ver['note_id'])
            pt = _decrypt_content(dek, ver['content'], ver['note_id']) if dek else None
            if pt is None:
                ver['content'] = ''
                ver['is_encrypted'] = True
            else:
                ver['content'] = pt
                ver['is_encrypted'] = False
        else:
            ver['is_encrypted'] = False
        return ver

    def versions_restore(self, vid, unlocked=False):
        """恢复到指定版本。父笔记加密且后端未解锁时拒绝。"""
        r = conn.execute("SELECT * FROM versions WHERE id = ?", (vid,)).fetchone()
        if not r:
            return None
        ver = dict(r)
        parent = conn.execute("SELECT password_hash FROM notes WHERE id = ?", (ver['note_id'],)).fetchone()
        if parent and parent['password_hash'] and ver['note_id'] not in _unlocked_deks:
            return None  # 笔记已加密且未解锁，拒绝恢复
        # 版本与正文共用同一 DEK 且 AAD 均为 note_id，密文可直接拷贝，无需解密重加密
        conn.execute(
            "UPDATE notes SET title = ?, content = ?, format = ?, "
            "updated_at = datetime('now','localtime') WHERE id = ?",
            (ver['title'], ver['content'], ver.get('format') or 'delta', ver['note_id'])
        )
        _fts_sync_from_row(ver['note_id'])
        _refresh_derived(ver['note_id'])
        conn.commit()
        return self.notes_get(ver['note_id'])

    def versions_delete(self, vid):
        """删除单个历史版本"""
        conn.execute("DELETE FROM versions WHERE id = ?", (vid,))
        conn.commit()
        return True

    def versions_delete_all(self, note_id):
        """删除笔记的全部历史版本"""
        conn.execute("DELETE FROM versions WHERE note_id = ?", (note_id,))
        conn.commit()
        return True

    def versions_create(self, note_id, title, content):
        # 加密笔记：必须已在后端解锁，版本内容加密后入库
        parent = conn.execute("SELECT password_hash, format FROM notes WHERE id = ?",
                              (note_id,)).fetchone()
        dek = None
        if parent and parent['password_hash']:
            dek = _unlocked_deks.get(note_id)
            if dek is None:
                return None  # 未解锁，不创建版本
        # 检查是否和最新版本相同（密文含随机 nonce 不可直接比较，需解密后比较明文）
        last = conn.execute(
            "SELECT content FROM versions WHERE note_id = ? ORDER BY created_at DESC LIMIT 1",
            (note_id,)
        ).fetchone()
        if last:
            last_content = last['content']
            if dek is not None:
                last_content = _decrypt_content(dek, last_content, note_id)
            if last_content == content:
                return None  # 内容没变，不创建版本
        store_content = _encrypt_content(dek, content, note_id) if dek is not None else content
        vid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO versions (id, note_id, title, content, format) VALUES (?, ?, ?, ?, ?)",
            (vid, note_id, title, store_content,
             (parent['format'] if parent else None) or 'delta')
        )
        # 清理旧版本（保留最新 50 个）
        conn.execute("""
            DELETE FROM versions WHERE id IN (
                SELECT id FROM versions WHERE note_id = ?
                ORDER BY created_at DESC LIMIT -1 OFFSET 50
            )
        """, (note_id,))
        conn.commit()
        return vid

    # ----- 密码 (PBKDF2 验证 + AES-GCM 内容加密) -----
    def _hash_password(self, password):
        salt = os.urandom(32)
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ITERATIONS)
        return f"pbkdf2v2:{salt.hex()}:{PBKDF2_ITERATIONS}:{key.hex()}"

    def _verify_hash(self, password, stored):
        try:
            parts = stored.split(':')
            if parts[0] == 'pbkdf2v2' and len(parts) == 4:
                salt, key = bytes.fromhex(parts[1]), bytes.fromhex(parts[3])
                # 迭代数也要钳制 —— 与 _unwrap_dek 同一条理由：迭代数存在库里，损坏或被篡改
                # 的库塞个天文数字进来，就能把"输一次密码"变成纯 CPU 卡死（PBKDF2 无法短路
                # 失败，而且这些方法还持有全局 _db_lock，会把所有桥调用一起拖住）。
                iters = min(int(parts[2]), MAX_PBKDF2_ITERATIONS)
                if iters < 1:
                    return False
                return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iters), key)
            if len(parts) == 2:
                # 旧 salt:key 格式（未存迭代数），依次尝试 600000 / 200000 保持兼容
                salt, key = bytes.fromhex(parts[0]), bytes.fromhex(parts[1])
                for iterations in (600000, 200000):
                    if hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations), key):
                        return True
            return False
        except Exception:
            return False

    def _encrypt_all_note_content(self, note_id, dek):
        """用 DEK 加密笔记正文和全部历史版本（幂等：只处理明文行），单事务提交"""
        row = conn.execute("SELECT content FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row and not (row['content'] or '').startswith(ENC_PREFIX):
            conn.execute("UPDATE notes SET content = ? WHERE id = ?",
                         (_encrypt_content(dek, row['content'], note_id), note_id))
        for v in conn.execute("SELECT id, content FROM versions WHERE note_id = ?", (note_id,)).fetchall():
            if not (v['content'] or '').startswith(ENC_PREFIX):
                conn.execute("UPDATE versions SET content = ? WHERE id = ?",
                             (_encrypt_content(dek, v['content'], note_id), v['id']))
        # 加密后必须立刻清空派生索引：待办明细/字数当初是按明文建的，
        # 留在 note_todos 里就等于把明文侧面泄漏出去（FTS body 也是同一条约定）
        _refresh_derived(note_id)
        conn.commit()

    def _decrypt_all_note_content(self, note_id, dek):
        """解密笔记正文和全部历史版本回明文；任一行解密失败返回 False（不提交）"""
        row = conn.execute("SELECT content FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row and (row['content'] or '').startswith(ENC_PREFIX):
            pt = _decrypt_content(dek, row['content'], note_id)
            if pt is None:
                return False
            conn.execute("UPDATE notes SET content = ? WHERE id = ?", (pt, note_id))
        for v in conn.execute("SELECT id, content FROM versions WHERE note_id = ?", (note_id,)).fetchall():
            if (v['content'] or '').startswith(ENC_PREFIX):
                pt = _decrypt_content(dek, v['content'], note_id)
                if pt is None:
                    conn.rollback()
                    return False
                conn.execute("UPDATE versions SET content = ? WHERE id = ?", (pt, v['id']))
        _refresh_derived(note_id)      # 回到明文：派生索引可以按明文重建了
        conn.commit()
        return True

    def note_set_password(self, note_id, password):
        """设置密码并加密笔记内容（正文 + 全部历史版本）"""
        if not password or len(password) < 6:
            return False
        row = conn.execute("SELECT id, enc_dek, password_hash FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            return False
        # 防御：**已有密码但未解锁**时禁止直接覆盖密码。
        # 判据必须看 password_hash 而不只是 enc_dek：存量「明文密码」笔记（旧版本设过密码、
        # 还没走过懒迁移）正好是 password_hash 非空而 enc_dek 为 NULL —— 只查 enc_dek 的话
        # 守卫整个被跳过，任何调用方都能用**自选的新密码**调本函数，随后该笔记就被算作"已解锁"，
        # 原本被隐藏的正文直接被读出来（实测复现）。改密码请走 note_change_password（要验旧密码）。
        if row['password_hash'] and note_id not in _unlocked_deks:
            return False
        # 复用已解锁的 DEK（旧格式升级等路径），否则生成新 DEK
        dek = _unlocked_deks.get(note_id) or os.urandom(32)
        conn.execute("UPDATE notes SET password_hash = ?, enc_dek = ? WHERE id = ?",
                     (self._hash_password(password), _wrap_dek(dek, password), note_id))
        self._encrypt_all_note_content(note_id, dek)  # 内部 commit
        _fts_sync_from_row(note_id)  # 加密后 body 清空（索引绝不留明文正文）
        conn.commit()
        _unlocked_deks[note_id] = dek  # 设完保持解锁
        return True

    def note_verify_password(self, note_id, password):
        """验证密码；成功时解包 DEK 存入后端缓存（= 解锁）。存量明文密码笔记在此懒迁移为密文。"""
        row = conn.execute(
            "SELECT password_hash, enc_dek FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if not row or not row['password_hash']:
            return True  # 没有密码的笔记直接通过
        stored_hash = row['password_hash']
        # 验证（兼容旧裸 SHA-256 无冒号格式）。
        # 用 compare_digest 而不是 !=：这是哈希比较，逐字节提前返回会泄漏"前几位对上了"的
        # 时间信号。本仓库其它两处哈希比较（_verify_hash）都用的是 compare_digest，这里漏了。
        # （旧格式本身是无盐 SHA-256，未迁移前可被彩虹表爆破 —— 这是历史包袱，迁移后即消失；
        #   能做的是不再额外泄漏时间信息。）
        if ':' not in stored_hash:
            if not hmac.compare_digest(hashlib.sha256(password.encode()).hexdigest(), stored_hash):
                return False
            needs_rehash = True
        else:
            if not self._verify_hash(password, stored_hash):
                return False
            needs_rehash = not stored_hash.startswith('pbkdf2v2:')
        # 取 DEK：解包已有 enc_dek；或为存量明文笔记生成 DEK 并迁移内容为密文
        if row['enc_dek']:
            dek = _unwrap_dek(row['enc_dek'], password)
            if dek is None:
                return False  # enc_dek 损坏（理论上不应发生）
        else:
            dek = os.urandom(32)
            conn.execute("UPDATE notes SET enc_dek = ? WHERE id = ?", (_wrap_dek(dek, password), note_id))
            self._encrypt_all_note_content(note_id, dek)  # 幂等，崩溃后下次解锁补齐
        if needs_rehash:
            conn.execute("UPDATE notes SET password_hash = ? WHERE id = ?",
                         (self._hash_password(password), note_id))
            conn.commit()
        _unlocked_deks[note_id] = dek
        return True

    def note_change_password(self, note_id, old_password, new_password):
        """改密码：重新包裹同一个 DEK，内容零重加密"""
        if not new_password or len(new_password) < 6:
            return False
        if not self.note_verify_password(note_id, old_password):
            return False
        dek = _unlocked_deks.get(note_id)
        if dek is None:
            return False
        conn.execute("UPDATE notes SET password_hash = ?, enc_dek = ? WHERE id = ?",
                     (self._hash_password(new_password), _wrap_dek(dek, new_password), note_id))
        conn.commit()
        return True

    def note_remove_password(self, note_id, password):
        """移除密码并把内容解密回明文"""
        if not self.note_verify_password(note_id, password):
            return False
        dek = _unlocked_deks.get(note_id)
        if dek is not None and not self._decrypt_all_note_content(note_id, dek):
            return False  # 解密失败，保守不动
        conn.execute("UPDATE notes SET password_hash = NULL, enc_dek = NULL WHERE id = ?", (note_id,))
        _fts_sync_from_row(note_id)  # 回明文后正文重新入索引
        _refresh_derived(note_id)    # 明文可索引了，派生指标要跟着填回来
        conn.commit()
        _unlocked_deks.pop(note_id, None)
        return True

    def note_lock(self, note_id):
        """锁定笔记：清除后端解锁缓存"""
        _unlocked_deks.pop(note_id, None)
        return True

    def note_unlock_status(self):
        """当前**后端**还认为处于解锁状态的 note_id 列表（顺带做一次过期清理）。

        用途：让界面与后端对齐。以前"自动锁定"的判据只有一个 —— 前端自己的
        `unlockedNotes` 对象；后端过期了、前端没跟上，界面就会继续显示明文，
        等于没锁。现在前端每轮提醒轮询（30s）顺带问一次这个接口：
        返回的集合里没有的笔记，就按"已锁定"处理（清编辑器 + 隐藏编辑区）。
        """
        _unlocked_deks.sweep()
        return list(dict.keys(_unlocked_deks))

    def note_set_lock_ttl(self, minutes):
        """设置后端解锁缓存的有效期（分钟；0 = 不过期）。

        与前端那个「闲置自动锁定」下拉是**同一条设置的两种执行者**：
        前端负责"用户一停手就锁"的即时体验，后端这层是兜底 ——
        前端脚本出错/窗口被挂起/用户直接改内存标志时，密钥也不会在进程里长留。
        """
        try:
            m = int(minutes)
        except (TypeError, ValueError):
            return False
        _unlocked_deks.ttl_minutes = max(0, min(24 * 60, m))
        if _unlocked_deks.ttl_minutes:
            _unlocked_deks.sweep()      # 立刻按新 TTL 清一遍
        return True

    def note_has_password(self, note_id):
        stored = conn.execute(
            "SELECT password_hash FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return bool(stored and stored['password_hash'])

    # ----- 前端错误上报 -----
    def log_error(self, message, stack='', source='js'):
        """前端 window.onerror/unhandledrejection 上报（只记消息+栈，不含笔记内容）"""
        try:
            msg = str(message)[:2048]
            stk = str(stack)[:8192]
            applog.get_logger().error("[js:%s] %s\n%s", str(source)[:64], msg, stk)
        except Exception:
            pass
        return True

    # ----- 提醒 -----
    def reminder_set(self, note_id, reminder_time):
        """兼容旧接口：创建一次性提醒"""
        note = self.notes_get(note_id)
        content = note['title'] if note else ''
        return self.reminder_create(note_id, content, reminder_time, 'none', 1)

    def reminder_cancel(self, note_id):
        """兼容旧接口：删除该笔记的所有提醒"""
        conn.execute("DELETE FROM reminders WHERE note_id = ?", (note_id,))
        conn.commit()
        return True

    def reminders_check(self):
        """兼容旧接口：检查到期的提醒，返回列表"""
        return self.reminder_check()

    # ----- 新提醒 API -----
    def reminder_create(self, note_id, content, remind_at, repeat_type='none', repeat_interval=1):
        """创建提醒"""
        rid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO reminders (id, note_id, content, remind_at, repeat_type, repeat_interval) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (rid, note_id, content, remind_at, repeat_type, repeat_interval)
        )
        conn.commit()
        return self.reminder_get(rid)

    def reminder_get(self, reminder_id):
        """获取单条提醒"""
        r = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
        return dict(r) if r else None

    def reminder_update(self, reminder_id, fields):
        """更新提醒"""
        allowed = {'content', 'remind_at', 'repeat_type', 'repeat_interval', 'is_completed'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return None
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [reminder_id]
        conn.execute(f"UPDATE reminders SET {sets} WHERE id = ?", vals)
        conn.commit()
        return self.reminder_get(reminder_id)

    def reminder_delete(self, reminder_id):
        """删除单条提醒"""
        conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()
        return True

    def reminder_list(self, note_id=None):
        """列出提醒（可选按笔记筛选，仅返回未完成的）"""
        if note_id:
            rows = conn.execute(
                "SELECT r.* FROM reminders r INNER JOIN notes n ON r.note_id = n.id "
                "WHERE r.note_id = ? AND r.is_completed = 0 AND n.deleted_at IS NULL "
                "ORDER BY r.remind_at ASC",
                (note_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT r.* FROM reminders r INNER JOIN notes n ON r.note_id = n.id "
                "WHERE r.is_completed = 0 AND n.deleted_at IS NULL "
                "ORDER BY r.remind_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def reminder_list_all(self):
        """列出所有提醒（包括已完成的，用于管理面板）"""
        # 决策：软删笔记的提醒不显示（行保留，恢复笔记后自动重现）
        rows = conn.execute(
            "SELECT r.*, n.title as note_title FROM reminders r "
            "INNER JOIN notes n ON r.note_id = n.id AND n.deleted_at IS NULL "
            "ORDER BY r.is_completed ASC, r.remind_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def reminder_check(self):
        """检查到期的提醒：只返回 24h 内的（防启动时补弹一堆过期提醒）。
        过期 >24h 的重复提醒自动推进到未来首次触发；一次性提醒标记完成（管理面板仍可见）。"""
        # 搭车清理解锁缓存：提醒是 30 秒轮询的（前端与托盘守护各一条），够密。
        # 这样"惰性过期"之外还有一层主动过期 —— 用户挂着不动时密钥也不会一直留在进程里。
        # 刻意不新开线程：这个应用已经有主循环 + 提醒守护 + 热键三条线程了。
        _unlocked_deks.sweep()
        # INNER JOIN notes + deleted 过滤：软删笔记的提醒不再触发（恢复笔记后提醒自动重现）
        due = conn.execute(
            "SELECT r.* FROM reminders r INNER JOIN notes n ON r.note_id = n.id "
            "WHERE n.deleted_at IS NULL AND r.remind_at <= datetime('now','localtime') "
            "AND r.is_completed = 0 AND r.remind_at > datetime('now','localtime', '-1 day')"
        ).fetchall()
        stale = conn.execute(
            "SELECT r.* FROM reminders r INNER JOIN notes n ON r.note_id = n.id "
            "WHERE n.deleted_at IS NULL AND r.remind_at <= datetime('now','localtime', '-1 day') "
            "AND r.is_completed = 0"
        ).fetchall()
        for r in stale:
            if r['repeat_type'] != 'none':
                self.reminder_update_next_repeat(r['id'])
            else:
                self.reminder_complete(r['id'])
        return [dict(r) for r in due]

    def reminder_complete(self, reminder_id):
        """标记提醒为已完成"""
        conn.execute("UPDATE reminders SET is_completed = 1 WHERE id = ?", (reminder_id,))
        conn.commit()
        return True

    def reminder_snooze(self, reminder_id, minutes):
        """推迟提醒 N 分钟"""
        from datetime import timedelta
        r = self.reminder_get(reminder_id)
        if not r:
            return None
        old_time = _parse_remind_at(r['remind_at'])
        if old_time is None:
            return None  # 时间格式异常，不修改
        new_time = old_time + timedelta(minutes=minutes)
        new_time_str = new_time.strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("UPDATE reminders SET remind_at = ? WHERE id = ?", (new_time_str, reminder_id))
        conn.commit()
        return self.reminder_get(reminder_id)

    def reminder_update_next_repeat(self, reminder_id):
        """重复提醒触发后：自动计算下一次提醒时间；过期的直接推进到未来首次触发（防启动连弹）"""
        from datetime import datetime, timedelta
        r = self.reminder_get(reminder_id)
        if not r or r['repeat_type'] == 'none':
            self.reminder_complete(reminder_id)
            return None

        old_time = _parse_remind_at(r['remind_at'])
        if old_time is None:
            # 时间格式异常无法计算下次 → 标记完成，不无限滞留
            self.reminder_complete(reminder_id)
            return None
        interval = r.get('repeat_interval', 1)
        repeat_type = r['repeat_type']

        def _next(t):
            """计算 t 的下一次触发；未知重复类型返回 None"""
            if repeat_type == 'daily':
                return t + timedelta(days=interval)
            if repeat_type == 'weekly':
                return t + timedelta(weeks=interval)
            if repeat_type == 'weekday':
                # 跳到下一个工作日（周一到周五）
                n = t + timedelta(days=1)
                while n.weekday() >= 5:  # 5=周六, 6=周日
                    n += timedelta(days=1)
                return n
            if repeat_type == 'monthly':
                # 加 N 个月
                month = t.month - 1 + interval
                year = t.year + month // 12
                month = month % 12 + 1
                day = min(t.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                                  31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
                return t.replace(year=year, month=month, day=day)
            if repeat_type == 'yearly':
                # 2/29 的年度提醒在平年无此日，需像 monthly 一样钳制到月底（否则 replace 抛 ValueError）
                year = t.year + interval
                feb = 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28
                return t.replace(year=year, day=min(t.day, feb))
            return None

        new_time = _next(old_time)
        if new_time is None:
            self.reminder_complete(reminder_id)
            return None
        # 应用关闭错过多个周期时，推进到未来首次触发。逐档步进保证「月末钳制 / 跳过周末」
        # 语义正确（不能按周期数直接跳）；100_000 档上限只兜底异常远古数据（如 1900 年），
        # 正常过期场景几千档内即完成。撞上限仍不到未来 → 标记完成，避免每次轮询反复处理滞留项。
        now = datetime.now()
        guard = 0
        while new_time <= now and guard < 100_000:
            new_time = _next(new_time)
            guard += 1
        if new_time <= now:
            self.reminder_complete(reminder_id)
            return None
        conn.execute("UPDATE reminders SET remind_at = ? WHERE id = ?",
                     (new_time.strftime('%Y-%m-%d %H:%M:%S'), reminder_id))
        conn.commit()
        return self.reminder_get(reminder_id)

    # ----- 导出笔记 -----
    def _snapshot_db(self, dest):
        """用 sqlite backup API 把当前库页级一致地快照到 dest（遇写入自动重启）"""
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()

    @staticmethod
    def _zip_dir(zf, base, arc):
        if os.path.isdir(base):
            for root, _, files in os.walk(base):
                for fn in files:
                    fp = os.path.join(root, fn)
                    zf.write(fp, os.path.join(arc, os.path.relpath(fp, base)).replace('\\', '/'))

    def export_all_to_zip(self, save_path):
        """一键全库导出：sqlite backup API 快照 notes.db + attachments/ + backgrounds/ 归档为 zip。
        snapshot 页级一致（遇写入自动重启），归档内容与备份包同构，可直接替换 data/ 目录恢复。"""
        try:
            import zipfile
            tmp = tempfile.mkdtemp(prefix='mynotepad_export_')
            try:
                snap = os.path.join(tmp, 'notes.db')
                self._snapshot_db(snap)
                with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(snap, 'notes.db')
                    self._zip_dir(zf, ATTACH_DIR, 'attachments')
                    self._zip_dir(zf, os.path.join(DATA_DIR, 'backgrounds'), 'backgrounds')
                return True
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            applog.get_logger().exception("全库导出失败")
            return False

    def export_notes_zip(self, save_path, notebook_id=None, tag_id=None):
        """按范围导出为一个**可当库打开**的 zip（笔记 + 标签 + 附件 + 背景图）。

        做法：先页级快照，再在**快照副本**里删掉范围外的笔记（FK 级联清 attachments/
        note_tags/versions/reminders），然后只把范围内的附件与背景图打进 zip。
        这样导出的包与全库备份同构——解压后替换 data/ 就是一个只含这些笔记的记事本，
        而不是一堆需要手工整理的散文件。

        返回导出的笔记数；无匹配返回 0；出错返回 None。
        """
        where, params = ["deleted_at IS NULL"], []
        if notebook_id:
            where.append("notebook_id = ?")
            params.append(notebook_id)
        if tag_id:
            where.append("id IN (SELECT note_id FROM note_tags WHERE tag_id = ?)")
            params.append(tag_id)
        clause = " AND ".join(where)
        try:
            ids = [r['id'] for r in conn.execute(
                "SELECT id FROM notes WHERE " + clause, params).fetchall()]
            if not ids:
                return 0
            import zipfile
            tmp = tempfile.mkdtemp(prefix='mynotepad_export_')
            try:
                snap = os.path.join(tmp, 'notes.db')
                self._snapshot_db(snap)
                # 在快照副本里裁剪：必须是另一个连接，且开 FK 才能级联清子表
                c = sqlite3.connect(snap)
                try:
                    c.execute("PRAGMA foreign_keys=ON")
                    # ⚠️ `NOT IN` **不能**像 IN 那样简单分块：每块只知道自己的集合，
                    # 分块后第二块会把第一块要保留的笔记当成"不在我这一块里"删掉。
                    # 正确做法是先算出"要删的那些 id"（全集 - 保留集），再对删除集分块。
                    keep = {str(i) for i in ids}
                    all_ids = [r[0] for r in c.execute("SELECT id FROM notes").fetchall()]
                    doomed = [i for i in all_ids if i not in keep]
                    for chunk in _chunked(doomed):
                        marks = ','.join('?' * len(chunk))
                        c.execute("DELETE FROM notes WHERE id IN (%s)" % marks, chunk)
                    c.commit()
                finally:
                    c.close()
                with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(snap, 'notes.db')
                    for nid in ids:                      # 只打包这些笔记的附件目录
                        self._zip_dir(zf, os.path.join(ATTACH_DIR, nid),
                                      os.path.join('attachments', nid))
                    bg = os.path.join(DATA_DIR, 'backgrounds')
                    used_bg = set()
                    for chunk in _chunked(ids):     # 分块：范围导出可能含上千篇
                        marks = ','.join('?' * len(chunk))
                        for r in conn.execute(
                                "SELECT bg_value FROM notes WHERE id IN (%s)" % marks,
                                chunk).fetchall():
                            v = r['bg_value'] or ''
                            if v and os.path.isfile(v):
                                used_bg.add(os.path.basename(v))
                    for name in used_bg:                 # 只带这些笔记用到的背景图
                        fp = os.path.join(bg, name)
                        if os.path.isfile(fp):
                            zf.write(fp, 'backgrounds/' + name)
                    zf.writestr('导出说明.txt',
                                '本包含 %d 篇笔记（%s）。\n\n'
                                '恢复方法：退出「我的记事本」，把这里的 notes.db、attachments/、\n'
                                'backgrounds/ 覆盖到程序目录的 data/ 下即可（建议先备份原 data/）。\n'
                                % (len(ids), '按笔记本' if notebook_id else '按标签'))
                return len(ids)
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            applog.get_logger().exception("按范围导出失败")
            return None

    def export_note_markdown(self, note_id, save_path):
        """导出单篇为 Markdown：图片复制到同级 `<文件名>.assets/` 并写成相对路径。

        为什么要复制图片：正文里的图片存在 attachments/<note_id>/ 下，只写绝对路径的 .md
        换台机器就全断图；复制到同级目录后整个文件夹可以整体带走。
        加密笔记只在已解锁时可导出（与复制笔记同一条规则）。
        """
        try:
            row = conn.execute("SELECT * FROM notes WHERE id = ? AND deleted_at IS NULL",
                               (note_id,)).fetchone()
            if not row:
                return False
            note = dict(row)
            content = note.get('content') or ''
            if note.get('password_hash'):
                dek = _unlocked_deks.get(note_id)
                if dek is None:
                    return False
                content = _decrypt_content(dek, content, note_id) or ''
            base = os.path.splitext(save_path)[0]
            assets_dir = base + '.assets'
            used = {}

            def resolve(name):
                if not name:
                    return ''
                src = os.path.join(ATTACH_DIR, note_id, name)
                if not os.path.isfile(src):
                    return ''
                if not os.path.isdir(assets_dir):
                    os.makedirs(assets_dir, exist_ok=True)
                dest = os.path.join(assets_dir, name)
                if name not in used:
                    shutil.copy2(src, dest)
                    used[name] = True
                return os.path.basename(assets_dir) + '/' + name

            md = _delta_to_markdown(content, resolve_image=resolve)
            title = note.get('title') or '未命名笔记'
            with open(save_path, 'w', encoding='utf-8') as f:
                if not md.lstrip().startswith('#'):
                    f.write('# %s\n\n' % title)     # 标题补成一级标题，导出即可读
                f.write(md)
            return True
        except Exception:
            applog.get_logger().exception("Markdown 导出失败")
            return False

    def import_markdown(self, md_text, title=None, notebook_id=None):
        """把 Markdown 文本导入为一篇新笔记（**原样保存，不做转换**），返回新笔记。

        第 6 轮起 Markdown 是原生格式：导入就是"把这份文本交给 Markdown 编辑器"，
        因此逐字节保存——转成 Delta 反而会引入有损转换（贴纸/分割线/内联 HTML 那些）。
        需要富文本时用户可以自己点格式徽标转换。
        """
        try:
            text = md_text or ''
            if not title:
                for line in text.splitlines():
                    if line.strip():
                        title = re.sub(r'^#{1,6}\s*', '', line.strip())[:60]
                        break
            nid = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO notes (id, title, content, notebook_id, sort_order, format) "
                "VALUES (?, ?, ?, ?, ?, 'md')",
                (nid, title or '导入的笔记', text, notebook_id or None,
                 _next_sort_order('notes')))
            _fts_sync(nid, title or '导入的笔记', text, 'md')
            _refresh_derived(nid, text, 'md')
            conn.commit()
            return self.notes_get(nid)
        except Exception:
            applog.get_logger().exception("Markdown 导入失败")
            return None

    def export_note(self, title, html_content, format_type, save_path):
        """导出笔记为指定格式"""
        if format_type == 'html':
            return self._export_html(title, html_content, save_path)
        elif format_type == 'txt':
            return self._export_txt(html_content, save_path)
        elif format_type == 'docx':
            return self._export_docx(title, html_content, save_path)
        elif format_type == 'xlsx':
            return self._export_xlsx(title, html_content, save_path)
        else:
            return False

    def _export_html(self, title, html_content, save_path):
        import html
        safe_title = html.escape(title)
        template = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{safe_title}</title>
<style>
  body {{ max-width: 800px; margin: 40px auto; padding: 0 20px;
         font-family: "Microsoft YaHei", sans-serif; font-size: 16px;
         line-height: 1.8; color: #333; }}
  h1 {{ font-size: 24px; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
  img {{ max-width: 100%; border-radius: 8px; margin: 10px 0; }}
</style>
</head>
<body>
<h1>{safe_title}</h1>
{html_content}
</body>
</html>'''
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(template)
        return True

    def _export_txt(self, html_content, save_path):
        import re
        # 简单的 HTML 标签去除
        text = re.sub(r'<br\s*/?>', '\n', html_content)
        text = re.sub(r'</p>', '\n\n', text)
        text = re.sub(r'</div>', '\n', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        with open(save_path, 'w', encoding='utf-8') as f:
            f.write(text)
        return True

    def _export_docx(self, title, html_content, save_path):
        try:
            import base64
            import os
            import re

            from docx import Document
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.shared import Inches

            doc = Document()

            # 标题
            title_para = doc.add_heading(title, level=0)
            title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

            # 解析 HTML 并转换为 docx 段落
            # 按块级元素分割
            blocks = re.split(r'(</?(?:p|h[1-6]|div|br|img)[^>]*>)', html_content)
            current_text = ''

            for block in blocks:
                if not block.strip():
                    continue

                # 图片处理
                img_match = re.match(r'<img[^>]+src="([^"]+)"', block)
                if img_match:
                    # 先输出当前累积的文本
                    if current_text.strip():
                        para = doc.add_paragraph()
                        self._add_formatted_runs(para, current_text)
                        current_text = ''

                    src = img_match.group(1)
                    # 处理 base64 图片
                    if src.startswith('data:image'):
                        try:
                            header, encoded = src.split(',', 1)
                            img_data = base64.b64decode(encoded)
                            import tempfile
                            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                            tmp.write(img_data)
                            tmp.close()
                            doc.add_picture(tmp.name, width=Inches(5))
                            os.unlink(tmp.name)
                        except Exception as e:
                            # 单张图片失败不该毁掉整篇导出：记录原因后降级为文字占位
                            applog.get_logger().warning(
                                'DOCX 导出内嵌图片失败（%s）：%s', src[:80], e)
                            doc.add_paragraph(f'[图片: {src[:50]}...]')
                    elif src.startswith('file:///'):
                        # Windows 下 file:///C:/... 需先 unquote 再剥掉根斜杠，否则空格路径失效
                        import urllib.parse
                        local_path = urllib.parse.unquote(src.replace('file:///', '')).lstrip('/')
                        if os.path.exists(local_path):
                            try:
                                doc.add_picture(local_path, width=Inches(5))
                            except Exception:
                                doc.add_paragraph(f'[图片: {os.path.basename(local_path)}]')
                    continue

                # 换行
                if block == '<br>' or block == '<br/>' or block == '<br />':
                    current_text += '\n'
                    continue

                # 段落结束
                if block in ('</p>', '</div>', '</h1>', '</h2>', '</h3>'):
                    if current_text.strip():
                        para = doc.add_paragraph()
                        self._add_formatted_runs(para, current_text)
                        current_text = ''
                    continue

                # 累积文本
                if not (block.startswith('<') and block.endswith('>')):
                    current_text += block

            # 输出剩余的文本
            if current_text.strip():
                para = doc.add_paragraph()
                self._add_formatted_runs(para, current_text)

            doc.save(save_path)
            return True
        except Exception as e:
            print(f"DOCX 导出失败: {e}")
            return False

    def _add_formatted_runs(self, para, html_text):
        """解析内联格式并添加到段落"""
        import re

        from docx.shared import Pt

        # 分割标签和文本
        parts = re.split(r'(</?(?:b|strong|i|em|u|span)[^>]*>)', html_text)
        bold = False
        italic = False
        underline = False
        font_size = None

        for part in parts:
            if not part: continue
            if part in ('<b>', '<strong>'): bold = True; continue
            if part in ('</b>', '</strong>'): bold = False; continue
            if part in ('<i>', '<em>'): italic = True; continue
            if part in ('</i>', '</em>'): italic = False; continue
            if part in ('<u>'): underline = True; continue
            if part in ('</u>'): underline = False; continue
            if part.startswith('<span'):
                # 提取 font-size 样式
                size_match = re.search(r'font-size:\s*(\d+)px', part)
                if size_match:
                    font_size = int(size_match.group(1))
                continue
            if part == '</span>': font_size = None; continue
            if part.startswith('<'): continue

            # 纯文本：添加 run
            clean = re.sub(r'\s+', ' ', part)
            if not clean.strip(): continue
            run = para.add_run(clean)
            run.bold = bold
            run.italic = italic
            run.underline = underline
            if font_size:
                run.font.size = Pt(font_size * 0.75)  # px → pt 近似
            else:
                run.font.size = Pt(11)

    def _export_xlsx(self, title, html_content, save_path):
        """导出为 Excel，有表格时按表格导出，保留加粗/斜体/颜色等格式"""
        try:
            import re

            from openpyxl import Workbook
            from openpyxl.cell.rich_text import CellRichText, TextBlock
            from openpyxl.cell.text import InlineFont
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

            wb = Workbook()
            ws = wb.active
            ws.title = title[:31]

            table_pattern = re.compile(r'<table>(.*?)</table>', re.DOTALL)
            tables = table_pattern.findall(html_content)

            def _parse_inline(cell_html):
                """解析单元格 HTML，返回 (纯文本, Font列表) 用于创建 CellRichText。
                如果无需特殊格式则返回 (纯文本, None)。"""
                segments = re.split(r'(<(?:b|strong|i|em|u|/b|/strong|/i|/em|/u|span[^>]*|/span)>)', cell_html)
                has_format = False
                result_segments = []
                bold = False
                italic = False
                underline = False
                color = None
                for seg in segments:
                    if not seg: continue
                    if seg in ('<b>', '<strong>'): bold = True; has_format = True; continue
                    if seg in ('</b>', '</strong>'): bold = False; continue
                    if seg in ('<i>', '<em>'): italic = True; has_format = True; continue
                    if seg in ('</i>', '</em>'): italic = False; continue
                    if seg == '<u>': underline = True; has_format = True; continue
                    if seg == '</u>': underline = False; continue
                    if seg.startswith('<span'):
                        has_format = True
                        cm = re.search(r'color:\s*([^;"]+)', seg)
                        if cm: color = cm.group(1).strip()
                        continue
                    if seg == '</span>': color = None; continue
                    if seg.startswith('<'): continue
                    clean = re.sub(r'\s+', ' ', seg)
                    if not clean.strip(): continue
                    font_kw = {'rFont': None, 'sz': 11.0}
                    if bold: font_kw['b'] = True
                    if italic: font_kw['i'] = True
                    if underline: font_kw['u'] = 'single'
                    if color:
                        c = color.lstrip('#')
                        if len(c) == 6:
                            font_kw['color'] = 'FF' + c
                    result_segments.append(TextBlock(InlineFont(**font_kw), clean))
                if not result_segments:
                    return ('', None)
                if not has_format:
                    return (''.join(s.text for s in result_segments), None)
                return (None, result_segments)

            # 防止公式注入
            def _sanitize(v):
                if v and v[0] in '=+-@':
                    return "'" + v
                return v

            if tables:
                thin_border = Border(
                    left=Side(style='thin'), right=Side(style='thin'),
                    top=Side(style='thin'), bottom=Side(style='thin')
                )
                header_fill = PatternFill(start_color='E8EFFF', end_color='E8EFFF', fill_type='solid')
                header_font = Font(bold=True, size=11)
                body_font = Font(size=11)

                row_idx = 1
                for table_html in tables:
                    rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
                    for tr in rows:
                        cells = re.findall(r'<t[dh]>(.*?)</t[dh]>', tr, re.DOTALL)
                        for col_idx, cell_html in enumerate(cells, 1):
                            plain, rich = _parse_inline(cell_html)
                            is_header = (row_idx == 1)
                            # 判断该行是否在 thead 中（通过检查原始 HTML）
                            if not is_header and row_idx > 1:
                                thead_match = re.search(r'<thead>(.*?)</thead>', table_html, re.DOTALL)
                                if thead_match:
                                    thead_rows = len(re.findall(r'<tr>', thead_match.group(1), re.DOTALL))
                                    if row_idx <= thead_rows:
                                        is_header = True

                            if rich:
                                cell = ws.cell(row=row_idx, column=col_idx)
                                cell.value = CellRichText(*rich)
                            else:
                                value = _sanitize(plain.strip())
                                cell = ws.cell(row=row_idx, column=col_idx, value=value)
                            cell.border = thin_border
                            cell.alignment = Alignment(vertical='center', wrap_text=True)
                            if is_header:
                                cell.font = header_font
                                cell.fill = header_fill
                            else:
                                cell.font = body_font
                        row_idx += 1
                    row_idx += 1

                for col in ws.columns:
                    max_length = 0
                    col_letter = col[0].column_letter
                    for cell in col:
                        if cell.value:
                            val = str(cell.value)
                            max_length = max(max_length, len(val))
                    ws.column_dimensions[col_letter].width = min(max_length + 4, 40)
            else:
                text = re.sub(r'<br\s*/?>', '\n', html_content)
                text = re.sub(r'</p>', '\n', text)
                text = re.sub(r'<[^>]+>', '', text)
                lines = [l.strip() for l in text.split('\n') if l.strip()]
                for i, line in enumerate(lines, 1):
                    ws.cell(row=i, column=1, value=line).font = Font(size=11)
                ws.column_dimensions['A'].width = 80

            wb.save(save_path)
            return True
        except Exception as e:
            print(f"XLSX 导出失败: {e}")
            return False

    def read_file_base64(self, file_path):
        """读取文件为 base64。仅允许附件目录、数据目录和系统临时目录中的文件。"""
        # 规范化路径
        try:
            real_path = os.path.realpath(os.path.normpath(file_path))
        except (ValueError, TypeError):
            return None
        # 白名单：只允许以下目录中的文件
        allowed_dirs = [
            os.path.realpath(ATTACH_DIR),
            os.path.realpath(DATA_DIR),
            os.path.realpath(tempfile.gettempdir()),
        ]
        # 也允许 EXE_DIR 下的 resources 目录（用于背景图片）
        resources_dir = os.path.realpath(os.path.join(_EXE_DIR, "resources"))
        allowed_dirs.append(resources_dir)
        # 检查文件是否在白名单目录内
        allowed = any(real_path.startswith(d + os.sep) or real_path == d for d in allowed_dirs)
        if not allowed:
            return None
        # 拒绝路径遍历
        if '..' in file_path.replace('\\', '/'):
            return None
        if not os.path.isfile(real_path):
            return None
        # 禁止读取数据库文件
        if real_path.endswith(('.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3')):
            return None
        try:
            with open(real_path, 'rb') as f:
                data = base64.b64encode(f.read()).decode('utf-8')
            ext = os.path.splitext(real_path)[1].lower()
            mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.gif': 'image/gif', '.bmp': 'image/bmp', '.webp': 'image/webp',
                        '.svg': 'image/svg+xml'}
            mime = mime_map.get(ext, 'image/png')
            return f"data:{mime};base64,{data}"
        except Exception:
            return None


def _format_size(size):
    if size < 1024: return f"{size} B"
    elif size < 1024*1024: return f"{size/1024:.1f} KB"
    else: return f"{size/(1024*1024):.1f} MB"


# ====== 路径包含性校验（唯一入口）======
# 为什么需要统一助手：这些路径参数全部来自前端/正文，属于**信任边界之外**的值。
# 项目以前散着 6 处各写各的内联校验，写法不一致，于是漏了几处（附件的 note_id/filename、
# file_copy_to_note 的 note_id、OCR 的图片路径）。
#
# ⚠️ 顺序至关重要：**先做字符串级拒绝，再 realpath**。
# 反过来的话，`realpath(r'\\attacker\share\x.png')` 会真的去解析这个 UNC 路径 ——
# 那是一次对外的 SMB 连接，会泄露 NetNTLM 响应；`os.path.join` 遇到 UNC/绝对路径组件还会
# **丢弃前面的基础目录**（`join('C:\\base', '\\\\a\\b')` == `'\\\\a\\b'`），白名单比较就成了摆设。
_REJECT_PATH_CHARS = ('\\', '/', ':')      # 路径分隔符、盘符、NTFS 备用数据流（file.txt:ads）


def _safe_path_part(part):
    """单个路径组件是否安全（不含分隔符/盘符/冒号/.. 等）。"""
    if not isinstance(part, str) or not part or part in ('.', '..'):
        return False
    if any(c in part for c in _REJECT_PATH_CHARS):
        return False
    return not any(ord(c) < 32 for c in part)     # 控制字符


def safe_join(base, *parts):
    """把 `parts` 安全地拼到 `base` 下；任何可疑输入返回 None。

    返回前做两道：① 字符串级拒绝（UNC / 盘符 / 分隔符 / `..` / 冒号）；
    ② realpath 之后用 commonpath 确认真的落在 base 里（挡符号链接与剩余的花样）。
    """
    if not base:
        return None
    for part in parts:
        if not _safe_path_part(part):
            return None
    try:
        base_real = os.path.realpath(base)
        joined = os.path.realpath(os.path.join(base_real, *parts))
        if os.path.commonpath([base_real, joined]) != base_real:
            return None
    except (ValueError, OSError, TypeError):
        return None
    return joined


def _is_under(path, base):
    """`path` 是否在 `base` 内（都用 realpath 比较）。用于"先 realpath 再判定"的场合。"""
    try:
        real = os.path.realpath(os.path.normpath(path))
        base_real = os.path.realpath(base)
        return real == base_real or real.startswith(base_real + os.sep)
    except (ValueError, OSError, TypeError):
        return False


# ====== 线程安全 ======
# pywebview 的每个 JS API 调用运行在独立线程，全局 conn 与 _unlocked_deks 需要串行化保护。
# 统一给 Api 的公开方法加 RLock（可重入：方法间存在互调，如 notes_update→notes_get）。
def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _db_lock:
            return fn(*args, **kwargs)
    return wrapper

for _name, _attr in list(vars(Api).items()):
    if not _name.startswith('_') and callable(_attr):
        setattr(Api, _name, _locked(_attr))


api = Api()
