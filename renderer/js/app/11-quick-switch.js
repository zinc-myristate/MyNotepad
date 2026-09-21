// ====== 快速跳转（Ctrl+P）======
// 笔记一多，从列表里翻不如直接敲标题里的几个字。这是最小的"命令面板"：
// 子序列模糊匹配（"m19" 能命中「MATS1192 复习笔记」——按字符顺序匹配，不是拼音），
// 按匹配质量排序，回车打开。
//
// 为什么单独一个模块：它与列表、编辑器、密码流程都只是"读"的关系，
// 放在 05-shell（快捷键）里会让那个文件同时管标签栏和一堆面板逻辑。

import { $, state, showToast } from './01-core.js';
import { verifyAndSelectNote } from './07-formula-security-dnd.js';
import { escapeHtml } from '../shared/utils.js';
import { listCommands, runCommand } from './19-command-panel.js';

const MAX_ITEMS = 30;      // 候选上限：再多也看不过来，还拖慢渲染

let matches = [];          // 笔记候选 [{id, title, score}]
let commands = [];         // 命令候选 [{id, title, hint, cmd}]
let mode = 'notes';        // 'notes' = 跳笔记，'commands' = 执行命令（输入以 > 开头）
let cursor = 0;            // 键盘高亮的行号

/**
 * 子序列模糊匹配打分：query 的字符按顺序出现在 text 中即命中。
 * 连续命中、靠前命中加权；标题越短越优先（同等匹配下短标题更像目标）。
 * 返回 0 表示不匹配。
 */
export function fuzzyScore(text, query) {
  const t = String(text || '').toLowerCase();
  const q = String(query || '').toLowerCase();
  if (!q) return 1;
  let ti = 0, score = 0, streak = 0;
  for (const ch of q) {
    const at = t.indexOf(ch, ti);
    if (at < 0) return 0;
    streak = (at === ti) ? streak + 1 : 0;
    score += 10 + streak * 6 - Math.min(at - ti, 15);
    ti = at + 1;
  }
  return Math.max(1, score - Math.floor(t.length / 3));
}

/** 命中字符包 <mark>：按命中位置切段再逐段转义（不能先转义再找，否则位置会错） */
function highlight(title, query) {
  const q = String(query || '').toLowerCase();
  if (!q) return escapeHtml(title);
  const lower = String(title).toLowerCase();
  let out = '', ti = 0;
  for (const ch of q) {
    const at = lower.indexOf(ch, ti);
    if (at < 0) break;
    out += escapeHtml(title.slice(ti, at));
    out += '<mark>' + escapeHtml(title.slice(at, at + 1)) + '</mark>';
    ti = at + 1;
  }
  return out + escapeHtml(title.slice(ti));
}

function rank(query) {
  const out = [];
  for (const n of state.notes) {
    const title = n.title || '未命名笔记';
    const s = fuzzyScore(title, query);
    if (s > 0) out.push({ id: n.id, title, score: s });
  }
  out.sort((a, b) => b.score - a.score || a.title.length - b.title.length);
  return out.slice(0, MAX_ITEMS);
}

function renderList(query) {
  const list = $('#quick-switch-list');
  if (!list) return;
  if (mode === 'commands') {
    if (!commands.length) {
      list.innerHTML = '<div class="quick-switch-empty">没有匹配的命令</div>';
      return;
    }
    list.innerHTML = commands.map((c, i) => `
      <div class="quick-switch-item${i === cursor ? ' cursor' : ''}" data-cmd="${i}">
        <span class="qs-kind">命令</span>
        <span class="qs-title">${escapeHtml(c.title)}</span>
        <span class="qs-meta">${c.hint || (i === cursor ? 'Enter 执行' : '')}</span>
      </div>`).join('');
  } else if (!matches.length) {
    list.innerHTML = '<div class="quick-switch-empty">没有匹配的笔记（输入 &gt; 可执行命令）</div>';
    return;
  } else {
    list.innerHTML = matches.map((m, i) => `
      <div class="quick-switch-item${i === cursor ? ' cursor' : ''}" data-qs-id="${m.id}">
        <span class="qs-title">${query ? highlight(m.title, query) : escapeHtml(m.title)}</span>
        <span class="qs-meta">${i === cursor ? 'Enter 打开' : ''}</span>
      </div>`).join('');
  }
  const cur = list.querySelector('.quick-switch-item.cursor');
  if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
}

function itemCount() {
  return mode === 'commands' ? commands.length : matches.length;
}

function refresh() {
  const input = $('#quick-switch-input');
  const q = (input ? input.value : '').replace(/^\s+/, '');
  cursor = 0;
  if (q.startsWith('>')) {
    mode = 'commands';
    commands = listCommands(q.slice(1));
    renderList('');
    return;
  }
  mode = 'notes';
  commands = [];
  matches = rank(q.trim());
  renderList(q.trim());
}

export function openQuickSwitch() {
  const panel = $('#quick-switch-panel');
  const input = $('#quick-switch-input');
  if (!panel || !input) return;
  input.value = '';
  input.placeholder = '搜索笔记…（输入 > 执行命令）';
  mode = 'notes';
  commands = [];
  matches = rank('');
  cursor = 0;
  panel.style.display = 'flex';
  renderList('');
  setTimeout(() => input.focus(), 30);
}

export function closeQuickSwitch() {
  const panel = $('#quick-switch-panel');
  if (panel) panel.style.display = 'none';
}

function moveCursor(delta) {
  const n = itemCount();
  if (!n) return;
  cursor = (cursor + delta + n) % n;
  const input = $('#quick-switch-input');
  const q = (input ? input.value : '').replace(/^\s+/, '');
  renderList(mode === 'commands' ? '' : q.trim());
}

async function runSelected() {
  const item = commands[cursor];
  closeQuickSwitch();
  await runCommand(item);
}

async function choose(id) {
  const note = state.notes.find(n => n.id === id);
  closeQuickSwitch();
  if (!note) return;
  try {
    await verifyAndSelectNote(id);       // 加密笔记会走既有的密码验证流程
  } catch (err) {
    showToast('打开失败：' + (err.message || err), { type: 'error' });
  }
}

// ----- 事件绑定 -----
const qsInput = $('#quick-switch-input');
if (qsInput) {
  qsInput.addEventListener('input', refresh);
  qsInput.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveCursor(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveCursor(-1); }
    else if (e.key === 'Enter') {
      e.preventDefault();
      if (mode === 'commands') runSelected();
      else if (matches[cursor]) choose(matches[cursor].id);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeQuickSwitch();
    }
  });
}

const qsList = $('#quick-switch-list');
if (qsList) {
  // 容器级委托：候选行是整体重绘的，逐行绑监听会在每次输入后泄漏
  qsList.addEventListener('click', (e) => {
    const cmdRow = e.target.closest('[data-cmd]');
    if (cmdRow) {
      cursor = Number(cmdRow.dataset.cmd);
      runSelected();
      return;
    }
    const row = e.target.closest('[data-qs-id]');
    if (row) choose(row.dataset.qsId);
  });
  qsList.addEventListener('mousemove', (e) => {
    const row = e.target.closest('[data-qs-id]');
    if (!row) return;
    const idx = matches.findIndex(m => m.id === row.dataset.qsId);
    if (idx >= 0 && idx !== cursor) { cursor = idx; renderList(qsInput ? qsInput.value.trim() : ''); }
  });
}

// Ctrl+P 打开（在任何地方都生效：输入框里也允许——这正是快速跳转的用法）
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && !e.shiftKey && !e.altKey && (e.key === 'p' || e.key === 'P')) {
    e.preventDefault();
    const panel = $('#quick-switch-panel');
    if (panel && panel.style.display === 'flex') closeQuickSwitch();
    else openQuickSwitch();
  }
});
