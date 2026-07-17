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
import tkinter.filedialog
import tkinter.messagebox
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
    except:
        pass

    result = tkinter.messagebox.askyesno(title, message)

    try:
        window.restore()
    except:
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
    def notes_update(self, note_id, fields): return self.backend.notes_update(note_id, fields)
    def notes_delete(self, note_id): return self.backend.notes_delete(note_id)

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
        import os
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

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
        except:
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
                except: pass
                # 刷新图标缓存
                subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=5)
                return True
        except:
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
        except:
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
            except: pass
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

# ====== 创建窗口 ======
html_path = os.path.join(BASE_DIR, "renderer", "index.html")

# 加载用户自定义图标（如果存在）
icon_path = os.path.join(EXE_DIR, "resources", "icon.ico")
if not os.path.exists(icon_path):
    icon_path = os.path.join(BASE_DIR, "resources", "icon.ico")

window = webview.create_window(
    "我的记事本",
    html_path,
    js_api=api,
    width=1200,
    height=800,
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
        kernel32.AllowSetForegroundWindow(pid.value)
        user32.ShowWindow(hwnd, 9)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        # 0.5s 后取消置顶
        ctypes.windll.kernel32.Sleep(500)
        user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001)

window.events.loaded += _on_loaded

# 启动
if os.path.exists(icon_path):
    webview.start(debug=False, icon=icon_path)
else:
    webview.start(debug=False)
