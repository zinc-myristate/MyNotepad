# -*- coding: utf-8 -*-
"""前端 ES 模块图的静态检查（无需浏览器，属于默认单测）。

为什么需要：改成 ES 模块后有两类错误**不会**在加载时报错，只会静默地少功能或到运行时才炸：
  1. 孤儿模块——新写的模块没被任何模块 import，代码永不执行（改造前由 index.html 的
     FILES 数组保证执行，现在靠 import 图，必须自动检查）；
  2. 漏 import——某个文件用了别的模块导出的名字却没 import，浏览器只会在**运行时**
     抛 ReferenceError（浏览器不做「自由变量」检查）。

本文件用纯正则 + 手写状态机（剥离注释与字符串，保留模板字面量 ${} 里的代码）做两项检查：
  · 链接检查：每个 import 的名字必须真的被目标文件 export；
  · 引用检查：任何「别的模块导出了的名字」在本文件里被用到时，必须已 import 或本地声明。
另外锁死 index.html 的加载方式：应用模块不再以经典 <script src> 引入，且入口指向 00-main.js。
"""
import os
import re

import pytest
from conftest import PROJECT_ROOT

JS_DIR = os.path.join(PROJECT_ROOT, 'renderer', 'js')
ENTRY = 'app/00-main.js'
INDEX = os.path.join(PROJECT_ROOT, 'renderer', 'index.html')


# ---------- 工具：剥离注释与字符串（模板字面量的 ${} 内部视为代码） ----------

def strip_code(src, blank_strings=True):
    """剥离注释；blank_strings=True 时连字符串内容一起抹掉（保留模板字面量 ${} 内的代码）。

    为什么需要两种模式：
      · 解析 import/export 时必须**保留**字符串内容，否则 `from './x.js'` 的模块路径会被抹掉；
      · 检查跨模块引用时必须**抹掉**字符串内容，否则注释/文案里的同名词会被误判为引用。
    两种模式都保证位置一一对应（同长度替换），以便报错时给出正确行号。
    """
    out = []
    i, n = 0, len(src)
    mode = 'code'          # code | line | block | sq | dq | tpl
    stack = []
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ''
        if mode == 'line':
            if c == '\n':
                mode = 'code'; out.append('\n')
            else:
                out.append(' ')
            i += 1
            continue
        if mode == 'block':
            if c == '*' and nxt == '/':
                mode = 'code'; out.append('  '); i += 2; continue
            out.append('\n' if c == '\n' else ' '); i += 1; continue
        if mode in ('sq', 'dq'):
            if c == '\\':
                out.append('  ' if blank_strings else src[i:i + 2]); i += 2; continue
            if (mode == 'sq' and c == "'") or (mode == 'dq' and c == '"'):
                mode = 'code'; out.append(c if not blank_strings else ' '); i += 1; continue
            out.append(' ' if blank_strings else c); i += 1; continue
        if mode == 'tpl':
            if c == '\\':
                out.append('  ' if blank_strings else src[i:i + 2]); i += 2; continue
            if c == '`':
                if stack and stack[-1] == 'tpl':
                    stack.pop()
                out.append(' ' if blank_strings else c); i += 1; mode = 'code'; continue
            if c == '$' and nxt == '{':
                stack.append('interp'); mode = 'code'; out.append('  '); i += 2; continue
            out.append(' ' if blank_strings else c); i += 1; continue
        # mode == 'code'
        if c == '/' and nxt == '/':
            mode = 'line'; out.append('  '); i += 2; continue
        if c == '/' and nxt == '*':
            mode = 'block'; out.append('  '); i += 2; continue
        if c in "'\"`":
            out.append(' ' if blank_strings else c)
            mode = {'\'': 'sq', '"': 'dq', '`': 'tpl'}[c]
            if c == '`':
                stack.append('tpl')
            i += 1
            continue
        if c == '{':
            stack.append('brace'); out.append(c); i += 1; continue
        if c == '}':
            if stack and stack[-1] == 'brace':
                stack.pop()
            elif stack and stack[-1] == 'interp':
                stack.pop(); mode = 'tpl'
            out.append(c); i += 1; continue
        out.append(c); i += 1
    return ''.join(out)


def strip_comments_and_strings(src):
    return strip_code(src, blank_strings=True)


# ---------- 工具：JS 模块的 import / export 抽取 ----------

IDENT = r'[A-Za-z_$][\w$]*'
IMPORT_RE = re.compile(
    r"""import\s*(?:\{([^}]*)\}\s*from\s*)?['"]([^'"]+)['"]""")
EXPORT_FN_RE = re.compile(r'export\s+(?:async\s+)?function\s+(' + IDENT + r')')
EXPORT_CLASS_RE = re.compile(r'export\s+class\s+(' + IDENT + r')')
EXPORT_VAR_RE = re.compile(r'export\s+(?:const|let|var)\s+')
EXPORT_LIST_RE = re.compile(r'export\s*\{([^}]*)\}')
DECL_FN_RE = re.compile(r'(?:^|\s)(?:async\s+)?function\s+(' + IDENT + r')')
DECL_CLASS_RE = re.compile(r'(?:^|\s)class\s+(' + IDENT + r')')
DECL_VAR_RE = re.compile(r'(?:^|\s)(?:const|let|var)\s+')
PARAM_RE = re.compile(r'\(([^)]*)\)\s*(?:=>|\{)')
IDENT_RE = re.compile(IDENT)


def _statement_end(src, start):
    """从 start 起找到顶层 ; 的位置（跳过字符串/注释/括号嵌套）"""
    stripped = strip_comments_and_strings(src)
    depth = 0
    i = start
    while i < len(stripped):
        c = stripped[i]
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        elif c == ';' and depth == 0:
            return i
        elif c == '\n' and depth == 0:
            return i
        i += 1
    return len(src)


def _declared_names_in(decl_text):
    """从 `a = 1, b = 2` 或 `{x, y} = z` 之类的声明段里取被声明的名字"""
    names = []
    depth = 0
    cur = []
    parts = []
    for ch in decl_text:
        if ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth -= 1
        if ch == ',' and depth == 0:
            parts.append(''.join(cur)); cur = []
        else:
            cur.append(ch)
    parts.append(''.join(cur))
    for part in parts:
        head = part.split('=')[0]
        names.extend(IDENT_RE.findall(head))
    return names


def parse_module(src):
    """返回 (imports, exports, declared) —— imports: [(imported_name, local_name, source)]"""
    clean = strip_code(src, blank_strings=False)   # 必须保留字符串：模块路径在引号里
    imports, exports, declared = [], set(), set()

    for m in IMPORT_RE.finditer(clean):
        names, source = m.group(1), m.group(2)
        if names is None:
            imports.append((None, None, source))          # 副作用导入
            continue
        for part in names.split(','):
            part = part.strip()
            if not part:
                continue
            if ' as ' in part:
                imported, local = [p.strip() for p in part.split(' as ')]
            else:
                imported = local = part
            imports.append((imported, local, source))
            declared.add(local)

    for m in EXPORT_FN_RE.finditer(clean):
        exports.add(m.group(1))
    for m in EXPORT_CLASS_RE.finditer(clean):
        exports.add(m.group(1))
    for m in EXPORT_VAR_RE.finditer(clean):
        end = _statement_end(src, m.end())
        decl_text = strip_comments_and_strings(src[m.end():end])
        for name in _declared_names_in(decl_text):
            exports.add(name)
    for m in EXPORT_LIST_RE.finditer(clean):
        for part in m.group(1).split(','):
            part = part.strip()
            if part:
                exports.add(part.split(' as ')[-1].strip())

    # 本文件所有声明位置（含函数参数与局部变量），用于排除「本地同名」的干扰
    for m in DECL_FN_RE.finditer(clean):
        declared.add(m.group(1))
    for m in DECL_CLASS_RE.finditer(clean):
        declared.add(m.group(1))
    for m in DECL_VAR_RE.finditer(clean):
        end = _statement_end(src, m.end())
        for name in _declared_names_in(strip_comments_and_strings(src[m.end():end])):
            declared.add(name)
    for m in PARAM_RE.finditer(clean):
        for name in IDENT_RE.findall(m.group(1)):
            declared.add(name)

    return imports, exports, declared


def all_modules():
    mods = {}
    for root, _dirs, files in os.walk(JS_DIR):
        for f in files:
            if f.endswith('.js'):
                path = os.path.join(root, f)
                rel = os.path.relpath(path, JS_DIR).replace(os.sep, '/')
                with open(path, encoding='utf-8') as fh:
                    src = fh.read()
                imports, exports, declared = parse_module(src)
                mods[rel] = {'src': src, 'imports': imports,
                             'exports': exports, 'declared': declared}
    return mods


MODULES = all_modules()


def _resolve(rel, source):
    base = os.path.dirname(rel)
    return os.path.normpath(os.path.join(base, source)).replace(os.sep, '/')


# ---------- 检查 ----------

def test_entry_exists_and_index_loads_it():
    assert ENTRY in MODULES, '入口模块 %s 不存在' % ENTRY
    with open(INDEX, encoding='utf-8') as f:
        html = f.read()
    assert "'./js/app/00-main.js'" in html, 'index.html 的加载器应 import 入口模块'
    # 应用模块不应再以经典脚本方式引入（第三方 UMD 除外）
    classic = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)
    assert classic == [], 'index.html 不应再有静态 <script src>：%r（模块应走 import 图）' % (classic,)


def test_every_import_resolves_to_existing_module():
    bad = []
    for rel, m in MODULES.items():
        for _imported, _local, source in m['imports']:
            target = _resolve(rel, source)
            if target not in MODULES:
                bad.append('%s -> %s（解析为 %s）' % (rel, source, target))
    assert not bad, '存在无法解析的 import：\n' + '\n'.join(bad)


def test_every_imported_name_is_exported_by_target():
    bad = []
    for rel, m in MODULES.items():
        for imported, _local, source in m['imports']:
            if imported is None:
                continue
            target = _resolve(rel, source)
            if target in MODULES and imported not in MODULES[target]['exports']:
                bad.append('%s import { %s } from %s —— 目标未导出' % (rel, imported, source))
    assert not bad, '链接检查失败（浏览器会直接语法错误）：\n' + '\n'.join(bad)


def test_no_orphan_modules():
    """所有模块都必须能从入口沿 import 图到达，否则代码永不执行"""
    seen = set()

    def visit(rel):
        if rel in seen:
            return
        seen.add(rel)
        for _imported, _local, source in MODULES[rel]['imports']:
            target = _resolve(rel, source)
            if target in MODULES:
                visit(target)

    visit(ENTRY)
    orphans = sorted(set(MODULES) - seen)
    assert not orphans, '存在孤儿模块（没有被任何模块 import，代码不会执行）：%r' % (orphans,)


def test_references_to_other_modules_exports_are_imported():
    """别的模块导出的名字在本文件被使用时，必须已 import（浏览器不做自由变量检查）"""
    exported_by_others = {}
    for rel, m in MODULES.items():
        for name in m['exports']:
            exported_by_others.setdefault(name, []).append(rel)

    problems = []
    for rel, m in MODULES.items():
        clean = strip_comments_and_strings(m['src'])
        imported_locals = {local for _i, local, _s in m['imports'] if local}
        for match in IDENT_RE.finditer(clean):
            name = match.group(0)
            owners = [o for o in exported_by_others.get(name, []) if o != rel]
            if not owners:
                continue
            if name in m['declared'] or name in imported_locals:
                continue
            # 排除属性访问 obj.name 与对象字面量键 name:
            before = clean[:match.start()].rstrip()
            after = clean[match.end():].lstrip()
            if before.endswith('.'):
                continue
            if after.startswith(':'):
                continue
            line = clean[:match.start()].count('\n') + 1
            problems.append('%s:%d 用到 %s（由 %s 导出）但没有 import'
                            % (rel, line, name, ', '.join(owners)))
    assert not problems, '存在未 import 的跨模块引用：\n' + '\n'.join(sorted(set(problems)))


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-v']))
