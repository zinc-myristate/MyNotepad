"""
我的记事本 - 主程序入口
"""
import os, sys

# 在 import webview 之前，猴子补丁 subprocess 以抑制所有子进程控制台窗口
if sys.platform == 'win32':
    import subprocess as _subprocess
    _orig_popen = _subprocess.Popen
    def _popen_nowindow(*args, **kwargs):
        if 'startupinfo' not in kwargs:
            si = _subprocess.STARTUPINFO()
            si.dwFlags |= _subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            kwargs['startupinfo'] = si
        kwargs.setdefault('creationflags', 0)
        kwargs['creationflags'] |= 0x08000000  # CREATE_NO_WINDOW
        return _orig_popen(*args, **kwargs)
    _subprocess.Popen = _popen_nowindow

import webview
# 必须在**模块级**导入 filedialog 子模块：`import tkinter.messagebox` 不会把 filedialog 挂到
# tkinter 上，而下面 pick_image_file / pick_attachment_file / pick_background_file 以及
# AppApi.pick_and_preview_icon 都用 `tkinter.filedialog.xxx` 这种**属性访问**（名字没被用到），
# 于是它很容易被当成"未使用导入"删掉 —— 156c20f 那一轮就是这么删的，后果是
# 插入图片 / 插入附件 / 更换图标 / 选择背景 四个功能全部静默失效（点了没反应，
# 异常只落在 error.log：AttributeError: module 'tkinter' has no attribute 'filedialog'）。
# 删之前请先看 tests/test_module_imports.py —— 它会直接判定失败。
import tkinter.filedialog
import tkinter.messagebox
import desktop          # 托盘常驻 / 开机自启 / 提醒守护（见 desktop.py）
import json
import base64
import ocr              # 图片文字识别（Windows 内置 OCR，见 ocr.py）
import tempfile
import time

# 打包版的 OCR 自检：`MyNotepad.exe --ocr-selftest <图片> [语言] [--out 结果.json]`。
# 必须在**创建窗口之前**处理并退出——这条路的用途正是"窗口起不来/点了没反应时"排查
# 系统 OCR 语言包与 winrt 运行时到底在不在。
if '--ocr-selftest' in sys.argv:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import ocr
    raise SystemExit(ocr.selftest_from_argv(sys.argv))

# PyInstaller 兼容
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS  # bundle 内的静态文件
    EXE_DIR = os.path.dirname(sys.executable)  # exe 所在目录（数据存这里）
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = BASE_DIR

# ====== 打包版的 Tcl/Tk 自检：`MyNotepad.exe --tk-selftest [--out 结果.json]` ======
# 为什么要有：`tkinter.filedialog`（选图片 / 选附件 / 选背景）、`tkinter.messagebox`（确认框）、
# `tkinter.Tk()`（图标处理）全靠产物里的 `_tk_data`（Tk 的脚本库 tk.tcl + ttk/）。
# 2026-09-27 发出去过一个 `_tk_data` 里装的是 **Tcl 库**的包：主界面照常能用，
# 一点「选择图片」才报 Tk 错 —— 而当时的静态闸门被宽松标记（`tclIndex`）骗过了。
# 所以这里在**冻结产物里**真起一个 Tk 解释器，把"数据在不在 + 到底能不能用"一次问清；
# 结果同时写文件（打包版是窗口模式，没有控制台可看）。
TK_DATA_MARKERS = ('tk.tcl', os.path.join('ttk', 'ttk.tcl'))


def tk_selftest():
    """Tcl/Tk 自检报告（纯数据，不抛异常 —— 它是 CI 的冒烟闸门）。"""
    info = {'frozen': bool(getattr(sys, 'frozen', False)),
            'tk_data_ok': False, 'tk_ok': False, 'filedialog': False, 'messagebox': False}
    for base in (getattr(sys, '_MEIPASS', None), EXE_DIR, os.path.join(EXE_DIR, '_internal')):
        if not base:
            continue
        d = os.path.join(base, '_tk_data')
        if not os.path.isdir(d):
            continue
        info['tk_data'] = d
        info['tk_data_top'] = sorted(os.listdir(d))[:16]
        info['tk_data_markers'] = [m for m in TK_DATA_MARKERS
                                   if os.path.exists(os.path.join(d, m))]
        info['tk_data_ok'] = bool(info['tk_data_markers'])
        break
    try:
        import tkinter
        info['tcl_version'] = tkinter.TclVersion
        info['tk_version'] = tkinter.TkVersion
        # 真起一个 Tk：tk.tcl 缺失（或 `_tk_data` 装成 Tcl 库）时这里就抛。
        # ⚠️ 起两次：同一个进程里"建 root → destroy → 再建"偶发过一次 TclError
        # （tkinter 的 `_default_root` 被上一次的 filedialog 临时 root 占着时最明显），
        # 而 CI 闸门**不能偶发**。数据不对是确定性失败，重试一次照样报错。
        for attempt in (1, 2):
            try:
                root = tkinter.Tk()
                root.withdraw()
                info['tk_patchlevel'] = root.tk.call('info', 'patchlevel')
                root.destroy()
                info['tk_ok'] = True
                info['tk_attempts'] = attempt
                info.pop('tk_error', None)   # 第一次失败第二次成功时，别留下过期的错
                break
            except Exception as exc:                              # noqa: BLE001
                info['tk_error'] = '%s: %s' % (type(exc).__name__, exc)
                if attempt == 2:
                    raise
    except Exception:                                             # noqa: BLE001
        pass
    try:
        import tkinter.filedialog                                 # noqa: F401
        import tkinter.messagebox                                 # noqa: F401
        info['filedialog'] = hasattr(tkinter, 'filedialog')
        info['messagebox'] = hasattr(tkinter, 'messagebox')
    except Exception as exc:                                      # noqa: BLE001
        info['dialog_error'] = '%s: %s' % (type(exc).__name__, exc)
    return info


def tk_selftest_from_argv(argv):
    """解析 `--tk-selftest [--out 结果.json]`，返回进程退出码（0 = 数据与 Tk 都真的可用）。"""
    args = list(argv)
    out_path = None
    if '--out' in args:
        i = args.index('--out')
        out_path = args[i + 1] if i + 1 < len(args) else None
    payload = tk_selftest()
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if out_path:
        try:
            with open(out_path, 'w', encoding='utf-8') as fh:
                fh.write(text)
        except OSError:
            pass
    try:                       # 窗口模式的打包版没有控制台，print 可能没有去处
        print(text)
    except Exception:                                         # noqa: BLE001
        pass
    return 0 if (payload['tk_data_ok'] and payload['tk_ok']) else 1


if '--tk-selftest' in sys.argv:
    raise SystemExit(tk_selftest_from_argv(sys.argv))

# ====== 文件对话框辅助函数 ======
def pick_image_file():
    """打开图片选择对话框"""
    path = tkinter.filedialog.askopenfilename(
        title="选择图片",
        filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.bmp *.webp *.svg")]
    )
    return path if path else None

def pick_attachment_file():
    """打开附件选择对话框"""
    path = tkinter.filedialog.askopenfilename(
        title="选择附件"
    )
    return path if path else None

def pick_background_file():
    """打开背景图片选择对话框"""
    path = tkinter.filedialog.askopenfilename(
        title="选择背景图片",
        filetypes=[("图片文件", "*.png *.jpg *.jpeg *.gif *.bmp *.webp")]
    )
    return path if path else None

def show_confirm_dialog(message, title="确认操作"):
    """显示确认对话框"""
    # 隐藏主窗口避免对话框被遮挡
    try:
        window = webview.windows[0]
        window.minimize()
    except Exception:
        pass

    result = tkinter.messagebox.askyesno(title, message)

    try:
        window.restore()
    except Exception:
        pass

    return result

def show_error_dialog(message, title="错误"):
    """显示错误对话框"""
    tkinter.messagebox.showerror(title, message)
    return True

# ====== 图标图像处理（智能裁切 + Win11 风格大圆角） ======
ICON_SIZE = 512
DEFAULT_ICON_RADIUS_PCT = 15  # 圆角半径 = 边长 ×15%，参照 Win11 应用图标

def _icon_temp_dir():
    import tempfile
    d = os.path.join(tempfile.gettempdir(), "mynotepad_icons")
    os.makedirs(d, exist_ok=True)
    return d

def _validate_icon_temp(temp_path):
    """临时文件路径白名单校验：必须是 mynotepad_icons 目录内的 PNG，防任意路径读/删"""
    try:
        real = os.path.realpath(temp_path)
        if os.path.dirname(real) != os.path.realpath(_icon_temp_dir()):
            return None
        if not real.lower().endswith('.png') or not os.path.isfile(real):
            return None
        return real
    except Exception:
        return None

def _smart_square_crop(img):
    """自适应正方形裁切：OpenCV 谱残差显著性 + 人脸加权定位主体；cv2 不可用时降级为居中裁切"""
    from PIL import Image
    w, h = img.size
    size = min(w, h)
    center_box = ((w - size) // 2, (h - size) // 2, (w - size) // 2 + size, (h - size) // 2 + size)
    if w == h:
        return img
    try:
        import cv2
        import numpy as np
    except Exception:
        return img.crop(center_box)
    try:
        # 降采样加速：长边缩到 ≤512 再分析
        scale = max(w, h) / 512 if max(w, h) > 512 else 1
        sw, sh = max(1, round(w / scale)), max(1, round(h / scale))
        small = np.asarray(img.convert("RGB").resize((sw, sh), Image.BILINEAR))
        bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
        # 谱残差显著性图
        ok, salmap = cv2.saliency.StaticSaliencySpectralResidual_create().computeSaliency(bgr)
        if not ok or salmap is None or float(salmap.sum()) < 1e-6:
            return img.crop(center_box)
        salmap = salmap.astype(np.float64)
        # 人脸加权（可选增强：XML 缺失或检测失败时静默跳过）
        try:
            cascade_xml = os.path.join(BASE_DIR, "resources", "haarcascade_frontalface_default.xml")
            if os.path.isfile(cascade_xml):
                cascade = cv2.CascadeClassifier(cascade_xml)
                if not cascade.empty():
                    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
                    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
                    for (x, y, fw, fh) in faces:
                        salmap[y:y+fh, x:x+fw] += float(salmap.max()) * 2.0  # 人脸权重压过背景纹理
        except Exception:
            pass
        # 积分图 + 沿长轴滑窗，取显著性积分最大的正方形窗口
        integ = cv2.integral(salmap)
        win = min(sw, sh)
        travel = max(sw, sh) - win
        step = max(1, travel // 64)

        def window_score(off):
            x1, y1 = (off, 0) if sw > sh else (0, off)
            x2, y2 = x1 + win, y1 + win
            return integ[y2, x2] - integ[y1, x2] - integ[y2, x1] + integ[y1, x1]

        best_off, best_score = 0, float('-inf')
        for off in range(0, travel + 1, step):
            s = window_score(off)
            if s > best_score:
                best_off, best_score = off, s
        # 纯色/低对比图退化保护：与居中窗口得分差 <2% 时保持居中
        center_score = window_score(travel // 2)
        if best_score - center_score < 0.02 * max(center_score, 1e-6):
            best_off = travel // 2
        # 映射回原图坐标
        long_side = max(w, h)
        orig_off = min(max(0, round(best_off * (long_side / max(sw, sh)))), long_side - size)
        box = (orig_off, 0, orig_off + size, size) if w > h else (0, orig_off, size, orig_off + size)
        return img.crop(box)
    except Exception:
        return img.crop(center_box)

def _apply_rounded_corners(img, radius_pct):
    """RGBA 正方形图做大圆角：4× 超采样蒙版抗锯齿，圆角外完全透明，保留原图自身 alpha"""
    from PIL import Image, ImageDraw, ImageChops
    radius_pct = max(0.0, min(50.0, float(radius_pct)))
    side = img.size[0]
    radius_px = round(side * radius_pct / 100)
    if radius_px <= 0:
        return img.copy()
    ss = 4  # 超采样倍数：大图画圆角再缩回，边缘平滑无锯齿
    big = Image.new("L", (side * ss, side * ss), 0)
    ImageDraw.Draw(big).rounded_rectangle([0, 0, side * ss - 1, side * ss - 1], radius=radius_px * ss, fill=255)
    mask = big.resize((side, side), Image.LANCZOS)
    out = img.copy()
    out.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    return out

def _icon_preview_b64(img):
    """生成 128×128 预览 base64 dataURL"""
    from PIL import Image
    import io, base64 as b64
    buf = io.BytesIO()
    img.resize((128, 128), Image.LANCZOS).save(buf, "PNG")
    return "data:image/png;base64," + b64.b64encode(buf.getvalue()).decode("utf-8")


# ====== 导入后端 API ======
from backend import api as backend_api
import backend as _backend_mod

# 启动自检：数据库完整性检查失败立即退出（窗口未创建，退出干净），提示从备份恢复
if not _backend_mod.check_integrity():
    show_error_dialog("数据库完整性检查失败，为避免数据损坏，程序将退出。\n可尝试从 data/backups/ 恢复最近备份。")
    sys.exit(1)

# 图片外置迁移（幂等，失败不阻塞启动；premig- 快照先于写库，可一键回滚）
try:
    _migrated = _backend_mod.migrate_images()
    if _migrated:
        print(f"图片外置迁移完成：{_migrated} 行（快照见 data/backups/premig-*）")
except Exception:
    import applog
    applog.get_logger().exception("图片外置迁移失败")

# 启动后台线程：先清理回收站超期笔记（30 天），再定期备份（24h 判定 + 滚动 7 份，不拖慢启动）
import threading as _threading

def _startup_maintenance():
    try:
        _backend_mod.purge_expired_trash()
    except Exception:
        import applog
        applog.get_logger().exception("回收站超期清理失败")
    # 空间回收放在备份之后：先落一份页级一致的备份，再压缩库（见 reclaim_space 文档）
    _backend_mod.backup_database()
    try:
        _backend_mod.reclaim_space()
    except Exception:
        import applog
        applog.get_logger().exception("数据库空间回收失败")

_threading.Thread(target=_startup_maintenance, daemon=True).start()


# ====== 开发态数据目录提示 ======
# 为什么需要：数据目录跟着「谁在运行」走——打包版读 dist/MyNotepad/data/，源码运行读仓库 data/。
# 两个目录会各自积累笔记，于是直接 `python app.pyw` 时可能打开**另一本笔记更少的记事本**，
# 那一瞬间非常像"笔记全丢了"。这里只做提示，不改行为（改行为会让开发态动到真实数据）。
# 用只读 URI 打开候选库，绝不因为探测而创建文件。
def _build_startup_notice():
    try:
        cur_dir = _backend_mod.DATA_DIR
        cur_n = _backend_mod.count_notes(_backend_mod.DB_PATH) or 0
        parts = []
        if not getattr(sys, 'frozen', False):
            parts.append('开发模式：当前数据目录 %s（%d 篇）。' % (cur_dir, cur_n))
        # 另一处常见的数据目录：打包版的 dist/MyNotepad/data
        other = os.path.join(BASE_DIR, 'dist', 'MyNotepad', 'data', 'notes.db')
        if os.path.abspath(other) != os.path.abspath(_backend_mod.DB_PATH) and os.path.exists(other):
            other_n = _backend_mod.count_notes(other)
            if other_n is not None and other_n > cur_n:
                parts.append('注意：打包版数据 %s 里有 %d 篇（比当前多）——'
                             '快捷方式/启动.vbs 用的是那一份。' % (os.path.dirname(other), other_n))
        return ' '.join(parts)
    except Exception:
        return ''


_STARTUP_NOTICE = _build_startup_notice()

# 扩展 API，添加文件对话框功能
class AppApi:
    def __init__(self, backend):
        self.backend = backend

    # 笔记操作（代理到 backend）
    # 第 12 轮：笔记本范围。列表与创建都带 notebook_id（None/'省略' = 全量/未分类），
    # 少了这个参数，前端传了也是 TypeError —— 笔记照样混在一起。
    def notes_list(self, notebook_id=None):
        return self.backend.notes_list(notebook_id)
    def notebook_counts(self):
        return self.backend.notebook_counts()
    def notes_created_on(self, date_str):
        return self.backend.notes_created_on(date_str)
    def notes_get(self, note_id, unlocked=False): return self.backend.notes_get(note_id, unlocked)
    def notes_create(self, notebook_id=None):
        return self.backend.notes_create(notebook_id)
    def notes_duplicate(self, note_id): return self.backend.notes_duplicate(note_id)
    # 第 7 轮：派生指标 / 跨笔记待办 / 标签管理 / 保存的搜索
    def note_metrics(self, note_id): return self.backend.note_metrics(note_id)
    def metrics_bulk(self, note_ids=None): return self.backend.metrics_bulk(note_ids)
    def todos_list(self, scope='open'): return self.backend.todos_list(scope)
    def todo_toggle(self, note_id, idx, expected_text=''):
        return self.backend.todo_toggle(note_id, idx, expected_text)
    def tags_rename(self, tag_id, new_name): return self.backend.tags_rename(tag_id, new_name)
    def tags_merge(self, src_id, dst_id): return self.backend.tags_merge(src_id, dst_id)
    def saved_searches_list(self): return self.backend.saved_searches_list()
    def saved_search_create(self, name, query):
        return self.backend.saved_search_create(name, query)
    def saved_search_update(self, sid, fields):
        return self.backend.saved_search_update(sid, fields)
    def saved_search_delete(self, sid): return self.backend.saved_search_delete(sid)
    # 第 9 轮：双链 / 属性 / 表格视图
    def note_links(self, note_id): return self.backend.note_links(note_id)
    def notes_resolve_link(self, title): return self.backend.notes_resolve_link(title)
    def notes_create_from_link(self, title, notebook_id=None):
        return self.backend.notes_create_from_link(title, notebook_id)
    def notes_table(self, note_ids=None): return self.backend.notes_table(note_ids)

    # 第 10 轮：模板 / 每日笔记 / 快速捕获
    def templates_list(self): return self.backend.templates_list()
    def template_create(self, name, content=''): return self.backend.template_create(name, content)
    def template_get(self, template_id): return self.backend.template_get(template_id)
    def template_update(self, template_id, fields):
        return self.backend.template_update(template_id, fields)
    def template_delete(self, template_id): return self.backend.template_delete(template_id)
    def template_render(self, template_id, title=''):
        return self.backend.template_render(template_id, title)
    def daily_note_open(self): return self.backend.daily_note_open()
    def capture_text(self, text, notebook_name=None):
        return self.backend.capture_text(text, notebook_name)
    def capture_image(self, src_path, title=None, body=None):
        return self.backend.capture_image(src_path, title, body)

    def clipboard_capture(self):
        """读系统剪贴板文本并捕获成笔记（剪贴板只在 Python 侧读，前端不碰）"""
        text = ''
        try:
            import tkinter
            root = tkinter.Tk()
            root.withdraw()
            try:
                text = root.clipboard_get()
            finally:
                root.destroy()
        except Exception:
            text = ''
        return self.backend.capture_text(text) if (text or '').strip() else None

    def notes_create_from_template(self, template_id, title=None, notebook_name=None, notebook_id=None):
        return self.backend.notes_create_from_template(template_id, title, notebook_name, notebook_id)

    # 第 10 轮：截图选区覆盖窗 / 迷你捕获窗（实现在本文件下方，见「捕获窗口」一节）
    def capture_begin(self, note_id=None): return _capture_begin(note_id)
    def capture_overlay_info(self, viewport_w=None, viewport_h=None):
        return _capture_overlay_info(viewport_w, viewport_h)
    def capture_overlay_ready(self): return _capture_overlay_ready()
    def capture_commit(self, action, rect=None, viewport=None):
        return _capture_commit(action, rect, viewport)
    def capture_mini_open(self): return _mini_open()
    def capture_mini_submit(self, text, keep_open=False):
        return _mini_submit(text, keep_open)
    def capture_mini_close(self): return _mini_close()

    # 第 11 轮：图片文字识别（OCR）。识别本身在 ocr.py，这里只做桥接与临时文件管理
    def ocr_languages(self): return ocr.languages_info()
    def ocr_recognize(self, path, lang=None): return ocr.recognize(path, lang)
    def ocr_pick_image(self): return pick_image_file()
    def ocr_clipboard_image(self): return _ocr_clipboard_image()
    def ocr_release(self, path): return _ocr_release(path)

    def export_table_csv(self, note_ids=None):
        """导出表格为 CSV（对话框在这一层，写文件在后端——与其它导出同一条规矩）"""
        try:
            import tkinter.filedialog
            from datetime import datetime
            save_path = tkinter.filedialog.asksaveasfilename(
                title="导出表格为 CSV",
                defaultextension='.csv',
                filetypes=[('CSV 表格', '*.csv')],
                initialfile=f"笔记表格-{datetime.now():%Y%m%d-%H%M%S}.csv")
            if not save_path:
                return None
            return self.backend.export_table_csv(note_ids, save_path)
        except Exception as exc:
            applog.get_logger().exception("导出 CSV 失败")
            return {'error': str(exc)}

    # 正文格式（Delta ↔ Markdown）双轨：查询状态 / 转换 / 还原原始富文本
    def note_format_info(self, note_id): return self.backend.note_format_info(note_id)
    def convert_note_format(self, note_id, target):
        return self.backend.convert_note_format(note_id, target)
    def restore_delta_backup(self, note_id): return self.backend.restore_delta_backup(note_id)
    def notes_delete_many(self, note_ids): return self.backend.notes_delete_many(note_ids)
    def notes_move_many(self, note_ids, notebook_id=None):
        return self.backend.notes_move_many(note_ids, notebook_id)
    def notes_add_tag_many(self, note_ids, tag_id):
        return self.backend.notes_add_tag_many(note_ids, tag_id)
    def notes_update(self, note_id, fields): return self.backend.notes_update(note_id, fields)
    def notes_delete(self, note_id): return self.backend.notes_delete(note_id)
    def notes_trash_list(self): return self.backend.notes_trash_list()
    def notes_restore(self, note_id): return self.backend.notes_restore(note_id)
    def notes_purge(self, note_id): return self.backend.notes_purge(note_id)
    def notes_purge_all(self): return self.backend.notes_purge_all()
    def notes_search(self, query): return self.backend.notes_search(query)

    # 附件操作
    def attachments_list(self, note_id): return self.backend.attachments_list(note_id)
    def attachments_get_path(self, note_id, filename): return self.backend.attachments_get_path(note_id, filename)

    # 文件操作
    def file_copy_to_note(self, source_path, note_id, file_type):
        return self.backend.file_copy_to_note(source_path, note_id, file_type)
    def file_open(self, file_path):
        return self.backend.file_open(file_path)

    # 文件对话框
    def pick_image(self): return pick_image_file()
    def pick_attachment(self): return pick_attachment_file()
    def pick_background(self):
        path = pick_background_file()
        if not path:
            return None
        # 将图片复制到 data/backgrounds/ 目录，确保重启后 read_file_base64 白名单通过
        import base64, os, uuid
        try:
            bg_dir = os.path.join(EXE_DIR, "data", "backgrounds")
            os.makedirs(bg_dir, exist_ok=True)
            ext = os.path.splitext(path)[1].lower()
            if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
                ext = '.png'
            # 用 uuid 生成唯一文件名，防止冲突
            copy_name = str(uuid.uuid4())[:8] + ext
            copy_path = os.path.join(bg_dir, copy_name)
            import shutil
            shutil.copy2(path, copy_path)
            # 使用副本路径作为 bg_value（在 read_file_base64 白名单内）
            saved_path = copy_path
        except Exception:
            # 复制失败时回退到原路径
            saved_path = path

        try:
            with open(saved_path, 'rb') as f:
                data = base64.b64encode(f.read()).decode('utf-8')
            ext2 = os.path.splitext(saved_path)[1].lower()
            mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                        '.gif': 'image/gif', '.bmp': 'image/bmp', '.webp': 'image/webp'}
            mime = mime_map.get(ext2, 'image/png')
            return {'path': saved_path, 'dataUri': 'data:' + mime + ';base64,' + data}
        except Exception:
            return None

    # 设置
    def settings_get(self, key): return self.backend.settings_get(key)
    def settings_set(self, key, value): return self.backend.settings_set(key, value)
    def settings_get_all(self): return self.backend.settings_get_all()

    # 对话框
    def confirm(self, message, title="确认操作"):
        return show_confirm_dialog(message, title)

    def show_error(self, message, title="错误"):
        return show_error_dialog(message, title)

    # 获取数据目录（用于加载本地图片）
    def get_data_dir(self):
        # 与后端一致：frozen 模式下 __file__ 在 bundle 内，不能据此拼数据目录
        return _backend_mod.DATA_DIR

    # 读取文件为 base64（用于在 WebView 中显示本地图片）
    def read_file_base64(self, file_path):
        return self.backend.read_file_base64(file_path)

    def save_temp_image(self, bytes_list, filename):
        return self.backend.save_temp_image(bytes_list, filename)

    # 打开新图标选择对话框
    # 标签
    def tags_list(self): return self.backend.tags_list()
    def tags_create(self, name, color='#7D8A6E'): return self.backend.tags_create(name, color)
    def tags_delete(self, tid): return self.backend.tags_delete(tid)
    def note_tags_get(self, note_id): return self.backend.note_tags_get(note_id)
    def note_tags_set(self, note_id, tag_ids): return self.backend.note_tags_set(note_id, tag_ids)
    def notes_by_tag(self, tag_id, notebook_id=None):
        return self.backend.notes_by_tag(tag_id, notebook_id)

    # 笔记本
    def notebooks_list(self): return self.backend.notebooks_list()
    def notebooks_create(self, name='新建笔记本'): return self.backend.notebooks_create(name)
    def notebooks_update(self, nid, fields): return self.backend.notebooks_update(nid, fields)
    def notebooks_delete(self, nid): return self.backend.notebooks_delete(nid)

    # 历史版本
    def versions_list(self, note_id): return self.backend.versions_list(note_id)
    def versions_get(self, vid, unlocked=False): return self.backend.versions_get(vid, unlocked)
    def versions_restore(self, vid, unlocked=False): return self.backend.versions_restore(vid, unlocked)
    def versions_create(self, note_id, title, content): return self.backend.versions_create(note_id, title, content)
    def versions_delete(self, vid): return self.backend.versions_delete(vid)
    def versions_delete_all(self, note_id): return self.backend.versions_delete_all(note_id)

    # 密码
    def note_set_password(self, note_id, password): return self.backend.note_set_password(note_id, password)
    def note_verify_password(self, note_id, password): return self.backend.note_verify_password(note_id, password)
    def note_change_password(self, note_id, old_password, new_password): return self.backend.note_change_password(note_id, old_password, new_password)
    def note_remove_password(self, note_id, password): return self.backend.note_remove_password(note_id, password)
    def note_has_password(self, note_id): return self.backend.note_has_password(note_id)
    def note_lock(self, note_id): return self.backend.note_lock(note_id)

    # 前端错误上报
    def log_error(self, message, stack='', source='js'): return self.backend.log_error(message, stack, source)

    def startup_notice(self):
        """启动提示（开发态数据目录 / 另一份数据更完整）。前端启动后展示，可关闭。"""
        return _STARTUP_NOTICE

    # ====== 桌面集成（托盘常驻 / 开机自启）======
    # 状态存在两处：tray_enabled 存 settings；开机自启以注册表为准（不另存副本）。
    def desktop_status(self):
        return {
            'tray': (self.backend.settings_get(TRAY_KEY) or '1') != '0',
            'autostart': desktop.autostart_enabled(),
            'autostart_supported': desktop.autostart_supported(),
            'hotkey': (self.backend.settings_get(HOTKEY_KEY) or '0') == '1',
            'hotkey_supported': sys.platform.startswith('win'),
            # 第 10 轮：迷你捕获窗的独立热键（Ctrl+Alt+S，同样默认关）
            'capture_hotkey': (self.backend.settings_get(CAPTURE_HOTKEY_KEY) or '0') == '1',
            'capture_hotkey_supported': sys.platform.startswith('win'),
        }

    def set_hotkey_enabled(self, enabled):
        self.backend.settings_set(HOTKEY_KEY, '1' if enabled else '0')
        apply = _HOTKEY_CTL.get('apply')
        if apply is not None:
            return apply(bool(enabled))     # 立即生效，返回是否真的注册上了
        return bool(enabled)

    def set_capture_hotkey_enabled(self, enabled):
        self.backend.settings_set(CAPTURE_HOTKEY_KEY, '1' if enabled else '0')
        apply = _CAPTURE_HOTKEY_CTL.get('apply')
        if apply is not None:
            return apply(bool(enabled))
        return bool(enabled)

    def set_tray_enabled(self, enabled):
        self.backend.settings_set(TRAY_KEY, '1' if enabled else '0')
        # 立即生效（不必重启）：由 app.pyw 底部注册的控制器起/停托盘与提醒守护
        apply = _TRAY_CTL.get('apply')
        if apply is not None:
            apply(bool(enabled))
        return bool(enabled)

    def set_autostart(self, enabled):
        """写注册表；返回操作后的真实状态（失败时可能与请求不同）"""
        return desktop.set_autostart(bool(enabled))

    # 提醒（兼容旧接口）
    def reminder_set(self, note_id, reminder_time): return self.backend.reminder_set(note_id, reminder_time)
    def reminder_cancel(self, note_id): return self.backend.reminder_cancel(note_id)
    def reminders_check(self): return self.backend.reminders_check()
    # 提醒（新接口）
    def reminder_create(self, note_id, content, remind_at, repeat_type='none', repeat_interval=1):
        return self.backend.reminder_create(note_id, content, remind_at, repeat_type, repeat_interval)
    def reminder_get(self, reminder_id): return self.backend.reminder_get(reminder_id)
    def reminder_update(self, reminder_id, fields): return self.backend.reminder_update(reminder_id, fields)
    def reminder_delete(self, reminder_id): return self.backend.reminder_delete(reminder_id)
    def reminder_list(self, note_id=None): return self.backend.reminder_list(note_id)
    def reminder_list_all(self): return self.backend.reminder_list_all()
    def reminder_complete(self, reminder_id): return self.backend.reminder_complete(reminder_id)
    def reminder_snooze(self, reminder_id, minutes): return self.backend.reminder_snooze(reminder_id, minutes)
    def reminder_update_next_repeat(self, reminder_id): return self.backend.reminder_update_next_repeat(reminder_id)

    # 导出笔记
    def export_note(self, title, html_content, format_type):
        """导出笔记，返回保存路径或 None"""
        import tkinter.filedialog
        ext_map = {
            'html': ('HTML 网页', '*.html'),
            'txt': ('纯文本', '*.txt'),
            'docx': ('Word 文档', '*.docx'),
            'xlsx': ('Excel 表格', '*.xlsx'),
        }
        desc, ext = ext_map.get(format_type, ('文件', '*.*'))
        save_path = tkinter.filedialog.asksaveasfilename(
            title=f"导出为 {desc}",
            defaultextension=ext,
            filetypes=[(desc, ext)],
            initialfile=f"{title}.{format_type}"
        )
        if not save_path:
            return None

        success = self.backend.export_note(title, html_content, format_type, save_path)
        if success:
            return save_path
        return None

    # PDF 导出（通过前端打印）
    def print_to_pdf(self):
        """触发前端打印（用户可在打印对话框中选择另存为 PDF)"""
        return True  # 前端通过 window.print() 处理

    # 一键全库导出（zip：notes.db + attachments + backgrounds）
    def export_all(self):
        import tkinter.filedialog
        from datetime import datetime
        save_path = tkinter.filedialog.asksaveasfilename(
            title="导出全部数据为备份包",
            defaultextension='.zip',
            filetypes=[('ZIP 压缩包', '*.zip')],
            initialfile=f"我的记事本备份-{datetime.now():%Y%m%d-%H%M%S}.zip"
        )
        if not save_path:
            return None
        if self.backend.export_all_to_zip(save_path):
            return save_path
        return None

    def export_markdown(self, note_id, title=''):
        """导出单篇为 Markdown（图片会复制到同级 .assets 目录）"""
        import tkinter.filedialog
        save_path = tkinter.filedialog.asksaveasfilename(
            title="导出为 Markdown",
            defaultextension='.md',
            filetypes=[('Markdown', '*.md')],
            initialfile='%s.md' % (title or '笔记'))
        if not save_path:
            return None
        if self.backend.export_note_markdown(note_id, save_path):
            return save_path
        return None

    def import_markdown_dialog(self, notebook_id=None):
        """选择 .md 文件导入为新笔记，返回新笔记或 None

        notebook_id：当前笔记本（第 12 轮）—— 在「原神」里导入一篇 .md，
        它就该落在原神里，而不是混进未分类。
        """
        import tkinter.filedialog
        path = tkinter.filedialog.askopenfilename(
            title="导入 Markdown", filetypes=[('Markdown', '*.md'), ('所有文件', '*.*')])
        if not path:
            return None
        try:
            with open(path, encoding='utf-8') as f:
                text = f.read()
        except Exception:
            try:
                with open(path, encoding='gbk', errors='replace') as f:
                    text = f.read()
            except Exception:
                return None
        return self.backend.import_markdown(text, None, notebook_id)

    def export_scope(self, notebook_id=None, tag_id=None, label=''):
        """按笔记本/标签导出为可当库打开的 zip。返回 (保存路径, 笔记数) 或 None"""
        import tkinter.filedialog
        from datetime import datetime
        stem = ('我的记事本-%s' % (label or ('笔记本' if notebook_id else '标签'))
                ).replace('/', '_').replace('\\', '_')
        save_path = tkinter.filedialog.asksaveasfilename(
            title="导出所选范围",
            defaultextension='.zip',
            filetypes=[('ZIP 压缩包', '*.zip')],
            initialfile=f"{stem}-{datetime.now():%Y%m%d-%H%M%S}.zip"
        )
        if not save_path:
            return None
        n = self.backend.export_notes_zip(save_path, notebook_id=notebook_id, tag_id=tag_id)
        if n is None:
            return None
        return {'path': save_path, 'count': n}

    def pick_and_preview_icon(self):
        """第一步：选择图片，智能裁切为 512×512 基准方图并生成默认圆角预览，不修改正式图标"""
        path = tkinter.filedialog.askopenfilename(
            title="选择新图标（推荐 512×512 正方形，PNG/JPG，≤10MB）",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp")]
        )
        if not path:
            return None
        try:
            fsize = os.path.getsize(path)
            if fsize > 10 * 1024 * 1024:
                return {"success": False, "error": "图片文件不能超过 10MB"}
        except Exception:
            return {"success": False, "error": "无法读取文件"}
        try:
            from PIL import Image
            import uuid
            Image.MAX_IMAGE_PIXELS = 100_000_000
            img = Image.open(path).convert("RGBA")
            w, h = img.size
            # 自适应裁切：显著性识别主体（cv2 缺失时降级居中）
            img = _smart_square_crop(img)
            base = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
            # 保存 RGBA 基准方图（不含圆角，供滑杆实时重渲染；不覆盖用户原图）
            temp_path = os.path.join(_icon_temp_dir(), str(uuid.uuid4())[:8] + ".png")
            base.save(temp_path, "PNG")
            # 默认圆角预览
            preview = _icon_preview_b64(_apply_rounded_corners(base, DEFAULT_ICON_RADIUS_PCT))
            return {
                "success": True,
                "preview": preview,
                "tempPath": temp_path,
                "origSize": f"{w}×{h}",
                "radiusPct": DEFAULT_ICON_RADIUS_PCT
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def update_icon_preview(self, temp_path, radius_pct):
        """滑杆实时调节：按新圆角半径重渲染预览"""
        real = _validate_icon_temp(temp_path)
        if not real:
            return {"success": False, "error": "无效的临时文件"}
        try:
            from PIL import Image
            base = Image.open(real).convert("RGBA")
            return {"success": True, "preview": _icon_preview_b64(_apply_rounded_corners(base, radius_pct))}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _update_shortcut(self, ico_path):
        try:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            lnk_path = os.path.join(desktop, "我的记事本.lnk")
            if os.path.exists(lnk_path):
                import subprocess, tempfile
                # 写入临时 UTF-8 ps1 文件，避免中文路径编码问题
                # 转义单引号防止 PowerShell 注入
                safe_lnk = lnk_path.replace("'", "''")
                safe_ico = ico_path.replace("'", "''")
                ps_cmd = (
                    f"$sc = (New-Object -ComObject WScript.Shell).CreateShortcut('{safe_lnk}');"
                    f"$sc.IconLocation = '{safe_ico},0';"
                    f"$sc.Save()"
                )
                ps_file = os.path.join(tempfile.gettempdir(), "mynotepad_icon.ps1")
                with open(ps_file, "w", encoding="utf-8-sig") as f:
                    f.write(ps_cmd)
                subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-File", ps_file],
                               capture_output=True, timeout=10)
                try: os.remove(ps_file)
                except Exception: pass
                # 刷新图标缓存
                subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=5)
                return True
        except Exception:
            pass
        return False

    def _update_taskbar_icon(self, ico_path):
        """通过 Win32 API 设置任务栏图标"""
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            # 找窗口
            hwnd = user32.FindWindowW(None, "我的记事本")
            if not hwnd:
                return False
            WM_SETICON = 0x0080
            ICON_BIG = 1
            # 加载图标
            hIcon = user32.LoadImageW(0, ico_path, 1, 0, 0, 0x00000010)  # IMAGE_ICON | LR_LOADFROMFILE
            if not hIcon:
                return False
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hIcon)
            return True
        except Exception:
            return False

    def confirm_icon(self, temp_path, radius_pct=DEFAULT_ICON_RADIUS_PCT):
        """第二步：确认更换，按选定圆角生成正式图标（带 Alpha 透明 PNG/ICO）并更新快捷方式"""
        real = _validate_icon_temp(temp_path)
        if not real:
            return {"success": False, "error": "无效的临时文件"}
        try:
            from PIL import Image
            import shutil
            resources_dir = os.path.join(EXE_DIR, "resources")
            bundle_res = os.path.join(BASE_DIR, "resources")
            if not os.path.exists(resources_dir) and os.path.exists(bundle_res) and BASE_DIR != EXE_DIR:
                shutil.copytree(bundle_res, resources_dir)
            os.makedirs(resources_dir, exist_ok=True)
            icon_path = os.path.join(resources_dir, "icon.png")
            ico_path = os.path.join(resources_dir, "icon.ico")
            base = Image.open(real).convert("RGBA")
            rounded = _apply_rounded_corners(base, radius_pct)
            # 512×512 带 Alpha PNG（圆角外完全透明）
            rounded.save(icon_path, "PNG")
            # ICO 256×256：Pillow 用 PNG 编码，alpha 完整保留
            rounded.resize((256, 256), Image.LANCZOS).save(ico_path, "ICO", sizes=[(256, 256)])
            # 更新桌面快捷方式（ICO 格式最可靠）
            sc_ok = self._update_shortcut(ico_path)
            # 清理临时文件
            try: os.remove(real)
            except Exception: pass
            # 尝试更新任务栏图标
            tb_ok = self._update_taskbar_icon(ico_path)
            msg = "图标已更新"
            if sc_ok: msg += "，桌面快捷方式已同步"
            if tb_ok: msg += "，任务栏已更新"
            return {"success": True, "msg": msg}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def cancel_icon(self, temp_path):
        """取消更换，删除临时文件"""
        try:
            real = _validate_icon_temp(temp_path) if temp_path else None
            if real:
                os.remove(real)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def restore_default_icon(self):
        """恢复默认图标"""
        try:
            resources_dir = os.path.join(EXE_DIR, "resources")
            for f in ["icon.png", "icon.ico"]:
                fp = os.path.join(resources_dir, f)
                if os.path.exists(fp):
                    os.remove(fp)
            # 恢复桌面快捷方式指向 exe 内置图标
            exe_path = os.path.join(EXE_DIR, "MyNotepad.exe")
            shortcut_updated = self._update_shortcut(exe_path)
            # 读取默认图标预览
            import base64 as b64
            default_png = os.path.join(BASE_DIR, "resources", "icon.png")
            preview = ""
            if os.path.exists(default_png):
                with open(default_png, "rb") as f:
                    preview = "data:image/png;base64," + b64.b64encode(f.read()).decode("utf-8")
            return {
                "success": True,
                "msg": "已恢复默认图标" + ("，桌面快捷方式已同步" if shortcut_updated else ""),
                "preview": preview
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

api = AppApi(backend_api)

# 关窗兜底处理器工厂（app.pyw 底部与 E2E 测试共用）。
# 注意死锁陷阱：closing 事件在 UI 线程同步执行，而 evaluate_js 的 JS 回调也需要
# UI 线程——在 closing 里直接 evaluate_js 会互等死锁。
# 采用「取消-冲洗-再关」：首次 closing 返回 False 取消关闭，后台线程 flush
# （此时 UI 线程已空闲，evaluate_js 正常），完成后 destroy 触发第二次 closing 放行。
# 3 秒看门狗兜底：flush 卡死也强制关窗，绝不让窗口关不掉。
def make_closing_handler(target_window, backend, tray=None, is_quitting=None, should_hide=None):
    """关窗兜底 + 「关窗驻留托盘」。

    tray 不为 None 且用户没点退出时：flush 完成后**隐藏**窗口而不是销毁——进程与提醒守护
    线程继续跑，提醒才有意义（原先窗口一关提醒就彻底失效）。tray=None（测试与未启用托盘）
    时行为不变：flush 后直接销毁。

    should_hide 让「驻留托盘」这一开关**即时生效**：用户在设置里关掉后，本次会话点关闭
    就应该真的退出，而不是等下次启动。tray 也可以传一个可调用对象（`lambda: _tray`）——
    托盘可能在运行中被起/停，按值捕获会拿到过期的引用。
    """
    state = {'phase': 'idle', 'watchdog': None}  # idle -> flushing -> done

    def _current_tray():
        return tray() if callable(tray) else tray

    def _force_close():
        if state['phase'] == 'done':
            return  # 幂等：flush 与看门狗只有一方真正执行关闭
        if state['watchdog'] is not None:
            state['watchdog'].cancel()  # 必须取消：迟到的 Timer 会按 uid 误杀后续新窗口
        _save_window_geometry(backend, target_window)   # 记住窗口大小/位置，下次启动恢复
        quitting = bool(is_quitting and is_quitting())
        current_tray = _current_tray()
        want_hide = current_tray is not None and (should_hide() if should_hide else True)
        if want_hide and not quitting:
            try:
                target_window.hide()
                state['phase'] = 'idle'   # 回到空闲：下次点关闭还能再走一遍
                current_tray.notify_hidden_once()
                return
            except Exception:
                # 隐藏失败就老实关掉：绝不能出现"点了关闭却关不掉"
                try:
                    import applog
                    applog.get_logger().exception("隐藏到托盘失败，改为直接关闭")
                except Exception:
                    pass
        state['phase'] = 'done'
        try:
            target_window.destroy()
        except Exception:
            pass

    def _flush_and_close():
        try:
            raw = target_window.evaluate_js(
                "typeof window.__getUnsavedSnapshot === 'function' ? window.__getUnsavedSnapshot() : null")
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if data and data.get('noteId'):
                    backend.notes_update(data['noteId'],
                        {'title': data.get('title', ''), 'content': data.get('content', '')})
        except Exception:
            try:
                import applog
                applog.get_logger().exception("关窗兜底保存失败")
            except Exception:
                pass
        finally:
            _force_close()

    def _on_closing():
        if state['phase'] == 'done':
            return None  # 第二次（destroy 触发）：放行
        if state['phase'] == 'flushing':
            return False  # flush 期间用户重复点关闭：仍拦截
        state['phase'] = 'flushing'
        import threading
        threading.Thread(target=_flush_and_close, daemon=True).start()
        watchdog = threading.Timer(3.0, _force_close)  # 看门狗：flush 卡死也强制关
        state['watchdog'] = watchdog
        watchdog.start()
        return False  # 取消本次关闭，flush 完成后代码再关
    return _on_closing


# ====== 自定义图标持久化（免受重新打包覆盖） ======
# 为什么需要：用户用「更换图标」写入的是 EXE_DIR/resources/icon.{png,ico}。
# 重新打包（pyinstaller MyNotepad.spec）会清空 dist/MyNotepad 并把 resources
# 还原成打包源里的默认图标——自定义图标与桌面快捷方式指向随即失效。
# 这里在每次启动时把自定义图标另存一份到 exe 同级「数据目录」，
# 之后重建 dist 也能自动还原。
_ICON_BAK_DIRNAME = 'custom_icon'


def _icon_backup_dir():
    return os.path.join(EXE_DIR, 'data', _ICON_BAK_DIRNAME)


def _icon_is_custom(ico_path):
    """与 bundle 内默认图标比对，判断 ico 是否为用户自定义（大小/字节一致即默认）"""
    try:
        if not os.path.isfile(ico_path):
            return False
        default = os.path.join(BASE_DIR, 'resources', 'icon.ico')
        if os.path.abspath(ico_path) == os.path.abspath(default):
            return False
        if not os.path.isfile(default):
            return True
        if os.path.getsize(ico_path) != os.path.getsize(default):
            return True
        with open(ico_path, 'rb') as a, open(default, 'rb') as b:
            return a.read() != b.read()
    except Exception:
        return False


def _backup_custom_icon():
    """把当前自定义图标（若存在）备份到 data/custom_icon/；失败静默"""
    try:
        res_dir = os.path.join(EXE_DIR, 'resources')
        ico = os.path.join(res_dir, 'icon.ico')
        if not _icon_is_custom(ico):
            return False
        bdir = _icon_backup_dir()
        os.makedirs(bdir, exist_ok=True)
        import shutil
        shutil.copy2(ico, os.path.join(bdir, 'icon.ico'))
        png = os.path.join(res_dir, 'icon.png')
        if os.path.isfile(png):
            shutil.copy2(png, os.path.join(bdir, 'icon.png'))
        return True
    except Exception:
        return False


def _restore_custom_icon():
    """打包后 resources 被还原成默认图标时，用备份恢复用户自定义图标"""
    try:
        bdir = _icon_backup_dir()
        bico = os.path.join(bdir, 'icon.ico')
        if not os.path.isfile(bico):
            return False
        res_dir = os.path.join(EXE_DIR, 'resources')
        ico = os.path.join(res_dir, 'icon.ico')
        if _icon_is_custom(ico):
            return False  # 当前已是自定义图标，无需恢复
        os.makedirs(res_dir, exist_ok=True)
        import shutil
        shutil.copy2(bico, ico)
        bpng = os.path.join(bdir, 'icon.png')
        if os.path.isfile(bpng):
            shutil.copy2(bpng, os.path.join(res_dir, 'icon.png'))
        return True
    except Exception:
        return False


_restore_custom_icon()
_backup_custom_icon()

# ====== 窗口几何：按可用工作区约束（避开任务栏/高 DPI） ======
# 为什么需要：pywebview 在 Windows 上按「整个屏幕」居中，不避开任务栏，也不理会
# 高 DPI 缩放。实测 3072×1920 物理屏 @200% 缩放（逻辑 1536×960）+ 底部任务栏，
# 可用高度只有 912px；固定 1200×800 的窗口被居中到 y=196，底边落在 y=996——
# 下沿 84px 永久压在任务栏底下，侧边栏最后一排按钮（日历/回收站）就此看不见。
DEFAULT_WIN_W, DEFAULT_WIN_H = 1200, 800
MIN_WIN_W, MIN_WIN_H = 900, 600
_WORKAREA_RESERVE = 48  # 垂直方向预留：留出拖动把手与呼吸空间


def _get_work_area():
    """返回主屏可用工作区 (left, top, width, height)，已排除任务栏。

    统一换算成「逻辑像素」：pywebview 在 Windows 上按逻辑单位设置窗口尺寸/位置，
    而 SystemParametersInfoW 返回的是当前 DPI 感知级别下的物理像素。高缩放屏上
    两者相差 dpi/96 倍，不换算会把窗口撑成屏幕的数倍大。
    注意：绝不在此处调用 SetProcessDpiAwareness——那会改变整个进程的感知级别，
    反而让 pywebview 自己的尺寸计算错位。
    """
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        class RECT(ctypes.Structure):
            _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                        ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

        rect = RECT()
        if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            l, t = rect.left, rect.top
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w > 0 and h > 0:
                # 物理 -> 逻辑（dpi/96）
                dpi = 96
                try:
                    dpi = ctypes.windll.user32.GetDpiForSystem() or 96
                except Exception:
                    try:
                        hdc = user32.GetDC(0)
                        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88) or 96  # LOGPIXELSX
                        user32.ReleaseDC(0, hdc)
                    except Exception:
                        dpi = 96
                if dpi and dpi != 96:
                    scale = dpi / 96.0
                    l, t = int(l / scale), int(t / scale)
                    w, h = int(w / scale), int(h / scale)
                return l, t, w, h
    except Exception:
        pass
    try:  # 兜底：整屏尺寸（不扣任务栏，仍好过放弃约束）
        import ctypes
        user32 = ctypes.windll.user32
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        if sw > 0 and sh > 0:
            return 0, 0, sw, sh
    except Exception:
        pass
    return None


def clamp_window_geometry(w, h, work_area,
                          min_w=MIN_WIN_W, min_h=MIN_WIN_H,
                          reserve=_WORKAREA_RESERVE, prefer=None):
    """把窗口尺寸与位置收进可用工作区，返回 (w, h, x, y)。**纯函数，不碰 Win32。**

    work_area 为 (left, top, width, height)；传 None 表示取不到工作区，此时
    只把尺寸按最小值修正，位置交回给 pywebview（x/y 返回 None）。

    位置默认由内部重新计算（居中），不沿用调用方传入的 x/y——唯一可靠的做法是把 x/y
    直接交给 pywebview.create_window（winforms 后端会自行按 DPI 换算）；创建后
    再用 SetWindowPos 抢位置会被 pywebview 的 CenterScreen 覆盖（实测逐次漂移
    25→123→221），而 CenterScreen 本身又不避开任务栏，那正是问题根源。

    prefer=(x, y) 用于「恢复上次关闭时的窗口位置」：只有该位置能让窗口**完整**落在
    工作区内才采用，否则退回居中——显示器被拔掉/分辨率变小后，旧坐标可能整个在屏外。
    """
    w, h = int(w), int(h)
    # 工作区可能来自浮点 DPI 换算；宽高非正说明取值失败，一并按「无工作区」处理
    if not work_area:
        return max(min_w, w), max(min_h, h), None, None
    wl, wt, ww, wh = (int(v) for v in work_area)
    if ww <= 0 or wh <= 0:
        return max(min_w, w), max(min_h, h), None, None
    # 尺寸必须严格小于工作区，否则任何居中结果都会有边被推出屏外
    max_h = max(min_h, wh - reserve)
    w = max(min_w, min(w, ww))
    h = max(min_h, min(h, max_h))
    if prefer and prefer[0] is not None and prefer[1] is not None:
        px, py = int(prefer[0]), int(prefer[1])
        if wl <= px and px + w <= wl + ww and wt <= py and py + h <= wt + wh:
            return int(w), int(h), px, py
    cx = wl + max(0, (ww - w) // 2)
    cy = wt + max(0, (wh - h) // 2)
    return int(w), int(h), int(cx), int(cy)


def _clamp_window_geometry(w, h, x=None, y=None):
    """_get_work_area() 的薄封装：取真实工作区后交给纯函数（保持原有调用签名）"""
    return clamp_window_geometry(w, h, _get_work_area())


# ====== 窗口几何持久化 ======
# 默认 1200×800 居中在多数机器上够用，但用户一旦习惯某个尺寸/位置（副屏、分屏缩到 900 宽），
# 每次启动都被重置就很难受。关窗时把几何写进 settings，启动时读回来再按工作区钳制。
# 只认 window_geometry 这一个键：早期版本在 settings 里留下了 window_width/window_height/
# window_x/window_y，但没有任何代码读它们（那些残留值可能是过期甚至屏外的），刻意不读。
_WIN_GEOM_KEY = 'window_geometry'
TRAY_KEY = 'tray_enabled'      # '1'（默认）= 关闭窗口时驻留托盘
HOTKEY_KEY = 'quick_hotkey'    # '1' = 启用全局快速记录热键（默认关，避免抢占系统热键）
CAPTURE_HOTKEY_KEY = 'capture_hotkey'   # '1' = 启用迷你捕获窗热键 Ctrl+Alt+S（同样默认关）
# 托盘/热键控制器：AppApi 只能看到桥接层，这些对象在下面创建，
# 用字典做一次「后注册回调」（启动时还没创建 → 设置只落库，启动时读取即可）。
_TRAY_CTL = {'apply': None}
_HOTKEY_CTL = {'apply': None}
_CAPTURE_HOTKEY_CTL = {'apply': None}


def _load_saved_geometry(backend):
    """读取上次保存的窗口几何，返回 (w, h, x, y) 或 None（无/损坏/非法一律当没存过）"""
    if backend is None:
        return None
    try:
        raw = backend.settings_get(_WIN_GEOM_KEY)
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        w, h = int(data['w']), int(data['h'])
        if w <= 0 or h <= 0:
            return None
        x, y = data.get('x'), data.get('y')
        return (w, h, int(x) if x is not None else None, int(y) if y is not None else None)
    except Exception:
        return None


def _save_window_geometry(backend, win):
    """关窗时把当前几何写回设置。任何失败都不能影响关闭流程。"""
    try:
        w, h = int(win.width), int(win.height)
        if w <= 0 or h <= 0:
            return
        backend.settings_set(_WIN_GEOM_KEY,
                             json.dumps({'w': w, 'h': h, 'x': int(win.x), 'y': int(win.y)}))
    except Exception:
        try:
            import applog
            applog.get_logger().exception("保存窗口几何失败")
        except Exception:
            pass


def _resolve_window_geometry(backend=None):
    """按「上次保存的几何 > 默认尺寸」选尺寸与位置，再按可用工作区钳制"""
    try:
        work = _get_work_area()
        saved = _load_saved_geometry(backend)
        if saved:
            w, h, sx, sy = saved
            w, h, x, y = clamp_window_geometry(w, h, work, prefer=(sx, sy))
        else:
            w, h, x, y = clamp_window_geometry(DEFAULT_WIN_W, DEFAULT_WIN_H, work)
        return {'w': w, 'h': h, 'x': x, 'y': y}
    except Exception:
        return {'w': DEFAULT_WIN_W, 'h': DEFAULT_WIN_H, 'x': None, 'y': None}


# ====== OCR 临时文件辅助（第 11 轮）======
# ⚠️ 这三个函数必须留在「创建窗口」标记**之前**：无头测试（conftest.load_app_partial）
# 只 exec 标记前的部分，而 AppApi 的 ocr_clipboard_image / ocr_release 就在标记前调用它们。
# 原来它们写在标记之后 —— `api.ocr_release()` 直接 NameError（e2e 日志里实测刷了 8 次），
# 于是"临时图用完就删"这条契约**从来没被真正验证过**。

def _temp_png(tag):
    """临时 PNG 的规范命名：系统临时目录 + `mynotepad_` 前缀。

    前缀不是装饰：`_ocr_release` 靠它区分"自己造的"和"用户的图"，删错文件不可逆。
    """
    return os.path.join(tempfile.gettempdir(),
                        'mynotepad_%s_%d.png' % (tag, int(time.time() * 1000)))


def _ocr_clipboard_image():
    """剪贴板里的图片 → 临时 PNG（给 OCR 用）；剪贴板里没有图返回 None。

    只允许写进系统临时目录且文件名带 `mynotepad_` 前缀——`_ocr_release` 会按这个前缀
    判断"这是我自己的临时文件"，绝不删用户的东西。
    """
    path = _temp_png('ocr_clip')
    return desktop.clipboard_image_to_file(path)


def _ocr_release(path):
    """识别面板用完临时图后清掉它，**只认自己造的临时文件**。

    前端 `closeOcr()` 对"非附件来源"的图一定会请求删除（截图/剪贴板/选图那几条路），
    所以这里是最后一道闸：路径不在系统临时目录、或文件名没有 `mynotepad_` 前缀，一律
    拒绝（返回 False）——用户自己选的图绝不能被顺手删掉。
    """
    try:
        p = os.path.abspath(path or '')
        tmp = os.path.realpath(tempfile.gettempdir())
        if (p.startswith(tmp) and os.path.basename(p).startswith('mynotepad_')
                and os.path.isfile(p)):
            os.remove(p)
            return True
    except OSError:
        pass
    return False


# ====== 创建窗口 ======
# 单实例互斥：已有实例在运行时聚焦其窗口并退出本进程。
# 两个进程并发写同一 SQLite（DELETE journal 模式）会撞 database is locked，必须互斥。
# 测试环境（MYNOTEPAD_DATA_DIR 隔离）跳过，避免并行测试互锁。
if not os.environ.get('MYNOTEPAD_DATA_DIR'):
    import ctypes as _ctypes
    _inst_mutex = _ctypes.windll.kernel32.CreateMutexW(None, False, "MyNotepad_SingleInstance_Mutex")
    if _ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        _hwnd = _ctypes.windll.user32.FindWindowW(None, "我的记事本")
        if _hwnd:
            _ctypes.windll.user32.ShowWindow(_hwnd, 9)  # SW_RESTORE（最小化时还原）
            _ctypes.windll.user32.SetForegroundWindow(_hwnd)
        sys.exit(0)
    # _inst_mutex 句柄需存活整个进程，否则互斥量被释放

html_path = os.path.join(BASE_DIR, "renderer", "index.html")

# 加载用户自定义图标（如果存在）
icon_path = os.path.join(EXE_DIR, "resources", "icon.ico")
if not os.path.exists(icon_path):
    icon_path = os.path.join(BASE_DIR, "resources", "icon.ico")

# 按「上次保存的几何 > 默认尺寸」解析窗口几何，并按可用工作区钳制（避开任务栏）
_geo = _resolve_window_geometry(backend_api)

window = webview.create_window(
    "我的记事本",
    html_path,
    js_api=api,
    width=_geo['w'],
    height=_geo['h'],
    x=_geo['x'],
    y=_geo['y'],
    min_size=(900, 600),
    text_select=True
)

# 窗口加载完成后自动弹出到桌面最前
def _on_loaded():
    import ctypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.FindWindowW(None, "我的记事本")
    if hwnd:
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        user32.AllowSetForegroundWindow(pid.value)
        user32.ShowWindow(hwnd, 9)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        # 0.5s 后取消置顶
        ctypes.windll.kernel32.Sleep(500)
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001)


window.events.loaded += _on_loaded

# ====== 托盘常驻 + 提醒守护 ======
# 关窗驻留托盘不只是「方便」：提醒原先只靠前端 30s 轮询，窗口一关就再也不提醒。
# 驻留托盘后进程还活着，配合 ReminderWatcher（窗口隐藏时在后端补气泡）才让提醒可信。
_state = {'quitting': False}


def _tray_is_enabled():
    try:
        return (backend_api.settings_get(TRAY_KEY) or '1') != '0'
    except Exception:
        return True


def _window_visible():
    """以 Win32 为准判断主窗口用户是否看得见。

    不在进程内自己记标志：单实例激活时是**另一个进程**把本窗口显示出来的，标志会失真。
    最小化也算「看不见」——那时前端 Toast 弹在看不见的地方，正该用托盘气泡。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "我的记事本")
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):        # 最小化
            return False
        return bool(user32.IsWindowVisible(hwnd))
    except Exception:
        return True                      # 判断不了就当作可见：宁少弹气泡，也别重复提醒


def _show_window():
    try:
        window.show()
        window.restore()          # 可能还处于最小化状态
    except Exception:
        pass
    _on_loaded()                  # 重新置顶一次，方便用户看到


def _quit_from_tray():
    _state['quitting'] = True
    try:
        window.destroy()          # 触发 closing -> flush -> 真正销毁（quitting=True 时不再隐藏）
    except Exception:
        pass


# ====== 捕获窗口：截图选区覆盖窗 + 迷你捕获窗（第 10 轮）======
# 两个独立的小窗口，都只在需要时创建、用完就 destroy（不常驻：pywebview 的窗口没法真正"复用"，
# 常驻一个隐藏窗口反而要在每次都清理它的状态）。
#
# 覆盖窗为什么是"冻屏图"而不是半透明蒙层：WebView2 **不支持真正的透明窗口**
# （`transparent=True` 实测无效），半透明蒙层下面仍是自己的底色而不是桌面。所以改成
# 「藏主窗 → 抓整个虚拟屏幕存 PNG → 覆盖窗显示这张图 → 按坐标从原图裁剪」，
# 用户看到的与最终裁到的像素完全一致（详见 desktop.py 顶部说明）。
_CAPTURE_TITLE = '我的记事本 · 截图选区'
_MINI_TITLE = '我的记事本 · 快速记录'
_OVERLAY_HTML = os.path.join(BASE_DIR, 'renderer', 'capture-overlay.html')
_MINI_HTML = os.path.join(BASE_DIR, 'renderer', 'capture-mini.html')
# 藏窗后等这么久再抓屏：DWM 把窗口撤下去有个过程，立刻抓会把自己拍进去（实测 0.12s 起稳定）
_HIDE_SETTLE_SEC = 0.18

_capture = {
    'full': None,        # 冻屏整图（虚拟屏幕）临时文件
    'view': None,        # 覆盖窗显示的那张（按客户区裁过）
    'origin': None,      # 整图物理原点 (x, y)
    'view_origin': None, # 显示图物理原点 (x, y)
    'view_size': None,   # 显示图物理尺寸 (w, h)
    'client': None,      # 覆盖窗客户区物理矩形 (x, y, w, h)（位置=摆过去之后的位置）
    'image': None,       # 给覆盖窗的 data URI
    'note_id': None,     # 开始截图时正在编辑的笔记（插入用）
    'window': None,
    'was_visible': False,
}
_mini = {'window': None}


def _dpi_scale():
    """系统 DPI 缩放（1.0 = 100%）。pywebview 的窗口坐标是**逻辑**像素，物理坐标要除以它。"""
    try:
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForSystem() or 96
        return (dpi / 96.0) or 1.0
    except Exception:
        return 1.0


def _client_rect_on_screen(title, timeout=1.5):
    """按标题找窗口，返回其**客户区**在屏幕上的物理矩形 (x, y, w, h)。

    为什么要等：覆盖窗的页面在**表单还没 Show 完**时就已经加载并回调 `capture_overlay_info`
    了（WebView2 在构造表单的过程中就开始导航），那一刻 `FindWindowW` 找不到窗口 →
    实测整次截图会因此作废（页面拿不到图就直接取消）。所以这里轮询等一小会儿。

    为什么要客户区而不是请求的尺寸：pywebview 的 width/height 是窗口外框逻辑尺寸，
    实际客户区会小一点（DWM 边框/阴影），而 DPI 换算又可能再差几个像素。
    拿真实的客户区 + JS 的 innerWidth 一除，缩放与边框补偿一次算清（见 desktop.map_selection_to_image）。
    """
    deadline = time.time() + max(0.0, timeout)
    while True:
        rect = _client_rect_probe(title)
        if rect:
            return rect
        if time.time() >= deadline:
            return None
        time.sleep(0.05)


def _client_rect_probe(title):
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return None
        rc = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
            return None
        pt = wintypes.POINT(0, 0)
        if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
            return None
        w, h = int(rc.right - rc.left), int(rc.bottom - rc.top)
        if w <= 0 or h <= 0:
            return None
        return (int(pt.x), int(pt.y), w, h)
    except Exception:
        return None


def _focus_window(title):
    """把某个窗口叫到前台（覆盖窗/迷你窗都要抢键盘焦点，否则 Esc/Enter 收不到）"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _eval_main(js):
    """让**主窗**的 JS 做点事（跨窗口通信只能这么走）。失败只记日志：内容已经落库了。"""
    try:
        window.evaluate_js(js)
        return True
    except Exception:
        try:
            import applog
            applog.get_logger().exception("调用主窗 JS 失败")
        except Exception:
            pass
        return False


def _cleanup_files(*paths):
    for p in paths:
        if not p:
            continue
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def _capture_begin(note_id=None):
    """开始一次截图。返回 True 表示覆盖窗已创建（失败时主窗原样恢复）。"""
    if _capture['window'] is not None:
        return False                        # 已经在截了（连点两次不该冒出两个覆盖窗）
    screen = desktop.virtual_screen_rect()
    if not screen:
        return False
    was_visible = _window_visible()
    if was_visible:
        try:
            window.hide()
            time.sleep(_HIDE_SETTLE_SEC)    # 不睡这一下就会把记事本自己拍进图里
        except Exception:
            try:
                import applog
                applog.get_logger().exception("截图前隐藏主窗失败（会把自己拍进图里）")
            except Exception:
                pass
    full = _temp_png('shot_full')
    if not desktop.grab_screen(full):
        _cleanup_files(full)
        if was_visible:
            _show_window()
        return False
    scale = _dpi_scale()
    geo = {'width': max(200, int(screen[2] / scale)), 'height': max(100, int(screen[3] / scale)),
           'x': int(screen[0] / scale), 'y': int(screen[1] / scale)}
    try:
        overlay = webview.create_window(
            _CAPTURE_TITLE, _OVERLAY_HTML,
            js_api=api,
            width=geo['width'], height=geo['height'], x=geo['x'], y=geo['y'],
            frameless=True, easy_drag=False, resizable=False, on_top=True,
            focus=True, shadow=False, background_color='#000000', hidden=True)
    except Exception:
        _cleanup_files(full)
        if was_visible:
            _show_window()
        try:
            import applog
            applog.get_logger().exception("创建截图覆盖窗失败")
        except Exception:
            pass
        return False
    if overlay is None:
        _cleanup_files(full)
        if was_visible:
            _show_window()
        return False
    _capture.update({'full': full, 'view': None, 'origin': (screen[0], screen[1]),
                     'view_origin': (screen[0], screen[1]),
                     'view_size': (screen[2], screen[3]), 'client': None,
                     'image': None, 'note_id': note_id, 'window': overlay,
                     'was_visible': was_visible})
    overlay.events.closed += _on_overlay_closed
    return True


def _on_overlay_closed():
    """覆盖窗被外部关掉（Alt+F4 / 系统）时也要收尾，不留一个卡住的会话"""
    if _capture['window'] is None:
        return
    _capture_end(restore=False)


def _capture_overlay_info(viewport_w=None, viewport_h=None):
    """覆盖窗 JS 索要冻屏图与映射参数（图片按 base64 data URI 过去）。

    视口尺寸由页面**报上来**（`window.innerWidth/innerHeight`），不让 Python 反过来
    `evaluate_js` 去问：桥接调用返回之前 JS 正等着结果，Python 再回调 JS 会互相等死
    （e2e 里实测卡死几分钟）。它还有一个用处：**量不到客户区时当兜底**（×DPI 缩放）。

    裁剪框恒为 `(0, 0, 客户区宽, 客户区高)`：窗口被摆在虚拟屏左上角，而整屏图的 (0,0) 就是
    虚拟屏左上角。窗口比屏幕小一点（边框/DWM）时，直接拉伸会让像素与光标错开半格，
    裁出来之后显示图就是 1:1 的，映射只剩一个缩放比例。
    """
    if not _capture['full'] or not os.path.isfile(_capture['full']):
        return None
    measured = _client_rect_on_screen(_CAPTURE_TITLE, timeout=1.5)
    ox, oy = _capture['origin']
    if measured:
        cw, ch = measured[2], measured[3]
    else:
        # 兜底：按页面报的视口 × DPI 缩放推算（量不到也要让截图能用，只是可能有几像素错位）
        cw = int(round(float(viewport_w or 0) * _dpi_scale()))
        ch = int(round(float(viewport_h or 0) * _dpi_scale()))
        if cw <= 0 or ch <= 0:
            return None
    client = (ox, oy, cw, ch)
    view = _capture['full'].replace('_full.png', '_view.png')
    box = (0, 0, cw, ch)
    if desktop.crop_png(_capture['full'], box, view):
        _capture['view'] = view
        _capture['view_origin'] = (ox, oy)
        _capture['view_size'] = (cw, ch)
    else:
        # 裁不动就退回整图：映射参数跟着退回，公式自己会兜住（宁可图大一圈，不能错位）
        _capture['view'] = _capture['full']
        _capture['view_origin'] = (ox, oy)
        try:
            from PIL import Image
            with Image.open(_capture['full']) as im:
                _capture['view_size'] = im.size
        except Exception:
            return None
    try:
        with open(_capture['view'], 'rb') as f:
            _capture['image'] = 'data:image/png;base64,' + base64.b64encode(f.read()).decode('ascii')
    except OSError:
        return None
    _capture['client'] = client
    return {'image': _capture['image'], 'client': list(client),
            'origin': list(_capture['view_origin']), 'size': list(_capture['view_size']),
            'note_id': _capture['note_id'] or '',
            'viewport': [viewport_w or 0, viewport_h or 0]}


def _capture_overlay_ready():
    """冻屏图已经在页面里画好了 → 现在才显示窗口（先显示会闪一下自己那块黑底）"""
    overlay = _capture['window']
    if overlay is None:
        return False
    try:
        overlay.show()
        overlay.restore()
    except Exception:
        return False
    _focus_window(_CAPTURE_TITLE)
    return True


def _capture_box(rect, viewport):
    """选区（CSS px）→ **整图**里的物理像素矩形，顺便把显示图坐标平移回整图"""
    view_box = desktop.map_selection_to_image(
        rect or (), viewport or (), _capture['client'],
        _capture['view_origin'], _capture['view_size'])
    if view_box is None:
        return None
    dx = _capture['view_origin'][0] - _capture['origin'][0]
    dy = _capture['view_origin'][1] - _capture['origin'][1]
    return (view_box[0] + dx, view_box[1] + dy, view_box[2], view_box[3])


def _capture_commit(action, rect=None, viewport=None):
    """覆盖窗把用户的选择交回来：insert / note / clipboard / cancel"""
    action = (action or 'cancel').strip()
    result = {'ok': True, 'action': action}
    note_id = _capture['note_id']          # 收尾会把会话清空，插入要用的 id 得先留一份
    if action == 'cancel':
        _capture_end(restore=True)
        return result
    box = _capture_box(rect, viewport)
    if box is None:
        return {'ok': False, 'error': '选区太小了'}
    shot = _temp_png('shot')
    if not desktop.crop_png(_capture['full'], box, shot):
        return {'ok': False, 'error': '裁剪失败'}
    keep_shot = False
    try:
        if action == 'clipboard':
            result['ok'] = bool(desktop.set_clipboard_image(shot))
            if not result['ok']:
                result['error'] = '写入剪贴板失败'
        elif action == 'ocr':
            # 识别要在**主窗**里弹面板让人过一眼，所以这张裁剪图得留给前端用，
            # 不能像别的动作那样用完即删（前端用完调 ocr_release 删掉）
            keep_shot = True
            result['path'] = shot
        elif action == 'note':
            note = backend_api.capture_image(shot)
            if not note:
                result['ok'] = False
                result['error'] = '存成笔记失败'
            else:
                result['id'] = note.get('id')
        elif action == 'insert':
            if not note_id:
                result['ok'] = False
                result['error'] = '没有正在编辑的笔记'
            else:
                saved = backend_api.file_copy_to_note(shot, note_id, 'image')
                if not saved or not saved.get('filename'):
                    result['ok'] = False
                    result['error'] = '保存截图附件失败'
                else:
                    result['rel'] = 'attachments/%s/%s' % (note_id, saved['filename'])
                    result['saved'] = saved
        else:
            result['ok'] = False
            result['error'] = '未知操作：' + action
    except Exception as exc:
        result['ok'] = False
        result['error'] = str(exc)
        try:
            import applog
            applog.get_logger().exception("截图提交失败")
        except Exception:
            pass
    _cleanup_files(shot) if not keep_shot else None
    # 失败时**不收窗**：覆盖窗一关，用户就再也看不到失败原因了（只能重截一次）
    if result.get('ok'):
        _capture_end(restore=True)
        # 窗口收掉之后再让主窗做插入/刷新：顺序反了会先看到笔记变化再看到窗口消失，观感很跳
        if action == 'insert':
            _eval_main('window.__capture && window.__capture.insertImage(%s, %s, %s)'
                       % (json.dumps(result['rel']), json.dumps(result.get('saved') or {}),
                          json.dumps(note_id or '')))
        elif action == 'ocr':
            _eval_main('window.__ocr && window.__ocr.openFromCapture(%s, %s)'
                       % (json.dumps(shot), json.dumps(note_id or '')))
        elif action == 'note' and result.get('id'):
            _eval_main('window.__capture && window.__capture.afterExternalCapture(%s, true)'
                       % json.dumps(result['id']))
    return result


def _capture_end(restore=True):
    """收尾：销毁覆盖窗、删掉临时图、按需把主窗放回来"""
    was_visible = _capture['was_visible']
    overlay = _capture['window']
    files = [_capture['full'], _capture['view']]
    _capture.update({'window': None, 'image': None, 'full': None, 'view': None,
                     'origin': None, 'view_origin': None, 'view_size': None,
                     'client': None, 'note_id': None, 'was_visible': False})
    _cleanup_files(*files)
    if overlay is not None:
        try:
            overlay.destroy()
        except Exception:
            pass
    if restore and was_visible:
        _show_window()


def _mini_open():
    """迷你捕获窗：无边框小窗，Enter 落「收件箱」。返回 True 表示窗口可用。"""
    mini = _mini['window']
    if mini is not None:
        try:
            mini.show()
            mini.restore()
            _focus_window(_MINI_TITLE)
            _eval_mini('window.__mini && window.__mini.focusInput()')
            return True
        except Exception:
            _mini['window'] = None
    work = _get_work_area() or (0, 0, 1280, 800)
    w, h = 560, 168
    x = int(work[0] + max(0, (work[2] - w) // 2))
    y = int(work[1] + max(0, work[3] // 4))          # 靠上四分之一：离视线近，又不压住中间
    try:
        win = webview.create_window(
            _MINI_TITLE, _MINI_HTML, js_api=api,
            width=w, height=h, x=x, y=y,
            frameless=True, easy_drag=False, resizable=False, on_top=True,
            focus=True, shadow=True, background_color='#000000')
    except Exception:
        try:
            import applog
            applog.get_logger().exception("创建迷你捕获窗失败")
        except Exception:
            pass
        return False
    if win is None:
        return False
    _mini['window'] = win
    win.events.closed += _on_mini_closed
    return True


def _on_mini_closed():
    _mini['window'] = None


def _eval_mini(js):
    mini = _mini['window']
    if mini is None:
        return False
    try:
        mini.evaluate_js(js)
        return True
    except Exception:
        return False


def _mini_submit(text, keep_open=False):
    """迷你窗提交：落「收件箱」；keep_open=True（Ctrl+Enter）留着继续记下一条。

    刻意**不**把主窗叫到前台：捕获是"记一下就走"，抢焦点等于打断用户正在做的事。
    主窗的列表用 JS 悄悄刷新一下，等用户切回去时东西已经在了。
    """
    body = (text or '').strip()
    note = backend_api.capture_text(body) if body else None
    if note and note.get('id'):
        _eval_main('window.__capture && window.__capture.afterExternalCapture(%s, false)'
                   % json.dumps(note['id']))
    if not keep_open:
        _mini_close()
    return {'ok': bool(note), 'id': (note or {}).get('id')}


def _mini_close():
    mini = _mini['window']
    _mini['window'] = None
    if mini is not None:
        try:
            mini.destroy()
        except Exception:
            pass
    return True


_tray = None
_watcher = None


def _start_tray():
    """起托盘 + 提醒守护；失败返回 False（退化为普通窗口行为，不影响应用可用）"""
    global _tray, _watcher
    if _tray is not None:
        return True
    tray = desktop.Tray(icon_path, _show_window, _quit_from_tray,
                        on_toggle_autostart=lambda v: desktop.set_autostart(v))
    if not tray.start():
        return False
    _tray = tray
    # 窗口隐藏/最小化时由后端补提醒气泡（可见时交给前端 Toast，避免重复提醒）
    _watcher = desktop.ReminderWatcher(backend_api, _tray, _window_visible)
    _watcher.start()
    return True


def _stop_tray():
    global _tray, _watcher
    if _watcher is not None:
        _watcher.stop()
        _watcher = None
    if _tray is not None:
        _tray.stop()
        _tray = None


def _apply_tray_setting(enabled):
    """设置项变化即时生效：关掉「驻留托盘」后本次会话点关闭就该真的退出"""
    if enabled:
        return _start_tray()
    _stop_tray()
    return False


_TRAY_CTL['apply'] = _apply_tray_setting


# ====== 全局快速记录热键 ======
# 任意界面按 Ctrl+Alt+N：把窗口叫到前台，并新建一篇笔记、把光标放进标题。
# 用「点新建按钮」而不是另开 API：复用既有的新建链路（列表刷新、编辑器 UI、保存基线都在里面）。
_hotkey = None


def _quick_capture():
    _show_window()
    try:
        window.evaluate_js(
            "document.getElementById('btn-new-note').click();"
            "setTimeout(function(){var t=document.getElementById('note-title-input');"
            "if(t){t.focus();t.select();}},400);")
    except Exception:
        try:
            import applog
            applog.get_logger().exception("全局热键新建笔记失败")
        except Exception:
            pass


def _apply_hotkey_setting(enabled):
    """起/停全局热键，返回最终是否处于启用状态（注册失败会返回 False，前端据此提示）"""
    global _hotkey
    if not enabled:
        if _hotkey is not None:
            _hotkey.stop()
            _hotkey = None
        return False
    if _hotkey is not None and _hotkey.started:
        return True
    hk = desktop.GlobalHotkey(_quick_capture)
    hk.start()
    hk.join(timeout=2)          # 等它注册完，好把"是否成功"立刻反馈给界面
    if hk.started:
        _hotkey = hk
        return True
    return False


_HOTKEY_CTL['apply'] = _apply_hotkey_setting


# ----- 迷你捕获窗的全局热键（Ctrl+Alt+S）-----
# 与上面那个是**两个独立热键**：Ctrl+Alt+N 是"叫出主窗并新建笔记"，这个只弹一个不抢主窗的
# 小输入框。合成一个会让老用户熟悉的行为变样，也让"想安静记一句"的人被迫看到整个窗口。
_capture_hotkey = None


def _open_capture_mini():
    try:
        _mini_open()
    except Exception:
        try:
            import applog
            applog.get_logger().exception("全局热键打开捕获窗失败")
        except Exception:
            pass


def _apply_capture_hotkey_setting(enabled):
    global _capture_hotkey
    if not enabled:
        if _capture_hotkey is not None:
            _capture_hotkey.stop()
            _capture_hotkey = None
        return False
    if _capture_hotkey is not None and _capture_hotkey.started:
        return True
    hk = desktop.GlobalHotkey(_open_capture_mini, vk=desktop.GlobalHotkey.VK_S, hotkey_id=2)
    hk.start()
    hk.join(timeout=2)
    if hk.started:
        _capture_hotkey = hk
        return True
    return False


_CAPTURE_HOTKEY_CTL['apply'] = _apply_capture_hotkey_setting

if _tray_is_enabled():
    _start_tray()

if (backend_api.settings_get(HOTKEY_KEY) or '0') == '1':
    _apply_hotkey_setting(True)

if (backend_api.settings_get(CAPTURE_HOTKEY_KEY) or '0') == '1':
    _apply_capture_hotkey_setting(True)

# 关窗兜底：不等防抖的最后输入由 closing 同步落库；启用托盘时关窗 = 隐藏到托盘
window.events.closing += make_closing_handler(
    window, backend_api, tray=lambda: _tray, is_quitting=lambda: _state['quitting'],
    should_hide=_tray_is_enabled)

# 启动
if os.path.exists(icon_path):
    webview.start(debug=False, icon=icon_path)
else:
    webview.start(debug=False)

# webview 退出（真正关闭）：收尾托盘、热键与守护线程
_state['quitting'] = True
_stop_tray()
if _hotkey is not None:
    _hotkey.stop()
if _capture_hotkey is not None:
    _capture_hotkey.stop()
_mini_close()
_capture_end(restore=False)
