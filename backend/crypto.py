# -*- coding: utf-8 -*-
"""信封加密（envelope encryption）与解锁缓存。

方案：每篇笔记一个随机 DEK（AES-256-GCM）；DEK 被「密码派生的 KEK」包裹后存库
（`notes.enc_dek`）。**改密码只需重新包裹 32 字节 DEK，内容零重加密。**

解锁状态在**后端进程内存**（`unlocked_deks`），前端传来的 `unlocked` 标志不再作为安全依据。

⚠️ 这个模块**只管密码学与解锁状态，不碰数据库** —— 所以它不需要 conn 参数，
也不依赖 backend 包的任何其它子模块（只有 stdlib + cryptography + applog）。
拆包时它是依赖图上的叶子，这也是它被第二个搬出来的原因。
"""
import base64
import hashlib
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import applog


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



unlocked_deks = _UnlockedDeks()   # note_id -> DEK bytes


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

