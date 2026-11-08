# -*- coding: utf-8 -*-
"""依赖漏洞扫描（build_resources/audit_deps.py）的离线测试。

不联网：OSV 的回复由假 opener 给出。"这批版本今天有没有漏洞"由 `audit.yml` 定时查，
这里只钉逻辑：查不到不能当成没有漏洞、已接受的公告要有理由、版本不能退回修复之前。
"""
import io
import json
import os
import re
import sys
import urllib.error

import pytest
from conftest import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, 'build_resources'))
import audit_deps  # noqa: E402


def _read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), encoding='utf-8') as fh:
        return fh.read()


def _version(text):
    return tuple(int(x) for x in text.split('.'))


def _opener(find_ids):
    """假 OSV：`find_ids(包名)` 决定这个包回哪些公告编号"""
    def opener(request, timeout=None):
        queries = json.loads(request.data)['queries']
        results = [{'vulns': [{'id': vid} for vid in find_ids(q['package']['name'])]}
                   if find_ids(q['package']['name']) else {} for q in queries]
        return io.BytesIO(json.dumps({'results': results}).encode('utf-8'))
    return opener


class TestPackageLists:
    def test_reads_every_vendored_library_from_its_file(self):
        found = {name: ver for _eco, name, ver in audit_deps.vendored_packages()}
        assert set(found) == set(audit_deps.VENDORED)
        for name, ver in found.items():
            assert re.fullmatch(r'\d+(\.\d+)+', ver), '%s 的版本读歪了：%r' % (name, ver)

    def test_unreadable_version_is_an_error_not_a_skip(self, tmp_path, monkeypatch):
        """静默跳过等于这个库从此没人查"""
        target = tmp_path / 'quill.css'
        target.write_text('no version here', encoding='utf-8')
        monkeypatch.setattr(audit_deps, 'VENDORED', {'quill': ('quill.css', r'v(\d+)')})
        with pytest.raises(ValueError):
            audit_deps.vendored_packages(str(tmp_path))

    def test_covers_transitive_python_packages(self):
        """lxml 是 python-docx 带进来的，不在 requirements.txt 里，同样随产物分发"""
        names = {name.lower() for _eco, name, _ver in audit_deps.python_packages()}
        assert {'pillow', 'cryptography', 'lxml'} <= names

    def test_notices_list_the_vendored_versions_we_ship(self):
        """清单是手写的、版本在文件里，两边分叉不会在别处露头"""
        notices = _read('THIRD_PARTY_NOTICES.md')
        for name, ver in (('Quill', 'quill'), ('KaTeX', 'katex'), ('markdown-it', 'markdown-it'),
                          ('DOMPurify', 'dompurify'), ('CodeMirror', 'codemirror'),
                          ('highlight.js', 'highlight.js')):
            actual = next(v for _e, n, v in audit_deps.vendored_packages() if n == ver)
            assert re.search(r'^\| %s \| %s \|' % (re.escape(name), re.escape(actual)), notices, re.M), \
                'THIRD_PARTY_NOTICES.md 里 %s 的版本不是 %s' % (name, actual)


class TestVersionsStayFixed:
    """升级修掉的公告不能因为回退版本又回来（修复版本来自 OSV，2026-11-01）。"""

    @pytest.mark.parametrize('name,floor', [
        ('Pillow', '13.0.0'), ('cryptography', '50.0.0'), ('setuptools', '84.2.1')])
    def test_python_pins(self, name, floor):
        pinned = re.search(r'^%s==(\S+)' % name, _read('requirements.txt'), re.M)
        assert pinned and _version(pinned.group(1)) >= _version(floor), name

    @pytest.mark.parametrize('name,floor', [
        ('dompurify', '3.4.17'), ('markdown-it', '14.3.3'), ('katex', '0.18.2')])
    def test_vendored_versions(self, name, floor):
        actual = next(v for _e, n, v in audit_deps.vendored_packages() if n == name)
        assert _version(actual) >= _version(floor), '%s %s' % (name, actual)


class TestVerdict:
    def test_query_keeps_only_packages_with_advisories(self):
        pkgs = [('PyPI', 'a', '1'), ('npm', 'b', '2')]
        found = audit_deps.query_osv(pkgs, _opener(lambda n: ['X-1'] if n == 'b' else []))
        assert found == {('npm', 'b', '2'): ['X-1']}

    def test_short_answer_is_an_error(self):
        """回复条数对不上时，zip 会把后面的包静默丢掉"""
        def opener(request, timeout=None):
            return io.BytesIO(json.dumps({'results': []}).encode('utf-8'))
        with pytest.raises(ValueError):
            audit_deps.query_osv([('PyPI', 'a', '1')], opener)

    def test_accepted_ids_are_filtered_per_id(self, monkeypatch):
        monkeypatch.setattr(audit_deps, 'ACCEPTED', {'OK-1': 'why'})
        pkg = ('npm', 'b', '2')
        assert audit_deps.unaccepted({pkg: ['OK-1']}) == {}
        assert audit_deps.unaccepted({pkg: ['OK-1', 'BAD-2']}) == {pkg: ['BAD-2']}

    def test_clean_run_exits_zero(self):
        assert audit_deps.main(_opener(lambda n: [])) == 0

    def test_new_advisory_exits_one(self, capsys):
        code = audit_deps.main(_opener(lambda n: ['GHSA-new'] if n == 'dompurify' else []))
        out = capsys.readouterr().out
        assert code == 1 and 'dompurify' in out and 'GHSA-new' in out
        assert out.isascii(), '输出要能穿过 cp1252 的 stdout'

    def test_accepted_advisory_alone_exits_zero_but_is_shown(self, capsys):
        vid = next(iter(audit_deps.ACCEPTED))
        assert audit_deps.main(_opener(lambda n: [vid] if n == 'quill' else [])) == 0
        assert vid in capsys.readouterr().out

    @pytest.mark.parametrize('failure', [urllib.error.URLError('offline'), TimeoutError('slow')])
    def test_query_failure_is_not_a_pass(self, failure):
        def opener(request, timeout=None):
            raise failure
        assert audit_deps.main(opener) == 2

    def test_garbled_answer_is_not_a_pass(self):
        def opener(request, timeout=None):
            return io.BytesIO(b'<html>rate limited</html>')
        assert audit_deps.main(opener) == 2


class TestAcceptedReasonsStillHold:
    def test_every_entry_explains_itself(self):
        for vid, why in audit_deps.ACCEPTED.items():
            assert len(why) > 20, '%s 要写清为什么不影响本应用' % vid

    def test_quill_html_export_stays_unused(self):
        """接受 GHSA-v3m3-f69x-jf25 的前提：不调用 Quill 的 HTML 导出。用上了就要重新评估。"""
        hits = []
        for base, _dirs, files in os.walk(os.path.join(PROJECT_ROOT, 'renderer', 'js')):
            hits += [os.path.join(base, f) for f in files if f.endswith('.js')
                     and 'getSemanticHTML' in _read(os.path.relpath(os.path.join(base, f), PROJECT_ROOT))]
        assert not hits, hits


def test_audit_workflow_runs_on_a_schedule():
    """新漏洞不改代码也会冒出来，只在提交时查不够"""
    wf = _read('.github', 'workflows', 'audit.yml')
    assert 'schedule:' in wf and 'cron:' in wf
    assert 'build_resources/audit_deps.py' in wf
    assert 'continue-on-error' not in wf, '发现漏洞要显示成失败'
