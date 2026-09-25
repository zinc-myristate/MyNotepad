// ====== 快速捕获（第 10 轮）======
// 侧栏底部「捕获」菜单 + 剪贴板文本 / 截图选区 / 今日日记 / 用模板新建 / 迷你记录窗。
//
// 为什么这几件事放同一个菜单：它们共享一个意图——**把外面的东西先收进来，别打断当前思路**。
// 收进来的东西一律落在「收件箱」（日记落在「日记」），之后再整理；捕获动作本身不再追问
// "放哪个笔记本 / 起什么标题"（追问一次，捕获的价值就没了）。
//
// 截图那条路跨了三个进程内实体：主窗（发起）→ Python（藏窗抓屏/裁剪/存附件）→
// 覆盖窗（框选）。覆盖窗的动作由 Python 转回主窗，落到下面 `window.__capture` 上——
// 跨窗口没有别的通信方式，所以这里显式暴露一个专用桥（与 `window.__app` 那个调试桥分开：
// 这个是**生产路径**，e2e 也靠它断言）。

import { $, state, showToast } from './01-core.js';
import { ICONS } from '../shared/icons.js';
import { loadNotes, selectNote } from './03-notes.js';
import { insertImageResult } from './02-editor.js';
import { applyMarkdownAction } from './17-markdown-actions.js';
import { listTemplates, createNoteFromTemplate, openTemplates } from './27-templates.js';

let _open = false;

function esc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

async function renderMenuTemplates() {
  const box = $('#capture-tpl-list');
  if (!box) return;
  const templates = await listTemplates();
  box.innerHTML = templates.length
    ? templates.map((t) => '<button class="capture-tpl-item" data-cap-tpl="' + esc(t.id)
        + '" title="用「' + esc(t.name) + '」新建">' + ICONS.template + esc(t.name) + '</button>').join('')
    : '<div class="capture-tpl-empty">还没有模板，点下面「管理模板」新建一个</div>';
}

async function toggleMenu(force) {
  const menu = $('#capture-menu');
  if (!menu) return;
  const next = force === undefined ? !_open : !!force;
  if (next === _open) return;
  _open = next;
  menu.classList.toggle('hidden', !_open);
  $('#btn-capture')?.classList.toggle('active', _open);
  if (_open) await renderMenuTemplates();
}

/** 捕获成功后的统一收尾：刷新列表 → 选中新笔记（可选）→ 提示 */
async function afterCapture(note, message, select) {
  if (!note || !note.id) return false;
  await loadNotes();
  if (select) await selectNote(note.id);
  showToast(message, { type: 'success' });
  return true;
}

// ----- 菜单各项 -----

async function captureClipboard() {
  await toggleMenu(false);
  let note = null;
  try {
    note = await window.pywebview.api.clipboard_capture();
  } catch (e) {
    showToast('读取剪贴板失败：' + (e && e.message ? e.message : e), { type: 'error' });
    return;
  }
  if (!note) {
    showToast('剪贴板里没有文本', { type: 'warn' });
    return;
  }
  await afterCapture(note, '已收进「收件箱」', true);
}

async function openDailyNote() {
  await toggleMenu(false);
  try {
    const note = await window.pywebview.api.daily_note_open();
    if (!note || !note.id) throw new Error('创建失败');
    await afterCapture(note, '已打开今天的日记', true);
  } catch (e) {
    showToast('打开今日日记失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
}

async function startScreenshot() {
  await toggleMenu(false);
  let ok = false;
  try {
    // 把当前笔记 id 交给 Python：截图完点「插入当前笔记」时要复制进它的附件目录
    ok = await window.pywebview.api.capture_begin(state.activeNoteId || null);
  } catch (e) {
    showToast('截图启动失败：' + (e && e.message ? e.message : e), { type: 'error' });
    return;
  }
  if (!ok) showToast('截图启动失败（可能已经有一次截图在进行）', { type: 'warn' });
}

async function openMiniWindow() {
  await toggleMenu(false);
  try {
    const ok = await window.pywebview.api.capture_mini_open();
    if (!ok) showToast('打开快速记录窗失败', { type: 'error' });
  } catch (e) {
    showToast('打开快速记录窗失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
}

async function newFromTemplate(templateId) {
  await toggleMenu(false);
  // 菜单里点模板名 = 一次点击就建（标题回落成模板名，进去改一下就行）
  await createNoteFromTemplate(templateId, null);
}

// ----- 主窗这一侧的跨窗口桥（覆盖窗的动作由 Python 转过来）-----

function exposeCaptureBridge() {
  window.__capture = {
    /** 覆盖窗选了「插入当前笔记」：把已经存好的附件插进正文 */
    insertImage(rel, saved, noteId) {
      if (!rel) return;
      if (noteId && state.activeNoteId && state.activeNoteId !== noteId) {
        showToast('截图已存进附件，但当前笔记已切换，未插入', { type: 'warn' });
        return;
      }
      if (state.noteFormat === 'md') {
        applyMarkdownAction('image', { path: rel, name: '截图' });
      } else if (state.quill && saved) {
        insertImageResult(saved);
      }
      showToast('截图已插入当前笔记', { type: 'success' });
    },

    /**
     * 别处（迷你窗 / 覆盖窗存成新笔记）刚建了一篇笔记。
     * `select` 决定要不要切过去：截图是在主窗里发起的，切过去能看到结果；
     * 迷你窗是"记一下就走"，切走用户正在看的东西才叫打断。
     */
    async afterExternalCapture(id, select) {
      try {
        await loadNotes();
      } catch (e) { /* 列表刷不动不影响笔记已经落库 */ }
      if (id && select) {
        try { await selectNote(id); } catch (e) { /* 同上 */ }
      }
      showToast(select ? '截图已存为新笔记' : '已收进「收件箱」', { type: 'success' });
    },
  };
}

export function initCapture() {
  exposeCaptureBridge();
  $('#btn-capture')?.addEventListener('click', (ev) => {
    ev.stopPropagation();
    toggleMenu();
  });
  $('#cap-clipboard')?.addEventListener('click', () => captureClipboard());
  $('#cap-screenshot')?.addEventListener('click', () => startScreenshot());
  $('#cap-daily')?.addEventListener('click', () => openDailyNote());
  $('#cap-mini')?.addEventListener('click', () => openMiniWindow());
  $('#cap-tpl-manage')?.addEventListener('click', async () => {
    await toggleMenu(false);
    openTemplates();
  });
  $('#capture-tpl-list')?.addEventListener('click', (ev) => {
    const item = ev.target.closest && ev.target.closest('[data-cap-tpl]');
    if (item) newFromTemplate(item.getAttribute('data-cap-tpl'));
  });
  // 点别处 / Esc 关菜单（菜单是浮层，不关就会一直压着笔记列表）
  document.addEventListener('click', (ev) => {
    if (!_open) return;
    if (ev.target.closest && ev.target.closest('#capture-menu')) return;
    toggleMenu(false);
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && _open) toggleMenu(false);
  });
}
