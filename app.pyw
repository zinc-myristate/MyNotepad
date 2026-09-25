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

# PyInstaller 兼容
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS  # bundle 内的静态文件
    EXE_DIR = os.path.dirname(sys.executable)  # exe 所在目录（数据存这里）
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    EXE_DIR = BASE_DIR

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
    def notes_list(self):
        return self.backend.notes_list()
    def notes_get(self, note_id, unlocked=False): return self.backend.notes_get(note_id, unlocked)
    def notes_create(self):
        return self.backend.notes_create()
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
    def notes_create_from_link(self, title):
        return self.backend.notes_create_from_link(title)
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
    def capture_image(self, src_path, title=None):
        return self.backend.capture_image(src_path, title)

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
    def notes_by_tag(self, tag_id): return self.backend.notes_by_tag(tag_id)

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
        }

    def set_hotkey_enabled(self, enabled):
        self.backend.settings_set(HOTKEY_KEY, '1' if enabled else '0')
        apply = _HOTKEY_CTL.get('apply')
        if apply is not None:
            return apply(bool(enabled))     # 立即生效，返回是否真的注册上了
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

    def import_markdown_dialog(self):
        """选择 .md 文件导入为新笔记，返回新笔记或 None"""
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
        return self.backend.import_markdown(text)

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
# 托盘/热键控制器：AppApi 只能看到桥接层，这些对象在下面创建，
# 用字典做一次「后注册回调」（启动时还没创建 → 设置只落库，启动时读取即可）。
_TRAY_CTL = {'apply': None}
_HOTKEY_CTL = {'apply': None}


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

if _tray_is_enabled():
    _start_tray()

if (backend_api.settings_get(HOTKEY_KEY) or '0') == '1':
    _apply_hotkey_setting(True)

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
