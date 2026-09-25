// ====== 迷你捕获窗（第 10 轮）======
// 一个只有输入框的小窗：Ctrl+Alt+S 弹出来，敲完 Enter，内容落进「收件箱」，窗口消失。
// 刻意**不把主窗叫到前台**：捕获是"记一下就走"，抢焦点等于打断用户正在做的事。
//
// 键盘约定（写在这里以免以后被"顺手统一"改掉）：
//   Enter        → 保存并关窗
//   Ctrl+Enter   → 保存但**留着**（连着记好几条）
//   Shift+Enter  → 换行（不拦截）
//   Esc          → 直接关掉，不保存

import { ICONS } from '../shared/icons.js';

const input = document.getElementById('mini-input');
const saveBtn = document.getElementById('mini-save');
const statusEl = document.getElementById('mini-status');
const closeBtn = document.getElementById('mini-close');

let busy = false;

function setStatus(text, ok) {
  if (!statusEl) return;
  statusEl.textContent = text || '';
  statusEl.classList.toggle('ok', !!ok);
}

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

async function close() {
  try {
    await window.pywebview.api.capture_mini_close();
  } catch (e) { /* 窗口关不掉也只能作罢，Python 侧还有收尾 */ }
}

async function submit(keepOpen) {
  if (busy) return;
  const text = input ? input.value : '';
  if (!text.trim()) {              // 空内容：Enter 等同于"算了"
    if (!keepOpen) close();
    else setStatus('还没写内容');
    return;
  }
  busy = true;
  if (saveBtn) saveBtn.disabled = true;
  setStatus('保存中…');
  let res = null;
  try {
    res = await window.pywebview.api.capture_mini_submit(text, !!keepOpen);
  } catch (e) {
    res = null;
  }
  busy = false;
  if (saveBtn) saveBtn.disabled = false;
  if (!res || !res.ok) {
    // 保存失败时**不关窗**：内容还在输入框里，用户可以重试（否则写的东西就丢了）
    setStatus('保存失败，内容还在，可重试', false);
    return;
  }
  if (keepOpen) {
    input.value = '';
    setStatus('已收进「收件箱」', true);
    input.focus();
  }
  // 不 keepOpen 时窗口已被 Python 关掉，这里不会再执行
}

function init() {
  if (closeBtn) closeBtn.innerHTML = ICONS.close;
  if (saveBtn) saveBtn.innerHTML = ICONS.capture + '<span>收进收件箱</span>';
  input?.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { ev.preventDefault(); close(); return; }
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      submit(ev.ctrlKey || ev.metaKey);
    }
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') { ev.preventDefault(); close(); }
  });
  saveBtn?.addEventListener('click', () => submit(false));
  closeBtn?.addEventListener('click', () => close());
  // 让 Python 那边能重新聚焦输入框（热键再次按下时窗口已存在）
  window.__mini = { focusInput() { input?.focus(); input?.select(); } };
  input?.focus();
}

whenBridgeReady().then(init);
