// ====== 截图选区覆盖窗（第 10 轮）======
// 这个页面跑在**独立窗口**里，铺满整个虚拟屏幕，显示的是一张冻屏图（Python 抓的整屏 PNG）。
// 为什么不是半透明蒙层：WebView2 的 `transparent=True` 实测无效，蒙层下面是自己的底色而不是
// 桌面。冻屏图这条路还有个额外好处——用户看到的像素与最终裁出来的**完全一致**。
//
// 坐标换算全在 Python 侧（`desktop.map_selection_to_image`，纯函数、有单测）：
// 这里只上报「选区 CSS px + 视口尺寸」，因为 JS 拿不到自己的客户区在屏幕上的物理位置。
// 图片位置则按「图片物理原点 vs 客户区物理原点」摆好，所以光标底下的像素就是它看起来的那个。

import { ICONS } from '../shared/icons.js';

const $ = (sel) => document.querySelector(sel);

const shot = $('#shot');
const sel = $('#sel');
const sizeTag = $('#sel-size');
const bar = $('#bar');
const barMsg = $('#bar-msg');
const hint = $('#hint');
const mag = $('#mag');
const magCanvas = $('#mag-canvas');
const magHex = $('#mag-hex');
const magCtx = magCanvas.getContext('2d', { willReadFrequently: true });

const BUTTONS = {
  insert: { icon: ICONS.insert, text: '插入当前笔记', primary: true },
  note: { icon: ICONS.plus, text: '存为新笔记' },
  clipboard: { icon: ICONS.copy, text: '复制' },
  cancel: { icon: ICONS.close, text: '取消' },
};

const MAG_SRC = 17;      // 放大镜取样边长（图片物理像素）
const MAG_ZOOM = 8;      // 每个物理像素放大成 8×8
const MIN_PICK = 4;      // 小于这个尺寸（CSS px）当成误点，不算选区

let info = null;         // Python 给的：{image, client, origin, size, note_id}
let bitmap = null;       // 冻屏图原尺寸**画布**（drawImage 的合法源）
let bitmapCtx = null;    // 它的 2D 上下文（放大镜取色用）
let sx = 1;              // CSS px → 图片物理 px
let sy = 1;
let rect = null;         // 当前选区（CSS px）
let drag = null;
let busy = false;

function whenBridgeReady() {
  if (window.pywebview && window.pywebview.api) return Promise.resolve();
  return new Promise((resolve) => {
    window.addEventListener('pywebviewready', () => resolve(), { once: true });
    let n = 0;
    const timer = setInterval(() => {
      if (window.pywebview && window.pywebview.api) { clearInterval(timer); resolve(); }
      else if (++n > 200) { clearInterval(timer); resolve(); }
    }, 50);
  });
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function inside(cx, cy) {
  return !!rect && cx >= rect.x && cx <= rect.x + rect.w && cy >= rect.y && cy <= rect.y + rect.h;
}

// ----- 渲染 -----

function renderSel() {
  if (!rect) {
    sel.classList.add('hidden');
    return;
  }
  sel.classList.remove('hidden');
  sel.style.left = rect.x + 'px';
  sel.style.top = rect.y + 'px';
  sel.style.width = rect.w + 'px';
  sel.style.height = rect.h + 'px';
  if (sizeTag) {
    // 显示**物理**像素：这才是最终存下来的图片尺寸（本机 200% 缩放下是界面的 2 倍）
    sizeTag.textContent = Math.round(rect.w * sx) + ' × ' + Math.round(rect.h * sy);
    sizeTag.classList.toggle('inside', rect.y < 26);
  }
}

function hideBar() {
  bar.classList.add('hidden');
  showMsg('');
}

function showMsg(text) {
  if (!barMsg) return;
  barMsg.textContent = text || '';
  barMsg.classList.toggle('hidden', !text);
}

function placeBar() {
  if (!rect) { hideBar(); return; }
  bar.classList.remove('hidden');
  const bw = bar.offsetWidth;
  const bh = bar.offsetHeight;
  const below = rect.y + rect.h + 10;
  const above = rect.y - bh - 10;
  const top = (below + bh <= window.innerHeight - 6) ? below : Math.max(6, above);
  const left = clamp(rect.x + rect.w / 2 - bw / 2, 6, Math.max(6, window.innerWidth - bw - 6));
  bar.style.left = left + 'px';
  bar.style.top = top + 'px';
}

function placeMag(cx, cy) {
  const mw = mag.offsetWidth || 144;
  const mh = mag.offsetHeight || 164;
  let x = cx + 20;
  let y = cy + 20;
  if (x + mw > window.innerWidth - 6) x = cx - mw - 20;
  if (y + mh > window.innerHeight - 6) y = cy - mh - 20;
  mag.style.left = Math.max(6, x) + 'px';
  mag.style.top = Math.max(6, y) + 'px';
}

function drawMag(cx, cy) {
  if (!bitmap || !bitmapCtx) return;
  const ix = Math.round(cx * sx + (info.client[0] - info.origin[0]));
  const iy = Math.round(cy * sy + (info.client[1] - info.origin[1]));
  const half = Math.floor(MAG_SRC / 2);
  magCtx.imageSmoothingEnabled = false;
  magCtx.clearRect(0, 0, magCanvas.width, magCanvas.height);
  magCtx.fillStyle = '#000';
  magCtx.fillRect(0, 0, magCanvas.width, magCanvas.height);
  // bitmap 是**画布**（不是它的 2D 上下文——那玩意不是 drawImage 的合法源，实测抛 TypeError）
  magCtx.drawImage(bitmap, ix - half, iy - half, MAG_SRC, MAG_SRC,
    0, 0, magCanvas.width, magCanvas.height);
  const mid = magCanvas.width / 2;
  magCtx.strokeStyle = 'rgba(255,255,255,0.9)';
  magCtx.lineWidth = 1;
  magCtx.strokeRect(mid - MAG_ZOOM / 2 + 0.5, mid - MAG_ZOOM / 2 + 0.5, MAG_ZOOM - 1, MAG_ZOOM - 1);
  try {
    const px = bitmapCtx.getImageData(clamp(ix, 0, bitmap.width - 1),
      clamp(iy, 0, bitmap.height - 1), 1, 1).data;
    magHex.textContent = '#' + [px[0], px[1], px[2]]
      .map((v) => v.toString(16).padStart(2, '0')).join('').toUpperCase();
  } catch (e) {
    magHex.textContent = '';
  }
  mag.classList.remove('hidden');
  placeMag(cx, cy);
}

// ----- 选区几何 -----

function normalize(x0, y0, x1, y1) {
  const x = Math.min(x0, x1);
  const y = Math.min(y0, y1);
  return {
    x: clamp(x, 0, window.innerWidth),
    y: clamp(y, 0, window.innerHeight),
    w: clamp(Math.abs(x1 - x0), 0, window.innerWidth),
    h: clamp(Math.abs(y1 - y0), 0, window.innerHeight),
  };
}

function updateDrag(ev) {
  const px = ev.clientX;
  const py = ev.clientY;
  if (drag.mode === 'new') {
    rect = normalize(drag.ox, drag.oy, px, py);
  } else if (drag.mode === 'move') {
    const dx = px - drag.px;
    const dy = py - drag.py;
    rect = {
      x: clamp(drag.start.x + dx, 0, window.innerWidth - drag.start.w),
      y: clamp(drag.start.y + dy, 0, window.innerHeight - drag.start.h),
      w: drag.start.w,
      h: drag.start.h,
    };
  } else if (drag.mode === 'resize') {
    const s = drag.start;
    const right = s.x + s.w;
    const bottom = s.y + s.h;
    let x0 = s.x; let y0 = s.y; let x1 = right; let y1 = bottom;
    if (drag.h.indexOf('w') >= 0) x0 = px;
    if (drag.h.indexOf('e') >= 0) x1 = px;
    if (drag.h.indexOf('n') >= 0) y0 = py;
    if (drag.h.indexOf('s') >= 0) y1 = py;
    rect = normalize(x0, y0, x1, y1);
  }
  renderSel();
}

// ----- 提交 -----

async function commit(action) {
  if (busy) return;
  busy = true;
  showMsg('');
  const payload = (rect && rect.w >= 1 && rect.h >= 1)
    ? [Math.round(rect.x), Math.round(rect.y), Math.round(rect.w), Math.round(rect.h)]
    : null;
  let res = null;
  try {
    res = await window.pywebview.api.capture_commit(
      action, payload, [window.innerWidth, window.innerHeight]);
  } catch (e) {
    res = { ok: false, error: String(e && e.message ? e.message : e) };
  }
  busy = false;
  if (!res || !res.ok) {
    // Python 在失败时**不**收窗（否则用户看不到任何反馈），原因写进工具条
    showMsg((res && res.error) || '操作失败，可重试或按 Esc 取消');
    return;
  }
  // 成功时窗口已被 Python 销毁，这里不会再往下执行
}

function defaultAction() {
  return (info && info.note_id && document.querySelector('[data-act="insert"]')) ? 'insert' : 'note';
}

// ----- 事件 -----

function bindEvents() {
  document.addEventListener('pointerdown', (ev) => {
    if (busy || ev.button !== 0) return;
    const handle = ev.target.closest && ev.target.closest('.ov-handle');
    if (handle && rect) {
      drag = { mode: 'resize', h: handle.getAttribute('data-h'), start: { ...rect } };
      hideBar();
      return;
    }
    if (inside(ev.clientX, ev.clientY)) {
      drag = { mode: 'move', start: { ...rect }, px: ev.clientX, py: ev.clientY };
      hideBar();
      return;
    }
    rect = { x: ev.clientX, y: ev.clientY, w: 0, h: 0 };
    drag = { mode: 'new', ox: ev.clientX, oy: ev.clientY };
    hideBar();
    renderSel();
  });

  document.addEventListener('pointermove', (ev) => {
    if (drag) updateDrag(ev);
    drawMag(ev.clientX, ev.clientY);
  });

  document.addEventListener('pointerup', () => {
    if (!drag) return;
    drag = null;
    mag.classList.add('hidden');
    if (rect && (rect.w < MIN_PICK || rect.h < MIN_PICK)) {
      rect = null;                 // 误点：清掉，别留下一个 2×3 像素的"选区"
      renderSel();
      hint.classList.remove('dim');
      hideBar();
      return;
    }
    hint.classList.add('dim');
    renderSel();
    placeBar();
  });

  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { ev.preventDefault(); commit('cancel'); }
    else if (ev.key === 'Enter') { ev.preventDefault(); commit(rect ? defaultAction() : 'cancel'); }
  });

  document.querySelectorAll('.ov-btn').forEach((btn) => {
    btn.addEventListener('click', () => commit(btn.getAttribute('data-act') || 'cancel'));
  });
}

async function boot() {
  // 按钮的图标与文字都从这里出：图标库是唯一来源（UI 里不许出现 emoji 当图标）
  document.querySelectorAll('.ov-btn').forEach((btn) => {
    const cfg = BUTTONS[btn.getAttribute('data-act')];
    if (!cfg) return;
    btn.innerHTML = cfg.icon + '<span>' + cfg.text + '</span>';
    if (cfg.primary) btn.classList.add('primary');
  });
  await whenBridgeReady();
  try {
    // 视口尺寸**由页面报上去**，不让 Python 反过来 evaluate_js 来问：
    // 桥接调用返回前 JS 正在等结果，此时 Python 再回调 JS 会互相等死（实测卡死）。
    info = await window.pywebview.api.capture_overlay_info(window.innerWidth, window.innerHeight);
  } catch (e) {
    info = null;
  }
  if (!info || !info.image) { commit('cancel'); return; }

  const client = info.client;
  sx = client[2] / window.innerWidth;
  sy = client[3] / window.innerHeight;
  // 按物理坐标把图片摆到它真实的位置上（正常情况下就是铺满窗口）
  shot.style.left = ((info.origin[0] - client[0]) / sx) + 'px';
  shot.style.top = ((info.origin[1] - client[1]) / sy) + 'px';
  shot.style.width = (info.size[0] / sx) + 'px';
  shot.style.height = (info.size[1] / sy) + 'px';

  await new Promise((resolve) => {
    shot.onload = resolve;
    shot.onerror = resolve;
    shot.src = info.image;
    if (shot.complete && shot.naturalWidth) resolve();
  });

  // 原尺寸画布：放大镜要逐像素取样，DOM 里的 <img> 读不出像素
  try {
    const c = document.createElement('canvas');
    c.width = info.size[0];
    c.height = info.size[1];
    const cx = c.getContext('2d', { willReadFrequently: true });
    cx.drawImage(shot, 0, 0);
    bitmap = c;
    bitmapCtx = cx;
  } catch (e) {
    bitmap = null;               // 取样失败只是没有放大镜，不影响框选
    bitmapCtx = null;
  }

  // 冻屏图已经画好了 → 现在才让 Python 把窗口显示出来（先显示会白闪一下）
  const insertBtn = document.querySelector('[data-act="insert"]');
  if (insertBtn && !info.note_id) {
    insertBtn.disabled = true;
    insertBtn.title = '当前没有正在编辑的笔记';
  }
  bindEvents();
  try {
    await window.pywebview.api.capture_overlay_ready();
  } catch (e) { /* 显示失败时窗口本来也不会出现，交给 Python 侧超时 */ }
}

boot();
