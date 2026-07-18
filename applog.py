# -*- coding: utf-8 -*-
"""崩溃兜底日志：轮转 error.log + 进程级异常钩子。

只记录异常消息与调用栈，绝不记录笔记标题/内容等用户数据。
"""
import logging
import logging.handlers
import sys
import threading

_initialized = False
_logger = logging.getLogger('mynotepad')


def get_logger():
    return _logger


def init(data_dir):
    """初始化：data_dir/error.log，1MB × 2 轮转；安装全局异常钩子。

    handler 每次调用重建（重复 init 指向新目录时旧 handler 会被替换）；
    异常钩子只安装一次。
    """
    global _initialized
    import os
    logging.raiseExceptions = False  # 日志自身写盘失败（磁盘满/只读）不抛错
    try:
        for h in list(_logger.handlers):
            _logger.removeHandler(h)
            try: h.close()
            except Exception: pass
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(data_dir, 'error.log'),
            maxBytes=1_000_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(message)s'))
        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
    except Exception:
        return  # 日志目录不可写时静默放弃，绝不影响主程序

    if _initialized:
        return  # 钩子只装一次
    _initialized = True

    _orig_excepthook = sys.excepthook

    def _excepthook(exc_type, exc, tb):
        try:
            _logger.error('未捕获异常', exc_info=(exc_type, exc, tb))
        except Exception:
            pass
        _orig_excepthook(exc_type, exc, tb)

    def _thread_excepthook(args):
        try:
            _logger.error('线程 %s 未捕获异常', args.thread.name if args.thread else '?',
                          exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        except Exception:
            pass

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
