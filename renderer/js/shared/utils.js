// ====== 工具函数 ======
// 提取自 app.js — 全局命名空间，与现有代码兼容

export function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

export function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// 防抖函数（返回的函数带 .cancel() 可取消待执行任务）
export function debounce(fn, delay) {
  let timer;
  const wrapped = function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

/** 节流（**首个调用立即执行**，之后每 delay 最多一次，且停手后一定补一次尾部调用）。
 *
 *  与 debounce 的分工：debounce 是"停手才做"（适合落库——中间态没有价值）；
 *  throttle 是"边做边限速 + 收尾补偿"（适合刷新界面文案——用户希望敲字时就能看到字数变化，
 *  但不希望每个按键都全量重算一遍）。
 *  为什么必须带尾部调用：只有 leading 的节流会让"最后一次输入"永远不反映到界面上
 *  （用户敲完最后两个字，字数停在之前的值）。
 */
export function throttle(fn, delay) {
  let last = 0;
  let timer = null;
  const wrapped = function (...args) {
    const now = Date.now();
    const wait = delay - (now - last);
    if (wait <= 0) {
      if (timer) { clearTimeout(timer); timer = null; }
      last = now;
      fn.apply(this, args);
      return;
    }
    if (timer) return;                       // 已经排了尾部调用，不重复排
    timer = setTimeout(() => {
      timer = null;
      last = Date.now();
      fn.apply(this, args);
    }, wait);
  };
  wrapped.cancel = () => {
    if (timer) { clearTimeout(timer); timer = null; }
    last = 0;
  };
  return wrapped;
}

/** 标题 → 锚点 slug（第 9 轮）。
 *
 *  两处必须用**同一个**函数：markdown-it 生成的标题 id（`md-h-<slug>`）与
 *  `[[标题#小节]]` 的匹配、大纲定位。中文原样保留（Obsidian 也是这么做的），
 *  空格转连字符，其余标点丢掉——对不上时还有"标题文字精确相等"这条兜底。
 */
export function slugify(text) {
  return String(text == null ? '' : text)
    .trim().toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^\w\u4e00-\u9fff-]/g, '')
    .replace(/-{2,}/g, '-')
    .replace(/^-|-$/g, '');
}

// ====== front-matter（属性）的读写（第 9 轮）======
// 放在 shared 而不是某个 app 模块里：渲染层（预览要隐藏 front-matter、导出要印成属性表）
// 与属性面板都要用它，而 shared 是叶子模块，谁 import 都不会成环。

// 与 backend._FM_RE **逐字符一致**（只有 `.*?` ↔ `[\s\S]*?` 这一处写法差异）
const FM_RE = /^---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/;

function unquote(v) {
  const s = String(v == null ? '' : v).trim();
  if (s.length >= 2 && (s[0] === '"' || s[0] === "'") && s[s.length - 1] === s[0]) return s.slice(1, -1);
  return s;
}

/** front-matter 文本块 → 属性对象（与 backend.parse_front_matter 同一套规则） */
export function parsePropsBlock(blockText) {
  const props = {};
  let key = null;
  for (const raw of String(blockText || '').split(/\r?\n/)) {
    if (!raw.trim() || raw.trim().startsWith('#')) continue;
    const item = /^\s*-\s+(.*)$/.exec(raw);
    if (item && key) {
      if (!Array.isArray(props[key])) props[key] = [];
      props[key].push(unquote(item[1]));
      continue;
    }
    const at = raw.indexOf(':');
    if (at < 0) continue;
    key = raw.slice(0, at).trim();
    const val = raw.slice(at + 1).trim();
    if (!key) continue;
    if (!val) props[key] = [];
    else if (val.startsWith('[') && val.endsWith(']')) {
      props[key] = val.slice(1, -1).split(',').map((s) => unquote(s)).filter((s) => s !== '');
    } else props[key] = unquote(val);
  }
  // `key:` 后面没有列表项 → 空列表就是空值；不然"空数组"和"空串"会变成两副面孔
  Object.keys(props).forEach((k) => {
    if (Array.isArray(props[k]) && !props[k].length) props[k] = '';
  });
  return props;
}

/** 正文 → { props, body, block }（block 是原文里那段 front-matter，含结尾换行） */
export function splitFrontMatter(text) {
  const src = String(text == null ? '' : text);
  const m = FM_RE.exec(src);
  if (!m) return { props: {}, body: src, block: '' };
  return { props: parsePropsBlock(m[1]), body: src.slice(m[0].length), block: m[0] };
}

function yamlScalar(value) {
  const s = String(value == null ? '' : value).trim();
  if (!s) return '';
  // 需要引号的情形：含 YAML 元字符、首尾空白、以 `-` 开头（会被当成列表项）
  if (/[:#\[\]{},"'\n]|^\s|\s$|^-/.test(s)) return '"' + s.replace(/"/g, "'") + '"';
  return s;
}

/** 属性对象 → front-matter 文本块（列表写成换行式 `- 项`，空对象返回空串=删掉整段） */
export function serializeFrontMatter(props) {
  const keys = Object.keys(props || {});
  if (!keys.length) return '';
  const lines = ['---'];
  for (const k of keys) {
    const v = props[k];
    if (Array.isArray(v)) {
      lines.push(k + ':');
      v.forEach((item) => lines.push('  - ' + yamlScalar(item)));
    } else if (typeof v === 'boolean') {
      lines.push(k + ': ' + (v ? 'true' : 'false'));
    } else {
      lines.push(k + ': ' + yamlScalar(v));
    }
  }
  lines.push('---', '');
  return lines.join('\n') + '\n';
}
