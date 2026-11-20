// ====== 属性（front-matter）面板（第 9 轮）======
// 设计原则：**正文是唯一真相**。面板不是另一个数据源，它就是 front-matter 的编辑器——
// 编辑面板只重写正文顶部那一段，其余字符逐字节不动；改正文则面板跟着刷新。
// 两套存储（正文一份、DB 一份）才是真正会分叉的东西，这里刻意不给自己留那个坑。
//
// 为什么只对 Markdown 笔记开放：front-matter 是 Markdown 的概念。富文本笔记要属性，
// 标题行的「+ 属性」会问要不要转成 Markdown——转换本来就有备份与内容守恒校验，不必再造一套。
//
// 属性行只在有属性、或者面板开着时出现；入口「+ 属性」在标题行（2026-11-18 界面一轮：
// 以前没有属性时这一行也照样占着，富文本笔记上还多一行"没有属性"的说明）。
//
// 属性在**字数/FTS/摘要**里都不算正文（后端 `_markdown_to_text` 与前端镜像都会先剥 front-matter），
// 它们有自己的查询语法：`prop:键` / `prop:键=值` / `prop:截止>2026-10-01` / `has:prop`。

import { $, $$, state, showToast } from './01-core.js';
import { serializeFrontMatter, splitFrontMatter } from '../shared/utils.js';
import { promptFormatChange } from './15-note-format.js';

const TYPES = [
  ['text', '文本'], ['date', '日期'], ['bool', '复选'], ['list', '列表'],
];

let _draft = null;        // 面板打开时正在编辑的属性（键顺序 = 写回顺序）

function cm() {
  const wrap = document.querySelector('.CodeMirror');
  return (wrap && wrap.CodeMirror) || null;
}

/** 当前笔记的属性（非 Markdown 笔记恒为空） */
export function currentProps() {
  if (state.noteFormat !== 'md') return {};
  const inst = cm();
  if (!inst) return {};
  return splitFrontMatter(inst.getValue()).props;
}

/** 把属性写回正文顶部（整段替换 front-matter，其余内容一个字符都不动） */
function writeProps(props) {
  const inst = cm();
  if (!inst) return;
  const split = splitFrontMatter(inst.getValue());
  const block = serializeFrontMatter(props);
  if (!block && !split.block) return;          // 本来没有、现在也没有 → 不制造一次空改动
  inst.replaceRange(block, inst.posFromIndex(0), inst.posFromIndex(split.block.length));
}

function typeOf(value) {
  if (Array.isArray(value)) return 'list';
  const s = String(value == null ? '' : value).trim();
  if (s === 'true' || s === 'false') return 'bool';
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return 'date';
  return 'text';
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function chipText(value) {
  if (Array.isArray(value)) return value.join('、');
  if (value === true) return '是';
  if (value === false) return '否';
  return String(value == null ? '' : value);
}

function renderChips(props) {
  const box = $('#prop-chips');
  if (!box) return;
  const keys = Object.keys(props || {});
  box.innerHTML = keys.map((k) => '<span class="prop-chip" data-prop="' + escapeHtml(k) + '">'
    + '<b>' + escapeHtml(k) + '</b>' + escapeHtml(chipText(props[k])) + '</span>').join('');
}

function renderRows(props) {
  const box = $('#prop-rows');
  if (!box) return;
  const keys = Object.keys(props || {});
  box.innerHTML = keys.map((k) => {
    const type = typeOf(props[k]);
    const val = props[k];
    let input;
    if (type === 'bool') {
      input = '<input class="prop-val" type="checkbox"' + (val === true ? ' checked' : '') + '>';
    } else if (type === 'date') {
      input = '<input class="prop-val" type="date" value="' + escapeHtml(String(val).trim()) + '">';
    } else if (type === 'list') {
      input = '<input class="prop-val" type="text" placeholder="用逗号分隔" value="'
        + escapeHtml((Array.isArray(val) ? val : [val]).join(', ')) + '">';
    } else {
      input = '<input class="prop-val" type="text" placeholder="值" value="' + escapeHtml(val) + '">';
    }
    const opts = TYPES.map(([v, label]) => '<option value="' + v + '"'
      + (v === type ? ' selected' : '') + '>' + label + '</option>').join('');
    return '<div class="prop-row">'
      + '<input class="prop-key" type="text" placeholder="键" value="' + escapeHtml(k) + '">'
      + '<select class="prop-type">' + opts + '</select>'
      + input
      + '<button class="prop-del" title="删除这一行">×</button>'
      + '</div>';
  }).join('');
}

/** 面板 DOM → 属性对象（没键的行直接忽略：用户可能刚点完"添加一行"） */
function syncFromRows() {
  const props = {};
  $$('#prop-rows .prop-row').forEach((row) => {
    const key = (row.querySelector('.prop-key') || {}).value || '';
    const type = (row.querySelector('.prop-type') || {}).value || 'text';
    const field = row.querySelector('.prop-val');
    const name = key.trim();
    if (!name || !field) return;
    if (type === 'bool') props[name] = !!field.checked;
    else if (type === 'list') {
      props[name] = String(field.value || '').split(/[,，]/).map((s) => s.trim()).filter(Boolean);
    } else props[name] = String(field.value || '').trim();
  });
  _draft = props;
  writeProps(props);
  renderChips(props);
}

/** 面板编辑的防抖落库定时器。`_syncTimerNoteId` 记下它是**为哪篇笔记**排的。
 *
 *  为什么必须带笔记身份、而且要在关面板/切笔记时清掉：`syncFromRows()` 读的是**当前面板
 *  DOM**，然后 `writeProps()` 直接改**当前编辑器**顶部。以前定时器只被"下一次 scheduleSync"
 *  清除，`closePropsEditor()` 与 `refreshPropBar()` 都不清 —— 于是"敲完属性值 350ms 内切笔记"
 *  会让它在编辑器已经换成新笔记之后触发：轻则把新笔记的属性按旧面板内容重写一遍，
 *  重则把上一篇的属性**写进另一篇笔记的 front-matter**。
 */
let _syncTimer = null;
let _syncTimerNoteId = null;

function cancelScheduledSync() {
  if (_syncTimer) { clearTimeout(_syncTimer); _syncTimer = null; }
  _syncTimerNoteId = null;
}

function scheduleSync() {
  cancelScheduledSync();
  const forNote = state.activeNoteId;
  _syncTimerNoteId = forNote;
  _syncTimer = setTimeout(() => {
    _syncTimer = null;
    _syncTimerNoteId = null;
    // 双保险：定时器排好之后笔记被切走/关掉了，这次回写就作废
    if (!forNote || state.activeNoteId !== forNote) return;
    syncFromRows();
  }, 350);
}

function panelHasFocus() {
  const panel = $('#prop-editor');
  return !!(panel && document.activeElement && panel.contains(document.activeElement));
}

/** 标题行的「+ 属性」：Markdown 笔记上开关属性面板；富文本笔记没有属性，点了问要不要转成 Markdown */
function syncPropButton() {
  const btn = $('#btn-prop-edit');
  if (!btn) return;
  const isMd = state.noteFormat === 'md';
  btn.title = isMd ? '编辑属性（Markdown 的 front-matter）' : '富文本笔记没有属性：点这里可以转成 Markdown';
  btn.setAttribute('aria-expanded', String(isMd && !!_draft));
}

/** 刷新属性行（切笔记、正文变化、面板编辑后都走它） */
export function refreshPropBar() {
  const block = $('#prop-block');
  if (!block) return;
  // 笔记换了（或清空了）：上一篇排着的回写必须作废，否则会写到这一篇头上
  if (_syncTimer && _syncTimerNoteId !== state.activeNoteId) cancelScheduledSync();
  if (!state.activeNoteId || state.noteFormat !== 'md') {
    if (_draft) closePropsEditor(false);
    block.classList.add('hidden');
    syncPropButton();
    return;
  }
  syncPropButton();
  if (_draft && panelHasFocus()) { renderChips(_draft); return; }   // 别在用户打字时重建面板
  const props = currentProps();
  if (_draft) { _draft = props; renderRows(props); }
  renderChips(props);
  // 面板开着时这一行一直在：第一次敲下键名时 chip 才出来，这一行要是那时才出现，正在打字的输入框会往下跳
  block.classList.toggle('hidden', !_draft && Object.keys(props).length === 0);
}

/** 属性面板里加一个空行，焦点放进它的键名框 */
function addRow() {
  const box = $('#prop-rows');
  if (!box) return;
  const row = document.createElement('div');
  row.className = 'prop-row';
  row.innerHTML = '<input class="prop-key" type="text" placeholder="键">'
    + '<select class="prop-type">' + TYPES.map(([v, l]) => '<option value="' + v + '">' + l
      + '</option>').join('') + '</select>'
    + '<input class="prop-val" type="text" placeholder="值">'
    + '<button class="prop-del" title="删除这一行">×</button>';
  box.appendChild(row);
  const key = row.querySelector('.prop-key');
  if (key) key.focus();
}

export function openPropsEditor() {
  if (state.noteFormat !== 'md') {
    showToast('富文本笔记没有属性：点标题行的「+ 属性」可以转成 Markdown', { type: 'info' });
    return;
  }
  const panel = $('#prop-editor');
  if (!panel) return;
  _draft = currentProps();
  renderRows(_draft);
  $('#prop-block')?.classList.remove('hidden');
  panel.classList.remove('hidden');
  syncPropButton();
  // 还没有属性：直接给一个空行，打开就能写（没写键名的行不会写进正文）
  if (Object.keys(_draft).length === 0) { addRow(); return; }
  const first = $('#prop-rows .prop-key');
  if (first) first.focus();
}

export function closePropsEditor(refresh = true) {
  const panel = $('#prop-editor');
  if (panel) panel.classList.add('hidden');
  // 关面板前先把排着的回写落掉（用户刚敲的值不能丢），再清定时器
  if (_syncTimer) {
    const stillSameNote = _syncTimerNoteId && _syncTimerNoteId === state.activeNoteId;
    cancelScheduledSync();
    if (stillSameNote) syncFromRows();
  }
  _draft = null;
  if (refresh) refreshPropBar();
  else syncPropButton();
}

export function isPropsEditorOpen() {
  return !!_draft;
}

export function initProperties() {
  $('#btn-prop-edit')?.addEventListener('click', () => {
    if (!state.activeNoteId) return;
    if (state.noteFormat !== 'md') {
      // 富文本：和格式徽标同一个确认框，最前面说明为什么要转（以前这里不问就直接转了）
      promptFormatChange({ lead: '属性（front-matter）是 Markdown 笔记才有的。' });
      return;
    }
    if (isPropsEditorOpen()) closePropsEditor();
    else openPropsEditor();
  });
  $('#btn-prop-add')?.addEventListener('click', addRow);
  $('#btn-prop-done')?.addEventListener('click', () => closePropsEditor());
  const rows = $('#prop-rows');
  if (rows) {
    rows.addEventListener('input', scheduleSync);
    rows.addEventListener('change', scheduleSync);         // 复选/下拉/日期选择器
    rows.addEventListener('click', (ev) => {
      const del = ev.target.closest && ev.target.closest('.prop-del');
      if (!del) return;
      const row = del.closest('.prop-row');
      if (row) row.remove();
      syncFromRows();
    });
  }
  const chips = $('#prop-chips');
  if (chips) {
    chips.addEventListener('click', (ev) => {
      const chip = ev.target.closest && ev.target.closest('[data-prop]');
      if (chip) openPropsEditor();
    });
  }
  refreshPropBar();
}
