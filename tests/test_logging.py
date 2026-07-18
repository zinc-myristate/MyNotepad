# -*- coding: utf-8 -*-
"""崩溃兜底日志：error.log 生成、log_error 桥接、不含用户内容"""
import os


def test_log_error_writes_file(api, backend_mod, tmp_path):
    api.log_error('测试错误消息', 'Error: x\n    at foo (app.js:1:1)', 'unittest')
    log_path = tmp_path / 'error.log'
    assert log_path.exists()
    text = log_path.read_text(encoding='utf-8')
    assert '[js:unittest] 测试错误消息' in text
    assert 'app.js:1:1' in text


def test_log_error_truncates(api, tmp_path):
    api.log_error('A' * 10000, 'B' * 20000, 'unittest')
    text = (tmp_path / 'error.log').read_text(encoding='utf-8')
    # message 截 2048、stack 截 8192
    assert 'A' * 2048 in text and 'A' * 2049 not in text
    assert 'B' * 8192 in text and 'B' * 8193 not in text


def test_log_never_contains_note_content(api, backend_mod, tmp_path):
    """保存链路的错误上报只传异常信息——日志中不应出现笔记正文"""
    nid = api.notes_create()['id']
    api.notes_update(nid, {'title': '机密标题XYZ', 'content': '{"ops":[{"insert":"机密正文ABC\\n"}]}'})
    api.log_error('保存笔记失败: some error', 'stack', 'saveCurrentNote')
    text = (tmp_path / 'error.log').read_text(encoding='utf-8')
    assert '机密正文ABC' not in text
    assert '机密标题XYZ' not in text


def test_uncaught_thread_exception_logged(backend_mod, tmp_path):
    import threading, time
    def boom():
        raise RuntimeError('线程崩溃测试')
    t = threading.Thread(target=boom)
    t.start()
    t.join()
    time.sleep(0.1)
    text = (tmp_path / 'error.log').read_text(encoding='utf-8')
    assert '线程崩溃测试' in text
