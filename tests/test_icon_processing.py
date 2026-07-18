# -*- coding: utf-8 -*-
"""图标处理管线（移植自历史 22 项）：圆角/抗锯齿/智能裁切/路径安全/输出规范"""
import os
import random
import statistics

import pytest
from PIL import Image, ImageDraw


@pytest.fixture
def fns(app_ns):
    return {
        'apply': app_ns['_apply_rounded_corners'],
        'crop': app_ns['_smart_square_crop'],
        'validate': app_ns['_validate_icon_temp'],
        'tempdir': app_ns['_icon_temp_dir'],
    }


@pytest.fixture
def red():
    return Image.new('RGBA', (512, 512), (200, 30, 30, 255))


class TestRoundedCorners:
    def test_output_shape(self, fns, red):
        out = fns['apply'](red, 15)
        assert out.size == (512, 512) and out.mode == 'RGBA'

    def test_corner_transparent_center_opaque(self, fns, red):
        out = fns['apply'](red, 15)
        assert out.getpixel((0, 0))[3] == 0
        assert out.getpixel((256, 256))[3] == 255
        assert out.getpixel((256, 0))[3] == 255  # 边中点不在圆角内

    def test_antialiasing_gradient(self, fns, red):
        out = fns['apply'](red, 15)
        radius = round(512 * 0.15)
        diag = [out.getpixel((i, i))[3] for i in range(radius)]
        assert any(0 < a < 255 for a in diag), '圆弧边缘应有平滑过渡像素'

    def test_zero_pct_is_square(self, fns, red):
        assert fns['apply'](red, 0).getpixel((0, 0))[3] == 255

    def test_fifty_pct_and_clamp(self, fns, red):
        r50 = fns['apply'](red, 50)
        assert r50.getpixel((0, 0))[3] == 0
        assert fns['apply'](red, 99).getpixel((0, 0))[3] == r50.getpixel((0, 0))[3]

    def test_preserves_source_alpha(self, fns):
        semi = Image.new('RGBA', (512, 512), (30, 30, 200, 128))
        out = fns['apply'](semi, 15)
        assert out.getpixel((256, 256))[3] == 128
        assert out.getpixel((0, 0))[3] == 0


class TestSmartCrop:
    def test_offset_toward_subject(self, fns):
        wide = Image.new('RGBA', (1200, 600), (128, 128, 128, 255))
        d = ImageDraw.Draw(wide)
        random.seed(7)
        for _ in range(400):  # 右侧高细节彩色噪点块（显著区）
            x, y = random.randint(850, 1150), random.randint(150, 450)
            d.rectangle([x, y, x + 14, y + 14],
                        fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255), 255))
        cropped = fns['crop'](wide)
        assert cropped.size == (600, 600)
        px = list(cropped.convert('L').resize((60, 60)).get_flattened_data())
        assert statistics.pstdev(px) > 15, '裁切窗口应覆盖高细节主体区'

    def test_square_passthrough(self, fns):
        sq = Image.new('RGBA', (300, 300), (1, 2, 3, 255))
        assert fns['crop'](sq).size == (300, 300)

    def test_flat_image_degrades_gracefully(self, fns):
        flat = Image.new('RGBA', (1000, 500), (77, 77, 77, 255))
        assert fns['crop'](flat).size == (500, 500)


class TestTempPathSafety:
    def test_valid_png_accepted(self, fns):
        p = os.path.join(fns['tempdir'](), 'test_ok.png')
        Image.new('RGBA', (8, 8)).save(p, 'PNG')
        try:
            assert fns['validate'](p) is not None
        finally:
            os.remove(p)

    @pytest.mark.parametrize('bad', [
        r'C:\Windows\System32\notepad.exe',
        r'd:\MyNotepad\resources\icon.png',
    ])
    def test_bad_paths_rejected(self, fns, bad):
        assert fns['validate'](bad) is None

    def test_traversal_and_nonpng_rejected(self, fns):
        tmp = fns['tempdir']()
        assert fns['validate'](os.path.join(tmp, '..', 'x.png')) is None
        assert fns['validate'](os.path.join(tmp, 'a.txt')) is None


class TestOutputSpec:
    def test_png_roundtrip_with_alpha(self, fns, red, tmp_path):
        out_path = str(tmp_path / 'final.png')
        fns['apply'](red, 15).save(out_path, 'PNG')
        img = Image.open(out_path)
        assert img.format == 'PNG' and img.mode == 'RGBA'
        assert img.getpixel((0, 0))[3] == 0

    def test_ico_generation(self, fns, red, tmp_path):
        ico_path = str(tmp_path / 'final.ico')
        fns['apply'](red, 15).resize((256, 256), Image.LANCZOS).save(
            ico_path, 'ICO', sizes=[(256, 256)])
        with Image.open(ico_path) as ico:
            assert ico.size == (256, 256)
