"""
我的记事本 - Python 后端
处理数据库、文件操作，暴露 API 给前端
"""
import sqlite3
import os
import sys
import shutil
import uuid
import base64
import tempfile
import hashlib
import hmac
import json
import threading
import functools
from datetime import datetime
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
        except: pass

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
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN notebook_id TEXT")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN reminder_time TEXT")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN reminder_task_name TEXT")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN password_hash TEXT")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN bg_zoom INTEGER DEFAULT 100")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN bg_pos_x REAL DEFAULT 50")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN bg_pos_y REAL DEFAULT 50")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN paper_style TEXT DEFAULT 'none'")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN paper_color TEXT DEFAULT 'white'")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN cover_type TEXT DEFAULT 'none'")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN cover_value TEXT DEFAULT ''")
except: pass
try: conn.execute("ALTER TABLE notes ADD COLUMN enc_dek TEXT")  # 内容加密：被密码包裹的 DEK，NULL=未启用
except: pass
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
# 笔记本表扩展字段（必须在 CREATE TABLE 之后）
try: conn.execute("ALTER TABLE notebooks ADD COLUMN color TEXT DEFAULT '#7D8A6E'")
except: pass
try: conn.execute("ALTER TABLE notebooks ADD COLUMN cover_type TEXT DEFAULT 'color'")
except: pass
try: conn.execute("ALTER TABLE notebooks ADD COLUMN default_paper TEXT DEFAULT 'none'")
except: pass
conn.commit()

# 默认设置
for k, v in [('theme', 'white'), ('bg_type', 'color'), ('bg_value', ''), ('bg_opacity', '1.0')]:
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
            except:
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
            except: pass

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
    except:
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
_unlocked_deks = {}            # note_id -> DEK bytes（会话级，随进程消亡）

PBKDF2_ITERATIONS = 600000     # OWASP 2023
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
        kek = _derive_kek(password, bytes.fromhex(salt_hex), int(iters))
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

def backup_database():
    """启动时调用（app.pyw 后台线程）：距最新备份 >24h 才备份，保留最近 7 份。

    用 sqlite3 backup API 而非文件复制：DELETE journal 模式写入瞬间有 -journal
    残留，直接 copy 可能撕裂；backup API 页级一致且遇写入自动重启。
    返回备份文件路径；未到期或失败返回 None。
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
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            src.close()
            dst.close()
        # 滚动保留最近 7 份（文件名含时间戳，字典序即时间序）
        all_backups = sorted(
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith('notes-') and f.endswith('.db')
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

def _fts_sync(note_id, title, plain_content):
    """重写单条 FTS 行。plain_content=None（加密笔记）时 body 恒空——索引绝不落加密明文。"""
    if not FTS_AVAILABLE:
        return
    try:
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
        body = _delta_to_text(plain_content) if plain_content is not None else ''
        conn.execute("INSERT INTO notes_fts (note_id, title, body) VALUES (?, ?, ?)",
                     (note_id, title or '', body))
    except Exception:
        applog.get_logger().exception("FTS 同步失败")

def _fts_sync_from_row(note_id):
    """从 notes 表当前行重建 FTS 行（加密笔记 body 空，明文笔记提取正文）"""
    row = conn.execute(
        "SELECT title, content, password_hash FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    if not row:
        return
    _fts_sync(note_id, row['title'], None if row['password_hash'] else row['content'])

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
            for _r in conn.execute("SELECT id, title, content, password_hash FROM notes").fetchall():
                _body = '' if _r['password_hash'] else _delta_to_text(_r['content'])
                conn.execute("INSERT INTO notes_fts (note_id, title, body) VALUES (?, ?, ?)",
                             (_r['id'], _r['title'] or '', _body))
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('fts_version', ?)",
                         (FTS_VERSION,))
        conn.commit()
    except Exception:
        applog.get_logger().exception("FTS 初始化失败，降级为 LIKE 搜索")
        FTS_AVAILABLE = False

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
    def notes_list(self):
        # 排序：置顶 → 手动排序（sort_order，拖拽写入）→ 最近更新。新建笔记 sort_order=max+1，
        # 在 DESC 下自然排最前，与旧的「仅 updated_at」行为一致；未拖拽过的存量笔记 sort_order
        # 全为 0，退化为 updated_at DESC（兼容旧行为）。
        rows = conn.execute(
            "SELECT id, title, bg_type, bg_value, bg_opacity, is_pinned, is_favorite, "
            "sort_order, created_at, updated_at, "
            "CASE WHEN password_hash IS NOT NULL AND password_hash != '' THEN 1 ELSE 0 END AS has_password "
            "FROM notes ORDER BY is_pinned DESC, sort_order DESC, updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def notes_get(self, note_id, unlocked=False):
        """获取笔记详情。加密笔记仅当后端已解锁（DEK 在缓存）时返回明文内容。
        unlocked 参数保留兼容旧前端调用，但不再作为安全依据。"""
        r = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
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
        return note

    def notes_create(self):
        nid = str(uuid.uuid4())
        max_order = conn.execute("SELECT MAX(sort_order) as m FROM notes").fetchone()['m'] or -1
        conn.execute(
            "INSERT INTO notes (id, title, content, sort_order) VALUES (?, '未命名笔记', '', ?)",
            (nid, max_order + 1)
        )
        _fts_sync(nid, '未命名笔记', '')
        conn.commit()
        return self.notes_get(nid)

    def notes_update(self, note_id, fields):
        allowed = {'title', 'content', 'bg_type', 'bg_value', 'bg_opacity', 'bg_zoom', 'bg_pos_x', 'bg_pos_y', 'sort_order', 'is_pinned', 'is_favorite', 'paper_style', 'paper_color', 'cover_type', 'cover_value', 'notebook_id'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return None
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
        conn.commit()
        return self.notes_get(note_id)

    def notes_delete(self, note_id):
        # 清理该笔记专属的背景图副本（data/backgrounds/ 内的 uuid 文件，每次选图独立复制，可安全删除）
        row = conn.execute("SELECT bg_value FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row and row['bg_value']:
            try:
                bg_dir = os.path.realpath(os.path.join(DATA_DIR, 'backgrounds'))
                real = os.path.realpath(row['bg_value'])
                if os.path.dirname(real) == bg_dir and os.path.isfile(real):
                    os.remove(real)
            except OSError:
                pass
        # 删除附件文件夹
        note_attach = os.path.join(ATTACH_DIR, note_id)
        if os.path.exists(note_attach):
            shutil.rmtree(note_attach, ignore_errors=True)
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        _fts_delete(note_id)
        conn.commit()
        _unlocked_deks.pop(note_id, None)
        return True

    def notes_search(self, query):
        """全文搜索：≥3 字符且 FTS5 可用走 trigram；否则 LIKE 回退（标题 + 明文笔记正文）。
        加密笔记任何情况下只搜标题。返回 {'ids': [...], 'title_hits': [...]}"""
        q = (query or '').strip()
        if not q:
            return {'ids': [], 'title_hits': []}
        if FTS_AVAILABLE and len(q) >= 3:
            try:
                phrase = '"' + q.replace('"', '""') + '"'
                title_ids = [r['note_id'] for r in conn.execute(
                    "SELECT note_id FROM notes_fts WHERE notes_fts MATCH ?",
                    ('title:' + phrase,)).fetchall()]
                all_ids = [r['note_id'] for r in conn.execute(
                    "SELECT note_id FROM notes_fts WHERE notes_fts MATCH ?",
                    (phrase,)).fetchall()]
                return {'ids': all_ids, 'title_hits': title_ids}
            except Exception:
                applog.get_logger().exception("FTS 查询失败，回退 LIKE")
        # LIKE 回退：<3 字符（中文双字常态）/ FTS 不可用 / FTS 查询异常
        esc = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like = f'%{esc}%'
        rows = conn.execute(
            "SELECT id, (title LIKE ? ESCAPE '\\') AS th FROM notes "
            "WHERE title LIKE ? ESCAPE '\\' "
            "   OR (COALESCE(password_hash, '') = '' AND content LIKE ? ESCAPE '\\')",
            (like, like, like)).fetchall()
        return {'ids': [r['id'] for r in rows],
                'title_hits': [r['id'] for r in rows if r['th']]}

    # ----- 附件 -----
    def attachments_list(self, note_id):
        rows = conn.execute(
            "SELECT * FROM attachments WHERE note_id = ? ORDER BY created_at",
            (note_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def attachments_get_path(self, note_id, filename):
        return os.path.join(ATTACH_DIR, note_id, filename)

    # ----- 文件操作 -----
    def file_copy_to_note(self, source_path, note_id, file_type):
        # 文件大小检查
        try:
            fsize = os.path.getsize(source_path)
            max_size = MAX_IMAGE_SIZE if file_type == 'image' else MAX_ATTACH_SIZE
            if fsize > max_size:
                max_mb = max_size // (1024*1024)
                return {"error": f"文件超过 {max_mb}MB 限制"}
        except OSError:
            return {"error": "无法读取文件"}
        note_dir = os.path.join(ATTACH_DIR, note_id)
        os.makedirs(note_dir, exist_ok=True)

        ext = os.path.splitext(source_path)[1]
        new_name = f"{'img' if file_type == 'image' else 'file'}_{uuid.uuid4().hex}{ext}"
        dest = os.path.join(note_dir, new_name)
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
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
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

    def notes_by_tag(self, tag_id):
        rows = conn.execute(
            "SELECT n.id, n.title, n.bg_type, n.bg_value, n.bg_opacity, n.is_pinned, n.is_favorite, "
            "n.sort_order, n.created_at, n.updated_at, "
            "CASE WHEN n.password_hash IS NOT NULL AND n.password_hash != '' THEN 1 ELSE 0 END AS has_password "
            "FROM notes n JOIN note_tags nt ON n.id = nt.note_id "
            "WHERE nt.tag_id = ? ORDER BY n.is_pinned DESC, n.sort_order DESC, n.updated_at DESC",
            (tag_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ----- 笔记本 -----
    def notebooks_list(self):
        rows = conn.execute("SELECT * FROM notebooks ORDER BY sort_order, created_at").fetchall()
        return [dict(r) for r in rows]

    def notebooks_create(self, name='新建笔记本'):
        nid = str(uuid.uuid4())
        max_order = conn.execute("SELECT MAX(sort_order) as m FROM notebooks").fetchone()['m'] or -1
        conn.execute("INSERT INTO notebooks (id, name, sort_order) VALUES (?, ?, ?)", (nid, name, max_order + 1))
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
            "UPDATE notes SET title = ?, content = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (ver['title'], ver['content'], ver['note_id'])
        )
        _fts_sync_from_row(ver['note_id'])
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
        parent = conn.execute("SELECT password_hash FROM notes WHERE id = ?", (note_id,)).fetchone()
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
            "INSERT INTO versions (id, note_id, title, content) VALUES (?, ?, ?, ?)",
            (vid, note_id, title, store_content)
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
                salt, iters, key = bytes.fromhex(parts[1]), int(parts[2]), bytes.fromhex(parts[3])
                return hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iters), key)
            if len(parts) == 2:
                # 旧 salt:key 格式（未存迭代数），依次尝试 600000 / 200000 保持兼容
                salt, key = bytes.fromhex(parts[0]), bytes.fromhex(parts[1])
                for iterations in (600000, 200000):
                    if hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations), key):
                        return True
            return False
        except:
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
        conn.commit()
        return True

    def note_set_password(self, note_id, password):
        """设置密码并加密笔记内容（正文 + 全部历史版本）"""
        if not password or len(password) < 6:
            return False
        row = conn.execute("SELECT id, enc_dek FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not row:
            return False
        # 防御：已有加密内容但未解锁时，禁止直接覆盖密码（否则新 DEK 与旧密文错配，内容永久丢失）
        if row['enc_dek'] and note_id not in _unlocked_deks:
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
        # 验证（兼容旧裸 SHA-256 无冒号格式）
        if ':' not in stored_hash:
            if hashlib.sha256(password.encode()).hexdigest() != stored_hash:
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
        conn.commit()
        _unlocked_deks.pop(note_id, None)
        return True

    def note_lock(self, note_id):
        """锁定笔记：清除后端解锁缓存"""
        _unlocked_deks.pop(note_id, None)
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
                "SELECT * FROM reminders WHERE note_id = ? AND is_completed = 0 "
                "ORDER BY remind_at ASC",
                (note_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE is_completed = 0 "
                "ORDER BY remind_at ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def reminder_list_all(self):
        """列出所有提醒（包括已完成的，用于管理面板）"""
        rows = conn.execute(
            "SELECT r.*, n.title as note_title FROM reminders r "
            "LEFT JOIN notes n ON r.note_id = n.id "
            "ORDER BY r.is_completed ASC, r.remind_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def reminder_check(self):
        """检查到期的提醒：只返回 24h 内的（防启动时补弹一堆过期提醒）。
        过期 >24h 的重复提醒自动推进到未来首次触发；一次性提醒标记完成（管理面板仍可见）。"""
        due = conn.execute(
            "SELECT * FROM reminders "
            "WHERE remind_at <= datetime('now','localtime') AND is_completed = 0 "
            "AND remind_at > datetime('now','localtime', '-1 day')"
        ).fetchall()
        stale = conn.execute(
            "SELECT * FROM reminders "
            "WHERE remind_at <= datetime('now','localtime', '-1 day') AND is_completed = 0"
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
            from docx import Document
            from docx.shared import Pt, Inches, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            import re
            import os
            import base64

            doc = Document()

            # 标题
            title_para = doc.add_heading(title, level=0)
            title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

            # 解析 HTML 并转换为 docx 段落
            # 按块级元素分割
            blocks = re.split(r'(</?(?:p|h[1-6]|div|br|img)[^>]*>)', html_content)
            current_text = ''
            current_tag = 'p'

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
                            doc.add_paragraph(f'[图片: {src[:50]}...]')
                    elif src.startswith('file:///'):
                        # Windows 下 file:///C:/... 需先 unquote 再剥掉根斜杠，否则空格路径失效
                        import urllib.parse
                        local_path = urllib.parse.unquote(src.replace('file:///', '')).lstrip('/')
                        if os.path.exists(local_path):
                            try:
                                doc.add_picture(local_path, width=Inches(5))
                            except:
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
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.cell.rich_text import TextBlock, CellRichText
            from openpyxl.cell.text import InlineFont
            import re

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
