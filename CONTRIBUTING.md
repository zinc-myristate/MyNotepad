# 参与开发（Contributing）

欢迎 issue 和 PR。这个项目是**一个人维护的 Windows 本地应用**，为了少走弯路，请先看下面几段。

## 环境

- Windows 10 1809+ / Windows 11（依赖系统自带的 **WebView2 运行时**）
- Python **3.14**
- 依赖：`python -m pip install -r requirements.txt`

```bash
python app.pyw          # 开发运行（数据在仓库 data/）
python build.py         # 打包（关实例 → 备份 data/resources → PyInstaller → 还原 → 冒烟）
python 诊断.bat          # 环境自检（只读）
```

> ⚠️ 直接跑 `pyinstaller MyNotepad.spec` 会**清空整个 `dist/MyNotepad`**，而运行数据就在
> `dist/MyNotepad/data/`。手工打包前先备份 `data/`，或者直接用 `build.py`。

## 提交前必须过

```bash
python -m ruff check .   # 阻断项
python -m pytest         # 单测（默认不含 e2e）
python -m pytest -m e2e  # 无头 E2E（真开窗口，约 10 分钟；改了界面/前端模块必跑）
```

CI（`.github/workflows/ci.yml`）会跑同样的三步，**e2e 失败会直接拦提交**。

## 代码约定（踩过坑的，别改回去）

- **脚本编码有讲究**（改了会乱码，别随手"统一成 UTF-8"）：
  - `启动.bat` 是 **UTF-8（无 BOM）+ `chcp 65001`**：它只 echo 自己的中文提示，切成 UTF-8 后中文正常、
    GitHub 上也能读；
  - `诊断.bat` **保持 GBK**：它要调用 `systeminfo` / `wmic` 这类按系统代码页输出的老工具，
    切成 UTF-8 会把**它们的**输出变成乱码；
  - `启动.vbs` **保持纯 ASCII**：Windows Script Host 按 ANSI 代码页读 .vbs（除非有 UTF-16 BOM），
    UTF-8 的中文在 MsgBox 里必然是乱码。
- **前端是一个 ES 模块图**，唯一入口 `renderer/js/app/00-main.js`。跨文件依赖一律写 `import`，
  不要往 index.html 里加脚本；新增模块不需要在任何数组里登记。`tests/test_frontend_modules.py`
  会静态检查孤儿模块 / 不存在的导出 / 忘了 import。
- **正文格式双轨**：新笔记是 Markdown（`notes.format='md'`），历史笔记是 Quill Delta。
  两种格式的指标口径（字数、待办）由测试锁死，改一处要同时改另一处。
- **渲染只有一个入口**：`14-markdown-render.js` 的 `renderMarkdown()`，预览与导出共用。
- **界面文案与注释用中文**，注释写"为什么"而不是"做了什么"——尤其是反直觉的取舍。
- **改完记得同步文档**：`CLAUDE.md`（维护者/AI 用的完整技术档案）与 `README.md`（用户看的），
  两者都不能过期（仓库里有一个 Claude Code hook 会在提交后自动同步，手工改也行）。
- 提交信息用中文短句概括（`第十一轮：图片文字识别（OCR）—— 四个入口 / 结果面板 / 打包修复`），
  一次提交一件事。

## 新增功能时的检查清单

1. 有没有**第二个真相源**？（例如属性面板改写正文而不是另存一份）
2. 加了新的桥接方法？**参数个数**要和前端调用一致——`tests/test_capture_templates_unit.py`
   里有一张参数个数守卫表，历史上两次踩过"桥接方法少一个参数导致点了没反应"。
3. 加了新的 `data/` 文件或设置项？**写进备份/导出/恢复的路径**，并补默认值。
4. 用了新的第三方库？**加进 `requirements.txt`（锁精确版本）、`THIRD_PARTY_NOTICES.md`，
   并在 `MyNotepad.spec` 里确认能被打包**（前端库要离线自带，不要 CDN）。
5. 改了 `notes` 表结构？写进 `backend.py` 的 `ALTER TABLE` 迁移区（老库要能平滑升级）。

## 发布前检查（维护者）

打 tag 让 [`release.yml`](.github/workflows/release.yml) 自动构建是最省事也最安全的路子：
CI 从源码构建、用临时数据目录冒烟，产物里**天然没有你的笔记**。

⚠️ **不要手工把自己本地的 `dist/MyNotepad/` 打包上传**——那里有你自己的 `data/`
（`notes.db`、`attachments/`、`backgrounds/`、`backups/`，由 `build.py` 还原进去的）。
工作流里有一道**隐私闸门**会在打包前拦截（`data/notes.db` 非空、或 attachments/backgrounds/
backups/custom_icon 里有文件就直接失败），但手工打包会绕过它。

发布前顺手确认：`git status` 干净、`data/` 与 `dist/` 没有被跟踪、截图里没有私人笔记内容。

## 素材与许可

- 图标 / 截图请用**自制或明确可商用**的素材。**不要**把游戏、动漫角色图放进仓库或 README。
- 提交代码即表示同意以本仓库的 [MIT](LICENSE) 许可发布。
