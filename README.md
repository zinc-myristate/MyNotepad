# 我的记事本 (MyNotepad)

> Windows 桌面本地记事本 —— Python + pywebview 架构，纸质笔记本风格。
> 数据完全本地存储，便携模式（数据跟随 exe 目录），可拷到 U 盘随身携带。

![tech](https://img.shields.io/badge/Python-3.14-blue) ![UI](https://img.shields.io/badge/UI-pywebview-green) ![DB](https://img.shields.io/badge/DB-SQLite%20(FTS5)-lightgrey)

---

## 功能概览

| 类别 | 能力 |
|---|---|
| 📝 笔记管理 | CRUD / 置顶 / 收藏 / 拖拽排序 / **回收站**（30 天自动清理）/ 全文搜索（FTS5 trigram，中文可用）/ 自动保存防抖 + 关窗兜底 |
| 🔐 密码保护 | **真加密**：AES-256-GCM envelope 信封方案，PBKDF2-600k 派生，内容/历史版本全部加密，改密码零重加密 |
| 🕐 历史版本 | Ctrl+S 手动保存生成快照，每笔记最多 50 个，可预览 / 恢复 / 删除 |
| ⏰ 提醒系统 | 自然语言输入（「明天下午3点」「每周五9:00」）、重复提醒（每天/每周/工作日/每月/每年）、Toast 通知、稍后提醒 |
| 🎨 外观 | 4 主题 / 双层背景系统（全局层 + 笔记层，毛玻璃自适应 UI）/ 封面系统（4 类型） |
| ✍️ 富文本 | Quill.js v2：加粗/斜体/颜色/自定义字体字号、待办清单、表格、图片（拖拽+滚轮缩放）、附件卡片、装饰分割线、贴纸印章、Emoji |
| 🧮 数学公式 | KaTeX 行内/块级公式，点击可编辑 |
| 📤 导出 | HTML / TXT / DOCX / XLSX / PDF（系统打印）/ **一键全库备份 ZIP**（数据库+附件+背景图） |
| 🎙 其他 | 语音输入（Web Speech API zh-CN）、日历面板、自定义应用图标（显著性智能裁切 + Win11 大圆角） |

## 技术架构

| 层 | 技术栈 |
|---|---|
| 前端 | pywebview + Quill.js v2 + KaTeX |
| 后端 | Python 3.14 |
| 数据库 | SQLite（`data/notes.db`，FTS5 trigram 全文索引） |
| 加密 | `cryptography`（AES-256-GCM） |
| 打包 | PyInstaller（`MyNotepad.spec`） |
| 测试 | pytest（123 单测 + 10 无头 E2E） |

## 快速开始

**开发运行**（需要 Python 3.14 及依赖）：

```bash
pip install pywebview cryptography Pillow opencv-contrib-python numpy python-docx openpyxl pytest
python app.pyw          # 或 py -3.14 app.pyw
```

**打包分发**：

```bash
pyinstaller MyNotepad.spec   # 产物在 dist/MyNotepad/
```

**便捷启动**：`启动.bat` / `启动.vbs`（桌面快捷方式）；`诊断.bat` 排查环境问题。

## 数据与安全

- **数据目录**：`data/`（与 exe 同目录，便携模式；`MYNOTEPAD_DATA_DIR` 环境变量可覆盖，测试隔离用）
  - `notes.db` — 笔记数据库（正文为 Quill Delta JSON）
  - `attachments/` — 附件与图片
  - `backgrounds/` — 笔记背景图副本
  - `backups/` — 定期自动备份（>24h 触发，滚动保留 7 份，sqlite backup API 页级一致）
  - `error.log` — 崩溃兜底日志（1MB×2 轮转，绝不含笔记内容）
- **单实例**：重复启动会自动聚焦已有窗口（互斥量保证，避免并发写库冲突）
- **多设备同步（可选）**：把整个应用目录（含 `data/`）放入同步盘（坚果云 / OneDrive / Dropbox 等）即可多设备同步。注意：先退出应用、等同步完成后再在另一台设备打开；不要在两台设备同时运行应用（SQLite 并发写会锁冲突），不要在应用运行时手动覆盖同步来的文件
- **关窗兜底**：closing 事件「取消-冲洗-再关」，不等防抖的最后 500ms 输入也落库，3s 看门狗保证关得掉
- **加密设计**：每笔记随机 DEK（AES-256-GCM）加密正文与历史版本，DEK 被密码派生的 KEK 包裹（`dekv1:`），密文 AAD 绑定 note_id 防跨笔记移植；解锁状态只存后端进程内存；**忘记密码内容不可恢复**
- **全文搜索**：FTS5 trigram（中文可用）；加密笔记正文不入索引，只搜标题；<3 字符自动 LIKE 回退
- **图片外置**：图片落盘 `attachments/`，正文只存引用（Delta dict），不再内嵌 base64；启动时自动迁移存量图片（幂等，`data/backups/premig-*` 快照可回滚）；导出时自动重新内嵌

## 测试

```bash
py -3.14 -m pytest                 # 123 单测（自动隔离临时数据目录）
py -3.14 -m pytest -m e2e          # 10 无头 pywebview 端到端
python -m ruff check .             # 代码检查（配置见 pyproject.toml）
```

## 打包

```bash
python build.py                    # 推荐：一条命令完成全部步骤
```

`build.py` 按 `关实例 → 备份 data/resources → PyInstaller → 还原数据 → 修快捷方式 → 冒烟启动`
执行，任一步失败都会**尽力还原数据**。

> ⚠️ 不要直接跑 `pyinstaller MyNotepad.spec`：它会清空整个 `dist/MyNotepad`，
> 而你的笔记库、附件、背景图就在 `dist/MyNotepad/data/` 里。手工执行前务必先备份 `data/`。

## 数据库体积

程序启动时（后台线程，备份之后）会检查数据库的**空闲页占比**，超过 30% 就自动 `VACUUM` 回收。
SQLite 删除或改写大字段后**不会**把空间还给文件系统——图片外置迁移与历史版本快照的删除都会
持续堆积空闲页。实测某库：12.02 MB 的文件里有效数据只有 0.12 MB（空闲页占 99%），
启动一次后即回收为 0.12 MB，数据完整性校验通过。

## 项目结构

```
├── app.pyw                  # 入口（pywebview 窗口 + JS API 桥接 + 关窗兜底 + 图标处理）
├── backend.py               # 后端 API（数据库/加密/搜索/备份/提醒/导出）
├── applog.py                # 崩溃兜底日志
├── build.py                 # 打包脚本（备份-构建-还原-修快捷方式-冒烟）
├── MyNotepad.spec           # PyInstaller 打包配置
├── pyproject.toml           # ruff 配置
├── renderer/                # 前端（ES 模块图 + Quill/KaTeX + 4 主题样式）
├── data/                    # 运行时数据
├── resources/               # 图标 + 人脸检测模型
├── tests/                   # pytest 单测 + 无头 E2E
└── CLAUDE.md                # 开发文档（架构细节 + 更新历史）
```

## 已知限制

1. 前端为**原生 ES 模块**（无打包器/构建工具）：跨文件依赖显式写在 `import` 里，加载顺序由模块图
   决定；新增模块只需被 `js/app/00-main.js`（或其依赖）import 到即会加载
2. 编辑器内**复制**图片走 Quill 剪贴板，粘贴出的副本仍以 data URI 内嵌，下次启动迁移外置
   （用按钮插入/拖拽的图片本身已外置到 `attachments/`）
3. 任务栏图标需重启应用后更新（pywebview 框架限制）

## 窗口尺寸与任务栏

窗口初始尺寸/位置不再写死，而是启动时读取**可用工作区**（已排除任务栏，并按 DPI 换算成逻辑像素）
后居中放置，因此：

- 高 DPI 缩放屏（如 200%）下不会再出现下沿被任务栏压住、底部按钮（日历/回收站）看不见的情况
- 工作区较小（笔记本、缩放后逻辑分辨率低）时自动收缩，最小不小于 900×600；
  工作区本身就小于 900×600 时优先保住可用尺寸（允许略微溢出，但左上角一定可见）
- 调整默认尺寸改 `app.pyw` 的 `DEFAULT_WIN_W` / `DEFAULT_WIN_H`（仍会被工作区钳制）
- 该逻辑是纯函数（`clamp_window_geometry`），由 `tests/test_window_geometry.py` 覆盖
  1080p / 1366×768 / 带鱼屏 / 副屏 / 任务栏在左或在顶 / 极小屏等 10 种配置，防止回归

## 自定义图标与重新打包

「更换图标」写入的是 `dist/MyNotepad/resources/icon.{png,ico}`，而重新打包会清空 `dist/MyNotepad`
并把它还原成仓库默认图标。为此程序每次启动会把自定义图标备份到 `data/custom_icon/`，
检测到被还原成默认图标时自动恢复（删除 `data/custom_icon/` 即放弃该备份）。

桌面快捷方式的图标则**指向 exe 内嵌资源**（`MyNotepad.exe,0`），由 `build.py` 自动维护——
不再依赖 `resources/icon.ico` 是否存在，因此重新打包不会让快捷方式变成白图标。

> ⚠️ 用 `python build.py` 打包即可（它会自动备份并还原 `data/`）。
> 若坚持手工 `pyinstaller`，务必先备份整个 `dist/MyNotepad/data/`。

## 前端脚本加载

前端分两部分：

- **第三方 UMD 库**（`quill.js`、`katex/katex.min.js`）：由 `index.html` 末尾的加载器按顺序注入，
  单个文件失败自动重试（最多 4 次，重试带 `?retry=N` 绕开缓存）。WebView2 偶发会拉取失败个别脚本
  （实测约 1/20），不重试就会静默缺功能。
- **应用自身**（`renderer/js/` 下 16 个文件）：是一个 **ES 模块图**，唯一入口 `js/app/00-main.js`。
  每个文件显式 `import` 自己用到的东西，加载顺序由模块图决定。

改成模块是为了消除一个结构性隐患：改造前这些文件是经典脚本，靠「全局作用域 + 加载顺序」互相依赖，
`utils.js` 一挂就会让依赖它的 6 个模块在顶层抛错而整段不执行，表现为「新建笔记点了没反应」＋
「工具栏按钮图标消失」。模块化后依赖关系是显式的，缺文件不再可能造成「界面半死」——要么整图
成功，要么整体失败并在窗口顶部明确报错（并写 `error.log`）。

模块图失败时用**整页 reload**重试（上限 4 次，计数存 sessionStorage）：浏览器会把「取不到的模块」
记进本次文档的模块表，同一文档内再试也不会重新请求，reload 才能拿到全新的模块表。

回归测试见 `tests/test_frontend_modules.py`（静态检查：孤儿模块 / import 了不存在的导出 /
忘了 import；无需浏览器，属默认单测）与 `tests/test_boot_loader.py`（无头 E2E：干净启动、
经典脚本失败重试、模块图失败明确报错、失败后整页重试到上限）。
