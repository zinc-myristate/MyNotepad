# -*- coding: utf-8 -*-
"""纯前端工具函数的单测。

为什么这些能单测而别的 UI 逻辑不能：它们是**纯函数**（不碰 DOM、不依赖浏览器 API），
所以塞进无头 WebView2 里跑一遍是浪费——直接用 Python 驱动的极简 JS 运行时跑更快更稳。
没有 node 时自动跳过（CI 上有 node，本地开发机通常也有）。
"""
import json
import os
import pathlib
import shutil
import subprocess

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UTILS = os.path.join(PROJECT_ROOT, 'renderer', 'js', 'shared', 'utils.js')

NODE = shutil.which('node')


def _run_js(body):
    """把 utils.js 当 ESM 载入并执行一段断言代码。

    body 里调 `check(name, cond)` 收集结果；可以是异步的（`await new Promise(...)`），
    最后只打印**一次** JSON —— 不做"提前 console.log 抢先输出"那套，否则尾部断言会丢。
    """
    if not NODE:
        pytest.skip('没找到 node，跳过纯 JS 工具函数的单测')
    # 只导入**纯函数**：`escapeHtml` 走 document.createElement，在 node 里没有 DOM，
    # 那一类要用无头浏览器测（本项目已有 e2e 体系），不塞进这个纯函数单测文件。
    module_url = pathlib.Path(UTILS).as_uri()
    script = """
import { throttle, debounce, slugify, splitFrontMatter, serializeFrontMatter,
         parsePropsBlock } from %s;
const out = {};
const check = (name, cond) => { out[name] = !!cond; };
await (async () => {
%s
})();
console.log('@@RESULT@@' + JSON.stringify(out));
""" % (json.dumps(module_url), body)
    proc = subprocess.run([NODE, '--input-type=module', '-e', script],
                          capture_output=True, text=True, timeout=60,
                          encoding='utf-8', errors='replace')
    if proc.returncode != 0:
        pytest.fail('node 执行失败：%s\n%s' % (proc.stdout, proc.stderr))
    for line in proc.stdout.splitlines():
        if line.startswith('@@RESULT@@'):
            return json.loads(line[len('@@RESULT@@'):])
    pytest.fail('没有拿到结果标记，node 输出：\n%s' % proc.stdout)


def _wait(ms):
    return 'await new Promise(r => setTimeout(r, %d));' % ms


class TestThrottle:
    """`throttle`：首个调用立即执行、期间最多一次、**停手后一定补一次尾部调用**。

    尾部补偿是必须的：只有 leading 的节流会让"最后一次输入"永远不反映到界面上
    （用户敲完最后两个字，字数停在之前的值）。编辑器侧栏的合并刷新就靠它。
    """

    def test_leading_edge_fires_immediately(self):
        r = _run_js("""
let n = 0;
const t = throttle(() => { n++; }, 1000);
t();
check('first_call_runs_synchronously', n === 1);
t(); t(); t();
check('calls_during_window_are_merged', n === 1);
t.cancel();
""")
        assert r['first_call_runs_synchronously'], '首个调用必须立即执行（界面不能迟钝）'
        assert r['calls_during_window_are_merged'], '窗口内的连击要被合并'

    def test_trailing_edge_fires_after_quiet(self):
        r = _run_js("""
let n = 0;
const t = throttle(() => { n++; }, 60);
t();                      // leading
t(); t();                 // 合并成一次 trailing
check('before_quiet', n === 1);
%s
check('trailing_fired', n === 2);
check('no_extra_calls', n === 2);
""" % _wait(250))
        assert r['before_quiet'], '窗口内的连击不该立即执行'
        assert r['trailing_fired'], '停手后必须补一次（否则界面停在中间值）'
        assert r['no_extra_calls'], '不该多补'

    def test_cancel_drops_the_pending_trailing_call(self):
        r = _run_js("""
let n = 0;
const t = throttle(() => { n++; }, 60);
t();
t.cancel();
%s
check('cancel_dropped_trailing', n === 1);
""" % _wait(200))
        assert r['cancel_dropped_trailing'], 'cancel() 之后不该再触发'


class TestDebounceStillWorks:
    def test_only_the_last_call_in_a_burst_runs(self):
        r = _run_js("""
let n = 0;
const d = debounce(() => { n++; }, 40);
d(); d(); d();
%s
check('only_last_ran', n === 1);
""" % _wait(200))
        assert r['only_last_ran']


class TestPureHelpers:
    """顺手把几个纯函数钉一下（它们的口径在别处被后端镜像，容易分叉）。"""

    def test_slug_matches_backend_conventions(self):
        r = _run_js("""
check('spaces_to_dash', slugify('Hello World') === 'hello-world');
check('chinese_kept', slugify('第一章 概述') === '第一章-概述');
check('punct_dropped', slugify('a.b,c') === 'abc');
check('collapses_dashes', slugify('a -- b') === 'a-b');
""")
        assert all(r.values()), r

    def test_front_matter_round_trip(self):
        r = _run_js("""
const text = '---\\n标题: 测试\\n标签:\\n  - 甲\\n  - 乙\\n---\\n\\n正文\\n';
const { props, body, block } = splitFrontMatter(text);
check('props_parsed', props['标题'] === '测试' && props['标签'].join(',') === '甲,乙');
check('body_excludes_block', body === '\\n正文\\n');
check('block_kept', block.startsWith('---'));
const out = serializeFrontMatter(props);
const again = splitFrontMatter(out + '正文\\n');
check('round_trip_props', JSON.stringify(again.props) === JSON.stringify(props));
check('no_front_matter', splitFrontMatter('纯正文').block === '');
check('empty_props_removes_block', serializeFrontMatter({}) === '');
""")
        assert all(r.values()), r

    def test_parse_props_block_handles_inline_and_block_lists(self):
        r = _run_js("""
const a = parsePropsBlock('键: [甲, 乙]\\n单: 值');
check('inline_list', Array.isArray(a['键']) && a['键'].length === 2);
check('scalar', a['单'] === '值');
const b = parsePropsBlock('键:\\n  - 甲\\n  - 乙');
check('block_list', Array.isArray(b['键']) && b['键'].join(',') === '甲,乙');
const c = parsePropsBlock('空键:');
check('empty_key_is_empty_string', c['空键'] === '');
const d = parsePropsBlock('# 注释行\\n键: 值');
check('comment_skipped', Object.keys(d).length === 1);
""")
        assert all(r.values()), r
