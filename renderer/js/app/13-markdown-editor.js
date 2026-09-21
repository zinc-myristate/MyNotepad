// ====== Markdown 编辑器（源码 + 实时预览双栏）======
// 第 6 轮「双轨并存」的 md 一侧：新建笔记默认走这里，历史 Delta 笔记仍走 Quill。
//
// 选型：CodeMirror 5（单文件 UMD）。CM6 是 ESM 模块图、必须打包，而本项目没有构建工具；
// CM5 直接 <script> 引入 + markdown 模式 + 继续列表插件，够用且零构建。
//
// 与保存链路的接法：输入 → setSaveDot('dirty') + debouncedSave()（复用 03-notes 的
// 500ms 防抖与串行化保存），内容由 03-notes 通过 getMarkdownContent() 取走。
// 预览是**节流**渲染（长文每敲一个字都重排会很卡），并且永远渲染"当前源码"。

import { dom, $, state } from './01-core.js';
import { debouncedSave, setSaveDot } from './03-notes.js';
import { bindPreviewLinks, hydrateMarkdownAssets, renderMarkdown } from './14-markdown-render.js';

const PREVIEW_DELAY = 180;      // 预览渲染节流（毫秒）

let cm = null;                  // CodeMirror 实例
let _previewTimer = null;
let _scrollLock = false;        // 防止两栏滚动互相触发成死循环
let _lastPreviewText = null;

export function isMarkdownEditorReady() {
  return !!cm;
}

/** 初始化（启动时调用一次）。缺库时降级为普通 textarea，功能仍在，只是没有高亮。 */
export function initMarkdownEditor() {
  if (cm) return;
  const ta = document.getElementById('md-source');
  if (!ta) return;
  if (!window.CodeMirror) {
    // 降级：直接用 textarea（没有语法高亮，但输入/保存/预览都照常）
    ta.addEventListener('input', onSourceInput);
    ta.addEventListener('scroll', () => syncScroll('source'));
    return;
  }
  cm = window.CodeMirror.fromTextArea(ta, {
    mode: 'markdown',
    lineWrapping: true,
    lineNumbers: false,
    theme: 'mddefault',
    viewportMargin: 40,
    tabSize: 2,
    extraKeys: {
      Enter: 'newlineAndIndentContinueMarkdownList',   // 列表内回车自动续 `- ` / `1. `
      'Shift-Tab': 'indentLess',
    },
  });
  cm.on('change', () => {
    if (state.isLoading) return;                      // 切换笔记时的程序化写入不算用户输入
    setSaveDot('dirty');
    debouncedSave();
    schedulePreview();
  });
  cm.on('scroll', () => syncScroll('source'));
  const pane = document.getElementById('md-preview-pane');
  if (pane) pane.addEventListener('scroll', () => syncScroll('preview'));
  bindPreviewLinks(document.getElementById('md-preview'));
}

function onSourceInput() {
  if (state.isLoading) return;
  setSaveDot('dirty');
  debouncedSave();
  schedulePreview();
}

/** 读出源码（保存链路用；一次性 flush 时也走它） */
export function getMarkdownContent() {
  if (cm) return cm.getValue();
  const ta = document.getElementById('md-source');
  return ta ? ta.value : '';
}

/** 载入一篇笔记的源码（切换笔记时调用；清空 undo 历史，避免撤销回上一篇内容） */
export function setMarkdownContent(text) {
  const value = text || '';
  if (cm) {
    cm.setValue(value);
    cm.clearHistory();
    cm.setCursor(0, 0);
    cm.scrollTo(0, 0);
  } else {
    const ta = document.getElementById('md-source');
    if (ta) ta.value = value;
  }
  _lastPreviewText = null;
  renderPreviewNow();
}

export function setMarkdownReadOnly(readonly) {
  if (cm) cm.setOption('readOnly', readonly ? 'nocursor' : false);
  const ta = document.getElementById('md-source');
  if (ta) ta.readOnly = !!readonly;
}

/** 显示/隐藏整个双栏（切换 Delta 笔记时隐藏） */
export function setMarkdownVisible(visible) {
  const pane = document.getElementById('md-editor');
  if (!pane) return;
  pane.classList.toggle('hidden', !visible);
  if (visible && cm) {
    // 容器从 display:none 恢复时必须 refresh，否则行高/滚动位置是脏的
    requestAnimationFrame(() => { try { cm.refresh(); } catch (e) { /* 忽略 */ } });
  }
}

export function schedulePreview() {
  if (_previewTimer) clearTimeout(_previewTimer);
  _previewTimer = setTimeout(renderPreviewNow, PREVIEW_DELAY);
}

export function renderPreviewNow() {
  if (_previewTimer) { clearTimeout(_previewTimer); _previewTimer = null; }
  const root = document.getElementById('md-preview');
  if (!root) return;
  const text = getMarkdownContent();
  if (text === _lastPreviewText) return;              // 没变就不重排
  _lastPreviewText = text;
  root.innerHTML = renderMarkdown(text, { noteId: state.activeNoteId });
  hydrateMarkdownAssets(root, state.activeNoteId);
}

/** 滚动同步：按比例对齐两栏（源码行高与预览块高不成正比，只能近似） */
function syncScroll(from) {
  if (_scrollLock) return;
  const pane = document.getElementById('md-preview-pane');
  if (!pane) return;
  _scrollLock = true;
  try {
    if (from === 'source') {
      if (!cm) return;
      const info = cm.getScrollInfo();
      const max = Math.max(1, info.height - info.clientHeight);
      const ratio = info.top / max;
      pane.scrollTop = ratio * Math.max(0, pane.scrollHeight - pane.clientHeight);
    } else if (cm) {
      const info = cm.getScrollInfo();
      const max = Math.max(1, info.height - info.clientHeight);
      const ratio = pane.scrollTop / Math.max(1, pane.scrollHeight - pane.clientHeight);
      cm.scrollTo(null, ratio * max);
    }
  } finally {
    requestAnimationFrame(() => { _scrollLock = false; });
  }
}

/** 在光标处插入一段 Markdown（工具栏/快捷键用；后续提交里接按钮） */
export function insertMarkdownSnippet(snippet, selectOffset) {
  const text = snippet || '';
  if (!cm) {
    const ta = document.getElementById('md-source');
    if (!ta) return;
    const pos = ta.selectionStart || 0;
    ta.value = ta.value.slice(0, pos) + text + ta.value.slice(ta.selectionEnd || pos);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return;
  }
  const sel = cm.getSelection();
  const doc = cm.getDoc();
  const from = doc.getCursor('from');
  if (sel && text.indexOf('\n') < 0) {
    doc.replaceSelection(text.replace('%s', sel));    // 包住选中文字
  } else {
    doc.replaceSelection(text);
  }
  if (typeof selectOffset === 'number') {
    doc.setCursor({ line: from.line, ch: from.ch + selectOffset });
  }
  cm.focus();
}
