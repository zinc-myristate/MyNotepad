// ==========================================
// 我的记事本 — 核心状态与共享 UI 原语
// ==========================================
//
// 本文件是模块图的叶子：只定义状态、DOM 引用、面板/对话框/Toast，不 import 任何应用模块，
// 因此必然先于其他模块求值——其他模块在**顶层**就会用到 dom / $ / $$。
// （改造前这些靠 index.html 的加载顺序保证；现在由 ES 模块图保证。）
//
// 第三方库 Quill / KaTeX 仍是经典脚本，由 index.html 在模块图之前加载，这里按全局使用。
//
// 对外导出（各模块显式 import，不再依赖全局作用域）：
//   state, dom, $, $$                    — 核心状态与 DOM 引用
//   NotepadConfig                        — 共享数据常量命名空间
//   openPanel/closePanel                 — 面板显隐
//   showConfirmAsync/showInfoDialog/showInputDialog — 统一对话框
//   showToast / saveIndicatorTimer       — 统一提示（后者带 setter，见下方注释）
//   showEditorUI/hideEditorUI/reportError
//
// 其余跨模块状态不在这里：unlockedNotes → 07，currentTagFilter → 05，currentNotebookId → 09。

// ====== 全局数据常量命名空间 ======
export var NotepadConfig = {
  // 各主题对应的封面颜色
  _themeCoverColors: { white: '#7D8A6E', cream: '#B8844A', pink: '#C0766E', blue: '#5E7DA8', dark: '#9CAE8B' },
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
export const state = {
  notes: [],
  activeNoteId: null,
  currentContent: '',       // 当前笔记的 HTML 内容（用于比较是否变化）
  currentTitle: '',         // 当前笔记已落库的标题（保存去重基线，独立于 state.notes 的即时 UI 更新）
  quill: null,
  // 当前笔记的正文格式：'delta'（Quill Delta JSON，历史笔记）或 'md'（Markdown 文本）。
  // 这是前端的**唯一格式判据**：编辑器分流、保存取值、存档基线全看它。
  noteFormat: 'md',
  isLoading: false,
  isSaving: false,
  currentTheme: 'white',
  globalBg: { type: 'color', value: '', opacity: 1.0, zoom: 100, posX: 50, posY: 50, blur: 0, scrim: 0.3, contentScrim: 0.3 },
  // 当前生效的背景模糊/界面薄纱/正文薄纱（笔记级背景优先，见 04-appearance 的 currentBgTuning）
  bgBlur: 0,
  bgScrim: 0.3,
  bgContentScrim: 0.3,
  noteBgImagePath: null,    // 当前笔记自定义背景图片的绝对路径
  searchQuery: '',          // 当前搜索词（列表摘要据此高亮）
  searchSnippets: {},       // note_id -> 命中片段（后端 notes_search 返回，仅命中集）
  searchMatched: new Set(), // 当前搜索命中的 note_id 集合（列表重建后重放筛选用，见 applySearchToDom）
  selectedIds: new Set(),   // 多选批量操作的选中集合（空集 = 非多选态）
};

// ====== DOM 引用 ======
export const $ = (sel) => document.querySelector(sel);
export const $$ = (sel) => document.querySelectorAll(sel);

export const dom = {
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
export function reportError(message, stack, source) {
  try {
    if (_errorReportCount >= 20) return;  // 会话内限 20 条，防报错风暴打穿桥接
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.log_error) return;
    _errorReportCount++;
    window.pywebview.api.log_error(String(message || ''), String(stack || ''), String(source || 'js'));
  } catch (e) { /* 上报失败静默 */ }
}

// 异常必须**看得见**：桥接调用被拒绝时，以前只在 error.log 里留痕，界面上毫无反应，
// 用户看到的就是"点了没反应"（真实案例：tkinter.filedialog 导入被删，插入图片/附件/
// 更换图标/选择背景四个功能同时静默失效，用户只能靠猜）。这里补一条 Toast，
// 带节流与会话上限，避免报错风暴刷屏。
let _errToastCount = 0;
let _lastErrToastAt = 0;
function notifyError(message) {
  const now = Date.now();
  if (_errToastCount >= 5 || now - _lastErrToastAt < 15000) return;
  _errToastCount++;
  _lastErrToastAt = now;
  showToast('操作失败：' + message, { type: 'error', duration: 5000 });
}

window.onerror = (msg, src, line, col, err) => {
  reportError(msg, (err && err.stack) || (src + ':' + line + ':' + col), 'window.onerror');
  notifyError(msg);
};
window.addEventListener('unhandledrejection', (e) => {
  const r = e.reason;
  const message = (r && r.message) || String(r);
  reportError(message, (r && r.stack) || '', 'unhandledrejection');
  notifyError(message);
});

// ====== 面板管理 ======

export function openPanel(panel) {
  panel.style.display = 'flex';
}
export function closePanel(panel) {
  panel.style.display = 'none';
}
// 所有面板 ID 列表（新增面板只需在此添加）
const ALL_PANEL_IDS = [
  'theme-panel','background-panel','confirm-dialog','input-dialog','move-notebook-panel','export-panel','table-picker',
  'emoji-panel','tag-picker-panel','tag-manager-panel','version-panel','todo-panel',
  'version-preview-panel','reminder-panel','reminder-list-panel','trash-panel',
  'password-panel','password-verify-panel',
  'math-panel','calendar-panel','divider-panel','sticker-panel','paper-panel',
  'link-panel','icon-preview-panel','quick-switch-panel','ocr-panel',
];

export function closeAllPanels() {
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
export let saveIndicatorTimer = null; // showToast / showSaveToast 共用（03-notes.js 不再声明）

// ESM：其他模块需要写入本变量（import 的绑定不可赋值），故导出 setter
export function setSaveIndicatorTimer(v) { saveIndicatorTimer = v; }

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
export function showConfirmAsync({ title = '确认', message = '', okText = '确定', danger = false } = {}) {
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
export function showInfoDialog({ title = '提示', message = '' } = {}) {
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
export function showInputDialog({ title = '输入', message = '', defaultValue = '', placeholder = '', okText = '确定' } = {}) {
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
export function showToast(msg, { type = 'info', duration = 2500 } = {}) {
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
export function showEditorUI() {
  const isMd = state.noteFormat === 'md';
  // Quill 工具栏与字体/字号栏只对 Delta 笔记有意义；Markdown 笔记用源码 + 预览
  const tb = document.querySelector('.ql-toolbar');
  if (tb) tb.classList.toggle('hidden', isMd);
  const mdtb = $('#md-toolbar');
  if (mdtb) mdtb.classList.toggle('hidden', !isMd);
  const fsb = $('#font-size-bar');
  if (fsb) fsb.style.display = isMd ? 'none' : 'flex';
  dom.quillEditor.classList.toggle('hidden', isMd);
  const mdPane = $('#md-editor');
  if (mdPane) mdPane.classList.toggle('hidden', !isMd);
  dom.noNoteHint.classList.add('hidden');
  dom.titleInput.classList.remove('hidden');
  // 状态栏 / 查找条 / 大纲在"无笔记"时用 CSS 隐藏（见 style.css）。
  // 做成 body 上的一个类而不是在这里 import 那三个模块：01-core 是叶子模块，
  // 反过来 import 会形成环；而「无笔记」本来就是整个编辑区的一种状态。
  document.body.classList.remove('no-active-note');
}
export function hideEditorUI() {
  const tb = document.querySelector('.ql-toolbar');
  if (tb) tb.classList.add('hidden');
  const mdtb = $('#md-toolbar');
  if (mdtb) mdtb.classList.add('hidden');
  const fsb = $('#font-size-bar');
  if (fsb) fsb.style.display = 'none';
  dom.quillEditor.classList.add('hidden');
  const mdPane = $('#md-editor');
  if (mdPane) mdPane.classList.add('hidden');
  dom.noNoteHint.classList.remove('hidden');
  dom.titleInput.classList.add('hidden');
  document.body.classList.add('no-active-note');
}

