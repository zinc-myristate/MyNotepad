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
| 测试 | pytest（63 单测 + 3 无头 E2E） |

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
py -3.14 -m pytest                 # 83 单测（自动隔离临时数据目录）
py -3.14 -m pytest -m e2e          # 6 无头 pywebview 端到端
```

## 项目结构

```
├── app.pyw                  # 入口（pywebview 窗口 + JS API 桥接 + 关窗兜底 + 图标处理）
├── backend.py               # 后端 API（数据库/加密/搜索/备份/提醒/导出）
├── applog.py                # 崩溃兜底日志
├── MyNotepad.spec           # PyInstaller 打包配置
├── renderer/                # 前端（index.html + 4 主题样式 + Quill/KaTeX + js/ 10 模块）
├── data/                    # 运行时数据
├── resources/               # 图标 + 人脸检测模型
├── tests/                   # pytest 单测 + 无头 E2E
└── CLAUDE.md                # 开发文档（架构细节 + 更新历史）
```

## 已知限制

1. 前端为全局作用域多文件（无模块系统/构建工具），跨文件依赖靠加载顺序保证
2. 编辑器内复制图片走 Quill 剪贴板，粘贴出的副本仍以 data URI 内嵌，下次启动迁移外置
3. 任务栏图标需重启应用后更新（pywebview 框架限制）
