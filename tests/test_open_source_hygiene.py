# -*- coding: utf-8 -*-
"""开源仓库的"合规与门面"守卫（纯静态，不需要浏览器）。

这些检查点都是**公开仓库**特有的坑，而且一旦回归就要么违规、要么让新用户第一次就卡住：

- 缺 `LICENSE` → GitHub 显示 "No license"，法律上等于"保留所有权利"，别人 fork 都不算合法；
- 缺 `renderer/quill.js.LICENSE.txt` → Quill 的 BSD-3 要求保留版权声明，而 `quill.js` 头部就写着
  "For license information please see quill.js.LICENSE.txt"（第一版真的忘了放这个文件）；
- 仓库里混进 `.claude/` / `.superpowers/` 这类**本地工具产物**（里面有本机绝对路径与进程 pid 文件）；
- `启动.vbs` 写死盘符 → 别人克隆到别的目录点了必然打不开；
- 打包脚本不收集依赖许可证 → 分发出的二进制没有附带授权条款。
"""
import importlib.util
import os
import re

from conftest import PROJECT_ROOT

README = os.path.join(PROJECT_ROOT, 'README.md')
NOTICES = os.path.join(PROJECT_ROOT, 'THIRD_PARTY_NOTICES.md')
GITIGNORE = os.path.join(PROJECT_ROOT, '.gitignore')
SPEC = os.path.join(PROJECT_ROOT, 'MyNotepad.spec')
HELPER = os.path.join(PROJECT_ROOT, 'build_resources', 'collect_licenses.py')


def _read(path):
    """按 utf-8 读，失败退回 gbk —— 与「导入 Markdown」同一条约定（Windows 中文脚本多是 GBK）"""
    with open(path, 'rb') as fh:
        raw = fh.read()
    for enc in ('utf-8', 'gbk'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', 'replace')


# ---------------- 许可证 ----------------

def test_mit_license_present_and_linked():
    text = _read(os.path.join(PROJECT_ROOT, 'LICENSE'))
    assert text.startswith('MIT License'), 'LICENSE 应当是 MIT 全文'
    assert 'Permission is hereby granted, free of charge' in text
    assert 'WITHOUT WARRANTY OF ANY KIND' in text
    readme = _read(README)
    assert '(LICENSE)' in readme, 'README 必须链到 LICENSE（GitHub 才会显示许可徽标）'
    assert 'MIT' in readme and '## 许可证' in readme


def test_quill_license_file_is_restored():
    path = os.path.join(PROJECT_ROOT, 'renderer', 'quill.js.LICENSE.txt')
    assert os.path.isfile(path), 'quill.js 头部指向的许可证文件必须存在'
    text = _read(path)
    assert 'Redistribution and use in source and binary forms' in text
    assert 'Slab' in text and 'Jason Chen' in text, 'BSD-3 的版权行不能丢'


def test_vendored_libraries_keep_license_headers():
    """自带的第三方压缩库必须保留许可声明（MIT/BSD 都要求"保留版权声明"）。"""
    files = ['renderer/quill.js', 'renderer/vendor/markdown-it.min.js',
             'renderer/vendor/purify.min.js', 'renderer/vendor/codemirror.js',
             'renderer/vendor/highlight.min.js']
    for rel in files:
        head = '\n'.join(_read(os.path.join(PROJECT_ROOT, rel)).splitlines()[:6]).lower()
        assert 'license' in head or 'copyright' in head, '%s 丢了许可头：%r' % (rel, head[:80])


def test_third_party_notices_covers_what_we_ship():
    text = _read(NOTICES).lower()
    for token in ['quill', 'katex', 'markdown-it', 'dompurify', 'codemirror', 'highlight.js',
                  'haarcascade', 'pystray', 'lgpl', 'pyinstaller', 'intel license agreement']:
        assert token in text, 'THIRD_PARTY_NOTICES.md 漏了 %s' % token


def test_spec_collects_dependency_licenses():
    spec = _read(SPEC)
    assert 'collect_license_datas' in spec and 'collect_licenses' in spec, \
        '打包时必须收集依赖许可证（产物里要有 licenses/）'
    assert '_license_datas' in spec and 'datas=' in spec
    # 收集失败不能让打包挂掉（拿不到许可证只是少几个说明文件）
    assert 'try:' in spec and 'except Exception' in spec


def test_collect_licenses_helper_outputs_usable_datas():
    spec = importlib.util.spec_from_file_location('collect_licenses', HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    items = mod.collect_license_datas()
    assert items, '一个都没收到说明选择逻辑坏了'
    for src, dest in items:
        assert os.path.isfile(src), '源文件不存在：%r' % src
        assert dest.startswith('licenses/') and '..' not in dest, dest
        assert '..' not in os.path.basename(src), '不许把转义路径（如 ../../LICENSE）收进来'
    dests = ' '.join(d for _s, d in items).lower()
    assert 'pystray' in dests, 'LGPL 组件 pystray 必须被收进产物'


# ---------------- 仓库卫生 ----------------

def test_local_tooling_dirs_are_ignored():
    text = _read(GITIGNORE)
    for entry in ['.claude/', '.superpowers/', 'data/', 'dist/', 'build/', '*.log']:
        assert entry in text, '.gitignore 少了 %s' % entry


def test_launchers_do_not_hardcode_drive_letters():
    """启动脚本必须能跟在任意目录（克隆到 D:/code/xxx 也得能用）。"""
    bat = _read(os.path.join(PROJECT_ROOT, '启动.bat'))
    vbs = _read(os.path.join(PROJECT_ROOT, '启动.vbs'))
    assert '%~dp0' in bat
    assert 'WScript.ScriptFullName' in vbs, 'vbs 要用脚本自身目录拼路径'
    for name, text in (('启动.bat', bat), ('启动.vbs', vbs)):
        code = '\n'.join(ln for ln in text.splitlines() if not ln.strip().startswith(("'", 'rem')))
        assert not re.search(r'[A-Za-z]:\\\\', code), '%s 里还有写死的盘符路径' % name


def test_github_housekeeping_files_exist():
    for rel in ['.github/workflows/ci.yml', '.github/workflows/release.yml',
                '.github/ISSUE_TEMPLATE/bug_report.yml', '.github/ISSUE_TEMPLATE/feature_request.yml',
                '.github/ISSUE_TEMPLATE/config.yml', 'SECURITY.md', 'CONTRIBUTING.md',
                'CHANGELOG.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE', '.gitattributes']:
        assert os.path.isfile(os.path.join(PROJECT_ROOT, rel)), '缺少 %s' % rel
    rel_yml = _read(os.path.join(PROJECT_ROOT, '.github', 'workflows', 'release.yml'))
    assert 'softprops/action-gh-release' in rel_yml, 'Release 工作流要真的发 Release'
    assert 'THIRD_PARTY_NOTICES.md' in rel_yml, '分发包里要带第三方许可清单'


def test_gitattributes_marks_vendored_frontend():
    """第三方前端库不该盖过项目自身语言统计（GitHub 语言栏靠 linguist-vendored）。"""
    text = _read(os.path.join(PROJECT_ROOT, '.gitattributes'))
    assert 'linguist-vendored' in text
    for token in ['renderer/vendor/**', 'renderer/katex/**', 'renderer/quill.js']:
        assert token in text, '.gitattributes 少了 %s' % token
    assert '*.bat text eol=crlf' in text and '*.vbs text eol=crlf' in text, \
        'Windows 脚本要固定 CRLF，否则 cmd 解析可能翻车'


# ---------------- 门面：社交预览图 ----------------

def test_social_preview_meets_github_spec():
    """`Settings → Social preview` 的图有硬规格：PNG/JPG/GIF、至少 640x320、**小于 1 MB**。

    这张图是别人在聊天软件/社交平台贴链接时的卡片。规格不对 GitHub 会拒收（或退回灰块），
    而"图看着好好的"完全看不出问题 —— 所以按 GitHub 的文档把这几条钉住，
    免得哪天换了截图重生成时悄悄超限。重生成脚本：`docs/make_social_preview.py`。
    """
    path = os.path.join(PROJECT_ROOT, 'docs', 'social-preview.png')
    assert os.path.isfile(path), 'docs/social-preview.png 缺失（Social preview 上传用）'
    size = os.path.getsize(path)
    assert size < 1024 * 1024, 'GitHub 上限 1 MB，当前 %d 字节（%.1f KB）' % (size, size / 1024)
    with open(path, 'rb') as fh:
        head = fh.read(8)
    assert head.startswith(b'\x89PNG\r\n\x1a\n'), '必须是真 PNG —— 改扩展名骗不过 GitHub'
    from PIL import Image
    with Image.open(path) as im:
        assert im.format == 'PNG'
        assert im.size[0] >= 640 and im.size[1] >= 320, 'GitHub 要求至少 640x320，当前 %s' % (im.size,)
        assert im.size == (1280, 640), '推荐尺寸 1280x640，当前 %s' % (im.size,)
