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
  'theme-panel','background-panel','confirm-dialog','input-dialog','move-notebook-panel','export-panel','table-picker',
  'emoji-panel','tag-picker-panel','tag-manager-panel','version-panel',
  'version-preview-panel','reminder-panel','reminder-list-panel','trash-panel',
  'password-panel','password-verify-panel',
  'math-panel','calendar-panel','divider-panel','sticker-panel','paper-panel',
  'link-panel','icon-preview-panel',
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

// ====== 统一对话框系统（Promise 化，替代原生 confirm/alert/prompt） ======
// 单例 resolver：新对话框顶掉旧对话框（旧 resolve false/null，绝不悬挂）

let _confirmResolver = null;   // confirm-dialog / info-dialog 共用
let _inputResolver = null;     // input-dialog
let saveIndicatorTimer = null; // showToast / showSaveToast 共用（03-notes.js 不再声明）

function _resolveConfirm(v) {
  const r = _confirmResolver;
  _confirmResolver = null;
  closePanel($('#confirm-dialog'));
  if (r) r(v);
}
function _resolveInput(v) {
  const r = _inputResolver;
  _inputResolver = null;
  closePanel($('#input-dialog'));
  if (r) r(v);
}

/** 通用确认：resolve(true)=确认 / resolve(false)=取消。danger 控制按钮危险样式 */
function showConfirmAsync({ title = '确认', message = '', okText = '确定', danger = false } = {}) {
  return new Promise((resolve) => {
    closeAllPanels();
    if (_confirmResolver) _confirmResolver(false);
    $('#confirm-dialog-title').textContent = title;
    $('#confirm-message').textContent = message;
    const ok = $('#btn-confirm-ok');
    const cancel = $('#btn-confirm-cancel');
    ok.textContent = okText;
    ok.classList.toggle('btn-danger', danger);
    ok.classList.toggle('btn-solid-secondary', !danger);
    cancel.style.display = '';
    _confirmResolver = resolve;
    openPanel($('#confirm-dialog'));
    setTimeout(() => ok.focus(), 50);
  });
}

/** 长文本提示（替代多行 alert）：与确认同框，隐藏取消按钮，任意方式关闭即 resolve */
function showInfoDialog({ title = '提示', message = '' } = {}) {
  return new Promise((resolve) => {
    closeAllPanels();
    if (_confirmResolver) _confirmResolver(false);
    $('#confirm-dialog-title').textContent = title;
    $('#confirm-message').textContent = message;
    const ok = $('#btn-confirm-ok');
    const cancel = $('#btn-confirm-cancel');
    ok.textContent = '知道了';
    ok.classList.remove('btn-danger');
    ok.classList.add('btn-solid-secondary');
    cancel.style.display = 'none';
    _confirmResolver = () => resolve();  // 任意关闭方式（ok/遮罩/Esc/关闭钮）都 resolve
    openPanel($('#confirm-dialog'));
    setTimeout(() => ok.focus(), 50);
  });
}

/** 通用输入：resolve(字符串)=确认 / resolve(null)=取消 */
function showInputDialog({ title = '输入', message = '', defaultValue = '', placeholder = '', okText = '确定' } = {}) {
  return new Promise((resolve) => {
    closeAllPanels();
    if (_inputResolver) _inputResolver(null);
    $('#input-dialog-title').textContent = title;
    $('#input-dialog-message').textContent = message;
    const input = $('#input-dialog-input');
    input.value = defaultValue;
    input.placeholder = placeholder || '';
    _inputResolver = resolve;
    openPanel($('#input-dialog'));
    setTimeout(() => { input.focus(); input.select(); }, 50);
  });
}

// 确认框按钮
$('#btn-confirm-ok').addEventListener('click', () => _resolveConfirm(true));
$('#btn-confirm-cancel').addEventListener('click', () => _resolveConfirm(false));
// 确认框遮罩点击 = 取消（通用 overlay 处理器只负责关闭面板，这里补 resolver）
$('#confirm-dialog').addEventListener('click', (e) => {
  if (e.target === $('#confirm-dialog')) _resolveConfirm(false);
});
// 输入框按钮 + 关闭钮 + 遮罩 = 取消
$('#btn-input-ok').addEventListener('click', () => _resolveInput($('#input-dialog-input').value));
$('#btn-input-cancel').addEventListener('click', () => _resolveInput(null));
document.querySelector('#input-dialog .btn-close-panel').addEventListener('click', () => _resolveInput(null));
$('#input-dialog').addEventListener('click', (e) => {
  if (e.target === $('#input-dialog')) _resolveInput(null);
});
// 输入框 Enter = 确认
$('#input-dialog-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); _resolveInput($('#input-dialog-input').value); }
});
// Esc = 取消当前对话框
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (_confirmResolver) _resolveConfirm(false);
  else if (_inputResolver) _resolveInput(null);
});

// ====== 统一 Toast 提示（替代一次性 alert；单元素模式，与现状一致） ======
function showToast(msg, { type = 'info', duration = 2500 } = {}) {
  let toast = document.getElementById('save-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'save-toast';
    toast.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);padding:10px 24px;color:#fff;border-radius:20px;font-size:14px;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,0.3);transition:all 0.3s ease;opacity:0;pointer-events:none;';
    document.body.appendChild(toast);
  }
  const colors = { info: '#4A5568', success: '#38A169', warn: '#D69E2E', error: '#E53E3E' };
  toast.style.background = colors[type] || colors.info;
  toast.textContent = msg;
  toast.style.opacity = '1';
  toast.style.transform = 'translateX(-50%) translateY(-10px)';
  if (saveIndicatorTimer) clearTimeout(saveIndicatorTimer);
  saveIndicatorTimer = setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(0)';
  }, duration);
}

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

