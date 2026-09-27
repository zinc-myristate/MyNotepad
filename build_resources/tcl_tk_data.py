# -*- coding: utf-8 -*-
"""Tcl/Tk 数据目录：给 `MyNotepad.spec` 兜底收集，也给测试与 CI 闸门直接调用。

## 为什么需要它（2026-09-27 发出去过一个坏包）

PyInstaller 的 Tcl/Tk 发现逻辑在 `PyInstaller/utils/hooks/tcl_tk.py::_get_tcl_tk_info()`：
它在**隔离子进程**里 `import tkinter; tkinter.Tcl()`，一旦抛 `TclError`（Tcl 找不到自己的
脚本库）就 **`return None` —— 不报错、不警告**，于是 Tcl/Tk 的数据目录一个都不进包。
而运行时的 `pyi_rth__tkinter` 仍然会去 `sys._MEIPASS/_tcl_data`、`_tk_data` 找数据，
找不到就抛：

    FileNotFoundError: Tcl data directory "...\\_internal\\_tcl_data" not found.

—— 打包版**根本起不来**（弹一个模态异常框）。实测触发条件很隐蔽：CI 的 Python 3.14 用
Tcl **9.0**、开发机是 8.6，于是只有 CI 构建出坏包；而 CI 的"启动后存活 8 秒"冒烟**恰好**
被那个模态框骗过（进程因为弹窗而活着），坏包就这样发了出去。

## 这里怎么做

**不启动 Tcl**（那正是会失败的地方），改为在构建解释器的安装目录里按**文件标记**找：
Tcl 的库目录里有 `init.tcl`、Tk 的库目录里有 `tk.tcl`。
放进包里的目标目录名必须与 PyInstaller 的 `TclTkInfo.TCL_ROOTNAME` / `TK_ROOTNAME`
（'_tcl_data' / '_tk_data'）一致 —— 运行时钩子就是按这两个名字找的（有测试钉住这一点）。
"""

import os

#: 与 PyInstaller 的 `TclTkInfo` 保持一致（tests 里会直接比对，改名会当场失败）
TCL_ROOTNAME = '_tcl_data'
TK_ROOTNAME = '_tk_data'

# 判定"这是 Tcl/Tk 的库目录"的标记：**任一**命中即可。
#
# ⚠️ 2026-09-27 实测踩到的坑（v1.0.0 第一次发布就是坏包，原因在这里）：
# 这两套标记**绝不能含 `tclIndex`**。它看着像 Tcl 库的旁证，但 Tcl 8.6 与 9.0 的库目录里
# **都有**它、而 Tk 的库目录里没有 —— 于是"这是 Tk 库"的判定被 **Tcl 库**满足了：
# `collect_from_zips` 在 `libtcl9.0.4.zip`（按名字排序在 `libtk9.0.4.zip` 之前）里命中，
# 把整个 Tcl 库解成了 `_tk_data`，真正的 tk zip 反而因为"已经收到 _tk_data"被跳过。
# 结果：包里 `_tk_data` 与 `_tcl_data` 逐文件相同、**没有 tk.tcl**，而 `verify_bundle`
# 用的是同一套弱标记 ⇒ 闸门放行。冻结点上 `tkinter.filedialog` / `messagebox` / `Tk()`
# （app.pyw 的选图片/选附件/换背景/确认框全走它们）一调用就报 Tk 错。
# ⇒ 两套标记必须**互斥**：Tcl 只认 init.tcl / clock.tcl，Tk 只认 tk.tcl / ttk/ttk.tcl。
_TCL_MARKERS = ('init.tcl', 'clock.tcl')
_TK_MARKERS = ('tk.tcl', os.path.join('ttk', 'ttk.tcl'))


def candidate_roots(base_prefix, prefix=None, env=None):
    """候选父目录（按优先级去重）：安装目录下的 `tcl/`、`lib/tcl/`，以及 `$TCL_LIBRARY` 所在目录。"""
    env = os.environ if env is None else env
    raw = []
    for p in (base_prefix, prefix):
        if p:
            raw.append(os.path.join(p, 'tcl'))
            raw.append(os.path.join(p, 'lib', 'tcl'))
    lib = env.get('TCL_LIBRARY')
    if lib:
        raw.append(os.path.dirname(os.path.abspath(lib)))
    out, seen = [], set()
    for r in raw:
        key = os.path.normcase(os.path.normpath(r))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _has_marker(d, markers):
    return any(os.path.exists(os.path.join(d, m)) for m in markers)


def find_data_dirs(roots):
    """在候选父目录里找 (Tcl 目录, Tk 目录)；找不到的那个返回 None。

    只看**文件标记**，不启动 Tcl —— 这正是与 PyInstaller 那套逻辑的区别
    （它靠 `tkinter.Tcl()` 问 Tcl 自己，问不到就静默放弃）。
    """
    tcl_dir = tk_dir = None
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            d = os.path.join(root, name)
            if not os.path.isdir(d):
                continue
            low = name.lower()
            if tcl_dir is None and low.startswith('tcl') and _has_marker(d, _TCL_MARKERS):
                tcl_dir = d
            elif tk_dir is None and low.startswith('tk') and _has_marker(d, _TK_MARKERS):
                tk_dir = d
        if tcl_dir and tk_dir:
            break
    return tcl_dir, tk_dir


def collect(base_prefix, prefix=None, env=None, workdir=None):
    """返回可直接追加到 spec `datas` 的项：`[(源目录, '_tcl_data' | '_tk_data'), ...]`。

    先按磁盘目录找；找不到的再去 zip 里解（见 `collect_from_zips` 的说明 —— 部分 Windows
    发行版把 Tcl/Tk 9 的脚本库打成了 zip，磁盘上没有目录）。只包含**真正找到**的那些：
    缺一个就少一项，由调用方决定是中止构建还是继续。
    """
    tcl_dir, tk_dir = find_data_dirs(candidate_roots(base_prefix, prefix, env))
    out = []
    if tcl_dir:
        out.append((tcl_dir, TCL_ROOTNAME))
    if tk_dir:
        out.append((tk_dir, TK_ROOTNAME))
    if not (tcl_dir and tk_dir):
        have = {dest for _src, dest in out}
        for src, dest in collect_from_zips(base_prefix, prefix, workdir):
            if dest not in have:
                out.append((src, dest))
    return out


def _zip_candidates(base_prefix, prefix=None, env=None):
    """候选目录下所有 `.zip`（Tcl/Tk 9 的库压缩包就放在 `<prefix>/tcl/` 里）。"""
    out = []
    for root in candidate_roots(base_prefix, prefix, env):
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if name.lower().endswith('.zip'):
                out.append(os.path.join(root, name))
    return out


def _library_root_in_zip(zf, markers):
    """在 zip 里找"库根目录"：含标记文件（init.tcl / tk.tcl …）的那个**最短**目录。

    `markers` 可以是文件名（`init.tcl`）也可以是相对路径（`ttk/ttk.tcl`）——
    两者都按"路径尾段"匹配，库根就是去掉这段尾段后的前缀。

    ⚠️ **不再按目录名（`tcl_library` / `tk_library`）兜底**：那条线索在标记认不出来时给出的是
    **未经证实**的根，v1.0.0 正是"猜了一个根就直接解包"才出现 `_tk_data` 里装 Tcl 库。
    现在的规矩是：认不出标记就不解包 ⇒ 构建期由 spec 大声失败（并打出诊断），
    而不是悄悄发一个"能启动、点文件对话框才炸"的包。
    """
    want = [m.replace('\\', '/').strip('/') for m in markers]
    best = None
    for name in zf.namelist():
        norm = name.replace('\\', '/')
        parts = [p for p in norm.split('/') if p]
        for m in want:
            tail = m.split('/')
            if not parts or parts[-len(tail):] != tail:
                continue
            root = '/'.join(parts[:-len(tail)])
            if best is None or len(root) < len(best):
                best = root
            break
    return best


def _extract_subtree(zip_path, inner_prefix, target):
    """把 zip 里 `inner_prefix/` 这棵子树解到 `target/`（去掉前缀，保持相对结构）。"""
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if inner_prefix and not name.startswith(inner_prefix + '/'):
                continue
            rel = name[len(inner_prefix) + 1:] if inner_prefix else name
            if not rel or rel.endswith('/'):
                continue
            dst = os.path.join(target, *rel.split('/'))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with zf.open(info) as src, open(dst, 'wb') as fh:
                fh.write(src.read())


def collect_from_zips(base_prefix, prefix=None, workdir=None):
    """从 Tcl/Tk 9 的库 zip 里解出数据目录（磁盘上没有目录时的兜底）。

    ## 为什么需要（2026-09-27 实测的 runner 布局）

    Windows 上的 Python 3.14.7 把 Tcl/Tk 9 的脚本库打成了 zip：
    `<prefix>\\tcl\\libtcl9.0.4.zip`、`libtk9.0.4.zip`，Tcl 通过 zipfs 加载，
    `info library` 返回的是**虚拟路径** `//zipfs:/lib/tcl/tcl_library` —— 磁盘上不存在。
    于是 PyInstaller 收不到数据（它只会照着磁盘路径拷），我们按目录扫描也扫不到，
    打包出来的 exe 一启动就在 `pyi_rth__tkinter` 里抛 "Tcl data directory ... not found"。

    做法：把 zip 里含 `init.tcl`（Tcl）/ `tk.tcl`（Tk）的那棵子树解到临时目录，
    再按 `_tcl_data` / `_tk_data` 放进包里。运行时钩子只认"目录里有库脚本"，不关心它从哪来。

    ⚠️ 解出来之后**必须复验标记**（`_has_marker`）：这是"数据装错"的最后一道拦截 ——
    2026-09-27 那个坏包正是"解出来就没再看一眼"的后果（`_tk_data` 里装的是 Tcl 库，
    而 Tcl 库里有 `tclIndex`，当年那套宽松标记把它当成了 Tk 库）。
    """
    import tempfile
    workdir = workdir or tempfile.mkdtemp(prefix='mynotepad_tcl_tk_')
    out = []
    for zip_path in _zip_candidates(base_prefix, prefix):
        # 同一个 zip 里 Tcl 与 Tk 可能各有一棵子树；也可能分两个 zip
        for dest, markers in ((TCL_ROOTNAME, _TCL_MARKERS), (TK_ROOTNAME, _TK_MARKERS)):
            if any(d == dest for _src, d in out):
                continue
            try:
                import zipfile
                with zipfile.ZipFile(zip_path) as zf:
                    inner = _library_root_in_zip(zf, markers)
            except Exception:                                     # noqa: BLE001
                continue
            if not inner:
                continue
            target = os.path.join(workdir, dest)
            try:
                _extract_subtree(zip_path, inner, target)
            except Exception:                                     # noqa: BLE001
                continue
            if (os.path.isdir(target) and os.listdir(target)
                    and _has_marker(target, markers)):
                out.append((target, dest))
    return out


def hook_dest_names(data_files):
    """从 PyInstaller 的 `tcltk_info.data_files`（3 元 TOC）里取出它已经收到的顶层目标名。"""
    names = set()
    for entry in data_files or []:
        dest = str(entry[0]).replace('\\', '/')
        head = dest.split('/', 1)[0]
        if head in (TCL_ROOTNAME, TK_ROOTNAME):
            names.add(head)
    return names


def verify_bundle(bundle_root):
    """校验打包产物里 Tcl/Tk 数据都在；返回缺失的目标目录名列表（空 = 通过）。

    CI 用它当**发版闸门**（`python build_resources/tcl_tk_data.py dist/MyNotepad`）：
    缺一个就不许打 zip、更不许发 Release。
    `bundle_root` 传 `dist/MyNotepad`，数据在它下面的 `_internal/`（PyInstaller 6 的布局）。

    ⚠️ 这里的判定强度**就是标记的强度**：标记写成宽松的（比如含 `tclIndex`）闸门就会放行
    "`_tk_data` 里装的是 Tcl 库"这种坏包（2026-09-27 真实发生过，见 `_TCL_MARKERS` 上的注释）。
    """
    missing = []
    for name, markers in ((TCL_ROOTNAME, _TCL_MARKERS), (TK_ROOTNAME, _TK_MARKERS)):
        for base in (os.path.join(bundle_root, '_internal'), bundle_root):
            d = os.path.join(base, name)
            if os.path.isdir(d) and _has_marker(d, markers):
                break
        else:
            missing.append(name)
    return missing


def describe_bundle(bundle_root):
    """闸门失败时打给 CI 注解看的现场：每个数据目录在不在、标记命中没命中、里面装着什么。

    为什么要有它：Actions 的 job 日志要登录才能读，而 `::error::` 注解是公开可读的 ——
    上一次坏包排查就是靠注解才看到 runner 的 Tcl 9 zipfs 布局。失败信息必须自带证据。
    （`ensure_ascii=True`：注解与 runner 的 cp1252 stdout 都只吃 ASCII。）
    """
    report = {}
    for name, markers in ((TCL_ROOTNAME, _TCL_MARKERS), (TK_ROOTNAME, _TK_MARKERS)):
        entry = {'want_markers': [m.replace('\\', '/') for m in markers],
                 'exists': False, 'ok': False}
        for base in (os.path.join(bundle_root, '_internal'), bundle_root):
            d = os.path.join(base, name)
            if not os.path.isdir(d):
                continue
            entry['exists'] = True
            entry['path'] = d
            entry['files'] = sum(len(files) for _r, _dirs, files in os.walk(d))
            entry['top'] = sorted(os.listdir(d))[:16]
            entry['markers_hit'] = [m.replace('\\', '/') for m in markers
                                    if os.path.exists(os.path.join(d, m))]
            entry['ok'] = bool(entry['markers_hit'])
            break
        report[name] = entry
    return report


def diagnostics(base_prefix=None, prefix=None, env=None):
    """给 CI 报错用的一行诊断：解释器路径、候选目录里到底有什么、tkinter 能不能起来。

    为什么要它：产物构建失败时，**排障要用的 job 日志需要登录才能读**；而 GitHub Actions 的
    `::error::` 注解是公开可读的。spec 在决定中止构建前会把它打进注解里，这样"为什么找不到
    Tcl/Tk"一眼可见，不用去翻日志。
    """
    import json
    import sys
    base_prefix = sys.base_prefix if base_prefix is None else base_prefix
    prefix = sys.prefix if prefix is None else prefix
    roots = candidate_roots(base_prefix, prefix, env)
    info = {
        'python': sys.version.split()[0],
        'base_prefix': base_prefix,
        'prefix': prefix,
        'TCL_LIBRARY': (os.environ if env is None else env).get('TCL_LIBRARY'),
        'roots': [],
        'tkinter': None,
    }
    for r in roots:
        entry = {'path': r, 'exists': os.path.isdir(r)}
        if entry['exists']:
            entry['children'] = sorted(os.listdir(r))[:24]
        info['roots'].append(entry)
    try:
        info['tcl_zips'] = [os.path.basename(p) for p in _zip_candidates(base_prefix, prefix)]
    except Exception:                                             # noqa: BLE001
        info['tcl_zips'] = []
    try:
        import _tkinter
        info['tkinter'] = {'file': getattr(_tkinter, '__file__', None),
                           'TCL_VERSION': _tkinter.TCL_VERSION,
                           'TK_VERSION': _tkinter.TK_VERSION}
        try:
            import tkinter
            info['tkinter']['info_library'] = tkinter.Tcl().eval('info library')
        except Exception as exc:                                  # noqa: BLE001
            info['tkinter']['info_library_error'] = repr(exc)[:200]
    except Exception as exc:                                      # noqa: BLE001
        info['tkinter'] = {'import_error': repr(exc)[:200]}
    # ensure_ascii=True：诊断要穿过 cp1252 的 stdout 与 Actions 注解，ASCII 才最稳（路径里的
    # 非 ASCII 会变成 \uXXXX，信息不丢）
    return json.dumps(info, ensure_ascii=True)


def main(argv=None):
    """命令行入口：`python build_resources/tcl_tk_data.py [产物目录]`（默认 dist/MyNotepad）。

    输出**只用 ASCII**：这个命令是 CI 的发版闸门，而 runner 的 stdout 可能是 cp1252 ——
    中文 print 会抛 UnicodeEncodeError，把"闸门本身"变成失败原因（2026-09-27 实测踩到）。
    """
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    target = args[0] if args else os.path.join('dist', 'MyNotepad')
    if not os.path.isdir(target):
        print('bundle dir not found: %s' % target)
        return 2
    missing = verify_bundle(target)
    if missing:
        import json
        print('missing Tcl/Tk data dirs in %s: %s '
              '(the frozen app would die in pyi_rth__tkinter)' % (target, ', '.join(missing)))
        # 现场（存在性与标记命中情况）：让失败原因不必登录就能从 Actions 注解里读出来
        print('bundle report: %s'
              % json.dumps(describe_bundle(target), ensure_ascii=True, sort_keys=True))
        return 1
    print('Tcl/Tk data dirs OK in: %s' % target)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
