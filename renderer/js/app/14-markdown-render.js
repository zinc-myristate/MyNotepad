// ====== Markdown 渲染（预览与导出共用的唯一入口）======
// 为什么单独一个模块：渲染只能有一条路径——预览、导出 HTML/DOCX 都走它，否则
// 「预览里好看、导出后变样」是必然的；安全上也只需要审计这一处消毒逻辑。
//
// 内嵌 HTML 是**用户内容**（还可能来自导入的 .md 文件），所以：
//   1) 一律先 DOMPurify 白名单化；
//   2) style 属性只放行排版相关的少量属性，且 class 必须以白名单前缀开头
//      （否则用户可以写 class="panel-overlay" 之类去借应用自己的样式）；
//   3) 链接只允许 http/https/mailto/attachments 相对路径，事件属性全部剥掉。

import { state } from './01-core.js';

const MATH_BLOCK = /\$\$([\s\S]+?)\$\$/g;
const MATH_INLINE = /\$([^$\n]+?)\$/g;
const MATH_TOKEN = '@@MDMATH';

const ALLOWED_TAGS = [
  'p', 'br', 'hr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'strong', 'em', 'del', 's', 'u', 'mark', 'sup', 'sub', 'code', 'pre', 'kbd',
  'blockquote', 'ul', 'ol', 'li', 'span', 'div',
  'table', 'thead', 'tbody', 'tr', 'th', 'td',
  'a', 'img', 'details', 'summary',
  // KaTeX 输出（MathML + 排版用的 span）
  'math', 'semantics', 'annotation', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub',
  'mfrac', 'msqrt', 'mroot', 'mover', 'munder', 'mtable', 'mtr', 'mtd', 'mtext',
  'mspace', 'mpadded', 'mphantom', 'mstyle', 'menclose',
];
const ALLOWED_ATTR = [
  'class', 'style', 'href', 'title', 'alt', 'src', 'colspan', 'rowspan', 'align',
  'data-md-src', 'data-md-attach', 'data-md-file', 'data-md-broken',
  'aria-hidden', 'mathvariant', 'encoding', 'display',
];
const ALLOWED_STYLE = new Set([
  'color', 'background-color', 'font-size', 'font-family', 'font-weight',
  'font-style', 'text-align', 'text-decoration', 'vertical-align', 'line-height',
]);
// class 白名单前缀：应用自己的样式类绝不能被用户内容借走
const ALLOWED_CLASS_PREFIX = ['md-', 'divider-', 'sticker', 'katex', 'math', 'hljs'];

let _md = null;

function md() {
  if (_md || !window.markdownit) return _md;
  _md = window.markdownit({
    html: true,          // 允许内嵌 HTML（第 6 轮决策：用它保留颜色/字号/字体）
    linkify: false,      // 不自动把裸 URL 变链接：记事本里出现的网址原文更该保持原样
    breaks: false,       // 单个换行不当 <br>（Markdown 语义：空行才分段）
    highlight: highlightCode,   // ```lang 代码块 → hljs 着色（无 hljs 时返回空串=退化为纯文本）
  });
  installTaskList(_md);
  installSanitizer();
  return _md;
}

/** GFM 待办清单：markdown-it 默认不认 `- [ ]`，这里把列表项转成带勾选框的 li */
function installTaskList(instance) {
  instance.core.ruler.after('inline', 'md_task_lists', (state2) => {
    const toks = state2.tokens;
    for (let i = 0; i < toks.length; i++) {
      if (toks[i].type !== 'list_item_open') continue;
      let j = i + 1;
      let inline = null;
      while (j < toks.length && toks[j].type !== 'list_item_close') {
        if (toks[j].type === 'inline') { inline = toks[j]; break; }
        j++;
      }
      if (!inline) continue;
      const m = /^\[([ xX])\]\s+/.exec(inline.content);
      if (!m) continue;
      inline.content = inline.content.slice(m[0].length);
      inline.children = [];
      state2.md.inline.parse(inline.content, state2.md, state2.env, inline.children);
      toks[i].meta = Object.assign({}, toks[i].meta, { mdTask: m[1].toLowerCase() === 'x' });
    }
  });

  const defaultItemOpen = instance.renderer.rules.list_item_open
    || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options));
  instance.renderer.rules.list_item_open = (tokens, idx, options, env, self) => {
    const meta = tokens[idx].meta || {};
    if (typeof meta.mdTask !== 'boolean') return defaultItemOpen(tokens, idx, options, env, self);
    return '<li class="md-task' + (meta.mdTask ? ' md-task-done' : '') + '">'
      + '<span class="md-task-box" aria-hidden="true"></span>';
  };
}

/** 一次性安装 DOMPurify 钩子：过滤 style 白名单与 class 前缀 */
let _purifyReady = false;
function installSanitizer() {
  if (_purifyReady || !window.DOMPurify) return;
  _purifyReady = true;
  window.DOMPurify.addHook('afterSanitizeAttributes', (node) => {
    if (node.hasAttribute && node.hasAttribute('style')) {
      const keep = [];
      (node.getAttribute('style') || '').split(';').forEach((decl) => {
        const i = decl.indexOf(':');
        if (i < 0) return;
        const prop = decl.slice(0, i).trim().toLowerCase();
        const val = decl.slice(i + 1).trim();
        if (!ALLOWED_STYLE.has(prop)) return;
        if (/expression|javascript:|url\s*\(/i.test(val)) return;
        keep.push(prop + ': ' + val);
      });
      if (keep.length) node.setAttribute('style', keep.join('; '));
      else node.removeAttribute('style');
    }
    if (node.hasAttribute && node.hasAttribute('class')) {
      const keep = (node.getAttribute('class') || '').split(/\s+/).filter((c) => c
        && ALLOWED_CLASS_PREFIX.some((p) => c.toLowerCase().startsWith(p)));
      if (keep.length) node.setAttribute('class', keep.join(' '));
      else node.removeAttribute('class');
    }
    // 相对路径的图片先摘掉 src：由 hydrateMarkdownAssets 异步换成 data URI，
    // 否则浏览器会去请求 http://127.0.0.1:<port>/attachments/...（那是渲染器目录，必然 404）
    if (node.tagName === 'IMG') {
      const src = node.getAttribute('src') || '';
      if (src && !/^(https?:|data:|file:)/i.test(src)) {
        node.setAttribute('data-md-src', src);
        node.removeAttribute('src');
      }
    }
    if (node.tagName === 'A') {
      const href = node.getAttribute('href') || '';
      if (/^attachments\//i.test(href)) {
        node.setAttribute('data-md-attach', href);
        node.removeAttribute('href');
      } else if (!/^(https?:|mailto:|file:)/i.test(href)) {
        node.removeAttribute('href');       // javascript:/vbscript: 等一律不给
      }
    }
  });
}

/** 把 `$…$` / `$$…$$` 抽成占位符（Markdown 解析完再插回 KaTeX 结果） */
function extractMath(text, store) {
  let out = text.replace(MATH_BLOCK, (_m, tex) => {
    store.push({ tex, display: true });
    return '\n\n' + MATH_TOKEN + (store.length - 1) + '@@\n\n';
  });
  out = out.replace(MATH_INLINE, (m, tex) => {
    if (!tex.trim()) return m;
    store.push({ tex, display: false });
    return MATH_TOKEN + (store.length - 1) + '@@';
  });
  return out;
}

function renderMath(store) {
  const katex = window.katex;
  return store.map((item) => {
    if (!katex) return '<code>' + escapeAttr(item.tex) + '</code>';
    try {
      const html = katex.renderToString(item.tex, {
        displayMode: item.display, throwOnError: false, trust: false, strict: 'ignore',
      });
      // KaTeX 自己的输出再过一遍消毒（唯一目的是不给自己开后门）
      return window.DOMPurify ? window.DOMPurify.sanitize(html, {
        ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false,
      }) : html;
    } catch (e) {
      return '<code>' + escapeAttr(item.tex) + '</code>';
    }
  });
}

/** 代码块高亮：markdown-it 的 highlight 钩子要返回**完整**的 <pre><code>，
 *  否则它自己再包一层。语言未知时返回空串 → markdown-it 走默认转义（安全）。 */
function highlightCode(code, lang) {
  const hljs = window.hljs;
  if (!hljs || !lang) return '';
  try {
    if (!hljs.getLanguage(lang)) return '';
    const html = hljs.highlight(code, { language: lang, ignoreIllegals: true }).value;
    return '<pre class="hljs"><code class="language-' + escapeAttr(lang) + '">' + html + '</code></pre>';
  } catch (e) {
    return '';
  }
}

function escapeAttr(s) {
  return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/**
 * Markdown 文本 → 可安全插入 DOM 的 HTML。
 * @param {string} text 源码
 * @param {{noteId?: string}} opts 当前笔记 id（附件路径解析要用）
 */
export function renderMarkdown(text, opts = {}) {
  const instance = md();
  if (!instance) {
    return '<pre class="md-fallback">' + escapeAttr(text) + '</pre>';
  }
  const mathStore = [];
  const prepared = extractMath(text || '', mathStore);
  let html = instance.render(prepared);
  if (window.DOMPurify) {
    html = window.DOMPurify.sanitize(html, {
      ALLOWED_TAGS, ALLOWED_ATTR, ALLOW_DATA_ATTR: false,
      FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'form', 'input'],
    });
  }
  const rendered = renderMath(mathStore);
  html = html.replace(new RegExp(MATH_TOKEN + '(\\d+)@@', 'g'), (m, i) => rendered[Number(i)] || '');
  return html;
}

/** 相对附件路径 → 后端可读的绝对路径（后端还有一层目录白名单） */
async function resolveAssetPath(rel, noteId) {
  const m = /^attachments\/([^/]+)\/(.+)$/.exec(rel || '');
  if (m) {
    try {
      return await window.pywebview.api.attachments_get_path(m[1], m[2]);
    } catch (e) {
      return null;
    }
  }
  if (/^[a-zA-Z]:[\\/]/.test(rel)) return rel;      // 绝对路径（后端再判白名单）
  return null;
}

/** 异步把相对路径图片换成 data URI，并给附件链接挂上打开行为 */
export async function hydrateMarkdownAssets(root, noteId) {
  if (!root) return;
  const imgs = Array.from(root.querySelectorAll('img[data-md-src]'));
  for (const img of imgs) {
    const rel = img.getAttribute('data-md-src');
    try {
      const abs = await resolveAssetPath(rel, noteId);
      if (!abs) throw new Error('unresolvable');
      const uri = await window.pywebview.api.read_file_base64(abs);
      if (!uri) throw new Error('unreadable');
      if (img.isConnected) img.setAttribute('src', uri);
    } catch (e) {
      if (img.isConnected) {
        img.setAttribute('data-md-broken', '1');
        img.setAttribute('alt', (img.getAttribute('alt') || rel || '') + '（图片缺失）');
      }
    }
  }
  root.querySelectorAll('a[data-md-attach]').forEach((a) => {
    a.classList.add('md-attachment');
    if (a.dataset.mdBound === '1') return;
    a.dataset.mdBound = '1';
    a.addEventListener('click', async (ev) => {
      ev.preventDefault();
      const rel = a.getAttribute('data-md-attach');
      try {
        const abs = await resolveAssetPath(rel, noteId);
        if (abs) await window.pywebview.api.file_open(abs);
      } catch (e) { /* 打开失败不打断 */ }
    });
  });
}

/** 导出用 HTML：渲染 + 把相对路径图片内嵌成 data URI（导出文件换台机器也不会破图）。
 *  为什么导出重新渲染而不是直接抓预览区 innerHTML：预览有 180ms 节流，可能落后于源码。 */
export async function markdownExportHtml(text, noteId) {
  const html = renderMarkdown(text, { noteId });
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  for (const img of Array.from(tmp.querySelectorAll('img[data-md-src]'))) {
    try {
      const abs = await resolveAssetPath(img.getAttribute('data-md-src'), noteId);
      if (!abs) continue;
      const uri = await window.pywebview.api.read_file_base64(abs);
      if (uri) img.setAttribute('src', uri);
    } catch (e) { /* 单张图失败不影响整篇导出 */ }
  }
  return tmp.innerHTML;
}

/** 预览区里的链接点击：交给系统默认程序打开（http/https/本地文件都支持） */
export function bindPreviewLinks(root) {
  if (!root || root.dataset.mdLinksBound === '1') return;
  root.dataset.mdLinksBound = '1';
  root.addEventListener('click', async (ev) => {
    const a = ev.target.closest && ev.target.closest('a[href]');
    if (!a) return;
    ev.preventDefault();
    try {
      await window.pywebview.api.file_open(a.getAttribute('href'));
    } catch (e) { /* 忽略 */ }
  });
}

export function currentNoteId() {
  return state.activeNoteId || null;
}
