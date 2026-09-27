// ====== 多选批量操作 ======
// Ctrl/Shift 点列表项进入多选，顶部出现操作条：全选 / 移动到笔记本 / 打标签 / 删除。
//
// 为什么单开一个模块：选中态是"跨列表项与操作条"的横切状态，塞进 03-notes（列表渲染与
// 保存链路）会让那个文件同时管两件事；这里只依赖 state 与后端批量接口。
//
// 关键取舍：批量动作都走**一个**桥调用（notes_delete_many / notes_move_many /
// notes_add_tag_many），而不是前端 for 循环逐个调——N 次跨语言往返在大批量下明显卡顿，
// 且失败一半时很难给出准确反馈。

import { $, $$, dom, hideEditorUI, showConfirmAsync, showToast, state } from './01-core.js';
import { loadNotes, renderNoteList } from './03-notes.js';
import { loadTagFilter } from './05-shell.js';
import { verifyAndSelectNote } from './07-formula-security-dnd.js';
import { loadNotebookBar } from './09-boot.js';

let _anchor = null;      // Shift 连选的起点
let _notebooks = [];
let _tags = [];

export function isMultiSelecting() { return state.selectedIds.size > 0; }

/** 就地刷新选中样式：不整表重渲染，避免每次 Ctrl+点击都重置滚动位置 */
function applySelectionClass() {
  dom.noteList.querySelectorAll('.note-item').forEach(el => {
    el.classList.toggle('selected', state.selectedIds.has(el.dataset.noteId));
  });
  refreshBulkBar();
}

export function toggleSelection(id) {
  if (state.selectedIds.has(id)) state.selectedIds.delete(id);
  else state.selectedIds.add(id);
  _anchor = id;
  applySelectionClass();
}

/** Shift 连选：按当前列表顺序从锚点到目标整段选中 */
export function extendSelectionTo(id) {
  const ids = state.notes.map(n => n.id);
  const to = ids.indexOf(id);
  if (to < 0) return;
  const from = _anchor ? ids.indexOf(_anchor) : to;
  if (from < 0) { toggleSelection(id); return; }
  const [a, b] = from <= to ? [from, to] : [to, from];
  for (let i = a; i <= b; i++) state.selectedIds.add(ids[i]);
  applySelectionClass();
}

export function clearSelection() {
  if (!state.selectedIds.size) return;
  state.selectedIds.clear();
  _anchor = null;
  applySelectionClass();
}

export function selectAllVisible() {
  // 只选"当前列表里看得见的"（搜索过滤后隐藏的不选），符合直觉
  dom.noteList.querySelectorAll('.note-item').forEach(el => {
    if (!el.classList.contains('hidden-by-search')) state.selectedIds.add(el.dataset.noteId);
  });
  applySelectionClass();
}

async function refreshBulkBar() {
  const bar = $('#bulk-bar');
  if (!bar) return;
  const n = state.selectedIds.size;
  bar.classList.toggle('hidden', n === 0);
  const count = $('#bulk-count');
  if (count) count.textContent = '已选 ' + n + ' 项';
  if (n === 0) return;
  // 两个下拉的内容随数据变化，展开时才拉一次，避免每次点击都请求
  try {
    _notebooks = await window.pywebview.api.notebooks_list();
    _tags = await window.pywebview.api.tags_list();
  } catch (e) {
    return;
  }
  const nbSel = $('#bulk-notebook');
  if (nbSel && nbSel.options.length <= 1) {
    _notebooks.forEach(nb => {
      const o = document.createElement('option');
      o.value = nb.id;
      o.textContent = nb.name;
      nbSel.appendChild(o);
    });
  }
  const tagSel = $('#bulk-tag');
  if (tagSel && tagSel.options.length <= 1) {
    _tags.forEach(t => {
      const o = document.createElement('option');
      o.value = t.id;
      o.textContent = t.name;
      tagSel.appendChild(o);
    });
  }
}

function selectedArray() { return [...state.selectedIds]; }

async function afterBulk(message) {
  clearSelection();
  await loadNotes();
  await loadNotebookBar();
  loadTagFilter();
  renderNoteList();
  // 批量操作可能把**当前打开的那篇**移出当前笔记本（或删除）：编辑区不能还停在它上面，
  // 否则又成了"列表里看不见、编辑区却在编辑它"。按切范围的规矩收尾：跳第一篇，没有就清空。
  if (state.activeNoteId && !state.notes.some(n => n.id === state.activeNoteId)) {
    if (state.notes.length > 0) {
      await verifyAndSelectNote(state.notes[0].id);
    } else {
      state.activeNoteId = null;
      hideEditorUI();
    }
  }
  if (message) showToast(message, { type: 'success' });
}

async function bulkDelete() {
  const ids = selectedArray();
  if (!ids.length) return;
  const ok = await showConfirmAsync({
    title: '批量删除',
    message: '确定把选中的 ' + ids.length + ' 篇笔记移入回收站吗？\n\n可在回收站中恢复。',
    okText: '删除',
    danger: true,
  });
  if (!ok) return;
  try {
    const n = await window.pywebview.api.notes_delete_many(ids);
    await afterBulk('已移入回收站：' + n + ' 篇');
  } catch (err) {
    showToast('批量删除失败：' + (err.message || err), { type: 'error' });
  }
}

async function bulkMove(notebookId) {
  const ids = selectedArray();
  if (!ids.length) return;
  try {
    const n = await window.pywebview.api.notes_move_many(ids, notebookId || null);
    const nb = _notebooks.find(x => x.id === notebookId);
    await afterBulk('已移动 ' + n + ' 篇到「' + (nb ? nb.name : '未分类') + '」');
  } catch (err) {
    showToast('批量移动失败：' + (err.message || err), { type: 'error' });
  }
}

async function bulkAddTag(tagId) {
  const ids = selectedArray();
  if (!ids.length) return;
  try {
    const n = await window.__addTagMany(ids, tagId);
    await afterBulk('已为 ' + n + ' 篇添加标签');
  } catch (err) {
    showToast('批量打标签失败：' + (err.message || err), { type: 'error' });
  }
}

// ----- 事件绑定 -----
// 打标签走一层薄包装，便于 e2e 探针直接注入（也避免在测试里依赖 select 的交互）
window.__addTagMany = (ids, tagId) => window.pywebview.api.notes_add_tag_many(ids, tagId);

const bar = $('#bulk-bar');
if (bar) {
  $('#bulk-all')?.addEventListener('click', selectAllVisible);
  $('#bulk-cancel')?.addEventListener('click', clearSelection);
  $('#bulk-delete')?.addEventListener('click', bulkDelete);
  $('#bulk-notebook')?.addEventListener('change', async (e) => {
    const v = e.target.value;
    e.target.value = '__none__';            // 复位，方便对同一目标再操作一次
    if (v && v !== '__none__') await bulkMove(v);
  });
  $('#bulk-tag')?.addEventListener('change', async (e) => {
    const v = e.target.value;
    e.target.value = '';
    if (v) await bulkAddTag(v);
  });
}

// Esc 退出多选（输入框里不接管，避免影响正常输入）
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const el = document.activeElement;
  if (el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return;
  clearSelection();
});
