# -*- coding: utf-8 -*-
"""CI 把 E2E 拆成几个并行任务（`E2E_SHARD=k/n`，分法在 conftest.py）。

拆错了不会报错，只会**静默少跑测试**：哪一份没人跑、或者两份分到的用例对不上，CI 照样全绿。
所以这里钉住两件事：分法本身（不重不漏、份数平均），以及 ci.yml 的 matrix 和分母对得上。
"""
import os
import re

import pytest
from conftest import PROJECT_ROOT, parse_e2e_shard, split_e2e_shard

CI_YML = os.path.join(PROJECT_ROOT, '.github', 'workflows', 'ci.yml')


class FakeItem:
    def __init__(self, name, e2e):
        self.name = name
        self._e2e = e2e

    def get_closest_marker(self, name):
        return object() if name == 'e2e' and self._e2e else None

    def __repr__(self):
        return self.name


@pytest.mark.parametrize('spec, expected', [
    (None, None), ('', None), ('  ', None), ('1/3', (1, 3)), ('3/3', (3, 3)), (' 2/4 ', (2, 4)), ('1/1', (1, 1)),
])
def test_parse_accepts_k_of_n(spec, expected):
    assert parse_e2e_shard(spec) == expected


@pytest.mark.parametrize('spec', ['0/3', '4/3', '1-3', '3', '/3', '1/', 'a/b', '-1/3', '1/0', '1/3/5'])
def test_parse_rejects_anything_else(spec):
    """写错了要当场报错（pytest 退出码 4），不能当成"不拆"把全部用例跑一遍、或者一个都不跑"""
    with pytest.raises(pytest.UsageError):
        parse_e2e_shard(spec)


def test_split_covers_every_e2e_test_exactly_once_and_keeps_unit_tests():
    items = [FakeItem('e%d' % i, True) for i in range(10)]
    items[2:2] = [FakeItem('u1', False)]
    items.append(FakeItem('u2', False))
    e2e = [it for it in items if it._e2e]
    shards = [split_e2e_shard(items, (k, 3)) for k in (1, 2, 3)]

    for keep, drop in shards:
        assert sorted(keep + drop, key=repr) == sorted(items, key=repr), '每份都只是把用例分成留下和去掉'
        assert [it.name for it in keep if not it._e2e] == ['u1', 'u2'], '不是 E2E 的用例一律留下'
    picked = [it for keep, _drop in shards for it in keep if it._e2e]
    assert sorted(picked, key=repr) == sorted(e2e, key=repr), '三份合起来正好是全部 E2E，不重不漏'
    sizes = [sum(it._e2e for it in keep) for keep, _drop in shards]
    assert max(sizes) - min(sizes) <= 1, '份数要平均：%r' % sizes
    assert [it.name for it in shards[0][0] if it._e2e] == ['e0', 'e3', 'e6', 'e9'], '按收集顺序轮流分'


def test_ci_matrix_runs_every_shard():
    """matrix 里的份数和 E2E_SHARD 的分母必须一致：分母大了，多出来的几份没人跑，那部分测试静默不跑"""
    text = open(CI_YML, encoding='utf-8').read()
    shard_env = re.findall(r'E2E_SHARD:\s*\$\{\{\s*matrix\.shard\s*\}\}/(\d+)', text)
    assert len(shard_env) == 1, 'ci.yml 里应当正好有一处 E2E_SHARD: ${{ matrix.shard }}/n'
    matrix = re.search(r'shard:\s*\[([^\]]*)\]', text)
    assert matrix, 'e2e 任务要有 matrix.shard 列表'
    values = [int(v) for v in re.findall(r'\d+', matrix.group(1))]
    total = int(shard_env[0])
    assert values == list(range(1, total + 1)), \
        'matrix.shard=%r 和分母 %d 对不上（要正好是 1..%d）' % (values, total, total)
    assert 'fail-fast: false' in text, '一份红了别把另外几份取消：要拿到全部失败的用例'
