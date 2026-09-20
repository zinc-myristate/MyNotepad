// ====== Quill 编辑器初始化 ======
// 字体/字号 Blot 已在 js/quill/quill-blots.js 中注册

// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { $, $$, closePanel, dom, openPanel, showInfoDialog, showToast, state } from './01-core.js';
import { debouncedSave, flushSave, loadNotes, renderNoteList, setSaveDot } from './03-notes.js';
import { getCurrentNotebookId, getCurrentNotebookName } from './09-boot.js';
import { _stickerSyncTimer, getCurrentTagFilter, getCurrentTagName, set_stickerSyncTimer } from './05-shell.js';
import { unlockedNotes } from './07-formula-security-dnd.js';
import { syncStickersToOverlay } from '../quill/quill-deco.js';

export function initQuill() {
  const quill = new Quill('#quill-editor', {
    theme: 'snow',
    placeholder: '开始写点什么…',
    modules: {
      toolbar: '#editor-toolbar'
    }
  });

  // 开启 WebView2 内置拼写检查：Quill 生成的 .ql-editor 默认没有 spellcheck 属性，
  // 英文笔记写错不会标红。中文不受影响（Chromium 对中文不做拼写检查）。
  try {
    quill.root.setAttribute('spellcheck', 'true');
    quill.root.setAttribute('lang', 'zh-CN');
  } catch (e) { /* 属性设置失败不影响编辑功能 */ }

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
      setSaveDot('dirty');
      debouncedSave();
      // 延迟同步贴纸覆盖层（Quill 可能重建了 DOM）
      if (typeof syncStickersToOverlay === 'function') {
        clearTimeout(_stickerSyncTimer);
        set_stickerSyncTimer(setTimeout(() => syncStickersToOverlay(), 150));
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
    showToast('请先选择或新建一篇笔记', { type: 'warn' });
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
    showToast('插入图片失败：' + (err.message || err), { type: 'error' });
  }
}

async function insertImageFromPath(sourcePath) {
  const result = await window.pywebview.api.file_copy_to_note(sourcePath, state.activeNoteId, 'image');
  if (!result) return;

  // 在 Quill 中插入图片：只存引用（id/filename/storedPath），正文不再内嵌 base64
  //（渲染由 NoteImageBlot 异步 read_file_base64 完成）
  const range = state.quill.getSelection(true);
  state.quill.insertEmbed(range.index, 'image', {
    id: result.id,
    filename: result.filename,
    storedPath: result.storedPath
  });
  state.quill.setSelection(range.index + 1);
}

// ====== 附件处理 ======

async function handleAttachmentFile(file) {
  if (!state.activeNoteId) {
    showToast('请先选择或新建一篇笔记', { type: 'warn' });
    return;
  }

  try {
    const tempPath = file.path;
    if (tempPath) {
      await insertAttachmentFromPath(tempPath, file.name);
    }
  } catch (err) {
    console.error('插入附件失败:', err);
    showToast('插入附件失败：' + (err.message || err), { type: 'error' });
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
    showToast('请先选择或新建一篇笔记', { type: 'warn' });
    return;
  }
  const filePath = await window.pywebview.api.pick_image();
  if (filePath) {
    await insertImageFromPath(filePath);
  }
});

$('#btn-insert-attachment').addEventListener('click', async () => {
  if (!state.activeNoteId) {
    showToast('请先选择或新建一篇笔记', { type: 'warn' });
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
export let isVoiceRecording = false;
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
export function clearVoiceTemp() {
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
    showToast('请先选择或新建一篇笔记', { type: 'warn' });
    return;
  }

  // 每次启动前重建识别器，避免复用导致的状态异常
  voiceRecognition = createVoiceRecognition();

  if (!voiceRecognition) {
    showInfoDialog({
      title: '语音识别不可用',
      message: '您的系统不支持语音识别功能。\n\n需要 Windows 10/11 系统，并确保已安装中文语音包。'
    });
    return;
  }

  try {
    voiceRecognition.start();
  } catch (err) {
    console.error('启动语音识别失败:', err);
    voiceRecognition = null;
    resetVoiceUI();
    showToast('启动语音识别失败，请检查麦克风权限', { type: 'error' });
  }
}

export function stopVoiceRecording() {
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

export function syncFontSizeDisplay() {
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
  if (/^(javascript|data|vbscript):/i.test(url)) { showToast('不允许的链接协议', { type: 'warn' }); return; }
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
  if (!state.activeNoteId) { showToast('请先选择一篇笔记', { type: 'warn' }); return; }
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
export async function togglePinNote(noteId) {
  const note = state.notes.find(n => n.id === noteId);
  if (!note) return;
  const newPinned = note.is_pinned ? 0 : 1;
  // 先更新本地，再调 API，避免 UI 响应延迟
  note.is_pinned = Number(newPinned);
  await window.pywebview.api.notes_update(noteId, { is_pinned: Number(newPinned) });
  // loadNotes 刷新全量并重新排序
  await loadNotes();
}

export async function toggleFavoriteNote(noteId) {
  const note = state.notes.find(n => n.id === noteId);
  if (!note) return;
  const newFav = note.is_favorite ? 0 : 1;
  await window.pywebview.api.notes_update(noteId, { is_favorite: newFav });
  note.is_favorite = newFav;
  renderNoteList();
}

/** 同步笔记字段到 state.notes（在 notes_update 后调用，保持面板 UI 一致） */
export function syncNoteFields(noteId, fields) {
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
    showToast('请先选择或新建一篇笔记', { type: 'warn' });
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
    showToast('未找到表格', { type: 'warn' });
  }
});

// ====== 导出笔记 ======

const exportPanel = $('#export-panel');
$('#btn-export').addEventListener('click', () => {
  // 无笔记也允许打开面板（「备份全部数据」zip 导出不依赖当前笔记）
  refreshScopeExportLabels();
  openPanel(exportPanel);
});

/** 按范围导出用的是「当前筛选」（所见即所得）：没在筛选就把按钮置灰并说明原因，
 *  而不是让用户点开一个保存对话框才发现导错了范围。 */
function refreshScopeExportLabels() {
  const nbBtn = $('#export-scope-notebook');
  const tagBtn = $('#export-scope-tag');
  const nbDesc = $('#export-scope-notebook-desc');
  const tagDesc = $('#export-scope-tag-desc');
  const nbId = getCurrentNotebookId();
  const tagId = getCurrentTagFilter();
  const nbName = getCurrentNotebookName();
  const tagName = getCurrentTagName();

  [nbBtn, tagBtn].forEach(b => { if (b) b.classList.remove('is-disabled'); });

  if (nbBtn) {
    const ok = !!nbId;
    nbBtn.classList.toggle('is-disabled', !ok);
    nbBtn.title = ok ? ('导出笔记本「' + (nbName || '当前笔记本') + '」') : '先在侧边栏选择一个笔记本';
    if (nbDesc) {
      nbDesc.textContent = ok
        ? ('当前：' + (nbName || '所选笔记本') + '（含附件）')
        : '先在侧边栏选择一个笔记本';
    }
  }
  if (tagBtn) {
    const ok = !!tagId;
    tagBtn.classList.toggle('is-disabled', !ok);
    tagBtn.title = ok ? ('导出标签「' + (tagName || '当前标签') + '」') : '先按标签筛选';
    if (tagDesc) {
      tagDesc.textContent = ok
        ? ('当前：' + (tagName || '所选标签') + '（含附件）')
        : '先按标签筛选';
    }
  }
}

// 导出前把外置图片引用解析回 data URI（占位图/file:// 直通会导致导出文件破图；html/docx/xlsx 共用）
async function resolveExportImages(htmlContent) {
  const tmp = document.createElement('div');
  tmp.innerHTML = htmlContent;
  for (const img of [...tmp.querySelectorAll('img')]) {
    const path = img.getAttribute('data-stored-path');
    const src = img.getAttribute('src') || '';
    if (path && !src.startsWith('data:image')) {
      const uri = await window.pywebview.api.read_file_base64(path);
      if (uri) img.setAttribute('src', uri);
    }
  }
  return tmp.innerHTML;
}

// 导出选项点击
$$('.export-option').forEach(btn => {
  btn.addEventListener('click', async () => {
    const format = btn.dataset.format;
    closePanel(exportPanel);

    // 全库备份 zip：不依赖当前笔记
    if (format === 'zip') {
      try {
        const p = await window.pywebview.api.export_all();
        if (p) showToast('备份包已导出', { type: 'success' });
      } catch (err) {
        showToast('导出失败：' + (err.message || err), { type: 'error' });
      }
      return;
    }

    // 按当前筛选范围导出（笔记本 / 标签）：没在筛选就提示，不让用户导错范围
    if (format === 'zip-notebook' || format === 'zip-tag') {
      const byTag = format === 'zip-tag';
      const scopeId = byTag ? getCurrentTagFilter() : getCurrentNotebookId();
      const scopeName = byTag ? getCurrentTagName() : getCurrentNotebookName();
      if (!scopeId) {
        showToast(byTag ? '请先按标签筛选' : '请先在侧边栏选择笔记本', { type: 'warn' });
        return;
      }
      try {
        const r = await window.pywebview.api.export_scope(
          byTag ? null : scopeId, byTag ? scopeId : null, scopeName);
        if (r && r.count === 0) showToast('这个范围里还没有笔记', { type: 'warn' });
        else if (r) showToast('已导出 ' + r.count + ' 篇笔记', { type: 'success' });
      } catch (err) {
        showToast('导出失败：' + (err.message || err), { type: 'error' });
      }
      return;
    }

    if (!state.activeNoteId) {
      showToast('请先选择一篇笔记', { type: 'warn' });
      return;
    }
    const note = state.notes.find(n => n.id === state.activeNoteId);
    if (note && note.has_password && !unlockedNotes[state.activeNoteId]) {
      showToast('请先解锁笔记再导出', { type: 'warn' });
      return;
    }

    const title = dom.titleInput.value.trim() || '未命名笔记';
    const htmlContent = state.quill ? await resolveExportImages(state.quill.root.innerHTML) : '';

    if (format === 'pdf') {
      showInfoDialog({
        title: '导出 PDF',
        message: '即将打开系统打印对话框。\n\n请在打印设置中选择"另存为 PDF"作为打印机，然后点击保存即可导出为 PDF 文件。'
      }).then(() => window.print());
      return;
    }

    try {
      await window.pywebview.api.export_note(title, htmlContent, format);
    } catch (err) {
      console.error('导出失败:', err);
      showToast('导出失败：' + (err.message || err), { type: 'error' });
    }
  });
});

