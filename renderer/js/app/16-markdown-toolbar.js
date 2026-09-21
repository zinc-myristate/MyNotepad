// ====== Markdown 工具栏（只在 Markdown 笔记下显示）======
// 与 Quill 工具栏同一个网格行，靠 hidden 类二选一：Delta 笔记用 Quill 工具栏，
// Markdown 笔记用这条。按钮只负责把 data-md 映射成动作名，实现全在 17-markdown-actions.js。

import { $, showToast, state } from './01-core.js';
import { applyMarkdownAction } from './17-markdown-actions.js';

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

export function initMarkdownToolbar() {
  const bar = $('#md-toolbar');
  if (!bar) return;
  bar.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-md]');
    if (!btn) return;
    const action = btn.getAttribute('data-md');
    if (action === 'image' || action === 'attachment') {
      insertAsset(action);
      return;
    }
    applyMarkdownAction(action);
  });
}
