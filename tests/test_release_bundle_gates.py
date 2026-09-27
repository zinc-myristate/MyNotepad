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


class TestMarkersAreTolerant:
    """判定"这是 Tcl/Tk 库目录"不能只认 init.tcl / tk.tcl。

    runner 上的 Python 3.14 用的是 **Tcl 9**，库目录布局与 8.6 有出入；只认单个文件名
    会让兜底在那台机器上又找不到数据（第一次修复就是那样没修好）。所以再认两个旁证。
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

    def test_tcl_dir_with_only_tclindex(self, tmp_path):
        root = tmp_path / 'tcl'
        self._dir(root, 'tcl9.0', 'tclIndex')
        self._dir(root, 'tk9.0', 'tk.tcl')
        tcl_dir, tk_dir = tkdata.find_data_dirs([str(root)])
        assert os.path.basename(tcl_dir) == 'tcl9.0' and tk_dir

    def test_tcl_dir_with_only_encoding_table(self, tmp_path):
        root = tmp_path / 'tcl'
        self._dir(root, 'tcl9.0', os.path.join('encoding', 'ascii.enc'))
        assert tkdata.find_data_dirs([str(root)])[0]

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

    def test_falls_back_to_library_dir_name_when_no_marker(self, tmp_path):
        """标记文件认不出来时，按 Tcl 9 的目录名兜底（`tcl_library` / `tk_library`）。

        这条线索来自 runner 的公开诊断：`info library = "//zipfs:/lib/tcl/tcl_library"`。
        """
        tcl_root = tmp_path / 'py' / 'tcl'
        self._zip(tcl_root / 'libtcl9.0.4.zip',
                  ['lib/tcl/tcl_library/some-unknown-init.tclx',
                   'lib/tk/tk_library/some-unknown.tclx'])
        pairs = tkdata.collect(str(tmp_path / 'py'), env={}, workdir=str(tmp_path / 'work'))
        assert {dest for _src, dest in pairs} == {tkdata.TCL_ROOTNAME, tkdata.TK_ROOTNAME}

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
