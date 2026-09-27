// ====== 双链导航（第 9 轮）======
// `[[标题]]` / `[[标题#小节]]` / `[[标题|别名]]` / `[[#本笔记的小节]]`。
//
// 后端 `note_links` 只存"标题归一"，**解析放在查询时**做——所以改标题之后所有反向链接
// 自动跟着修正，永远不会有过期索引。这里只负责把它变成可点的界面：
// 预览里点链接、抽屉里的三组列表（指向 / 反向链接 / 尚未创建）。
//
// 点一个"尚不存在"的链接 = 直接建同名笔记并跳过去（用户选定的行为）：双链最有用的时候
// 就是先把链接写下来、回头再补内容。

import { $, state, showToast } from './01-core.js';
import { slugify } from '../shared/utils.js';
import { loadNotes, selectNote } from './03-notes.js';
import { getCurrentNotebookId, revealAndSelectNote } from './09-boot.js';
import { jumpToHeadingByName } from './22-outline.js';

let _visible = false;
let _data = null;

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function linkLabel(item) {
  const title = item.title || ('#' + (item.heading || ''));
  return item.alias || title;
}

function itemHtml(item, opts = {}) {
  const heading = item.heading ? ' <span class="link-heading">#' + escapeHtml(item.heading) + '</span>' : '';
  // opts.label：反向链接用**源笔记标题**（见调用处说明）；aliasNote 才是"链接里怎么写"的注解
  const label = opts.label || linkLabel(item);
  const alias = opts.aliasNote && item.alias
    ? '<span class="link-alias">（写作「' + escapeHtml(item.alias) + '」）</span>' : '';
  const attrs = ' data-link-title="' + escapeHtml(item.title || '') + '"'
    + ' data-link-heading="' + escapeHtml(item.heading || '') + '"'
    + (opts.missing ? ' data-link-missing="1"' : '');
  const context = opts.context
    ? '<div class="link-context">' + escapeHtml(opts.context) + '</div>' : '';
  const meta = opts.meta ? '<span class="link-meta">' + escapeHtml(opts.meta) + '</span>' : '';
  return '<div class="link-item"' + attrs + '>'
    + '<span class="link-title">' + escapeHtml(label) + '</span>'
    + heading + alias + meta + context
    + '</div>';
}

function section(title, count, body, hint) {
  const inner = count ? body : '<div class="link-empty">' + escapeHtml(hint) + '</div>';
  return '<div class="link-section">'
    + '<div class="link-section-head">' + escapeHtml(title)
    + (count ? '<span class="link-count">' + count + '</span>' : '') + '</div>'
    + inner + '</div>';
}

function render() {
  const list = $('#links-list');
  if (!list) return;
  if (!state.activeNoteId || !_data) {
    list.innerHTML = '<div class="link-empty">打开一篇笔记后这里会显示它的链接关系</div>';
    return;
  }
  const out = _data.outgoing || [];
  const back = _data.backlinks || [];
  const missing = _data.missing || [];
  const outHtml = out.map((i) => itemHtml(i, {
    missing: !i.target && !i.same_note,
    meta: i.same_note ? '本笔记内' : (i.target ? '' : '尚未创建'),
  })).join('');
  list.innerHTML = section('指向（本文提到的）', out.length, outHtml, '这篇笔记还没有 [[双链]]')
    + section('反向链接（谁提到了这篇）', back.length,
      // 这一侧的问题是"哪篇笔记提到了我"，所以标题必须是源笔记的标题；
      // 链接处写的别名只作注解（同一个源笔记可能用不同别名指过来）
      back.map((i) => itemHtml(i, { label: i.title, aliasNote: true,
        meta: i.updated_at || '', context: i.context })).join(''),
      '还没有别的笔记链接到这里')
    + section('尚未创建', missing.length,
      missing.map((i) => itemHtml(i, { missing: true })).join(''),
      '所有 [[双链]] 都指向已存在的笔记');
}

export async function refreshLinks() {
  if (!state.activeNoteId) {
    _data = null;
  } else {
    try {
      _data = await window.pywebview.api.note_links(state.activeNoteId);
    } catch (e) {
      _data = null;
    }
  }
  if (_visible) render();
}

/** 抽屉开着才重算（正文每敲一下都查一次库没有意义） */
export async function refreshLinksIfOpen() {
  if (_visible) await refreshLinks();
}

export function toggleLinks(force) {
  const drawer = $('#links-drawer');
  if (!drawer) return;
  _visible = force === undefined ? !_visible : !!force;
  if (_visible) document.dispatchEvent(new CustomEvent('myapp:links-opened'));
  drawer.classList.toggle('hidden', !_visible);
  const btn = $('#btn-links');
  if (btn) btn.classList.toggle('active', _visible);
  if (_visible) refreshLinks().catch(() => { /* 查不到就不渲染，界面照常 */ });
}

/** 跳转到一个 [[双链]] 目标：不存在就直接建一篇同名笔记 */
export async function openWikilink(title, heading) {
  const name = (title || '').trim();
  const head = (heading || '').trim();
  if (!name) {                       // `[[#小节]]`：本笔记内跳转
    if (head) jumpToHeading(head);
    return;
  }
  let hit = null;
  try {
    hit = await window.pywebview.api.notes_resolve_link(name);
  } catch (e) {
    showToast('链接解析失败：' + (e && e.message ? e.message : e), { type: 'error' });
    return;
  }
  if (!hit) {
    try {
      // 双链是从"这一本里的某篇"长出来的：新笔记跟着当前笔记本走
      const note = await window.pywebview.api.notes_create_from_link(name, getCurrentNotebookId());
      if (!note || !note.id) throw new Error('创建失败');
      await loadNotes();
      await selectNote(note.id);
      showToast('已新建「' + name + '」', { type: 'success' });
    } catch (e) {
      showToast('新建笔记失败：' + (e && e.message ? e.message : e), { type: 'error' });
      return;
    }
  } else {
    if (hit.matches > 1) {
      showToast('有 ' + hit.matches + ' 篇同名笔记，已打开最近更新的那篇', { type: 'info' });
    }
    // 目标可能在别的笔记本里：视角跟着切过去，保证"列表里看得见正在编辑的这篇"
    if (hit.id !== state.activeNoteId) await revealAndSelectNote(hit.id);
  }
  if (head) jumpToHeading(head);
}

/** 跳到某个小节：编辑器里的定位交给大纲模块（它已经会算 md 行号与 delta 偏移） */
function jumpToHeading(heading) {
  if (!jumpToHeadingByName(heading)) {
    showToast('没找到小节「' + heading + '」', { type: 'warn' });
    return;
  }
  // 预览栏也要滚到那一节：标题 id 是渲染时按同一个 slugify 生成的
  const scroll = () => {
    const el = document.getElementById('md-h-' + slugify(heading));
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'start' });
  };
  setTimeout(scroll, 320);       // 切笔记时预览有 180ms 节流，等它渲完
  setTimeout(scroll, 700);
}

export function initLinks() {
  $('#btn-links')?.addEventListener('click', () => toggleLinks());
  // 与大纲抽屉互斥（见 22-outline.js 的说明）
  document.addEventListener('myapp:outline-opened', () => toggleLinks(false));
  document.addEventListener('myapp:templates-opened', () => toggleLinks(false));
  $('#links-close')?.addEventListener('click', () => toggleLinks(false));
  const list = $('#links-list');
  if (list) {
    list.addEventListener('click', (ev) => {
      const item = ev.target.closest && ev.target.closest('[data-link-title]');
      if (!item) return;
      openWikilink(item.getAttribute('data-link-title') || '',
        item.getAttribute('data-link-heading') || '');
    });
  }
  // 预览里的 [[双链]]：自己接管（普通链接仍由 14 的 bindPreviewLinks 交给系统打开）
  const preview = $('#md-preview');
  if (preview) {
    preview.addEventListener('click', (ev) => {
      const a = ev.target.closest && ev.target.closest('a[data-wikilink]');
      if (!a) return;
      ev.preventDefault();
      openWikilink(a.getAttribute('data-wikilink') || '',
        a.getAttribute('data-heading') || '');
    });
  }
}
