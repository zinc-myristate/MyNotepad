// ====== 图片文字识别（第 11 轮）======
// Windows 内置 OCR（经 winocr），识别质量不错但**不是 100% 准**（形近字、标点会错几位），
// 所以流程刻意做成"先看一眼"：识别 → 面板里可改可换语言重识别 → 再决定插哪里。
//
// 四个入口共用这一套：
//   1. 截图工具条「识别文字」（裁剪图由 Python 侧交给 window.__ocr.openFromCapture）
//   2. 捕获菜单「识别图片…」（选文件）
//   3. 捕获菜单「识别剪贴板图片」
//   4. 笔记里图片上**右键** →「识别文字」（预览里的图 / 富文本图片 / 图片附件卡片）
//
// 前三个是"图片还没进笔记"（temp 文件），插入时要先复制进附件目录；
// 第四个是"图片已经在笔记里"，插入时只把文字接到那张图下面——两种落点不同，所以有 `source`。

import { $, state, showToast, openPanel, closePanel } from './01-core.js';
import { ICONS } from '../shared/icons.js';
import { loadNotes, selectNote, debouncedSave } from './03-notes.js';
import { insertImageResult } from './02-editor.js';
import { applyMarkdownAction } from './17-markdown-actions.js';

const LANG_KEY = 'ocr_language';

let _state = null;          // 见 openXXX 里的注释
let _langs = [];
let _busy = false;
let _menu = null;

function esc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function api() {
  return window.pywebview.api;
}

// ----- 面板 -----

function setStatus(text, kind) {
  const el = $('#ocr-status');
  if (!el) return;
  el.textContent = text || '';
  el.className = 'ocr-status' + (kind ? ' ' + kind : '');
}

function setBusy(busy) {
  _busy = busy;
  ['ocr-insert', 'ocr-copy', 'ocr-note', 'ocr-retry'].forEach((id) => {
    const el = $('#' + id);
    if (el) el.disabled = busy;
  });
}

function currentLang() {
  const sel = $('#ocr-lang');
  return (sel && sel.value) || 'zh-Hans-CN';
}

async function loadLanguages() {
  const sel = $('#ocr-lang');
  if (!sel) return;
  try {
    _langs = (await api().ocr_languages()) || [];
  } catch (e) {
    _langs = [];
  }
  if (!_langs.length) {
    sel.innerHTML = '<option value="zh-Hans-CN">中文（简体）</option>';
    setStatus('这台机器上没有找到 Windows OCR 语言包', 'warn');
    return;
  }
  let saved = '';
  try {
    saved = (await api().settings_get(LANG_KEY)) || '';
  } catch (e) { /* 读不到就用默认 */ }
  const want = saved && _langs.some((l) => l.tag === saved) ? saved
    : ((_langs.find((l) => l.default) || _langs[0]).tag);
  sel.innerHTML = _langs.map((l) => '<option value="' + esc(l.tag) + '"'
    + (l.tag === want ? ' selected' : '') + '>' + esc(l.label) + '</option>').join('');
}

async function runRecognize() {
  if (_busy || !_state || !_state.path) return;
  const lang = currentLang();
  setBusy(true);
  setStatus('识别中…');
  let res = null;
  try {
    res = await api().ocr_recognize(_state.path, lang);
  } catch (e) {
    res = { ok: false, error: String(e && e.message ? e.message : e) };
  }
  setBusy(false);
  if (!res || !res.ok) {
    setStatus((res && res.error) || '识别失败', 'error');
    if (res && res.languages && res.languages.length) {
      // 语言包不对时把可用的列出来，用户直接在下拉里换
      await loadLanguages();
    }
    return;
  }
  const text = $('#ocr-text');
  if (text) text.value = res.text || '';
  setStatus(res.has_text ? ('识别完成 · ' + res.ms + 'ms') : '没有识别到文字',
    res.has_text ? 'ok' : 'warn');
  try {
    await api().settings_set(LANG_KEY, lang);      // 记住语言选择
  } catch (e) { /* 存不上不影响这次识别 */ }
}

function hintFor(source) {
  if (source === 'attachment') return '文字会插入到这张图片的下面（图保留）';
  return '文字会插在当前笔记的光标处；图片一起存进附件（图在上、文字在下）';
}

async function openPanelFor(opts) {
  _state = Object.assign({ path: '', noteId: null, source: 'file', rel: '', filename: '' }, opts);
  const thumb = $('#ocr-thumb');
  if (thumb) {
    // 缩略图用 data URI：临时文件路径不能直接给 <img>（会去请求渲染器目录）
    thumb.removeAttribute('src');
    if (_state.path) {
      try {
        const uri = await api().read_file_base64(_state.path);
        if (uri) thumb.setAttribute('src', uri);
      } catch (e) { /* 缩略图失败不影响识别 */ }
    }
  }
  const text = $('#ocr-text');
  if (text) text.value = '';
  const hint = $('#ocr-hint');
  if (hint) hint.textContent = hintFor(_state.source);
  await loadLanguages();
  openPanel($('#ocr-panel'));
  await runRecognize();
}

function closeOcr(release) {
  const path = _state && _state.path;
  const temp = _state && _state.source !== 'attachment';
  _state = null;
  closePanel($('#ocr-panel'));
  if (release && temp && path) {
    // 临时裁剪图/剪贴板图用完就删（插入时已经复制进附件目录了，删原件不影响）
    api().ocr_release(path).catch(() => {});
  }
}

// ----- 三个落点 -----

function currentNoteTitle() {
  const note = state.notes.find((n) => n.id === state.activeNoteId);
  return (note && note.title) || '';
}

/** 把文字接到**某张已存在的图片**下面（Markdown 找那一行，富文本找那个 embed） */
function insertAfterExistingImage(text, opts) {
  const filename = opts.filename || '';
  const rel = opts.rel || '';
  if (state.noteFormat === 'md') {
    const cm = document.querySelector('.CodeMirror') && document.querySelector('.CodeMirror').CodeMirror;
    if (!cm) return false;
    const doc = cm.getDoc();
    const needle = filename || rel;
    let hitLine = -1;
    for (let i = 0; i < doc.lineCount(); i += 1) {
      const line = doc.getLine(i) || '';
      if (line.indexOf(needle) >= 0 && line.indexOf('](') >= 0) { hitLine = i; break; }
    }
    if (hitLine < 0) return false;
    const end = { line: hitLine, ch: (doc.getLine(hitLine) || '').length };
    doc.replaceRange('\n\n' + text + '\n', end);
    cm.focus();
    debouncedSave();
    return true;
  }
  // 富文本：在对应的 image embed 之后插入文字
  const quill = state.quill;
  if (!quill) return false;
  const ops = quill.getContents().ops || [];
  let index = 0;
  for (const op of ops) {
    const ins = op.insert;
    if (ins && typeof ins === 'object' && ins.image) {
      const name = ins.image.filename || '';
      if (name && (name === filename || String(ins.image.storedPath || '').indexOf(filename) >= 0)) {
        quill.insertText(index + 1, '\n' + text + '\n', 'user');
        debouncedSave();
        return true;
      }
    }
    index += typeof ins === 'string' ? ins.length : 1;
  }
  return false;
}

/** 把「图片 + 文字」一起插进当前笔记（图片还没进笔记的情况） */
async function insertImageAndText(text, opts) {
  const noteId = state.activeNoteId;
  if (!noteId) { showToast('请先打开一篇笔记', { type: 'warn' }); return false; }
  let saved = null;
  try {
    saved = await api().file_copy_to_note(opts.path, noteId, 'image');
  } catch (e) {
    saved = null;
  }
  if (!saved || !saved.filename) {
    showToast('保存图片失败', { type: 'error' });
    return false;
  }
  const rel = 'attachments/' + noteId + '/' + saved.filename;
  if (state.noteFormat === 'md') {
    applyMarkdownAction('image-text', { path: rel, name: '识别图片', text });
  } else if (state.quill) {
    const range = state.quill.getSelection(true) || { index: state.quill.getLength() };
    insertImageResult(saved);
    state.quill.insertText(range.index + 1, '\n' + text + '\n', 'user');
    state.quill.setSelection(range.index + 1 + text.length + 2);
    debouncedSave();
  } else {
    showToast('编辑器还没就绪', { type: 'warn' });
    return false;
  }
  return true;
}

async function doInsert() {
  if (!_state) return;
  const text = ($('#ocr-text') && $('#ocr-text').value) || '';
  if (!text.trim()) { showToast('识别结果是空的', { type: 'warn' }); return; }
  setBusy(true);
  let ok = false;
  if (_state.source === 'attachment') {
    ok = insertAfterExistingImage(text.trim(), _state);
    if (!ok) showToast('没找到那张图片在正文里的位置', { type: 'warn' });
  } else {
    ok = await insertImageAndText(text.trim(), _state);
  }
  setBusy(false);
  if (ok) {
    showToast('已插入识别出的文字', { type: 'success' });
    closeOcr(true);
  }
}

async function doCopy() {
  const text = ($('#ocr-text') && $('#ocr-text').value) || '';
  if (!text.trim()) { showToast('识别结果是空的', { type: 'warn' }); return; }
  try {
    await navigator.clipboard.writeText(text);
    showToast('已复制识别结果', { type: 'success' });
  } catch (e) {
    showToast('复制失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
}

async function doSaveAsNote() {
  if (!_state) return;
  const text = ($('#ocr-text') && $('#ocr-text').value) || '';
  setBusy(true);
  let note = null;
  try {
    note = await api().capture_image(_state.path, null, text || null);
  } catch (e) {
    note = null;
  }
  setBusy(false);
  if (!note || !note.id) { showToast('存成新笔记失败', { type: 'error' }); return; }
  await loadNotes();
  await selectNote(note.id);
  showToast('已存为「收件箱」里的新笔记', { type: 'success' });
  closeOcr(true);
}

// ----- 图片右键菜单 -----

function hideImageMenu() {
  if (_menu) { _menu.remove(); _menu = null; }
}

/** 从被右键的图片元素上找出「绝对路径 / 附件文件名 / 相对路径」 */
async function imageInfoFrom(el) {
  if (!el) return null;
  // Markdown 预览里的图：原相对路径留在 data-md-src（src 已经被换成 data URI）
  const mdRel = el.getAttribute && el.getAttribute('data-md-src');
  if (mdRel) {
    const m = /^attachments\/([^/]+)\/(.+)$/.exec(mdRel);
    let abs = '';
    if (m) {
      try {
        abs = await api().attachments_get_path(m[1], m[2]);
      } catch (e) { abs = ''; }
    }
    return abs ? { path: abs, rel: mdRel, filename: m ? m[2] : '', source: 'attachment' } : null;
  }
  // 编辑器里的图片 / 附件卡片：Blot 把绝对路径与文件名写在 data 属性上
  const holder = el.closest ? el.closest('[data-stored-path]') : null;
  if (holder) {
    const abs = holder.getAttribute('data-stored-path') || '';
    const filename = holder.getAttribute('data-filename') || '';
    const mime = holder.getAttribute('data-mime-type') || '';
    if (!abs) return null;
    if (mime && mime.indexOf('image/') !== 0 && !/\.(png|jpe?g|gif|bmp|webp)$/i.test(filename)) {
      return null;                       // 附件卡片里的非图片不参与
    }
    return { path: abs, rel: 'attachments/' + state.activeNoteId + '/' + filename,
             filename, source: 'attachment' };
  }
  return null;
}

function showImageMenu(x, y, info) {
  hideImageMenu();
  const menu = document.createElement('div');
  menu.className = 'checklist-context-menu';
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.innerHTML = '<button data-action="ocr">' + ICONS.ocr + ' 识别文字</button>';
  menu.querySelector('[data-action="ocr"]').addEventListener('click', () => {
    hideImageMenu();
    openPanelFor({ path: info.path, filename: info.filename, rel: info.rel,
                   noteId: state.activeNoteId, source: 'attachment' });
  });
  document.body.appendChild(menu);
  _menu = menu;
  setTimeout(() => {
    document.addEventListener('click', function close() {
      hideImageMenu();
      document.removeEventListener('click', close);
    }, { once: true });
  }, 0);
}

// ----- 对外 -----

export function initOcr() {
  $('#ocr-retry')?.addEventListener('click', () => runRecognize());
  $('#ocr-lang')?.addEventListener('change', () => runRecognize());
  $('#ocr-insert')?.addEventListener('click', () => doInsert());
  $('#ocr-copy')?.addEventListener('click', () => doCopy());
  $('#ocr-note')?.addEventListener('click', () => doSaveAsNote());
  $('#ocr-cancel')?.addEventListener('click', () => closeOcr(true));

  // 捕获菜单两项
  $('#cap-ocr-file')?.addEventListener('click', async () => {
    document.dispatchEvent(new CustomEvent('myapp:capture-menu-close'));
    let path = null;
    try {
      path = await api().ocr_pick_image();
    } catch (e) { path = null; }
    if (!path) return;
    openPanelFor({ path, noteId: state.activeNoteId, source: 'file' });
  });
  $('#cap-ocr-clipboard')?.addEventListener('click', async () => {
    document.dispatchEvent(new CustomEvent('myapp:capture-menu-close'));
    let path = null;
    try {
      path = await api().ocr_clipboard_image();
    } catch (e) { path = null; }
    if (!path) { showToast('剪贴板里没有图片', { type: 'warn' }); return; }
    openPanelFor({ path, noteId: state.activeNoteId, source: 'clipboard' });
  });

  // 图片右键 → 识别文字
  document.addEventListener('contextmenu', async (ev) => {
    const el = ev.target && ev.target.closest
      ? ev.target.closest('#md-preview img, .ql-editor img, .attachment-card') : null;
    if (!el) { hideImageMenu(); return; }
    const info = await imageInfoFrom(el);
    if (!info) { hideImageMenu(); return; }
    ev.preventDefault();
    showImageMenu(ev.clientX, ev.clientY, info);
  });

  // 截图工具条选「识别文字」时 Python 会调这里
  window.__ocr = {
    openFromCapture(path, noteId) {
      openPanelFor({ path, noteId, source: 'capture' });
    },
  };
}
