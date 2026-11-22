# -*- coding: utf-8 -*-
"""用演示数据重拍 README 的四张截图：`docs/screenshots/{main,markdown,dark,background}.png`。

## 为什么有这个脚本

截图会公开挂在仓库首页，里面只能是演示数据。旧图是在自己的库上拍的，笔记本名就这么公开了，
之后每次界面一改又得手工重拍。现在一条命令：

    python docs/make_screenshots.py
    python docs/make_social_preview.py      # 社交预览图用的是 markdown.png，跟着重新生成

## 做了什么

1. 在 `dist/_demo-screenshots-data/` 建一个全新的库（不是用户数据的副本）：3 个笔记本、3 个标签、
   7 篇 Markdown 笔记，外加一张生成的渐变背景图；
2. 用这个库从源码起应用（设了 `MYNOTEPAD_DATA_DIR`，单实例互斥体会跳过，不影响正在用的记事本），
   开 CDP 端口，把视口定成 1523x828、2 倍像素（图是 3046x1656，和以前一样）；
3. 关掉源码运行才有的「开发模式：当前数据目录 …」提示（带着本机路径），依次拍四张；
4. 界面那三张量化成 256 色（平均误差不到 0.1，体积小一半多）；背景那张是渐变，量化会出色带，保留 RGB；
5. 关掉应用，删掉演示库。

只用标准库和 Pillow（CDP 的 WebSocket 客户端是下面那几十行，不另装依赖）。
"""
import base64
import json
import os
import random
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'docs', 'screenshots')
DEMO = os.path.join(ROOT, 'dist', '_demo-screenshots-data')
PORT = 9367
VIEW_W, VIEW_H, SCALE = 1523, 828, 2


def log(msg):
    print('[screenshots] %s' % msg, flush=True)


# ---------------------------------------------------------------- 演示数据

NOTES = [   # (标题, 笔记本, 标签, 置顶, 正文)；先建的排在列表后面（按更新时间倒序）
    ('随手记', None, (), False, '''
- 周五前把发票交给财务
- 试试番茄工作法：25 分钟专注 + 5 分钟休息
'''),
    ('番茄牛腩', '生活', (), False, '''
## 材料
- 牛腩 500 克，番茄 3 个，洋葱半个

## 做法
1. 牛腩切块，冷水下锅焯水
2. 番茄去皮切块，一半先炒出汁
3. 加牛腩和热水，小火炖 90 分钟
4. 放剩下的番茄，再炖 15 分钟，盐调味
'''),
    ('读书清单', '学习', ('灵感',), False, '''
- [x] 《设计心理学》
- [ ] 《写给大家看的设计书》
- [ ] 《人月神话》

> 每读完一本，写三句话的读后感。
'''),
    ('发布检查清单', '工作', ('进行中',), False, '''
- [x] 版本号和更新日志对得上
- [ ] 在干净的系统上装一遍，能启动
- [ ] 截图换成演示数据
'''),
    ('京都五日行程', '生活', ('灵感',), False, '''
- **第一天**　清水寺 → 二年坂 → 祇园
- **第二天**　伏见稻荷大社，七点前出发避开人流
- **第三天**　岚山竹林、渡月桥
- **第四天**　金阁寺 → 龙安寺
- **第五天**　锦市场买伴手礼

## 预算

| 项目 | 金额 |
| --- | --- |
| 住宿 | ¥2,400 |
| 交通 | ¥600 |
| 门票 | ¥320 |
'''),
    ('Markdown 速查', '学习', (), False, r'''
> 写起来是纯文本，看起来是排好版的文章。

## 文字

**加粗**、*斜体*、~~删除线~~、`行内代码`，还有[链接](https://example.com)。

## 公式

质能方程 $E = mc^2$，前 n 个正整数的和：

$$
\sum_{k=1}^{n} k = \frac{n(n+1)}{2}
$$

## 代码

```python
def greet(name):
    return f"你好，{name}！"
```

## 表格

| 快捷键 | 作用 |
| --- | --- |
| Ctrl + B | 加粗 |
| Ctrl + K | 插入链接 |
| Ctrl + P | 跳到任意笔记 |
'''),
    ('第 47 周项目周会', '工作', ('重要', '进行中'), True, '''
---
状态: 进行中
负责人: 小林
截止: 2026-11-28
---
## 本周进展

- 登录页改版上线，注册转化率提升 **12%**
- 导出模块重写完成，进入测试

## 待办

- [x] 整理上周的用户反馈 📅 2026-11-20
- [ ] 补导出功能的回归测试 📅 2026-11-25
- [ ] 和设计确认深色主题的配色 📅 2026-11-26

## 数据

| 指标 | 上周 | 本周 |
| --- | --- | --- |
| 日活 | 1,240 | 1,386 |
| 平均停留 | 6.2 分钟 | 6.9 分钟 |

下一步见 [[发布检查清单]]。
'''),
]


def make_background(path):
    """柔和的抽象渐变 + 几团虚化的光斑（生成的，没有版权问题）"""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = 1920, 1200
    top, bottom = (214, 226, 210), (244, 236, 222)
    img = Image.new('RGB', (w, h))
    for y in range(h):
        t = y / (h - 1)
        img.paste(tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)), (0, y, w, y + 1))
    glow = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    rnd = random.Random(7)
    palette = [(255, 250, 235, 120), (196, 214, 186, 140), (236, 214, 196, 120), (210, 222, 236, 110)]
    for _ in range(14):
        r = rnd.randint(140, 420)
        cx, cy = rnd.randint(-100, w + 100), rnd.randint(-100, h + 100)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=rnd.choice(palette))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    Image.alpha_composite(img.convert('RGBA'), glow).convert('RGB').save(path, quality=92)


def make_demo_data(data_dir):
    """在一个全新的目录里建演示库（单独的进程里跑：后端导入时就按环境变量定下数据目录）"""
    os.environ['MYNOTEPAD_DATA_DIR'] = data_dir
    sys.path.insert(0, ROOT)
    import backend
    assert os.path.normcase(backend.DATA_DIR) == os.path.normcase(data_dir), backend.DATA_DIR
    api = backend.api
    bg = os.path.join(data_dir, 'backgrounds', 'demo-background.jpg')
    os.makedirs(os.path.dirname(bg), exist_ok=True)
    make_background(bg)
    notebooks = {name: api.notebooks_create(name)['id'] for name in ('工作', '学习', '生活')}
    tags = {name: api.tags_create(name, color)['id']
            for name, color in (('重要', '#B5685A'), ('进行中', '#7D8A6E'), ('灵感', '#6E7FA0'))}
    for title, notebook, tag_names, pinned, content in NOTES:
        note = api.notes_create(notebooks.get(notebook))
        fields = {'title': title, 'content': content.strip() + '\n'}
        if pinned:
            fields['is_pinned'] = 1
        if title == '京都五日行程':
            fields.update(bg_type='image', bg_value=bg)
        api.notes_update(note['id'], fields)
        if tag_names:
            api.note_tags_set(note['id'], [tags[t] for t in tag_names])
    api.settings_set('tray_enabled', '0')     # 结束进程时不留托盘图标
    api.settings_set('theme', 'white')
    backend.conn.commit()


# ---------------------------------------------------------------- 最小的 CDP 客户端

class Cdp:
    """本机 CDP 的 WebSocket（RFC 6455）：只有文本帧、客户端帧加掩码，够用就好"""

    def __init__(self, url):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(('GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                           'Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n'
                           % (u.path, u.hostname, u.port, key)).encode())
        self.buf = bytearray()
        while b'\r\n\r\n' not in self.buf:
            self._fill()
        head, _, rest = bytes(self.buf).partition(b'\r\n\r\n')
        if b' 101 ' not in head.split(b'\r\n', 1)[0]:
            raise ConnectionError('CDP 握手失败：%r' % head[:80])
        self.buf = bytearray(rest)
        self.next_id = 0

    def _fill(self):
        chunk = self.sock.recv(1 << 20)
        if not chunk:
            raise ConnectionError('CDP 连接断了')
        self.buf += chunk

    def _take(self, n):
        while len(self.buf) < n:
            self._fill()
        data = bytes(self.buf[:n])
        del self.buf[:n]
        return data

    def _send(self, opcode, payload):
        n, mask = len(payload), os.urandom(4)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack('>H', n)
        else:
            head += bytes([0x80 | 127]) + struct.pack('>Q', n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def _message(self):
        parts = []
        while True:
            b0, b1 = self._take(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack('>H', self._take(2))[0]
            elif n == 127:
                n = struct.unpack('>Q', self._take(8))[0]
            mask = self._take(4) if b1 & 0x80 else None
            data = self._take(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise ConnectionError('CDP 连接被关掉了')
            if opcode == 0x9:                       # ping
                self._send(0xA, data)
                continue
            if opcode == 0xA:                       # pong
                continue
            parts.append(data)
            if b0 & 0x80:
                return b''.join(parts).decode('utf-8')

    def call(self, method, **params):
        self.next_id += 1
        self._send(0x1, json.dumps({'id': self.next_id, 'method': method, 'params': params}).encode('utf-8'))
        while True:
            msg = json.loads(self._message())
            if msg.get('id') == self.next_id:
                if 'error' in msg:
                    raise RuntimeError('%s: %s' % (method, msg['error']))
                return msg.get('result', {})

    def js(self, expr):
        r = self.call('Runtime.evaluate', expression=expr, awaitPromise=True, returnByValue=True)
        if 'exceptionDetails' in r:
            raise RuntimeError('页面脚本出错：%s' % json.dumps(r['exceptionDetails'], ensure_ascii=False)[:300])
        return r['result'].get('value')

    def wait(self, expr, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.js('!!(%s)' % expr):
                    return
            except RuntimeError:
                pass
            time.sleep(0.2)
        raise TimeoutError('等不到：%s' % expr)


def page_ws_url():
    end = time.time() + 60
    while time.time() < end:
        try:
            with urllib.request.urlopen('http://127.0.0.1:%d/json/list' % PORT, timeout=2) as r:
                for target in json.load(r):
                    if target.get('type') == 'page' and 'index.html' in target.get('url', ''):
                        return target['webSocketDebuggerUrl']
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError('60 秒内没连上应用的 CDP 端口 %d' % PORT)


# ---------------------------------------------------------------- 拍

SETTLE = """(async () => {
  await document.fonts.ready;
  for (let i = 0; i < 50 && document.getAnimations().length; i++) await new Promise(r => setTimeout(r, 100));
  await new Promise(r => setTimeout(r, 600));
})()"""

SHOTS = [   # (文件名, 主题, 笔记, 视图)
    ('main', 'white', '第 47 周项目周会', 'preview'),
    ('markdown', 'white', 'Markdown 速查', 'split'),
    ('dark', 'dark', '第 47 周项目周会', 'split'),
    ('background', 'white', '京都五日行程', 'preview'),
]


def shoot_all(cdp, note_ids):
    import io

    from PIL import Image
    cdp.call('Emulation.setDeviceMetricsOverride', width=VIEW_W, height=VIEW_H, deviceScaleFactor=SCALE, mobile=False)
    cdp.wait('window.__app && window.pywebview && window.pywebview.api && window.pywebview.api.notes_list')
    cdp.wait("document.querySelectorAll('#note-list [data-note-id]').length === %d" % len(NOTES), timeout=30)
    try:
        cdp.wait("document.getElementById('startup-notice')", timeout=8)
        cdp.js("document.querySelector('#startup-notice button').click()")
        cdp.wait("!document.getElementById('startup-notice')"
                 " || !document.getElementById('startup-notice').getClientRects().length", timeout=5)
    except TimeoutError:
        pass
    for name, theme, title, view in SHOTS:
        if cdp.js('document.body.dataset.theme') != theme:
            cdp.js("document.querySelector('.theme-option[data-theme=\"%s\"]').click()" % theme)
            cdp.wait("document.body.dataset.theme === '%s'" % theme)
        nid = note_ids[title]
        cdp.js("document.querySelector('[data-note-id=\"%s\"]').click()" % nid)
        cdp.wait("__app.state.activeNoteId === '%s'" % nid)
        cdp.js("document.querySelector('button[data-md-view=\"%s\"]').click()" % view)
        cdp.wait("document.body.dataset.mdView === '%s'" % view)
        cdp.js('document.activeElement && document.activeElement.blur && document.activeElement.blur()')
        cdp.call('Input.dispatchMouseEvent', type='mouseMoved', x=VIEW_W - 1, y=VIEW_H - 1)   # 别留 :hover
        cdp.js(SETTLE)
        png = base64.b64decode(cdp.call('Page.captureScreenshot', format='png')['data'])
        img = Image.open(io.BytesIO(png)).convert('RGB')
        assert img.size == (VIEW_W * SCALE, VIEW_H * SCALE), img.size
        path = os.path.join(OUT, name + '.png')
        if name == 'background':
            img.save(path, optimize=True)
        else:
            img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(path, optimize=True)
        log('%s.png  %d KB' % (name, os.path.getsize(path) // 1024))


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--make-data':
        make_demo_data(sys.argv[2])
        return 0
    if os.path.exists(DEMO):
        shutil.rmtree(DEMO)
    os.makedirs(DEMO)
    log('建演示库：%s' % DEMO)
    subprocess.run([sys.executable, os.path.abspath(__file__), '--make-data', DEMO], check=True)
    import sqlite3
    with sqlite3.connect(os.path.join(DEMO, 'notes.db')) as db:
        note_ids = {title: nid for nid, title in db.execute('SELECT id, title FROM notes')}
    env = dict(os.environ, MYNOTEPAD_DATA_DIR=DEMO,
               WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS='--remote-debugging-port=%d' % PORT)
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, 'app.pyw')], cwd=ROOT, env=env)
    try:
        shoot_all(Cdp(page_ws_url()), note_ids)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        for _ in range(30):                  # 刚写过的库可能还被杀毒软件扫着（实测 10 秒不够），稍等再删
            try:
                shutil.rmtree(DEMO)
                break
            except OSError:
                time.sleep(1)
        log('演示库%s' % ('已删除' if not os.path.exists(DEMO) else '没删掉，请手动删：' + DEMO))
    log('完成。社交预览图用的是 markdown.png：python docs/make_social_preview.py')
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
