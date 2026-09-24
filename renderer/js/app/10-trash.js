// ====== 回收站面板 ======

// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { $, openPanel, showConfirmAsync, showToast } from './01-core.js';
import { loadNotes } from './03-notes.js';
import { loadNotebookBar } from './09-boot.js';
import { ICONS } from '../shared/icons.js';
import { escapeHtml } from '../shared/utils.js';

$('#btn-trash').addEventListener('click', async () => {
  await loadTrashList();
  openPanel($('#trash-panel'));
});

async function loadTrashList() {
  const container = $('#trash-list-items');
  if (!container) return;
  try {
    const items = await window.pywebview.api.notes_trash_list();
    if (!items || items.length === 0) {
      container.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-muted);">回收站是空的</div>';
      return;
    }
    const svgRestore = ICONS.restore;
    const svgTrash = ICONS.trash;
    container.innerHTML = items.map(t => `
      <div class="reminder-item" data-tid="${t.id}">
        <div class="reminder-item-content" style="flex:1;">${escapeHtml(t.title || '未命名笔记')}</div>
        <div class="reminder-item-meta">
          <span>删除于 ${escapeHtml((t.deleted_at || '').substring(0, 16))}</span>
        </div>
        <div class="reminder-item-actions">
          <button class="reminder-item-btn" title="恢复" data-action="restore" data-tid="${t.id}">${svgRestore}</button>
          <button class="reminder-item-btn danger" title="彻底删除" data-action="purge" data-tid="${t.id}">${svgTrash}</button>
        </div>
      </div>`).join('');

    container.querySelectorAll('.reminder-item-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const tid = btn.dataset.tid;
        const action = btn.dataset.action;
        if (action === 'restore') {
          await window.pywebview.api.notes_restore(tid);
          showToast('已恢复', { type: 'success' });
          await loadTrashList();
          await loadNotes();
          loadNotebookBar();
        } else if (action === 'purge') {
          const title = btn.closest('.reminder-item')?.querySelector('.reminder-item-content')?.textContent || '';
          if (await showConfirmAsync({
            title: '彻底删除',
            message: `确定彻底删除「${title}」？\n\n此操作不可恢复，笔记的附件与图片也将一并删除。`,
            okText: '彻底删除',
            danger: true
          })) {
            await window.pywebview.api.notes_purge(tid);
            showToast('已彻底删除', { type: 'success' });
            await loadTrashList();
          }
        }
      });
    });
  } catch (err) {
    console.error('加载回收站失败:', err);
    container.innerHTML = '<div style="text-align:center;padding:30px;color:#E53E3E;">加载失败</div>';
  }
}

// 清空回收站
$('#btn-trash-purge-all').addEventListener('click', async () => {
  const items = await window.pywebview.api.notes_trash_list();
  if (!items || items.length === 0) return;
  if (await showConfirmAsync({
    title: '清空回收站',
    message: `确定彻底删除回收站中的 ${items.length} 篇笔记？此操作不可恢复。`,
    okText: '清空',
    danger: true
  })) {
    await window.pywebview.api.notes_purge_all();
    showToast('回收站已清空', { type: 'success' });
    await loadTrashList();
    await loadNotes();
    loadNotebookBar();
  }
});
