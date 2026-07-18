// ====== 自适应背景分析 ======
function analyzeImageColor(dataUri, callback) {
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement('canvas');
    const maxDim = 80; // 缩放到 80px 采样
    const scale = Math.min(1, maxDim / Math.max(img.width, img.height));
    canvas.width = Math.round(img.width * scale);
    canvas.height = Math.round(img.height * scale);
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    // 网格分区采样：每个区域取代表色，然后取中位数
    const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const gridSize = 8;
    const samples = [];

    for (let gy = 0; gy < gridSize; gy++) {
      for (let gx = 0; gx < gridSize; gx++) {
        let sr = 0, sg = 0, sb = 0, sc = 0;
        const x0 = Math.floor(gx * canvas.width / gridSize);
        const y0 = Math.floor(gy * canvas.height / gridSize);
        const x1 = Math.floor((gx + 1) * canvas.width / gridSize);
        const y1 = Math.floor((gy + 1) * canvas.height / gridSize);
        for (let y = y0; y < y1; y += 2) {
          for (let x = x0; x < x1; x += 2) {
            const i = (y * canvas.width + x) * 4;
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
    callback({ r, g, b, luminance, isDarkBg });
  };
  img.onerror = () => callback(null);
  img.src = dataUri;
}

function applyAdaptiveUI(colorInfo) {
  // 极简自适应：只控制工具栏/编辑器透明 + 边框线
  // 侧边栏跟随主题色，背景面板深色底白字，均不受影响
  const body = document.body;
  body.classList.add('adaptive-bg');
  body.style.setProperty('--ad-toolbar-bg', 'transparent');
  body.style.setProperty('--ad-border-strong', 'rgba(128,128,128,0.40)');

  if (colorInfo) {
    window.pywebview.api.settings_set('adaptive_color', JSON.stringify(colorInfo));
  }
}

function clearAdaptiveUI() {
  const body = document.body;
  body.classList.remove('adaptive-bg');
  ['--ad-toolbar-bg','--ad-border-strong'].forEach(k => body.style.removeProperty(k));
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
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
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
    stickerCat = btn.dataset.stickerCat;
    buildStickerGrid();
  });
});

$('#btn-sticker').addEventListener('click', () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  stickerCat = 'date';
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

function loadPaperForNote(note) {
  if (!state.quill || !note) return;
  NotepadConfig.paperStyles.forEach(s => { if (s.innerCls) state.quill.root.classList.remove(s.innerCls); });
  const style = note.paper_style || 'none';
  const ps = NotepadConfig.paperStyles.find(s => s.id === style);
  if (ps && ps.innerCls) state.quill.root.classList.add(ps.innerCls);
}

$('#btn-paper').addEventListener('click', () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
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

function generateNoteCover(note) {
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
function buildCoverPanel() {
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

