# -*- coding: utf-8 -*-
r"""数据库备份 / 完整性校验 / 空间回收。

四件事，都是"围着 `notes.db` 这个文件本身"的操作（不改业务数据）：

* `backup_database()` —— 用 SQLite 的 **backup API**（不是文件拷贝）写一份快照，
  距上次不足 24h 就跳过，滚动保留若干份。
  ⚠️ **必须用 backup API**：WAL 模式下裸拷 `notes.db` 会丢掉 `-wal` 里**已提交**的事务。
* `check_integrity()` / `count_notes()` —— 恢复流程的前置校验（`restore.py` 也在用）。
* `space_stats()` / `reclaim_space()` —— 空闲页占比过高时 VACUUM 回收空间。

## 为什么路径是模块级默认 + 显式参数

它们必须知道"哪一个库"（`DB_PATH`）与"备份放哪"（`BACKUP_DIR`）。本模块按 `DATA_DIR`
算出一份默认值，同时每个函数都接显式参数：

* 生产路径不传参，用默认值；
* **测试可以指向任意临时库**，不必改模块全局 —— 改全局在拆包后对子模块不生效，
  这是"patch 打错模块"那类坑的近亲（上一轮在 crypto 上踩过）。

⚠️ 这两个默认值与 `backend/__init__.py` 的 `DATA_DIR` 是**同一个来源**（都读环境变量），
不是第二个真相源。真要说谁是唯一来源，是 `__init__.py` 的 `DATA_DIR`；这里是它的投影。
"""
import contextlib
import os
import sqlite3

# ⚠️ 必须是 `from datetime import datetime`（**类**），不是 `import datetime`（模块）：
# 函数体里写的是 `datetime.now()`。原文件（backend.py）就是 `from datetime import datetime`，
# 按"名字出现过"生成 `import datetime` 会让 `datetime.now()` 报"模块没有 now 属性"
# —— 同一个坑在 text.py 上踩过一次（当时 5 条模板测试红），这里是第二次。
from datetime import datetime

import applog

# 与 __init__.py 的 DATA_DIR 同一口径：环境变量优先，否则仓库根下的 data/
DATA_DIR = os.environ.get('MYNOTEPAD_DATA_DIR') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')

DB_PATH = os.path.join(DATA_DIR, 'notes.db')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

# `reclaim_space(lock=None)` 时用的空上下文（不传锁 = 自己保证没有并发调用）
_NULL_LOCK = contextlib.nullcontext()




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
            # 【坑】WAL 模式下给一个还不存在的文件建连接，SQLite 会顺手生成
            # `<tmp>-wal` / `<tmp>-shm`（这里是 `notes-<时间戳>.db.part-wal` / `.part-shm`）。
            # `os.replace(tmp, dest)` 只搬走主文件，那两个伴生文件会**留在备份目录里**
            # （实测：备份完目录里多出 0 字节的 .part-wal 和 32KB 的 .part-shm）。
            # 所以下面统一用 _sidecar() 清掉它们 —— 改名后、失败分支都要清。
            def _sidecar(p):
                for suffix in ('-wal', '-shm'):
                    try:
                        if os.path.exists(p + suffix):
                            os.remove(p + suffix)
                    except OSError:
                        pass
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
            _sidecar(tmp)            # 清掉 .part-wal / .part-shm
            _sidecar(dest)           # 万一 SQLite 在主文件名下也留了
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            _sidecar(tmp)
            raise
        # 滚动保留最近 7 份（文件名含时间戳，字典序即时间序）
        # 【坑】必须排掉 WAL 的伴生文件：备份写的是 `notes-<时间戳>.db.part`，SQLite 会给它
        # 生成 `.part-wal` / `.part-shm`，而这两个名字**不以 `.part` 结尾**（结尾是 `-wal`），
        # 所以旧的 `not.endswith('.part')` 过滤放它们过去 —— 一份备份被数成三份，
        # "保留 7 份"实际留下 9 个文件（实测踩到，WAL 切完立刻红了一条测试）。
        _shard = ('.part', '.part-wal', '.part-shm', '.db-wal', '.db-shm')
        all_backups = sorted(
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith('notes-') and f.endswith('.db') and not f.endswith(_shard)
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

def check_integrity(db_path=None):
    """独立连接跑 PRAGMA integrity_check；损坏返回 False。启动自检用，绝不进模块级执行。

    ⚠️ 参数默认值必须是 `None`、在函数体里再解析成 `DB_PATH`，**不能写成 `db_path=DB_PATH`**：
    默认值在**函数定义时**就求值了，于是它被冻结成"第一次导入那一刻"的路径 ——
    测试换了 `MYNOTEPAD_DATA_DIR` 之后，`check_integrity()` 还在检查上一个库
    （实测：拆包后 `inspect.signature` 显示默认值是**上一个测试的 temp 路径**）。
    同文件的 `space_stats` / `reclaim_space` 一直是惰性解析的写法，这里统一过来。
    """
    path = db_path or DB_PATH
    try:
        c = sqlite3.connect(path)
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


def reclaim_space(threshold=0.30, db_path=None, lock=None):
    """空闲页占比超过 threshold 时 VACUUM 回收磁盘空间（启动维护线程调用）。

    调用时机在 backup_database() **之前**：先有一份页级一致的备份兜底，再压缩。
    VACUUM 需要重写整个库，必须独占——所以独立连接 + 调用方的写锁。

    `lock` 就是 `backend` 的那把全局写锁（`_db_lock`）。**为什么做成参数而不是 import**：
    `backend/__init__.py` 要 import 本模块，本模块再 import 它的 `_db_lock` 就是循环导入。
    不传 = 不加锁（自己保证没有并发调用），传了就按 with 语义持有。
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

    with (lock if lock is not None else _NULL_LOCK):
        try:
            c = sqlite3.connect(path)
            try:
                c.execute("PRAGMA foreign_keys=OFF")
                c.execute("VACUUM")          # 触发隐式提交；若被事务挡住则 repair 分支处理
                c.commit()
                # 【坑】WAL 模式下 VACUUM 重写出来的页**先落在 notes.db-wal 里**，
                # 主库文件此时不会变小 —— 于是"回收后文件小了"这件事根本不成立
                # （实测：12226560 -> 12226560，测试当场红）。VACUUM 完立刻 checkpoint
                # 把回写落到主库并让 WAL 归零，回收才算真的完成（也顺带缩小了磁盘占用）。
                c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
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
