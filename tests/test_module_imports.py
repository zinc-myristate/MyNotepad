"""模块级「包.子模块」导入的静态 + 语义守卫（无需浏览器、无需 GUI）。

为什么需要这个测试：`import tkinter.messagebox` **不会**把 `filedialog` 挂到 `tkinter` 包上，
而代码里是用**属性访问**写的（`tkinter.filedialog.askopenfilename(...)`）——名字 `filedialog`
从没被直接引用，所以「未使用导入」类的检查会建议删掉 `import tkinter.filedialog`。

156c20f 那一轮就是这样把它删掉的，后果：插入图片 / 插入附件 / 更换图标 / 选择背景
四个功能全部**静默失效**（用户只看到"点了没反应"，异常只落在 error.log 里：
`AttributeError: module 'tkinter' has no attribute 'filedialog'`），而不写局部 import 的
导出功能之所以没事，只是因为那几个方法各自在函数里补了 `import tkinter.filedialog`。

覆盖范围：仓库内所有 .py/.pyw，检查 `tkinter.<子模块>` 属性访问是否有对应导入
（模块级导入，或同一函数内的局部导入）。
"""
import ast
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOTS = ('tkinter',)                      # 目前只有 tkinter 有这种"子模块必须显式导入"的语义
SKIP_DIRS = {'dist', 'build', '.git', '__pycache__', 'node_modules', '.pytest_cache'}
SUBMODULE_RE = re.compile(r'^(?:%s)\.([A-Za-z_]\w*)$' % '|'.join(ROOTS))


def py_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS and not d.startswith(('pytmp', '_predist'))]
        for name in sorted(files):
            if name.endswith(('.py', '.pyw')):
                yield os.path.join(base, name)


def imported_names(node):
    """一个语句节点里 `import a.b` 形式的完整名（不含 from ... import）"""
    out = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            out.add(alias.name)
    return out


def module_level_imports(tree):
    out = set()
    for node in tree.body:
        out |= imported_names(node)
    return out


def _enclosing_usage(tree):
    """产出 (使用的 "tkinter.子模块" 名, 所在函数节点或 None)"""
    found = []

    def walk(node, func):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child)                      # 进入新函数，作用域收窄
                continue
            if (isinstance(child, ast.Attribute)
                    and isinstance(child.value, ast.Name)
                    and child.value.id in ROOTS):
                found.append(('%s.%s' % (child.value.id, child.attr), func))
            walk(child, func)

    walk(tree, None)
    return found


def function_imports(func):
    out = set()
    for node in ast.walk(func):
        out |= imported_names(node)
    return out


def scan(path):
    """返回该文件里"用了子模块但没导入"的清单 [(子模块全名, 所在函数或 None)]"""
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read(), filename=path)
    mod_imports = module_level_imports(tree)
    local = {}
    bad = []
    for name, func in _enclosing_usage(tree):
        if not SUBMODULE_RE.match(name):
            continue                                # tkinter.Tk 这类不是子模块，跳过
        if name in mod_imports:
            continue
        if func is not None:
            if id(func) not in local:
                local[id(func)] = function_imports(func)
            if name in local[id(func)]:
                continue
        bad.append((name, func))
    return bad


def test_submodule_imports_present():
    offenders = []
    for path in py_files():
        for name, func in scan(path):
            where = ('%s()' % func.name) if func is not None else '模块级'
            offenders.append('%s: %s（%s）' % (os.path.relpath(path, ROOT), name, where))
    assert not offenders, (
        '以下位置用了 `包.子模块` 属性访问却没有导入该子模块（子模块必须显式导入，'
        '否则运行期 AttributeError）：\n  ' + '\n  '.join(offenders))


def test_app_py_import_lines_make_dialogs_available():
    """在**全新解释器**里只执行 app.pyw 顶部那几行 import，再检查子模块属性可访问。

    必须新开进程：本测试进程里一旦 import 过 filedialog，`hasattr(tkinter, 'filedialog')`
    就恒为真，检查会失去意义——这正是这个 bug 能溜过其它测试的原因（只有真的点按钮、
    在"从没导入过 filedialog"的进程里才会炸）。
    """
    src = open(os.path.join(ROOT, 'app.pyw'), encoding='utf-8').read()
    lines = [ln for ln in src.splitlines()
             if ln.startswith(('import tkinter', 'from tkinter'))]
    assert lines, 'app.pyw 顶部应当有 tkinter 相关导入'
    code = '\n'.join(lines) + """
import tkinter
assert callable(tkinter.filedialog.askopenfilename), 'filedialog.askopenfilename 不可用'
assert callable(tkinter.filedialog.asksaveasfilename), 'filedialog.asksaveasfilename 不可用'
assert callable(tkinter.messagebox.askyesno), 'messagebox.askyesno 不可用'
"""
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT)
    assert done.returncode == 0, (
        'app.pyw 顶部那几行 import 不足以让文件对话框可用 —— '
        '插入图片/附件、更换图标、选择背景会全部失效')


def test_dialog_helpers_use_filedialog():
    """这四项功能依赖 filedialog：插入图片/附件/背景/更换图标。"""
    src = open(os.path.join(ROOT, 'app.pyw'), encoding='utf-8').read()
    for helper in ('pick_image_file', 'pick_attachment_file', 'pick_background_file',
                   'pick_and_preview_icon'):
        assert helper in src, 'app.pyw 里应当还有 %s' % helper
    assert src.count('tkinter.filedialog.') >= 4, '文件对话框调用点变少了，请核对'
