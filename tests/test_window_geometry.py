# -*- coding: utf-8 -*-
"""窗口几何回归测试：保证窗口在任何屏/DPI/任务栏配置下都完整可见。

背景（2026-09-20 实测）：3072×1920 物理屏 @200% 缩放 → 逻辑 1536×960，
底部任务栏占物理 96px（逻辑 48px），可用工作区仅 1536×912。原实现把窗口写死
1200×800 且交给 pywebview 的 CenterScreen，窗口被居中到 y=196、底边落在 y=996，
下沿 84px 压在任务栏底下，侧边栏最后一排（日历/回收站）因此看不见。

这里通过 app.pyw 的纯函数 clamp_window_geometry 覆盖各种工作区，无需真实屏幕。
"""
import pytest


def _clamp(ns, w, h, work_area, **kw):
    return ns['clamp_window_geometry'](w, h, work_area, **kw)


def assert_inside(geom, work_area):
    """断言窗口四边完全落在工作区内（即不会被任务栏/屏幕边缘切掉）"""
    w, h, x, y = geom
    assert x is not None and y is not None, '有工作区时必须给出明确位置'
    wl, wt, ww, wh = work_area
    assert x >= wl, '左边越界: x=%s < 工作区左边=%s' % (x, wl)
    assert y >= wt, '上边越界: y=%s < 工作区上边=%s' % (y, wt)
    assert x + w <= wl + ww, '右边越界: %s > %s' % (x + w, wl + ww)
    assert y + h <= wt + wh, '下边越界（会被任务栏压住）: %s > %s' % (y + h, wt + wh)


# 工作区比最小窗口还小的极端配置：(标签, 工作区)
TINY_WORK_AREAS = [
    ('960x540   @150% 小屏缩放（工作区比最小窗口还小）', (0, 0, 960, 540)),
    ('800x420   @100% 极小工作区', (0, 0, 800, 420)),
]


def assert_usable_and_anchored(geom, work_area, ns):
    """工作区本身就小于最小窗口时：可以溢出，但必须保住最小可用尺寸且左上角可见

    取舍：宁可窗口略超出屏幕（用户仍能拖动/最大化），也不能把窗口缩到低于
    pywebview 的 min_size(900x600) —— 那会让整个界面挤压到不可用。
    """
    w, h, x, y = geom
    assert w >= ns['MIN_WIN_W'] and h >= ns['MIN_WIN_H'], '不得低于最小可用尺寸'
    wl, wt, _ww, _wh = work_area
    assert x >= wl and y >= wt, '左上角必须留在工作区内（否则用户抓不到窗口）'


# 常见实际配置：(标签, 工作区 left/top/w/h)  —— 工作区已排除任务栏
WORK_AREAS = [
    ('1536x912 @200% 200% 缩放 + 底部任务栏（本次实际故障场景）', (0, 0, 1536, 912)),
    ('1920x1040 @100% 1080p + 40px 任务栏', (0, 0, 1920, 1040)),
    ('1280x680  @100% 1366x768 笔记本 + 任务栏', (0, 0, 1280, 680)),
    ('1024x728  @100% 1024x768', (0, 0, 1024, 728)),
    ('3440x1360 @100% 带鱼屏', (0, 0, 3440, 1360)),
    ('1920x1040 副屏（工作区左上角不在原点）', (1920, 0, 1920, 1040)),
    ('1280x1024 任务栏在左侧（工作区 left 偏移）', (48, 0, 1280, 1024)),
    ('1280x1024 任务栏在顶部（工作区 top 偏移）', (0, 40, 1280, 1024)),
]


@pytest.mark.parametrize('label,work_area', WORK_AREAS)
def test_default_window_fits_every_work_area(app_ns, label, work_area):
    """默认尺寸的窗口在每种工作区下都必须完整可见（核心回归）"""
    geom = _clamp(app_ns, app_ns['DEFAULT_WIN_W'], app_ns['DEFAULT_WIN_H'], work_area)
    assert_inside(geom, work_area)


@pytest.mark.parametrize('label,work_area', WORK_AREAS)
def test_oversized_request_is_shrunk_to_fit(app_ns, label, work_area):
    """请求一个超屏尺寸（如 4000x3000）必须被收缩到工作区内"""
    geom = _clamp(app_ns, 4000, 3000, work_area)
    assert_inside(geom, work_area)


@pytest.mark.parametrize('label,work_area', TINY_WORK_AREAS)
def test_tiny_work_area_keeps_window_usable(app_ns, label, work_area):
    """工作区小于最小窗口：保住最小尺寸且左上角可见（允许溢出，不可缩到不可用）"""
    for req in ((app_ns['DEFAULT_WIN_W'], app_ns['DEFAULT_WIN_H']), (4000, 3000)):
        geom = _clamp(app_ns, *req, work_area)
        assert_usable_and_anchored(geom, work_area, app_ns)


def test_actual_bug_scenario_regression(app_ns):
    """本次故障的精确复现：1536x912 工作区下，底边必须留出余量而非被任务栏压住。

    修复前：窗口 1200x800 居中到 y=196，底边 996 > 912 —— 越界 84px。
    """
    work_area = (0, 0, 1536, 912)
    w, h, x, y = _clamp(app_ns, 1200, 800, work_area)
    assert (w, h) == (1200, 800), '默认尺寸本身能放下，不该被改小'
    assert (x, y) == (168, 56), '应居中且落在安全位置（实测值）'
    assert y + h == 856 < 912, '底边必须高于任务栏上沿（修复前是 996）'
    assert_inside((w, h, x, y), work_area)


def test_never_below_min_size(app_ns):
    """即使工作区很大也不放大；请求过小时抬到最小尺寸 900x600"""
    ns = app_ns
    big = (0, 0, 4000, 3000)
    assert _clamp(ns, ns['DEFAULT_WIN_W'], ns['DEFAULT_WIN_H'], big)[:2] == (1200, 800), \
        '默认尺寸不应被放大'
    w, h, _, _ = _clamp(ns, 100, 100, big)
    assert (w, h) == (ns['MIN_WIN_W'], ns['MIN_WIN_H'])


def test_min_size_is_respected_even_in_tiny_work_area(app_ns):
    """工作区小于最小窗口时，仍返回最小尺寸（宁可略溢出也不能缩到不可用）"""
    ns = app_ns
    w, h, x, y = _clamp(ns, ns['DEFAULT_WIN_W'], ns['DEFAULT_WIN_H'], (0, 0, 600, 300))
    assert w == ns['MIN_WIN_W'] and h == ns['MIN_WIN_H']
    assert x is not None and y is not None


def test_vertical_reserve_keeps_bottom_clear_of_taskbar(app_ns):
    """垂直必须留出预留量：能放下时高度不该等于工作区高度（否则底边贴住任务栏）"""
    ns = app_ns
    reserve = ns['_WORKAREA_RESERVE']
    assert reserve > 0
    for _label, wa in WORK_AREAS:
        w, h, x, y = _clamp(ns, ns['DEFAULT_WIN_W'], ns['DEFAULT_WIN_H'], wa)
        if wa[3] - reserve >= ns['MIN_WIN_H']:
            assert h <= wa[3] - reserve, '应留出 %dpx 余量' % reserve


def test_no_work_area_falls_back_gracefully(app_ns):
    """取不到工作区（非 Windows / API 失败）时不得抛错，只保证最小尺寸，位置交回 pywebview"""
    ns = app_ns
    for wa in (None, (), (0, 0, 0, 0)):
        w, h, x, y = _clamp(ns, 300, 200, wa)
        assert (w, h) == (ns['MIN_WIN_W'], ns['MIN_WIN_H'])
        assert x is None and y is None


def test_position_is_centered_within_work_area(app_ns):
    """位置应相对工作区居中，而不是相对整屏"""
    ns = app_ns
    wa = (100, 50, 1400, 900)
    w, h, x, y = _clamp(ns, 1200, 800, wa)
    assert x == 100 + (1400 - w) // 2
    assert y == 50 + (900 - h) // 2


def test_helper_wrapper_matches_pure_function(app_ns):
    """_clamp_window_geometry 薄封装必须与纯函数在同一真实工作区下结果一致"""
    ns = app_ns
    wa = ns['_get_work_area']()
    if not wa:
        pytest.skip('本机取不到工作区，跳过一致性校验')
    assert ns['_clamp_window_geometry'](1200, 800) == \
        ns['clamp_window_geometry'](1200, 800, wa)


def test_resolved_geometry_fits_real_work_area(app_ns):
    """_resolve_window_geometry 产出的几何必须能在本机真实工作区内完整显示"""
    ns = app_ns
    geo = ns['_resolve_window_geometry'](None)
    assert geo['w'] > 0 and geo['h'] > 0
    wa = ns['_get_work_area']()
    if wa:
        assert_inside((geo['w'], geo['h'], geo['x'], geo['y']), wa)


def test_returns_integers(app_ns):
    """返回值必须是整数（pywebview create_window 对类型敏感）"""
    ns = app_ns
    for val in _clamp(ns, 1200.7, 800.4, (0, 0, 1536.9, 912.5)):
        assert isinstance(val, int), '应为 int，实际 %r' % type(val)
