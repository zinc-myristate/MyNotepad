// ====== 表格视图（第 9 轮）======
// 「列表看到的 = 表格看到的」：表格是**在列表上换一种看法**，不是第二个入口。
// 所以它显示的就是当前筛选后的那批笔记（把 state.notes 的 id 交给后端一次取全字段），
// 搜索语法（tag: / todo:open / prop:状态=进行中 / 已保存的视图）全部直接复用，零额外概念。
//
// 数据由后端 `notes_table` 一次桥调用返回（不是前端逐篇查）：N 次跨语言往返在大库下会明显卡。
// 加密笔记只显示标题与「已加密」，派生数据列留空——属性同理，后端根本不下发。

import { $, state, showToast } from './01-core.js';
import { loadNotes, selectNote } from './03-notes.js';

const FIXED_COLUMNS = [
  { key: 'title', label: '标题', type: 'text' },
  { key: 'notebook', label: '笔记本', type: 'text' },
  { key: 'tagsText', label: '标签', type: 'text' },
  { key: 'updated_at', label: '更新时间', type: 'text' },
  { key: 'word_count', label: '字数', type: 'num' },
  { key: 'todosText', label: '待办', type: 'num' },
  { key: 'todo_next_due', label: '最近到期', type: 'text' },
];

let _rows = [];
let _propKeys = [];
let _sort = { key: 'updated_at', dir: -1 };
let _visible = false;

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function cellValue(row, col) {
  if (col.key === 'tagsText') return (row.tags || []).join('、');
  if (col.key === 'todosText') return row.todo_total ? (row.todo_open + '/' + row.todo_total) : '';
  if (col.prop) {
    const v = (row.props || {})[col.prop];
    if (Array.isArray(v)) return v.join('、');
    if (v === true) return '是';
    if (v === false) return '否';
    return v == null ? '' : String(v);
  }
  if (col.key === 'title') return row.encrypted ? '🔒 ' + row.title : row.title;
  return row[col.key] == null ? '' : String(row[col.key]);
}

function sortRows(rows) {
  const col = [...FIXED_COLUMNS, ..._propKeys.map((k) => ({ key: 'prop:' + k, prop: k, type: 'text' }))]
    .find((c) => c.key === _sort.key) || FIXED_COLUMNS[0];
  const dir = _sort.dir;
  const val = (row) => {
    if (col.key === 'todosText') return row.todo_open || 0;
    return cellValue(row, col);
  };
  return rows.slice().sort((a, b) => {
    const x = val(a); const y = val(b);
    if (col.type === 'num') return ((Number(x) || 0) - (Number(y) || 0)) * dir;
    const nx = Number(x); const ny = Number(y);
    if (x !== '' && y !== '' && !Number.isNaN(nx) && !Number.isNaN(ny)) return (nx - ny) * dir;
    return String(x).localeCompare(String(y), 'zh') * dir;
  });
}

function render() {
  const head = $('#table-view-head');
  const body = $('#table-view-body');
  const meta = $('#table-view-meta');
  if (!head || !body) return;
  const cols = [...FIXED_COLUMNS, ..._propKeys.map((k) => ({ key: 'prop:' + k, prop: k, label: k, type: 'text' }))];
  head.innerHTML = '<tr>' + cols.map((c) => {
    const mark = _sort.key === c.key ? (_sort.dir > 0 ? ' ▲' : ' ▼') : '';
    return '<th data-sort="' + escapeHtml(c.key) + '"' + (c.prop ? ' class="prop-col"' : '')
      + '>' + escapeHtml(c.label) + mark + '</th>';
  }).join('') + '</tr>';
  const rows = sortRows(_rows);
  body.innerHTML = rows.length ? rows.map((r) => '<tr data-note="' + escapeHtml(r.id) + '"'
    + (r.id === state.activeNoteId ? ' class="active"' : '') + '>'
    + cols.map((c) => '<td' + (c.prop ? ' class="prop-col"' : '') + '>' + escapeHtml(cellValue(r, c))
      + '</td>').join('') + '</tr>').join('')
    : '<tr><td class="table-empty" colspan="' + cols.length
      + '">当前筛选下没有笔记（表格显示的永远是列表里看到的那批）</td></tr>';
  if (meta) {
    meta.textContent = rows.length + ' 篇'
      + (state.searchQuery ? ' · 当前筛选：' + state.searchQuery : '')
      + (_propKeys.length ? ' · 属性列 ' + _propKeys.length + ' 个' : '');
  }
}

/** 列表当前可见的笔记 id。
 *
 *  为什么读 DOM 而不是 state.notes：搜索的筛选**只体现在 DOM 上**——09-boot 是给不匹配的
 *  `.note-item` 加 `hidden-by-search` 类，而不是把笔记从 state.notes 里摘掉。DOM 才等于
 *  "用户眼前那一份"，也天然覆盖了以后新增的任何列表筛选。没有搜索词时直接用 state.notes，
 *  这样"搜不到东西"不会被误当成"没有筛选"而显示全部。
 */
function visibleNoteIds() {
  if (!state.searchQuery) return (state.notes || []).map((n) => n.id);
  return Array.from(document.querySelectorAll('#note-list .note-item:not(.hidden-by-search)'))
    .map((el) => el.dataset.noteId)
    .filter(Boolean);
}

export async function reloadTableView() {
  const ids = visibleNoteIds();
  try {
    _rows = (await window.pywebview.api.notes_table(ids)) || [];
  } catch (e) {
    _rows = [];
    showToast('读取表格数据失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
  const keys = new Set();
  _rows.forEach((r) => Object.keys(r.props || {}).forEach((k) => keys.add(k)));
  _propKeys = Array.from(keys).sort();
  render();
}

export async function openTableView() {
  const panel = $('#table-view');
  if (!panel) return;
  _visible = true;
  panel.classList.remove('hidden');
  await reloadTableView();
  if (!_rows.length) {
    await loadNotes();          // 可能还没加载过笔记列表
    await reloadTableView();
  }
}

export function closeTableView() {
  const panel = $('#table-view');
  if (panel) panel.classList.add('hidden');
  _visible = false;
}

export function isTableViewOpen() {
  return _visible;
}

export async function exportTableCsv() {
  const ids = visibleNoteIds();
  try {
    const res = await window.pywebview.api.export_table_csv(ids);
    if (res === null || res === undefined) return;            // 用户取消
    if (typeof res === 'object' && res.error) throw new Error(res.error);
    showToast('已导出 ' + res + ' 行 CSV', { type: 'success' });
  } catch (e) {
    showToast('导出失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
}

export function initTableView() {
  $('#btn-table-view')?.addEventListener('click', () => openTableView());
  $('#table-view-close')?.addEventListener('click', () => closeTableView());
  $('#btn-table-csv')?.addEventListener('click', () => exportTableCsv());
  const head = $('#table-view-head');
  if (head) {
    head.addEventListener('click', (ev) => {
      const th = ev.target.closest && ev.target.closest('[data-sort]');
      if (!th) return;
      const key = th.getAttribute('data-sort');
      if (_sort.key === key) _sort.dir = -_sort.dir;
      else _sort = { key, dir: key === 'updated_at' ? -1 : 1 };
      render();
    });
  }
  const body = $('#table-view-body');
  if (body) {
    body.addEventListener('click', async (ev) => {
      const tr = ev.target.closest && ev.target.closest('[data-note]');
      if (!tr) return;
      const id = tr.getAttribute('data-note');
      closeTableView();
      await selectNote(id);
    });
  }
}
