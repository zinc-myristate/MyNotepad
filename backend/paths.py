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
"""
import os

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
