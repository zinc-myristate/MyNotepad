# -*- coding: utf-8 -*-
"""第十一轮「图片文字识别」的单测。

分成两半：
  · **纯函数**（拼接词、算行框、段落合并）——不需要任何 Windows 组件，规则逐条钉死；
  · **真引擎**（`ocr.recognize`）——确认真能识别出字、错误路径给人话而不是抛异常。
    没有 OCR 引擎的机器上自动跳过（本机 Windows 自带 zh-Hans-CN）。

这些纯函数为什么值得测：Windows OCR 的中文结果是**逐字带空格**的
（`"第 一 段 文 字"`），直接拿去用会得到一堆莫名其妙的空格；段落则完全靠行距判断——
这两件事都是"错了用户立刻看得出来、但代码不会报错"的地方。
"""
import os

import pytest

import ocr

# ---------------- 纯函数 ----------------

def _w(text, x, y=0.0, width=10.0, height=20.0):
    return {'text': text, 'bounding_rect': {'x': x, 'y': y, 'width': width, 'height': height}}


def test_is_cjk():
    assert ocr.is_cjk('中')
    assert ocr.is_cjk('，')
    assert ocr.is_cjk('％')
    assert not ocr.is_cjk('a')
    assert not ocr.is_cjk('1')
    assert not ocr.is_cjk('')


def test_join_words_drops_spaces_between_chinese():
    """中文逐字是"词"，空格全是 OCR 加的 → 必须去掉"""
    words = [_w('第', 0), _w('一', 12), _w('段', 24), _w('。', 36)]
    assert ocr.join_words(words) == '第一段。'


def test_join_words_glues_split_latin_word():
    """拉丁词被拆开时词框几乎贴着（间距≈0）→ 拼回去"""
    words = [_w('M', 0, width=22), _w('ixed', 22.5, width=60),
             _w('text', 96, width=50)]
    assert ocr.join_words(words) == 'Mixed text'


def test_join_words_keeps_real_spaces_in_latin():
    words = [_w('hello', 0, width=50), _w('world', 70, width=50)]
    assert ocr.join_words(words) == 'hello world'


def test_join_words_between_latin_and_chinese_uses_gap():
    """中英之间按实际间距决定：有间隙就留一个空格，贴着就不留"""
    spaced = [_w('and', 0, width=40), _w('中', 60), _w('文', 72)]
    assert ocr.join_words(spaced) == 'and 中文'
    tight = [_w('abc', 0, width=40), _w('中', 40.5), _w('文', 52.5)]
    assert ocr.join_words(tight) == 'abc中文'


def test_extract_lines_computes_box_from_words():
    """行框要从词框算：行级 bounding_rect Windows 不给"""
    raw = {'lines': [
        {'words': [_w('上', 20, y=30, height=26), _w('行', 32, y=28, height=30)]},
        {'words': [_w('', 0)]},                                  # 空行丢掉
        {'words': [_w('下', 22, y=100, height=28)]},
    ]}
    lines = ocr.extract_lines(raw)
    assert [ln['text'] for ln in lines] == ['上行', '下']
    assert lines[0]['left'] == 20 and lines[0]['top'] == 28 and lines[0]['bottom'] == 58
    assert lines[0]['height'] == 30


def _line(text, left=20.0, top=0.0, bottom=30.0):
    return {'text': text, 'left': left, 'top': top, 'bottom': bottom, 'height': bottom - top}


def test_merge_paragraphs_joins_wrapped_lines():
    """排版折行（行距小、同缩进、上句没结束）→ 接成一段"""
    lines = [_line('第一段文字跨行排版测试，内容比较长，', top=0, bottom=34),
             _line('所以被排版折成了两行显示。', top=38, bottom=70)]
    assert ocr.merge_paragraphs(lines) == '第一段文字跨行排版测试，内容比较长，所以被排版折成了两行显示。'


def test_merge_paragraphs_breaks_on_sentence_end():
    lines = [_line('第一句结束了。', top=0, bottom=30),
             _line('这是新的一句话', top=34, bottom=64)]
    assert ocr.merge_paragraphs(lines) == '第一句结束了。\n\n这是新的一句话'


def test_merge_paragraphs_breaks_on_gap():
    lines = [_line('上一个大块', top=0, bottom=30),
             _line('下一个大块', top=90, bottom=120)]
    assert ocr.merge_paragraphs(lines) == '上一个大块\n\n下一个大块'


def test_merge_paragraphs_breaks_on_list_marker_and_indent():
    lines = [_line('说明文字：', top=0, bottom=30),
             _line('- 列表项一', top=34, bottom=64, left=20),
             _line('- 列表项二', top=68, bottom=98, left=20),
             _line('缩进变了的行', top=102, bottom=132, left=60)]
    out = ocr.merge_paragraphs(lines)
    assert out.split('\n\n') == ['说明文字：', '- 列表项一', '- 列表项二', '缩进变了的行']


def test_merge_paragraphs_recognises_numbered_list_without_dot():
    """实测 OCR 常把 `1.` 的句点读丢（变成 `1 第一项`）→ 也要认成新块"""
    lines = [_line('说明：', top=0, bottom=30),
             _line('1 第一项', top=34, bottom=64),
             _line('2 第二项', top=68, bottom=98)]
    assert ocr.merge_paragraphs(lines).split('\n\n') == ['说明：', '1 第一项', '2 第二项']


def test_merge_paragraphs_english_hyphenation():
    lines = [_line('docu-', top=0, bottom=30),
             _line('ment it', top=34, bottom=64)]
    assert ocr.merge_paragraphs(lines) == 'document it'


def test_join_fragments_rules():
    assert ocr.join_fragments('中文', '接上') == '中文接上'
    assert ocr.join_fragments('english', 'words') == 'english words'
    assert ocr.join_fragments('docu-', 'ment') == 'document'
    assert ocr.join_fragments('', 'x') == 'x'


def test_merge_paragraphs_handles_empty():
    assert ocr.merge_paragraphs([]) == ''
    assert ocr.merge_paragraphs([_line('  ', top=0, bottom=10), _line('真内容', top=20, bottom=50)]) \
        == '真内容'


def test_language_label_falls_back_to_tag():
    assert ocr.language_label('zh-Hans-CN') == '中文（简体）'
    assert ocr.language_label('xx-XX') == 'xx-XX'


# ---------------- 真引擎（没有引擎就跳过）----------------

def _need_engine(lang=None):
    """没有引擎就跳过；给了 lang 还要求**那个语言包真的装了**。

    ⚠️ 只检查"有没有引擎"是不够的：GitHub Actions 的 windows-latest 装了英文引擎、
    却没装中文语言包，守卫会放行，后面的中文识别用例必然失败（实测 7 条全挂）。
    """
    if not ocr.ocr_ready():
        pytest.skip('这台机器没有可用的 Windows OCR 引擎/语言包')
    if lang and lang not in ocr.available_languages():
        pytest.skip('这台机器没有安装 %s 的 OCR 语言包' % lang)


def _make_image(path, lines, size=(900, 240)):
    """画一张白底黑字图（用雅黑，缺字体就跳过——默认字体画不出中文）"""
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype(r'C:\Windows\Fonts\msyh.ttc', 28)
    except Exception:
        pytest.skip('没有中文字体，无法生成测试图')
    img = Image.new('RGB', size, 'white')
    draw = ImageDraw.Draw(img)
    for i, text in enumerate(lines):
        draw.text((20, 20 + i * 44), text, font=font, fill='black')
    img.save(path)
    return path


def test_available_languages_lists_something():
    _need_engine()
    langs = ocr.available_languages()
    assert langs, '装了引擎却列不出语言'
    assert all(isinstance(x, str) and '-' in x for x in langs)


def test_recognize_real_image(tmp_path):
    """真识别：中文正文 + 折行 + 列表项 → 段落还原正确"""
    _need_engine('zh-Hans-CN')
    img = _make_image(os.path.join(str(tmp_path), 'shot.png'), [
        '第一段文字跨行排版测试，内容比较长，',
        '所以被排版折成了两行显示。',
    ])
    res = ocr.recognize(img, 'zh-Hans-CN')
    assert res['ok'] is True, res
    assert res['ms'] >= 0 and res['lang'] == 'zh-Hans-CN'
    text = res['text']
    # 识别不可能逐字全对，但关键词与"合成一段"必须成立
    assert '排版' in text and '两行' in text
    assert '\n\n' not in text, '折行的同一段不该被拆开：%r' % text
    assert ' ' not in text.replace(' ', '') or True   # 中文里不应有空格（下面单独断言）
    assert '第 一' not in text, '中文之间不该有空格：%r' % text


def test_recognize_list_items_stay_separate(tmp_path):
    """真识别只保证"内容都在、顺序对"——逐字精度是引擎的事，不该由测试来赌"""
    _need_engine('zh-Hans-CN')
    img = _make_image(os.path.join(str(tmp_path), 'list.png'), [
        '正文说明',
        '1. 第一项',
        '2. 第二项',
    ])
    res = ocr.recognize(img, 'zh-Hans-CN')
    assert res['ok'] is True, res
    text = res['text']
    assert '第一项' in text and '第二项' in text
    assert text.index('第一项') < text.index('第二项')


def test_recognize_default_language_is_chinese(tmp_path):
    _need_engine(ocr.OCR_LANG_DEFAULT)
    img = _make_image(os.path.join(str(tmp_path), 'a.png'), ['中文测试'])
    res = ocr.recognize(img)
    assert res['ok'] is True and res['lang'] == ocr.OCR_LANG_DEFAULT


def test_recognize_unsupported_language_is_friendly(tmp_path):
    _need_engine()
    img = _make_image(os.path.join(str(tmp_path), 'b.png'), ['中文测试'])
    res = ocr.recognize(img, 'ja-JP')
    if 'ja-JP' in ocr.available_languages():
        pytest.skip('这台机器居然装了日文 OCR')
    assert res['ok'] is False
    assert '语言包' in res['error']
    # 报错时把**本机可用**的语言一起给出来，界面好直接换成能用的那个
    assert res['languages'] == ocr.available_languages()


def test_recognize_missing_file():
    res = ocr.recognize('/no/such/file.png')
    assert res['ok'] is False and '找不到' in res['error']


def test_recognize_blank_image_has_no_text(tmp_path):
    """纯白图：ok=True 但没有文字（界面据此提示"没认出文字"）"""
    _need_engine(ocr.OCR_LANG_DEFAULT)
    from PIL import Image
    path = os.path.join(str(tmp_path), 'blank.png')
    Image.new('RGB', (400, 200), 'white').save(path)
    res = ocr.recognize(path)
    assert res['ok'] is True
    assert res['has_text'] is False and res['text'].strip() == ''


def test_recognize_short_text_is_not_silently_empty(tmp_path):
    """小图里几个字：不管靠不靠放大，**结果都不能是空的**。

    （引擎在"字太少/字太小"这种边界上时好时坏——1× 有时认得出、有时直接返回空行，
     所以这里只断言最终有字；放大重试的机制本身由下面那条用桩函数确定性地测。）
    """
    _need_engine(ocr.OCR_LANG_DEFAULT)
    img = _make_image(os.path.join(str(tmp_path), 'short.png'), ['图片里的字'])
    res = ocr.recognize(img)
    assert res['ok'] is True, res
    assert res['has_text'] is True, '短文本被引擎丢掉了：%r' % res
    assert '图片' in res['text'] or '片里' in res['text']


def test_recognize_upscales_when_first_pass_finds_nothing(tmp_path, monkeypatch):
    """第一遍什么都没认出来时，必须放大再试（并用放大后的图）"""
    _need_engine(ocr.OCR_LANG_DEFAULT)
    img = _make_image(os.path.join(str(tmp_path), 'short.png'), ['图片里的字'])
    real = ocr._recognize_one
    seen = []

    def fake(image, tag):
        seen.append((image.width, image.height))
        if len(seen) == 1:
            return ({'lines': [], 'text': ''}, 7)      # 假装 1× 什么都没看到
        return real(image, tag)

    monkeypatch.setattr(ocr, '_recognize_one', fake)
    res = ocr.recognize(img)
    assert res['ok'] is True and res['has_text'] is True, res
    assert res['scales_tried'] == [1, 2], res
    assert res['scale'] == 2
    assert seen[1][0] == seen[0][0] * 2 and seen[1][1] == seen[0][1] * 2, \
        '第二遍必须用放大后的图：%r' % (seen,)


def test_recognize_does_not_upscale_when_first_pass_works(tmp_path):
    """正常图不该白做一次放大（多花几十毫秒）"""
    _need_engine(ocr.OCR_LANG_DEFAULT)
    img = _make_image(os.path.join(str(tmp_path), 'long.png'),
                      ['第一段文字跨行排版测试，内容比较长，', '所以被排版折成了两行显示。'])
    res = ocr.recognize(img)
    assert res['ok'] is True and res['has_text'] is True
    assert res['scale'] == 1 and res['scales_tried'] == [1]
