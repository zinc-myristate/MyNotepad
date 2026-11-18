// ====== 表格视图（第 9 轮）======
// 「列表看到的 = 表格看到的」：表格是**在列表上换一种看法**，不是第二个入口。
// 所以它显示的就是当前筛选后的那批笔记（把 state.notes 的 id 交给后端一次取全字段），
// 搜索语法（tag: / todo:open / prop:状态=进行中 / 已保存的视图）全部直接复用，零额外概念。
//
// 数据由后端 `notes_table` 一次桥调用返回（不是前端逐篇查）：N 次跨语言往返在大库下会明显卡。
// 加密笔记只显示标题与「已加密」，派生数据列留空——属性同理，后端根本不下发。

import { $, state, showResultToast, showToast } from './01-core.js';
import { ICONS } from '../shared/icons.js';
import { loadNotes, selectNote } from './03-notes.js';
import { listItems } from './30-virtual-list.js';

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
    const mark = _sort.key === c.key
      ? ' ' + (_sort.dir > 0 ? ICONS['chevron-up'] : ICONS['chevron-down']) : '';
    return '<th data-sort="' + escapeHtml(c.key) + '"' + (c.prop ? ' class="prop-col"' : '')
      + '>' + escapeHtml(c.label) + mark + '</th>';
  }).join('') + '</tr>';
  const rows = sortRows(_rows);
  body.innerHTML = rows.length ? rows.map((r) => '<tr data-note="' + escapeHtml(r.id) + '"'
    + (r.id === state.activeNoteId ? ' class="active"' : '') + '>'
    + cols.map((c) => {
      // 加密标记用图标（列里要塞 HTML，所以不能放在 cellValue 里）
      const icon = (c.key === 'title' && r.encrypted) ? ICONS.lock + ' ' : '';
      return '<td' + (c.prop ? ' class="prop-col"' : '') + '>' + icon
        + escapeHtml(cellValue(r, c)) + '</td>';
    }).join('') + '</tr>').join('')
    : '<tr><td class="table-empty" colspan="' + cols.length
      + '">当前筛选下没有笔记（表格显示的永远是列表里看到的那批）</td></tr>';
  if (meta) {
    meta.textContent = rows.length + ' 篇'
      + (state.searchQuery ? ' · 当前筛选：' + state.searchQuery : '')
      + (_propKeys.length ? ' · 属性列 ' + _propKeys.length + ' 个' : '');
  }
}

/** 列表里的那些笔记（当前范围 + 搜索筛选之后），表格和导出的 CSV 都按它来。
 *
 *  注意不能读 DOM：列表第 13 轮改成只画看得见的十几行（30-virtual-list.js），以前这里数的是
 *  `#note-list .note-item`，搜索命中 60 篇时表格和 CSV 里只有画出来的 18 行（2026-11-18 实测）。
 *  `listItems()` 就是列表自己画行用的那一份：没有搜索词时是 state.notes，有搜索词时只留命中的，
 *  一篇都没命中时是空的（不会被误当成"没有筛选"而显示全部）。
 */
function visibleNoteIds() {
  return listItems().map((n) => n.id);
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
  // 表格是空的就别让人先挑一个保存位置，再告诉他什么都没写
  if (!ids.length) { showToast('表格里没有笔记', { type: 'warn' }); return; }
  try {
    // 以前写不进去时后端返回 null，这里当成「取消」一声不吭（CSV 正在 Excel 里开着就是这样）
    const res = await window.pywebview.api.export_table_csv(ids);
    if (res && res.ok && res.count === 0) showToast('表格里没有笔记', { type: 'warn' });
    else showResultToast(res, '已导出 ' + (res && res.count) + ' 行 CSV');
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
