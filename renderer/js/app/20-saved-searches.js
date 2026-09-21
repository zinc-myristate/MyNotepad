// ====== 保存的搜索（侧栏"视图"区）======
// 把一串查询（`tag:数学 todo:open` 之类）存成侧栏里的一个条目，点一下就等于把查询填进
// 搜索框。为什么不叫"智能笔记本"：它**不存储笔记归属**，只是查询的快捷方式——
// 笔记该属于哪个笔记本/标签完全不受影响，删掉视图不会有任何数据变化。

import { $, showConfirmAsync, showInputDialog, showToast, state } from './01-core.js';

let _items = [];

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export async function loadSavedSearches() {
  try {
    _items = await window.pywebview.api.saved_searches_list();
  } catch (err) {
    _items = [];
  }
  render();
}

function render() {
  const box = $('#ss-list');
  const wrap = $('#saved-searches');
  if (!box || !wrap) return;
  if (!_items.length) {
    wrap.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  wrap.classList.remove('hidden');
  box.innerHTML = _items.map((it) => `
    <div class="ss-item${state.searchQuery === it.query ? ' active' : ''}" data-ss="${it.id}"
         title="${esc(it.query)}">
      <span class="ss-name">${esc(it.name)}</span>
      <span class="ss-count">${it.count == null ? '' : it.count}</span>
      <button class="ss-more" data-ss-menu="${it.id}" title="重命名 / 删除">…</button>
    </div>`).join('');
}

async function apply(item) {
  const input = $('#search-input');
  if (!input) return;
  input.value = item.query;
  input.dispatchEvent(new Event('input', { bubbles: true }));
  render();
}

async function menu(item) {
  const rename = await showConfirmAsync({
    title: '视图：' + item.name,
    message: '查询：' + item.query + '\n\n要重命名这个视图吗？点「删除」则移除它（不影响任何笔记）。',
    okText: '重命名', cancelText: '删除',
  });
  if (rename) {
    const name = await showInputDialog({ title: '重命名视图', value: item.name, placeholder: '视图名称' });
    if (name == null) return;
    const r = await window.pywebview.api.saved_search_update(item.id, { name: name.trim() });
    if (!r || !r.ok) showToast('重命名失败', { type: 'error' });
  } else {
    const ok = await showConfirmAsync({
      title: '删除视图', message: '删除「' + item.name + '」？这只删掉这个快捷方式。',
      okText: '删除', danger: true,
    });
    if (!ok) return;
    await window.pywebview.api.saved_search_delete(item.id);
    showToast('已删除视图', { type: 'info' });
  }
  loadSavedSearches();
}

export function initSavedSearches() {
  const box = $('#ss-list');
  if (!box) return;
  box.addEventListener('click', (ev) => {
    const menuBtn = ev.target.closest('[data-ss-menu]');
    if (menuBtn) {
      ev.stopPropagation();
      menu(_items.find((x) => x.id === menuBtn.getAttribute('data-ss-menu')));
      return;
    }
    const row = ev.target.closest('[data-ss]');
    if (row) apply(_items.find((x) => x.id === row.getAttribute('data-ss')));
  });
  $('#btn-save-search')?.addEventListener('click', async () => {
    const q = (state.searchQuery || '').trim();
    if (!q) {
      showToast('先在搜索框里输入查询，再保存为视图', { type: 'warn' });
      return;
    }
    const name = await showInputDialog({ title: '保存为视图', value: q, placeholder: '视图名称' });
    if (name == null) return;
    const r = await window.pywebview.api.saved_search_create(name, q);
    if (r && r.ok) showToast('已保存视图「' + r.name + '」', { type: 'success' });
    else showToast('保存失败：' + ((r && r.error) || ''), { type: 'error' });
    loadSavedSearches();
  });
  // 命令面板里也能保存视图
  window.addEventListener('saved-searches-changed', loadSavedSearches);
  loadSavedSearches();
}
