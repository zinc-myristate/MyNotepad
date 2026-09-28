// ====== 装饰元素 Quill Blot ======
// 依赖：全局 Quill 对象（由 quill.js 提供），必须在 quill.js + quill-blots.js 之后加载
// 包含：装饰分割线 (Divider)、贴纸印章 (Sticker) 及其面板构建函数

// ====== 装饰分割线 ======
// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { state } from '../app/01-core.js';

const DividerBlot = Quill.import('blots/block/embed');

class Divider extends DividerBlot {
  static create(data) {
    const node = super.create();
    node.setAttribute('data-divider-type', data.type || 'floral');
    node.contentEditable = 'false';
    node.classList.add('divider-blot', 'div-' + (data.type || 'floral'));
    return node;
  }
  static value(node) {
    return { type: node.getAttribute('data-divider-type') || 'floral' };
  }
}
Divider.blotName = 'divider';
Divider.tagName = 'div';
Divider.className = 'divider-blot';
Quill.register('formats/divider', Divider);

var dividerTypes = [
  { id:'floral', name:'花藤线', cls:'div-floral' },
  { id:'diamond', name:'菱形线', cls:'div-diamond' },
  { id:'wave', name:'波浪线', cls:'div-wave' },
  { id:'dots', name:'星点线', cls:'div-dots' },
  { id:'leaf', name:'叶饰线', cls:'div-leaf' },
  { id:'heart', name:'蝴蝶结线', cls:'div-heart' },
  { id:'dashed', name:'虚线花纹', cls:'div-dashed' },
  { id:'feather', name:'羽状线', cls:'div-feather' },
  { id:'double', name:'双线', cls:'div-double' },
  { id:'ornament', name:'花饰线', cls:'div-ornament' },
  { id:'lace', name:'镂空花边', cls:'div-lace' },
  { id:'bookend', name:'书卷线', cls:'div-bookend' },
];

export function buildDividerPanel() {
  const grid = document.getElementById('divider-grid');
  if (!grid) return;
  grid.innerHTML = '';
  dividerTypes.forEach(function(d) {
    const btn = document.createElement('button');
    btn.className = 'divider-option';
    btn.title = d.name;
    const preview = document.createElement('div');
    preview.className = d.cls;
    preview.style.cssText = 'width:100%;pointer-events:none;';
    btn.appendChild(preview);
    btn.addEventListener('click', function() {
      const panel = document.getElementById('divider-panel');
      if (panel) panel.style.display = 'none';
      if (!state.quill || !state.activeNoteId) return;
      const range = state.quill.getSelection(true);
      state.quill.insertEmbed(range.index, 'divider', { type: d.id });
      state.quill.setSelection(range.index + 1);
      state.quill.insertText(range.index + 1, '\n');
    });
    grid.appendChild(btn);
  });
}

// ====== 贴纸印章 ======
const StickerBlot = Quill.import('blots/embed');

// 贴纸 HTML 的重建式消毒。
// 为什么必须有这一层：贴纸 blot 走的是 Quill 的**剪贴板 class 匹配**——把
//   <span class="sticker-blot" data-sticker-id="deco-star" data-sticker-html="<img src=x onerror=...>">
// 粘进富文本笔记，Sticker.value() 就会把这串 HTML 收进 Delta；随后
// syncStickersToOverlay() 用 innerHTML 把它写回页面 —— 零点击执行，而且随正文落库
// （重开笔记再跑一次，持久型 XSS）。本应用不联网、桥接口能读/导出全部笔记，所以这条
// 通道的后果是"粘一段 HTML 就能拿走整个笔记本"。
//
// 贴纸的视觉本来就只有一种形态：`<span class="..." style="...">文字</span>`，全部内容来自
// 本文件的静态 stickerData 表。所以这里不做通用消毒，而是**只保留这个形态**：
// 除 span 外的标签一律丢弃、属性只留 class/style、style 只留贴纸用得上的少数几项且不含 url()。
const STICKER_HTML_TAGS = { SPAN: true };
const STICKER_STYLE_OK = new Set([
  'background', 'background-color', 'color', 'font-size', 'font-weight', 'font-style',
  'border', 'border-color', 'border-width', 'border-style', 'border-radius',
  'box-shadow', 'letter-spacing', 'line-height', 'text-align', 'padding',
]);

function _stickerSafeStyle(text) {
  const out = [];
  String(text || '').split(';').forEach((decl) => {
    const i = decl.indexOf(':');
    if (i < 0) return;
    const prop = decl.slice(0, i).trim().toLowerCase();
    const val = decl.slice(i + 1).trim();
    if (!STICKER_STYLE_OK.has(prop) || !val) return;
    // 值里不许出现能发起请求/执行的东西：url()/expression()/javascript:
    if (/url\s*\(|expression\s*\(|javascript:|@import|\\/i.test(val)) return;
    out.push(prop + ':' + val);
  });
  return out.join(';');
}

/** 把（可能来自粘贴的）贴纸 HTML 重建成"只有 span + 文字 + 安全 style"的形态 */
export function sanitizeStickerHtml(html) {
  const holder = document.createElement('div');
  holder.innerHTML = String(html == null ? '' : html);
  const out = document.createElement('span');
  const walk = (src, dst) => {
    src.childNodes.forEach((node) => {
      if (node.nodeType === 3) {                       // 文本
        dst.appendChild(document.createTextNode(node.nodeValue));
        return;
      }
      if (node.nodeType !== 1) return;                 // 注释/其他一律丢
      if (!STICKER_HTML_TAGS[node.tagName]) return;     // 白名单外整棵子树丢弃（含其文字）
      const span = document.createElement('span');
      const cls = node.getAttribute('class');
      if (cls && /^[A-Za-z0-9_\-\s]+$/.test(cls)) span.setAttribute('class', cls);
      const st = _stickerSafeStyle(node.getAttribute('style'));
      if (st) span.setAttribute('style', st);
      walk(node, span);
      dst.appendChild(span);
    });
  };
  walk(holder, out);
  return out.innerHTML;
}

class Sticker extends StickerBlot {
  static create(data) {
    const node = super.create();
    node.setAttribute('data-sticker-type', data.cat || 'deco');
    node.setAttribute('data-sticker-id', data.id || '');
    // 存进属性前先消毒：这是从 Delta/剪贴板进来的值，下面 value() 还会再消毒一次
    node.setAttribute('data-sticker-html', sanitizeStickerHtml(data.html || ''));
    node.setAttribute('data-x', data.x != null ? data.x : -1);
    node.setAttribute('data-y', data.y != null ? data.y : -1);
    node.contentEditable = 'false';
    node.classList.add('sticker-blot');
    // 用零宽空格占位，视觉效果在 overlay 中渲染
    node.innerHTML = '​';
    node.style.cssText = 'display:inline;font-size:0;line-height:0;visibility:hidden;';
    return node;
  }
  static value(node) {
    return {
      cat: node.getAttribute('data-sticker-type') || 'deco',
      id: node.getAttribute('data-sticker-id') || '',
      // 消毒后再收进 Delta —— 粘贴进来的节点属性不可信（见上面 sanitizeStickerHtml 的说明）
      html: sanitizeStickerHtml(node.getAttribute('data-sticker-html') || node.innerHTML),
      x: parseInt(node.getAttribute('data-x')) || 0,
      y: parseInt(node.getAttribute('data-y')) || 0
    };
  }
}
Sticker.blotName = 'sticker';
Sticker.tagName = 'span';
Sticker.className = 'sticker-blot';
Quill.register('formats/sticker', Sticker);

var stickerData = {
  date: [
    { id:'date-red', cat:'date', cls:'sticker-date', bg:'#E53E3E' },
    { id:'date-blue', cat:'date', cls:'sticker-date', bg:'#3182CE' },
    { id:'date-green', cat:'date', cls:'sticker-date', bg:'#38A169' },
    { id:'date-purple', cat:'date', cls:'sticker-date', bg:'#805AD5' },
    { id:'date-orange', cat:'date', cls:'sticker-date', bg:'#DD6B20' },
    { id:'date-teal', cat:'date', cls:'sticker-date', bg:'#319795' },
    { id:'date-pink', cat:'date', cls:'sticker-date', bg:'#B83280' },
  ],
  stamp: [
    { id:'stamp-done', cat:'stamp', cls:'sticker-stamp', html:'DONE', style:'color:#38A169;border-color:#38A169;box-shadow:inset 0 0 0 2px rgba(56,161,105,0.3);' },
    { id:'stamp-todo', cat:'stamp', cls:'sticker-stamp', html:'TODO', style:'color:#E53E3E;border-color:#E53E3E;box-shadow:inset 0 0 0 2px rgba(229,62,62,0.3);' },
    { id:'stamp-idea', cat:'stamp', cls:'sticker-stamp', html:'IDEA', style:'color:#D69E2E;border-color:#D69E2E;box-shadow:inset 0 0 0 2px rgba(214,158,46,0.3);' },
    { id:'stamp-imp', cat:'stamp', cls:'sticker-stamp', html:'重要', style:'color:#E53E3E;border-color:#E53E3E;font-size:13px;box-shadow:inset 0 0 0 2px rgba(229,62,62,0.3);' },
    { id:'stamp-new', cat:'stamp', cls:'sticker-stamp', html:'NEW', style:'color:#805AD5;border-color:#805AD5;box-shadow:inset 0 0 0 2px rgba(128,90,213,0.3);' },
    { id:'stamp-fix', cat:'stamp', cls:'sticker-stamp', html:'FIX', style:'color:#DD6B20;border-color:#DD6B20;box-shadow:inset 0 0 0 2px rgba(221,107,32,0.3);' },
  ],
  deco: [
    { id:'deco-star', cat:'deco', cls:'sticker-deco', html:'⭐', style:'' },
    { id:'deco-heart', cat:'deco', cls:'sticker-deco', html:'❤️', style:'' },
    { id:'deco-flower', cat:'deco', cls:'sticker-deco', html:'🌸', style:'' },
    { id:'deco-ribbon', cat:'deco', cls:'sticker-deco', html:'🎀', style:'' },
    { id:'deco-sparkle', cat:'deco', cls:'sticker-deco', html:'✨', style:'' },
    { id:'deco-gift', cat:'deco', cls:'sticker-deco', html:'🎁', style:'' },
    { id:'deco-fire', cat:'deco', cls:'sticker-deco', html:'🔥', style:'' },
    { id:'deco-bulb', cat:'deco', cls:'sticker-deco', html:'💡', style:'' },
  ],
  arrow: [
    { id:'arrow-right', cat:'arrow', cls:'sticker-deco', html:'→', style:'font-size:28px;color:var(--accent);' },
    { id:'arrow-left', cat:'arrow', cls:'sticker-deco', html:'←', style:'font-size:28px;color:var(--accent);' },
    { id:'arrow-down', cat:'arrow', cls:'sticker-deco', html:'↓', style:'font-size:28px;color:var(--accent);' },
    { id:'arrow-up', cat:'arrow', cls:'sticker-deco', html:'↑', style:'font-size:28px;color:var(--accent);' },
    { id:'arrow-ne', cat:'arrow', cls:'sticker-deco', html:'↗', style:'font-size:28px;color:var(--accent);' },
    { id:'arrow-se', cat:'arrow', cls:'sticker-deco', html:'↘', style:'font-size:28px;color:var(--accent);' },
  ],
  status: [
    { id:'status-todo', cat:'status', cls:'sticker-deco', html:'☐ 待办', style:'font-size:14px;color:var(--text-muted);' },
    { id:'status-progress', cat:'status', cls:'sticker-deco', html:'◐ 进行中', style:'font-size:14px;color:#D69E2E;' },
    { id:'status-done', cat:'status', cls:'sticker-deco', html:'✓ 完成', style:'font-size:14px;color:#38A169;' },
    { id:'status-cancel', cat:'status', cls:'sticker-deco', html:'✕ 已取消', style:'font-size:14px;color:#E53E3E;' },
    { id:'status-pause', cat:'status', cls:'sticker-deco', html:'⏸ 暂停', style:'font-size:14px;color:var(--text-muted);' },
  ],
  mark: [
    { id:'mark-star2', cat:'mark', cls:'sticker-deco', html:'★', style:'font-size:24px;color:#D69E2E;' },
    { id:'mark-flag', cat:'mark', cls:'sticker-deco', html:'⚑', style:'font-size:24px;color:#E53E3E;' },
    { id:'mark-heart2', cat:'mark', cls:'sticker-deco', html:'♥', style:'font-size:24px;color:#E53E3E;' },
    { id:'mark-bookmark', cat:'mark', cls:'sticker-deco', html:'🔖', style:'' },
    { id:'mark-qmark', cat:'mark', cls:'sticker-deco', html:'？', style:'font-size:22px;font-weight:900;color:#805AD5;' },
    { id:'mark-exclaim', cat:'mark', cls:'sticker-deco', html:'！', style:'font-size:22px;font-weight:900;color:#DD6B20;' },
    { id:'mark-check', cat:'mark', cls:'sticker-deco', html:'✔', style:'font-size:22px;color:#38A169;' },
  ]
};

var stickerCat = 'date';

// ESM：其他模块需要写入本变量（import 的绑定不可赋值），故导出 setter
export function setStickerCat(v) { stickerCat = v; }

export function buildStickerGrid() {
  const grid = document.getElementById('sticker-grid');
  if (!grid) return;
  grid.innerHTML = '';
  const items = stickerData[stickerCat] || [];
  // 日期贴纸：动态生成当天日期
  const today = new Date();
  const monthNames = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  const todayDay = today.getDate();
  const todayMonth = monthNames[today.getMonth()];
  items.forEach(function(s) {
    const btn = document.createElement('button');
    btn.className = 'sticker-item';
    const preview = document.createElement('span');
    preview.className = s.cls;
    preview.style.cssText = (s.bg ? 'background:' + s.bg + ';' : '') + (s.style||'');
    // 日期类型动态生成当天 HTML
    var itemHtml = s.html || '';
    if (stickerCat === 'date') {
      itemHtml = '<span class="sticker-date-day">' + todayDay + '</span><span class="sticker-date-month">' + todayMonth + '</span>';
    }
    preview.innerHTML = itemHtml;
    btn.appendChild(preview);
    btn.addEventListener('click', function() {
      const panel = document.getElementById('sticker-panel');
      if (panel) panel.style.display = 'none';
      if (!state.quill || !state.activeNoteId) return;
      const range = state.quill.getSelection(true);
      const html = '<span class="' + s.cls + '" style="' + ((s.bg?'background:'+s.bg+';':'')+(s.style||'')) + '">' + itemHtml + '</span>';
      // x=-1, y=-1 表示无预设位置，由 overlay 自动分配
      state.quill.insertEmbed(range.index, 'sticker', { cat: s.cat, id: s.id, html: html, x: -1, y: -1 });
      state.quill.setSelection(range.index + 1);
      // 立即同步到浮动覆盖层
      setTimeout(function() { syncStickersToOverlay(); }, 50);
    });
    grid.appendChild(btn);
  });
}

// ====== 贴纸覆盖层：自由浮动定位 ======
var _selectedStickerId = null;
var _dragState = null;

/** 扫描 Quill 中的 sticker blot，同步到浮动覆盖层 */
export function syncStickersToOverlay() {
  if (!state.quill) return;
  const overlay = document.getElementById('sticker-overlay');
  if (!overlay) return;
  const editor = state.quill.root;
  const blots = editor.querySelectorAll('.sticker-blot');
  const editorRect = editor.getBoundingClientRect();

  // 清除不在 Quill 中的覆盖层贴纸
  const blotIds = new Set();
  blots.forEach(function(b) { blotIds.add(b.getAttribute('data-sticker-id') + '|' + b.getAttribute('data-sticker-type')); });

  overlay.querySelectorAll('.sticker-float').forEach(function(el) {
    const sid = el.dataset.sid;
    if (!blotIds.has(sid)) {
      el.remove();
    }
  });

  // 为每个 Quill blot 创建/更新覆盖层贴纸
  blots.forEach(function(blot) {
    const sid = blot.getAttribute('data-sticker-id') + '|' + blot.getAttribute('data-sticker-type');
    let x = parseInt(blot.getAttribute('data-x'));
    let y = parseInt(blot.getAttribute('data-y'));
    const cat = blot.getAttribute('data-sticker-type');
    // 从 data-sticker-html 或原始数据恢复 HTML
    let html = blot.getAttribute('data-sticker-html');
    if (!html || html === '​') {
      // 尝试从 stickerData 恢复
      const sid_only = blot.getAttribute('data-sticker-id');
      for (var c in stickerData) {
        var found = stickerData[c].find(function(s) { return s.id === sid_only; });
        if (found) { html = found.html; break; }
      }
    }

    var existing = overlay.querySelector('[data-sid="' + sid + '"]');
    if (!existing) {
      existing = document.createElement('div');
      existing.className = 'sticker-float';
      existing.dataset.sid = sid;
      existing.dataset.blotCat = cat;
      // 设置贴纸的视觉样式（从 blot 上读取 class + 构建显示）
      var cls = '';
      if (cat === 'date') cls = 'sticker-date';
      else if (cat === 'stamp') cls = 'sticker-stamp';
      else cls = 'sticker-deco';
      existing.className = 'sticker-float ' + cls;
      // 从原始 stickerData 找到完整样式
      var blotId = blot.getAttribute('data-sticker-id');
      var foundSticker = null;
      for (var cc in stickerData) {
        foundSticker = stickerData[cc].find(function(s) { return s.id === blotId; });
        if (foundSticker) break;
      }
      if (foundSticker) {
        existing.style.cssText = (foundSticker.bg ? 'background:' + foundSticker.bg + ';' : '') + (foundSticker.style||'') + 'display:inline-flex;align-items:center;justify-content:center;';
        // 消毒后再写 innerHTML：html 来自 Delta（可能是被粘贴进来的内容），
        // 直接 innerHTML 会让里面的 onerror= 之类事件属性跑起来（见 sanitizeStickerHtml）
        existing.innerHTML = sanitizeStickerHtml(html);
      }
      // 如果位置未设置（-1），自动放在编辑器中心区域
      if (x === -1 || isNaN(x)) x = Math.max(20, (editorRect.width - 60) / 2);
      if (y === -1 || isNaN(y)) y = 80;
      overlay.appendChild(existing);
    }

    existing.style.left = x + 'px';
    existing.style.top = y + 'px';
    existing.dataset.x = x;
    existing.dataset.y = y;

    // 事件绑定（避免重复绑定用标记）
    if (existing.dataset.bound === '1') return;
    existing.dataset.bound = '1';

    existing.addEventListener('mousedown', function(e) {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      _dragState = {
        el: existing,
        startX: e.clientX,
        startY: e.clientY,
        origLeft: parseInt(existing.style.left) || 0,
        origTop: parseInt(existing.style.top) || 0,
        moved: false
      };
      // 选中当前贴纸
      overlay.querySelectorAll('.sticker-float.selected').forEach(function(el) { el.classList.remove('selected'); });
      existing.classList.add('selected');
      _selectedStickerId = sid;
      existing.classList.add('dragging');
    });
  });
}

// 全局鼠标移动和释放（在 document 上监听）
document.addEventListener('mousemove', function(e) {
  if (!_dragState) return;
  var dx = e.clientX - _dragState.startX;
  var dy = e.clientY - _dragState.startY;
  if (Math.abs(dx) < 2 && Math.abs(dy) < 2) return;
  _dragState.moved = true;
  var editor = state.quill ? state.quill.root : null;
  var editorRect = editor ? editor.getBoundingClientRect() : null;
  var elWidth = _dragState.el.offsetWidth || 50;
  var elHeight = _dragState.el.offsetHeight || 50;
  var newLeft = Math.max(0, Math.min(_dragState.origLeft + dx, (editorRect ? editorRect.width - elWidth : 700)));
  var newTop = Math.max(0, Math.min(_dragState.origTop + dy, (editorRect ? editorRect.height - elHeight : 900)));
  _dragState.el.style.left = newLeft + 'px';
  _dragState.el.style.top = newTop + 'px';
});

document.addEventListener('mouseup', function(e) {
  if (!_dragState) return;
  _dragState.el.classList.remove('dragging');
  if (_dragState.moved) {
    // 同步位置回 Quill blot
    var sid = _dragState.el.dataset.sid;
    syncStickerPositionToBlot(sid);
  }
  _dragState = null;
});

/** 将覆盖层贴纸位置同步回 Quill blot */
function syncStickerPositionToBlot(sid) {
  if (!state.quill) return;
  var overlay = document.getElementById('sticker-overlay');
  if (!overlay) return;
  var floatEl = overlay.querySelector('[data-sid="' + sid + '"]');
  if (!floatEl) return;
  var x = parseInt(floatEl.style.left) || 0;
  var y = parseInt(floatEl.style.top) || 0;
  var editor = state.quill.root;
  var blots = editor.querySelectorAll('.sticker-blot');
  blots.forEach(function(b) {
    var bsid = b.getAttribute('data-sticker-id') + '|' + b.getAttribute('data-sticker-type');
    if (bsid === sid) {
      b.setAttribute('data-x', x);
      b.setAttribute('data-y', y);
    }
  });
}

/** 保存前同步所有贴纸位置 */
export function syncStickersToQuill() {
  var overlay = document.getElementById('sticker-overlay');
  if (!overlay || !state.quill) return;
  var floats = overlay.querySelectorAll('.sticker-float');
  floats.forEach(function(f) {
    var sid = f.dataset.sid;
    if (sid) syncStickerPositionToBlot(sid);
  });
}

// 全局键盘事件：删除选中贴纸
document.addEventListener('keydown', function(e) {
  if (!_selectedStickerId || !state.quill) return;
  // 检查焦点是否在编辑器或覆盖层
  var overlay = document.getElementById('sticker-overlay');
  var activeEl = document.activeElement;
  var editorArea = document.getElementById('editor-container');
  if (!editorArea || !editorArea.contains(activeEl)) return;

  if (e.key === 'Delete' || e.key === 'Backspace') {
    e.preventDefault();
    var sid = _selectedStickerId;
    var parts = sid.split('|');
    var blotId = parts[0];
    // 从 Quill Delta 中删除该 blot
    var editor = state.quill.root;
    var blots = editor.querySelectorAll('.sticker-blot');
    blots.forEach(function(b) {
      if (b.getAttribute('data-sticker-id') === blotId) {
        var blot = Quill.find(b);
        if (blot) blot.remove();
      }
    });
    // 从 overlay 中删除
    var floatEl = overlay.querySelector('[data-sid="' + sid + '"]');
    if (floatEl) floatEl.remove();
    _selectedStickerId = null;
  }
});

// 点击编辑器空白区取消选中
document.addEventListener('click', function(e) {
  var overlay = document.getElementById('sticker-overlay');
  if (!overlay) return;
  if (!overlay.contains(e.target)) {
    overlay.querySelectorAll('.sticker-float.selected').forEach(function(el) { el.classList.remove('selected'); });
    _selectedStickerId = null;
  }
});
