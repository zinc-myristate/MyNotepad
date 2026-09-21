// ====== 大纲抽屉（H1–H6 导航）======
// 只做"跳转 + 当前标题高亮"，**不做折叠正文**：折叠可编辑内容会和光标位置、保存链路打架
// （Obsidian 能做是因为它编辑的就是 Markdown 源码）。真想要清爽可以等"专注模式"。

import { $, state } from './01-core.js';
import { slugify } from '../shared/utils.js';

let _headings = [];
let _visible = false;

/** 从 Markdown 源码抽取标题（跳过代码围栏内的 #） */
function headingsFromMarkdown(text) {
  const out = [];
  let inFence = false;
  String(text || '').split('\n').forEach((line, i) => {
    if (/^\s*```/.test(line)) { inFence = !inFence; return; }
    if (inFence) return;
    const m = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (m) out.push({ level: m[1].length, text: m[2], line: i });
  });
  return out;
}

/** 从 Quill Delta 抽取标题（行属性挂在含换行的 op 上），并记录纯文本偏移 */
function headingsFromDelta(content) {
  const out = [];
  let offset = 0;
  const ops = (content && content.ops) || [];
  for (const op of ops) {
    const ins = op.insert;
    if (typeof ins !== 'string') continue;
    const attrs = op.attributes || {};
    const parts = ins.split('\n');
    for (let i = 0; i < parts.length; i += 1) {
      if (i < parts.length - 1) {
        if (attrs.header) out.push({ level: attrs.header, text: parts[i], index: offset });
        offset += parts[i].length + 1;
      } else {
        offset += parts[i].length;
      }
    }
  }
  return out;
}

export function buildOutline() {
  if (!state.activeNoteId) { _headings = []; return; }
  if (state.noteFormat === 'md') {
    const cm = (document.querySelector('.CodeMirror') || {}).CodeMirror;
    _headings = headingsFromMarkdown(cm ? cm.getValue() : '');
  } else {
    _headings = headingsFromDelta(state.quill ? state.quill.getContents() : null);
  }
}

export function refreshOutline() {
  buildOutline();
  render();
}

function render() {
  const list = $('#outline-list');
  if (!list) return;
  if (!_headings.length) {
    list.innerHTML = '<div class="outline-empty">这篇笔记还没有标题<br>（Markdown 里写 <code># 标题</code>，富文本用 H1–H3）</div>';
    return;
  }
  const min = Math.min(..._headings.map((h) => h.level));
  list.innerHTML = _headings.map((h, i) => `
    <div class="outline-item" data-outline="${i}"
         style="padding-left:${(h.level - min) * 12 + 8}px">
      <span class="outline-dot"></span>
      <span class="outline-text">${escapeHtml(h.text)}</span>
    </div>`).join('');
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

/** 跳到第 i 个标题 */
function jumpTo(i) {
  const h = _headings[i];
  if (!h) return;
  if (state.noteFormat === 'md') {
    const cm = (document.querySelector('.CodeMirror') || {}).CodeMirror;
    if (!cm) return;
    cm.setCursor({ line: h.line, ch: 0 });
    cm.scrollIntoView({ line: h.line, ch: 0 }, 100);
    cm.focus();
  } else if (state.quill) {
    state.quill.setSelection(h.index, 0);
    // Quill 的公开 API 不会自动滚动，取行节点自己滚
    try {
      const [line] = state.quill.getLine(h.index);
      const node = line && line.domNode;
      if (node && node.scrollIntoView) node.scrollIntoView({ block: 'center' });
    } catch (e) { /* 滚动失败不影响选中 */ }
  }
  markActive(i);
}

function markActive(i) {
  const list = $('#outline-list');
  if (!list) return;
  list.querySelectorAll('.outline-item').forEach((el, k) => {
    el.classList.toggle('active', k === i);
  });
}

/** 按当前光标位置高亮最近的标题（滚动/输入时调用，成本很低） */
export function syncOutlineActive() {
  if (!_visible || !_headings.length) return;
  let idx = -1;
  if (state.noteFormat === 'md') {
    const cm = (document.querySelector('.CodeMirror') || {}).CodeMirror;
    if (cm) {
      const line = cm.getCursor().line;
      _headings.forEach((h, i) => { if (h.line <= line) idx = i; });
    }
  } else if (state.quill) {
    const r = state.quill.getSelection();
    if (r) _headings.forEach((h, i) => { if (h.index <= r.index) idx = i; });
  }
  if (idx >= 0) markActive(idx);
}

/** 按小节名跳转（`[[标题#小节]]` 与链接抽屉共用）。先精确比标题文字，再退到 slug 比对。 */
export function jumpToHeadingByName(name) {
  buildOutline();
  const want = String(name || '').trim().toLowerCase();
  if (!want) return false;
  const wantSlug = slugify(want);
  let idx = _headings.findIndex((h) => String(h.text || '').trim().toLowerCase() === want);
  if (idx < 0) idx = _headings.findIndex((h) => slugify(h.text) === wantSlug);
  if (idx < 0) return false;
  jumpTo(idx);
  return true;
}

export function toggleOutline(force) {
  const drawer = $('#outline-drawer');
  if (!drawer) return;
  _visible = force === undefined ? !_visible : !!force;
  // 大纲与链接抽屉是**同一侧的参考面板**：同时打开只会互相压住，所以开一个就关另一个。
  // 用自定义事件而不是互相 import：两个模块本来就互相需要（链接要按小节跳转），
  // 再加一条 import 就成环了。
  if (_visible) document.dispatchEvent(new CustomEvent('myapp:outline-opened'));
  drawer.classList.toggle('hidden', !_visible);
  const btn = $('#btn-outline');
  if (btn) btn.classList.toggle('active', _visible);
  if (_visible) { refreshOutline(); syncOutlineActive(); }
}

export function isOutlineVisible() {
  return _visible;
}

export function initOutline() {
  $('#btn-outline')?.addEventListener('click', () => toggleOutline());
  document.addEventListener('myapp:links-opened', () => toggleOutline(false));
  $('#outline-close')?.addEventListener('click', () => toggleOutline(false));
  $('#outline-list')?.addEventListener('click', (ev) => {
    const item = ev.target.closest('[data-outline]');
    if (item) jumpTo(Number(item.getAttribute('data-outline')));
  });
}
