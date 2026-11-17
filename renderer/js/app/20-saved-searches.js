// ====== 保存的搜索（侧栏"视图"区）======
// 把一串查询（`tag:数学 todo:open` 之类）存成侧栏里的一个条目，点一下就等于把查询填进
// 搜索框。为什么不叫"智能笔记本"：它**不存储笔记归属**，只是查询的快捷方式——
// 笔记该属于哪个笔记本/标签完全不受影响，删掉视图不会有任何数据变化。

import { $, showConfirmAsync, showInputDialog, showToast, state } from './01-core.js';
import { ICONS } from '../shared/icons.js';

let _items = [];
let _menu = null;      // 打开着的「…」菜单：{ el, anchor }

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
  closeMenu();          // 列表要重建，菜单挂着的那个「…」按钮马上就不在了
  if (!_items.length) {
    wrap.classList.add('hidden');
    box.innerHTML = '';
    return;
  }
  wrap.classList.remove('hidden');
  const scrollLeft = box.scrollLeft;   // 矮窗口下是横向滚动的窄条，重建要留在原位
  // 焦点在视图上时重建会掉到 body，下一次 Tab 还会退回第一个视图
  const focused = document.activeElement;
  const focusedItem = focused?.closest('.ss-item')?.dataset.ss;
  const focusedPart = focused?.classList.contains('ss-more') ? '.ss-more' : '.ss-name';
  box.innerHTML = _items.map((it) => `
    <div class="ss-item${state.searchQuery === it.query ? ' active' : ''}" data-ss="${it.id}"
         title="${esc(it.query)}">
      <span class="ss-name" role="button" tabindex="0">${esc(it.name)}</span>
      <span class="ss-count">${it.count == null ? '' : it.count}</span>
      <button class="ss-more" data-ss-menu="${it.id}" title="重命名 / 删除" aria-haspopup="menu"
              aria-expanded="false">…</button>
    </div>`).join('');
  box.scrollLeft = scrollLeft;
  if (focusedItem) box.querySelector(`[data-ss="${CSS.escape(focusedItem)}"] ${focusedPart}`)?.focus();
}

/** 高亮跟着已生效的搜索走。搜索有防抖和桥接往返，点视图那一刻 state.searchQuery 还是旧值，
 *  所以由搜索落定的地方调用；改写或清空搜索时高亮也靠这里撤掉。 */
export function markActiveView() {
  document.querySelectorAll('#ss-list .ss-item').forEach((row) => {
    const item = _items.find((x) => x.id === row.getAttribute('data-ss'));
    row.classList.toggle('active', item?.query === state.searchQuery);
  });
}

async function apply(item) {
  const input = $('#search-input');
  if (!input) return;
  input.value = item.query;
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

async function renameView(item) {
  const name = await showInputDialog({ title: '重命名视图', defaultValue: item.name, placeholder: '视图名称' });
  if (name == null) return;
  const r = await window.pywebview.api.saved_search_update(item.id, { name: name.trim() });
  if (!r || !r.ok) showToast('重命名失败', { type: 'error' });
  loadSavedSearches();
}

async function deleteView(item) {
  const ok = await showConfirmAsync({
    title: '删除视图', message: '删除「' + item.name + '」？这只删掉这个快捷方式，不影响任何笔记。',
    okText: '删除', danger: true,
  });
  if (!ok) return;
  await window.pywebview.api.saved_search_delete(item.id);
  showToast('已删除视图', { type: 'info' });
  loadSavedSearches();
}

function closeMenu(returnFocus = false) {
  if (!_menu) return;
  const { el, anchor } = _menu;
  _menu = null;
  el.remove();
  anchor.setAttribute('aria-expanded', 'false');
  document.removeEventListener('pointerdown', onPointerOutside, true);
  window.removeEventListener('resize', onViewportChange);
  if (returnFocus && anchor.isConnected) anchor.focus();
}

function onPointerOutside(ev) {
  if (_menu && !_menu.el.contains(ev.target) && !_menu.anchor.contains(ev.target)) closeMenu();
}

function onViewportChange() { closeMenu(); }

/** 视图的「…」菜单：重命名 / 删除。
 *  以前拿确认框当二选一菜单用：确定 = 重命名、取消 = 删除，于是按 Esc、点遮罩走的都是删除那一支。
 *  现在是一个普通的菜单，Esc、Tab、点别处都只是把它收起来。 */
function openMenu(item, anchor) {
  closeMenu();
  const el = document.createElement('div');
  el.className = 'checklist-context-menu ss-menu';
  el.setAttribute('role', 'menu');
  el.setAttribute('aria-label', '视图「' + item.name + '」');
  el.innerHTML = `<button type="button" role="menuitem" data-ss-act="rename">${ICONS.edit} 重命名</button>`
    + `<button type="button" role="menuitem" data-ss-act="delete">${ICONS.trash} 删除</button>`;
  document.body.appendChild(el);
  // 贴着「…」按钮往下开；底下放不下就往上开，左右不出窗口
  const r = anchor.getBoundingClientRect();
  const w = el.offsetWidth;
  const h = el.offsetHeight;
  el.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
  el.style.top = (r.bottom + 4 + h <= innerHeight - 8 ? r.bottom + 4 : Math.max(8, r.top - h - 4)) + 'px';
  _menu = { el, anchor };
  anchor.setAttribute('aria-expanded', 'true');
  el.addEventListener('keydown', (ev) => {
    const items = [...el.querySelectorAll('[data-ss-act]')];
    const i = items.indexOf(document.activeElement);
    if (ev.key === 'Escape' || ev.key === 'Tab') {
      ev.preventDefault();
      ev.stopPropagation();          // Esc 只收菜单，不再往下清多选、退专注模式
      closeMenu(true);
    } else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      items[(i + (ev.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length].focus();
    }
  });
  // 点在菜单的空白处（按钮之间的内边距）不能把焦点弄丢：焦点掉到 body 上，Esc 就收不到了
  el.addEventListener('mousedown', (ev) => { if (!ev.target.closest('button')) ev.preventDefault(); });
  el.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-ss-act]');
    if (!btn) return;
    closeMenu(true);                 // 焦点先回到「…」按钮：对话框关掉后焦点回到这里
    if (btn.dataset.ssAct === 'rename') renameView(item);
    else deleteView(item);
  });
  document.addEventListener('pointerdown', onPointerOutside, true);
  window.addEventListener('resize', onViewportChange);
  el.querySelector('button').focus();
}

/** 把查询里写死的 `键:旧名` 换成新名字。规则与后端 _retarget_saved_searches 一样：
 *  只换整个词、`-` 取反的也换、键不分大小写；新名字里有空白就不换（查询按空白分词，没有引号）。 */
export function retargetQuery(query, keys, from, to) {
  if (!query || !from || !to || from === to || /\s/.test(to)) return query;
  const key = keys.map((k) => [...k].map((c) => `[${c.toLowerCase()}${c.toUpperCase()}]`).join('')).join('|');
  const name = from.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return query.replace(new RegExp(`(?<!\\S)(-?(?:${key}):)${name}(?!\\S)`, 'g'), (_m, head) => head + to);
}

/** 标签、笔记本改名（或标签合并）之后：视图后端已经改好了，这里重新拉一遍；
 *  搜索框里正用着旧名字的话也换掉，结果不会因为改名一下子变成空的。 */
function onSavedSearchesChanged(ev) {
  const d = ev && ev.detail;
  const input = $('#search-input');
  if (d && d.from && input && input.value) {
    const next = retargetQuery(input.value, d.keys || [], d.from, d.to);
    if (next !== input.value) {
      input.value = next;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
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
      if (_menu && _menu.anchor === menuBtn) { closeMenu(true); return; }      // 再点一次就收起
      const item = _items.find((x) => x.id === menuBtn.getAttribute('data-ss-menu'));
      if (item) openMenu(item, menuBtn);
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
    const name = await showInputDialog({ title: '保存为视图', defaultValue: q, placeholder: '视图名称' });
    if (name == null) return;
    const r = await window.pywebview.api.saved_search_create(name, q);
    if (r && r.ok) showToast('已保存视图「' + r.name + '」', { type: 'success' });
    else showToast('保存失败：' + ((r && r.error) || ''), { type: 'error' });
    loadSavedSearches();
  });
  // 命令面板里也能保存视图；标签、笔记本改名后也走这个事件（带着旧名和新名）
  window.addEventListener('saved-searches-changed', onSavedSearchesChanged);
  box.addEventListener('scroll', () => closeMenu());    // 矮窗口下视图是横向滚动的窄条
  loadSavedSearches();
}
