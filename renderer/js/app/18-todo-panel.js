// ====== 跨笔记待办面板 ======
// 数据来自后端的 note_todos（保存时算好），所以打开面板是纯读；勾选才写回原文。
//
// 两条硬约定（后端也有对应实现与测试）：
//   · 勾选按「第 idx 条 + 文本校验」定位 —— 正文被改过就拒绝并让用户刷新，绝不翻错一条；
//   · 勾选**不建历史版本**（否则 50 条历史很快被待办刷满）。
// 加密笔记不参与派生（后端保证），所以这里不会出现密文笔记的待办。

import { $, closePanel, openPanel, showToast, state } from './01-core.js';
import { ICONS } from '../shared/icons.js';
import { loadNotes, selectNote } from './03-notes.js';
import { verifyAndSelectNote } from './07-formula-security-dnd.js';

// 「全部」放第一个且做默认：大多数待办不带日期，默认落在"今天"会看到空面板
const SCOPES = [
  ['open', '全部'], ['today', '今天'], ['overdue', '逾期'], ['week', '本周'],
  ['nodue', '无日期'], ['done', '已完成'],
];

let _scope = 'open';
let _data = { items: [], counts: {} };

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderTabs() {
  const box = $('#todo-tabs');
  if (!box) return;
  box.innerHTML = SCOPES.map(([key, label]) => {
    const n = _data.counts ? (_data.counts[key] || 0) : 0;
    return `<button class="todo-tab${key === _scope ? ' active' : ''}" data-scope="${key}">`
      + `${label}${n ? ' <span class="todo-tab-count">' + n + '</span>' : ''}</button>`;
  }).join('');
}

function renderItems() {
  const box = $('#todo-list');
  if (!box) return;
  if (!_data.items.length) {
    box.innerHTML = '<div class="todo-empty">这个范围里没有待办</div>';
    return;
  }
  box.innerHTML = _data.items.map((it, i) => `
    <div class="todo-item${it.done ? ' done' : ''}" data-i="${i}">
      <button class="todo-check" data-toggle="${i}" title="${it.done ? '取消完成' : '标记完成'}"></button>
      <div class="todo-main">
        <div class="todo-text">${esc(it.text)}</div>
        <div class="todo-meta">
          <span class="todo-note" data-open="${i}" title="打开这篇笔记">${esc(it.note_title || '未命名笔记')}</span>
          ${it.due ? `<span class="todo-due${isOverdue(it.due) ? ' overdue' : ''}">${ICONS.calendar} ${esc(it.due)}</span>` : ''}
        </div>
      </div>
    </div>`).join('');
}

function isOverdue(due) {
  const today = new Date();
  const iso = today.getFullYear() + '-'
    + String(today.getMonth() + 1).padStart(2, '0') + '-'
    + String(today.getDate()).padStart(2, '0');
  return due < iso;
}

export async function refreshTodoPanel(scope) {
  if (scope) _scope = scope;
  try {
    _data = await window.pywebview.api.todos_list(_scope);
  } catch (err) {
    _data = { items: [], counts: {} };
    showToast('读取待办失败：' + (err && err.message ? err.message : err), { type: 'error' });
  }
  renderTabs();
  renderItems();
}

export async function openTodoPanel() {
  await refreshTodoPanel(_scope);
  openPanel($('#todo-panel'));
}

/** 打开笔记；Markdown 笔记尽力滚到那条待办所在行 */
async function jumpToNote(item) {
  closePanel($('#todo-panel'));
  await verifyAndSelectNote(item.note_id);
  if (state.noteFormat !== 'md') {
    showToast('已打开「' + (item.note_title || '') + '」', { type: 'info' });
    return;
  }
  // Markdown：按第 idx 个待办算出源码行号再滚过去（注意：这是"尽力定位"，
  // 正文若被改过会与列表里的 idx 不一致，所以只是滚动，不改内容）
  const cm = (document.querySelector('.CodeMirror') || {}).CodeMirror;
  if (!cm) return;
  const lines = cm.getValue().split('\n');
  const re = /^\s*[-*+]\s+\[([ xX])\]/;
  let seen = -1;
  for (let i = 0; i < lines.length; i += 1) {
    if (!re.test(lines[i])) continue;
    seen += 1;
    if (seen === item.idx) {
      cm.setCursor({ line: i, ch: 0 });
      cm.scrollIntoView({ line: i, ch: 0 }, 120);
      break;
    }
  }
}

async function toggle(item) {
  const r = await window.pywebview.api.todo_toggle(item.note_id, item.idx, item.text);
  if (!r || !r.ok) {
    showToast('操作失败：' + ((r && r.error) || '未知错误'), { type: 'warn' });
    await refreshTodoPanel();
    return;
  }
  await loadNotes();          // 列表摘要/更新时间会变
  await refreshTodoPanel();
}

export function initTodoPanel() {
  const panel = $('#todo-panel');
  if (!panel) return;
  $('#btn-todos')?.addEventListener('click', () => openTodoPanel());
  $('#todo-tabs')?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-scope]');
    if (btn) refreshTodoPanel(btn.getAttribute('data-scope'));
  });
  $('#todo-list')?.addEventListener('click', (ev) => {
    const toggleBtn = ev.target.closest('[data-toggle]');
    if (toggleBtn) {
      toggle(_data.items[Number(toggleBtn.getAttribute('data-toggle'))]);
      return;
    }
    const openBtn = ev.target.closest('[data-open]');
    if (openBtn) jumpToNote(_data.items[Number(openBtn.getAttribute('data-open'))]);
  });
}
