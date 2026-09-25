# -*- coding: utf-8 -*-
"""第十轮「捕获与模板」的单测：模板 CRUD / 变量渲染 / 每日笔记幂等 / 收件箱捕获。

两条关键约定在这里锁死：
  1. 每日笔记**幂等**（当天已存在就打开，不能生成两篇）——否则"打开今天的日记"每点一次多一篇；
  2. 捕获落到「收件箱」且**第一行当标题**——捕获的价值是"不打断"，之后再整理。
"""
import os

# ---------------- 模板 ----------------

def test_template_crud(api, backend_mod):
    assert api.templates_list() == []
    tpl = api.template_create('会议记录', '# 会议 {{date}}\n\n- 议题\n')
    assert tpl['name'] == '会议记录'
    assert [t['id'] for t in api.templates_list()] == [tpl['id']]
    api.template_update(tpl['id'], {'name': '周会记录', 'content': '正文'})
    assert api.template_get(tpl['id'])['name'] == '周会记录'
    api.template_delete(tpl['id'])
    assert api.templates_list() == []
    assert api.template_get(tpl['id']) is None


def test_template_render_variables(api, backend_mod):
    tpl = api.template_create('日记', '{{date}} {{weekday}} {{time}}\n\n# {{title}}\n')
    out = api.template_render(tpl['id'], title='测试标题')
    import datetime
    today = datetime.datetime.now()
    assert today.strftime('%Y-%m-%d') in out
    assert today.strftime('%H:%M') in out
    assert '周' in out
    assert '# 测试标题' in out
    # 不认识的变量原样留着（用户可能真的想写 {{ }}）
    tpl2 = api.template_create('奇怪', '{{unknown}} 与 {{ date }}')
    assert api.template_render(tpl2['id']) == '{{unknown}} 与 ' + today.strftime('%Y-%m-%d')
    # 模板不存在返回空串（不是异常）
    assert api.template_render('no-such-id') == ''


# ---------------- 每日笔记 ----------------

def test_daily_note_is_idempotent(api, backend_mod):
    first = api.daily_note_open()
    second = api.daily_note_open()
    assert first['id'] == second['id'], '同一天只能有一篇日记'
    assert len(api.notes_list()) == 1
    import datetime
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    assert first['title'].startswith(today)
    assert '周' in first['title']
    # 落在「日记」笔记本里
    nbs = {nb['name']: nb['id'] for nb in api.notebooks_list()}
    assert '日记' in nbs and first['notebook_id'] == nbs['日记']


def test_daily_note_uses_daily_template(api, backend_mod):
    api.template_create('日记', '# {{date}}\n\n- [ ] 今天的事 📅 {{date}}\n')
    note = api.daily_note_open()
    assert '# ' in note['content'] and '今天的事' in note['content']
    # 第二次打开不会重复套模板（返回同一篇）
    again = api.daily_note_open()
    assert again['content'] == note['content']


# ---------------- 捕获 ----------------

def test_capture_text_goes_to_inbox(api, backend_mod):
    note = api.capture_text('买牛奶\n还有鸡蛋\n')
    assert note is not None
    assert note['title'] == '买牛奶', '第一行当标题'
    assert '还有鸡蛋' in note['content']
    assert note['format'] == 'md'
    nbs = {nb['name']: nb['id'] for nb in api.notebooks_list()}
    assert '收件箱' in nbs and note['notebook_id'] == nbs['收件箱']
    # 空内容不建笔记
    assert api.capture_text('   \n  ') is None
    assert len(api.notes_list()) == 1


def test_capture_text_title_truncated(api, backend_mod):
    long_line = '很长的标题' * 20
    note = api.capture_text(long_line + '\n正文')
    assert len(note['title']) <= 51 and note['title'].endswith('…')


def test_capture_image_creates_attachment_note(tmp_path, api, backend_mod):
    from PIL import Image
    img = os.path.join(str(tmp_path), 'shot.png')
    Image.new('RGB', (80, 60), (10, 20, 30)).save(img)
    note = api.capture_image(img)
    assert note is not None
    assert note['title'].startswith('截图 ')
    assert 'attachments/%s/' % note['id'] in note['content']
    files = api.attachments_list(note['id'])
    assert files and files[0]['filename'] in note['content']
    # 图片真的复制进来了（不是只写了路径）
    path = api.attachments_get_path(note['id'], files[0]['filename'])
    assert path and os.path.isfile(path)
    # 收件箱
    nbs = {nb['name']: nb['id'] for nb in api.notebooks_list()}
    assert note['notebook_id'] == nbs['收件箱']


def test_capture_image_missing_file(api, backend_mod):
    assert api.capture_image('/no/such/file.png') is None
    assert api.capture_image('') is None


def test_capture_image_with_ocr_body(tmp_path, api, backend_mod):
    """第 11 轮：识别出的文字接在图片下面（图保留），标题取文字第一行"""
    from PIL import Image
    img = os.path.join(str(tmp_path), 'ocr.png')
    Image.new('RGB', (60, 40), (250, 250, 250)).save(img)
    note = api.capture_image(img, None, '第一行标题\n\n正文内容')
    assert note is not None
    assert note['title'] == '第一行标题', '给了正文就该拿正文第一行当标题'
    body = api.notes_get(note['id'])['content']
    assert body.startswith('!['), '图片在前'
    assert body.index('第一行标题') > body.index('!['), '文字在图片下面'
    assert '正文内容' in body
    # 再确认图片真进了附件（不是只写了个路径）
    files = api.attachments_list(note['id'])
    assert files and files[0]['filename'] in body
    # 没给正文时保持原样：标题还是"截图 时间"
    note2 = api.capture_image(img)
    assert note2['title'].startswith('截图 ')


def test_capture_custom_notebook_name(api, backend_mod):
    note = api.capture_text('随手记', notebook_name='速记')
    nbs = {nb['name']: nb['id'] for nb in api.notebooks_list()}
    assert note['notebook_id'] == nbs['速记']
    # 同一个名字不会重复建笔记本
    api.capture_text('第二条', notebook_name='速记')
    assert len([nb for nb in api.notebooks_list() if nb['name'] == '速记']) == 1
