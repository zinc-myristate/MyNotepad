# -*- coding: utf-8 -*-
"""conftest.wait_for_js：等具体值超时要算失败。

以前超时返回最后取到的值：等 'flex' 没等到，拿回来的 'none' 也是真值，
`assert wait_for_js(window, "….style.display", 'flex')` 照样通过，老测试里有上百处这么写。
现在给了 expected 却超时，返回的是假值 `WaitTimeout`；`==` 按最后取到的值比，
所以 `assert wait_for_js(…)`、`wait_for_js(…) == 值`、先存进结果字典再比，三种写法都对。
这里用一个假窗口按顺序吐值，不开浏览器。
"""
import pytest
from conftest import WaitTimeout, wait_for_js

PANEL = "document.getElementById('theme-panel').style.display"


class FakeWindow:
    """按顺序吐出给定的值，吐到最后一个就一直停在那里（像一个再也不变的页面）"""

    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def evaluate_js(self, expression):
        self.calls += 1
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


def test_returns_the_value_itself_once_it_matches():
    window = FakeWindow('none', 'none', 'flex')
    value = wait_for_js(window, PANEL, 'flex', timeout=5, interval=0)
    assert value == 'flex' and type(value) is str
    assert window.calls == 3


def test_assert_on_a_timed_out_wait_fails():
    """这就是以前假通过的写法：等 'flex'，页面一直是 'none'"""
    window = FakeWindow('none')
    with pytest.raises(AssertionError):
        assert wait_for_js(window, PANEL, 'flex', timeout=0, interval=0)


def test_timed_out_value_compares_as_the_last_value_seen():
    value = wait_for_js(FakeWindow('none'), PANEL, 'flex', timeout=0, interval=0)
    assert isinstance(value, WaitTimeout)
    assert not value
    assert value == 'none' and 'none' == value
    assert value != 'flex' and 'flex' != value
    # 先存进结果字典、最后整个比的写法（E2E 里最常见）
    assert {'display': value} == {'display': 'none'}
    assert {'display': value} != {'display': 'flex'}
    assert value is not True


def test_timed_out_value_says_what_was_awaited_and_what_was_seen():
    value = wait_for_js(FakeWindow('none'), PANEL, 'flex', timeout=0, interval=0)
    text = repr(value)
    assert "'flex'" in text and "'none'" in text and 'theme-panel' in text and '超时' in text
    # 字典里的值也走 repr：`assert result == {…}, result` 的失败信息里看得到
    assert text in repr({'display': value})


def test_numbers_and_booleans_time_out_the_same_way():
    """不只是字符串：等 2 篇、超时拿到的 3 也是真值，以前一样假通过"""
    count = wait_for_js(FakeWindow(3), 'n', 2, timeout=0, interval=0)
    assert not count and count == 3 and count != 2
    loaded = wait_for_js(FakeWindow(False), 'ok', True, timeout=0, interval=0)
    assert not loaded and loaded is not True and loaded.value is False


def test_falsy_expected_still_needs_an_explicit_comparison():
    """expected 本身是假值时，等到了返回的也是假值：只能写 `== 值`（老测试就是这么写的）"""
    assert wait_for_js(FakeWindow(2, 1, 0), 'n', 0, timeout=5, interval=0) == 0
    assert wait_for_js(FakeWindow('x', ''), 's', '', timeout=5, interval=0) == ''
    assert (wait_for_js(FakeWindow(1), 'n', 0, timeout=0, interval=0) == 0) is False


def test_without_expected_the_last_value_comes_back_unchanged():
    """expected=None 的行为不变：等真值，超时返回最后取到的值本身（它本来就是假值）"""
    assert wait_for_js(FakeWindow('', '', 'ok'), 'x', timeout=5, interval=0) == 'ok'
    for falsy in (0, '', None, False, []):
        value = wait_for_js(FakeWindow(falsy), 'x', timeout=0, interval=0)
        assert value is falsy


def test_timeout_zero_evaluates_once():
    window = FakeWindow('none')
    wait_for_js(window, PANEL, 'flex', timeout=0, interval=0)
    assert window.calls == 1
