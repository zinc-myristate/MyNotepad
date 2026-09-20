// ====== 自定义 Quill Blot（核心） ======
// 依赖：全局 Quill 对象（由 quill.js 提供），必须在 quill.js 之后加载
// 依赖：全局 katex 对象（由 katex/katex.min.js 提供）
// 包含：AttachmentBlot、FontBlot、SizeBlot、MathFormula

// ====== 自定义附件 Blot ======
// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { editMathFormula } from '../app/07-formula-security-dnd.js';
import { escapeHtml, formatFileSize } from '../shared/utils.js';

const Embed = Quill.import('blots/embed');

class AttachmentBlot extends Embed {
  static create(data) {
    const node = super.create();
    node.setAttribute('data-attachment-id', data.id || '');
    node.setAttribute('data-filename', data.filename || '');
    node.setAttribute('data-original-name', data.originalName || '');
    node.setAttribute('data-file-size', data.fileSize || 0);
    node.setAttribute('data-mime-type', data.mimeType || '');
    node.setAttribute('data-stored-path', data.storedPath || '');
    node.contentEditable = 'false';
    node.classList.add('attachment-card');

    const ext = (data.originalName || '').split('.').pop().toLowerCase();
    const iconMap = {
      pdf: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
      doc: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>',
      docx: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>',
      xls: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
      xlsx: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>',
      ppt: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
      pptx: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>',
      txt: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
      md: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>',
      zip: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>',
      rar: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>',
      '7z': '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M21 16V8a2 2 0 00-1-1.73l-7-4a2 2 0 00-2 0l-7 4A2 2 0 003 8v8a2 2 0 001 1.73l7 4a2 2 0 002 0l7-4A2 2 0 0021 16z"/></svg>',
      jpg: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
      jpeg: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
      png: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
      gif: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
      webp: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
      bmp: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>',
      svg: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>'
    };
    const icon = iconMap[ext] || '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"/></svg>';

    node.innerHTML = '<span class="attachment-icon">' + icon + '</span><span class="attachment-info"><span class="attachment-name">' + escapeHtml(data.originalName || '未知文件') + '</span><span class="attachment-meta">' + formatFileSize(data.fileSize || 0) + '</span></span><button class="attachment-remove" title="移除附件"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg></button>';

    node.addEventListener('dblclick', async (e) => {
      const path = node.getAttribute('data-stored-path');
      if (path) { await window.pywebview.api.file_open(path); }
    });

    node.querySelector('.attachment-remove').addEventListener('click', (e) => {
      e.stopPropagation();
      e.preventDefault();
      const blot = Quill.find(node);
      if (blot) { blot.remove(); }
    });

    return node;
  }

  static value(node) {
    return {
      id: node.getAttribute('data-attachment-id'),
      filename: node.getAttribute('data-filename'),
      originalName: node.getAttribute('data-original-name'),
      fileSize: parseInt(node.getAttribute('data-file-size')) || 0,
      mimeType: node.getAttribute('data-mime-type'),
      storedPath: node.getAttribute('data-stored-path')
    };
  }
}
AttachmentBlot.blotName = 'attachment';
AttachmentBlot.tagName = 'div';
AttachmentBlot.className = 'attachment-card';
Quill.register('formats/attachment', AttachmentBlot);

// ====== 自定义字体 Blot ======
const Inline = Quill.import('blots/inline');

class FontBlot extends Inline {
  static create(value) {
    const node = super.create();
    node.style.fontFamily = value;
    return node;
  }
  static formats(node) { return node.style.fontFamily || ''; }
  format(name, value) {
    if (name === 'myfont' && value) { this.domNode.style.fontFamily = value; }
    else { super.format(name, value); }
  }
}
FontBlot.blotName = 'myfont';
FontBlot.tagName = 'span';
Quill.register(FontBlot);

// ====== 自定义字号 Blot ======
class SizeBlot extends Inline {
  static create(value) {
    const node = super.create();
    node.style.fontSize = value;
    return node;
  }
  static formats(node) { return node.style.fontSize || ''; }
  format(name, value) {
    if (name === 'mysize' && value) { this.domNode.style.fontSize = value; }
    else { super.format(name, value); }
  }
}
SizeBlot.blotName = 'mysize';
SizeBlot.tagName = 'span';
Quill.register(SizeBlot);

// ====== LaTeX 数学公式 Blot ======
const MathBlot = Quill.import('blots/embed');

class MathFormula extends MathBlot {
  static create(data) {
    const node = super.create();
    node.setAttribute('data-latex', data.latex || '');
    node.setAttribute('data-display', data.display || 'inline');
    node.contentEditable = 'false';
    node.classList.add(data.display === 'block' ? 'math-block' : 'math-inline');
    try {
      katex.render(data.latex || '', node, {
        displayMode: data.display === 'block',
        throwOnError: false,
        strict: false,
        trust: true
      });
    } catch(e) {
      node.textContent = '[公式错误: ' + (data.latex || '') + ']';
    }
    node.addEventListener('click', () => { if (typeof editMathFormula === 'function') editMathFormula(node); });
    return node;
  }

  static value(node) {
    return { latex: node.getAttribute('data-latex'), display: node.getAttribute('data-display') };
  }

  static formats(node) { return node.getAttribute('data-latex'); }
}
MathFormula.blotName = 'math-formula';
MathFormula.tagName = 'span';
MathFormula.className = 'math-inline';
Quill.register('formats/math-formula', MathFormula);

// ====== 图片引用 Blot（覆盖内置 Image blot，图片外置的核心） ======
// Delta 值双态：string = 旧 data URI（存量/剪贴板粘贴，原样渲染）；dict = 新引用
//   {id, filename, storedPath, w?, x?, y?} — w/x/y 为位置/尺寸（.img-resizable 的 dataset 同步，
//   经 value() 进入 Delta，修复「位置/尺寸重启丢失」的存量问题）
// 渲染：占位 1px → 异步 read_file_base64 换 data URI（自包含，与现状显示一致；失败兜底 file://）。
// 异步设 src 不走 Quill API，不触发 text-change，不会误触发保存。
const IMG_PLACEHOLDER = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';

class NoteImageBlot extends Embed {
  static create(data) {
    const node = super.create();
    if (typeof data === 'string') {
      // 旧数据 URI（存量数据 / Quill 剪贴板内部复制）：原样渲染
      node.setAttribute('src', data);
      return node;
    }
    data = data || {};
    node.setAttribute('data-img-id', data.id || '');
    node.setAttribute('data-filename', data.filename || '');
    node.setAttribute('data-stored-path', data.storedPath || '');
    if (data.w) node.setAttribute('data-w', data.w);
    if (data.x) node.setAttribute('data-x', data.x);
    if (data.y) node.setAttribute('data-y', data.y);
    node.setAttribute('src', IMG_PLACEHOLDER);
    node.classList.add('note-image');
    NoteImageBlot._loadAsync(node);
    return node;
  }

  static value(node) {
    if (!node.hasAttribute('data-img-id')) {
      // 旧节点：原样返回 src 字符串（与内置 Image blot 语义一致）
      return node.getAttribute('src');
    }
    const v = {
      id: node.getAttribute('data-img-id'),
      filename: node.getAttribute('data-filename'),
      storedPath: node.getAttribute('data-stored-path'),
    };
    // 位置/尺寸持久化：.img-resizable 拖拽/滚轮写入 dataset，这里收进 Delta
    if (node.hasAttribute('data-w')) v.w = parseInt(node.dataset.w) || undefined;
    if (node.hasAttribute('data-x')) v.x = parseInt(node.dataset.x) || undefined;
    if (node.hasAttribute('data-y')) v.y = parseInt(node.dataset.y) || undefined;
    return v;
  }

  static async _loadAsync(node) {
    const path = node.getAttribute('data-stored-path');
    if (!path) return;
    try {
      const uri = await window.pywebview.api.read_file_base64(path);
      // isConnected 守卫：迟到渲染的节点可能已被删除/重建
      if (uri && node.isConnected) node.setAttribute('src', uri);
    } catch (e) {
      if (node.isConnected) node.setAttribute('src', 'file:///' + path.replace(/\\/g, '/'));
    }
  }
}
NoteImageBlot.blotName = 'image';
NoteImageBlot.tagName = 'img';
Quill.register('formats/image', NoteImageBlot, true);  // true = 覆盖内置 Image blot

