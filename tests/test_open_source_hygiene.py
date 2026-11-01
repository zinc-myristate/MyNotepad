# -*- coding: utf-8 -*-
"""开源仓库的"合规与门面"守卫（纯静态，不需要浏览器）。

这些检查点都是**公开仓库**特有的坑，而且一旦回归就要么违规、要么让新用户第一次就卡住：

- 缺 `LICENSE` → GitHub 显示 "No license"，法律上等于"保留所有权利"，别人 fork 都不算合法；
- 缺 `renderer/quill.js.LICENSE.txt` → Quill 的 BSD-3 要求保留版权声明，而 `quill.js` 头部就写着
  "For license information please see quill.js.LICENSE.txt"（第一版真的忘了放这个文件）；
- 仓库里混进 `.claude/` / `.superpowers/` 这类**本地工具产物**（里面有本机绝对路径与进程 pid 文件）；
- `启动.vbs` 写死盘符 → 别人克隆到别的目录点了必然打不开；
- 打包脚本不收集依赖许可证 → 分发出的二进制没有附带授权条款；
- 收集脚本按后缀过滤时漏掉 `COPYING.LGPL`、`LICENSE.APACHE` → 产物里只剩空壳（2026-11-01 审计查出）；
- pystray 进了 exe 内的模块归档、FFmpeg 随 opencv 混进产物 → 清单写的和实际分发的对不上。
"""
import importlib.util
import os
import re
import subprocess
import sys

import pytest
from conftest import PROJECT_ROOT

README = os.path.join(PROJECT_ROOT, 'README.md')
NOTICES = os.path.join(PROJECT_ROOT, 'THIRD_PARTY_NOTICES.md')
GITIGNORE = os.path.join(PROJECT_ROOT, '.gitignore')
SPEC = os.path.join(PROJECT_ROOT, 'MyNotepad.spec')
HELPER = os.path.join(PROJECT_ROOT, 'build_resources', 'collect_licenses.py')
WORKFLOWS = os.path.join(PROJECT_ROOT, '.github', 'workflows')


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


def _helper():
    spec = importlib.util.spec_from_file_location('collect_licenses', HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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
                  'haarcascade', 'pystray', 'lgpl', 'pyinstaller', 'intel license agreement',
                  'sil ofl-1.1', 'ffmpeg', 'webview2', 'cpython', 'parchment', 'entities']:
        assert token in text, 'THIRD_PARTY_NOTICES.md 漏了 %s' % token


def test_katex_ships_its_own_license_texts():
    """KaTeX 的代码是 MIT、字体是 OFL-1.1，清单曾把两者都写成 MIT。

    OFL 要求每份副本都带许可全文，条件不满足授权即失效；而 katex.min.js 里没有任何许可头。
    """
    katex = os.path.join(PROJECT_ROOT, 'renderer', 'katex')
    assert 'MIT License' in _read(os.path.join(katex, 'LICENSE'))
    ofl = _read(os.path.join(katex, 'fonts', 'OFL.txt'))
    assert 'SIL OPEN FONT LICENSE Version 1.1' in ofl
    assert '<dates>' not in ofl, '官方模板的占位符要换成字体真实的版权行'
    for name in ('Design Science', 'Khan Academy', 'KaTeX_AMS', 'KaTeX_Typewriter'):
        assert name in ofl, 'OFL.txt 漏了 %s' % name


def test_spec_collects_dependency_licenses():
    spec = _read(SPEC)
    assert 'collect_license_datas' in spec and 'collect_licenses' in spec, \
        '打包时必须收集依赖许可证（产物里要有 licenses/）'
    assert '_license_datas' in spec and 'datas=' in spec
    # 收集失败不能让打包挂掉（拿不到许可证只是少几个说明文件）
    assert 'try:' in spec and 'except Exception' in spec


def test_collect_licenses_helper_outputs_usable_datas():
    items = _helper().collect_license_datas()
    assert items, '一个都没收到说明选择逻辑坏了'
    for src, dest in items:
        assert os.path.isfile(src), '源文件不存在：%r' % src
        assert dest.startswith('licenses/') and '..' not in dest, dest
        assert '..' not in os.path.basename(src), '不许把转义路径（如 ../../LICENSE）收进来'
    dests = ' '.join(d for _s, d in items).lower()
    assert 'pystray' in dests, 'LGPL 组件 pystray 必须被收进产物'


def test_license_files_named_after_their_license_are_kept():
    """后缀就是许可名的文件不能被过滤掉：LGPL 要求随附全文，而 pystray 的全文就在 COPYING.LGPL 里。"""
    mod = _helper()
    for name in ('COPYING.LGPL', 'LICENSE.APACHE', 'LICENSE.BSD', 'LICENCE.python', 'LICENSE', 'NOTICE.txt'):
        assert mod._is_license_file(name), name
    for name in ('license.py', 'LICENSE.pyc', 'copying.html'):
        assert not mod._is_license_file(name), name
    pystray = {os.path.basename(s) for s, d in mod.collect_license_datas() if d == 'licenses/pystray'}
    assert {'COPYING', 'COPYING.LGPL'} <= pystray, '只收到 %r' % pystray


def test_spec_matches_what_the_notices_promise():
    """清单写的是"pystray 可替换、产物里没有 FFmpeg"，spec 必须真的这么打包。"""
    spec = _read(SPEC)
    assert "module_collection_mode={'pystray': 'py'}" in spec, 'pystray 进了 PYZ 就替换不了'
    assert spec.index('a.binaries = [') < spec.index('COLLECT('), 'FFmpeg 要在 COLLECT 之前剔除'
    assert 'opencv_videoio_ffmpeg' in spec
    assert "'fastapi'" in spec and 'excludes=_UNRELATED_PACKAGES' in spec, \
        '开发机上的无关包不能进产物：它们不在依赖树里，许可证收不到'
    assert "'wheel'" not in spec, 'PyInstaller 把 wheel 当 setuptools 内置模块的别名，排除它构建直接报错'
    assert spec.index('COLLECT(') < spec.index("'THIRD_PARTY_NOTICES.md'"), \
        '清单要在 COLLECT 之后复制，否则 dist 目录还不存在'


class TestLicenseBundleGate:
    """`collect_licenses.py --verify`：收集阶段吞掉错误，合规文件有没有进包要在这里确认。"""

    def _bundle(self, root):
        for rel in _helper()._REQUIRED_FILES:
            path = os.path.join(str(root), *rel.split('/'))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write('stub')
        return str(root)

    def test_complete_bundle_passes(self, tmp_path):
        assert _helper().verify_bundle(self._bundle(tmp_path)) == []

    def test_missing_lgpl_text_is_reported(self, tmp_path):
        root = self._bundle(tmp_path)
        os.remove(os.path.join(root, '_internal', 'licenses', 'pystray', 'COPYING.LGPL'))
        assert _helper().verify_bundle(root) == ['missing: _internal/licenses/pystray/COPYING.LGPL']

    def test_pystray_inside_the_exe_is_reported(self, tmp_path):
        root = self._bundle(tmp_path)
        os.remove(os.path.join(root, '_internal', 'pystray', '__init__.py'))
        assert _helper().verify_bundle(root) == ['missing: _internal/pystray/__init__.py']

    def test_ffmpeg_coming_back_is_reported(self, tmp_path):
        root = self._bundle(tmp_path)
        cv2_dir = os.path.join(root, '_internal', 'cv2')
        os.makedirs(cv2_dir)
        with open(os.path.join(cv2_dir, 'opencv_videoio_ffmpeg500_64.dll'), 'wb') as fh:
            fh.write(b'MZ')
        assert _helper().verify_bundle(root) == [
            'forbidden: _internal/cv2/opencv_videoio_ffmpeg500_64.dll']

    def test_cli_exit_codes_survive_cp1252_stdout(self, tmp_path):
        """闸门的输出要能穿过 runner 的 cp1252 stdout，否则失败原因就成了 UnicodeEncodeError"""
        env = dict(os.environ, PYTHONIOENCODING='cp1252')
        env.pop('PYTHONUTF8', None)

        def run(path):
            return subprocess.run([sys.executable, HELPER, '--verify', path],
                                  capture_output=True, text=True, env=env)

        root = self._bundle(tmp_path / 'good')
        assert run(root).returncode == 0
        os.remove(os.path.join(root, 'LICENSE'))
        bad = run(root)
        assert bad.returncode == 1 and 'missing: LICENSE' in bad.stdout, bad
        assert 'UnicodeEncodeError' not in bad.stderr
        assert run(str(tmp_path / 'nope')).returncode == 2

    @pytest.mark.parametrize('name', ['release.yml', 'ci.yml'])
    def test_workflows_run_the_gate(self, name):
        wf = _read(os.path.join(WORKFLOWS, name))
        assert 'collect_licenses.py --verify' in wf, '%s 要跑许可证闸门' % name

    def test_gate_runs_before_the_release_zip(self):
        wf = _read(os.path.join(WORKFLOWS, 'release.yml'))
        assert wf.index('collect_licenses.py --verify') < wf.index('Compress-Archive')

    def test_local_build_runs_the_gate_too(self):
        """本机 `build.py` 打的包也可能被分发；失败时 main() 负责还原数据"""
        src = _read(os.path.join(PROJECT_ROOT, 'build.py'))
        assert "'--verify'" in src and 'collect_licenses.py' in src


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
    for rel in ['.github/workflows/ci.yml', '.github/workflows/release.yml', '.github/workflows/audit.yml',
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
