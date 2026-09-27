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
import os
import re
import sys

import pytest
from conftest import PROJECT_ROOT

sys.path.insert(0, os.path.join(PROJECT_ROOT, 'build_resources'))
import tcl_tk_data as tkdata  # noqa: E402

SPEC = os.path.join(PROJECT_ROOT, 'MyNotepad.spec')
WORKFLOWS = os.path.join(PROJECT_ROOT, '.github', 'workflows')


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

    def test_release_workflow_gate_runs_before_zip_and_release(self):
        """闸门必须在打 zip / 建 Release 之前 —— 顺序反了就等于没闸门"""
        wf = _read(os.path.join(WORKFLOWS, 'release.yml'))
        i_gate = wf.index('tcl_tk_data.py')
        i_zip = wf.index('Compress-Archive')
        i_rel = wf.index('Publish release')
        assert i_gate < i_zip < i_rel, '闸门要排在打 zip 之前'
