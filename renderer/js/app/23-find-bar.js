// ====== 查找 / 替换（两种编辑器共用同一套 UI）======
// md 笔记：完整能力（高亮全部匹配、上一个/下一个、替换、全部替换）。
// 富文本笔记：**查找 + 跳转**（选中匹配处，Quill 自己会滚过去），不做替换——
// 富文本里"替换"要改 Delta 结构，涉及自定义 blot（贴纸/分割线/表格）的序列化，
// 风险远大于收益（真想批量替换就先转成 Markdown，改完再转回来）。
//
// 实现上不依赖 CodeMirror 的 search 插件：自己按文本下标找匹配，再用 cm.posFromIndex
// 映射成位置即可（少一个 vendor 依赖，且两种编辑器共用同一套匹配/计数逻辑）。

import { $, state, showToast } from './01-core.js';

let _matches = [];          // md: [{from, to, index}]  delta: [{index, length}]
let _cursor = -1;
let _marks = [];            // md 的高亮 mark 句柄
let _query = '';

function cm() {
  const w = document.querySelector('.CodeMirror');
  return (w && w.CodeMirror) || null;
}

function editorText() {
  if (state.noteFormat === 'md') {
    const inst = cm();
    return inst ? inst.getValue() : '';
  }
  return state.quill ? state.quill.getText() : '';
}

function clearMarks() {
  _marks.forEach((m) => { try { m.clear(); } catch (e) { /* 已失效 */ } });
  _marks = [];
}

function caseFold() {
  return !$('#find-case') || !$('#find-case').checked;
}

function collect(query) {
  _matches = [];
  _query = query || '';
  if (!_query) { updateCount(); return; }
  const text = editorText();
  const hay = caseFold() ? text.toLowerCase() : text;
  const needle = caseFold() ? _query.toLowerCase() : _query;
  if (!needle) return;
  let at = hay.indexOf(needle);
  while (at >= 0 && _matches.length < 500) {       // 上限防止超大文档卡死
    _matches.push({ index: at, length: needle.length });
    at = hay.indexOf(needle, at + Math.max(1, needle.length));
  }
  if (state.noteFormat === 'md') {
    const inst = cm();
    clearMarks();
    if (inst) {
      _matches.forEach((m) => {
        const from = inst.posFromIndex(m.index);
        const to = inst.posFromIndex(m.index + m.length);
        m.from = from;
        m.to = to;
        _marks.push(inst.markText(from, to, { className: 'cm-find-hit' }));
      });
    }
  }
  updateCount();
}

function updateCount() {
  const el = $('#find-count');
  if (!el) return;
  el.textContent = _matches.length ? ((_cursor + 1) || 0) + '/' + _matches.length
    : (_query ? '无匹配' : '');
}

function select(i) {
  if (!_matches.length) return;
  _cursor = (i + _matches.length) % _matches.length;
  const m = _matches[_cursor];
  if (state.noteFormat === 'md') {
    const inst = cm();
    if (inst) {
      inst.setSelection(m.from, m.to);
      inst.scrollIntoView({ from: m.from, to: m.to }, 80);
      inst.focus();
    }
  } else if (state.quill) {
    state.quill.setSelection(m.index, m.length);
    try {
      const [line] = state.quill.getLine(m.index);
      const node = line && line.domNode;
      if (node && node.scrollIntoView) node.scrollIntoView({ block: 'center' });
    } catch (e) { /* 忽略 */ }
  }
  updateCount();
}

export function findNext() { select(_cursor + 1); }
export function findPrev() { select(_cursor - 1); }

function replacement() {
  const el = $('#find-replace-input');
  return el ? el.value : '';
}

/** 替换当前匹配（仅 md） */
export function replaceCurrent() {
  if (state.noteFormat !== 'md') {
    showToast('富文本笔记只支持查找；要批量替换请先点格式徽标转成 Markdown', { type: 'warn' });
    return;
  }
  if (_cursor < 0 || !_matches[_cursor]) { findNext(); return; }
  const inst = cm();
  if (!inst) return;
  const m = _matches[_cursor];
  clearMarks();
  inst.replaceRange(replacement(), m.from, m.to);
  collect($('#find-input') ? $('#find-input').value : '');
  findNext();
}

/** 全部替换（仅 md）；从后往前替换，避免前面的改动挪动后面匹配的下标 */
export function replaceAll() {
  if (state.noteFormat !== 'md') {
    showToast('富文本笔记只支持查找；要批量替换请先点格式徽标转成 Markdown', { type: 'warn' });
    return;
  }
  if (!_matches.length) { showToast('没有匹配项', { type: 'info' }); return; }
  const inst = cm();
  if (!inst) return;
  const count = _matches.length;
  clearMarks();
  inst.operation(() => {
    for (let i = _matches.length - 1; i >= 0; i -= 1) {
      const m = _matches[i];
      inst.replaceRange(replacement(), m.from, m.to);
    }
  });
  collect($('#find-input') ? $('#find-input').value : '');
  _cursor = -1;
  updateCount();
  showToast('已替换 ' + count + ' 处', { type: 'success' });
}

export function openFindBar(withReplace) {
  const bar = $('#find-bar');
  if (!bar) return;
  bar.classList.remove('hidden');
  const replaceRow = $('#find-replace-row');
  if (replaceRow) {
    // 富文本笔记藏掉替换行（能力上不支持，摆了也只能点出提示）
    const canReplace = state.noteFormat === 'md';
    replaceRow.classList.toggle('hidden', !canReplace || !withReplace);
  }
  const input = $('#find-input');
  if (input) {
    const sel = state.noteFormat === 'md' && cm() ? cm().getSelection() : '';
    if (sel && sel.length < 80) input.value = sel;
    input.focus();
    input.select();
    collect(input.value);
    if (_matches.length) findNext();
  } else {
    collect('');
  }
}

export function closeFindBar() {
  const bar = $('#find-bar');
  if (bar) bar.classList.add('hidden');
  clearMarks();
  _matches = [];
  _cursor = -1;
  _query = '';
  updateCount();
  if (state.noteFormat === 'md' && cm()) cm().focus();
  else if (state.quill) state.quill.focus();
}

export function initFindBar() {
  const bar = $('#find-bar');
  if (!bar) return;
  $('#find-input')?.addEventListener('input', (e) => { collect(e.target.value); if (_matches.length) findNext(); });
  $('#find-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); e.shiftKey ? findPrev() : findNext(); }
    else if (e.key === 'Escape') { e.preventDefault(); closeFindBar(); }
  });
  $('#find-replace-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeFindBar(); }
  });
  $('#find-case')?.addEventListener('change', () => { collect(_query); if (_matches.length) findNext(); });
  $('#find-prev')?.addEventListener('click', findPrev);
  $('#find-next')?.addEventListener('click', findNext);
  $('#find-replace-one')?.addEventListener('click', replaceCurrent);
  $('#find-replace-all')?.addEventListener('click', replaceAll);
  $('#find-close')?.addEventListener('click', closeFindBar);
}

/** 编辑器内容变化后重算（正文改了，旧的高亮与下标都会失效） */
export function refreshFindIfOpen() {
  const bar = $('#find-bar');
  if (!bar || bar.classList.contains('hidden')) return;
  collect($('#find-input') ? $('#find-input').value : '');
}
