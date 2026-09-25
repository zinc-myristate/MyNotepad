# -*- coding: utf-8 -*-
"""图片文字识别（Windows 内置 OCR，经 winocr / winrt 调用）。

单独一个模块的理由有两条：
  1. **失败必须可降级**：没装 OCR 语言包、没有 winrt、非 Windows —— 这些都不能让应用起不来，
     只能让"识别文字"这一项报一句人话；
  2. **后处理是纯逻辑**：Windows OCR 给的是**逐词的框**，中文每个汉字就是一个"词"，
     所以 `text` 里全是多余空格（`"第 一 段 文 字"`），段落也要靠行距自己判断。
     这些是纯函数，单独测比连窗口一起测便宜得多。

实测（本机 zh-Hans-CN，900×260 的图）：
  · 耗时 ~75ms，中文正文识别质量不错，但**不是 100% 准**（形近字/标点会错）
  · 返回结构：`{'text': str, 'lines': [{'text': str, 'words': [{'text', 'bounding_rect'}]}],
              'text_angle': float}`
  · 行级框**没有**，只能从词框算 min/max —— 段落判断（行距）全靠它
"""
import json
import os
import re
import sys
import time

# 默认语言：本机 Windows 一般至少装了 zh-Hans-CN / en-GB 之一
OCR_LANG_DEFAULT = 'zh-Hans-CN'
# 界面上给用户看的名字（没列到的就直接显示 tag）
OCR_LANG_LABELS = {
    'zh-Hans-CN': '中文（简体）',
    'zh-Hant-TW': '中文（繁体）',
    'zh-Hans': '中文（简体）',
    'en-GB': 'English (UK)',
    'en-US': 'English (US)',
    'ja-JP': '日本語',
    'ko-KR': '한국어',
}

# 句末标点：上一行以它结尾 → 段落结束，不再与下一行合并
SENT_END = '。！？；：!?;:…”"』」）)】》〉'
# 行首的"新块"标记：列表项 / 编号 / 标题 / 引用 / 待办
# 编号那一条特意允许"数字 + 空格"（实测 OCR 常把 `1.` 的句点读丢，变成 `1 第一项`），
# 否则整份清单会被接成一大段。
BLOCK_START_RE = re.compile(
    r'^\s*(?:[-*•·○●▪◦]|\d{1,2}\s*[.、)）]?\s+|[一二三四五六七八九十]+[、.]|#{1,6}\s|>|\[[ xX]?\])'
)


def is_cjk(ch):
    """汉字/中文标点/全角字符：这类字符之间**不能有空格**（中文没有词间空格）"""
    if not ch:
        return False
    o = ord(ch)
    return (
        0x3400 <= o <= 0x4DBF          # 扩展 A
        or 0x4E00 <= o <= 0x9FFF       # 基本区
        or 0xF900 <= o <= 0xFAFF       # 兼容汉字
        or 0x3000 <= o <= 0x303F       # 中文标点
        or 0xFF00 <= o <= 0xFFEF       # 全角
    )


def _rect(word):
    r = (word or {}).get('bounding_rect') or {}
    return (float(r.get('x') or 0.0), float(r.get('y') or 0.0),
            float(r.get('width') or 0.0), float(r.get('height') or 0.0))


def join_words(words, gap_ratio=0.28):
    """把一行里的"词"拼回一句话。

    为什么要这个函数：Windows OCR 把**每个汉字**当一个词，`line.text` 于是长成
    `"第 一 段 文 字"`；反过来拉丁词又可能被拆开（`"M" + "ixed"`）。所以既不能直接
    `''.join`，也不能直接加空格——只能看**词框之间的水平间距**：

      · 两侧都是中日韩字符 → 不空格（中文本来就没有词间空格）
      · 间距 ≤ 字高 × gap_ratio（≈0.28）→ 判定为"同一个词被拆开"，不空格
      · 其余 → 一个空格（真正的词间/中英之间的间隔）
    """
    out = []
    prev_text = ''
    prev_x2 = prev_h = 0.0
    for w in words or []:
        text = (w or {}).get('text') or ''
        if not text:
            continue
        x, _y, width, height = _rect(w)
        h = height or prev_h or 1.0
        if out:
            gap = x - prev_x2
            if is_cjk(prev_text[-1]) and is_cjk(text[0]):
                sep = ''
            elif gap <= h * gap_ratio:
                sep = ''
            else:
                sep = ' '
            out.append(sep)
        out.append(text)
        prev_text = text
        prev_x2 = x + width
        prev_h = h
    return ''.join(out).strip()


def extract_lines(result):
    """OCR 原始结果 → `[{text, left, top, bottom, height}]`（行框由词框推出来）"""
    lines = []
    for ln in (result or {}).get('lines') or []:
        words = (ln or {}).get('words') or []
        text = join_words(words)
        if not text.strip():
            continue
        rects = [_rect(w) for w in words]
        rects = [r for r in rects if r[2] or r[3]]
        if rects:
            left = min(r[0] for r in rects)
            top = min(r[1] for r in rects)
            bottom = max(r[1] + r[3] for r in rects)
        else:
            left = top = bottom = 0.0
        lines.append({
            'text': text,
            'left': float(left),
            'top': float(top),
            'bottom': float(bottom),
            'height': max(float(bottom - top), 1.0),
        })
    return lines


def join_fragments(prev, cur):
    """把同一段的下一行接到上一行后面（英文断词连字符要去掉）"""
    if not prev:
        return cur
    if not cur:
        return prev
    a, b = prev[-1], cur[0]
    if a == '-' and b.isascii() and b.islower():
        return prev[:-1] + cur          # 英文行末断词：`docu-` + `ment` → `document`
    if is_cjk(a) and is_cjk(b):
        return prev + cur               # 中文折行：直接接
    if a.isspace() or b.isspace():
        return prev + cur
    return prev + ' ' + cur


def merge_paragraphs(lines, gap_ratio=0.8, indent_ratio=0.6):
    """按行距/缩进/句末标点/行首标记，把 OCR 的行还原成段落。

    规则（都是"看起来该断开"才断，宁可多合一行的初衷是中文正文本来就会被排版折行）：
      · 行距 ≥ 行高 × gap_ratio（默认 0.8）→ 断段
      · 左边界差 > 行高 × indent_ratio（默认 0.6）→ 断段（缩进变了通常就是新段/新块）
      · 上一行以句末标点结尾 → 断段
      · 本行以列表/编号/标题/引用标记开头 → 断段
    空行（OCR 一般不给，但防御一下）强制断段。
    """
    paras = []
    prev = None
    for ln in lines or []:
        text = (ln.get('text') or '').strip()
        if not text:
            prev = None
            continue
        if prev is None:
            paras.append(text)
        else:
            gap = float(ln.get('top') or 0) - float(prev['bottom'] or 0)
            height = max(float(prev.get('height') or 1.0), 1.0)
            same_indent = abs(float(ln.get('left') or 0) - float(prev['left'] or 0)) <= height * indent_ratio
            ends_sentence = prev['text'].rstrip().endswith(tuple(SENT_END))
            starts_block = bool(BLOCK_START_RE.match(text))
            if gap < height * gap_ratio and same_indent and not ends_sentence and not starts_block:
                paras[-1] = join_fragments(paras[-1], text)
            else:
                paras.append(text)
        prev = ln
    return '\n\n'.join(paras)


# ====== 下面是要 Windows OCR 运行时的部分（失败一律返回人话，不抛异常）======

def language_label(tag):
    return OCR_LANG_LABELS.get(tag, tag)


def available_languages():
    """本机**真实可用**的 OCR 语言（列不出来就返回空表，界面据此提示）"""
    try:
        from winrt.windows.media.ocr import OcrEngine
        return [str(lang.language_tag) for lang in OcrEngine.available_recognizer_languages]
    except Exception:
        return []


def languages_info():
    """给界面用的列表：可用语言 + 中文名 + 哪个是默认（第一个可用的中文优先）"""
    langs = available_languages()
    tags = list(langs)
    preferred = [t for t in (OCR_LANG_DEFAULT, 'zh-Hans', 'zh-Hant-TW') if t in tags]
    if preferred:
        default = preferred[0]
    elif tags:
        default = tags[0]
    else:
        default = OCR_LANG_DEFAULT
    return [{'tag': t, 'label': language_label(t), 'default': t == default} for t in tags]


def ocr_ready():
    """能用的最小条件：winrt + winocr 能导入，且至少有一种语言"""
    try:
        import winocr  # noqa: F401
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
    except Exception:
        return False
    return bool(available_languages())


def _recognize_one(img, tag):
    import winocr
    started = time.time()
    raw = winocr.recognize_pil_sync(img, tag)
    return raw, int((time.time() - started) * 1000)


def recognize(path, lang=None, max_scale=3, long_side_cap=6000):
    """识别一张图片。

    返回 `{'ok': True, 'text':…, 'lines':…, 'ms':…, 'lang':…, 'scale':…}`
    或 `{'ok': False, 'error': '人话', 'languages': […]}`（**不抛异常**：
    调用方是界面，界面只需要一句话）。

    **会自动放大重试**：实测文字少/字号小的图，1× 时引擎会**什么都认不出来**
    （`图片里的字` 这种 5 个字的短行直接被丢掉），放大 2× 就正常了。
    所以第一遍啥也没认出来时会放大 2×、3× 再试——这类图恰恰是截图里最常见的
    （UI 文案、代码片段），静默返回空是最难查的那种失败。
    """
    tag = (lang or OCR_LANG_DEFAULT).strip() or OCR_LANG_DEFAULT
    if not path or not os.path.isfile(path):
        return {'ok': False, 'error': '找不到要识别的图片'}
    try:
        from PIL import Image
    except Exception as exc:                                   # pragma: no cover
        return {'ok': False, 'error': '缺少 Pillow：%s' % exc}
    try:
        import winocr  # noqa: F401
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine
    except Exception as exc:
        # 把完整 traceback 也带上：打包后这类失败（winrt 的 .pyd 没收进去、
        # 冻结环境下 asyncio 导入异常…）光看一句 message 根本查不出来
        import sys
        import traceback
        state = {k: bool(getattr(v, '__file__', None)) for k, v in list(sys.modules.items())
                 if k == 'asyncio' or k.startswith('asyncio.')}
        return {'ok': False, 'error': '这台机器没有 Windows OCR 组件（%s）' % exc,
                'languages': available_languages(),
                'import_state': state,
                'traceback': traceback.format_exc()[-2000:]}
    langs = available_languages()
    if not OcrEngine.is_language_supported(Language(tag)) or (langs and tag not in langs):
        return {'ok': False,
                'error': '没有安装「%s」的 OCR 语言包（可在「设置 → 时间和语言 → 语言」里添加）'
                         % language_label(tag),
                'languages': langs}
    try:
        with Image.open(path) as im:
            img = im.convert('RGB')
    except Exception as exc:
        return {'ok': False, 'error': '读不出这张图：%s' % exc, 'languages': langs}

    tried = []
    total_ms = 0
    lines, raw = [], {}
    used_scale = 1
    try:
        scale = 1
        while True:
            cur = img if scale == 1 else img.resize(
                (max(1, img.width * scale), max(1, img.height * scale)), Image.LANCZOS)
            if scale > 1 and max(cur.width, cur.height) > long_side_cap:
                break                       # 别为了小图把内存撑爆
            raw, ms = _recognize_one(cur, tag)
            total_ms += ms
            tried.append(scale)
            lines = extract_lines(raw)
            if lines or scale >= max_scale:
                used_scale = scale
                break
            scale += 1
    except Exception as exc:
        try:
            import applog
            applog.get_logger().exception("OCR 识别失败")
        except Exception:
            pass
        return {'ok': False, 'error': '识别失败：%s' % exc, 'languages': langs}
    text = merge_paragraphs(lines)
    if not text:
        text = (raw.get('text') or '').strip()
    return {'ok': True, 'text': text, 'lines': lines, 'ms': total_ms, 'lang': tag,
            'scale': used_scale, 'scales_tried': tried,
            'has_text': bool(text.strip())}


def _selftest():
    """`python ocr.py [图片] [语言]` —— 打包版也用它做冒烟（exe 带 `--ocr-selftest`）。

    为什么要留这个入口：OCR 依赖的是**系统语言包 + winrt 运行时**，
    这两样在打包后最容易静默失效（"点了没反应"那类）。有了它，
    诊断脚本和打包验证都能一句话问清楚"这台机器上到底能不能识别"。
    不带图片时只报告"能不能用 + 有哪些语言"（诊断脚本用这个）。
    """
    return selftest_from_argv(['--ocr-selftest'] + sys.argv[1:])


def selftest_from_argv(argv):
    """解析 `--ocr-selftest [图片] [语言] [--out 输出.json]`，返回进程退出码。

    没有控制台时（打包版是以窗口模式启动的）结果写进 `<图片>.ocr.json`，
    这样"打包版到底能不能识别"就是读一个文件的事。
    """
    args = list(argv)
    if '--ocr-selftest' not in args:
        return 2
    rest = args[args.index('--ocr-selftest') + 1:]
    out_path = None
    if '--out' in rest:
        i = rest.index('--out')
        out_path = rest[i + 1] if i + 1 < len(rest) else None
        rest = rest[:i]
    image = rest[0] if rest else ''
    lang = rest[1] if len(rest) > 1 else OCR_LANG_DEFAULT
    ready = ocr_ready()
    payload = {'ready': ready, 'languages': available_languages()}
    if image:
        payload['result'] = recognize(image, lang)
    if out_path is None and image:
        out_path = image + '.ocr.json'
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if out_path:
        try:
            with open(out_path, 'w', encoding='utf-8') as fh:
                fh.write(text)
        except OSError:
            pass
    print(text)
    if not image:
        return 0 if ready else 1
    return 0 if payload['result'].get('ok') else 1


if __name__ == '__main__':
    sys.exit(_selftest())
