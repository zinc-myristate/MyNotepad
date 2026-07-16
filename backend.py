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
from datetime import datetime

# 数据目录：优先存 exe 旁边（便携模式，拷到 U 盘/其他电脑数据一起走）
_EXE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_EXE_DIR, "data")

# 文件大小限制
MAX_IMAGE_SIZE = 50 * 1024 * 1024    # 图片最大 50MB
MAX_ATTACH_SIZE = 200 * 1024 * 1024  # 附件最大 200MB
MAX_ICON_SIZE = 10 * 1024 * 1024     # 图标图片最大 10MB

# 如果 AppData 里有旧数据，迁移过来
_OLD_APP_DATA = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'MyNotepad')
if os.path.exists(_OLD_APP_DATA) and _OLD_APP_DATA != DATA_DIR:
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
_cleanup_all_scheduled_tasks()

# ====== 导出给前端的 API 类 ======
class Api:
    # ----- 笔记 -----
    def notes_list(self):
        rows = conn.execute(
            "SELECT id, title, bg_type, bg_value, bg_opacity, is_pinned, is_favorite, "
            "sort_order, created_at, updated_at, "
            "CASE WHEN password_hash IS NOT NULL AND password_hash != '' THEN 1 ELSE 0 END AS has_password "
            "FROM notes ORDER BY is_pinned DESC, updated_at DESC"
        ).fetchall()
        result = [dict(r) for r in rows]
        # 调试日志
        try:
            with open(os.path.join(DATA_DIR, 'debug.log'), 'a', encoding='utf-8') as _f:
                _f.write(f"LOAD count={len(result)} db={DB_PATH}\n")
        except: pass
        return result

    def notes_get(self, note_id, unlocked=False):
        """获取笔记详情。unlocked=False 时，加密笔记不返回内容。"""
        r = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        if not r:
            return None
        note = dict(r)
        has_pwd = bool(note.get('password_hash'))
        # 永远不向前端返回密码哈希
        note.pop('password_hash', None)
        # 如果笔记有密码且未解锁，隐藏内容
        if has_pwd and not unlocked:
            note['content'] = ''
            note['is_encrypted'] = True
            note['title'] = note.get('title', '未命名笔记')  # 标题可以显示
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
        conn.commit()
        return self.notes_get(nid)

    def notes_update(self, note_id, fields):
        allowed = {'title', 'content', 'bg_type', 'bg_value', 'bg_opacity', 'bg_zoom', 'bg_pos_x', 'bg_pos_y', 'sort_order', 'is_pinned', 'is_favorite', 'paper_style', 'paper_color', 'cover_type', 'cover_value', 'notebook_id'}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return None
        updates["updated_at"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [note_id]
        conn.execute(f"UPDATE notes SET {sets} WHERE id = ?", vals)
        conn.commit()
        result = self.notes_get(note_id)
        # 调试日志写入文件
        try:
            with open(os.path.join(DATA_DIR, 'debug.log'), 'a', encoding='utf-8') as _f:
                _f.write(f"SAVE note={note_id[:8]} title={updates.get('title','')[:20]} content_len={len(updates.get('content',''))} db={DB_PATH} ok={result is not None}\n")
        except: pass
        return result

    def notes_delete(self, note_id):
        # 删除附件文件夹
        note_attach = os.path.join(ATTACH_DIR, note_id)
        if os.path.exists(note_attach):
            shutil.rmtree(note_attach, ignore_errors=True)
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()
        return True

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
            "WHERE nt.tag_id = ? ORDER BY n.is_pinned DESC, n.updated_at DESC",
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
        """获取版本详情。如果父笔记已加密且未解锁，不返回内容。"""
        r = conn.execute("SELECT * FROM versions WHERE id = ?", (vid,)).fetchone()
        if not r:
            return None
        ver = dict(r)
        # 检查父笔记是否加密
        parent = conn.execute("SELECT password_hash FROM notes WHERE id = ?", (ver['note_id'],)).fetchone()
        if parent and parent['password_hash'] and not unlocked:
            ver['content'] = ''
            ver['is_encrypted'] = True
        else:
            ver['is_encrypted'] = False
        return ver

    def versions_restore(self, vid, unlocked=False):
        """恢复到指定版本。如果父笔记加密且未解锁，拒绝操作。"""
        ver = self.versions_get(vid, unlocked)
        if not ver:
            return None
        if ver.get('is_encrypted'):
            return None  # 笔记已加密且未解锁，拒绝恢复
        conn.execute(
            "UPDATE notes SET title = ?, content = ?, updated_at = datetime('now','localtime') WHERE id = ?",
            (ver['title'], ver['content'], ver['note_id'])
        )
        conn.commit()
        return self.notes_get(ver['note_id'], unlocked)

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
        # 检查是否和最新版本相同
        last = conn.execute(
            "SELECT content FROM versions WHERE note_id = ? ORDER BY created_at DESC LIMIT 1",
            (note_id,)
        ).fetchone()
        if last and last['content'] == content:
            return None  # 内容没变，不创建版本
        vid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO versions (id, note_id, title, content) VALUES (?, ?, ?, ?)",
            (vid, note_id, title, content)
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

    # ----- 密码 (PBKDF2 + SHA-256 + 随机盐) -----
    def _hash_password(self, password):
        import hashlib, os
        salt = os.urandom(32)
        key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
        return salt.hex() + ':' + key.hex()

    def _verify_hash(self, password, stored):
        try:
            salt_hex, key_hex = stored.split(':')
            salt = bytes.fromhex(salt_hex)
            key = bytes.fromhex(key_hex)
            import hashlib
            # 先尝试当前迭代次数（600000），失败则回退旧值（200000）保持兼容
            for iterations in (600000, 200000):
                new_key = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
                if new_key == key:
                    return True
            return False
        except:
            return False

    def note_set_password(self, note_id, password):
        if not password or len(password) < 6:
            return False
        h = self._hash_password(password)
        conn.execute("UPDATE notes SET password_hash = ? WHERE id = ?", (h, note_id))
        conn.commit()
        return True

    def note_verify_password(self, note_id, password):
        stored = conn.execute(
            "SELECT password_hash FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        if not stored or not stored['password_hash']:
            return True  # 没有密码的笔记直接通过
        stored_hash = stored['password_hash']
        # 兼容旧 SHA-256 格式（无冒号）
        if ':' not in stored_hash:
            import hashlib
            if hashlib.sha256(password.encode()).hexdigest() == stored_hash:
                # 自动升级为新格式
                self.note_set_password(note_id, password)
                return True
            return False
        return self._verify_hash(password, stored_hash)

    def note_remove_password(self, note_id, password):
        if self.note_verify_password(note_id, password):
            conn.execute("UPDATE notes SET password_hash = NULL WHERE id = ?", (note_id,))
            conn.commit()
            return True
        return False

    def note_has_password(self, note_id):
        stored = conn.execute(
            "SELECT password_hash FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return bool(stored and stored['password_hash'])

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
        """检查到期的提醒（remind_at <= 当前时间，且未完成）"""
        rows = conn.execute(
            "SELECT * FROM reminders "
            "WHERE remind_at <= datetime('now','localtime') AND is_completed = 0"
        ).fetchall()
        return [dict(r) for r in rows]

    def reminder_complete(self, reminder_id):
        """标记提醒为已完成"""
        conn.execute("UPDATE reminders SET is_completed = 1 WHERE id = ?", (reminder_id,))
        conn.commit()
        return True

    def reminder_snooze(self, reminder_id, minutes):
        """推迟提醒 N 分钟"""
        from datetime import datetime, timedelta
        r = self.reminder_get(reminder_id)
        if not r:
            return None
        old_time = datetime.strptime(r['remind_at'], '%Y-%m-%d %H:%M:%S')
        new_time = old_time + timedelta(minutes=minutes)
        new_time_str = new_time.strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("UPDATE reminders SET remind_at = ? WHERE id = ?", (new_time_str, reminder_id))
        conn.commit()
        return self.reminder_get(reminder_id)

    def reminder_update_next_repeat(self, reminder_id):
        """重复提醒触发后：自动计算下一次提醒时间"""
        from datetime import datetime, timedelta
        r = self.reminder_get(reminder_id)
        if not r or r['repeat_type'] == 'none':
            self.reminder_complete(reminder_id)
            return None

        old_time = datetime.strptime(r['remind_at'], '%Y-%m-%d %H:%M:%S')
        interval = r.get('repeat_interval', 1)
        repeat_type = r['repeat_type']

        if repeat_type == 'daily':
            new_time = old_time + timedelta(days=interval)
        elif repeat_type == 'weekly':
            new_time = old_time + timedelta(weeks=interval)
        elif repeat_type == 'weekday':
            # 跳到下一个工作日（周一到周五）
            new_time = old_time + timedelta(days=1)
            while new_time.weekday() >= 5:  # 5=周六, 6=周日
                new_time += timedelta(days=1)
        elif repeat_type == 'monthly':
            # 加 N 个月
            month = old_time.month - 1 + interval
            year = old_time.year + month // 12
            month = month % 12 + 1
            day = min(old_time.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                                     31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
            new_time = old_time.replace(year=year, month=month, day=day)
        elif repeat_type == 'yearly':
            new_time = old_time.replace(year=old_time.year + interval)
        else:
            self.reminder_complete(reminder_id)
            return None

        new_time_str = new_time.strftime('%Y-%m-%d %H:%M:%S')
        conn.execute("UPDATE reminders SET remind_at = ? WHERE id = ?", (new_time_str, reminder_id))
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
                        local_path = src.replace('file:///', '')
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


api = Api()
