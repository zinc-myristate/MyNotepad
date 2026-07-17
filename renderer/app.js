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

// ====== Quill 编辑器初始化 ======
// 字体/字号 Blot 已在 js/quill/quill-blots.js 中注册

function initQuill() {
  const quill = new Quill('#quill-editor', {
    theme: 'snow',
    placeholder: '开始写点什么…',
    modules: {
      toolbar: '#editor-toolbar'
    }
  });

  // 表格删除增强：Quill 默认在单元格开头吃掉 Backspace（空 handler 保护结构），
  // 导致内容删完后行骨架（标题行）永远删不掉。改为：整行为空时 Backspace 删该行，
  // 删到只剩一行时删除整个表格。
  const _bsBindings = quill.keyboard.bindings['Backspace'] || [];
  const _tblNoop = _bsBindings.find(b => b.offset === 0 && Array.isArray(b.format) && b.format.includes('table'));
  if (_tblNoop) {
    _tblNoop.handler = function (range) {
      const [cell] = quill.getLine(range.index);
      if (!cell || cell.statics.blotName !== 'table') return false;
      const row = cell.parent;                    // table-row
      const body = row && row.parent;             // table-body
      const table = body && body.parent;          // table-container
      if (!row || !body || !table) return false;
      // 整行是否为空（每个单元格只剩自身换行符，length === 1）
      let rowEmpty = true;
      row.children.forEach(c => { if (c.length() > 1) rowEmpty = false; });
      if (!rowEmpty) return false;                // 行内还有内容：维持默认保护，不动
      const offset = table.offset(quill.scroll);
      if (body.children.length <= 1) {
        table.remove();                           // 最后一行 → 删除整个表格
      } else {
        row.remove();                             // 删除当前空行
      }
      quill.update(Quill.sources.USER);           // 走 user 变更，触发自动保存
      quill.setSelection(Math.max(0, offset - 1), 0, Quill.sources.SILENT);
      return false;
    };
  }

  // 列表 Word 模式：空列表项 Enter → 缩进降级或退出
  quill.keyboard.addBinding({
    key: 'Enter',
    collapsed: true,
    format: ['list'],
    empty: true
  }, function(range, context) {
    const fmt = context.format || {};
    if (fmt.indent && fmt.indent > 0) {
      // 有缩进 → 降一级，不退出列表
      this.quill.formatLine(range.index, range.length, 'indent', fmt.indent - 1, 'user');
    } else {
      // 无缩进 → 退出列表（和 Word 一致）
      this.quill.formatLine(range.index, range.length, 'list', false, 'user');
    }
  });

  // 待办清单勾选切换
  quill.root.addEventListener('click', (e) => {
    const li = e.target.closest('li');
    if (!li) return;
    const cur = li.getAttribute('data-list');
    if (cur !== 'unchecked' && cur !== 'checked') return;
    e.preventDefault();
    const blot = Quill.find(li);
    if (blot) blot.format('list', cur === 'unchecked' ? 'checked' : 'unchecked');
  });


  // 图片拖拽移动 + 滚轮缩放（Quill DOM 重建后自动恢复包装器）
  let _imgDrag = null;
  quill.root.addEventListener('mousedown', (e) => {
    // 检测图片：支持裸 img（向上查）和包装器内 img（向下查）
    var img = e.target.closest('img');
    var wrapper = null;
    if (!img) {
      wrapper = e.target.closest('.img-resizable');
      if (wrapper) img = wrapper.querySelector('img');
    }
    if (!img || img.classList.contains('ql-formula')) return;

    // 确保包装器存在（Quill 可能已销毁）
    if (!wrapper) wrapper = img.closest('.img-resizable');
    if (!wrapper) {
      wrapper = document.createElement('span');
      wrapper.className = 'img-resizable';
      wrapper.contentEditable = 'false';
      // 从 img data 属性恢复尺寸和位置
      if (img.dataset.w) wrapper.style.width = img.dataset.w + 'px';
      if (img.dataset.x) wrapper.style.left = img.dataset.x + 'px';
      if (img.dataset.y) wrapper.style.top = img.dataset.y + 'px';
      img.parentNode.insertBefore(wrapper, img);
      wrapper.appendChild(img);
    }

    e.preventDefault(); e.stopPropagation();
    _imgDrag = {
      el: wrapper, img: img,
      sx: e.clientX, sy: e.clientY,
      sl: parseInt(wrapper.style.left) || 0,
      st: parseInt(wrapper.style.top) || 0
    };
    wrapper.classList.add('dragging', 'selected');
    document.querySelectorAll('.img-resizable.selected').forEach(el => {
      if (el !== wrapper) el.classList.remove('selected');
    });
  });

  // 点击空白处取消选中
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.img-resizable')) {
      document.querySelectorAll('.img-resizable.selected').forEach(el => el.classList.remove('selected'));
    }
  });

  // 拖拽移动
  document.addEventListener('mousemove', (e) => {
    if (!_imgDrag) return;
    var d = _imgDrag;
    var dx = e.clientX - d.sx;
    var dy = e.clientY - d.sy;
    d.el.style.left = (d.sl + dx) + 'px';
    d.el.style.top = (d.st + dy) + 'px';
  });

  // 松开鼠标：保存位置
  document.addEventListener('mouseup', () => {
    if (!_imgDrag) return;
    _imgDrag.el.classList.remove('dragging');
    var x = parseInt(_imgDrag.el.style.left) || 0;
    var y = parseInt(_imgDrag.el.style.top) || 0;
    if (x) _imgDrag.img.dataset.x = x; else delete _imgDrag.img.dataset.x;
    if (y) _imgDrag.img.dataset.y = y; else delete _imgDrag.img.dataset.y;
    _imgDrag = null;
  });

  // 滚轮缩放选中图片
  quill.root.addEventListener('wheel', (e) => {
    var wrapper = e.target.closest('.img-resizable');
    if (!wrapper || !wrapper.classList.contains('selected')) return;
    e.preventDefault();
    e.stopPropagation();
    var curW = parseInt(wrapper.style.width) || wrapper.getBoundingClientRect().width;
    var newW = Math.max(50, Math.min(800, curW + (e.deltaY > 0 ? -10 : 10)));
    wrapper.style.width = newW + 'px';
    var img = wrapper.querySelector('img');
    if (img) img.dataset.w = newW;
  }, { passive: false });

  // 监听内容变化 → 防抖自动保存（500ms 合并连续输入，切换/失焦/Ctrl+S 时立即 flush）
  quill.on('text-change', () => {
    if (state.activeNoteId && !state.isLoading) {
      debouncedSave();
      // 延迟同步贴纸覆盖层（Quill 可能重建了 DOM）
      if (typeof syncStickersToOverlay === 'function') {
        clearTimeout(_stickerSyncTimer);
        _stickerSyncTimer = setTimeout(() => syncStickersToOverlay(), 150);
      }
    }
  });

  // 失焦时立即保存
  quill.root.addEventListener('blur', () => {
    if (state.activeNoteId && !state.isLoading) {
      flushSave();
    }
  });

  // 兜底定时器已移除，text-change 立即保存已覆盖所有场景

  // 活跃字体/字号注入：任何用户输入的文字自动附加格式
  let _fmtBusy = false;
  quill.on('text-change', (delta, _oldDelta, source) => {
    if (source !== 'user' || _fmtBusy) return;
    if (!activeFont && !activeSize && !activeColor) return;
    let start = null, end = null, idx = 0;
    for (const op of delta.ops) {
      if (op.retain) { idx += op.retain; continue; }
      if (op.delete) { idx -= op.delete; continue; }
      if (op.insert) {
        const len = typeof op.insert === 'string' ? op.insert.length : 1;
        if (start === null) start = idx;
        end = idx + len;
        idx += len;
      }
    }
    if (start == null || end == null || end <= start) return;
    const formats = {};
    if (activeFont) formats.myfont = activeFont;
    if (activeSize) formats.mysize = activeSize;
    if (activeColor) formats.color = activeColor;
    _fmtBusy = true;
    try { quill.formatText(start, end - start, formats, 'silent'); }
    finally { _fmtBusy = false; }
    if (activeFont) quill.format('myfont', activeFont);
    if (activeSize) quill.format('mysize', activeSize);
    if (activeColor) quill.format('color', activeColor);
  });

  // 处理粘贴图片
  quill.root.addEventListener('paste', async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) {
          await handleImageFile(file);
        }
        break;
      }
    }
  });

  // 处理拖拽文件
  quill.root.addEventListener('drop', async (e) => {
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) return;

    e.preventDefault();

    for (const file of files) {
      if (file.type.startsWith('image/')) {
        await handleImageFile(file);
      } else {
        await handleAttachmentFile(file);
      }
    }
  });

  quill.root.addEventListener('dragover', (e) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
  });


  state.quill = quill;
}

// ====== 图片处理 ======

async function handleImageFile(file) {
  if (!state.activeNoteId) {
    alert('请先选择或新建一篇笔记');
    return;
  }

  try {
    // pywebview: 拖拽的文件通常有 path 属性
    if (file.path) {
      await insertImageFromPath(file.path);
    } else {
      // 粘贴的图片：通过 Python 后端保存到临时文件
      const buffer = await file.arrayBuffer();
      const bytes = Array.from(new Uint8Array(buffer));
      // 通过后端保存临时文件
      const tempPath = await window.pywebview.api.save_temp_image(bytes, file.name);
      if (tempPath) {
        await insertImageFromPath(tempPath);
      }
    }
  } catch (err) {
    console.error('插入图片失败:', err);
    alert('插入图片失败：' + err.message);
  }
}

async function insertImageFromPath(sourcePath) {
  const result = await window.pywebview.api.file_copy_to_note(sourcePath, state.activeNoteId, 'image');
  if (!result) return;

  // 在 Quill 中插入图片
  const range = state.quill.getSelection(true);
  // pywebview: 用 base64 显示图片
  let fileUrl;
  try {
    fileUrl = await window.pywebview.api.read_file_base64(result.storedPath);
    if (!fileUrl) fileUrl = `file:///${result.storedPath.replace(/\\/g, '/')}`;
  } catch {
    fileUrl = `file:///${result.storedPath.replace(/\\/g, '/')}`;
  }
  state.quill.insertEmbed(range.index, 'image', fileUrl);
  state.quill.setSelection(range.index + 1);
}

// ====== 附件处理 ======

async function handleAttachmentFile(file) {
  if (!state.activeNoteId) {
    alert('请先选择或新建一篇笔记');
    return;
  }

  try {
    const tempPath = file.path;
    if (tempPath) {
      await insertAttachmentFromPath(tempPath, file.name);
    }
  } catch (err) {
    console.error('插入附件失败:', err);
    alert('插入附件失败：' + err.message);
  }
}

async function insertAttachmentFromPath(sourcePath, originalName) {
  const result = await window.pywebview.api.file_copy_to_note(sourcePath, state.activeNoteId, 'file');
  if (!result) return;

  // 在 Quill 中插入附件卡片
  const range = state.quill.getSelection(true);
  state.quill.insertEmbed(range.index, 'attachment', {
    id: result.id,
    filename: result.filename,
    originalName: result.original_name,
    fileSize: result.file_size,
    mimeType: result.mime_type,
    storedPath: result.storedPath
  });
  state.quill.setSelection(range.index + 1);
  state.quill.insertText(range.index + 1, '\n');
}

// ====== 工具栏按钮事件 ======

$('#btn-insert-image').addEventListener('click', async () => {
  if (!state.activeNoteId) {
    alert('请先选择或新建一篇笔记');
    return;
  }
  const filePath = await window.pywebview.api.pick_image();
  if (filePath) {
    await insertImageFromPath(filePath);
  }
});

$('#btn-insert-attachment').addEventListener('click', async () => {
  if (!state.activeNoteId) {
    alert('请先选择或新建一篇笔记');
    return;
  }
  const filePath = await window.pywebview.api.pick_attachment();
  if (filePath) {
    const fileName = filePath.split(/[/\\]/).pop();
    await insertAttachmentFromPath(filePath, fileName);
  }
});

// ====== 语音识别 ======
let voiceRecognition = null;
let isVoiceRecording = false;
let voiceStopTimer = null;
let _voiceRecId = 0;         // 实例 ID，防止竞态
let _voiceTempRange = null;  // 临时文字在 Quill 中的位置 { index, length }

function resetVoiceUI() {
  isVoiceRecording = false;
  if (voiceStopTimer) { clearTimeout(voiceStopTimer); voiceStopTimer = null; }
  dom.btnVoice.classList.remove('recording');
}

function setVoiceRecordingUI() {
  dom.btnVoice.classList.add('recording');
}

function createVoiceRecognition() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    console.warn('当前环境不支持语音识别');
    return null;
  }

  const recognition = new SpeechRecognition();
  const recId = ++_voiceRecId;  // 实例 ID，防止竞态
  recognition.lang = 'zh-CN';
  recognition.continuous = true;
  recognition.interimResults = true;
  let accFinal = '';  // 累积的最终文字（single-result 兼容）

  recognition.onstart = () => {
    isVoiceRecording = true;
    setVoiceRecordingUI();
    accFinal = '';
    _voiceTempRange = null;
  };

  recognition.onresult = (event) => {
    if (!state.quill || !state.activeNoteId) return;

    // 清空之前的临时文字
    clearVoiceTemp();

    let interimText = '';
    let newFinal = '';

    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) {
        newFinal += result[0].transcript;
      } else {
        interimText += result[0].transcript;
      }
    }

    // 累积最终文字并插入
    if (newFinal) {
      accFinal += newFinal;
      const range = state.quill.getSelection(true);
      state.quill.insertText(range.index, accFinal);
      state.quill.setSelection(range.index + accFinal.length);
      accFinal = '';
      _voiceTempRange = null;
    }

    // 插入新的中间临时文字（灰底斜体）
    if (interimText) {
      const range = state.quill.getSelection(true);
      const startIdx = range.index;
      state.quill.insertText(startIdx, interimText, { color: '#888888', italic: true });
      // 记录临时文字范围
      _voiceTempRange = { index: startIdx, length: interimText.length };
      // 应用临时视觉标记：包装为 .voice-temp
      const editor = state.quill.root;
      const tempNode = findVoiceTempNode(editor, startIdx);
      if (tempNode) {
        tempNode.classList.add('voice-temp');
      }
    }
  };

  recognition.onerror = () => {
    if (recId !== _voiceRecId) return;
    voiceRecognition = null;
    clearVoiceTemp();
    voiceStopTimer = setTimeout(resetVoiceUI, 2000);
  };

  recognition.onend = () => {
    if (isVoiceRecording && recId === _voiceRecId && voiceRecognition === recognition) {
      voiceRecognition = null;
      // 结束时确认所有临时文字：移除临时样式
      confirmVoiceTemp();
      resetVoiceUI();
    }
  };

  return recognition;
}

/** 查找 voice-temp 节点（从 Quill 编辑器中，按位置查找） */
function findVoiceTempNode(editor, startIdx) {
  let count = 0;
  function walk(node) {
    if (node.nodeType === 3) { // Text node
      count += node.textContent.length;
      if (count > startIdx) return node.parentElement;
    }
    for (const child of node.childNodes) {
      const r = walk(child);
      if (r) return r;
    }
    return null;
  }
  return walk(editor);
}

/** 清除临时文字 */
function clearVoiceTemp() {
  if (!_voiceTempRange || !state.quill) return;
  const { index, length } = _voiceTempRange;
  try {
    state.quill.deleteText(index, length);
  } catch(e) { /* 忽略——文字可能已被删除 */ }
  _voiceTempRange = null;
}

/** 确认临时文字：移除 .voice-temp 样式，只保留文字 */
function confirmVoiceTemp() {
  if (!state.quill) return;
  const editor = state.quill.root;
  editor.querySelectorAll('.voice-temp').forEach(el => {
    // 取消斜体和灰色（恢复默认样式）
    el.style.fontStyle = '';
    el.style.color = '';
    el.classList.remove('voice-temp');
  });
  _voiceTempRange = null;
}

function startVoiceRecording() {
  if (!state.activeNoteId) {
    alert('请先选择或新建一篇笔记');
    return;
  }

  // 每次启动前重建识别器，避免复用导致的状态异常
  voiceRecognition = createVoiceRecognition();

  if (!voiceRecognition) {
    alert('您的系统不支持语音识别功能\n\n需要 Windows 10/11 系统，\n并确保已安装中文语音包。');
    return;
  }

  try {
    voiceRecognition.start();
  } catch (err) {
    console.error('启动语音识别失败:', err);
    voiceRecognition = null;
    resetVoiceUI();
    alert('启动语音识别失败，请检查麦克风权限');
  }
}

function stopVoiceRecording() {
  // 防止重复调用
  if (!isVoiceRecording) return;
  isVoiceRecording = false;
  if (voiceStopTimer) { clearTimeout(voiceStopTimer); voiceStopTimer = null; }

  const rec = voiceRecognition;
  voiceRecognition = null;  // 立即清空引用，防止 onend 再次调用
  if (rec) {
    try { rec.stop(); } catch (e) { /* 忽略已停止的错误 */ }
  }
  resetVoiceUI();
}

dom.btnVoice.addEventListener('click', () => {
  if (isVoiceRecording) {
    stopVoiceRecording();
  } else {
    startVoiceRecording();
  }
});

// ====== 自定义文字颜色 ======
const colorInput = $('#custom-color-input');
const colorDot = $('#custom-color-dot');

$('#btn-custom-color').addEventListener('click', () => {
  if (!state.quill || !state.activeNoteId) return;
  colorInput.click();
});

colorInput.addEventListener('input', () => {
  const color = colorInput.value;
  colorDot.style.background = color;
  activeColor = color;
  if (state.quill && state.activeNoteId) {
    const range = state.quill.getSelection(true);
    state.quill.format('color', color);
    if (range.length > 0) state.quill.formatText(range.index, range.length, 'color', color);
  }
});

// ====== 字体 & 字号（自定义 Blot: myfont / mysize） ======
const fontMap = {
  '':         '',
  'serif':    'Georgia,"Noto Serif SC","SimSun",serif',
  'monospace':'"Cascadia Code",Consolas,monospace',
  'cursive':  '"Bradley Hand ITC","Segoe Script","KaiTi","楷体",cursive',
};

$('#my-font-select').addEventListener('change', () => {
  const val = $('#my-font-select').value;
  const family = fontMap[val] || '';
  doApply('myfont', family);
});

$('#my-size-select').addEventListener('change', () => {
  const val = $('#my-size-select').value;
  doApply('mysize', val);
});

// 追踪当前生效的字体/字号（用于空段落时下拉同步）
let activeFont = '';
let activeSize = '';
let activeColor = '';

function doApply(formatName, value) {
  if (!state.quill || !state.activeNoteId) return;
  const q = state.quill;
  const range = q.getSelection(true);
  if (!range) return;

  if (range.length > 0) {
    if (value) {
      q.formatText(range.index, range.length, formatName, value);
    } else {
      q.formatText(range.index, range.length, formatName, false);
    }
  }
  if (value) {
    q.format(formatName, value);
  } else {
    q.format(formatName, false);
  }

  // 更新追踪变量
  if (formatName === 'myfont') activeFont = value;
  if (formatName === 'mysize') activeSize = value;
  if (formatName === 'color') {
    activeColor = value;
    const dot = document.getElementById('custom-color-dot');
    if (dot) dot.style.background = value || 'var(--text-primary)';
  }
}

function syncFontSizeDisplay() {
  const q = state.quill;
  if (!q) return;
  var colorDot = document.getElementById('custom-color-dot');
  q.on('selection-change', (range) => {
    if (!range) return;
    const fmt = q.getFormat(range);
    const fontSelect = $('#my-font-select');
    const sizeSelect = $('#my-size-select');
    // myfont：优先读格式，空段落用 activeFont
    const mf = fmt.myfont || activeFont;
    if (mf) {
      var e = Object.entries(fontMap).find(function(kv) { var v = kv[1]; return v && mf.indexOf(v.split(',')[0].replace(/['\"]/g,'')) >= 0; });
      fontSelect.value = e ? e[0] : '';
    } else { fontSelect.value = ''; }
    // mysize：优先读格式，空段落用 activeSize
    const ms = fmt.mysize || activeSize;
    var mm = ms.match(/(\d+)px/);
    if (mm && Array.from(sizeSelect.options).some(function(o) { return o.value === mm[1] + 'px'; })) sizeSelect.value = mm[1] + 'px';
    else if (!ms) sizeSelect.value = '16px';
    // 颜色指示器跟随光标位置
    if (colorDot) {
      var cursorColor = fmt.color || activeColor;
      if (cursorColor) {
        colorDot.style.background = cursorColor;
      } else {
        colorDot.style.background = 'var(--text-primary)';
      }
    }
  });
}

// ====== 超链接面板 ======
let _linkRange = null;
$('#btn-link').addEventListener('click', () => {
  if (!state.quill || !state.activeNoteId) return;
  const range = state.quill.getSelection(true);
  _linkRange = range;
  const text = state.quill.getText(range.index, range.length).trim();
  const fmt = state.quill.getFormat(range);
  const existingUrl = fmt && fmt.link ? fmt.link : '';
  $('#link-url-input').value = existingUrl || 'https://';
  $('#link-text-input').value = text || '';
  $('#btn-link-open').style.display = existingUrl ? '' : 'none';
  $('#btn-link-remove').style.display = existingUrl ? '' : 'none';
  $('#btn-link-save').textContent = existingUrl ? '更新链接' : '插入链接';
  openPanel($('#link-panel'));
  setTimeout(() => $('#link-url-input').focus(), 100);
});
$('#btn-link-save').addEventListener('click', () => {
  const url = $('#link-url-input').value.trim();
  const text = $('#link-text-input').value.trim();
  if (!url) return;
  // 安全校验：禁止 javascript: / data: 等危险协议
  if (/^(javascript|data|vbscript):/i.test(url)) { alert('不允许的链接协议'); return; }
  if (_linkRange.length > 0) {
    state.quill.format('link', url);
  } else if (text) {
    state.quill.insertText(_linkRange.index, text, 'link', url);
    state.quill.setSelection(_linkRange.index + text.length);
  } else {
    state.quill.insertText(_linkRange.index, url, 'link', url);
    state.quill.setSelection(_linkRange.index + url.length);
  }
  closePanel($('#link-panel'));
});
$('#btn-link-open').addEventListener('click', () => {
  const url = $('#link-url-input').value.trim();
  if (url) window.open(url, '_blank');
});
$('#btn-link-remove').addEventListener('click', () => {
  if (_linkRange.length > 0) state.quill.formatText(_linkRange.index, _linkRange.length, 'link', false);
  closePanel($('#link-panel'));
});

// ====== Emoji 面板 ======
const emojiPanel = $('#emoji-panel');
const emojiGrid = $('#emoji-grid');
const emojiCategories = $('#emoji-categories');

const emojiData = [
  { cat: '😊', name: '表情', emojis: '😀😃😄😁😅😂🤣😊😇🙂🙃😉😌😍🥰😘😗😙😚😋😛😝😜🤪🤨🧐🤓😎🥸🤩🥳😏😒😞😔😟😕🙁😫😩😢😭😤😡🤬🤯😳🥵🥶😱😨😰' },
  { cat: '🖐', name: '手势', emojis: '👍👎👌✌🤞🤟🤘🤙👋🤚✋🖖🖐👆👇👉👈✍🤳🙏💪🦵🦶👂👃' },
  { cat: '❤️', name: '爱心', emojis: '❤️🧡💛💚💙💜🖤🤍🤎💔❣️💕💞💓💗💖💘💝💟' },
  { cat: '📝', name: '物品', emojis: '📱💻⌨🖥🖨🖱🖲🕹🗜💾💿📀📼📷📸📹🎥📽🎞📞☎📟📠📺📻🎙🎚🎛🧭⏰🕰⌛📡🔋🔌💡🔦🕯🗑📋📌📍📎🖇✂🗒🗓📅📆📊📈📉📔📕📗📘📙📚📖🔖🎀' },
  { cat: '🎉', name: '活动', emojis: '🎉🎊🎈🎂🎁🏆🥇🥈🥉🏅🎖🎗🎃🎄🎋🎍🎎🎏🎐🎑🧧🎀🎁🎪🎭🎨🎬🎤🎧🎼🎹🥁🎸🎺🎻🎲♟🎯🎳🎮🎰' },
  { cat: '🌿', name: '自然', emojis: '🌿🌱🌳🌴🌵🍀🍁🍂🍃🌸🌺🌻🌹🌷💐🌾🍄🌰🐚🌍🌎🌏🌕🌖🌗🌘🌑🌒🌓🌔☀🌤⛅🌥🌦☁🌧⛈🌩⚡❄☃⛄💧🌊' },
  { cat: '🍔', name: '食物', emojis: '🍔🍟🍕🌭🥪🌮🌯🥗🍝🍜🍣🍤🍙🍚🍘🍥🍢🍡🍧🍨🍩🍪🎂🍰🧁🥧🍫🍬🍭🍮☕🍵🍺🍻🥂🍷🥃🍸🍹' },
  { cat: '▶', name: '符号', emojis: '▶⏸⏹⏺⏏⏭⏮⏯🔀🔁🔂🔄↔↕⬅➡⬆⬇↖↗↘↙↩↪⤴⤵🔃🔄🔙🔚🔛🔜🔝♻⚜🔱©®™▪◾◼◻⬛⬜🔲🔳🔴🔵🟠🟡🟢🟣🟤🟥🟧🟨🟩🟦🟪' },
];

function buildEmojiPanel() {
  emojiCategories.innerHTML = '';
  emojiData.forEach((cat, i) => {
    const btn = document.createElement('button');
    btn.className = 'emoji-cat-btn' + (i === 0 ? ' active' : '');
    btn.textContent = cat.cat;
    btn.title = cat.name;
    btn.addEventListener('click', () => {
      emojiCategories.querySelectorAll('.emoji-cat-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      showEmojiGrid(cat.emojis);
    });
    emojiCategories.appendChild(btn);
  });
  showEmojiGrid(emojiData[0].emojis);
}

function showEmojiGrid(emojis) {
  emojiGrid.innerHTML = '';
  for (const ch of [...emojis]) {
    const btn = document.createElement('button');
    btn.className = 'emoji-item';
    btn.textContent = ch;
    btn.addEventListener('click', () => {
      if (state.quill && state.activeNoteId) {
        const range = state.quill.getSelection(true);
        state.quill.insertText(range.index, ch);
        state.quill.setSelection(range.index + ch.length);
      }
    });
    emojiGrid.appendChild(btn);
  }
}

$('#btn-emoji').addEventListener('click', () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  buildEmojiPanel();
  openPanel(emojiPanel);
});

// ====== 待办清单（Quill List 格式） ======
// 扩展 Quill List 支持 unchecked/checked
const ListFormat = Quill.import('formats/list');
ListFormat.whitelist = ['ordered', 'bullet', 'unchecked', 'checked'];
Quill.register(ListFormat, true);

$('#btn-checklist').addEventListener('click', () => {
  if (!state.quill || !state.activeNoteId) return;
  const range = state.quill.getSelection(true);
  state.quill.formatLine(range.index, 0, 'list', 'unchecked');
  state.quill.insertText(range.index, '新任务', 'user');
});

// ====== 笔记置顶/收藏 ======
async function togglePinNote(noteId) {
  const note = state.notes.find(n => n.id === noteId);
  if (!note) return;
  const newPinned = note.is_pinned ? 0 : 1;
  // 先更新本地，再调 API，避免 UI 响应延迟
  note.is_pinned = Number(newPinned);
  await window.pywebview.api.notes_update(noteId, { is_pinned: Number(newPinned) });
  // loadNotes 刷新全量并重新排序
  await loadNotes();
}

async function toggleFavoriteNote(noteId) {
  const note = state.notes.find(n => n.id === noteId);
  if (!note) return;
  const newFav = note.is_favorite ? 0 : 1;
  await window.pywebview.api.notes_update(noteId, { is_favorite: newFav });
  note.is_favorite = newFav;
  renderNoteList();
}

/** 同步笔记字段到 state.notes（在 notes_update 后调用，保持面板 UI 一致） */
function syncNoteFields(noteId, fields) {
  const note = state.notes.find(n => n.id === noteId);
  if (note) Object.assign(note, fields);
}

// ====== 表格插入 ======
let tblRows = 3, tblCols = 3;
const tablePicker = $('#table-picker');
const tblRowsVal = $('#tbl-rows-val');
const tblColsVal = $('#tbl-cols-val');
const tablePreview = $('#table-preview');

function updateTablePreview() {
  let html = '';
  for (let r = 0; r < tblRows; r++) {
    html += '<tr>';
    for (let c = 0; c < tblCols; c++) {
      html += `<td>${r === 0 ? '标题' : '内容'}</td>`;
    }
    html += '</tr>';
  }
  tablePreview.innerHTML = html;
}

$('#tbl-rows-plus').addEventListener('click', () => {
  if (tblRows < 10) { tblRows++; tblRowsVal.textContent = tblRows; updateTablePreview(); }
});
$('#tbl-rows-minus').addEventListener('click', () => {
  if (tblRows > 2) { tblRows--; tblRowsVal.textContent = tblRows; updateTablePreview(); }
});
$('#tbl-cols-plus').addEventListener('click', () => {
  if (tblCols < 8) { tblCols++; tblColsVal.textContent = tblCols; updateTablePreview(); }
});
$('#tbl-cols-minus').addEventListener('click', () => {
  if (tblCols > 2) { tblCols--; tblColsVal.textContent = tblCols; updateTablePreview(); }
});

$('#btn-table').addEventListener('click', () => {
  if (!state.activeNoteId) {
    alert('请先选择或新建一篇笔记');
    return;
  }
  updateTablePreview();
  openPanel(tablePicker);
});

$('#btn-insert-table-confirm').addEventListener('click', () => {
  closePanel(tablePicker);
  if (!state.quill || !state.activeNoteId) return;

  // 生成表格 HTML（第一行 td 加粗模拟表头，Quill 对 th 支持不稳定）
  let html = '<table><tr>';
  for (let c = 0; c < tblCols; c++) html += '<td><strong>标题</strong></td>';
  html += '</tr>';
  for (let r = 1; r < tblRows; r++) {
    html += '<tr>';
    for (let c = 0; c < tblCols; c++) html += '<td>内容</td>';
    html += '</tr>';
  }
  html += '</table><p><br></p>';

  const range = state.quill.getSelection(true);
  state.quill.clipboard.dangerouslyPasteHTML(range.index, html);
  state.quill.setSelection(range.index + html.length);
});

// 删除当前表格（优先删光标所在的表格，找不到再回退第一个）
$('#btn-delete-table').addEventListener('click', () => {
  if (!state.quill) return;
  let tableBlot = null;
  const range = state.quill.getSelection(true);
  if (range) {
    let [node] = state.quill.getLine(range.index);
    while (node && node !== state.quill.scroll) {
      if (node.statics && node.statics.blotName === 'table-container') { tableBlot = node; break; }
      node = node.parent;
    }
  }
  if (!tableBlot) {
    // 光标不在表格内：回退取文档中第一个表格
    const el = state.quill.root.querySelector('table');
    if (el && Quill.find(el)) tableBlot = Quill.find(el);
  }
  if (tableBlot) {
    const offset = tableBlot.offset(state.quill.scroll);
    tableBlot.remove();
    state.quill.update(Quill.sources.USER);  // 走 user 变更，触发自动保存
    state.quill.setSelection(Math.max(0, offset - 1), 0, Quill.sources.SILENT);
    closePanel($('#table-picker'));
  } else {
    alert('未找到表格');
  }
});

// ====== 导出笔记 ======
// ====== 导出笔记 ======

const exportPanel = $('#export-panel');
$('#btn-export').addEventListener('click', () => {
  if (!state.activeNoteId) {
    alert('请先选择一篇笔记');
    return;
  }
  const note = state.notes.find(n => n.id === state.activeNoteId);
  if (note && note.has_password && !unlockedNotes[state.activeNoteId]) {
    alert('请先解锁笔记再导出');
    return;
  }
  openPanel(exportPanel);
});

// 导出选项点击
$$('.export-option').forEach(btn => {
  btn.addEventListener('click', async () => {
    const format = btn.dataset.format;
    closePanel(exportPanel);

    if (!state.activeNoteId) return;

    const title = dom.titleInput.value.trim() || '未命名笔记';
    const htmlContent = state.quill ? state.quill.root.innerHTML : '';

    if (format === 'pdf') {
      alert('即将打开系统打印对话框。\n\n请在打印设置中选择"另存为 PDF"作为打印机，然后点击保存即可导出为 PDF 文件。');
      setTimeout(() => window.print(), 300);
      return;
    }

    try {
      const result = await window.pywebview.api.export_note(title, htmlContent, format);
      if (result) {
        // 导出成功
      }
    } catch (err) {
      console.error('导出失败:', err);
      alert('导出失败：' + (err.message || err.toString()));
    }
  });
});

// ====== 笔记操作 ======

async function loadNotes(retryCount = 0) {
  try {
    if (!window.pywebview || !window.pywebview.api) {
      if (retryCount < 10) {
        await new Promise(r => setTimeout(r, 500));
        return loadNotes(retryCount + 1);
      }
      return [];
    }
    state.notes = await window.pywebview.api.notes_list();
    // 防御：确保 is_pinned/is_favorite 字段存在且为数字（防止序列化变成字符串 "0"）
    state.notes = state.notes.map(n => ({ ...n, is_pinned: Number(n.is_pinned) || 0, is_favorite: Number(n.is_favorite) || 0 }));
    // 客户端排序双保险：置顶优先，同组按更新时间倒序
    state.notes.sort((a, b) => {
      if (a.is_pinned !== b.is_pinned) return b.is_pinned - a.is_pinned;
      return (b.updated_at || '').localeCompare(a.updated_at || '');
    });
    renderNoteList();
    return state.notes;
  } catch (err) {
    if (retryCount < 10) {
      await new Promise(r => setTimeout(r, 500));
      return loadNotes(retryCount + 1);
    }
    console.error('加载笔记列表失败:', err);
    return [];
  }
}

function renderNoteList() {
  dom.noteList.innerHTML = '';

  if (state.notes.length === 0) {
    dom.emptyHint.classList.remove('hidden');
  } else {
    dom.emptyHint.classList.add('hidden');
  }

  state.notes.forEach(note => {
    const item = document.createElement('div');
    item.className = `note-item${note.id === state.activeNoteId ? ' active' : ''}`;
    item.dataset.noteId = note.id;
    item.draggable = true;
    // SVG 图标定义
    const svgLock = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>';
    const svgPin = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 2v20M5 9h14l-3-7H8L5 9z"/><circle cx="12" cy="2" r="2"/></svg>';
    const svgStar = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
    const svgStarOutline = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
    const svgTrash = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>';
    const svgKey = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>';
    const pinIcon = note.is_pinned ? `<span class="note-status-icon pinned">${svgPin}</span>` : '';
    const favIcon = note.is_favorite ? `<span class="note-status-icon fav">${svgStar}</span>` : '';
    const lockIcon = note.has_password ? `<span class="note-status-icon locked">${svgLock}</span>` : '';
    // 生成封面
    const coverHtml = generateNoteCover(note);
    item.innerHTML = `
      <div class="note-item-row-cover">
        ${coverHtml}
        <div class="note-item-info">
          <div class="note-item-row">
            <span class="note-item-title">${escapeHtml(note.title || '未命名笔记')}</span>${pinIcon}${favIcon}${lockIcon}
          </div>
          <div class="note-item-meta">
            <span class="note-item-time">${(note.updated_at || '').substring(0, 16)}</span>
          </div>
        </div>
        <button class="note-item-password" title="设置密码" data-pwd-id="${note.id}">${svgKey}</button>
        <button class="note-item-pin" title="${note.is_pinned ? '取消置顶' : '置顶'}" data-pin-id="${note.id}">${svgPin}</button>
        <button class="note-item-fav" title="${note.is_favorite ? '取消收藏' : '收藏'}" data-fav-id="${note.id}">${note.is_favorite ? svgStar : svgStarOutline}</button>
        <button class="note-item-delete" title="删除笔记" data-delete-id="${note.id}">${svgTrash}</button>
      </div>
    `;

    // 置顶按钮
    item.querySelector('.note-item-pin').addEventListener('click', (e) => {
      e.stopPropagation();
      togglePinNote(note.id);
    });

    // 收藏按钮
    item.querySelector('.note-item-fav').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleFavoriteNote(note.id);
    });
    // 密码按钮
    item.querySelector('.note-item-password').addEventListener('click', async (e) => {
      e.stopPropagation();
      // 加密笔记需要先验证密码才能管理密码设置
      const hasPwd = await window.pywebview.api.note_has_password(note.id);
      if (hasPwd && !unlockedNotes[note.id]) {
        // 先弹出验证面板，验证成功后自动打开密码管理面板
        await flushSave();  // 旧笔记未保存内容先落盘，防止解锁后误存到加密笔记
        pendingSelectNoteId = note.id;
        state.activeNoteId = note.id;
        passwordVerifyCallback = () => {
          openPasswordPanel('set');
        };
        $('#password-verify-input').value = '';
        $('#password-error-msg').style.display = 'none';
        openPanel($('#password-verify-panel'));
        return;
      }
      // 无密码或已解锁：直接打开密码管理面板
      await selectNote(note.id);
      openPasswordPanel('set');
    });

    // 点击切换笔记（加密的需要验证密码）
    item.addEventListener('click', async (e) => {
      if (!e.target.closest('.note-item-delete, .note-item-pin, .note-item-fav')) {
        await verifyAndSelectNote(note.id);
      }
    });

    // 删除按钮
    item.querySelector('.note-item-delete').addEventListener('click', (e) => {
      e.stopPropagation();
      confirmDeleteNote(note.id, note.title);
    });

    dom.noteList.appendChild(item);
  });
}

function updateNoteListItem(noteId) {
  const note = state.notes.find(n => n.id === noteId);
  if (!note) return;

  // 更新列表中的标题和封面
  const item = dom.noteList.querySelector(`[data-note-id="${noteId}"]`);
  if (item) {
    const titleSpan = item.querySelector('.note-item-title');
    if (titleSpan) {
      titleSpan.textContent = note.title || '未命名笔记';
    }
    // 封面首字跟随标题实时更新（图片封面除外）
    if (note.cover_type !== 'image') {
      const coverEl = item.querySelector('.note-cover');
      if (coverEl && !coverEl.querySelector('img')) {
        coverEl.textContent = (note.title || '笔')[0];
      }
    }
  }

  // 更新活跃状态
  dom.noteList.querySelectorAll('.note-item').forEach(el => {
    el.classList.toggle('active', el.dataset.noteId === noteId);
  });
}

async function selectNote(noteId) {
  if (state.activeNoteId === noteId) return;
  if (isVoiceRecording) stopVoiceRecording();
  // 重置版本/公式/背景缓存状态防止跨笔记错乱
  currentPreviewVersionId = null;
  editingMathNode = null;
  state._noteBgDataUri = null;
  await flushSave();

  // 加载新笔记（传递解锁状态）
  state.isLoading = true;
  try {
    const isUnlocked = unlockedNotes[noteId] === true;
    const note = await window.pywebview.api.notes_get(noteId, isUnlocked);
    if (!note) return;

    // 如果笔记已加密且未解锁，不加载内容，显示密码验证面板
    if (note.is_encrypted) {
      state.activeNoteId = note.id;
      state.currentContent = '';
      state.currentTitle = note.title || '';
      dom.titleInput.value = note.title || '';
      dom.titleInput.classList.remove('hidden');
      dom.titleInput.readOnly = true;  // 加密未解锁时禁止编辑标题
      document.querySelector('.ql-toolbar')?.classList.remove('hidden');
      dom.quillEditor.classList.remove('hidden');
      dom.noNoteHint.classList.add('hidden');
      if (state.quill) {
        state.quill.setContents([]);
        state.quill.enable(false);
      }
      updateNoteListItem(noteId);
      state.isLoading = false;
      // 弹出密码验证面板
      pendingSelectNoteId = noteId;
      passwordVerifyCallback = null;  // 默认行为：解锁后加载笔记即可
      $('#password-verify-input').value = '';
      $('#password-error-msg').style.display = 'none';
      openPanel($('#password-verify-panel'));
      return;
    }

    state.activeNoteId = note.id;
    state.currentContent = note.content || '';
    state.currentTitle = note.title || '';

    // 显示编辑器，隐藏空提示
    showEditorUI();

    // 设置标题
    dom.titleInput.value = note.title || '';
    dom.titleInput.readOnly = false;  // 解锁后允许编辑标题

    // 设置编辑器内容
    if (state.quill) {
      state.quill.enable(true);
      if (note.content) {
        // Quill 内容可能是 HTML 或 Delta
        try {
          const delta = JSON.parse(note.content);
          state.quill.setContents(delta);
        } catch {
          // 如果不是 JSON，作为 HTML 处理
          state.quill.setText(note.content);
        }
      } else {
        state.quill.setContents([]);
      }
    }

    // 加载标签
    loadTagBar();
    // 应用笔记背景
    applyNoteBackground(note);
    // 应用纸张样式
    loadPaperForNote(note);
  } catch (err) {
    console.error('加载笔记失败:', err);
  }
  state.isLoading = false;

  // 同步贴纸到浮动覆盖层
  if (typeof syncStickersToOverlay === 'function') {
    setTimeout(() => syncStickersToOverlay(), 100);
  }
  updateLockButton();

  updateNoteListItem(noteId);
}

async function createNewNote() {
  try {
    // 先保存当前笔记
    await saveCurrentNote();

    const note = await window.pywebview.api.notes_create();
    if (!note) return;

    // 用 loadNotes 全量刷新以确保置顶排序正确
    await loadNotes();
    updateNotebookCount();
    await selectNote(note.id);

    // 聚焦标题输入框
    dom.titleInput.focus();
    dom.titleInput.select();
  } catch (err) {
    console.error('创建笔记失败:', err);
    alert('创建笔记失败：' + err.message);
  }
}

async function saveCurrentNote() {
  if (!state.activeNoteId) return;
  if (state._saving) return;
  if (state.quill && !state.quill.isEnabled()) return; // 加密未解锁时编辑器禁用，防止空内容覆盖
  const note = state.notes.find(n => n.id === state.activeNoteId);
  if (note && note.has_password && !unlockedNotes[state.activeNoteId]) return;

  state._saving = true;
  try {
    // 保存前同步贴纸覆盖层位置到 Quill blot
    if (typeof syncStickersToQuill === 'function') syncStickersToQuill();

    const title = dom.titleInput.value.trim() || '未命名笔记';
    const content = state.quill ? JSON.stringify(state.quill.getContents()) : '';
    // 去重基线用 currentTitle/currentContent，不能用 state.notes（标题输入处理器为刷新列表
    // 已提前更新 state.notes[].title，拿它比较会误判"无变化"导致纯标题修改永不落库）
    if (title === state.currentTitle && content === state.currentContent) return;

    await window.pywebview.api.notes_update(state.activeNoteId, { title, content });
    // 保存成功后才更新基线：失败时基线不动，下次自动重试
    state.currentTitle = title;
    state.currentContent = content;
    const noteIdx = state.notes.findIndex(n => n.id === state.activeNoteId);
    if (noteIdx >= 0) {
      state.notes[noteIdx].title = title;
      state.notes[noteIdx].content = content;
    }
    updateNoteListItem(state.activeNoteId);
  } catch (err) {
    console.error('保存笔记失败:', err.message || err);
  } finally {
    state._saving = false;
  }
}

// 防抖自动保存：连续输入合并为一次写库；flushSave 在切换/失焦/锁定等时机立即落盘
const debouncedSave = debounce(() => saveCurrentNote(), 500);
async function flushSave() {
  debouncedSave.cancel();
  await saveCurrentNote();
}

// 窗口关闭前兜底保存（尽力而为）
window.addEventListener('beforeunload', () => {
  debouncedSave.cancel();
  saveCurrentNote();
});

function confirmDeleteNote(noteId, title) {
  showConfirm(`确定要删除笔记「${escapeHtml(title || '未命名笔记')}」吗？\n\n此操作不可恢复，笔记中的图片和附件也会被删除。`, async () => {
    await deleteNoteById(noteId);
  });
}

async function deleteNoteById(noteId) {
  try {
    const wasActive = state.activeNoteId === noteId;
    if (wasActive) debouncedSave.cancel(); // 取消待保存任务，防止删除后迟到写库

    await window.pywebview.api.notes_delete(noteId);
    state.notes = state.notes.filter(n => n.id !== noteId);

    if (wasActive) {
      // 先保存再清除状态
      state.activeNoteId = null;
      state.currentContent = '';
      state.currentTitle = '';

      if (state.quill) {
        state.quill.setContents([]);
      }
      dom.titleInput.value = '';
      hideEditorUI();

      // 清除笔记背景
      dom.noteBgLayer.style.backgroundImage = '';
      dom.noteBgLayer.style.opacity = '';

      // 自动选择第一篇笔记
      if (state.notes.length > 0) {
        await selectNote(state.notes[0].id);
      }
    }

    renderNoteList();
    updateNotebookCount();
  } catch (err) {
    console.error('删除笔记失败:', err);
    alert('删除笔记失败：' + err.message);
  }
}

// 标题输入框事件 - 列表即时刷新 + 防抖持久化
dom.titleInput.addEventListener('input', () => {
  if (state.activeNoteId) {
    debouncedSave();
    const title = dom.titleInput.value.trim() || '未命名笔记';
    const noteIdx = state.notes.findIndex(n => n.id === state.activeNoteId);
    if (noteIdx >= 0) {
      state.notes[noteIdx].title = title;
      updateNoteListItem(state.activeNoteId);
    }
  }
});

// 手动保存按钮 + 提示
let saveIndicatorTimer = null;
function showSaveToast() {
  let toast = document.getElementById('save-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'save-toast';
    toast.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);padding:10px 24px;background:#38A169;color:#fff;border-radius:20px;font-size:14px;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(56,161,105,0.4);transition:all 0.3s ease;opacity:0;pointer-events:none;';
    document.body.appendChild(toast);
  }
  toast.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" style="vertical-align:middle;margin-right:6px;"><polyline points="20 6 9 17 4 12"/></svg>已保存';
  toast.style.opacity = '1';
  toast.style.transform = 'translateX(-50%) translateY(-10px)';
  if (saveIndicatorTimer) clearTimeout(saveIndicatorTimer);
  saveIndicatorTimer = setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(0)';
  }, 1500);
}

/** 通用 Toast 提示（无图标，自定义文字和颜色） */
function showToast(msg, bgColor) {
  var toast = document.getElementById('save-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'save-toast';
    toast.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);padding:10px 24px;color:#fff;border-radius:20px;font-size:14px;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,0.3);transition:all 0.3s ease;opacity:0;pointer-events:none;';
    document.body.appendChild(toast);
  }
  toast.style.background = bgColor || '#38A169';
  toast.textContent = msg;
  toast.style.opacity = '1';
  toast.style.transform = 'translateX(-50%) translateY(-10px)';
  if (saveIndicatorTimer) clearTimeout(saveIndicatorTimer);
  saveIndicatorTimer = setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(0)';
  }, 2000);
}

$('#btn-save').addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  await flushSave();
  // 手动保存时自动创建历史版本
  const title = dom.titleInput.value.trim() || '未命名笔记';
  const content = state.quill ? JSON.stringify(state.quill.getContents()) : '';
  window.pywebview.api.versions_create(state.activeNoteId, title, content).catch(e => console.error('版本创建失败:', e));
  showSaveToast();
});

// 新建笔记按钮
dom.btnNewNote.addEventListener('click', createNewNote);

// ====== 主题管理 ======

async function loadSettings(retryCount = 0) {
  try {
    if (!window.pywebview || !window.pywebview.api) {
      if (retryCount < 10) { await new Promise(r => setTimeout(r, 500)); return loadSettings(retryCount + 1); }
      return;
    }
    const settings = await window.pywebview.api.settings_get_all();
    state.currentTheme = settings.theme || 'white';
    state.globalBg.type = settings.bg_type || 'color';
    state.globalBg.value = settings.bg_value || '';
    state.globalBg.opacity = parseFloat(settings.bg_opacity) || 1.0;
    state.globalBg.zoom = parseInt(settings.bg_zoom) || 100;
    try { const pos = JSON.parse(settings.bg_pos || '{"x":50,"y":50}'); state.globalBg.posX = pos.x; state.globalBg.posY = pos.y; globalBgPos = pos; } catch(e) {}

    applyTheme(state.currentTheme);
    applyGlobalBackground();
    // 恢复之前保存的自适应配色
    if (state.globalBg.type === 'image' && state.globalBg.value) {
      const saved = await window.pywebview.api.settings_get('adaptive_color');
      if (saved) {
        try { applyAdaptiveUI(JSON.parse(saved)); } catch(e) {}
      } else {
        // 重新分析
        try {
          const dataUri = await window.pywebview.api.read_file_base64(state.globalBg.value);
          if (dataUri) analyzeImageColor(dataUri, (info) => applyAdaptiveUI(info));
        } catch(e) {}
      }
    }
  } catch (err) {
    console.error('加载设置失败:', err);
  }
}

function applyTheme(themeName) {
  state.currentTheme = themeName;
  dom.theme.setAttribute('data-theme', themeName);
  window.pywebview.api.settings_set('theme', themeName);

  // 切换封面颜色以匹配主题
  var coverColor = NotepadConfig._themeCoverColors[themeName];
  if (coverColor) {
    NotepadConfig.coverColors = [coverColor];
  }

  // 更新主题面板的选中状态
  $$('.theme-option').forEach(opt => {
    opt.classList.toggle('active', opt.dataset.theme === themeName);
  });

  // 如果全局背景是主题色，随主题变化
  if (state.globalBg.type === 'color') {
    applyGlobalBackground();

  }
}

async function applyGlobalBackground() {
  if (state.globalBg.type === 'color' || !state.globalBg.value) {
    dom.globalBgLayer.style.backgroundImage = '';
    dom.globalBgLayer.style.opacity = '';
    clearAdaptiveUI();
  } else if (state.globalBg.type === 'image' && state.globalBg.value) {
    const imgPath = state.globalBg.value;
    const zoom = state.globalBg.zoom || 100;
    const posX = state.globalBg.posX ?? 50;
    const posY = state.globalBg.posY ?? 50;
    // 优先使用缓存的 dataUri，其次尝试 read_file_base64，最后回退到文件路径
    var dataUri = state._globalBgDataUri || null;
    if (!dataUri) {
      try {
        dataUri = await window.pywebview.api.read_file_base64(imgPath);
      } catch (e) { /* ignore */ }
    }
    if (dataUri) {
      dom.globalBgLayer.style.backgroundImage = 'url(' + dataUri + ')';
      dom.globalBgLayer.style.opacity = state.globalBg.opacity;
      dom.globalBgLayer.style.backgroundSize = zoom + '% auto';
      dom.globalBgLayer.style.backgroundPosition = posX + '% ' + posY + '%';
      analyzeImageColor(dataUri, function(info) { applyAdaptiveUI(info); });
    } else {
      const fileUrl = 'file:///' + imgPath.replace(/\\/g, '/');
      dom.globalBgLayer.style.backgroundImage = 'url(' + fileUrl + ')';
      dom.globalBgLayer.style.opacity = state.globalBg.opacity;
      dom.globalBgLayer.style.backgroundSize = zoom + '% auto';
      dom.globalBgLayer.style.backgroundPosition = posX + '% ' + posY + '%';
      analyzeImageColor(fileUrl, function(info) { applyAdaptiveUI(info); });
    }
  }
}

async function applyNoteBackground(note) {
  if (!note) return;

  const bgType = note.bg_type || 'global';

  if (bgType === 'global') {
    // 跟随全局 → 清空笔记层，恢复全局层显示
    dom.noteBgLayer.style.backgroundImage = '';
    dom.noteBgLayer.style.opacity = '';
    state.noteBgImagePath = null;
    applyGlobalBackground();
  } else if (bgType === 'color') {
    // 使用主题色 → 隐藏两层，只显示纯色
    dom.globalBgLayer.style.backgroundImage = '';
    dom.globalBgLayer.style.opacity = '';
    dom.noteBgLayer.style.backgroundImage = '';
    dom.noteBgLayer.style.opacity = '';
    state.noteBgImagePath = null;
    clearAdaptiveUI();
  } else if (bgType === 'image' && note.bg_value) {
    // 自定义图片 → 隐藏全局层，只显示笔记自己的背景图
    dom.globalBgLayer.style.backgroundImage = '';
    dom.globalBgLayer.style.opacity = '';

    const imgPath = note.bg_value;
    const zoom = note.bg_zoom || 100;
    const posX = note.bg_pos_x ?? 50;
    const posY = note.bg_pos_y ?? 50;
    var dataUri = state._noteBgDataUri || null;
    if (!dataUri) {
      try {
        dataUri = await window.pywebview.api.read_file_base64(imgPath);
      } catch (e) { /* ignore */ }
    }
    if (dataUri) {
      dom.noteBgLayer.style.backgroundImage = 'url(' + dataUri + ')';
      dom.noteBgLayer.style.opacity = note.bg_opacity || 0.7;
      dom.noteBgLayer.style.backgroundSize = zoom + '% auto';
      dom.noteBgLayer.style.backgroundPosition = posX + '% ' + posY + '%';
      state.noteBgImagePath = note.bg_value;
      analyzeImageColor(dataUri, function(info) { applyAdaptiveUI(info); });
    } else {
      const fileUrl = 'file:///' + imgPath.replace(/\\/g, '/');
      dom.noteBgLayer.style.backgroundImage = 'url(' + fileUrl + ')';
      dom.noteBgLayer.style.opacity = note.bg_opacity || 0.7;
      dom.noteBgLayer.style.backgroundSize = zoom + '% auto';
      dom.noteBgLayer.style.backgroundPosition = posX + '% ' + posY + '%';
      state.noteBgImagePath = note.bg_value;
      analyzeImageColor(fileUrl, function(info) { applyAdaptiveUI(info); });
    }
  }
}

// 主题面板事件
$$('.theme-option').forEach(opt => {
  opt.addEventListener('click', () => {
    applyTheme(opt.dataset.theme);
    renderNoteList();  // 刷新封面颜色
    closePanel(dom.themePanel);
  });
});

dom.btnTheme.addEventListener('click', () => {
  updateThemePanelUI();
  openPanel(dom.themePanel);
});

function updateThemePanelUI() {
  $$('.theme-option').forEach(opt => {
    opt.classList.toggle('active', opt.dataset.theme === state.currentTheme);
  });
}

// ====== 背景设置面板 ======

let bgPanelTab = 'global-bg';

// 标签切换
$$('.panel-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('.panel-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    bgPanelTab = tab.dataset.tab;
    $$('.tab-content').forEach(c => c.style.display = 'none');
    $(`#tab-${bgPanelTab}`).style.display = 'block';
    if (bgPanelTab === 'note-cover') buildCoverPanel();
    updateBackgroundPanelUI();
  });
});

// 全局背景类型切换
$$('input[name="global-bg-type"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const imageSettings = $('#tab-global-bg .bg-image-settings');
    if (radio.value === 'image') {
      imageSettings.style.display = 'flex';
    } else {
      imageSettings.style.display = 'none';
      // 切换到主题色
      state.globalBg.type = 'color';
      state.globalBg.value = '';
      window.pywebview.api.settings_set('bg_type', 'color');
      window.pywebview.api.settings_set('bg_value', '');
      applyGlobalBackground();
  
    }
  });
});

// 笔记背景类型切换
$$('input[name="note-bg-type"]').forEach(radio => {
  radio.addEventListener('change', async () => {
    const imageSettings = $('#tab-note-bg .bg-image-settings');
    if (radio.value === 'image') {
      imageSettings.style.display = 'flex';
      // 切回自定义图片时，恢复之前的 bg_value
      if (state.activeNoteId) {
        await window.pywebview.api.notes_update(state.activeNoteId, {
          bg_type: 'image', bg_opacity: 1.0
        });
        syncNoteFields(state.activeNoteId, { bg_type: 'image', bg_opacity: 1.0 });
        const note = await window.pywebview.api.notes_get(state.activeNoteId);
        applyNoteBackground(note);
      }
    } else {
      imageSettings.style.display = 'none';
      if (state.activeNoteId) {
        await window.pywebview.api.notes_update(state.activeNoteId, {
          bg_type: radio.value, bg_opacity: 1.0
        });
        syncNoteFields(state.activeNoteId, { bg_type: radio.value, bg_opacity: 1.0 });
        const note = await window.pywebview.api.notes_get(state.activeNoteId);
        applyNoteBackground(note);
      }
    }
  });
});

// 选择全局背景图片
$('#btn-pick-global-bg').addEventListener('click', async () => {
  const result = await window.pywebview.api.pick_background();
  if (!result) return;

  const filePath = result.path;
  state.globalBg.type = 'image';
  state.globalBg.value = filePath;
  state._globalBgDataUri = result.dataUri;  // 缓存 base64
  window.pywebview.api.settings_set('bg_type', 'image');
  window.pywebview.api.settings_set('bg_value', filePath);
  $('#global-bg-filename').textContent = filePath.split(/[/\\]/).pop();
  applyGlobalBackground();

});

// 选择笔记背景图片
$('#btn-pick-note-bg').addEventListener('click', async () => {
  if (!state.activeNoteId) {
    alert('请先选择一篇笔记');
    return;
  }

  const result = await window.pywebview.api.pick_background();
  if (!result) return;

  const filePath = result.path;
  const opacity = parseInt($('#note-bg-opacity').value) / 100;
  await window.pywebview.api.notes_update(state.activeNoteId, {
    bg_type: 'image', bg_value: filePath, bg_opacity: opacity
  });
  syncNoteFields(state.activeNoteId, { bg_type: 'image', bg_value: filePath, bg_opacity: opacity });
  state._noteBgDataUri = result.dataUri;  // 缓存 base64
  $('#note-bg-filename').textContent = filePath.split(/[/\\]/).pop();

  const note = await window.pywebview.api.notes_get(state.activeNoteId);
  applyNoteBackground(note);
});

// 全局背景透明度滑块
$('#global-bg-opacity').addEventListener('input', () => {
  const val = parseInt($('#global-bg-opacity').value);
  $('#global-bg-opacity-val').textContent = val + '%';
  state.globalBg.opacity = val / 100;
  window.pywebview.api.settings_set('bg_opacity', String(val / 100));
  applyGlobalBackground();

});

// 全局背景缩放滑块
$('#global-bg-zoom').addEventListener('input', () => {
  const val = parseInt($('#global-bg-zoom').value);
  $('#global-bg-zoom-val').textContent = val + '%';
  state.globalBg.zoom = val;
  window.pywebview.api.settings_set('bg_zoom', String(val));
  applyGlobalBackground();

});

// 全局背景位置按钮
let globalBgPos = { x: 50, y: 50 };
['up','down','left','right'].forEach(dir => {
  $(`#bg-pos-${dir}`).addEventListener('click', () => {
    const step = 5;
    if (dir === 'up') globalBgPos.y = Math.max(0, globalBgPos.y - step);
    if (dir === 'down') globalBgPos.y = Math.min(100, globalBgPos.y + step);
    if (dir === 'left') globalBgPos.x = Math.max(0, globalBgPos.x - step);
    if (dir === 'right') globalBgPos.x = Math.min(100, globalBgPos.x + step);
    state.globalBg.posX = globalBgPos.x;
    state.globalBg.posY = globalBgPos.y;
    window.pywebview.api.settings_set('bg_pos', JSON.stringify(globalBgPos));
    applyGlobalBackground();

  });
});
$('#bg-pos-reset').addEventListener('click', () => {
  globalBgPos = { x: 50, y: 50 };
  state.globalBg.posX = 50; state.globalBg.posY = 50;
  window.pywebview.api.settings_set('bg_pos', JSON.stringify(globalBgPos));
  applyGlobalBackground();

});

// 笔记背景透明度滑块
$('#note-bg-opacity').addEventListener('input', async () => {
  const val = parseInt($('#note-bg-opacity').value);
  $('#note-bg-opacity-val').textContent = val + '%';
  if (state.activeNoteId) {
    await window.pywebview.api.notes_update(state.activeNoteId, { bg_opacity: val / 100 });
    syncNoteFields(state.activeNoteId, { bg_opacity: val / 100 });
    const note = await window.pywebview.api.notes_get(state.activeNoteId);
    applyNoteBackground(note);
  }
});

// 笔记背景缩放
let noteBgZoom = 100;
$('#note-bg-zoom').addEventListener('input', async () => {
  noteBgZoom = parseInt($('#note-bg-zoom').value);
  $('#note-bg-zoom-val').textContent = noteBgZoom + '%';
  if (state.activeNoteId) {
    await window.pywebview.api.notes_update(state.activeNoteId, { bg_zoom: noteBgZoom });
    syncNoteFields(state.activeNoteId, { bg_zoom: noteBgZoom });
    const note = await window.pywebview.api.notes_get(state.activeNoteId);
    applyNoteBackground(note);
  }
});

// 笔记背景位置
let noteBgPos = { x: 50, y: 50 };
$$('.note-pos-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const dir = btn.dataset.dir;
    if (dir === 'up') noteBgPos.y = Math.max(0, noteBgPos.y - 5);
    if (dir === 'down') noteBgPos.y = Math.min(100, noteBgPos.y + 5);
    if (dir === 'left') noteBgPos.x = Math.max(0, noteBgPos.x - 5);
    if (dir === 'right') noteBgPos.x = Math.min(100, noteBgPos.x + 5);
    if (state.activeNoteId) {
      await window.pywebview.api.notes_update(state.activeNoteId, { bg_pos_x: noteBgPos.x, bg_pos_y: noteBgPos.y });
      syncNoteFields(state.activeNoteId, { bg_pos_x: noteBgPos.x, bg_pos_y: noteBgPos.y });
      const note = await window.pywebview.api.notes_get(state.activeNoteId);
      applyNoteBackground(note);
    }
  });
});
$('#note-pos-reset').addEventListener('click', async () => {
  noteBgPos = { x: 50, y: 50 };
  if (state.activeNoteId) {
    await window.pywebview.api.notes_update(state.activeNoteId, { bg_pos_x: 50, bg_pos_y: 50 });
    syncNoteFields(state.activeNoteId, { bg_pos_x: 50, bg_pos_y: 50 });
    const note = await window.pywebview.api.notes_get(state.activeNoteId);
    applyNoteBackground(note);
  }
});

// 清除背景按钮
$('#btn-clear-global-bg').addEventListener('click', () => {
  state.globalBg.type = 'color';
  state.globalBg.value = '';
  state.globalBg.opacity = 1.0;
  state._globalBgDataUri = null;
  window.pywebview.api.settings_set('bg_type', 'color');
  window.pywebview.api.settings_set('bg_value', '');
  window.pywebview.api.settings_set('bg_opacity', '1.0');
  $('#global-bg-filename').textContent = '';
  $('#global-bg-opacity').value = 60;
  $('#global-bg-opacity-val').textContent = '60%';
  $('#tab-global-bg .bg-image-settings').style.display = 'none';
  $('input[name="global-bg-type"][value="color"]').checked = true;
  applyGlobalBackground();

});

$('#btn-clear-note-bg').addEventListener('click', async () => {
  if (state.activeNoteId) {
    await window.pywebview.api.notes_update(state.activeNoteId, {
      bg_type: 'global', bg_value: null, bg_opacity: 1.0
    });
    syncNoteFields(state.activeNoteId, { bg_type: 'global', bg_value: null, bg_opacity: 1.0 });
    state._noteBgDataUri = null; // 清除缓存
    const note = await window.pywebview.api.notes_get(state.activeNoteId);
    applyNoteBackground(note);
  }
  $('#note-bg-filename').textContent = '';
  $('#note-bg-opacity').value = 70;
  $('#note-bg-opacity-val').textContent = '70%';
  $('#tab-note-bg .bg-image-settings').style.display = 'none';
  $('input[name="note-bg-type"][value="global"]').checked = true;
});

// ====== 背景面板滚轮调节（透明度 + 缩放 + 图标圆角） ======
['#global-bg-opacity','#global-bg-zoom','#note-bg-opacity','#note-bg-zoom','#icon-radius'].forEach(sel => {
  const el = $(sel);
  if (!el) return;
  el.addEventListener('wheel', (e) => {
    e.preventDefault();
    const step = e.shiftKey ? 15 : 5;  // Shift加速
    const delta = e.deltaY > 0 ? -step : step;
    const val = Math.max(parseInt(el.min), Math.min(parseInt(el.max), parseInt(el.value) + delta));
    el.value = val;
    // 触发 input 事件让已有的处理逻辑生效
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, { passive: false });
});

dom.btnBackground.addEventListener('click', () => {
  updateBackgroundPanelUI();
  openPanel(dom.backgroundPanel);
});

// 更换图标 — 真·预览确认两步走
let _iconTempPath = null;
let _iconChanging = false;
let _iconRadius = 15;  // 圆角半径百分比（Win11 风格默认 15%）

function _showIconMsg(msg, isError) {
  const el = $('#icon-result-msg');
  if (el) {
    el.textContent = msg;
    el.style.color = isError ? 'var(--danger)' : 'var(--accent)';
  }
}

// 圆角滑杆：防抖调后端重渲染预览（捕获 tempPath 防迟到回调覆盖）
const _debouncedRadiusUpdate = debounce(async () => {
  const tp = _iconTempPath;
  if (!tp) return;
  try {
    const r = await window.pywebview.api.update_icon_preview(tp, _iconRadius);
    if (r && r.success && _iconTempPath === tp) {
      $('#icon-preview-img').src = r.preview;
    }
  } catch (e) { /* 预览更新失败不阻断流程 */ }
}, 150);

$('#icon-radius').addEventListener('input', () => {
  _iconRadius = parseInt($('#icon-radius').value, 10);
  $('#icon-radius-val').textContent = _iconRadius + '%';
  _debouncedRadiusUpdate();
});

dom.btnChangeIcon.addEventListener('click', async () => {
  if (_iconChanging) return;
  _iconChanging = true;
  _showIconMsg('', false);
  try {
    const result = await window.pywebview.api.pick_and_preview_icon();
    if (!result) return;
    if (result.success) {
      _iconTempPath = result.tempPath;
      _iconRadius = (typeof result.radiusPct === 'number') ? result.radiusPct : 15;
      $('#icon-radius').value = _iconRadius;
      $('#icon-radius-val').textContent = _iconRadius + '%';
      $('#icon-preview-img').src = result.preview;
      const sizeEl = $('#icon-preview-size');
      if (sizeEl && result.origSize) sizeEl.textContent = '原图：' + result.origSize + ' → 512×512';
      openPanel($('#icon-preview-panel'));
    } else {
      _showIconMsg(result.error || '选择失败', true);
    }
  } catch(e) { _showIconMsg('操作失败', true); }
  finally { _iconChanging = false; }
});

$('#btn-icon-confirm').addEventListener('click', async () => {
  if (!_iconTempPath) return;
  _debouncedRadiusUpdate.cancel();
  _showIconMsg('保存中…', false);
  const result = await window.pywebview.api.confirm_icon(_iconTempPath, _iconRadius);
  _iconTempPath = null;
  _showIconMsg(result && result.success ? (result.msg || '已更新') : (result.error || '失败'), !result || !result.success);
  setTimeout(() => closePanel($('#icon-preview-panel')), 1500);
});

$('#btn-icon-cancel').addEventListener('click', async () => {
  _debouncedRadiusUpdate.cancel();
  if (_iconTempPath) {
    await window.pywebview.api.cancel_icon(_iconTempPath);
    _iconTempPath = null;
  }
  closePanel($('#icon-preview-panel'));
});

$('#btn-icon-restore').addEventListener('click', async () => {
  _debouncedRadiusUpdate.cancel();
  _showIconMsg('恢复中…', false);
  const result = await window.pywebview.api.restore_default_icon();
  if (result && result.success) {
    $('#icon-preview-img').src = result.preview || '';
  }
  _showIconMsg(result && result.success ? (result.msg || '已恢复') : (result.error || '失败'), !result || !result.success);
  _iconTempPath = null;
  setTimeout(() => closePanel($('#icon-preview-panel')), 1500);
});

function updateBackgroundPanelUI() {
  // 全局背景
  $('input[name="global-bg-type"][value="' + (state.globalBg.type === 'image' ? 'image' : 'color') + '"]').checked = true;
  const globalImageSettings = $('#tab-global-bg .bg-image-settings');
  if (state.globalBg.type === 'image') {
    globalImageSettings.style.display = 'flex';
    $('#global-bg-filename').textContent = state.globalBg.value ? state.globalBg.value.split(/[/\\]/).pop() : '';
    $('#global-bg-opacity').value = Math.round(state.globalBg.opacity * 100);
    $('#global-bg-opacity-val').textContent = Math.round(state.globalBg.opacity * 100) + '%';
    $('#global-bg-zoom').value = state.globalBg.zoom || 100;
    $('#global-bg-zoom-val').textContent = (state.globalBg.zoom || 100) + '%';
  } else {
    globalImageSettings.style.display = 'none';
  }

  // 笔记背景
  if (state.activeNoteId) {
    const note = state.notes.find(n => n.id === state.activeNoteId);
    if (note) {
      const noteBgType = note.bg_type || 'global';
      const radio = $(`input[name="note-bg-type"][value="${noteBgType}"]`);
      if (radio) radio.checked = true;

      const noteImageSettings = $('#tab-note-bg .bg-image-settings');
      if (noteBgType === 'image') {
        noteImageSettings.style.display = 'flex';
        $('#note-bg-filename').textContent = note.bg_value ? note.bg_value.split(/[/\\]/).pop() : '';
        $('#note-bg-opacity').value = Math.round((note.bg_opacity || 0.7) * 100);
        $('#note-bg-opacity-val').textContent = Math.round((note.bg_opacity || 0.7) * 100) + '%';
        // 从数据库读取当前笔记的实际位置和缩放值
        $('#note-bg-zoom').value = note.bg_zoom || 100;
        $('#note-bg-zoom-val').textContent = (note.bg_zoom || 100) + '%';
        noteBgZoom = note.bg_zoom || 100;
        noteBgPos.x = note.bg_pos_x ?? 50;
        noteBgPos.y = note.bg_pos_y ?? 50;
      } else {
        noteImageSettings.style.display = 'none';
      }
    }
  }
}

// ====== 窗口关闭前保存 ======
let _intervals = [];
let _stickerSyncTimer = null;
window.addEventListener('beforeunload', async () => {
  _intervals.forEach(clearInterval);
  if (isVoiceRecording) stopVoiceRecording();
  if (state.activeNoteId && state.quill) {
    await saveCurrentNote();
  }
});

// ====== 键盘快捷键 ======
document.addEventListener('keydown', async (e) => {
  // Ctrl+N 新建笔记
  if (e.ctrlKey && e.key === 'n') {
    e.preventDefault();
    await createNewNote();
  }
  // Ctrl+S 手动保存
  if (e.ctrlKey && e.key === 's') {
    e.preventDefault();
    await flushSave();
  }
  // ESC 取消语音临时文字
  if (e.key === 'Escape' && isVoiceRecording) {
    e.preventDefault();
    clearVoiceTemp();
    stopVoiceRecording();
  }
});

// ====== 标签系统 ======
let currentTagFilter = null; // 当前筛选的标签 ID

async function loadTagBar() {
  if (!state.activeNoteId) { $('#tag-bar').classList.add('hidden'); return; }
  const tags = await window.pywebview.api.note_tags_get(state.activeNoteId);
  const tagList = $('#tag-list');
  tagList.innerHTML = '';
  tags.forEach(tag => {
    const chip = document.createElement('span');
    chip.className = 'tag-chip';
    chip.setAttribute('data-tag-color', tag.color || '');
    chip.innerHTML = `${escapeHtml(tag.name)}<span class="tag-remove" data-tag-id="${tag.id}">×</span>`;
    chip.querySelector('.tag-remove').addEventListener('click', async (e) => {
      e.stopPropagation();
      const current = await window.pywebview.api.note_tags_get(state.activeNoteId);
      const ids = current.filter(t => t.id !== tag.id).map(t => t.id);
      await window.pywebview.api.note_tags_set(state.activeNoteId, ids);
      loadTagBar();
      loadTagFilter();
    });
    tagList.appendChild(chip);
  });
  $('#tag-bar').classList.remove('hidden');
}

async function loadTagFilter() {
  const tags = await window.pywebview.api.tags_list();
  const container = $('#tag-filter-list');
  container.innerHTML = '';
  if (tags.length === 0) { $('#tag-filter').style.display = 'none'; return; }
  $('#tag-filter').style.display = 'block';
  tags.forEach(tag => {
    const chip = document.createElement('button');
    chip.className = 'tag-filter-chip';
    if (currentTagFilter === tag.id) chip.classList.add('active');
    chip.textContent = tag.name;
    chip.addEventListener('click', async () => {
      if (currentTagFilter === tag.id) {
        currentTagFilter = null;
        state.notes = await window.pywebview.api.notes_list();
      } else {
        currentTagFilter = tag.id;
        state.notes = await window.pywebview.api.notes_by_tag(tag.id);
      }
      renderNoteList();
      loadTagFilter();
    });
    container.appendChild(chip);
  });
}

$('#btn-clear-tag-filter').addEventListener('click', async () => {
  currentTagFilter = null;
  state.notes = await window.pywebview.api.notes_list();
  renderNoteList();
  loadTagFilter();
});

$('#btn-add-tag').addEventListener('click', () => openTagPicker());
$('#btn-tag-manager').addEventListener('click', () => openTagManager());

async function openTagPicker() {
  const allTags = await window.pywebview.api.tags_list();
  const noteTags = await window.pywebview.api.note_tags_get(state.activeNoteId);
  const selectedIds = noteTags.map(t => t.id);
  const list = $('#tag-picker-list');
  list.innerHTML = '';
  allTags.forEach(tag => {
    const chip = document.createElement('button');
    chip.className = 'tag-picker-chip';
    if (selectedIds.includes(tag.id)) chip.classList.add('selected');
    chip.textContent = tag.name;
    chip.addEventListener('click', () => {
      chip.classList.toggle('selected');
    });
    list.appendChild(chip);
  });
  openPanel($('#tag-picker-panel'));
}

$('#btn-save-tags').addEventListener('click', async () => {
  const selected = [...$('#tag-picker-list').querySelectorAll('.selected')]
    .map(el => {
      const tagName = el.textContent;
      return window.pywebview.api.tags_list().then(tags => tags.find(t => t.name === tagName)?.id);
    });
  const ids = (await Promise.all(selected)).filter(Boolean);
  await window.pywebview.api.note_tags_set(state.activeNoteId, ids);
  closePanel($('#tag-picker-panel'));
  loadTagBar();
  loadTagFilter();
});

// 清除当前笔记全部标签
$('#btn-clear-all-tags').addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  await window.pywebview.api.note_tags_set(state.activeNoteId, []);
  closePanel($('#tag-picker-panel'));
  loadTagBar();
  loadTagFilter();
});

async function openTagManager() {
  const tags = await window.pywebview.api.tags_list();
  const list = $('#tag-manager-list');
  list.innerHTML = '';
  tags.forEach(tag => {
    const item = document.createElement('div');
    item.className = 'tag-manager-item';
    item.innerHTML = `<span class="tag-chip" data-tag-color="${tag.color || ''}">${escapeHtml(tag.name)}</span>
      <button class="btn-link" data-delete-tag="${tag.id}">删除</button>`;
    item.querySelector('[data-delete-tag]').addEventListener('click', async () => {
      if (confirm(`确定删除标签「${tag.name}」？`)) {
        await window.pywebview.api.tags_delete(tag.id);
        openTagManager();
        loadTagFilter();
      }
    });
    list.appendChild(item);
  });
  openPanel($('#tag-manager-panel'));
}

$('#btn-create-tag').addEventListener('click', async () => {
  const name = $('#new-tag-input').value.trim();
  if (!name) return;
  await window.pywebview.api.tags_create(name);
  $('#new-tag-input').value = '';
  openTagManager();
  loadTagFilter();
});

// ====== 笔记本系统 ======
async function loadNotebooks() {
  const notebooks = await window.pywebview.api.notebooks_list();
  if (notebooks.length === 0) {
    await window.pywebview.api.notebooks_create('默认笔记本');
    return loadNotebooks();
  }
  // 渲染笔记本列表在侧边栏中
  // 简化实现：在搜索框下方显示笔记本选择
}

// ====== 历史版本 ======
let currentPreviewVersionId = null;

$('#btn-version-history').addEventListener('click', async () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  loadVersionList();
  openPanel($('#version-panel'));
});

async function loadVersionList() {
  const versions = await window.pywebview.api.versions_list(state.activeNoteId);
  const list = $('#version-list');
  list.innerHTML = '';
  // 操作栏
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:8px;margin-bottom:10px;';
  bar.innerHTML = '<button id="btn-delete-all-versions" class="btn-danger" style="flex:1;">删除全部</button>';
  bar.querySelector('#btn-delete-all-versions').addEventListener('click', async () => {
    if (!state.activeNoteId) return;
    if (confirm('确定删除当前笔记的全部历史版本？此操作不可恢复。')) {
      await window.pywebview.api.versions_delete_all(state.activeNoteId);
      loadVersionList();
    }
  });
  list.appendChild(bar);

  if (versions.length === 0) {
    const empty = document.createElement('p');
    empty.style.cssText = 'color:var(--text-muted);text-align:center;padding:20px;';
    empty.textContent = '暂无历史版本';
    list.appendChild(empty);
    return;
  }
  versions.forEach(v => {
    const item = document.createElement('div');
    item.className = 'version-item';
    item.innerHTML = `<span class="version-item-time">${v.created_at}</span>
      <span class="version-item-title">${escapeHtml(v.title || '无标题')}</span>
      <button class="btn-link" data-preview-version="${v.id}">预览</button>
      <button class="btn-link" data-delete-version="${v.id}" style="color:var(--danger);">删除</button>`;
    item.querySelector('[data-preview-version]').addEventListener('click', (e) => {
      e.stopPropagation();
      previewVersion(v.id);
    });
    item.querySelector('[data-delete-version]').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (confirm('确定删除此历史版本？此操作不可恢复。')) {
        await window.pywebview.api.versions_delete(v.id);
        loadVersionList();
      }
    });
    list.appendChild(item);
  });
}

async function previewVersion(vid) {
  const isUnlocked = unlockedNotes[state.activeNoteId] === true;
  const ver = await window.pywebview.api.versions_get(vid, isUnlocked);
  if (!ver) return;
  if (ver.is_encrypted) {
    alert('请先解锁笔记才能查看历史版本内容');
    return;
  }
  currentPreviewVersionId = vid;
  $('#version-preview-title').textContent = `版本预览 - ${ver.created_at}`;
  const content = $('#version-preview-content');
  try {
    const delta = JSON.parse(ver.content);
    const tmp = document.createElement('div');
    const q = new Quill(tmp);
    q.setContents(delta);
    content.innerHTML = q.root.innerHTML;
  } catch {
    content.textContent = ver.content;
  }
  closePanel($('#version-panel'));
  openPanel($('#version-preview-panel'));
}

$('#btn-restore-version').addEventListener('click', async () => {
  if (!currentPreviewVersionId) return;
  if (!confirm('确定恢复到此版本？当前内容将被覆盖。')) return;
  const isUnlocked = unlockedNotes[state.activeNoteId] === true;
  const note = await window.pywebview.api.versions_restore(currentPreviewVersionId, isUnlocked);
  if (!note) { alert('无法恢复：笔记已加密或版本不存在'); return; }
  if (note && state.quill) {
    try {
      const delta = JSON.parse(note.content);
      state.quill.setContents(delta);
    } catch {
      state.quill.root.innerHTML = note.content;
    }
    dom.titleInput.value = note.title || '';
    state.currentContent = note.content || '';
    state.currentTitle = note.title || '';
  }
  closePanel($('#version-preview-panel'));
  alert('已恢复到所选版本');
});

// ====== 自然语言日期解析器 ======
function parseNaturalDate(input) {
  if (!input || !input.trim()) return null;
  let text = input.trim();
  let repeatType = 'none';
  let repeatInterval = 1;

  // 提取重复前缀
  const repeatPatterns = [
    { regex: /^每天(早上|上午|中午|下午|晚上|早晨)?/, type: 'daily' },
    { regex: /^每周(隔)?(\d+)?/, type: 'weekly', intervalGroup: 2 },
    { regex: /^每个?月/, type: 'monthly' },
    { regex: /^每年/, type: 'yearly' },
    { regex: /^工作日/, type: 'weekday' },
    { regex: /^每逢?(周一|周二|周三|周四|周五|周六|周日|星期[一二三四五六日天])/, type: 'weekly' },
  ];

  for (const pat of repeatPatterns) {
    const m = text.match(pat.regex);
    if (m) {
      repeatType = pat.type;
      if (pat.intervalGroup && m[pat.intervalGroup]) {
        repeatInterval = parseInt(m[pat.intervalGroup]) || 1;
      }
      text = text.slice(m[0].length);
      break;
    }
  }

  // 星期名映射
  const weekNames = {
    '周一': 0, '周二': 1, '周三': 2, '周四': 3, '周五': 4, '周六': 5, '周日': 6,
    '星期一': 0, '星期二': 1, '星期三': 2, '星期四': 3, '星期五': 4, '星期六': 5, '星期日': 6, '星期天': 6,
    '周天': 6, 'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
  };

  const now = new Date();
  let targetDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let targetHour = 9, targetMinute = 0;
  let hasTime = false;
  let dateSet = false;

  // 匹配相对日期
  const dayPatterns = [
    { regex: /^今天/, offset: 0 },
    { regex: /^明天/, offset: 1 },
    { regex: /^后天/, offset: 2 },
    { regex: /^大后天/, offset: 3 },
    { regex: /^昨天/, offset: -1 },
    { regex: /^前天/, offset: -2 },
  ];
  for (const dp of dayPatterns) {
    if (text.startsWith(dp.regex.source.replace('^', ''))) {
      targetDate.setDate(targetDate.getDate() + dp.offset);
      text = text.slice(dp.regex.source.replace('^', '').length);
      dateSet = true;
      break;
    }
  }

  // 匹配星期
  if (!dateSet) {
    for (const [name, dow] of Object.entries(weekNames)) {
      let prefix = '';
      if (text.startsWith('下' + name)) { prefix = '下'; }
      else if (text.startsWith('下下' + name)) { prefix = '下下'; }
      else if (text.startsWith(name)) { prefix = ''; }
      else continue;

      if (prefix === '') {
        // 本周
        const todayDow = now.getDay();
        let diff = dow - todayDow;
        if (diff <= 0) diff += 7; // 本周已过则取下周
        targetDate.setDate(targetDate.getDate() + diff);
      } else if (prefix === '下') {
        const todayDow = now.getDay();
        let diff = dow - todayDow;
        if (diff <= 0) diff += 7;
        targetDate.setDate(targetDate.getDate() + diff + 7);
      } else {
        const todayDow = now.getDay();
        let diff = dow - todayDow;
        if (diff <= 0) diff += 7;
        targetDate.setDate(targetDate.getDate() + diff + 14);
      }
      text = text.slice(prefix.length + name.length);
      dateSet = true;
      break;
    }
  }

  // 匹配 N天后 / N周后 / N个月后
  const offsetMatch = text.match(/^(\d+)\s*(天|周|个?月|年)后/);
  if (offsetMatch) {
    const num = parseInt(offsetMatch[1]);
    const unit = offsetMatch[2];
    if (unit === '天') targetDate.setDate(targetDate.getDate() + num);
    else if (unit === '周') targetDate.setDate(targetDate.getDate() + num * 7);
    else if (unit.includes('月')) targetDate.setMonth(targetDate.getMonth() + num);
    else if (unit === '年') targetDate.setFullYear(targetDate.getFullYear() + num);
    text = text.slice(offsetMatch[0].length);
    dateSet = true;
  }

  // 匹配时间
  const timePatterns = [
    { regex: /^早上(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h },
    { regex: /^上午(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h },
    { regex: /^中午(\d{1,2})?点?(\d{1,2})?分?/, hourFn: (h, m) => h ? h : 12 },
    { regex: /^下午(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h === 12 ? 12 : h + 12 },
    { regex: /^晚上(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h === 12 ? 12 : h + 12 },
    { regex: /^傍晚(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h + 17 > 23 ? 23 : h + 17 },
    { regex: /^(\d{1,2}):(\d{2})/, hourFn: (h, m) => h },
  ];

  for (const tp of timePatterns) {
    const m = text.match(tp.regex);
    if (m) {
      const h = parseInt(m[1] || '0');
      const min = m[2] ? parseInt(m[2]) : 0;
      targetHour = tp.hourFn(h, min);
      targetMinute = min;
      hasTime = true;
      text = text.slice(m[0].length);
      break;
    }
  }

  if (!hasTime && text.trim()) {
    // 匹配纯数字时间如 "15:00"
    const tm = text.match(/^(\d{1,2})[：:](\d{2})/);
    if (tm) {
      targetHour = parseInt(tm[1]);
      targetMinute = parseInt(tm[2]);
      hasTime = true;
      text = text.slice(tm[0].length);
    }
  }

  // 格式化为日期时间字符串
  const y = targetDate.getFullYear();
  const mo = String(targetDate.getMonth() + 1).padStart(2, '0');
  const d = String(targetDate.getDate()).padStart(2, '0');
  const hh = String(targetHour).padStart(2, '0');
  const mm = String(targetMinute).padStart(2, '0');
  const remindAt = `${y}-${mo}-${d} ${hh}:${mm}`;

  // 生成人类可读的提示
  const weekNamesCN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const repeatNames = { none: '', daily: '每天', weekly: '每周', weekday: '工作日', monthly: '每月', yearly: '每年' };
  const dateStr = `${y}年${mo}月${d}日 ${weekNamesCN[targetDate.getDay()]}`;
  const timeStr = `${hh}:${mm}`;
  const repeatStr = repeatNames[repeatType] || '';
  const hint = repeatStr ? `${repeatStr} ${dateStr} ${timeStr}` : `${dateStr} ${timeStr}`;

  return { remindAt, repeatType, repeatInterval, hint, parsed: true };
}

// ====== 提醒系统 ======
let _editingReminderId = null; // 当前正在编辑的提醒 ID

// 打开提醒设置面板
$('#btn-reminder').addEventListener('click', async () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  _editingReminderId = null;
  $('#reminder-panel-title').textContent = '设置提醒';
  $('#reminder-content').value = '';
  $('#reminder-nl-input').value = '';
  $('#reminder-datetime').value = '';
  $('#reminder-parsed-hint').textContent = '';
  $('#reminder-parsed-hint').className = 'hint-text';
  $('#reminder-edit-id').value = '';
  $('#btn-delete-reminder').style.display = 'none';
  // 重置重复选项
  $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
  const defaultBtn = $('#reminder-repeat-options').querySelector('[data-repeat="none"]');
  if (defaultBtn) defaultBtn.classList.add('active');
  // 加载该笔记已有的提醒信息
  try {
    const reminders = await window.pywebview.api.reminder_list(state.activeNoteId);
    if (reminders && reminders.length === 1) {
      const r = reminders[0];
      _editingReminderId = r.id;
      $('#reminder-panel-title').textContent = '编辑提醒';
      $('#reminder-content').value = r.content || '';
      $('#reminder-datetime').value = r.remind_at.replace(' ', 'T');
      $('#reminder-edit-id').value = r.id;
      $('#btn-delete-reminder').style.display = 'block';
      $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
      const rb = $('#reminder-repeat-options').querySelector(`[data-repeat="${r.repeat_type}"]`);
      if (rb) rb.classList.add('active');
    }
  } catch(e) {}
  openPanel($('#reminder-panel'));
});

// 自然语言输入实时解析
$('#reminder-nl-input').addEventListener('input', () => {
  const val = $('#reminder-nl-input').value.trim();
  const hintEl = $('#reminder-parsed-hint');
  if (!val) { hintEl.textContent = ''; hintEl.className = 'hint-text'; return; }
  const parsed = parseNaturalDate(val);
  if (parsed) {
    hintEl.textContent = '📅 ' + parsed.hint;
    hintEl.className = 'hint-text';
    // 自动填充 datetime 选择器
    $('#reminder-datetime').value = parsed.remindAt.replace(' ', 'T');
    // 自动选择重复选项
    $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
    const rb = $('#reminder-repeat-options').querySelector(`[data-repeat="${parsed.repeatType}"]`);
    if (rb) rb.classList.add('active');
    else $('#reminder-repeat-options').querySelector('[data-repeat="none"]').classList.add('active');
  } else {
    hintEl.textContent = '无法识别，请手动选择时间';
    hintEl.className = 'hint-text error';
  }
});

// datetime 手动修改时清理解析提示
$('#reminder-datetime').addEventListener('input', () => {
  if ($('#reminder-nl-input').value.trim()) return;
});

// 重复选项点击
$$('#reminder-repeat-options .repeat-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

// 保存提醒
$('#btn-save-reminder').addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  const content = $('#reminder-content').value.trim();
  const nlInput = $('#reminder-nl-input').value.trim();
  let dt = $('#reminder-datetime').value;
  if (!dt) { alert('请设置提醒时间'); return; }

  // 如果用了自然语言且解析成功，优先使用解析结果
  let repeatType = 'none', repeatInterval = 1;
  if (nlInput) {
    const parsed = parseNaturalDate(nlInput);
    if (parsed) {
      dt = parsed.remindAt.replace(' ', 'T');
      repeatType = parsed.repeatType;
      repeatInterval = parsed.repeatInterval;
    }
  }
  // 从按钮获取重复类型
  if (repeatType === 'none') {
    const activeRepeat = $('#reminder-repeat-options').querySelector('.repeat-btn.active');
    if (activeRepeat) repeatType = activeRepeat.dataset.repeat;
  }
  const remindAt = dt.replace('T', ' ') + ':00';

  const editId = $('#reminder-edit-id').value;
  if (editId) {
    // 更新已有提醒
    await window.pywebview.api.reminder_update(editId, {
      content, remind_at: remindAt, repeat_type: repeatType, repeat_interval: repeatInterval, is_completed: 0
    });
  } else {
    await window.pywebview.api.reminder_create(state.activeNoteId, content || '未命名提醒', remindAt, repeatType, repeatInterval);
  }
  closePanel($('#reminder-panel'));
});

// 删除提醒
$('#btn-delete-reminder').addEventListener('click', async () => {
  const editId = $('#reminder-edit-id').value;
  if (editId) {
    await window.pywebview.api.reminder_delete(editId);
  }
  closePanel($('#reminder-panel'));
});

// ====== 提醒列表面板 ======
$('#btn-reminder-list').addEventListener('click', async () => {
  await loadReminderList();
  openPanel($('#reminder-list-panel'));
});

async function loadReminderList() {
  const container = $('#reminder-list-items');
  if (!container) return;
  try {
    const reminders = await window.pywebview.api.reminder_list_all();
    if (!reminders || reminders.length === 0) {
      container.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-muted);">暂无提醒</div>';
      return;
    }
    const svgCheck = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    const svgEdit = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
    const svgTrash = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
    const svgRepeat = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>';
    const repeatLabels = { none: '', daily: `${svgRepeat}每天`, weekly: `${svgRepeat}每周`, weekday: `${svgRepeat}工作日`, monthly: `${svgRepeat}每月`, yearly: `${svgRepeat}每年` };
    container.innerHTML = reminders.map(r => {
      const isCompleted = r.is_completed === 1;
      const repeatIcon = repeatLabels[r.repeat_type] || '';
      const noteTitle = r.note_title || '已删除笔记';
      return `<div class="reminder-item${isCompleted ? ' completed' : ''}" data-rid="${r.id}">
        <div class="reminder-item-content" title="点击跳转到笔记" data-noteid="${r.note_id}">${escapeHtml(r.content || '未命名提醒')}</div>
        <div class="reminder-item-meta">
          ${repeatIcon ? `<span class="reminder-item-repeat">${repeatIcon}</span>` : ''}
          <span>${r.remind_at}</span>
          <span class="reminder-item-note" data-noteid="${r.note_id}">${escapeHtml(noteTitle)}</span>
        </div>
        <div class="reminder-item-actions">
          ${!isCompleted ? `<button class="reminder-item-btn" title="完成" data-action="complete" data-rid="${r.id}">${svgCheck}</button>` : ''}
          <button class="reminder-item-btn" title="编辑" data-action="edit" data-rid="${r.id}">${svgEdit}</button>
          <button class="reminder-item-btn danger" title="删除" data-action="delete" data-rid="${r.id}">${svgTrash}</button>
        </div>
      </div>`;
    }).join('');

    // 绑定事件
    container.querySelectorAll('.reminder-item-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const rid = btn.dataset.rid;
        const action = btn.dataset.action;
        if (action === 'complete') {
          await window.pywebview.api.reminder_complete(rid);
          await loadReminderList();
        } else if (action === 'edit') {
          await editReminderFromList(rid);
        } else if (action === 'delete') {
          if (await window.pywebview.api.confirm('确定要删除此提醒吗？', '删除提醒')) {
            await window.pywebview.api.reminder_delete(rid);
            await loadReminderList();
          }
        }
      });
    });
    // 点击内容/笔记名跳转
    container.querySelectorAll('.reminder-item-content, .reminder-item-note').forEach(el => {
      el.addEventListener('click', async (e) => {
        e.stopPropagation();
        const noteId = el.dataset.noteid;
        if (noteId) {
          closePanel($('#reminder-list-panel'));
          await selectNote(noteId);
        }
      });
    });
  } catch(e) {
    container.innerHTML = '<div style="text-align:center;padding:30px;color:#E53E3E;">加载失败</div>';
  }
}

async function editReminderFromList(rid) {
  closePanel($('#reminder-list-panel'));
  // 获取提醒详情并打开编辑面板
  const r = await window.pywebview.api.reminder_get(rid);
  if (!r) return;
  _editingReminderId = r.id;
  // 切换到对应笔记
  if (r.note_id && r.note_id !== state.activeNoteId) {
    await selectNote(r.note_id);
  }
  $('#reminder-panel-title').textContent = '编辑提醒';
  $('#reminder-content').value = r.content || '';
  $('#reminder-datetime').value = (r.remind_at || '').replace(' ', 'T');
  $('#reminder-nl-input').value = '';
  $('#reminder-parsed-hint').textContent = '';
  $('#reminder-parsed-hint').className = 'hint-text';
  $('#reminder-edit-id').value = r.id;
  $('#btn-delete-reminder').style.display = 'block';
  $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
  const rb = $('#reminder-repeat-options').querySelector(`[data-repeat="${r.repeat_type}"]`);
  if (rb) rb.classList.add('active');
  else $('#reminder-repeat-options').querySelector('[data-repeat="none"]').classList.add('active');
  openPanel($('#reminder-panel'));
}

// ====== Toast 通知系统 ======
let _toastTimers = {};

function showToast(reminder) {
  const toastId = 'toast-' + reminder.id;
  // 避免重复弹出
  if (document.getElementById(toastId)) return;

  const container = $('#toast-container');
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.id = toastId;

  const content = reminder.content || '提醒';
  const time = reminder.remind_at || '';

  toast.innerHTML = `
    <div class="toast-content">🔔 ${escapeHtml(content)}</div>
    <div class="toast-time">${escapeHtml(time)}</div>
    <div class="toast-actions">
      <button class="toast-btn toast-btn-primary" data-action="complete">${svgCheck} 完成</button>
      <div class="snooze-dropdown">
        <button class="toast-btn snooze-toggle">🕐 稍后</button>
        <div class="snooze-menu">
          <button data-snooze="5">5 分钟后</button>
          <button data-snooze="15">15 分钟后</button>
          <button data-snooze="30">30 分钟后</button>
          <button data-snooze="60">1 小时后</button>
        </div>
      </div>
      <button class="toast-close" data-action="dismiss">✕</button>
    </div>
  `;

  container.appendChild(toast);

  // 完成按钮
  toast.querySelector('[data-action="complete"]').addEventListener('click', async () => {
    await window.pywebview.api.reminder_complete(reminder.id);
    removeToast(toastId);
  });

  // 关闭按钮
  toast.querySelector('[data-action="dismiss"]').addEventListener('click', () => {
    removeToast(toastId);
  });

  // 稍后下拉
  const snoozeToggle = toast.querySelector('.snooze-toggle');
  const snoozeMenu = toast.querySelector('.snooze-menu');
  snoozeToggle.addEventListener('click', (e) => {
    e.stopPropagation();
    snoozeMenu.classList.toggle('show');
  });
  toast.querySelectorAll('[data-snooze]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const minutes = parseInt(btn.dataset.snooze);
      await window.pywebview.api.reminder_snooze(reminder.id, minutes);
      snoozeMenu.classList.remove('show');
      removeToast(toastId);
    });
  });

  // 点击其他地方关闭下拉
  document.addEventListener('click', function hideSnooze(e) {
    if (!toast.contains(e.target)) {
      snoozeMenu.classList.remove('show');
    }
  }, { once: true });

  // 10 秒后自动消失
  _toastTimers[toastId] = setTimeout(() => {
    removeToast(toastId);
  }, 10000);
}

function removeToast(toastId) {
  const toast = document.getElementById(toastId);
  if (!toast) return;
  if (_toastTimers[toastId]) { clearTimeout(_toastTimers[toastId]); delete _toastTimers[toastId]; }
  toast.classList.add('removing');
  setTimeout(() => { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
}

// ====== 定时检查提醒 ======
function checkReminders() {
  if (!window.pywebview || !window.pywebview.api) return;
  window.pywebview.api.reminders_check().then(reminders => {
    if (reminders && reminders.length > 0) {
      reminders.forEach(r => {
        showToast(r);
        if (r.repeat_type && r.repeat_type !== 'none') {
          window.pywebview.api.reminder_update_next_repeat(r.id);
        } else {
          window.pywebview.api.reminder_complete(r.id);
        }
      });
    }
  }).catch(() => {});
}

// 首次 5 秒后检查，之后每 30 秒
setTimeout(() => { checkReminders(); _intervals.push(setInterval(checkReminders, 30000)); }, 5000);

// ====== 待办清单右键菜单：设置提醒 ======
let _checklistContextMenu = null;

function hideChecklistMenu() {
  if (_checklistContextMenu) {
    _checklistContextMenu.remove();
    _checklistContextMenu = null;
  }
}

document.addEventListener('contextmenu', (e) => {
  // 检查是否在待办清单项上
  if (!state.quill) return;
  const li = e.target.closest('li');
  if (!li) { hideChecklistMenu(); return; }
  const listType = li.getAttribute('data-list');
  if (listType !== 'unchecked' && listType !== 'checked') { hideChecklistMenu(); return; }

  e.preventDefault();
  hideChecklistMenu();

  const menu = document.createElement('div');
  menu.className = 'checklist-context-menu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';

  // 提取待办项文字
  const clone = li.cloneNode(true);
  // 移除嵌套列表
  clone.querySelectorAll('ul, ol').forEach(el => el.remove());
  const text = (clone.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 50);

  menu.innerHTML = `
    <button data-action="set-reminder">🔔 设置提醒</button>
    <button data-action="copy-text">📋 复制文字</button>
  `;

  menu.querySelector('[data-action="set-reminder"]').addEventListener('click', async () => {
    hideChecklistMenu();
    if (!state.activeNoteId) return;
    _editingReminderId = null;
    $('#reminder-panel-title').textContent = '设置提醒';
    $('#reminder-content').value = text;
    $('#reminder-nl-input').value = '';
    $('#reminder-datetime').value = '';
    $('#reminder-parsed-hint').textContent = '';
    $('#reminder-parsed-hint').className = 'hint-text';
    $('#reminder-edit-id').value = '';
    $('#btn-delete-reminder').style.display = 'none';
    $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
    const defaultBtn = $('#reminder-repeat-options').querySelector('[data-repeat="none"]');
    if (defaultBtn) defaultBtn.classList.add('active');
    openPanel($('#reminder-panel'));
  });

  menu.querySelector('[data-action="copy-text"]').addEventListener('click', () => {
    hideChecklistMenu();
    navigator.clipboard.writeText(text).catch(() => {});
  });

  document.body.appendChild(menu);
  _checklistContextMenu = menu;

  // 点击其他地方关闭
  setTimeout(() => {
    document.addEventListener('click', function closeMenu() {
      hideChecklistMenu();
      document.removeEventListener('click', closeMenu);
    }, { once: true });
  }, 0);
});

// 点击编辑器区域也关闭右键菜单
document.addEventListener('click', (e) => {
  if (_checklistContextMenu && !_checklistContextMenu.contains(e.target)) {
    hideChecklistMenu();
  }
});


// ====== LaTeX 数学公式 ======
let mathMode = 'inline'; // 'inline' | 'block'

// 分类标签切换
$$('.math-cat-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('.math-cat-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const cat = tab.dataset.cat;
    $$('.math-hint-group').forEach(g => g.style.display = g.dataset.cat === cat ? 'block' : 'none');
  });
});

// MathFormula Blot 已在 js/quill/quill-blots.js 中注册

let editingMathNode = null;

function editMathFormula(node) {
  editingMathNode = node;
  const latex = node.getAttribute('data-latex') || '';
  const display = node.getAttribute('data-display') || 'inline';
  mathMode = display;
  $('#math-input').value = latex;
  // 更新模式按钮
  $$('.math-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mathMode));
  updateMathPreview();
  // 改按钮文字
  $('#btn-insert-math').textContent = '更新公式';
  openPanel($('#math-panel'));
}

// 模式切换
$$('.math-mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    mathMode = btn.dataset.mode;
    $$('.math-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mathMode));
    updateMathPreview();
  });
});

// 实时预览
$('#math-input').addEventListener('input', updateMathPreview);
function updateMathPreview() {
  const latex = $('#math-input').value;
  const preview = $('#math-preview');
  if (!latex.trim()) { preview.innerHTML = '<span style="color:var(--text-muted);">预览</span>'; return; }
  try {
    katex.render(latex, preview, { displayMode: mathMode === 'block', throwOnError: false, strict: false, trust: true });
  } catch(e) {
    preview.innerHTML = `<span style="color:var(--danger);">格式错误: ${e.message}</span>`;
  }
}

// LaTeX 快捷提示
$$('.math-sym-btn').forEach(hint => {
  hint.addEventListener('click', () => {
    const input = $('#math-input');
    const latex = hint.dataset.latex;
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const text = input.value;
    input.value = text.substring(0, start) + latex + text.substring(end);
    input.focus();
    // 如果模板有 {}，光标移到第一个 {} 内
    const braceIdx = latex.indexOf('{}');
    if (braceIdx >= 0) {
      input.selectionStart = start + braceIdx + 1;
      input.selectionEnd = start + braceIdx + 1;
    } else {
      input.selectionStart = start + latex.length;
      input.selectionEnd = start + latex.length;
    }
    updateMathPreview();
  });
});

// 打开公式面板
$('#btn-math').addEventListener('click', () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  editingMathNode = null;
  $('#math-input').value = '';
  $('#btn-insert-math').textContent = '插入公式';
  $('#math-preview').innerHTML = '<span style="color:var(--text-muted);">预览</span>';
  $$('.math-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mathMode));
  openPanel($('#math-panel'));
});

// 插入/更新公式
$('#btn-insert-math').addEventListener('click', () => {
  const latex = $('#math-input').value.trim();
  if (!latex) { alert('请输入 LaTeX 公式'); return; }

  if (editingMathNode) {
    // 更新已有公式
    editingMathNode.setAttribute('data-latex', latex);
    editingMathNode.setAttribute('data-display', mathMode);
    editingMathNode.className = mathMode === 'block' ? 'math-block' : 'math-inline';
    try {
      katex.render(latex, editingMathNode, { displayMode: mathMode === 'block', throwOnError: false, strict: false, trust: true });
    } catch(e) {
      editingMathNode.textContent = '[错误]';
    }
    editingMathNode = null;
  } else {
    // 插入新公式
    if (!state.quill || !state.activeNoteId) return;
    const range = state.quill.getSelection(true);
    state.quill.insertEmbed(range.index, 'math-formula', {
      latex: latex,
      display: mathMode
    });
    state.quill.setSelection(range.index + 1);
    if (mathMode === 'block') {
      state.quill.insertText(range.index + 1, '\n');
    }
  }
  closePanel($('#math-panel'));
});

// ====== 密码保护 ======
const unlockedNotes = {}; // 本次会话已解锁的笔记 ID → true
let passwordPanelMode = 'set'; // 'set' | 'remove'

// 工具栏锁按钮
$('#btn-lock').addEventListener('click', async () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  const hasPwd = await window.pywebview.api.note_has_password(state.activeNoteId);
  if (hasPwd) {
    // 已加密 → 先落盘（此时后端仍解锁可加密写入），再清后端密钥缓存
    const noteId = state.activeNoteId;
    await flushSave();
    await window.pywebview.api.note_lock(noteId);
    delete unlockedNotes[noteId];
    state.activeNoteId = null;
    hideEditorUI();
    if (state.quill) state.quill.enable(true);
    updateLockButton();
    renderNoteList();
  } else {
    // 无密码 → 打开设置
    openPasswordPanel('set');
  }
});

function updateLockButton() {
  const btn = $('#btn-lock');
  if (!btn) return;
  if (state.activeNoteId && unlockedNotes[state.activeNoteId]) {
    // 已解锁 → 高亮锁图标
    btn.style.color = 'var(--accent)';
    btn.title = '锁定笔记';
  } else {
    btn.style.color = '';
    btn.title = state.activeNoteId ? '设置密码' : '锁定笔记';
  }
}

async function openPasswordPanel(mode) {
  passwordPanelMode = mode;
  const hasPwd = await window.pywebview.api.note_has_password(state.activeNoteId);
  $('#password-input1').value = '';
  $('#password-input2').value = '';
  if (mode === 'set') {
    $('#password-panel-title').textContent = hasPwd ? '修改密码' : '设置密码';
    $('#btn-save-password').textContent = hasPwd ? '修改密码' : '设置密码';
    $('#btn-remove-password').style.display = hasPwd ? 'block' : 'none';
  }
  openPanel($('#password-panel'));
}

$('#btn-save-password').addEventListener('click', async () => {
  const p1 = $('#password-input1').value;
  const p2 = $('#password-input2').value;
  if (!p1) { alert('请输入密码'); return; }
  if (p1 !== p2) { alert('两次输入不一致'); return; }
  if (p1.length < 6) { alert('密码至少 6 位'); return; }
  await flushSave();  // 设密码前先落盘，加密以最新内容为准
  const ok = await window.pywebview.api.note_set_password(state.activeNoteId, p1);
  if (!ok) { alert('密码设置失败：请先解锁笔记后再修改密码'); return; }
  closePanel($('#password-panel'));
  alert('密码设置成功！笔记内容已加密存储。\n\n请务必牢记密码：忘记密码将无法恢复笔记内容。');
  unlockedNotes[state.activeNoteId] = true;
  loadNotes().then(renderNoteList);
});

$('#btn-remove-password').addEventListener('click', async () => {
  const p = $('#password-input1').value;
  if (!p) { alert('请先输入当前密码'); return; }
  await flushSave();  // 先落盘，解密回写以最新内容为准
  const ok = await window.pywebview.api.note_remove_password(state.activeNoteId, p);
  if (ok) {
    closePanel($('#password-panel'));
    alert('密码已移除');
    delete unlockedNotes[state.activeNoteId];
    loadNotes().then(renderNoteList);
  } else {
    alert('密码错误');
  }
});

// 密码验证
let pendingSelectNoteId = null;
let passwordVerifyCallback = null;  // 验证成功后的自定义回调

// 密码输入框回车键直接验证
$('#password-verify-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    $('#btn-verify-password').click();
  }
});

async function verifyAndSelectNote(noteId) {
  const hasPwd = await window.pywebview.api.note_has_password(noteId);
  if (!hasPwd || unlockedNotes[noteId]) {
    unlockedNotes[noteId] = true;
    await selectNote(noteId);
    return;
  }
  await flushSave();  // 先把当前笔记未保存内容落盘（activeNoteId 此时仍指向旧笔记）
  pendingSelectNoteId = noteId;
  passwordVerifyCallback = null;  // 默认行为：解锁后加载笔记
  $('#password-verify-input').value = '';
  $('#password-error-msg').style.display = 'none';
  openPanel($('#password-verify-panel'));
}

$('#btn-verify-password').addEventListener('click', async () => {
  const p = $('#password-verify-input').value;
  if (!p) return;
  const ok = await window.pywebview.api.note_verify_password(pendingSelectNoteId, p);
  if (ok) {
    unlockedNotes[pendingSelectNoteId] = true;
    updateLockButton();
    closePanel($('#password-verify-panel'));
    // 不在此处保存：activeNoteId 可能已指向加密笔记，而编辑器内容并非它的
    //（未保存的旧笔记内容已在打开面板前 flush），仅取消待保存任务后重新加载
    debouncedSave.cancel();
    state.activeNoteId = null;
    await selectNote(pendingSelectNoteId);
    // 执行自定义回调（如果有）
    if (passwordVerifyCallback) {
      const cb = passwordVerifyCallback;
      passwordVerifyCallback = null;
      cb();
    }
  } else {
    $('#password-error-msg').style.display = 'block';
    $('#password-verify-input').value = '';
    $('#password-verify-input').focus();
  }
});

$('#btn-cancel-verify').addEventListener('click', () => {
  closePanel($('#password-verify-panel'));
  // 恢复之前的状态：如果之前有活跃笔记则切回去
  const prevNoteId = state.activeNoteId;
  state.activeNoteId = null;  // 重置，允许再次点击同一篇加密笔记
  if (pendingSelectNoteId && prevNoteId === pendingSelectNoteId) {
    // 用户取消了加密笔记的解锁，回到无笔记状态或之前的笔记
    hideEditorUI();
    if (state.quill) state.quill.enable(true);
    // 尝试加载第一篇非加密笔记
    const firstUnlocked = state.notes.find(n => !n.has_password);
    if (firstUnlocked) {
      selectNote(firstUnlocked.id);
    }
  }
  pendingSelectNoteId = null;
});

// 修改笔记点击事件：点击加密笔记时先验证
// 在 renderNoteList 中，note-item 的 click 需要调用 verifyAndSelectNote

// 工具栏加密码按钮
// 在标签栏的 btn-add-tag 旁边已有点击事件，无需额外 UI
// 在编辑器区域添加一个密码按钮的入口
// 实际上可以在侧边栏每个笔记的右键菜单里加

// ====== 笔记列表拖拽排序 ======
let dragSrcIndex = null;

dom.noteList.addEventListener('dragstart', (e) => {
  const item = e.target.closest('.note-item');
  if (!item) return;
  dragSrcIndex = [...dom.noteList.children].indexOf(item);
  item.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', '');
});

dom.noteList.addEventListener('dragend', (e) => {
  const item = e.target.closest('.note-item');
  if (item) item.classList.remove('dragging');
  dom.noteList.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
});

dom.noteList.addEventListener('dragover', (e) => {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const item = e.target.closest('.note-item');
  if (item) {
    dom.noteList.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    item.classList.add('drag-over');
  }
});

dom.noteList.addEventListener('drop', async (e) => {
  e.preventDefault();
  const item = e.target.closest('.note-item');
  if (!item || dragSrcIndex === null) return;
  item.classList.remove('drag-over');
  const dstIndex = [...dom.noteList.children].indexOf(item);
  if (dragSrcIndex === dstIndex) return;

  // 重新排列 state.notes
  const moved = state.notes.splice(dragSrcIndex, 1)[0];
  state.notes.splice(dstIndex, 0, moved);

  // 更新所有笔记的 sort_order
  for (let i = 0; i < state.notes.length; i++) {
    await window.pywebview.api.notes_update(state.notes[i].id, { sort_order: i });
  }
  renderNoteList();
  dragSrcIndex = null;
});

// ====== 通用拖拽排序函数 ======
function makeDraggable(containerSelector, buttonSelector, settingKey) {
  const container = document.querySelector(containerSelector);
  if (!container) return;

  const makeAllDraggable = () => {
    container.querySelectorAll(buttonSelector).forEach(btn => { btn.draggable = true; });
  };
  makeAllDraggable();

  container.addEventListener('dragstart', (e) => {
    const btn = e.target.closest(buttonSelector);
    if (!btn) return;
    btn.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', '');
  });

  container.addEventListener('dragend', (e) => {
    const btn = e.target.closest(buttonSelector);
    if (btn) btn.classList.remove('dragging');
    container.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    // 保存顺序
    const ids = [...container.querySelectorAll(buttonSelector)].map(b => b.id).filter(Boolean);
    if (ids.length > 0 && settingKey) {
      window.pywebview.api.settings_set(settingKey, JSON.stringify(ids));
    }
  });

  container.addEventListener('dragover', (e) => {
    e.preventDefault();
    const btn = e.target.closest(buttonSelector);
    if (btn) {
      container.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
      btn.classList.add('drag-over');
    }
  });

  container.addEventListener('drop', (e) => {
    e.preventDefault();
    const btn = e.target.closest(buttonSelector);
    if (!btn) return;
    btn.classList.remove('drag-over');
    const dragged = container.querySelector('.dragging');
    if (!dragged || dragged === btn) return;
    const rect = btn.getBoundingClientRect();
    const mid = rect.left + rect.width / 2;
    if (e.clientX < mid) {
      container.insertBefore(dragged, btn);
    } else {
      container.insertBefore(dragged, btn.nextSibling);
    }
  });

  // 加载保存的顺序
  if (settingKey) {
    (async () => {
      const order = await window.pywebview.api.settings_get(settingKey);
      if (order) {
        try {
          const ids = JSON.parse(order);
          ids.reverse().forEach(id => {
            const btn = container.querySelector('#' + id);
            if (btn) container.insertBefore(btn, container.firstChild);
          });
        } catch(e) {}
      }
    })();
  }
}

// ====== Dock 栏 + 工具栏按钮拖拽排序 ======
function initAllDrag() {
  makeDraggable('.sidebar-footer', '.btn-sidebar-footer', 'footer_order');
  makeDraggable('.custom-formats', '.ql-custom-btn', 'toolbar_order');
}

// ====== 自适应背景分析 ======
function analyzeImageColor(dataUri, callback) {
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement('canvas');
    const maxDim = 80; // 缩放到 80px 采样
    const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    // 网格分区采样：每个区域取代表色，然后取中位数
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const gridSize = 8;
    const samples = [];

    for (let gy = 0; gy < gridSize; gy++) {
      for (let gx = 0; gx < gridSize; gx++) {
        let sr = 0, sg = 0, sb = 0, sc = 0;
        const x0 = Math.floor(gx * canvas.width / gridSize);
        const y0 = Math.floor(gy * canvas.height / gridSize);
        const x1 = Math.floor((gx + 1) * canvas.width / gridSize);
        const y1 = Math.floor((gy + 1) * canvas.height / gridSize);
        for (let y = y0; y < y1; y += 2) {
          for (let x = x0; x < x1; x += 2) {
            const i = (y * canvas.width + x) * 4;
            sr += data[i]; sg += data[i+1]; sb += data[i+2]; sc++;
          }
        }
        if (sc > 0) samples.push({ r: sr/sc, g: sg/sc, b: sb/sc });
      }
    }

    // 按亮度排序取中位数，避免极值
    samples.sort((a, b) => {
      const la = 0.299*a.r + 0.587*a.g + 0.114*a.b;
      const lb = 0.299*b.r + 0.587*b.g + 0.114*b.b;
      return la - lb;
    });
    const mid = samples[Math.floor(samples.length / 2)];
    const r = Math.round(mid.r), g = Math.round(mid.g), b = Math.round(mid.b);
    // W3C 相对亮度
    const toLinear = (c) => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
    const luminance = 0.2126*toLinear(r) + 0.7152*toLinear(g) + 0.0722*toLinear(b);

    // 合并计算整体亮度偏差：偏暗则用亮底，偏亮则用暗底
    const isDarkBg = luminance < 0.35;
    callback({ r, g, b, luminance, isDarkBg });
  };
  img.onerror = () => callback(null);
  img.src = dataUri;
}

function applyAdaptiveUI(colorInfo) {
  // 极简自适应：只控制工具栏/编辑器透明 + 边框线
  // 侧边栏跟随主题色，背景面板深色底白字，均不受影响
  const body = document.body;
  body.classList.add('adaptive-bg');
  body.style.setProperty('--ad-toolbar-bg', 'transparent');
  body.style.setProperty('--ad-border-strong', 'rgba(128,128,128,0.40)');

  if (colorInfo) {
    window.pywebview.api.settings_set('adaptive_color', JSON.stringify(colorInfo));
  }
}

function clearAdaptiveUI() {
  const body = document.body;
  body.classList.remove('adaptive-bg');
  ['--ad-toolbar-bg','--ad-border-strong'].forEach(k => body.style.removeProperty(k));
  window.pywebview.api.settings_set('adaptive_color', '');
}

// ====== 手帐日历 ======
let calYear, calMonth;

function buildCalendar(year, month) {
  calYear = year; calMonth = month;
  const today = new Date(); today.setHours(0,0,0,0);
  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);
  const daysInMonth = lastDay.getDate();
  // 周一=0, 周日=6
  let startDow = firstDay.getDay(); // 0=日 1=一...
  startDow = startDow === 0 ? 6 : startDow - 1; // 转为周一=0

  $('#cal-month-label').textContent = `${year}年 ${month + 1}月`;
  const grid = $('#cal-grid');
  grid.innerHTML = '';

  // 上月填充
  const prevLast = new Date(year, month, 0).getDate();
  for (let i = startDow - 1; i >= 0; i--) {
    const day = prevLast - i;
    const cell = createCalDay(day, 'other-month');
    grid.appendChild(cell);
  }

  // 本月日期
  // 收集有笔记的日期
  const noteDates = {};
  state.notes.forEach(note => {
    const d = (note.created_at || '').substring(0, 10);
    noteDates[d] = (noteDates[d] || 0) + 1;
  });

  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const cell = createCalDay(d, '', dateStr);
    // 今天
    const cellDate = new Date(year, month, d);
    if (cellDate.getTime() === today.getTime()) {
      cell.classList.add('today');
    }
    // 有笔记
    if (noteDates[dateStr]) {
      cell.classList.add('has-note');
      if (noteDates[dateStr] > 1) {
        const badge = document.createElement('span');
        badge.className = 'cal-day-note-count';
        badge.textContent = noteDates[dateStr];
        cell.appendChild(badge);
      }
    }
    grid.appendChild(cell);
  }

  // 下月填充
  const totalCells = startDow + daysInMonth;
  const remaining = totalCells % 7 === 0 ? 0 : 7 - (totalCells % 7);
  for (let d = 1; d <= remaining; d++) {
    const cell = createCalDay(d, 'other-month');
    grid.appendChild(cell);
  }
}

function createCalDay(dayNum, extraClass, dateStr) {
  const cell = document.createElement('button');
  cell.className = `cal-day${extraClass ? ' ' + extraClass : ''}`;
  cell.textContent = dayNum;
  if (dateStr) {
    cell.addEventListener('click', () => onCalendarDateClick(dateStr));
  }
  return cell;
}

async function onCalendarDateClick(dateStr) {
  closePanel($('#calendar-panel'));
  // 查找当天创建的笔记
  const dayNotes = state.notes.filter(n => (n.created_at||'').startsWith(dateStr));
  if (dayNotes.length > 0) {
    // 跳转到第一篇
    await verifyAndSelectNote(dayNotes[0].id);
  } else {
    // 自动创建笔记（使用默认标题）
    await saveCurrentNote();
    const note = await window.pywebview.api.notes_create();
    if (note) {
      state.notes.unshift(note);
      renderNoteList();
      updateNotebookCount();
      await selectNote(note.id);
      dom.titleInput.focus();
    }
  }
}

$('#btn-calendar').addEventListener('click', () => {
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();
  buildCalendar(calYear, calMonth);
  openPanel($('#calendar-panel'));
});

$('#cal-prev-month').addEventListener('click', () => {
  if (calMonth === 0) { calYear--; calMonth = 11; }
  else calMonth--;
  buildCalendar(calYear, calMonth);
});

$('#cal-next-month').addEventListener('click', () => {
  if (calMonth === 11) { calYear++; calMonth = 0; }
  else calMonth++;
  buildCalendar(calYear, calMonth);
});

$('#cal-today').addEventListener('click', () => {
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();
  buildCalendar(calYear, calMonth);
});

// 装饰分割线 Blot 已在 js/quill/quill-deco.js 中注册

$('#btn-divider').addEventListener('click', () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  buildDividerPanel();
  openPanel($('#divider-panel'));
});

// 分割线blot样式
const dividerBlotStyle = document.createElement('style');
dividerBlotStyle.textContent = `
  .ql-editor .divider-blot { margin:18px 0; user-select:none; cursor:default; }
  .ql-editor .divider-blot.div-bookend { padding:0 8px; }
`;
document.head.appendChild(dividerBlotStyle);

// 贴纸印章 Blot 已在 js/quill/quill-deco.js 中注册

$$('.sticker-cat-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.sticker-cat-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    stickerCat = btn.dataset.stickerCat;
    buildStickerGrid();
  });
});

$('#btn-sticker').addEventListener('click', () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  stickerCat = 'date';
  $$('.sticker-cat-btn').forEach(b => b.classList.toggle('active', b.dataset.stickerCat === 'date'));
  buildStickerGrid();
  openPanel($('#sticker-panel'));
});

// ====== 笔记纸张样式 ======
// paperStyles / paperColors 已定义在 NotepadConfig 中

function buildPaperPanel() {

  const styleGrid = $('#paper-style-grid');
  styleGrid.innerHTML = '';
  const curStyle = state.notes.find(n => n.id === state.activeNoteId)?.paper_style || 'none';
  NotepadConfig.paperStyles.forEach(ps => {
    const btn = document.createElement('button');
    btn.className = 'paper-option' + (ps.id === curStyle ? ' active' : '');
    btn.dataset.style = ps.id;
    if (ps.cls !== 'paper-none') btn.classList.add(ps.cls);
    btn.textContent = ps.name;
    btn.addEventListener('click', () => {
      styleGrid.querySelectorAll('.paper-option').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyPaperStyle(ps.id);
      window.pywebview.api.notes_update(state.activeNoteId, { paper_style: ps.id });
      const note = state.notes.find(n => n.id === state.activeNoteId);
      if (note) note.paper_style = ps.id;
    });
    styleGrid.appendChild(btn);
  });
}

function applyPaperStyle(styleId) {
  const ps = NotepadConfig.paperStyles.find(s => s.id === styleId);
  if (!state.quill) return;
  NotepadConfig.paperStyles.forEach(s => {
    if (s.innerCls) state.quill.root.classList.remove(s.innerCls);
  });
  if (ps && ps.innerCls) state.quill.root.classList.add(ps.innerCls);
}

function loadPaperForNote(note) {
  if (!state.quill || !note) return;
  NotepadConfig.paperStyles.forEach(s => { if (s.innerCls) state.quill.root.classList.remove(s.innerCls); });
  const style = note.paper_style || 'none';
  const ps = NotepadConfig.paperStyles.find(s => s.id === style);
  if (ps && ps.innerCls) state.quill.root.classList.add(ps.innerCls);
}

$('#btn-paper').addEventListener('click', () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  buildPaperPanel();
  openPanel($('#paper-panel'));
});

// ====== 标题风格 ======
// titleStyles / titleStyleNames 已定义在 NotepadConfig 中
let currentTitleStyle = 'none';

function applyTitleStyle(style) {
  currentTitleStyle = style;
  NotepadConfig.titleStyles.forEach(s => dom.titleInput.classList.remove('title-' + s));
  if (style !== 'none') dom.titleInput.classList.add(`title-${style}`);
  $$('.title-style-btn').forEach(b => b.classList.toggle('active', b.dataset.ts === style));
}

// 标题样式按钮事件
$$('.title-style-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    applyTitleStyle(btn.dataset.ts);
  });
});

// ====== 封面系统 ======
// coverColors 已定义在 NotepadConfig 中

function generateNoteCover(note) {
  const type = note.cover_type || 'none';
  const val = note.cover_value || '';
  if (type === 'image' && val) {
    return '<div class="note-cover"><img class="note-cover-img" src="' + escapeHtml(val) + '" onerror="this.style.display=\'none\';this.parentElement.textContent=\'' + escapeHtml((note.title||'笔')[0]) + '\'"></div>';
  }
  if (type === 'gradient' && val) {
    const colors = val.split(',');
    return '<div class="note-cover" style="background:linear-gradient(135deg,' + (colors[0]||'#7D8A6E') + ',' + (colors[1]||'#D69E2E') + ');">' + escapeHtml((note.title||'笔')[0]) + '</div>';
  }
  if (type === 'color' && val) {
    return '<div class="note-cover" style="background:' + escapeHtml(val) + ';">' + escapeHtml((note.title||'笔')[0]) + '</div>';
  }
  const idx = (note.title || '笔').charCodeAt(0) % NotepadConfig.coverColors.length;
  const color = NotepadConfig.coverColors[idx];
  return `<div class="note-cover" style="background:${color};">${escapeHtml((note.title||'笔')[0])}</div>`;
}

// 封面设置面板
function buildCoverPanel() {
  const grid = $('#cover-grid');
  if (!grid) return;
  grid.innerHTML = '';
  const note = state.notes.find(n => n.id === state.activeNoteId);
  const curType = note?.cover_type || 'none';
  const curVal = note?.cover_value || '';

  // 默认自动色选项
  const noneBtn = document.createElement('button');
  noneBtn.className = 'cover-option' + (curType === 'none' ? ' active' : '');
  noneBtn.style.background = 'var(--bg-secondary)'; noneBtn.style.color = 'var(--text-muted)';
  noneBtn.textContent = 'A';
  noneBtn.title = '自动配色';
  noneBtn.addEventListener('click', () => setNoteCover('none', ''));
  grid.appendChild(noneBtn);

  // 纯色选项
  NotepadConfig.coverColors.forEach(c => {
    const btn = document.createElement('button');
    btn.className = 'cover-option' + (curType === 'color' && curVal === c ? ' active' : '');
    btn.style.background = c;
    btn.textContent = 'A';
    btn.addEventListener('click', () => setNoteCover('color', c));
    grid.appendChild(btn);
  });

  // 渐变色
  const gradPairs = [['#7D8A6E','#D69E2E'],['#3182CE','#805AD5'],['#E53E3E','#DD6B20'],['#38A169','#2B6CB0']];
  gradPairs.forEach(g => {
    const btn = document.createElement('button');
    btn.className = 'cover-option' + (curType === 'gradient' && curVal === g.join(',') ? ' active' : '');
    btn.style.background = `linear-gradient(135deg,${g[0]},${g[1]})`;
    btn.textContent = 'A';
    btn.addEventListener('click', () => setNoteCover('gradient', g.join(',')));
    grid.appendChild(btn);
  });
}

async function setNoteCover(type, value) {
  if (!state.activeNoteId) return;
  await window.pywebview.api.notes_update(state.activeNoteId, { cover_type: type, cover_value: value });
  const note = state.notes.find(n => n.id === state.activeNoteId);
  if (note) { note.cover_type = type; note.cover_value = value; }
  renderNoteList();
}

// ====== 笔记本管理 ======
let currentNotebookId = null; // null = 全部笔记

/** 同步更新笔记本计数徽章（轻量，无需 API 调用） */
function updateNotebookCount() {
  const countEl = document.getElementById('notebook-count');
  if (!countEl) return;
  if (currentNotebookId === null) {
    countEl.textContent = '(' + state.notes.length + ')';
  } else {
    const nbNotes = state.notes.filter(function(n) { return n.notebook_id === currentNotebookId; });
    countEl.textContent = '(' + nbNotes.length + ')';
  }
}

async function loadNotebookBar() {
  const notebooks = await window.pywebview.api.notebooks_list();
  const dot = $('#notebook-dot');
  const name = $('#notebook-name');

  // 更新按钮文字和颜色
  if (currentNotebookId === null) {
    dot.style.background = 'var(--accent)';
    name.textContent = '全部笔记';
  } else {
    const nb = notebooks.find(function(n) { return n.id === currentNotebookId; });
    if (nb) {
      dot.style.background = nb.color || '#7D8A6E';
      name.textContent = nb.name;
    } else {
      currentNotebookId = null;
      loadNotebookBar();
      return;
    }
  }

  // 同步更新计数
  updateNotebookCount();

  // 构建下拉列表
  const dropdown = $('#notebook-dropdown');
  dropdown.innerHTML = '';
  notebooks.forEach(nb => {
    const item = document.createElement('div');
    item.className = 'notebook-drop-item' + (nb.id === currentNotebookId ? ' active' : '');
    item.innerHTML = `<span class="notebook-dot" style="background:${nb.color||'#7D8A6E'}"></span>${escapeHtml(nb.name)}<span style="margin-left:auto;font-size:10px;color:var(--text-muted);">${state.notes.filter(n=>n.notebook_id===nb.id).length}篇</span><button class="notebook-delete-btn" title="删除笔记本" style="margin-left:4px;color:var(--text-muted);background:none;border:none;cursor:pointer;font-size:14px;padding:0 4px;">×</button>`;
    // 删除按钮事件（阻止冒泡）
    setTimeout(() => {
      const delBtn = item.querySelector('.notebook-delete-btn');
      if (delBtn) delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`确定删除笔记本「${nb.name}」？其中的笔记将移回未分类。`)) return;
        await window.pywebview.api.notebooks_delete(nb.id);
        currentNotebookId = null;
        await loadAllNotes();
        loadNotebookBar();
      });
    }, 0);
    item.addEventListener('click', async () => {
      currentNotebookId = nb.id;
      dropdown.style.display = 'none';
      await filterByNotebook(nb.id);
    });
    dropdown.appendChild(item);
  });
  // 全部笔记选项
  const allItem = document.createElement('div');
  allItem.className = 'notebook-drop-item' + (currentNotebookId === null ? ' active' : '');
  allItem.innerHTML = `<span class="notebook-dot" style="background:var(--accent)"></span>全部笔记<span style="margin-left:auto;font-size:10px;color:var(--text-muted);">${state.notes.length}篇</span>`;
  allItem.addEventListener('click', async () => {
    currentNotebookId = null;
    dropdown.style.display = 'none';
    await loadAllNotes();
  });
  dropdown.appendChild(allItem);
}

async function filterByNotebook(nbId) {
  state.notes = await window.pywebview.api.notes_list();
  state.notes = state.notes.filter(n => n.notebook_id === nbId);
  renderNoteList();
  loadNotebookBar();
  if (state.notes.length > 0) {
    await verifyAndSelectNote(state.notes[0].id);
  } else {
    hideEditorUI();
  }
}

async function loadAllNotes() {
  state.notes = await window.pywebview.api.notes_list();
  renderNoteList();
  loadNotebookBar();
  if (state.notes.length > 0 && !state.activeNoteId) {
    await verifyAndSelectNote(state.notes[0].id);
  }
}

$('#btn-notebook-select').addEventListener('click', () => {
  const dropdown = $('#notebook-dropdown');
  dropdown.style.display = dropdown.style.display === 'block' ? 'none' : 'block';
  loadNotebookBar();
});

// 点击其他地方关闭下拉
document.addEventListener('click', (e) => {
  if (!e.target.closest('.notebook-bar-wrapper')) {
    $('#notebook-dropdown').style.display = 'none';
  }
});

$('#btn-new-notebook-sidebar').addEventListener('click', async () => {
  const name = prompt('请输入笔记本名称：', '新笔记本');
  if (!name) return;
  await window.pywebview.api.notebooks_create(name);
  loadNotebookBar();
});

// 笔记移动到笔记本（在标签栏加按钮）
$('#btn-move-notebook')?.addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  const notebooks = await window.pywebview.api.notebooks_list();
  const names = notebooks.map(n => n.name + (n.id === currentNotebookId ? ' (当前)' : ''));
  names.unshift('无 (全部笔记)');
  const choice = prompt('移动到笔记本：\n' + names.map((n,i) => `${i}. ${n}`).join('\n') + '\n\n输入序号：', '0');
  if (choice === null) return;
  const idx = parseInt(choice);
  if (isNaN(idx) || idx < 0 || idx > notebooks.length) return;
  const targetId = idx === 0 ? null : notebooks[idx-1].id;
  await window.pywebview.api.notes_update(state.activeNoteId, { notebook_id: targetId || '' });
  loadNotes().then(renderNoteList);
  loadNotebookBar();
});

// ====== 搜索过滤 ======
async function filterNotesBySearch(query) {
  const q = query.trim().toLowerCase();
  if (!q) {
    // 无搜索词：显示全部
    dom.noteList.querySelectorAll('.note-item').forEach(el => el.classList.remove('hidden-by-search'));
    dom.btnSearchClear.style.display = 'none';
    return;
  }
  dom.btnSearchClear.style.display = 'flex';

  // 前端搜索：匹配标题，或加载内容进行匹配
  const items = dom.noteList.querySelectorAll('.note-item');
  for (const item of items) {
    const noteId = item.dataset.noteId;
    const note = state.notes.find(n => n.id === noteId);
    if (!note) continue;

    let matched = note.title.toLowerCase().includes(q);
    // 如果标题没匹配，检查正文（从数据库加载）
    if (!matched && note.content) {
      matched = note.content.toLowerCase().includes(q);
    }
    item.classList.toggle('hidden-by-search', !matched);
  }
}

dom.searchInput.addEventListener('input', () => filterNotesBySearch(dom.searchInput.value));
dom.btnSearchClear.addEventListener('click', () => {
  dom.searchInput.value = '';
  filterNotesBySearch('');
  dom.searchInput.focus();
});

// ====== 启动应用 ======
async function initApp() {
  // 初始化 Quill
  initQuill();
  // 字体/字号下拉同步
  syncFontSizeDisplay();

  // 加载设置
  await loadSettings();

  // 加载笔记列表
  const notes = await loadNotes();

  // 如果有笔记，自动选择第一篇（加密笔记会弹出密码验证）
  if (notes.length > 0) {
    await verifyAndSelectNote(notes[0].id);
  } else {
    // 无笔记状态
    hideEditorUI();
    document.querySelector('.ql-toolbar')?.classList.add('hidden');
  }

  // 加载标签筛选
  loadTagFilter();
  // 加载笔记本栏
  loadNotebookBar();
  // 初始化 Dock 拖拽
  initAllDrag();
  console.log('📒 我的记事本已就绪！');
  console.log(`   - ${notes.length} 篇笔记已加载`);
  console.log(`   - 当前主题：${state.currentTheme}`);
}

// 启动！
initApp().catch(err => {
  console.error('启动失败:', err);
});
