# -*- coding: utf-8 -*-
"""生成 GitHub 社交预览图：`docs/social-preview.png`（1280x640）。

## 为什么有这个脚本

仓库 Settings → Social preview 要一张固定规格的图（PNG/JPG/GIF、至少 640x320、
**小于 1 MB**，推荐 1280x640）：别人在聊天软件/社交平台贴仓库链接时，卡片就是它。
界面截图换了（`docs/screenshots/*.png`）之后重跑一次即可：

    python docs/make_social_preview.py

## 两条刻意的做法

1. 主视觉用**真界面截图**（`screenshots/markdown.png`：Markdown 双栏实时预览，有正文内容），
   不用示意图 —— 卡片是外人看到的第一眼，摆一张空界面没有意义
   （第一版就是拿了 `main.png`，而那张是"还没有笔记"的空态）。
2. 左侧每一行文字都**量过右边界**，压到截图左边缘之前就报错退出 ——
   这种事"看着差不多"在 1280 宽下会真的叠上去（第一版的技术栈那行就压上了）。
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
SHOT = HERE / 'screenshots' / 'markdown.png'
OUT = HERE / 'social-preview.png'

W, H = 1280, 640
PAPER = '#FCFAF7'
INK = '#33332E'
BODY = '#55554C'
MUTED = '#8A8A7E'
SAGE = '#7D8A6E'
BORDER = '#E0DBD4'
FONT_REG = 'C:/Windows/Fonts/msyh.ttc'        # 微软雅黑
FONT_BOLD = 'C:/Windows/Fonts/msyhbd.ttc'

SX, SY, SHOT_W = 580, 262, 700                # 截图位置与宽度（右下角出血）
RADIUS = 12
SAFE_GAP = 20                                 # 文字右边界与截图之间至少留这么多像素
GITHUB_MAX_BYTES = 1024 * 1024


def _spaced(draw, xy, text, font, fill, spacing=4):
    """逐字画（PIL 没有字间距参数）—— 小字标签拉开来才有"杂志感"。"""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError as exc:
        raise SystemExit('font not found: %s (%s)' % (path, exc))


def build():
    """画出整张卡片，返回 (画布, [(文字, 右边界), ...])。"""
    canvas = Image.new('RGBA', (W, H), PAPER)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([0, 0, 11, H], fill=SAGE)                       # 左侧主色竖条
    draw.ellipse([W - 300, H - 130, W + 160, H + 330], fill='#EFEBE4')

    shot = Image.open(SHOT).convert('RGB')
    shot_h = round(shot.height * SHOT_W / shot.width)
    shot = shot.resize((SHOT_W, shot_h), Image.LANCZOS)
    mask = Image.new('L', (SHOT_W, shot_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, SHOT_W - 1, shot_h - 1], RADIUS, fill=255)

    shadow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [SX + 6, SY + 12, SX + SHOT_W + 6, SY + shot_h + 12], RADIUS, fill=(70, 66, 58, 80))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(18)))
    canvas.paste(shot, (SX, SY), mask)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([SX, SY, SX + SHOT_W - 1, SY + shot_h - 1], RADIUS,
                           outline=BORDER, width=2)

    f_eyebrow = _font(FONT_REG, 21)
    f_title = _font(FONT_BOLD, 80)
    f_sub = _font(FONT_REG, 29)
    f_tech = _font(FONT_REG, 23)
    f_tech_bold = _font(FONT_BOLD, 23)

    measured = []                      # (文字, 右边界)

    def left_text(x, y, text, font, fill):
        draw.text((x, y), text, font=font, fill=fill)
        measured.append((text, x + draw.textlength(text, font=font)))

    _spaced(draw, (74, 104), 'MYNOTEPAD · WINDOWS 桌面', f_eyebrow, SAGE, spacing=5)
    left_text(72, 140, '我的记事本', f_title, INK)
    left_text(74, 266, 'Markdown 原生 + 富文本双轨', f_sub, BODY)
    left_text(74, 310, '离线优先，数据只在自己机器上', f_sub, BODY)

    x = 74                                                     # 技术栈一行（逐段着色）
    for part, font, fill in (('Python', f_tech_bold, SAGE), (' · ', f_tech, MUTED),
                             ('pywebview', f_tech_bold, SAGE), (' · ', f_tech, MUTED),
                             ('SQLite', f_tech_bold, SAGE)):
        left_text(x, 376, part, font, fill)
        x += draw.textlength(part, font=font)

    badge_w = draw.textlength('MIT License', font=f_tech_bold) + 40   # 右上角许可徽标
    draw.rounded_rectangle([W - 44 - badge_w, 44, W - 44, 96], 26, outline=SAGE, width=2)
    draw.text((W - 44 - badge_w + 20, 57), 'MIT License', font=f_tech_bold, fill=SAGE)
    return canvas, measured


def main():
    if not SHOT.is_file():
        print('screenshot not found: %s' % SHOT)
        return 2
    canvas, measured = build()
    bad = [(t, r) for t, r in measured if r > SX - SAFE_GAP]
    for text, right in measured:
        label = text.encode('ascii', 'backslashreplace').decode('ascii')
        print('  right=%6.1f  limit=%d  %s' % (right, SX - SAFE_GAP, label))
    if bad:
        for text, right in bad:
            print('OVERLAP: %r right=%.1f would hit the screenshot at x=%d'
                  % (text.encode('ascii', 'backslashreplace').decode('ascii'), right, SX))
        return 1
    canvas.convert('RGB').save(OUT, optimize=True)
    size = OUT.stat().st_size
    if size >= GITHUB_MAX_BYTES:
        print('too big for GitHub social preview: %d bytes' % size)
        return 1
    print('saved %s  %dx%d  %.1f KB' % (OUT, W, H, size / 1024))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
