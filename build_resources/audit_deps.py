# -*- coding: utf-8 -*-
"""依赖漏洞扫描：把本项目分发的依赖版本交给 OSV（https://osv.dev）查已知漏洞。

**为什么需要**：`requirements.txt` 全部锁死是为了打包可复现，代价是版本不会自己往前走；
自带的前端库更是没有任何工具盯着。2026-11-01 第一次扫描时，Pillow 落后到 59 条公告、
DOMPurify 落后到 23 条，而 CI 一直是绿的。

查两类依赖：
  · Python：`requirements.txt` 的传递闭包（与 collect_licenses.py 同一份清单），版本取当前环境；
  · 自带前端库：版本从 `renderer/` 里的文件读，不另存一份，免得和实际文件分叉。

用法：`python build_resources/audit_deps.py`
退出码：0 没有未接受的公告；1 有；2 查询失败（查不到不能当成"没有漏洞"）。
输出只用 ASCII：与 tcl_tk_data.py 同一条理由，runner 的 stdout 可能是 cp1252。
只发送包名和版本号。
"""
from __future__ import annotations

import importlib.metadata as md
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_licenses import _closure, project_requirements  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OSV_BATCH = 'https://api.osv.dev/v1/querybatch'
# 自带前端库：npm 包名 -> (文件, 取版本号的正则)
VENDORED = {
    'quill': ('renderer/quill.snow.css', r'Quill Editor v(\d[\d.]*)'),
    'katex': ('renderer/katex/katex.min.js', r'version:"(\d[\d.]*)"'),
    'markdown-it': ('renderer/vendor/markdown-it.min.js', r'markdown-it (\d[\d.]*)'),
    'dompurify': ('renderer/vendor/purify.min.js', r'DOMPurify (\d[\d.]*)'),
    'highlight.js': ('renderer/vendor/highlight.min.js', r'Highlight\.js v(\d[\d.]*)'),
    'codemirror': ('renderer/vendor/codemirror.js', r'CodeMirror\.version = "(\d[\d.]*)"'),
}
# 已评估、确认不影响本应用的公告。新增时必须写清为什么不影响，并尽量配一条测试钉住理由。
ACCEPTED = {
    'GHSA-v3m3-f69x-jf25': 'quill: only affects the HTML export API getSemanticHTML, unused here; no fixed release',
}


def vendored_packages(root=ROOT):
    """读 renderer/ 里的文件得到前端库版本；读不到版本是错误，不能静默跳过"""
    out = []
    for name, (rel, pattern) in VENDORED.items():
        with open(os.path.join(root, *rel.split('/')), encoding='utf-8') as fh:
            match = re.search(pattern, fh.read())
        if not match:
            raise ValueError('cannot read version of %s from %s' % (name, rel))
        out.append(('npm', name, match.group(1).rstrip('.')))
    return out


def python_packages(req_path=None):
    """requirements.txt 的传递闭包 + 当前环境里的实际版本"""
    req_path = req_path or os.path.join(ROOT, 'requirements.txt')
    out = []
    for key in sorted(_closure(project_requirements(req_path))):
        dist = md.distribution(key)
        out.append(('PyPI', dist.metadata['Name'], dist.version))
    return out


def query_osv(packages, opener=urllib.request.urlopen):
    """返回 `{(生态, 包名, 版本): [公告编号, ...]}`，只含有公告的包"""
    queries = [{'package': {'name': name, 'ecosystem': eco}, 'version': ver}
               for eco, name, ver in packages]
    body = json.dumps({'queries': queries}).encode('utf-8')
    request = urllib.request.Request(OSV_BATCH, data=body,
                                     headers={'Content-Type': 'application/json'})
    with opener(request, timeout=60) as resp:
        results = json.load(resp)['results']
    return {pkg: [v['id'] for v in res.get('vulns', [])]
            for pkg, res in zip(packages, results, strict=True) if res.get('vulns')}


def unaccepted(found):
    """去掉已接受的公告，剩下的才算失败"""
    left = {}
    for pkg, ids in found.items():
        rest = [vid for vid in ids if vid not in ACCEPTED]
        if rest:
            left[pkg] = rest
    return left


def main(opener=urllib.request.urlopen):
    try:
        packages = python_packages() + vendored_packages()
        found = query_osv(packages, opener)
    except (OSError, ValueError, KeyError) as exc:        # 含 URLError；ValueError 含 JSON 解析失败
        print('::error title=Dependency audit::query failed: %r' % (exc,))
        return 2
    for (_eco, name, ver), ids in sorted(found.items()):
        for vid in ids:
            note = ACCEPTED.get(vid)
            if note:
                print('accepted %s %s %s (%s)' % (name, ver, vid, note))
    left = unaccepted(found)
    for (eco, name, ver), ids in sorted(left.items()):
        print('::error title=Vulnerable dependency::%s %s %s: %s' % (eco, name, ver, ', '.join(ids)))
    print('checked %d packages, %d with unaccepted advisories' % (len(packages), len(left)))
    return 1 if left else 0


if __name__ == '__main__':
    raise SystemExit(main())
