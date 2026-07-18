// ==========================================
// 我的记事本 — 前端应用逻辑
// ==========================================
//
// 依赖加载顺序（index.html）：
//   1. quill.js, katex (第三方库)
//   2. js/shared/utils.js   — escapeHtml, formatFileSize, debounce
//   3. js/shared/icons.js   — getIcon, ICONS
//   4. js/quill/quill-blots.js — AttachmentBlot, FontBlot, SizeBlot, MathFormula
//   5. js/quill/quill-deco.js  — Divider, Sticker, dividerTypes, stickerData
//   6. app.js (本文件)       — 主应用逻辑
//
// 全局状态（有意暴露，供各模块访问）：
//   state, dom, $, $$        — 核心状态与 DOM 引用
//   unlockedNotes             — 密码解锁记录
//   currentTagFilter          — 当前标签筛选
//   currentNotebookId         — 当前笔记本
//   App                       — 应用全局配置
//   NotepadConfig             — 共享数据常量命名空间

// ====== 全局数据常量命名空间 ======
var NotepadConfig = {
  // 各主题对应的封面颜色
  _themeCoverColors: { white: '#7D8A6E', cream: '#B8844A', pink: '#C0766E', blue: '#5E7DA8' },
  // 当前封面颜色（随主题切换）
  coverColors: ['#7D8A6E'],
  // 纸张样式定义
  paperStyles: [
    { id:'none', name:'空白', cls:'paper-none', innerCls:'' },
    { id:'line', name:'横线', cls:'paper-line', innerCls:'paper-line-inner' },
  ],
  // 标题艺术风格
  titleStyles: ['none', 'shadow', 'gradient', 'outline', 'serif'],
  titleStyleNames: { none:'默认', shadow:'阴影', gradient:'渐变', outline:'描边', serif:'衬线' },
};

// ====== 应用状态 ======
const state = {
  notes: [],
  activeNoteId: null,
  currentContent: '',       // 当前笔记的 HTML 内容（用于比较是否变化）
  currentTitle: '',         // 当前笔记已落库的标题（保存去重基线，独立于 state.notes 的即时 UI 更新）
  quill: null,
  isLoading: false,
  isSaving: false,
  currentTheme: 'white',
  globalBg: { type: 'color', value: '', opacity: 1.0, zoom: 100, posX: 50, posY: 50 },
  noteBgImagePath: null,    // 当前笔记自定义背景图片的绝对路径
};

// ====== DOM 引用 ======
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

const dom = {
  app: $('#app'),
  theme: $('body'),
  sidebar: $('#sidebar'),
  noteList: $('#note-list'),
  emptyHint: $('#empty-hint'),
  searchInput: $('#search-input'),
  btnSearchClear: $('#btn-search-clear'),
  btnNewNote: $('#btn-new-note'),
  btnTheme: $('#btn-theme'),
  btnBackground: $('#btn-background'),
  btnChangeIcon: $('#btn-change-icon'),
  editorArea: $('#editor-area'),
  globalBgLayer: $('#global-bg-layer'),
  noteBgLayer: $('#note-bg-layer'),
  editorContainer: $('#editor-container'),
  titleInput: $('#note-title-input'),
  quillEditor: $('#quill-editor'),
  noNoteHint: $('#no-note-hint'),
  // 工具栏自定义按钮
  btnVoice: $('#btn-voice'),
  btnInsertImage: $('#btn-insert-image'),
  btnInsertAttachment: $('#btn-insert-attachment'),
  // 面板
  themePanel: $('#theme-panel'),
  backgroundPanel: $('#background-panel'),
  confirmDialog: $('#confirm-dialog'),
  confirmMessage: $('#confirm-message'),
};

// ====== 前端错误上报（只传 message+stack，绝不传笔记内容） ======
let _errorReportCount = 0;
function reportError(message, stack, source) {
  try {
    if (_errorReportCount >= 20) return;  // 会话内限 20 条，防报错风暴打穿桥接
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.log_error) return;
    _errorReportCount++;
    window.pywebview.api.log_error(String(message || ''), String(stack || ''), String(source || 'js'));
  } catch (e) { /* 上报失败静默 */ }
}
window.onerror = (msg, src, line, col, err) => {
  reportError(msg, (err && err.stack) || (src + ':' + line + ':' + col), 'window.onerror');
};
window.addEventListener('unhandledrejection', (e) => {
  const r = e.reason;
  reportError((r && r.message) || String(r), (r && r.stack) || '', 'unhandledrejection');
});

// ====== 面板管理 ======

function openPanel(panel) {
  panel.style.display = 'flex';
}
function closePanel(panel) {
  panel.style.display = 'none';
}
// 所有面板 ID 列表（新增面板只需在此添加）
const ALL_PANEL_IDS = [
  'theme-panel','background-panel','confirm-dialog','export-panel','table-picker',
  'emoji-panel','tag-picker-panel','tag-manager-panel','version-panel',
  'version-preview-panel','reminder-panel','reminder-list-panel',
  'password-panel','password-verify-panel',
  'math-panel','calendar-panel','divider-panel','sticker-panel','paper-panel',
  'link-panel','icon-preview-panel','chem-struct-panel',
];

function closeAllPanels() {
  ALL_PANEL_IDS.forEach(id => {
    const p = document.getElementById(id);
    if (p) p.style.display = 'none';
  });
  // 也关闭旧 dom 引用中的面板
  if (dom.themePanel) dom.themePanel.style.display = 'none';
  if (dom.backgroundPanel) dom.backgroundPanel.style.display = 'none';
  if (dom.confirmDialog) dom.confirmDialog.style.display = 'none';
}

// 关闭按钮
$$('.btn-close-panel').forEach(btn => {
  btn.addEventListener('click', () => {
    const panelId = btn.dataset.panel;
    if (panelId) closePanel(document.getElementById(panelId));
  });
});

// 点击遮罩关闭
$$('.panel-overlay').forEach(overlay => {
  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) {
      closePanel(overlay);
    }
  });
});

// ====== 确认对话框 ======
let confirmCallback = null;

function showConfirm(message, callback) {
  dom.confirmMessage.textContent = message;
  confirmCallback = callback;
  openPanel(dom.confirmDialog);
}

$('#btn-confirm-cancel').addEventListener('click', () => {
  closePanel(dom.confirmDialog);
  confirmCallback = null;
});

$('#btn-confirm-ok').addEventListener('click', () => {
  closePanel(dom.confirmDialog);
  if (confirmCallback) {
    confirmCallback();
    confirmCallback = null;
  }
});

// ====== 编辑器 UI 显隐 ======
function showEditorUI() {
  const tb = document.querySelector('.ql-toolbar');
  if (tb) tb.classList.remove('hidden');
  const fsb = $('#font-size-bar');
  if (fsb) fsb.style.display = 'flex';
  dom.quillEditor.classList.remove('hidden');
  dom.noNoteHint.classList.add('hidden');
  dom.titleInput.classList.remove('hidden');
}
function hideEditorUI() {
  const tb = document.querySelector('.ql-toolbar');
  if (tb) tb.classList.add('hidden');
  const fsb = $('#font-size-bar');
  if (fsb) fsb.style.display = 'none';
  dom.quillEditor.classList.add('hidden');
  dom.noNoteHint.classList.remove('hidden');
  dom.titleInput.classList.add('hidden');
}

