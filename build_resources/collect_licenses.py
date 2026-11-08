# -*- coding: utf-8 -*-
"""打包时把本项目**依赖树**里的第三方许可证收进产物的 `licenses/` 目录。

**为什么需要**：MIT / BSD / Apache 都要求"再分发时保留版权声明与许可条款"，而 PyInstaller 只会
自动带上**一部分**包的元数据 —— 实测 `click` / `cryptography` / `numpy` / `pydantic` 有
`*.dist-info/licenses/`，而 `Pillow` / `pywebview` / `pystray` 这些**没有**。与其依赖这种不确定性，
不如在 spec 里显式收一遍：产物自带每个依赖的许可证副本，`THIRD_PARTY_NOTICES.md` 里也就不用
把十几份法律文本抄进仓库。

**只收本项目的依赖树**（`requirements.txt` 里的包 + 它们的 `Requires-Dist` 传递闭包）：
开发机上 site-packages 里往往躺着几十个与本项目无关的包，把它们的许可证也塞进产物只会误导用户。

返回 PyInstaller 的 `datas` 条目：`[(源文件绝对路径, 产物内目录), ...]`。
收集失败（缺文件、元数据坏掉）一律跳过，**绝不让打包失败**；
发版前由 `--verify` 闸门核对产物（缺合规文件时在那里失败，见 `verify_bundle`）。
"""
from __future__ import annotations

import importlib.metadata as md
import os
import re
import sys

_LICENSE_PREFIXES = ('license', 'licence', 'copying', 'notice', 'authors')
_TEXT_SUFFIXES = ('', '.txt', '.md', '.rst')
# 后缀本身就是许可名的文件。漏掉它们收到的只是空壳：cryptography 的 `LICENSE` 只有 197 字节，
# 正文在 `LICENSE.APACHE` / `LICENSE.BSD`；pystray 的 LGPL 正文在 `COPYING.LGPL`。
_LICENSE_SUFFIXES = ('.apache', '.bsd', '.gpl', '.lgpl', '.mit', '.psf', '.python')
_MAX_BYTES = 512 * 1024          # 超过这个体积的多半不是许可证文本（有的包塞了示例数据）
# 产物里必须有的合规文件（相对产物根目录）
_REQUIRED_FILES = (
    'LICENSE',
    'THIRD_PARTY_NOTICES.md',
    '_internal/licenses/pystray/COPYING',            # LGPL-3.0 第 4 条：GPL 与 LGPL 全文都要随附
    '_internal/licenses/pystray/COPYING.LGPL',
    '_internal/pystray/__init__.py',                 # 独立文件才可替换，不能进 exe 内的模块归档
    '_internal/renderer/katex/LICENSE',
    '_internal/renderer/katex/fonts/OFL.txt',        # KaTeX 字体是 OFL-1.1，不是 MIT
    '_internal/renderer/quill.js.LICENSE.txt',
)
# 产物里不许有的文件：FFmpeg 是 LGPL-2.1 且含 H.264/HEVC/AAC 解码器，而本应用不处理视频
_FORBIDDEN_PREFIXES = ('opencv_videoio_ffmpeg',)
_REQ_RE = re.compile(r'^\s*([A-Za-z0-9][A-Za-z0-9._-]*)')     # requirements.txt 的行首包名
# 少数包的元数据里既没有 License 字段也没有 License 分类器（实测 winrt-* 七个包如此），
# 用这张前缀表兜底，免得生成的 NOTICE 里写着"见项目主页"这种没用的话。
_KNOWN_LICENSES = (
    ('winrt-', 'MIT'),
    ('winocr', 'MIT'),
    ('pywebview', 'BSD-3-Clause'),
    ('pythonnet', 'MIT'),
    ('clr-loader', 'MIT'),
    ('pillow', 'MIT-CMU (HPND)'),
    ('numpy', 'BSD-3-Clause'),
    ('cryptography', 'Apache-2.0 OR BSD-3-Clause'),
    ('opencv-contrib-python', 'Apache-2.0 (OpenCV) + MIT (opencv-python wrapper)'),
    ('pystray', 'LGPL-3.0'),
)


def _canon(name: str) -> str:
    """包名归一化（PEP 503）：`winrt-Windows.Media.Ocr` == `winrt_windows_media_ocr`"""
    return re.sub(r'[-_.]+', '-', (name or '').strip()).lower()


def project_requirements(req_path):
    """读 requirements.txt 得到直接依赖的**归一化**名字（忽略注释、空行、`-r` 之类）"""
    names = set()
    try:
        with open(req_path, encoding='utf-8') as fh:
            for line in fh:
                line = line.split('#', 1)[0].strip()
                if not line or line.startswith('-'):
                    continue
                m = _REQ_RE.match(line)
                if m:
                    names.add(_canon(m.group(1)))
    except OSError:
        pass
    return names


def _closure(roots):
    """按 Requires-Dist 展开传递闭包；环境里没装的直接跳过"""
    seen, stack = set(), list(roots)
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        try:
            dist = md.distribution(key)
        except Exception:                                    # noqa: BLE001 - 没装/名字对不上
            continue
        seen.add(key)
        for req in (dist.requires or []):
            # 'pywebview (>=6)' / 'pythonnet>=3.1.0; python_version>="3.8"' → 只取包名
            dep = _REQ_RE.match(req.strip())
            if not dep:
                continue
            name = _canon(dep.group(1))
            if name and name not in seen and 'extra ==' not in req:
                stack.append(name)
    return seen


def _is_license_file(basename: str) -> bool:
    low = basename.lower()
    if not low.startswith(_LICENSE_PREFIXES):
        return False
    return os.path.splitext(low)[1] in _TEXT_SUFFIXES + _LICENSE_SUFFIXES


def _dist_license_files(dist):
    """挑出**这个发行版自己目录下**的许可证文件。

    必须过滤掉两类东西：
      · `dist.files` 里可能写着 `../../LICENSE`（有的包指向解释器根目录，那会把 CPython 的
        许可证挂到这个包名下）；
      · 解析出来的路径不在该发行版自己的目录里（可编辑安装 / 命名空间包）。
    """
    try:
        root = os.path.abspath(str(dist.locate_file('')))
    except Exception:                                        # noqa: BLE001
        return []
    out = []
    for f in (dist.files or []):
        s = str(f)
        if any(part == '..' for part in re.split(r'[\\/]', s)):   # 显式跳过转义路径
            continue
        base = os.path.basename(s)
        if not _is_license_file(base):
            continue
        try:
            src = os.path.abspath(str(dist.locate_file(f)))
        except Exception:                                    # noqa: BLE001
            continue
        if not src.startswith(root):                         # 不属于这个发行版
            continue
        if not os.path.isfile(src) or os.path.getsize(src) > _MAX_BYTES:
            continue
        out.append((src, base))
    return out


def _meta_notice(dist):
    """轮子里**没有**许可证文件时，从元数据生成一份 NOTICE（否则产物里这个包一个字的授权都没有）。

    实测 `winrt-*` 七个包就是这样：MIT 只写在 METADATA 的 Classifier 里，轮子里没有任何 LICENSE。
    """
    meta = dist.metadata
    name = (meta.get('Name') or '?').strip()
    lic = (meta.get('License') or '').strip()
    if len(lic) > 80 or not lic:
        cls = meta.get_all('Classifier') or []
        lic = next((c.split('::')[-1].strip() for c in cls if c.startswith('License')), '')
    if not lic:
        low = name.lower()
        lic = next((v for k, v in _KNOWN_LICENSES if low.startswith(k)), '')
    lic = lic or '见项目主页'
    urls = [u for u in (meta.get_all('Project-URL') or [])]
    home = meta.get('Home-page') or ''
    if not home and urls:
        home = urls[0].split(',')[-1].strip()
    lines = [
        '%s %s' % (meta.get('Name') or '?', dist.version or '?'),
        '',
        'License: %s' % lic,
        'Author: %s' % (meta.get('Author') or meta.get('Author-email') or '见项目主页'),
        'Source: %s' % (home or '见 PyPI 项目页'),
        '',
        '本文件由 build_resources/collect_licenses.py 自动生成：该发行版没有随轮子附带许可证文件，',
        '以上信息取自它的包元数据。完整许可条款请见上面 Source 指向的项目。',
        '',
    ]
    return '\n'.join(lines)


def collect_license_datas(req_path=None):
    """返回 `[(源文件, 'licenses/<包名>'), ...]`；`req_path` 默认指向仓库根的 requirements.txt。"""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if req_path is None:
        req_path = os.path.join(repo_root, 'requirements.txt')
    gen_root = os.path.join(repo_root, 'build', 'licenses-generated')
    names = _closure(project_requirements(req_path))
    out, seen = [], set()
    for key in sorted(names):
        try:
            dist = md.distribution(key)
            name = (dist.metadata['Name'] or key).strip()
        except Exception:                                    # noqa: BLE001
            continue
        dest = 'licenses/%s' % name
        found = _dist_license_files(dist)
        if not found:                                        # 轮子里没有 → 生成一份 NOTICE
            try:
                os.makedirs(os.path.join(gen_root, name), exist_ok=True)
                path = os.path.join(gen_root, name, 'NOTICE.txt')
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(_meta_notice(dist))
                found = [(path, 'NOTICE.txt')]
            except OSError:
                found = []
        for src, base in found:
            if (dest, base) in seen:
                continue
            seen.add((dest, base))
            out.append((src, dest))
    # CPython 不是 pip 包，上面的循环收不到；LICENSE.txt 连它带的 OpenSSL、libffi 等一起写了
    py_license = os.path.join(sys.base_prefix, 'LICENSE.txt')
    if os.path.isfile(py_license):
        out.append((py_license, 'licenses/python'))
    return out


def verify_bundle(dist_dir):
    """发版闸门：核对打包产物的合规要点，返回问题清单（空 = 通过）。

    收集阶段为了不让打包挂掉而吞掉了所有错误，所以"合规文件真的进包了"要在这里单独确认。
    """
    problems = ['missing: %s' % rel for rel in _REQUIRED_FILES
                if not os.path.isfile(os.path.join(dist_dir, *rel.split('/')))]
    for base, _dirs, files in os.walk(os.path.join(dist_dir, '_internal')):
        for name in files:
            if name.lower().startswith(_FORBIDDEN_PREFIXES):
                rel = os.path.relpath(os.path.join(base, name), dist_dir).replace(os.sep, '/')
                problems.append('forbidden: %s' % rel)
    return problems


def main(argv=None):
    """`python build_resources/collect_licenses.py` 列出收集结果；
    `--verify [产物目录]` 是发版闸门（默认 dist/MyNotepad）。

    闸门的输出**只用 ASCII**：与 tcl_tk_data.py 同一条理由，runner 的 stdout 可能是 cp1252。
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] != ['--verify']:
        items = collect_license_datas()
        pkgs = sorted({dest.split('/', 1)[1] for _src, dest in items})
        print('共收集 %d 个许可证文件，覆盖 %d 个依赖：' % (len(items), len(pkgs)))
        for p in pkgs:
            print(' -', p)
        return 0
    target = args[1] if len(args) > 1 else os.path.join('dist', 'MyNotepad')
    if not os.path.isdir(target):
        print('bundle dir not found: %s' % target)
        return 2
    problems = verify_bundle(target)
    for problem in problems:
        print('::error title=License bundle gate::%s' % problem)
    if problems:
        return 1
    print('license bundle OK: %s' % target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
