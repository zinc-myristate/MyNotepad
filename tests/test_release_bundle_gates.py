# -*- coding: utf-8 -*-
"""发版产物闸门：Tcl/Tk 数据必须在包里，冒烟必须留下"真的跑起来了"的物证。

## 这一组测试是为什么来的

2026-09-27 打了第一个 tag `v1.0.0`，Release 工作流**全绿**、zip 也发了出去 —— 但那个包
**根本起不来**：双击弹

    Failed to execute script 'pyi_rth_tkinter' ... Tcl data directory "...\\_internal\\_tcl_data" not found.

根因（从 PyInstaller 源码读出来的，不是猜）：`PyInstaller/utils/hooks/tcl_tk.py::_get_tcl_tk_info()`
在隔离子进程里 `import tkinter; tkinter.Tcl()`，**一抛 `TclError` 就 `return None`**：
不报错、不警告，Tcl/Tk 的数据目录一个都不进包；而运行时的 `pyi_rth__tkinter` 仍然去
`sys._MEIPASS/_tcl_data`、`_tk_data` 找数据，找不到直接抛异常。
触发条件很隐蔽：runner 上的 Python 3.14 用 **Tcl 9.0**、开发机是 8.6，只有 CI 构建踩到。

而且**冒烟没能拦住**：原来的冒烟只判"进程启动后 8 秒没退出"，而启动异常会弹一个**模态框**，
进程因为弹窗而活着 → 坏包被判成通过。

所以这一组测试盯三件事：
1. 兜底发现逻辑本身对（按文件标记找，不启动 Tcl）；
2. 目标目录名与 PyInstaller 的 `TclTkInfo` 常量一致（运行时钩子按那两个名字找）；
3. spec 与两个工作流里确实接了闸门（缺数据要中止构建、冒烟要有功能那一层）。
"""
import json
import os
import re
import shutil
import sys

import pytest
from conftest import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, 'build_resources'))
import tcl_tk_data as tkdata  # noqa: E402

SPEC = os.path.join(PROJECT_ROOT, 'MyNotepad.spec')
WORKFLOWS = os.path.join(PROJECT_ROOT, '.github', 'workflows')
VERSION_INFO = os.path.join(PROJECT_ROOT, 'build_resources', 'version_info.txt')
CHANGELOG = os.path.join(PROJECT_ROOT, 'CHANGELOG.md')


def _read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


# ---------------- 1. 兜底发现逻辑（纯函数，用临时目录造各种布局） ----------------

class TestFindDataDirs:
    def _mk(self, root, name, marker):
        d = os.path.join(root, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, marker), 'w', encoding='utf-8') as f:
            f.write('# stub')
        return d

    def test_finds_tcl86_and_tk86(self, tmp_path):
        root = tmp_path / 'tcl'
        self._mk(str(root), 'tcl8.6', 'init.tcl')
        self._mk(str(root), 'tk8.6', 'tk.tcl')
        tcl_dir, tk_dir = tkdata.find_data_dirs([str(root)])
        assert os.path.basename(tcl_dir) == 'tcl8.6'
        assert os.path.basename(tk_dir) == 'tk8.6'

    def test_finds_tcl9_layout(self, tmp_path):
        """runner 上的 Python 3.14 就是 Tcl 9 —— 正是这里必须能找出来"""
        root = tmp_path / 'tcl'
        self._mk(str(root), 'tcl9.0', 'init.tcl')
        self._mk(str(root), 'tk9.0', 'tk.tcl')
        pairs = tkdata.collect(str(tmp_path), env={})
        assert {dest for _src, dest in pairs} == {tkdata.TCL_ROOTNAME, tkdata.TK_ROOTNAME}

    def test_ignores_dirs_without_marker(self, tmp_path):
        """只有目录名像不算数：没有 init.tcl / tk.tcl 就不是 Tcl/Tk 的库目录"""
        root = tmp_path / 'tcl'
        os.makedirs(str(root / 'tcl8.6'))
        os.makedirs(str(root / 'tk8.6'))
        assert tkdata.find_data_dirs([str(root)]) == (None, None)
        assert tkdata.collect(str(tmp_path), env={}) == []

    def test_tcl_only_is_reported_as_partial(self, tmp_path):
        """只找到 Tcl 没找到 Tk 时，只返回找到的那一项（由调用方决定是否中止构建）"""
        root = tmp_path / 'tcl'
        self._mk(str(root), 'tcl8.6', 'init.tcl')
        pairs = tkdata.collect(str(tmp_path), env={})
        assert [dest for _src, dest in pairs] == [tkdata.TCL_ROOTNAME]

    def test_missing_root_is_not_an_error(self, tmp_path):
        assert tkdata.collect(str(tmp_path / 'nope'), env={}) == []

    def test_tcl_library_env_is_used_as_fallback_root(self, tmp_path):
        """有的发行版把库装在别处并用 $TCL_LIBRARY 指出 —— 那是最后一个候选"""
        other = tmp_path / 'elsewhere'
        tcl = self._mk(str(other), 'tcl8.6', 'init.tcl')
        self._mk(str(other), 'tk8.6', 'tk.tcl')
        pairs = tkdata.collect(str(tmp_path / 'empty'), env={'TCL_LIBRARY': tcl})
        assert {dest for _src, dest in pairs} == {tkdata.TCL_ROOTNAME, tkdata.TK_ROOTNAME}

    def test_dest_names_match_pyinstaller_constants(self):
        """目标目录名必须与 PyInstaller 的 TclTkInfo 一致 —— 运行时钩子按那两个名字找。

        这条是**跨库耦合**的护栏：PyInstaller 哪天改了常量名（比如换成 'tcl_data'），
        我们的兜底就会把数据放进一个没人找的目录里，而打包不会报任何错。
        """
        tcl_tk = pytest.importorskip('PyInstaller.utils.hooks.tcl_tk')
        assert tkdata.TCL_ROOTNAME == tcl_tk.TclTkInfo.TCL_ROOTNAME
        assert tkdata.TK_ROOTNAME == tcl_tk.TclTkInfo.TK_ROOTNAME


class TestMarkersAreStrict:
    """标记必须**互斥**：`tclIndex` 不能当"这是 Tcl/Tk 库"的依据。

    2026-09-27 发出去过一个坏包：`_tk_data` 与 `_tcl_data` 逐文件相同（装的其实是 Tcl 库、
    里面**没有 tk.tcl**），而闸门放行了 —— 因为当时的标记里有 `tclIndex`：
    Tcl 8.6 与 9.0 的库目录**都有**它，于是"这是 Tk 库"被 Tcl 库满足了。
    实测 runner 解出来的 Tcl 9 布局（`init.tcl` / `clock.tcl` / `encoding/` / `tzdata/`）
    说明 `init.tcl` 一直是可靠标记，不需要那种"旁证"。
    """

    def _dir(self, root, name, *files):
        d = os.path.join(str(root), name)
        os.makedirs(d, exist_ok=True)
        for f in files:
            p = os.path.join(d, f)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write('# stub')
        return d

    def test_marker_sets_are_mutually_exclusive(self):
        """两套标记不许有交集，也不许含两库都有的文件名（这正是坏包的成因）"""
        assert not (set(tkdata._TCL_MARKERS) & set(tkdata._TK_MARKERS))
        for marker in tuple(tkdata._TCL_MARKERS) + tuple(tkdata._TK_MARKERS):
            assert marker.replace('\\', '/') not in ('tclIndex', 'encoding'), \
                '%s 两库都可能有，不能当判据' % marker

    def test_tcl_index_alone_is_nobody_s_library(self, tmp_path):
        """只有 tclIndex 的目录：既不是 Tcl 库，也**不许被认成 Tk 库**"""
        root = tmp_path / 'tcl'
        only_index = self._dir(root, 'tcl9.0', 'tclIndex')
        assert not tkdata._has_marker(only_index, tkdata._TCL_MARKERS)
        assert not tkdata._has_marker(only_index, tkdata._TK_MARKERS)
        self._dir(root, 'tk9.0', 'tk.tcl')
        tcl_dir, tk_dir = tkdata.find_data_dirs([str(root)])
        assert tcl_dir is None, '只有 tclIndex 的目录不算 Tcl 库'
        assert os.path.basename(tk_dir) == 'tk9.0'

    def test_real_tcl_9_layout_is_recognised(self, tmp_path):
        """实测的 Tcl 9.0 库顶层（从坏包解出来的那份）：init.tcl / clock.tcl / encoding/ / tzdata/"""
        root = tmp_path / 'tcl'
        d = self._dir(root, 'tcl9.0', 'init.tcl', 'clock.tcl', 'icu.tcl',
                      os.path.join('encoding', 'ascii.enc'), os.path.join('tzdata', 'UTC'))
        assert tkdata.find_data_dirs([str(root)])[0] == d

    def test_tk_dir_with_only_ttk_theme(self, tmp_path):
        root = tmp_path / 'tcl'
        self._dir(root, 'tcl9.0', 'init.tcl')
        self._dir(root, 'tk9.0', os.path.join('ttk', 'ttk.tcl'))
        assert tkdata.find_data_dirs([str(root)])[1]

    def test_lib_tcl_root_is_also_searched(self, tmp_path):
        """有的发行版把库放在 <prefix>/lib/tcl 下"""
        root = tmp_path / 'lib' / 'tcl'
        self._dir(root, 'tcl8.6', 'init.tcl')
        self._dir(root, 'tk8.6', 'tk.tcl')
        pairs = tkdata.collect(str(tmp_path), env={})
        assert {dest for _src, dest in pairs} == {tkdata.TCL_ROOTNAME, tkdata.TK_ROOTNAME}

    def test_empty_dir_is_never_accepted(self, tmp_path):
        os.makedirs(str(tmp_path / 'tcl' / 'tcl9.0'))
        os.makedirs(str(tmp_path / 'tcl' / 'tk9.0'))
        assert tkdata.find_data_dirs([str(tmp_path / 'tcl')]) == (None, None)


class TestDiagnostics:
    def test_single_line_json(self):
        """诊断要能直接塞进 GitHub Actions 的注解 —— 注解不支持换行，多行会被截断"""
        import json as _json
        text = tkdata.diagnostics()
        assert '\n' not in text and '\r' not in text
        info = _json.loads(text)
        for key in ('python', 'base_prefix', 'prefix', 'roots', 'tkinter'):
            assert key in info, '诊断缺少 %s' % key

    def test_reports_roots_even_when_missing(self, tmp_path):
        import json as _json
        info = _json.loads(tkdata.diagnostics(
            base_prefix=str(tmp_path / 'nope'), prefix=str(tmp_path / 'nope2'), env={}))
        assert info['roots'], '即使一个候选目录都不存在，也要把它们列出来'
        assert all(r['exists'] is False for r in info['roots'])
        assert all('children' not in r for r in info['roots'])


class TestZipfsLayout:
    """Tcl/Tk 9 在部分 Windows 发行版里把脚本库打成了 zip（实测：Python 3.14.7 的 toolcache）。

    runner 的公开诊断注解给出了铁证：
        info library = "//zipfs:/lib/tcl/tcl_library"
        <prefix>\\tcl 下只有 libtcl9.0.4.zip / libtk9.0.4.zip，没有任何库目录
    —— 磁盘上没有目录，PyInstaller 收不到、目录扫描也扫不到。这里覆盖"从 zip 解出来"这条路。
    """

    def _zip(self, path, entries):
        import zipfile
        os.makedirs(os.path.dirname(str(path)), exist_ok=True)
        with zipfile.ZipFile(str(path), 'w') as zf:
            for name in entries:
                zf.writestr(name, '# stub')

    def test_extracts_tcl_and_tk_from_separate_zips(self, tmp_path):
        tcl_root = tmp_path / 'py' / 'tcl'
        self._zip(tcl_root / 'libtcl9.0.4.zip',
                  ['lib/tcl/tcl_library/init.tcl', 'lib/tcl/tcl_library/encoding/ascii.enc'])
        self._zip(tcl_root / 'libtk9.0.4.zip',
                  ['lib/tk/tk_library/tk.tcl', 'lib/tk/tk_library/ttk/ttk.tcl'])
        pairs = tkdata.collect(str(tmp_path / 'py'), env={}, workdir=str(tmp_path / 'work'))
        got = {dest: src for src, dest in pairs}
        assert set(got) == {tkdata.TCL_ROOTNAME, tkdata.TK_ROOTNAME}
        assert os.path.isfile(os.path.join(got[tkdata.TCL_ROOTNAME], 'init.tcl'))
        assert os.path.isfile(os.path.join(got[tkdata.TK_ROOTNAME], 'tk.tcl'))
        assert os.path.isfile(os.path.join(got[tkdata.TK_ROOTNAME], 'ttk', 'ttk.tcl')), \
            '子树要按相对结构解开（少了 ttk 主题，Tk 界面会缺主题）'

    def test_extracts_both_from_one_zip(self, tmp_path):
        tcl_root = tmp_path / 'py' / 'tcl'
        self._zip(tcl_root / 'libtcl9.0.4.zip',
                  ['lib/tcl/tcl_library/init.tcl', 'lib/tk/tk_library/tk.tcl'])
        pairs = tkdata.collect(str(tmp_path / 'py'), env={}, workdir=str(tmp_path / 'work'))
        assert {dest for _src, dest in pairs} == {tkdata.TCL_ROOTNAME, tkdata.TK_ROOTNAME}

    def test_disk_layout_wins_over_zip(self, tmp_path):
        """磁盘上有真目录时不该去解 zip（少花时间、也少一层不确定性）"""
        base = tmp_path / 'py'
        self._mk_dir(base / 'tcl' / 'tcl8.6', 'init.tcl')
        self._mk_dir(base / 'tcl' / 'tk8.6', 'tk.tcl')
        self._zip(base / 'tcl' / 'libtcl9.0.4.zip', ['lib/tcl/tcl_library/init.tcl'])
        pairs = tkdata.collect(str(base), env={}, workdir=str(tmp_path / 'work'))
        assert all(os.path.basename(src) in ('tcl8.6', 'tk8.6') for src, _d in pairs)

    def test_tcl_zip_never_fills_tk_data(self, tmp_path):
        """★ 坏包复刻：tcl 的 zip **按名字排序更靠前**，而且它的库目录里也有 `tclIndex`。

        旧代码就是把 `tclIndex` 当"Tk 库"的判据，在 `libtcl9.0.4.zip` 里命中后
        把整个 Tcl 库解成了 `_tk_data`，真正的 tk zip 因为"已经收到 _tk_data"被跳过 ——
        发出去的 v1.0.0 就是这样少了 tk.tcl（点击选图片/选附件才报 Tk 错）。
        """
        tcl_root = tmp_path / 'py' / 'tcl'
        self._zip(tcl_root / 'libtcl9.0.4.zip',
                  ['lib/tcl/tcl_library/init.tcl', 'lib/tcl/tcl_library/tclIndex',
                   'lib/tcl/tcl_library/clock.tcl', 'lib/tcl/tcl_library/tzdata/UTC'])
        self._zip(tcl_root / 'libtk9.0.4.zip',
                  ['lib/tk/tk_library/tk.tcl', 'lib/tk/tk_library/tclIndex',
                   'lib/tk/tk_library/ttk/ttk.tcl'])
        pairs = tkdata.collect(str(tmp_path / 'py'), env={}, workdir=str(tmp_path / 'work'))
        got = {dest: src for src, dest in pairs}
        tk_src = got[tkdata.TK_ROOTNAME]
        assert os.path.isfile(os.path.join(tk_src, 'tk.tcl'))
        assert os.path.isfile(os.path.join(tk_src, 'ttk', 'ttk.tcl'))
        assert not os.path.isfile(os.path.join(tk_src, 'init.tcl')), \
            '_tk_data 里混进了 Tcl 的 init.tcl —— 这正是 v1.0.0 坏包的形状'

    def test_unknown_layout_is_not_guessed(self, tmp_path):
        """认不出标记时**宁可一个都不给**：构建期由 spec 大声失败，而不是发一个装错数据的包。

        旧版会按目录名（`tcl_library` / `tk_library`）兜底 —— 那条"线索"给出的是未经证实的根，
        v1.0.0 坏包的 `_tk_data` 就是这么定下来的。现在只认标记。
        """
        tcl_root = tmp_path / 'py' / 'tcl'
        self._zip(tcl_root / 'libtcl9.0.4.zip',
                  ['lib/tcl/tcl_library/some-unknown-init.tclx',
                   'lib/tk/tk_library/some-unknown.tclx'])
        pairs = tkdata.collect(str(tmp_path / 'py'), env={}, workdir=str(tmp_path / 'work'))
        assert pairs == []

    def test_broken_zip_is_ignored(self, tmp_path):
        tcl_root = tmp_path / 'py' / 'tcl'
        os.makedirs(str(tcl_root))
        (tcl_root / 'libtcl9.0.4.zip').write_text('not a zip', encoding='utf-8')
        assert tkdata.collect(str(tmp_path / 'py'), env={}, workdir=str(tmp_path / 'work')) == []

    def test_diagnostics_lists_zips(self, tmp_path):
        import json as _json
        base = tmp_path / 'py'
        self._zip(base / 'tcl' / 'libtcl9.0.4.zip', ['lib/tcl/tcl_library/init.tcl'])
        info = _json.loads(tkdata.diagnostics(base_prefix=str(base), prefix=str(base), env={}))
        assert info['tcl_zips'] == ['libtcl9.0.4.zip']

    def _mk_dir(self, d, marker):
        os.makedirs(str(d), exist_ok=True)
        (d / marker).write_text('# stub', encoding='utf-8')


# ---------------- 2. 产物闸门（verify_bundle） ----------------

class TestVerifyBundle:
    def _bundle(self, root, with_tcl=True, with_tk=True):
        internal = os.path.join(root, '_internal')
        os.makedirs(internal, exist_ok=True)
        for name, marker, present in ((tkdata.TCL_ROOTNAME, 'init.tcl', with_tcl),
                                      (tkdata.TK_ROOTNAME, 'tk.tcl', with_tk)):
            if not present:
                continue
            d = os.path.join(internal, name)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, marker), 'w', encoding='utf-8') as f:
                f.write('# stub')
        return root

    def test_good_bundle_passes(self, tmp_path):
        assert tkdata.verify_bundle(self._bundle(str(tmp_path / 'ok'))) == []

    def test_broken_bundle_reports_both(self, tmp_path):
        """这正是 2026-09-27 发出去那个包的样子：DLL 在、数据目录一个没有"""
        root = tmp_path / 'broken'
        os.makedirs(os.path.join(str(root), '_internal'), exist_ok=True)
        assert tkdata.verify_bundle(str(root)) == [tkdata.TCL_ROOTNAME, tkdata.TK_ROOTNAME]

    def test_partial_bundle_reports_missing_one(self, tmp_path):
        got = tkdata.verify_bundle(self._bundle(str(tmp_path / 'half'), with_tk=False))
        assert got == [tkdata.TK_ROOTNAME]

    def test_empty_dir_without_marker_is_missing(self, tmp_path):
        """空目录不算数（数据没被复制进去时就是空目录）"""
        root = tmp_path / 'empty'
        os.makedirs(os.path.join(str(root), '_internal', tkdata.TCL_ROOTNAME), exist_ok=True)
        os.makedirs(os.path.join(str(root), '_internal', tkdata.TK_ROOTNAME), exist_ok=True)
        assert tkdata.verify_bundle(str(root)) == [tkdata.TCL_ROOTNAME, tkdata.TK_ROOTNAME]

    def test_cli_returns_nonzero_on_broken(self, tmp_path, capsys):
        root = tmp_path / 'via-cli'
        os.makedirs(str(root), exist_ok=True)
        assert tkdata.main([str(root)]) == 1
        assert 'Tcl' in capsys.readouterr().out

    def test_cli_returns_zero_on_good(self, tmp_path):
        assert tkdata.main([self._bundle(str(tmp_path / 'cli-ok'))]) == 0

    def test_tk_data_holding_a_tcl_library_is_rejected(self, tmp_path):
        """★ 复刻**已发布的那个坏包**：`_tk_data` 与 `_tcl_data` 内容相同。

        实测那一版的 `_internal\\_tk_data`：顶层是 `cookiejar/ encoding/ tcltest/ tzdata/ …`
        （Tcl 9 的库），有 `init.tcl`、有 `tclIndex`，**没有 tk.tcl**。
        含 `tclIndex` 的宽松标记会把它判成"Tk 数据在" ⇒ 闸门放行 ⇒ 坏包发到 Release。
        """
        root = tmp_path / 'bad'
        internal = os.path.join(str(root), '_internal')
        tcl_lib = os.path.join(internal, tkdata.TCL_ROOTNAME)
        for rel in ('init.tcl', 'clock.tcl', 'tclIndex', os.path.join('tzdata', 'UTC')):
            p = os.path.join(tcl_lib, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write('# stub')
        shutil.copytree(tcl_lib, os.path.join(internal, tkdata.TK_ROOTNAME))
        assert tkdata.verify_bundle(str(root)) == [tkdata.TK_ROOTNAME], \
            'Tcl 库被当成 Tk 数据时必须判失败（这正是 v1.0.0 漏掉的那一关）'

    def test_cli_failure_reports_the_scene(self, tmp_path, capsys):
        """失败时要把现场打进输出：Actions 的 job 日志要登录才能读，注解是唯一公开通道"""
        root = tmp_path / 'bad2'
        internal = os.path.join(str(root), '_internal', tkdata.TK_ROOTNAME)
        os.makedirs(internal)
        with open(os.path.join(internal, 'init.tcl'), 'w', encoding='utf-8') as fh:
            fh.write('# stub')
        assert tkdata.main([str(root)]) == 1
        out = capsys.readouterr().out
        assert 'bundle report' in out and 'markers_hit' in out, \
            '闸门失败必须自带证据（哪个目录、命中了哪些标记）'
        assert out.isascii(), '输出要能穿过 cp1252 的 stdout'

    def test_describe_bundle_explains_a_wrong_tk_dir(self, tmp_path):
        internal = os.path.join(str(tmp_path / 'x'), '_internal')
        tk = os.path.join(internal, tkdata.TK_ROOTNAME)
        os.makedirs(os.path.join(tk, 'tzdata'))
        with open(os.path.join(tk, 'init.tcl'), 'w', encoding='utf-8') as fh:
            fh.write('# stub')
        report = tkdata.describe_bundle(str(tmp_path / 'x'))
        tk_info = report[tkdata.TK_ROOTNAME]
        assert tk_info['exists'] is True and tk_info['ok'] is False
        assert 'init.tcl' in tk_info['top'] and tk_info['markers_hit'] == []
        assert report[tkdata.TCL_ROOTNAME]['exists'] is False


# ---------------- 3. 接线：spec 与两个工作流都必须带闸门 ----------------

class TestWiring:
    def test_spec_falls_back_and_aborts_loudly(self):
        spec = _read(SPEC)
        assert 'tcl_tk_data' in spec, 'spec 必须用兜底模块找 Tcl/Tk 数据'
        assert 'hook_dest_names' in spec, 'spec 要先看 PyInstaller 收到了什么，再决定补哪些'
        assert 'raise SystemExit' in spec, '兜底也找不到时必须**中止构建**，而不是发一个起不来的包'
        # 两份数据都要进 datas
        assert '_tcl_tk_extra' in spec and 'datas=' in spec

    def test_spec_compiles(self):
        compile(_read(SPEC), SPEC, 'exec')

    @pytest.mark.parametrize('name', ['release.yml', 'ci.yml'])
    def test_workflows_run_the_bundle_gate(self, name):
        wf = _read(os.path.join(WORKFLOWS, name))
        assert 'build_resources/tcl_tk_data.py' in wf, \
            '%s 必须调用产物闸门（缺 Tcl/Tk 数据就失败）' % name

    @pytest.mark.parametrize('name', ['release.yml', 'ci.yml'])
    def test_workflows_smoke_proves_the_runtime_booted(self, name):
        """冒烟必须有"功能"那一层：只判进程活着会被启动异常的模态框骗过（真实教训）"""
        wf = _read(os.path.join(WORKFLOWS, name))
        assert '--ocr-selftest' in wf and '--out' in wf, \
            '%s 的功能冒烟要跑自检并检查产出文件' % name
        assert re.search(r'Test-Path\s+\$selftest', wf), \
            '%s 必须断言自检**产出了文件**（这才是"冻结运行时起来了"的物证）' % name

    @pytest.mark.parametrize('name', ['release.yml', 'ci.yml'])
    def test_workflows_prove_tk_works(self, name):
        """静态闸门会被"数据装错"骗过（v1.0.0 就是），所以工作流必须真起一次 Tk"""
        wf = _read(os.path.join(WORKFLOWS, name))
        assert '--tk-selftest' in wf, '%s 要在冻结产物里跑 Tk 自检' % name
        for key in ('tk_data_ok', 'tk_ok'):
            assert key in wf, '%s 的 Tk 冒烟要断言 %s' % (name, key)

    def test_release_workflow_gate_runs_before_zip_and_release(self):
        """闸门必须在打 zip / 建 Release 之前 —— 顺序反了就等于没闸门"""
        wf = _read(os.path.join(WORKFLOWS, 'release.yml'))
        i_gate = wf.index('tcl_tk_data.py')
        i_tk = wf.index('--tk-selftest')
        i_zip = wf.index('Compress-Archive')
        i_rel = wf.index('Publish release')
        assert i_gate < i_tk < i_zip < i_rel, '两道 Tcl/Tk 闸门都要排在打 zip 之前'


# ---------------- 3b. 冻结产物里的 Tk 自检（`--tk-selftest`） ----------------

class TestTkSelfTestInTheFrozenApp:
    """静态闸门只能证明"目录里有标记文件"；这一层在**冻结产物里**真起一个 Tk。

    v1.0.0 的坏包能过静态闸门（`_tk_data` 里有 `tclIndex`），而 `tkinter.Tk()` 会当场抛错 ——
    app.pyw 里的选图片 / 选附件 / 换背景（`tkinter.filedialog`）与确认框（`messagebox`）
    会全部失效，用户看到的却是"主界面好好的"。
    """

    def test_app_exposes_the_cli_entry(self):
        src = _read(os.path.join(PROJECT_ROOT, 'app.pyw'))
        assert "'--tk-selftest' in sys.argv" in src, 'app.pyw 要有 --tk-selftest 入口'
        # 退出码必须由这两个判据决定（CI 靠它当闸门）
        assert "payload['tk_data_ok'] and payload['tk_ok']" in src

    def test_selftest_reports_a_working_tk(self, app_ns):
        """开发态（Tcl 8.6 在磁盘上）跑一次：Tk 真能起来，两个对话框子模块可用"""
        info = app_ns['tk_selftest']()
        assert info['tk_ok'] is True, info
        assert info['filedialog'] and info['messagebox'], info
        assert info['tk_patchlevel'], info

    def test_cli_writes_the_report_and_fails_without_tk_data(self, app_ns, tmp_path):
        """`--out` 要写文件（打包版是窗口模式，没有控制台可看），退出码要**由报告里的判据决定**。

        开发态仓库根下没有 `_tk_data` ⇒ 数据判据为假 ⇒ 退出码 1：这就是"缺数据 = 失败"的语义，
        CI 正是靠它当闸门。（"Tk 在开发机上真能用"由上一条测试负责，这里不重复断言 ——
        否则这条会变成"断言这台机器有没有桌面"，那是两回事。）
        """
        out = tmp_path / 'tk.json'
        code = app_ns['tk_selftest_from_argv'](['--tk-selftest', '--out', str(out)])
        assert out.is_file()
        payload = json.loads(out.read_text(encoding='utf-8'))
        assert set(payload) >= {'frozen', 'tk_data_ok', 'tk_ok', 'filedialog', 'messagebox'}
        assert payload['frozen'] is False, payload
        assert payload['tk_data_ok'] is False, '开发态仓库根下不该有 _tk_data：%s' % payload
        assert code == (0 if (payload['tk_data_ok'] and payload['tk_ok']) else 1) == 1, payload

    def test_broken_tk_data_is_reported(self, app_ns, tmp_path):
        """把 EXE_DIR 指到一个"`_tk_data` 里装的是 Tcl 库"的假产物上：必须报数据不对"""
        exe_dir = tmp_path / 'fake-dist'
        tk_data = exe_dir / '_internal' / '_tk_data'
        tk_data.mkdir(parents=True)
        (tk_data / 'init.tcl').write_text('# stub', encoding='utf-8')
        app_ns['tk_selftest'].__globals__['EXE_DIR'] = str(exe_dir)
        info = app_ns['tk_selftest']()
        assert info['tk_data_ok'] is False, info
        assert info['tk_data_markers'] == [] and 'init.tcl' in info['tk_data_top']


# ---------------- 4. 构建日志的编码（这一条是真踩出来的） ----------------

class TestBuildLoggingIsAsciiSafe:
    """构建脚本往日志里写东西**不能失败** —— 2026-09-27 连续两次构建都死在一句中文 print 上。

    经过：runner 的 Python stdout 是 cp1252（英文版 Windows Server），
    `print('[spec] ... → 兜底补上：...')` 抛 `UnicodeEncodeError` → PyInstaller 进程挂掉 →
    构建失败。**真正的逻辑（兜底收集）其实没被验证到**，排障还因为"job 日志未登录读不到"
    多花了两轮。所以：spec 的日志一律 ASCII，工作流再把 Python 输出统一成 UTF-8。
    """

    def test_spec_prints_only_ascii(self):
        import ast
        src = _read(SPEC)
        tree = ast.parse(src)
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, 'id', None) or getattr(fn, 'attr', None)
            if name not in ('print', '_say'):
                continue
            for arg in ast.walk(node):
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if any(ord(ch) > 127 for ch in arg.value):
                        bad.append(arg.value[:60])
        assert not bad, 'spec 的构建日志必须只用 ASCII（cp1252 上会崩）：%s' % bad

    def test_gate_cli_survives_cp1252_stdout(self, tmp_path):
        """用 cp1252 的 stdout 跑闸门：不许抛 UnicodeEncodeError，退出码照常"""
        import subprocess
        script = os.path.join(PROJECT_ROOT, 'build_resources', 'tcl_tk_data.py')
        env = dict(os.environ, PYTHONIOENCODING='cp1252')
        env.pop('PYTHONUTF8', None)
        # 坏包 → 期望 exit 1 且没有编码异常
        r = subprocess.run([sys.executable, script, str(tmp_path / 'nope')],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 2, r
        assert 'UnicodeEncodeError' not in r.stderr
        # 好包 → 期望 exit 0
        good = tmp_path / 'good' / '_internal'
        for name, marker in ((tkdata.TCL_ROOTNAME, 'init.tcl'), (tkdata.TK_ROOTNAME, 'tk.tcl')):
            os.makedirs(str(good / name))
            (good / name / marker).write_text('# stub', encoding='utf-8')
        r2 = subprocess.run([sys.executable, script, str(tmp_path / 'good')],
                            capture_output=True, text=True, env=env)
        assert r2.returncode == 0, r2
        assert 'UnicodeEncodeError' not in r2.stderr

    def test_diagnostics_is_pure_ascii(self):
        """诊断要穿过 cp1252 的 stdout 与 Actions 注解：非 ASCII 路径转成 \\uXXXX，信息不丢"""
        text = tkdata.diagnostics()
        assert text.isascii(), '诊断必须是纯 ASCII'

    @pytest.mark.parametrize('name', ['release.yml', 'ci.yml'])
    def test_workflows_force_utf8_python_output(self, name):
        wf = _read(os.path.join(WORKFLOWS, name))
        assert 'PYTHONIOENCODING' in wf, '%s 要把 Python 输出统一成 UTF-8' % name


class TestBuildBackupPruning:
    """`build.py` 的备份剪枝必须**按时间**留最近的那份。

    为什么单开一组：这个 bug 是**完全静默**的 —— 构建照常成功、日志照打"已备份"，
    只是那份备份转头就被自己剪掉了。而它正是构建/还原失败时的回滚凭据
    （`main()` 的 except 分支就靠 `restore(backup)`），剪错等于把后悔药丢了。

    实测踩到的形状：两套命名在同一目录里共存时**名字序与时间序相反** ——
    `_predist-backup-<新>` 永远小于 `_predist-data-backup-<旧>`（'b' < 'd'），
    于是"保留名字最大的 keep 份"会剪掉刚做的那份、留下几天前的旧备份。
    同一条教训在 `restore.py` 的 `latest_backup()` 上也踩过一次（那边是 mtime + 只认 notes- 前缀）。
    """

    def test_keeps_newest_by_mtime_not_by_name(self, tmp_path, monkeypatch):
        import build
        old = tmp_path / '_predist-data-backup-20260101-000000'   # 名字更大，但更旧
        new = tmp_path / '_predist-backup-20260102-000000'        # 名字更小，但更新
        for d in (old, new):
            d.mkdir()
        os.utime(str(old), (1_000_000, 1_000_000))
        os.utime(str(new), (2_000_000, 2_000_000))

        monkeypatch.setattr(build, 'BACKUP_ROOT', str(tmp_path))
        build.prune_backups(keep=1)

        assert new.is_dir(), '必须留下**刚做的那份**（按时间），不是名字最大的那份'
        assert not old.exists(), '按名字排序时这条会反过来 —— 就是实测踩到的 bug'

    def test_keep_n_prunes_all_older(self, tmp_path, monkeypatch):
        import build
        for i in range(4):
            d = tmp_path / ('_predist-backup-2026010%d-000000' % (i + 1))
            d.mkdir()
            os.utime(str(d), (1_000_000 + i * 1000, 1_000_000 + i * 1000))
        monkeypatch.setattr(build, 'BACKUP_ROOT', str(tmp_path))
        build.prune_backups(keep=2)
        left = sorted(p.name for p in tmp_path.iterdir())
        assert len(left) == 2, 'keep=2 应该只留两份：%r' % left
        assert left[-1].endswith('20260104-000000'), '留下的必须是时间上最近的两份：%r' % left

    def test_non_directory_and_foreign_names_are_ignored(self, tmp_path, monkeypatch):
        """只认 `_predist*` 目录：同目录下的文件、以及 dist 里别的东西不能被误删。"""
        import build
        keep = tmp_path / '_predist-backup-20260105-000000'
        keep.mkdir()
        (tmp_path / '_predist-backup-20260105-000000.zip').write_text('x', encoding='utf-8')
        other = tmp_path / 'MyNotepad'
        other.mkdir()
        monkeypatch.setattr(build, 'BACKUP_ROOT', str(tmp_path))
        build.prune_backups(keep=1)
        assert keep.is_dir()
        assert (tmp_path / '_predist-backup-20260105-000000.zip').exists(), '文件不该被当成备份目录'
        assert other.is_dir(), '非 _predist* 的目录不能被碰'


class TestVersionConsistency:
    """版本号只有一个真相源（`build_resources/version_info.txt`），三处必须互相一致。

    为什么单开一组：CONTRIBUTING.md 的发版清单写着"tag 名必须与它一致"，但**一段时间里
    这条只在文档里**——`tests/` 里零命中、`release.yml` 也不读 `ProductVersion`，
    于是"改了代码忘了改版本号""tag 打成 v1.1.0 而文件还是 1.0.1.0"这类偏差
    **CI 会全绿放行**，现象仅仅是"exe 属性里显示的版本号和 Release 名对不上"。
    正是这个项目最怕的那类静默偏差，所以补成机器检查。

    三条各自挡一种错法：
    1. **文件内部四处不一致** ⇒ 属性里显示的版本号与实际不符（清单里"四处都要改"的由来）；
    2. **CHANGELOG 的「未发布」没坐实** ⇒ 发了版而变更记录还挂在"未发布"下；
    3. **HEAD 被 tag 时 tag 与版本号不一致** ⇒ Release 名与 exe 属性对不上（只在打 tag 的
       检出上生效，日常跑 pytest 会自动跳过）。
    """

    @staticmethod
    def _version_info():
        """从 version_info.txt 里取出四处版本号。

        刻意用正则读文本而不是 import 它：那个文件是 PyInstaller 的 `VSVersionInfo(...)`
        字面量，不是可 import 的模块（里面 `0x3f` 这类写法在 Python 里能跑，但语义是
        PyInstaller 自己解析的）。读文本才能钉住"文件里真的这么写"。
        """
        text = _read(VERSION_INFO)
        filevers = re.search(r'filevers=\(([^)]*)\)', text)
        prodvers = re.search(r'prodvers=\(([^)]*)\)', text)
        fv = re.search(r"StringStruct\('FileVersion',\s*'([^']*)'\)", text)
        pv = re.search(r"StringStruct\('ProductVersion',\s*'([^']*)'\)", text)
        assert filevers and prodvers and fv and pv, \
            'version_info.txt 四处版本号都要在（少一处 = 属性里显示旧版本号）'
        return filevers.group(1), prodvers.group(1), fv.group(1), pv.group(1)

    def test_four_declarations_agree_and_are_well_formed(self):
        filevers, prodvers, fv, pv = self._version_info()
        nums = [int(x.strip()) for x in filevers.split(',')]
        assert len(nums) == 4, 'filevers 必须是四元组：%r' % filevers
        dotted = '.'.join(str(n) for n in nums)
        assert fv == dotted, 'FileVersion(%r) 与 filevers(%r) 不一致' % (fv, dotted)
        assert pv == fv, 'ProductVersion(%r) 与 FileVersion(%r) 不一致' % (pv, fv)
        assert prodvers.replace(' ', '') == filevers.replace(' ', ''), \
            'prodvers(%r) 与 filevers(%r) 不一致' % (prodvers, filevers)

    def test_changelog_has_a_section_for_this_version(self):
        """CHANGELOG 顶部必须是「未发布」，紧跟着就是当前版本号那一节。

        顺序反了就说明：改了版本号但没把「未发布」坐实成正式版本（发出去的 Release
        说明里会缺这一版的变更），或者坐实了却忘了在顶上开新的「未发布」空段。
        """
        text = _read(CHANGELOG)
        heads = [h.strip() for h in re.findall(r'^## .*$', text, re.M)]
        assert heads[0] == '## 未发布', \
            'CHANGELOG 顶部应当是「## 未发布」空段，实际是 %r' % heads[0]
        _, _, _, pv = self._version_info()
        want = 'v' + pv.rsplit('.', 1)[0]          # '1.0.1.0' -> 'v1.0.1'
        assert heads[1].startswith('## %s' % want), \
            '「未发布」下面应当是 %r 那一节（版本号 %s），实际是 %r' % (want, pv, heads[1])

    def test_head_tag_matches_version_when_tagged(self):
        """打了 tag 的检出上：tag 名必须等于 `v` + 前三位版本号。

        只在 HEAD 恰好被 tag 指着时生效（`release.yml` 就是这种检出），
        日常开发检出上没有 tag，直接跳过 —— 所以它不会让普通 pytest 变脆。
        """
        import subprocess
        try:
            out = subprocess.run(['git', 'tag', '--points-at', 'HEAD'],
                                 cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pytest.skip('取不到 git 信息（打包分发或没有 git）')
        if out.returncode != 0:
            pytest.skip('不是 git 检出，跳过 tag 校验')
        tags = [t for t in out.stdout.split() if t.startswith('v')]
        if not tags:
            pytest.skip('HEAD 上没有 tag（日常开发检出），跳过')
        _, _, _, pv = self._version_info()
        want = 'v' + pv.rsplit('.', 1)[0]
        assert want in tags, \
            'HEAD 上的 tag 是 %r，但 version_info.txt 是 %s ⇒ Release 名与 exe 属性会对不上' \
            % (tags, pv)
