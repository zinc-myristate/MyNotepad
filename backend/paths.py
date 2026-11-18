# -*- coding: utf-8 -*-
r"""路径包含性校验（唯一入口）。

这些路径参数全部来自前端 / 正文，属于**信任边界之外**的值。项目以前散着 6 处各写各的
内联校验，写法不一致，于是漏了几处（附件的 note_id/filename、`file_copy_to_note` 的
note_id、OCR 的图片路径）—— 所以收成一个模块、一个入口。

⚠️ **顺序至关重要：先做字符串级拒绝，再 realpath。**
反过来的话，`realpath(r'\\attacker\share\x.png')` 会真的去解析这个 UNC 路径 ——
那是一次对外的 SMB 连接，会泄露 NetNTLM 响应；而 `os.path.join` 遇到 UNC / 绝对路径组件
还会**丢弃前面的基础目录**（`join('C:\\base', '\\\\a\\b')` == `'\\\\a\\b'`），
白名单比较就成了摆设。

本模块**不依赖 backend 包内的任何东西**（只有 stdlib），所以可以被任何子模块安全导入 ——
这是刻意的：路径安全是底层工具，不该有依赖方向上的纠缠。

另有 `safe_filename`：把笔记标题变成保存对话框里的默认文件名。它不做校验、不访问文件系统，
只保证名字本身在 Windows 上能用。
"""
import os
import re

_REJECT_PATH_CHARS = ('\\', '/', ':')      # 路径分隔符、盘符、NTFS 备用数据流（file.txt:ads）

# ── 保存对话框的默认文件名 ──────────────────────────────────────────────
# Windows 文件名里不许出现的九个字符，换成长得一样的全角字符：标题「2026/11/18 会议」导出时
# 默认叫「2026／11／18 会议.html」，读起来还是原来的标题（换成下划线也能用，只是像乱码）。
# 原样交给对话框的后果（实测）：有 `/` `\` 时提示"路径不存在"（当成了文件夹），
# 有 `?` `*` 时按「保存」没反应（当成了筛选条件），其余几个提示文件名无效。
_FULLWIDTH = str.maketrans({'<': '＜', '>': '＞', ':': '：', '"': '＂', '/': '／',
                            '\\': '＼', '|': '｜', '?': '？', '*': '＊'})
# 换行、制表符这类换成空格；其余控制字符去掉。另外去掉两类看不见的字符：
# 改变文字方向的控制符（U+202A–202E、U+2066–2069，能让「笔记\u202eftd.html」显示成「笔记lmth.dtf」），
# 落单的代理项（页面上的字符串可以有，Tk 把它交给 Windows 时会报错）
_FILENAME_SPACE = re.compile('[\t\n\x0b\x0c\r\u2028\u2029]')
_FILENAME_DROP = re.compile('[\x00-\x08\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069\ud800-\udfff]')
# DOS 设备名：Windows 10 上「CON.html」「nul .txt」都指设备，写进去的东西就没了
# （本机 Windows 11 26300 上已经是普通文件）。与 ntpath.isreserved 同一张表，另加微软文档列出的 COM0 / LPT0
_RESERVED_STEMS = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    | {prefix + digit for prefix in ('COM', 'LPT') for digit in '0123456789\xb9\xb2\xb3'})


def safe_filename(name, fallback='未命名笔记', limit=120):
    """笔记标题 → 保存对话框的默认文件名（不含扩展名，调用方自己接）。

    九个不许用的字符换成全角，控制字符去掉，首尾空白去掉，最长 `limit` 个字（再接上扩展名也不会
    超过 Windows 单个文件名 255 的上限），结尾的点和空格去掉（Windows 会悄悄去掉它们），
    设备名前面加 `_`。什么都不剩时用 `fallback`。
    """
    if not isinstance(name, str):
        name = ''
    name = _FILENAME_SPACE.sub(' ', name)
    name = _FILENAME_DROP.sub('', name)
    name = re.sub(r'[\s.]+$', '', name.translate(_FULLWIDTH).strip()[:limit])
    # 设备名要等截断之后再判：「CON」后面跟一长串空格，截完才成了设备名。加了 `_` 不能超过 limit，
    # 不然再清理一遍会少一个字
    if name.partition('.')[0].rstrip(' ').upper() in _RESERVED_STEMS:
        name = re.sub(r'[\s.]+$', '', '_' + name[:limit - 1])
    return name or fallback


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
    """`path` 是否在 `base` 内（都用 realpath 比较）。用于"先 realpath 再判定"的场合。

    ⚠️ 只能用在**自己拼出来的**路径上。来自前端 / 正文的完整路径要用 `resolve_under`：
    这里上来就 realpath，传进来的是 `\\\\主机\\共享\\…` 时，比较之前就已经连过去了。
    """
    try:
        real = os.path.realpath(os.path.normpath(path))
        base_real = os.path.realpath(base)
        return real == base_real or real.startswith(base_real + os.sep)
    except (ValueError, OSError, TypeError):
        return False


def _lexically_under(path, base):
    """只比字符串：`path` 是不是 `base` 本身或它下面的路径。两边都应该是绝对路径。"""
    path, base = os.path.normcase(path), os.path.normcase(base)
    return path == base or path.startswith(base.rstrip('\\/') + os.sep)


def resolve_under(path, *bases):
    """核对一个来自前端 / 正文的**完整路径**确实落在某个允许目录里，返回它的真实路径；不是就返回 None。

    `safe_join` 管的是"目录 + 几个文件名"，这里管的是别人直接给的一整条路径（富文本图片和附件卡片
    把绝对路径存在正文里，前端原样传回来）。三道，顺序不能换：

    ① 字符串级拒绝：不是字符串、空串、带控制字符。
    ② 词法包含：`abspath` 之后必须落在某个允许目录里。`abspath` 走的是 GetFullPathName，只做
       字符串运算，不访问磁盘也不访问网络，所以 `\\\\主机\\共享\\x.png` 这样的路径到这里就被拒了，
       后面的 realpath 根本碰不到它。允许目录按 `abspath` 和 `realpath` 两种写法都认：后端交给前端的
       路径有的是拼出来的（临时目录可能是 8.3 短名），有的过了 realpath。
    ③ realpath 之后再确认一次：挡允许目录里的符号链接、目录联接指到外面。

    盘符之后还有冒号的也拒绝（NTFS 备用数据流 `file.txt:ads`）。
    """
    if not isinstance(path, str) or not path or any(ord(c) < 32 for c in path):
        return None
    roots = []
    try:
        full = os.path.abspath(path)
        for base in bases:
            if base:
                roots.append((os.path.abspath(base), os.path.realpath(base)))
    except (ValueError, OSError, TypeError):
        return None
    if ':' in os.path.splitdrive(full)[1]:
        return None
    if not any(_lexically_under(full, form) for pair in roots for form in pair):
        return None
    try:
        real = os.path.realpath(full)
    except (ValueError, OSError, TypeError):
        return None
    if not any(_lexically_under(real, base_real) for _base_abs, base_real in roots):
        return None
    return real
