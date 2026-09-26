// ====== 自适应背景分析 ======
// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { $, $$, NotepadConfig, closePanel, dom, openPanel, showToast, state } from './01-core.js';
import { notesStore } from './01b-store.js';
import { loadNotes, renderNoteList, saveCurrentNote, selectNote } from './03-notes.js';
import { verifyAndSelectNote } from './07-formula-security-dnd.js';
import { updateNotebookCount } from './09-boot.js';
import { buildDividerPanel, buildStickerGrid, setStickerCat } from '../quill/quill-deco.js';
import { escapeHtml } from '../shared/utils.js';

/** 当前显示着背景图的那一层（笔记层优先于全局层） */
function activeBgLayer() {
  const note = document.querySelector('.note-bg-layer');
  const global = document.querySelector('.global-bg-layer');
  for (const el of [note, global]) {
    if (el && el.style.backgroundImage) return el;
  }
  return null;
}

/** 界面行 → 它压在图片的哪一段（条带划分见 BAND_RANGES）。
 *  为什么按段采样：一张图常常上半亮下半暗，整图一个"明/暗"判断必然有一半界面读不清。 */
const CHROME_BANDS = {
  title: ['#title-row', '#chrome-veil'],
  upper: ['#tag-bar', '#prop-block', '#md-toolbar', '#editor-toolbar', '#font-size-bar'],
  mid: ['.ql-editor', '#md-editor', '#md-preview', '#content-veil'],
  bottom: ['#editor-status', '#find-bar', '#bottom-veil'],
};
/** 正文里**装字**的那几个元素（薄纱不在其中：它只借 mid 段的明暗取色） */
const CONTENT_INK_SELECTORS = ['.ql-editor', '#md-editor', '#md-preview'];
const BAND_RANGES = {           // 占编辑器区域高度的比例（与界面行在屏幕上的位置对应）
  title: [0.00, 0.09],
  upper: [0.09, 0.27],
  // 正文带再拆上下两段：正文区跨度最大（0.30~0.72），一个判定必然照顾不到"上亮下暗"的图。
  // 两段一致 → 单色墨够用；两段不一致 → 走 .bg-ink-mixed 的描边兜底（见 applyChromeTones）。
  midTop: [0.28, 0.50],
  midBottom: [0.50, 0.74],
  mid: [0.30, 0.72],
  bottom: [0.92, 1.00],
};

function bandLuminance(ctx, w, h, from, to) {
  const y0 = Math.max(0, Math.floor(h * from));
  const y1 = Math.min(h, Math.max(y0 + 1, Math.ceil(h * to)));
  const data = ctx.getImageData(0, y0, w, y1 - y0).data;
  const toLinear = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
  let r = 0, g = 0, b = 0, n = 0;
  for (let i = 0; i < data.length; i += 16) {     // 每 4 像素取 1 个，够用且快
    r += data[i]; g += data[i + 1]; b += data[i + 2]; n++;
  }
  if (!n) return { r: 255, g: 255, b: 255, luminance: 1, dark: false };
  r /= n; g /= n; b /= n;
  const luminance = 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
  return { r: Math.round(r), g: Math.round(g), b: Math.round(b), luminance, dark: luminance < 0.42 };
}

export function analyzeImageColor(dataUri, callback) {
  const img = new Image();
  img.onload = () => {
    // 用与 CSS 完全相同的「cover + 缩放 + 位置」把图draw到小画布，再按条带取亮度。
    // 不自己算这套几何就用不上分区采样（画布哪一段对应屏幕哪一行会错位）。
    const layer = activeBgLayer();
    const area = document.getElementById('editor-area');
    const rect = area ? area.getBoundingClientRect() : { width: 1200, height: 800 };
    const boxW = Math.max(1, Math.round(rect.width));
    const boxH = Math.max(1, Math.round(rect.height));
    const zoom = parseFloat((layer && layer.style.backgroundSize) || '100') || 100;
    const posParts = ((layer && layer.style.backgroundPosition) || '50% 50%').split(/\s+/);
    const posX = (parseFloat(posParts[0]) || 0) / 100;                  // '50%'→0.5，'0%'→0
    const posY = posParts.length > 1 ? (parseFloat(posParts[1]) || 0) / 100 : 0.5;

    const scale = Math.min(1, 96 / Math.max(boxW, boxH));
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(boxW * scale));
    canvas.height = Math.max(1, Math.round(boxH * scale));
    const ctx = canvas.getContext('2d');
    const drawW = boxW * (zoom / 100) * scale;
    const drawH = drawW * (img.naturalHeight / Math.max(1, img.naturalWidth));
    const dx = (canvas.width - drawW) * posX;
    const dy = (canvas.height - drawH) * posY;
    // 图没盖住的地方露出来的是**主题的编辑器底色**（不是白色）：深色主题下那是暗的，
    // 当成白底会把上下两条界面行判反（实测：纯深色背景图却给标题行配了深色字）。
    const editorBg = getComputedStyle(document.body).getPropertyValue('--bg-editor').trim();
    ctx.fillStyle = editorBg || '#ffffff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, dx, dy, drawW, drawH);

    const bands = {};
    Object.keys(BAND_RANGES).forEach((key) => {
      bands[key] = bandLuminance(ctx, canvas.width, canvas.height,
        BAND_RANGES[key][0], BAND_RANGES[key][1]);
    });

    // 整图亮度（保持旧字段，settings.adaptive_color 里仍存一份）
    const small = document.createElement('canvas');
    const maxDim = 80;
    const s2 = Math.min(1, maxDim / Math.max(img.width, img.height));
    small.width = Math.round(img.width * s2);
    small.height = Math.round(img.height * s2);
    small.getContext('2d').drawImage(img, 0, 0, small.width, small.height);
    const data = small.getContext('2d').getImageData(0, 0, small.width, small.height).data;

    // 整图中位数（抗极值），仅用于记录/兜底
    const gridSize = 8;
    const samples = [];
    for (let gy = 0; gy < gridSize; gy++) {
      for (let gx = 0; gx < gridSize; gx++) {
        let sr = 0, sg = 0, sb = 0, sc = 0;
        const x0 = Math.floor(gx * small.width / gridSize);
        const y0 = Math.floor(gy * small.height / gridSize);
        const x1 = Math.floor((gx + 1) * small.width / gridSize);
        const y1 = Math.floor((gy + 1) * small.height / gridSize);
        for (let y = y0; y < y1; y += 2) {
          for (let x = x0; x < x1; x += 2) {
            const i = (y * small.width + x) * 4;
            sr += data[i]; sg += data[i+1]; sb += data[i+2]; sc++;
          }
        }
        if (sc > 0) samples.push({ r: sr/sc, g: sg/sc, b: sb/sc });
      }
    }

    // 按亮度排序取中位数，避免极值
    samples.sort((a, b) => {
      const la = 0.299*a.r + 0.587*a.g + 0.114*a.b;
      const lb = 0.299*b.r + 0.587*b.g + 0.114*b.b;
      return la - lb;
    });
    const mid = samples[Math.floor(samples.length / 2)];
    const r = Math.round(mid.r), g = Math.round(mid.g), b = Math.round(mid.b);
    // W3C 相对亮度
    const toLinear = (c) => { c /= 255; return c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4); };
    const luminance = 0.2126*toLinear(r) + 0.7152*toLinear(g) + 0.0722*toLinear(b);

    // 合并计算整体亮度偏差：偏暗则用亮底，偏亮则用暗底
    const isDarkBg = luminance < 0.35;
    callback({ r, g, b, luminance, isDarkBg, bands });
  };
  img.onerror = () => callback(null);
  img.src = dataUri;
}

const ALL_TONE_SELECTORS = Object.values(CHROME_BANDS).flat();
const TONE_CLASSES = ['bg-tone-dark', 'bg-tone-light', 'bg-ink-mixed'];

/** 两片界面薄纱往正文里的渐隐长度（CSS 的 mask 里写着同样的 48px / 40px，别只改一边） */
const VEIL_FADE_TOP = 48;
const VEIL_FADE_BOTTOM = 40;

/** 三片薄纱的高度：按"编辑器内容区顶边"与"状态栏顶边"实时算。
 *  为什么不用魔法数字：工具栏会换行、md↔富文本切换会让界面层高度变化，
 *  写死高度时渐隐段就会落在错误的行上（那样交界处反而更明显）。 */
export function updateChromeVeil() {
  const container = document.getElementById('editor-container');
  const top = document.getElementById('chrome-veil');
  const bottom = document.getElementById('bottom-veil');
  const content = document.getElementById('content-veil');
  if (!container || !top || !bottom) return;
  const inkArea = document.querySelector('#md-editor:not(.hidden)') || document.querySelector('.ql-container')
    || document.getElementById('md-editor') || document.querySelector('#quill-editor .ql-container');
  const status = document.getElementById('editor-status');
  const cTop = container.getBoundingClientRect().top;
  const contentTop = inkArea ? inkArea.getBoundingClientRect().top - cTop : 0;
  // 顶部薄纱：盖住界面层，再往正文里多留 48px 做渐隐
  const topH = Math.max(0, Math.round(contentTop)) + VEIL_FADE_TOP;
  if (top.style.height !== topH + 'px') top.style.height = topH + 'px';
  const statusTop = status && !status.classList.contains('hidden')
    ? status.getBoundingClientRect().top - cTop : container.clientHeight;
  const bottomH = Math.max(0, Math.round(container.clientHeight - statusTop)) + VEIL_FADE_BOTTOM;
  if (bottom.style.height !== bottomH + 'px') bottom.style.height = bottomH + 'px';

  // 正文薄纱（第三片）：上下两片之外剩下的那块。**必须**让它的渐隐段与前两片的渐隐段完全重合——
  // 三片薄纱的 alpha 是相加的，首尾相接就会出现两条新的硬边（"突兀的一块"就是这么来的）：
  //   上边 = 内容区顶边 - 48（顶部薄纱正是在这里开始变淡）
  //   下边 = 状态栏顶边 + 40（底部薄纱正是在这里开始变淡）
  // 于是总不透明度沿 y 连续：c → c+k → k → c，没有任何跳变。
  if (content) {
    const cvTop = Math.max(0, Math.round(contentTop)) - VEIL_FADE_TOP;
    const cvH = Math.max(0, (Math.max(0, Math.round(statusTop)) + VEIL_FADE_BOTTOM) - cvTop);
    if (content.style.top !== cvTop + 'px') content.style.top = cvTop + 'px';
    if (content.style.height !== cvH + 'px') content.style.height = cvH + 'px';
  }
}

/** 界面层高度会因窗口缩放 / 工具栏换行 / 编辑器切换而变化：观察几行界面元素即可 */
function watchChromeVeil() {
  if (typeof ResizeObserver === 'undefined') return;
  const rows = ['#title-row', '#tag-bar', '#prop-block', '#font-size-bar', '#md-toolbar',
    '#editor-toolbar', '#editor-status'];
  const ro = new ResizeObserver(() => updateChromeVeil());
  rows.forEach((sel) => {
    const el = document.querySelector(sel);
    if (el) ro.observe(el);
  });
  window.addEventListener('resize', updateChromeVeil);
}

/** 按图片各段明暗，给每一行界面切换文字色（.bg-tone-dark / .bg-tone-light） */
export function applyChromeTones(colorInfo) {
  const bands = (colorInfo && colorInfo.bands) || null;
  const fallbackDark = colorInfo ? !!colorInfo.isDarkBg : false;
  Object.keys(CHROME_BANDS).forEach((band) => {
    const dark = bands && bands[band] ? bands[band].dark : fallbackDark;
    CHROME_BANDS[band].forEach((sel) => {
      const el = document.querySelector(sel);
      if (!el) return;
      el.classList.toggle('bg-tone-dark', dark);
      el.classList.toggle('bg-tone-light', !dark);
    });
  });
  // 正文带拆出的上下两段一明一暗（"混合底"）：单色墨必然有一半读不清，于是给正文描边兜底。
  // 这是这轮划的界——**不做**"滚动跟随"（正文滚动时按视口位置换墨会抖，而且没法测），
  // 混合底用"描边 + 加厚阴影"解决；两段一致时（绝大多数图）不加，保持干净的字形。
  const midTop = bands && bands.midTop, midBottom = bands && bands.midBottom;
  const mixed = !!(midTop && midBottom && midTop.dark !== midBottom.dark);
  CONTENT_INK_SELECTORS.forEach((sel) => {
    const el = document.querySelector(sel);
    if (el) el.classList.toggle('bg-ink-mixed', mixed);
  });
}

/** 「背景模糊」与「界面不透明度 / 正文不透明度」：前者给图降噪，后两者只给界面行与正文区加薄纱 */
export function applyBackgroundTuning(opts = {}) {
  const blur = Number(opts.blur) || 0;
  const scrim = opts.scrim === undefined ? 0.3 : Number(opts.scrim) || 0;
  const contentScrim = opts.contentScrim === undefined
    ? Number(state.bgContentScrim === undefined ? 0.3 : state.bgContentScrim) || 0
    : Number(opts.contentScrim) || 0;
  [dom.globalBgLayer, dom.noteBgLayer].forEach((el) => {
    if (!el) return;
    // 模糊会让边缘透出底色，所以顺带放大一点点盖住（1 + 2*blur% 的幅度足够）
    el.style.filter = blur > 0 ? 'blur(' + blur + 'px)' : '';
    el.style.transform = blur > 0 ? 'scale(' + (1 + Math.min(0.12, blur * 0.008)) + ')' : '';
  });
  const clamp01 = (v) => String(Math.max(0, Math.min(1, v)));
  document.documentElement.style.setProperty('--ad-scrim', clamp01(scrim));
  document.documentElement.style.setProperty('--ad-content-scrim', clamp01(contentScrim));
  state.bgBlur = blur;
  state.bgScrim = scrim;
  state.bgContentScrim = contentScrim;
}

let _veilWatched = false;
let _adaptiveSeq = 0;   // 分析请求序号：迟到的回调直接丢弃（连点主题/连切笔记会乱序）

/** 当前那层图上正在显示的图片 URL（data: 或 file:///），没有图则 null */
function activeBgImageUrl() {
  const layer = activeBgLayer();
  if (!layer) return null;
  const raw = (layer.style.backgroundImage || '').trim();
  const m = /^url\((['"]?)(.*)\1\)$/.exec(raw);
  return m && m[2] ? m[2] : null;
}

/** 按「当前真正可见的那一层图」决定自适应层的去留。
 *
 *  为什么要有它：判断依据必须是**屏幕上有不有图**，而不是 settings 里的 bg_type。
 *  曾经的写法散在 applyGlobalBackground() / applyNoteBackground() 里，两处都是
 *  "全局背景 = 主题色 → clearAdaptiveUI()" —— 可图完全可能挂在**笔记层**
 *  （notes.bg_type='image'，全局层本来就是默认的「主题色」），于是点一下主题
 *  就把整个自适应层清掉：
 *    · body.adaptive-bg 没了 → .ql-toolbar 恢复自带的 55% 白玻璃（backdrop blur）
 *      = 一条硬边亮块，正是用户报的"突兀的一块"；
 *    · 两片薄纱 display:none（界面行直接压在原图上）；
 *    · .bg-tone-* 被摘掉 → 墨色回落成主题色 → 深色图上变暗底暗字，读不清。
 *  现在统一走这里：有图就**重算**（主题变了 --bg-editor 也变了，正是重算的理由），没图才清。 */
export function refreshAdaptiveUI() {
  const url = activeBgImageUrl();
  if (!url) { clearAdaptiveUI(); return; }
  const seq = ++_adaptiveSeq;
  analyzeImageColor(url, (info) => {
    if (seq !== _adaptiveSeq) return;        // 期间又换了背景/主题，这份结果已经过期
    if (!info) { clearAdaptiveUI(); return; }
    applyAdaptiveUI(info);
  });
}

export function applyAdaptiveUI(colorInfo) {
  const body = document.body;
  body.classList.add('adaptive-bg');
  body.style.setProperty('--ad-border-strong', 'rgba(128,128,128,0.40)');
  applyChromeTones(colorInfo);
  updateChromeVeil();
  if (!_veilWatched) { _veilWatched = true; watchChromeVeil(); }
  if (colorInfo) {
    window.pywebview.api.settings_set('adaptive_color', JSON.stringify(colorInfo));
  }
}

export function clearAdaptiveUI() {
  const body = document.body;
  body.classList.remove('adaptive-bg');
  body.style.removeProperty('--ad-border-strong');
  // 去掉所有行上的明暗类：不留"上一次图片"的配色
  ALL_TONE_SELECTORS.forEach((sel) => {
    const el = document.querySelector(sel);
    if (el) el.classList.remove(...TONE_CLASSES);
  });
  window.pywebview.api.settings_set('adaptive_color', '');
}

// ====== 手帐日历 ======
let calYear, calMonth;

function buildCalendar(year, month) {
  calYear = year; calMonth = month;
  const today = new Date(); today.setHours(0,0,0,0);
  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);
  const daysInMonth = lastDay.getDate();
  // 周一=0, 周日=6
  let startDow = firstDay.getDay(); // 0=日 1=一...
  startDow = startDow === 0 ? 6 : startDow - 1; // 转为周一=0

  $('#cal-month-label').textContent = `${year}年 ${month + 1}月`;
  const grid = $('#cal-grid');
  grid.innerHTML = '';

  // 上月填充
  const prevLast = new Date(year, month, 0).getDate();
  for (let i = startDow - 1; i >= 0; i--) {
    const day = prevLast - i;
    const cell = createCalDay(day, 'other-month');
    grid.appendChild(cell);
  }

  // 本月日期
  // 收集有笔记的日期
  const noteDates = {};
  state.notes.forEach(note => {
    const d = (note.created_at || '').substring(0, 10);
    noteDates[d] = (noteDates[d] || 0) + 1;
  });

  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const cell = createCalDay(d, '', dateStr);
    // 今天
    const cellDate = new Date(year, month, d);
    if (cellDate.getTime() === today.getTime()) {
      cell.classList.add('today');
    }
    // 有笔记
    if (noteDates[dateStr]) {
      cell.classList.add('has-note');
      if (noteDates[dateStr] > 1) {
        const badge = document.createElement('span');
        badge.className = 'cal-day-note-count';
        badge.textContent = noteDates[dateStr];
        cell.appendChild(badge);
      }
    }
    grid.appendChild(cell);
  }

  // 下月填充
  const totalCells = startDow + daysInMonth;
  const remaining = totalCells % 7 === 0 ? 0 : 7 - (totalCells % 7);
  for (let d = 1; d <= remaining; d++) {
    const cell = createCalDay(d, 'other-month');
    grid.appendChild(cell);
  }
}

function createCalDay(dayNum, extraClass, dateStr) {
  const cell = document.createElement('button');
  cell.className = `cal-day${extraClass ? ' ' + extraClass : ''}`;
  cell.textContent = dayNum;
  if (dateStr) {
    cell.addEventListener('click', () => onCalendarDateClick(dateStr));
  }
  return cell;
}

async function onCalendarDateClick(dateStr) {
  closePanel($('#calendar-panel'));
  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`;
  // 今天走「每日笔记」（套「日记」模板 + 落「日记」笔记本 + 幂等），其它日期保持原样：
  // 点过去的日期只会打开/新建"那天创建的笔记"，不会凭空按今天的模板生成一篇内容
  if (dateStr === todayStr) {
    try {
      const note = await window.pywebview.api.daily_note_open();
      if (note && note.id) {
        await loadNotes();
        await selectNote(note.id);
        return;
      }
    } catch (e) {
      showToast('打开今日日记失败：' + (e && e.message ? e.message : e), { type: 'error' });
      return;
    }
  }
  // 查找当天创建的笔记
  const dayNotes = state.notes.filter(n => (n.created_at||'').startsWith(dateStr));
  if (dayNotes.length > 0) {
    // 跳转到第一篇
    await verifyAndSelectNote(dayNotes[0].id);
  } else {
    // 自动创建笔记（使用默认标题）
    await saveCurrentNote();
    const note = await window.pywebview.api.notes_create();
    if (note) {
      notesStore.unshift(note);
      renderNoteList();
      updateNotebookCount();
      await selectNote(note.id);
      dom.titleInput.focus();
    }
  }
}

$('#btn-calendar').addEventListener('click', () => {
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();
  buildCalendar(calYear, calMonth);
  openPanel($('#calendar-panel'));
});

$('#cal-prev-month').addEventListener('click', () => {
  if (calMonth === 0) { calYear--; calMonth = 11; }
  else calMonth--;
  buildCalendar(calYear, calMonth);
});

$('#cal-next-month').addEventListener('click', () => {
  if (calMonth === 11) { calYear++; calMonth = 0; }
  else calMonth++;
  buildCalendar(calYear, calMonth);
});

$('#cal-today').addEventListener('click', () => {
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();
  buildCalendar(calYear, calMonth);
});

// 装饰分割线 Blot 已在 js/quill/quill-deco.js 中注册

$('#btn-divider').addEventListener('click', () => {
  if (!state.activeNoteId) { showToast('请先选择一篇笔记', { type: 'warn' }); return; }
  buildDividerPanel();
  openPanel($('#divider-panel'));
});

// 分割线blot样式
const dividerBlotStyle = document.createElement('style');
dividerBlotStyle.textContent = `
  .ql-editor .divider-blot { margin:18px 0; user-select:none; cursor:default; }
  .ql-editor .divider-blot.div-bookend { padding:0 8px; }
`;
document.head.appendChild(dividerBlotStyle);

// 贴纸印章 Blot 已在 js/quill/quill-deco.js 中注册

$$('.sticker-cat-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('.sticker-cat-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    setStickerCat(btn.dataset.stickerCat);
    buildStickerGrid();
  });
});

$('#btn-sticker').addEventListener('click', () => {
  if (!state.activeNoteId) { showToast('请先选择一篇笔记', { type: 'warn' }); return; }
  setStickerCat('date');
  $$('.sticker-cat-btn').forEach(b => b.classList.toggle('active', b.dataset.stickerCat === 'date'));
  buildStickerGrid();
  openPanel($('#sticker-panel'));
});

// ====== 笔记纸张样式 ======
// paperStyles / paperColors 已定义在 NotepadConfig 中

function buildPaperPanel() {

  const styleGrid = $('#paper-style-grid');
  styleGrid.innerHTML = '';
  const curStyle = state.notes.find(n => n.id === state.activeNoteId)?.paper_style || 'none';
  NotepadConfig.paperStyles.forEach(ps => {
    const btn = document.createElement('button');
    btn.className = 'paper-option' + (ps.id === curStyle ? ' active' : '');
    btn.dataset.style = ps.id;
    if (ps.cls !== 'paper-none') btn.classList.add(ps.cls);
    btn.textContent = ps.name;
    btn.addEventListener('click', () => {
      styleGrid.querySelectorAll('.paper-option').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      applyPaperStyle(ps.id);
      window.pywebview.api.notes_update(state.activeNoteId, { paper_style: ps.id });
      const note = state.notes.find(n => n.id === state.activeNoteId);
      if (note) note.paper_style = ps.id;
    });
    styleGrid.appendChild(btn);
  });
}

function applyPaperStyle(styleId) {
  const ps = NotepadConfig.paperStyles.find(s => s.id === styleId);
  if (!state.quill) return;
  NotepadConfig.paperStyles.forEach(s => {
    if (s.innerCls) state.quill.root.classList.remove(s.innerCls);
  });
  if (ps && ps.innerCls) state.quill.root.classList.add(ps.innerCls);
}

export function loadPaperForNote(note) {
  if (!state.quill || !note) return;
  NotepadConfig.paperStyles.forEach(s => { if (s.innerCls) state.quill.root.classList.remove(s.innerCls); });
  const style = note.paper_style || 'none';
  const ps = NotepadConfig.paperStyles.find(s => s.id === style);
  if (ps && ps.innerCls) state.quill.root.classList.add(ps.innerCls);
}

$('#btn-paper').addEventListener('click', () => {
  if (!state.activeNoteId) { showToast('请先选择一篇笔记', { type: 'warn' }); return; }
  buildPaperPanel();
  openPanel($('#paper-panel'));
});

// ====== 标题风格 ======
// titleStyles / titleStyleNames 已定义在 NotepadConfig 中
let currentTitleStyle = 'none';

function applyTitleStyle(style) {
  currentTitleStyle = style;
  NotepadConfig.titleStyles.forEach(s => dom.titleInput.classList.remove('title-' + s));
  if (style !== 'none') dom.titleInput.classList.add(`title-${style}`);
  $$('.title-style-btn').forEach(b => b.classList.toggle('active', b.dataset.ts === style));
}

// 标题样式按钮事件
$$('.title-style-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    applyTitleStyle(btn.dataset.ts);
  });
});

// ====== 封面系统 ======
// coverColors 已定义在 NotepadConfig 中

export function generateNoteCover(note) {
  const type = note.cover_type || 'none';
  const val = note.cover_value || '';
  if (type === 'image' && val) {
    return '<div class="note-cover"><img class="note-cover-img" src="' + escapeHtml(val) + '" onerror="this.style.display=\'none\';this.parentElement.textContent=\'' + escapeHtml((note.title||'笔')[0]) + '\'"></div>';
  }
  if (type === 'gradient' && val) {
    const colors = val.split(',');
    return '<div class="note-cover" style="background:linear-gradient(135deg,' + (colors[0]||'#7D8A6E') + ',' + (colors[1]||'#D69E2E') + ');">' + escapeHtml((note.title||'笔')[0]) + '</div>';
  }
  if (type === 'color' && val) {
    return '<div class="note-cover" style="background:' + escapeHtml(val) + ';">' + escapeHtml((note.title||'笔')[0]) + '</div>';
  }
  const idx = (note.title || '笔').charCodeAt(0) % NotepadConfig.coverColors.length;
  const color = NotepadConfig.coverColors[idx];
  return `<div class="note-cover" style="background:${color};">${escapeHtml((note.title||'笔')[0])}</div>`;
}

// 封面设置面板
export function buildCoverPanel() {
  const grid = $('#cover-grid');
  if (!grid) return;
  grid.innerHTML = '';
  const note = state.notes.find(n => n.id === state.activeNoteId);
  const curType = note?.cover_type || 'none';
  const curVal = note?.cover_value || '';

  // 默认自动色选项
  const noneBtn = document.createElement('button');
  noneBtn.className = 'cover-option' + (curType === 'none' ? ' active' : '');
  noneBtn.style.background = 'var(--bg-secondary)'; noneBtn.style.color = 'var(--text-muted)';
  noneBtn.textContent = 'A';
  noneBtn.title = '自动配色';
  noneBtn.addEventListener('click', () => setNoteCover('none', ''));
  grid.appendChild(noneBtn);

  // 纯色选项
  NotepadConfig.coverColors.forEach(c => {
    const btn = document.createElement('button');
    btn.className = 'cover-option' + (curType === 'color' && curVal === c ? ' active' : '');
    btn.style.background = c;
    btn.textContent = 'A';
    btn.addEventListener('click', () => setNoteCover('color', c));
    grid.appendChild(btn);
  });

  // 渐变色
  const gradPairs = [['#7D8A6E','#D69E2E'],['#3182CE','#805AD5'],['#E53E3E','#DD6B20'],['#38A169','#2B6CB0']];
  gradPairs.forEach(g => {
    const btn = document.createElement('button');
    btn.className = 'cover-option' + (curType === 'gradient' && curVal === g.join(',') ? ' active' : '');
    btn.style.background = `linear-gradient(135deg,${g[0]},${g[1]})`;
    btn.textContent = 'A';
    btn.addEventListener('click', () => setNoteCover('gradient', g.join(',')));
    grid.appendChild(btn);
  });
}

async function setNoteCover(type, value) {
  if (!state.activeNoteId) return;
  await window.pywebview.api.notes_update(state.activeNoteId, { cover_type: type, cover_value: value });
  const note = state.notes.find(n => n.id === state.activeNoteId);
  if (note) { note.cover_type = type; note.cover_value = value; }
  renderNoteList();
}

