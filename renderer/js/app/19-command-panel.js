// ====== 命令面板（Ctrl+P 面板里的 `>` 模式）======
// 只做"命令注册表 + 过滤 + 执行"三件事；面板与键盘交互仍由 11-quick-switch.js 负责
// （一个面板两种用途：直接输入 = 跳笔记，输入 `>` = 执行命令，像 Obsidian）。
//
// 命令尽量**点既有按钮**而不是重写逻辑：这样新增入口不会带来第二套行为，
// 现有按钮上的守卫（未选笔记、加密未解锁、保存防抖…）全部继续生效。

import { $, showToast, state } from './01-core.js';
import { createNewNote } from './03-notes.js';
import { openTodoPanel } from './18-todo-panel.js';
import { convertActiveNote, syncFormatBadge } from './15-note-format.js';
import { reloadActiveNote } from './03-notes.js';

function click(id) {
  const el = document.getElementById(id);
  if (el) el.click();
  else showToast('找不到入口：' + id, { type: 'warn' });
}

/** 当前笔记是 Markdown 还是富文本（决定"转格式"命令的文案） */
function formatLabel() {
  return state.noteFormat === 'md' ? '转为富文本' : '转为 Markdown';
}

const COMMANDS = [
  { id: 'new-note', title: '新建笔记', hint: 'Ctrl+N', keywords: 'new create',
    run: () => createNewNote() },
  { id: 'new-note-delta', title: '新建富文本笔记', keywords: 'new rich delta 富文本',
    run: async () => {
      await createNewNote();
      if (state.activeNoteId) {
        await convertActiveNote('delta');
        showToast('已新建富文本笔记', { type: 'info' });
      }
    } },
  { id: 'toggle-format', title: '转换正文格式', keywords: 'markdown 富文本 format',
    dynamicTitle: formatLabel,
    run: async () => {
      const to = state.noteFormat === 'md' ? 'delta' : 'md';
      const r = await convertActiveNote(to);
      if (!r || !r.ok) showToast('转换失败：' + ((r && r.error) || '未知错误'), { type: 'error' });
      else showToast(to === 'md' ? '已转为 Markdown' : '已转为富文本', { type: 'success' });
    } },
  { id: 'save-version', title: '手动保存（创建历史版本）', hint: 'Ctrl+S',
    run: () => click('btn-save') },
  { id: 'duplicate', title: '复制当前笔记', keywords: 'duplicate copy',
    run: () => click('btn-duplicate-note') },
  { id: 'export', title: '导出当前笔记', hint: 'Ctrl+E', run: () => click('btn-export') },
  { id: 'todos', title: '待办清单（跨笔记）', keywords: 'todo task 待办',
    run: () => openTodoPanel() },
  { id: 'versions', title: '历史版本', run: () => click('btn-version-history') },
  { id: 'reminders', title: '提醒列表', run: () => click('btn-reminder-list') },
  { id: 'calendar', title: '日历 / 今天', keywords: 'date daily 日记',
    run: () => click('btn-calendar') },
  { id: 'trash', title: '回收站', run: () => click('btn-trash') },
  { id: 'theme', title: '切换主题', keywords: 'theme dark 深色',
    run: () => click('btn-theme') },
  { id: 'tags', title: '管理标签', run: () => click('btn-tag-manager') },
  { id: 'lock', title: '锁定当前笔记', run: () => click('btn-lock') },
  { id: 'focus-search', title: '聚焦搜索框', hint: 'Ctrl+F',
    run: () => { const el = $('#search-input'); if (el) { el.focus(); el.select(); } } },
  { id: 'save-search', title: '把当前搜索保存为视图', keywords: 'saved search view',
    run: async () => {
      const q = (state.searchQuery || '').trim();
      if (!q) { showToast('先在搜索框里输入查询', { type: 'warn' }); return; }
      const r = await window.pywebview.api.saved_search_create('', q);
      if (r && r.ok) {
        showToast('已保存视图「' + r.name + '」', { type: 'success' });
        window.dispatchEvent(new CustomEvent('saved-searches-changed'));
      } else showToast('保存失败：' + ((r && r.error) || '未知错误'), { type: 'error' });
    } },
  { id: 'reload-note', title: '重新载入当前笔记', keywords: 'refresh reload',
    run: () => reloadActiveNote() },
  { id: 'desktop-settings', title: '桌面设置（托盘 / 自启 / 热键）', keywords: 'tray autostart hotkey',
    run: () => click('btn-reminder-list') },
];

/** 过滤命令（空查询返回全部） */
export function listCommands(query) {
  const q = (query || '').trim().toLowerCase();
  const items = COMMANDS.map((c) => ({
    id: c.id,
    title: c.dynamicTitle ? c.dynamicTitle() : c.title,
    hint: c.hint || '',
    cmd: c,
  }));
  if (!q) return items;
  return items.filter((it) => (it.title + ' ' + (it.cmd.keywords || '') + ' ' + it.id)
    .toLowerCase().includes(q));
}

export async function runCommand(item) {
  if (!item || !item.cmd) return;
  try {
    await item.cmd.run();
  } catch (err) {
    showToast('命令执行失败：' + (err && err.message ? err.message : err), { type: 'error' });
  }
}

export { syncFormatBadge };
