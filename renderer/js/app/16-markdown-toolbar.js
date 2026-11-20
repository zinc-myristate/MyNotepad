// ====== Markdown 工具栏（只在 Markdown 笔记下显示）======
// 与 Quill 工具栏同一个网格行，靠 hidden 类二选一：Delta 笔记用 Quill 工具栏，
// Markdown 笔记用这条。按钮只负责把 data-md 映射成动作名，实现全在 17-markdown-actions.js。
//
// 代码块、表格、分割线、附件收在「插入」菜单里（#md-insert-menu，挂在 <body> 下），
// 工具栏才放得进一行（2026-11-18 界面一轮：原来折成两行，第二行只有「公式」）。

import { $, showToast, state } from './01-core.js';
import { applyMarkdownAction } from './17-markdown-actions.js';
import { bindMenu } from '../shared/menu.js';

async function insertAsset(kind) {
  if (!state.activeNoteId) {
    showToast('请先选择或新建一篇笔记', { type: 'warn' });
    return;
  }
  try {
    const filePath = kind === 'image'
      ? await window.pywebview.api.pick_image()
      : await window.pywebview.api.pick_attachment();
    if (!filePath) return;                     // 用户取消
    const info = await window.pywebview.api.file_copy_to_note(
      filePath, state.activeNoteId, kind === 'image' ? 'image' : 'file');
    if (!info || info.error) {
      showToast('插入失败：' + ((info && info.error) || '未知错误'), { type: 'error' });
      return;
    }
    // 相对路径（attachments/<note_id>/<file>）：换台机器/被 Obsidian 打开都能解析
    const rel = 'attachments/' + state.activeNoteId + '/' + info.filename;
    applyMarkdownAction(kind, { path: rel, name: info.original_name || info.filename });
  } catch (err) {
    showToast('插入失败：' + (err && err.message ? err.message : err), { type: 'error' });
  }
}

function onActionClick(ev) {
  const btn = ev.target.closest('[data-md]');
  if (!btn) return;
  const action = btn.getAttribute('data-md');
  if (action === 'image' || action === 'attachment') {
    insertAsset(action);
    return;
  }
  applyMarkdownAction(action);
}

export function initMarkdownToolbar() {
  const bar = $('#md-toolbar');
  if (!bar) return;
  bar.addEventListener('click', onActionClick);
  const menu = $('#md-insert-menu');
  bindMenu($('#md-insert-btn'), menu, { label: '插入' });
  menu?.addEventListener('click', onActionClick);
}
