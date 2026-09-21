// ====== 主题管理 ======

// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { $, $$, NotepadConfig, closePanel, dom, openPanel, showToast, state } from './01-core.js';
import { syncNoteFields } from './02-editor.js';
import { renderNoteList } from './03-notes.js';
import { analyzeImageColor, applyAdaptiveUI, buildCoverPanel, clearAdaptiveUI } from './08-appearance2.js';
import { debounce } from '../shared/utils.js';

export async function loadSettings(retryCount = 0) {
  try {
    if (!window.pywebview || !window.pywebview.api) {
      if (retryCount < 10) { await new Promise(r => setTimeout(r, 500)); return loadSettings(retryCount + 1); }
      return;
    }
    const settings = await window.pywebview.api.settings_get_all();
    state.currentTheme = settings.theme || 'white';
    state.globalBg.type = settings.bg_type || 'color';
    state.globalBg.value = settings.bg_value || '';
    state.globalBg.opacity = parseFloat(settings.bg_opacity) || 1.0;
    state.globalBg.zoom = parseInt(settings.bg_zoom) || 100;
    try { const pos = JSON.parse(settings.bg_pos || '{"x":50,"y":50}'); state.globalBg.posX = pos.x; state.globalBg.posY = pos.y; globalBgPos = pos; } catch(e) {}

    applyTheme(state.currentTheme);
    applyGlobalBackground();
    // 恢复之前保存的自适应配色
    if (state.globalBg.type === 'image' && state.globalBg.value) {
      const saved = await window.pywebview.api.settings_get('adaptive_color');
      if (saved) {
        try { applyAdaptiveUI(JSON.parse(saved)); } catch(e) {}
      } else {
        // 重新分析
        try {
          const dataUri = await window.pywebview.api.read_file_base64(state.globalBg.value);
          if (dataUri) analyzeImageColor(dataUri, (info) => applyAdaptiveUI(info));
        } catch(e) {}
      }
    }
  } catch (err) {
    console.error('加载设置失败:', err);
  }
}

// 「跟随系统」：把 'system' 解析成 dark / white。只在用户明确选了「跟随系统」时才解析，
// 手动选主题时始终以用户选择为准（不去猜系统偏好）。
const _systemDark = (typeof window.matchMedia === 'function')
  ? window.matchMedia('(prefers-color-scheme: dark)') : null;

export function resolveTheme(themeName) {
  if (themeName !== 'system') return themeName;
  return (_systemDark && _systemDark.matches) ? 'dark' : 'white';
}

function applyTheme(themeName) {
  state.currentTheme = themeName;                       // 保存用户的选择（可能就是 'system'）
  const resolved = resolveTheme(themeName);             // 落到 DOM 的永远是具体主题
  dom.theme.setAttribute('data-theme', resolved);
  if (window.pywebview && window.pywebview.api) {
    window.pywebview.api.settings_set('theme', themeName);
  }

  // 切换封面颜色以匹配主题
  var coverColor = NotepadConfig._themeCoverColors[resolved];
  if (coverColor) {
    NotepadConfig.coverColors = [coverColor];
  }

  // 更新主题面板的选中状态（按用户选择高亮，而不是解析结果）
  $$('.theme-option').forEach(opt => {
    opt.classList.toggle('active', opt.dataset.theme === themeName);
  });

  // 如果全局背景是主题色，随主题变化
  if (state.globalBg.type === 'color') {
    applyGlobalBackground();

  }
}

// 系统深浅色切换时，若正处在「跟随系统」就立刻跟着变（Windows 设置里改主题无需重启应用）
if (_systemDark && typeof _systemDark.addEventListener === 'function') {
  _systemDark.addEventListener('change', () => {
    if (state.currentTheme === 'system') applyTheme('system');
  });
}

async function applyGlobalBackground() {
  if (state.globalBg.type === 'color' || !state.globalBg.value) {
    dom.globalBgLayer.style.backgroundImage = '';
    dom.globalBgLayer.style.opacity = '';
    clearAdaptiveUI();
  } else if (state.globalBg.type === 'image' && state.globalBg.value) {
    const imgPath = state.globalBg.value;
    const zoom = state.globalBg.zoom || 100;
    const posX = state.globalBg.posX ?? 50;
    const posY = state.globalBg.posY ?? 50;
    // 优先使用缓存的 dataUri，其次尝试 read_file_base64，最后回退到文件路径
    var dataUri = state._globalBgDataUri || null;
    if (!dataUri) {
      try {
        dataUri = await window.pywebview.api.read_file_base64(imgPath);
      } catch (e) { /* ignore */ }
    }
    if (dataUri) {
      dom.globalBgLayer.style.backgroundImage = 'url(' + dataUri + ')';
      dom.globalBgLayer.style.opacity = state.globalBg.opacity;
      dom.globalBgLayer.style.backgroundSize = zoom + '% auto';
      dom.globalBgLayer.style.backgroundPosition = posX + '% ' + posY + '%';
      analyzeImageColor(dataUri, function(info) { applyAdaptiveUI(info); });
    } else {
      const fileUrl = 'file:///' + imgPath.replace(/\\/g, '/');
      dom.globalBgLayer.style.backgroundImage = 'url(' + fileUrl + ')';
      dom.globalBgLayer.style.opacity = state.globalBg.opacity;
      dom.globalBgLayer.style.backgroundSize = zoom + '% auto';
      dom.globalBgLayer.style.backgroundPosition = posX + '% ' + posY + '%';
      analyzeImageColor(fileUrl, function(info) { applyAdaptiveUI(info); });
    }
  }
}

export async function applyNoteBackground(note) {
  if (!note) return;

  const bgType = note.bg_type || 'global';

  if (bgType === 'global') {
    // 跟随全局 → 清空笔记层，恢复全局层显示
    dom.noteBgLayer.style.backgroundImage = '';
    dom.noteBgLayer.style.opacity = '';
    state.noteBgImagePath = null;
    applyGlobalBackground();
  } else if (bgType === 'color') {
    // 使用主题色 → 隐藏两层，只显示纯色
    dom.globalBgLayer.style.backgroundImage = '';
    dom.globalBgLayer.style.opacity = '';
    dom.noteBgLayer.style.backgroundImage = '';
    dom.noteBgLayer.style.opacity = '';
    state.noteBgImagePath = null;
    clearAdaptiveUI();
  } else if (bgType === 'image' && note.bg_value) {
    // 自定义图片 → 隐藏全局层，只显示笔记自己的背景图
    dom.globalBgLayer.style.backgroundImage = '';
    dom.globalBgLayer.style.opacity = '';

    const imgPath = note.bg_value;
    const zoom = note.bg_zoom || 100;
    const posX = note.bg_pos_x ?? 50;
    const posY = note.bg_pos_y ?? 50;
    var dataUri = state._noteBgDataUri || null;
    if (!dataUri) {
      try {
        dataUri = await window.pywebview.api.read_file_base64(imgPath);
      } catch (e) { /* ignore */ }
    }
    if (dataUri) {
      dom.noteBgLayer.style.backgroundImage = 'url(' + dataUri + ')';
      dom.noteBgLayer.style.opacity = note.bg_opacity || 0.7;
      dom.noteBgLayer.style.backgroundSize = zoom + '% auto';
      dom.noteBgLayer.style.backgroundPosition = posX + '% ' + posY + '%';
      state.noteBgImagePath = note.bg_value;
      analyzeImageColor(dataUri, function(info) { applyAdaptiveUI(info); });
    } else {
      const fileUrl = 'file:///' + imgPath.replace(/\\/g, '/');
      dom.noteBgLayer.style.backgroundImage = 'url(' + fileUrl + ')';
      dom.noteBgLayer.style.opacity = note.bg_opacity || 0.7;
      dom.noteBgLayer.style.backgroundSize = zoom + '% auto';
      dom.noteBgLayer.style.backgroundPosition = posX + '% ' + posY + '%';
      state.noteBgImagePath = note.bg_value;
      analyzeImageColor(fileUrl, function(info) { applyAdaptiveUI(info); });
    }
  }
}

// 主题面板事件
$$('.theme-option').forEach(opt => {
  opt.addEventListener('click', () => {
    applyTheme(opt.dataset.theme);
    renderNoteList();  // 刷新封面颜色
    closePanel(dom.themePanel);
  });
});

dom.btnTheme.addEventListener('click', () => {
  updateThemePanelUI();
  openPanel(dom.themePanel);
});

function updateThemePanelUI() {
  $$('.theme-option').forEach(opt => {
    opt.classList.toggle('active', opt.dataset.theme === state.currentTheme);
  });
}

// ====== 背景设置面板 ======

let bgPanelTab = 'global-bg';

// 标签切换
$$('.panel-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('.panel-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    bgPanelTab = tab.dataset.tab;
    $$('.tab-content').forEach(c => c.style.display = 'none');
    $(`#tab-${bgPanelTab}`).style.display = 'block';
    if (bgPanelTab === 'note-cover') buildCoverPanel();
    updateBackgroundPanelUI();
  });
});

// 全局背景类型切换
$$('input[name="global-bg-type"]').forEach(radio => {
  radio.addEventListener('change', () => {
    const imageSettings = $('#tab-global-bg .bg-image-settings');
    if (radio.value === 'image') {
      imageSettings.style.display = 'flex';
    } else {
      imageSettings.style.display = 'none';
      // 切换到主题色
      state.globalBg.type = 'color';
      state.globalBg.value = '';
      window.pywebview.api.settings_set('bg_type', 'color');
      window.pywebview.api.settings_set('bg_value', '');
      applyGlobalBackground();
  
    }
  });
});

// 笔记背景类型切换
$$('input[name="note-bg-type"]').forEach(radio => {
  radio.addEventListener('change', async () => {
    const imageSettings = $('#tab-note-bg .bg-image-settings');
    if (radio.value === 'image') {
      imageSettings.style.display = 'flex';
      // 切回自定义图片时，恢复之前的 bg_value
      if (state.activeNoteId) {
        await window.pywebview.api.notes_update(state.activeNoteId, {
          bg_type: 'image', bg_opacity: 1.0
        });
        syncNoteFields(state.activeNoteId, { bg_type: 'image', bg_opacity: 1.0 });
        const note = await window.pywebview.api.notes_get(state.activeNoteId);
        applyNoteBackground(note);
      }
    } else {
      imageSettings.style.display = 'none';
      if (state.activeNoteId) {
        await window.pywebview.api.notes_update(state.activeNoteId, {
          bg_type: radio.value, bg_opacity: 1.0
        });
        syncNoteFields(state.activeNoteId, { bg_type: radio.value, bg_opacity: 1.0 });
        const note = await window.pywebview.api.notes_get(state.activeNoteId);
        applyNoteBackground(note);
      }
    }
  });
});

// 选择全局背景图片
$('#btn-pick-global-bg').addEventListener('click', async () => {
  let result;
  try {
    result = await window.pywebview.api.pick_background();
  } catch (err) {
    showToast('选择背景图片失败：' + (err && err.message ? err.message : err), { type: 'error' });
    return;
  }
  if (!result) return;

  const filePath = result.path;
  state.globalBg.type = 'image';
  state.globalBg.value = filePath;
  state._globalBgDataUri = result.dataUri;  // 缓存 base64
  window.pywebview.api.settings_set('bg_type', 'image');
  window.pywebview.api.settings_set('bg_value', filePath);
  $('#global-bg-filename').textContent = filePath.split(/[/\\]/).pop();
  applyGlobalBackground();

});

// 选择笔记背景图片
$('#btn-pick-note-bg').addEventListener('click', async () => {
  if (!state.activeNoteId) {
    showToast('请先选择一篇笔记', { type: 'warn' });
    return;
  }

  let result;
  try {
    result = await window.pywebview.api.pick_background();
  } catch (err) {
    showToast('选择背景图片失败：' + (err && err.message ? err.message : err), { type: 'error' });
    return;
  }
  if (!result) return;

  const filePath = result.path;
  const opacity = parseInt($('#note-bg-opacity').value) / 100;
  await window.pywebview.api.notes_update(state.activeNoteId, {
    bg_type: 'image', bg_value: filePath, bg_opacity: opacity
  });
  syncNoteFields(state.activeNoteId, { bg_type: 'image', bg_value: filePath, bg_opacity: opacity });
  state._noteBgDataUri = result.dataUri;  // 缓存 base64
  $('#note-bg-filename').textContent = filePath.split(/[/\\]/).pop();

  const note = await window.pywebview.api.notes_get(state.activeNoteId);
  applyNoteBackground(note);
});

// 全局背景透明度滑块
$('#global-bg-opacity').addEventListener('input', () => {
  const val = parseInt($('#global-bg-opacity').value);
  $('#global-bg-opacity-val').textContent = val + '%';
  state.globalBg.opacity = val / 100;
  window.pywebview.api.settings_set('bg_opacity', String(val / 100));
  applyGlobalBackground();

});

// 全局背景缩放滑块
$('#global-bg-zoom').addEventListener('input', () => {
  const val = parseInt($('#global-bg-zoom').value);
  $('#global-bg-zoom-val').textContent = val + '%';
  state.globalBg.zoom = val;
  window.pywebview.api.settings_set('bg_zoom', String(val));
  applyGlobalBackground();

});

// 全局背景位置按钮
let globalBgPos = { x: 50, y: 50 };
['up','down','left','right'].forEach(dir => {
  $(`#bg-pos-${dir}`).addEventListener('click', () => {
    const step = 5;
    if (dir === 'up') globalBgPos.y = Math.max(0, globalBgPos.y - step);
    if (dir === 'down') globalBgPos.y = Math.min(100, globalBgPos.y + step);
    if (dir === 'left') globalBgPos.x = Math.max(0, globalBgPos.x - step);
    if (dir === 'right') globalBgPos.x = Math.min(100, globalBgPos.x + step);
    state.globalBg.posX = globalBgPos.x;
    state.globalBg.posY = globalBgPos.y;
    window.pywebview.api.settings_set('bg_pos', JSON.stringify(globalBgPos));
    applyGlobalBackground();

  });
});
$('#bg-pos-reset').addEventListener('click', () => {
  globalBgPos = { x: 50, y: 50 };
  state.globalBg.posX = 50; state.globalBg.posY = 50;
  window.pywebview.api.settings_set('bg_pos', JSON.stringify(globalBgPos));
  applyGlobalBackground();

});

// 笔记背景透明度滑块
$('#note-bg-opacity').addEventListener('input', async () => {
  const val = parseInt($('#note-bg-opacity').value);
  $('#note-bg-opacity-val').textContent = val + '%';
  if (state.activeNoteId) {
    await window.pywebview.api.notes_update(state.activeNoteId, { bg_opacity: val / 100 });
    syncNoteFields(state.activeNoteId, { bg_opacity: val / 100 });
    const note = await window.pywebview.api.notes_get(state.activeNoteId);
    applyNoteBackground(note);
  }
});

// 笔记背景缩放
let noteBgZoom = 100;
$('#note-bg-zoom').addEventListener('input', async () => {
  noteBgZoom = parseInt($('#note-bg-zoom').value);
  $('#note-bg-zoom-val').textContent = noteBgZoom + '%';
  if (state.activeNoteId) {
    await window.pywebview.api.notes_update(state.activeNoteId, { bg_zoom: noteBgZoom });
    syncNoteFields(state.activeNoteId, { bg_zoom: noteBgZoom });
    const note = await window.pywebview.api.notes_get(state.activeNoteId);
    applyNoteBackground(note);
  }
});

// 笔记背景位置
let noteBgPos = { x: 50, y: 50 };
$$('.note-pos-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const dir = btn.dataset.dir;
    if (dir === 'up') noteBgPos.y = Math.max(0, noteBgPos.y - 5);
    if (dir === 'down') noteBgPos.y = Math.min(100, noteBgPos.y + 5);
    if (dir === 'left') noteBgPos.x = Math.max(0, noteBgPos.x - 5);
    if (dir === 'right') noteBgPos.x = Math.min(100, noteBgPos.x + 5);
    if (state.activeNoteId) {
      await window.pywebview.api.notes_update(state.activeNoteId, { bg_pos_x: noteBgPos.x, bg_pos_y: noteBgPos.y });
      syncNoteFields(state.activeNoteId, { bg_pos_x: noteBgPos.x, bg_pos_y: noteBgPos.y });
      const note = await window.pywebview.api.notes_get(state.activeNoteId);
      applyNoteBackground(note);
    }
  });
});
$('#note-pos-reset').addEventListener('click', async () => {
  noteBgPos = { x: 50, y: 50 };
  if (state.activeNoteId) {
    await window.pywebview.api.notes_update(state.activeNoteId, { bg_pos_x: 50, bg_pos_y: 50 });
    syncNoteFields(state.activeNoteId, { bg_pos_x: 50, bg_pos_y: 50 });
    const note = await window.pywebview.api.notes_get(state.activeNoteId);
    applyNoteBackground(note);
  }
});

// 清除背景按钮
$('#btn-clear-global-bg').addEventListener('click', () => {
  state.globalBg.type = 'color';
  state.globalBg.value = '';
  state.globalBg.opacity = 1.0;
  state._globalBgDataUri = null;
  window.pywebview.api.settings_set('bg_type', 'color');
  window.pywebview.api.settings_set('bg_value', '');
  window.pywebview.api.settings_set('bg_opacity', '1.0');
  $('#global-bg-filename').textContent = '';
  $('#global-bg-opacity').value = 60;
  $('#global-bg-opacity-val').textContent = '60%';
  $('#tab-global-bg .bg-image-settings').style.display = 'none';
  $('input[name="global-bg-type"][value="color"]').checked = true;
  applyGlobalBackground();

});

$('#btn-clear-note-bg').addEventListener('click', async () => {
  if (state.activeNoteId) {
    await window.pywebview.api.notes_update(state.activeNoteId, {
      bg_type: 'global', bg_value: null, bg_opacity: 1.0
    });
    syncNoteFields(state.activeNoteId, { bg_type: 'global', bg_value: null, bg_opacity: 1.0 });
    state._noteBgDataUri = null; // 清除缓存
    const note = await window.pywebview.api.notes_get(state.activeNoteId);
    applyNoteBackground(note);
  }
  $('#note-bg-filename').textContent = '';
  $('#note-bg-opacity').value = 70;
  $('#note-bg-opacity-val').textContent = '70%';
  $('#tab-note-bg .bg-image-settings').style.display = 'none';
  $('input[name="note-bg-type"][value="global"]').checked = true;
});

// ====== 背景面板滚轮调节（透明度 + 缩放 + 图标圆角） ======
['#global-bg-opacity','#global-bg-zoom','#note-bg-opacity','#note-bg-zoom','#icon-radius'].forEach(sel => {
  const el = $(sel);
  if (!el) return;
  el.addEventListener('wheel', (e) => {
    e.preventDefault();
    const step = e.shiftKey ? 15 : 5;  // Shift加速
    const delta = e.deltaY > 0 ? -step : step;
    const val = Math.max(parseInt(el.min), Math.min(parseInt(el.max), parseInt(el.value) + delta));
    el.value = val;
    // 触发 input 事件让已有的处理逻辑生效
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, { passive: false });
});

dom.btnBackground.addEventListener('click', () => {
  updateBackgroundPanelUI();
  openPanel(dom.backgroundPanel);
});

// 更换图标 — 真·预览确认两步走
let _iconTempPath = null;
let _iconChanging = false;
let _iconRadius = 15;  // 圆角半径百分比（Win11 风格默认 15%）

function _showIconMsg(msg, isError) {
  const el = $('#icon-result-msg');
  if (el) {
    el.textContent = msg;
    el.style.color = isError ? 'var(--danger)' : 'var(--accent)';
  }
}

// 圆角滑杆：防抖调后端重渲染预览（捕获 tempPath 防迟到回调覆盖）
const _debouncedRadiusUpdate = debounce(async () => {
  const tp = _iconTempPath;
  if (!tp) return;
  try {
    const r = await window.pywebview.api.update_icon_preview(tp, _iconRadius);
    if (r && r.success && _iconTempPath === tp) {
      $('#icon-preview-img').src = r.preview;
    }
  } catch (e) { /* 预览更新失败不阻断流程 */ }
}, 150);

$('#icon-radius').addEventListener('input', () => {
  _iconRadius = parseInt($('#icon-radius').value, 10);
  $('#icon-radius-val').textContent = _iconRadius + '%';
  _debouncedRadiusUpdate();
});

dom.btnChangeIcon.addEventListener('click', async () => {
  if (_iconChanging) return;
  _iconChanging = true;
  _showIconMsg('', false);
  try {
    const result = await window.pywebview.api.pick_and_preview_icon();
    if (!result) return;
    if (result.success) {
      _iconTempPath = result.tempPath;
      _iconRadius = (typeof result.radiusPct === 'number') ? result.radiusPct : 15;
      $('#icon-radius').value = _iconRadius;
      $('#icon-radius-val').textContent = _iconRadius + '%';
      $('#icon-preview-img').src = result.preview;
      const sizeEl = $('#icon-preview-size');
      if (sizeEl && result.origSize) sizeEl.textContent = '原图：' + result.origSize + ' → 512×512';
      openPanel($('#icon-preview-panel'));
    } else {
      _showIconMsg(result.error || '选择失败', true);
    }
  } catch(e) {
    // 面板这时还没打开，只在面板里写字等于没提示 —— 必须弹 Toast 才看得见
    _showIconMsg('操作失败：' + (e && e.message ? e.message : e), true);
    showToast('更换图标失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
  finally { _iconChanging = false; }
});

$('#btn-icon-confirm').addEventListener('click', async () => {
  if (!_iconTempPath) return;
  _debouncedRadiusUpdate.cancel();
  _showIconMsg('保存中…', false);
  const result = await window.pywebview.api.confirm_icon(_iconTempPath, _iconRadius);
  _iconTempPath = null;
  _showIconMsg(result && result.success ? (result.msg || '已更新') : (result.error || '失败'), !result || !result.success);
  setTimeout(() => closePanel($('#icon-preview-panel')), 1500);
});

$('#btn-icon-cancel').addEventListener('click', async () => {
  _debouncedRadiusUpdate.cancel();
  if (_iconTempPath) {
    await window.pywebview.api.cancel_icon(_iconTempPath);
    _iconTempPath = null;
  }
  closePanel($('#icon-preview-panel'));
});

$('#btn-icon-restore').addEventListener('click', async () => {
  _debouncedRadiusUpdate.cancel();
  _showIconMsg('恢复中…', false);
  const result = await window.pywebview.api.restore_default_icon();
  if (result && result.success) {
    $('#icon-preview-img').src = result.preview || '';
  }
  _showIconMsg(result && result.success ? (result.msg || '已恢复') : (result.error || '失败'), !result || !result.success);
  _iconTempPath = null;
  setTimeout(() => closePanel($('#icon-preview-panel')), 1500);
});

function updateBackgroundPanelUI() {
  // 全局背景
  $('input[name="global-bg-type"][value="' + (state.globalBg.type === 'image' ? 'image' : 'color') + '"]').checked = true;
  const globalImageSettings = $('#tab-global-bg .bg-image-settings');
  if (state.globalBg.type === 'image') {
    globalImageSettings.style.display = 'flex';
    $('#global-bg-filename').textContent = state.globalBg.value ? state.globalBg.value.split(/[/\\]/).pop() : '';
    $('#global-bg-opacity').value = Math.round(state.globalBg.opacity * 100);
    $('#global-bg-opacity-val').textContent = Math.round(state.globalBg.opacity * 100) + '%';
    $('#global-bg-zoom').value = state.globalBg.zoom || 100;
    $('#global-bg-zoom-val').textContent = (state.globalBg.zoom || 100) + '%';
  } else {
    globalImageSettings.style.display = 'none';
  }

  // 笔记背景
  if (state.activeNoteId) {
    const note = state.notes.find(n => n.id === state.activeNoteId);
    if (note) {
      const noteBgType = note.bg_type || 'global';
      const radio = $(`input[name="note-bg-type"][value="${noteBgType}"]`);
      if (radio) radio.checked = true;

      const noteImageSettings = $('#tab-note-bg .bg-image-settings');
      if (noteBgType === 'image') {
        noteImageSettings.style.display = 'flex';
        $('#note-bg-filename').textContent = note.bg_value ? note.bg_value.split(/[/\\]/).pop() : '';
        $('#note-bg-opacity').value = Math.round((note.bg_opacity || 0.7) * 100);
        $('#note-bg-opacity-val').textContent = Math.round((note.bg_opacity || 0.7) * 100) + '%';
        // 从数据库读取当前笔记的实际位置和缩放值
        $('#note-bg-zoom').value = note.bg_zoom || 100;
        $('#note-bg-zoom-val').textContent = (note.bg_zoom || 100) + '%';
        noteBgZoom = note.bg_zoom || 100;
        noteBgPos.x = note.bg_pos_x ?? 50;
        noteBgPos.y = note.bg_pos_y ?? 50;
      } else {
        noteImageSettings.style.display = 'none';
      }
    }
  }
}

