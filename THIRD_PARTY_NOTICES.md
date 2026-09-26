# 第三方组件与许可

本仓库自身以 [MIT](LICENSE) 发布。下面列出**随本仓库、或随打包产物一起分发**的第三方组件及其授权条款。

- 前端库都是**原样自带的离线副本**（不依赖 CDN，见 `renderer/`），因此这里附上完整的许可文本。
- Python 依赖由 `requirements.txt` 锁定版本，打包进 exe；`python build.py` 会把**每个依赖自带的许可证
  文件**一并收进产物的 `licenses/` 目录（PyInstaller 只自动带上一部分包的元数据，所以这一步是显式的，
  见 `build_resources/collect_licenses.py`）。
- 本应用**不联网、不下载模型、不采集任何数据**；第三方库均只在本地运行。

---

## 1. 仓库自带的前端库（`renderer/`）

| 组件 | 版本 | 许可 | 用途 | 出处 |
|---|---|---|---|---|
| Quill | 2.x | BSD-3-Clause | 富文本编辑器（`quill.js`、`quill.snow.css`） | <https://github.com/slab/quill> |
| KaTeX | 见 `renderer/katex/` | MIT | 数学公式渲染 | <https://github.com/KaTeX/KaTeX> |
| markdown-it | 14.1.0 | MIT | Markdown 渲染（预览与导出的唯一入口） | <https://github.com/markdown-it/markdown-it> |
| DOMPurify | 3.1.6 | Apache-2.0 **或** MPL-2.0（双许可，本项目按 Apache-2.0 使用） | 预览 HTML 消毒 | <https://github.com/cure53/DOMPurify> |
| CodeMirror | 5.x | MIT | Markdown 源码编辑（含 `continuelist` / `markdown` 两个官方插件） | <https://github.com/codemirror/codemirror5> |
| highlight.js | 11.11.1 | BSD-3-Clause | 代码块语法高亮 | <https://github.com/highlightjs/highlight.js> |

Apache-2.0 的完整条款见 <https://www.apache.org/licenses/LICENSE-2.0>；DOMPurify 的许可声明同时写在
`renderer/vendor/purify.min.js` 的文件头里。其余各库的完整许可文本如下。

### Quill — BSD-3-Clause

完整文本见 [`renderer/quill.js.LICENSE.txt`](renderer/quill.js.LICENSE.txt)（`quill.js` 头部指向的就是它）：

```
Copyright (c) 2017-2024, Slab
Copyright (c) 2014, Jason Chen
Copyright (c) 2013, salesforce.com
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### KaTeX — MIT

```
The MIT License (MIT)

Copyright (c) 2013-2020 Khan Academy and other contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### markdown-it — MIT

```
Copyright (c) 2014 Vitaly Puzrin, Alex Kocharin.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### CodeMirror — MIT

```
Copyright (c) by Marijn Haverbeke and others

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### highlight.js — BSD-3-Clause

```
BSD 3-Clause License

Copyright (c) 2006-2024, Josh Goebel and other contributors
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.
3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## 2. 仓库自带的数据文件

| 文件 | 许可 | 出处 |
|---|---|---|
| `resources/haarcascade_frontalface_default.xml` | Intel License Agreement（OpenCV 的 BSD 风格授权） | <https://github.com/opencv/opencv> |

这个级联分类器文件（作者 Rainer Lienhart）自带许可声明，关键条款：

```
                        Intel License Agreement
                For Open Source Computer Vision Library

 Copyright (C) 2000, Intel Corporation, all rights reserved.
 Third party copyrights are property of their respective owners.

 Redistribution and use in source and binary forms, with or without modification,
 are permitted provided that the following conditions are met:

   * Redistribution's of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.

   * Redistribution's in binary form must reproduce the above copyright notice,
     this list of conditions and the following disclaimer in the documentation
     and/or other materials provided with the distribution.

   * The name of Intel Corporation may not be used to endorse or promote products
     derived from this software without specific prior written permission.

 This software is provided by the copyright holders and contributors "as is" and
 any express or implied warranties ... are disclaimed.
```

完整文本就在该文件头部（`resources/haarcascade_frontalface_default.xml` 前 60 行）。

---

## 3. 运行时依赖（打包进 exe，版本由 `requirements.txt` 锁定）

| 组件 | 版本 | 许可 | 用途 |
|---|---|---|---|
| pywebview | 6.2.1 | BSD-3-Clause | 桌面窗口 + JS 桥（WebView2） |
| pythonnet / clr_loader | 3.1.0 / 0.3.1 | MIT | pywebview 的 Windows 后端 |
| cryptography | 49.0.0 | Apache-2.0 或 BSD-3-Clause（双许可） | AES-256-GCM / PBKDF2（笔记加密） |
| Pillow | 12.3.0 | MIT-CMU (HPND) | 图像处理、截图、剪贴板位图 |
| python-docx | 1.2.0 | MIT | 导出 DOCX |
| openpyxl | 3.1.5 | MIT | 导出 XLSX |
| opencv-contrib-python | 5.0.0.93 | Apache-2.0（OpenCV）+ MIT（opencv-python 封装） | 「自定义图标」的智能裁切（缺失时降级居中裁切） |
| numpy | 2.4.6 | BSD-3-Clause | opencv 的依赖 |
| **pystray** | 0.19.5 | **LGPL-3.0** | 系统托盘常驻（见下方说明） |
| winocr | 0.0.15 | MIT | Windows 自带 OCR 的薄封装 |
| winrt-*（7 个包） | 3.2.1 | MIT | WinRT 投影运行时 |
| tkinter / Tcl-Tk | 随 CPython | PSF / Tcl-Tk 许可 | 文件对话框（系统自带组件） |

各包的完整许可证文本随发行版分发，并由构建脚本收集到产物的 `licenses/` 目录；
Apache-2.0 的全文见 <https://www.apache.org/licenses/LICENSE-2.0>。

### LGPL 组件说明（pystray）

`pystray` 以 **LGPL-3.0** 授权，本项目**未修改**它，且它在打包产物中以**独立模块文件**存在
（`dist/MyNotepad/_internal/pystray/…`）。按 LGPL 第 4 条的要求，使用者可以删除或替换该目录下的
文件（换成自己编译的版本）而不影响本程序其余部分——本程序对它的使用仅限于系统托盘图标与菜单。
完整条款见 <https://www.gnu.org/licenses/lgpl-3.0.html>，产物 `licenses/pystray/` 内也附有一份。

---

## 4. 构建与测试工具（不随产物分发）

| 工具 | 许可 | 说明 |
|---|---|---|
| PyInstaller | GPL-2.0-or-later **附 Bootloader Exception** | 仅用于构建。该例外明确允许把打包产物（含内嵌 bootloader）以**任意许可**分发，因此不影响本项目以 MIT 发布 |
| pytest / ruff | MIT | 仅开发与 CI 使用 |

---

## 5. 素材

`resources/icon.*` 为本项目自制的中性图标插画（记事本 + 笔），不含第三方素材。
**文档与截图里请勿使用他人享有著作权的图片**（例如游戏 / 动漫角色同人图）——README 的截图请用
默认主题或纯色背景拍摄。
