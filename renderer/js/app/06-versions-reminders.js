// ====== 历史版本 ======
let currentPreviewVersionId = null;

$('#btn-version-history').addEventListener('click', async () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  loadVersionList();
  openPanel($('#version-panel'));
});

async function loadVersionList() {
  const versions = await window.pywebview.api.versions_list(state.activeNoteId);
  const list = $('#version-list');
  list.innerHTML = '';
  // 操作栏
  const bar = document.createElement('div');
  bar.style.cssText = 'display:flex;gap:8px;margin-bottom:10px;';
  bar.innerHTML = '<button id="btn-delete-all-versions" class="btn-danger" style="flex:1;">删除全部</button>';
  bar.querySelector('#btn-delete-all-versions').addEventListener('click', async () => {
    if (!state.activeNoteId) return;
    if (confirm('确定删除当前笔记的全部历史版本？此操作不可恢复。')) {
      await window.pywebview.api.versions_delete_all(state.activeNoteId);
      loadVersionList();
    }
  });
  list.appendChild(bar);

  if (versions.length === 0) {
    const empty = document.createElement('p');
    empty.style.cssText = 'color:var(--text-muted);text-align:center;padding:20px;';
    empty.textContent = '暂无历史版本';
    list.appendChild(empty);
    return;
  }
  versions.forEach(v => {
    const item = document.createElement('div');
    item.className = 'version-item';
    item.innerHTML = `<span class="version-item-time">${v.created_at}</span>
      <span class="version-item-title">${escapeHtml(v.title || '无标题')}</span>
      <button class="btn-link" data-preview-version="${v.id}">预览</button>
      <button class="btn-link" data-delete-version="${v.id}" style="color:var(--danger);">删除</button>`;
    item.querySelector('[data-preview-version]').addEventListener('click', (e) => {
      e.stopPropagation();
      previewVersion(v.id);
    });
    item.querySelector('[data-delete-version]').addEventListener('click', async (e) => {
      e.stopPropagation();
      if (confirm('确定删除此历史版本？此操作不可恢复。')) {
        await window.pywebview.api.versions_delete(v.id);
        loadVersionList();
      }
    });
    list.appendChild(item);
  });
}

async function previewVersion(vid) {
  const isUnlocked = unlockedNotes[state.activeNoteId] === true;
  const ver = await window.pywebview.api.versions_get(vid, isUnlocked);
  if (!ver) return;
  if (ver.is_encrypted) {
    alert('请先解锁笔记才能查看历史版本内容');
    return;
  }
  currentPreviewVersionId = vid;
  $('#version-preview-title').textContent = `版本预览 - ${ver.created_at}`;
  const content = $('#version-preview-content');
  try {
    const delta = JSON.parse(ver.content);
    const tmp = document.createElement('div');
    const q = new Quill(tmp);
    q.setContents(delta);
    content.innerHTML = q.root.innerHTML;
  } catch {
    content.textContent = ver.content;
  }
  closePanel($('#version-panel'));
  openPanel($('#version-preview-panel'));
}

$('#btn-restore-version').addEventListener('click', async () => {
  if (!currentPreviewVersionId) return;
  if (!confirm('确定恢复到此版本？当前内容将被覆盖。')) return;
  const isUnlocked = unlockedNotes[state.activeNoteId] === true;
  const note = await window.pywebview.api.versions_restore(currentPreviewVersionId, isUnlocked);
  if (!note) { alert('无法恢复：笔记已加密或版本不存在'); return; }
  if (note && state.quill) {
    try {
      const delta = JSON.parse(note.content);
      state.quill.setContents(delta);
    } catch {
      state.quill.root.innerHTML = note.content;
    }
    dom.titleInput.value = note.title || '';
    state.currentContent = note.content || '';
    state.currentTitle = note.title || '';
  }
  closePanel($('#version-preview-panel'));
  alert('已恢复到所选版本');
});

// ====== 自然语言日期解析器 ======
function parseNaturalDate(input) {
  if (!input || !input.trim()) return null;
  let text = input.trim();
  let repeatType = 'none';
  let repeatInterval = 1;

  // 提取重复前缀
  const repeatPatterns = [
    { regex: /^每天(早上|上午|中午|下午|晚上|早晨)?/, type: 'daily' },
    { regex: /^每周(隔)?(\d+)?/, type: 'weekly', intervalGroup: 2 },
    { regex: /^每个?月/, type: 'monthly' },
    { regex: /^每年/, type: 'yearly' },
    { regex: /^工作日/, type: 'weekday' },
    { regex: /^每逢?(周一|周二|周三|周四|周五|周六|周日|星期[一二三四五六日天])/, type: 'weekly' },
  ];

  for (const pat of repeatPatterns) {
    const m = text.match(pat.regex);
    if (m) {
      repeatType = pat.type;
      if (pat.intervalGroup && m[pat.intervalGroup]) {
        repeatInterval = parseInt(m[pat.intervalGroup]) || 1;
      }
      text = text.slice(m[0].length);
      break;
    }
  }

  // 星期名映射
  const weekNames = {
    '周一': 0, '周二': 1, '周三': 2, '周四': 3, '周五': 4, '周六': 5, '周日': 6,
    '星期一': 0, '星期二': 1, '星期三': 2, '星期四': 3, '星期五': 4, '星期六': 5, '星期日': 6, '星期天': 6,
    '周天': 6, 'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6,
  };

  const now = new Date();
  let targetDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let targetHour = 9, targetMinute = 0;
  let hasTime = false;
  let dateSet = false;

  // 匹配相对日期
  const dayPatterns = [
    { regex: /^今天/, offset: 0 },
    { regex: /^明天/, offset: 1 },
    { regex: /^后天/, offset: 2 },
    { regex: /^大后天/, offset: 3 },
    { regex: /^昨天/, offset: -1 },
    { regex: /^前天/, offset: -2 },
  ];
  for (const dp of dayPatterns) {
    if (text.startsWith(dp.regex.source.replace('^', ''))) {
      targetDate.setDate(targetDate.getDate() + dp.offset);
      text = text.slice(dp.regex.source.replace('^', '').length);
      dateSet = true;
      break;
    }
  }

  // 匹配星期
  if (!dateSet) {
    for (const [name, dow] of Object.entries(weekNames)) {
      let prefix = '';
      if (text.startsWith('下' + name)) { prefix = '下'; }
      else if (text.startsWith('下下' + name)) { prefix = '下下'; }
      else if (text.startsWith(name)) { prefix = ''; }
      else continue;

      if (prefix === '') {
        // 本周
        const todayDow = now.getDay();
        let diff = dow - todayDow;
        if (diff <= 0) diff += 7; // 本周已过则取下周
        targetDate.setDate(targetDate.getDate() + diff);
      } else if (prefix === '下') {
        const todayDow = now.getDay();
        let diff = dow - todayDow;
        if (diff <= 0) diff += 7;
        targetDate.setDate(targetDate.getDate() + diff + 7);
      } else {
        const todayDow = now.getDay();
        let diff = dow - todayDow;
        if (diff <= 0) diff += 7;
        targetDate.setDate(targetDate.getDate() + diff + 14);
      }
      text = text.slice(prefix.length + name.length);
      dateSet = true;
      break;
    }
  }

  // 匹配 N天后 / N周后 / N个月后
  const offsetMatch = text.match(/^(\d+)\s*(天|周|个?月|年)后/);
  if (offsetMatch) {
    const num = parseInt(offsetMatch[1]);
    const unit = offsetMatch[2];
    if (unit === '天') targetDate.setDate(targetDate.getDate() + num);
    else if (unit === '周') targetDate.setDate(targetDate.getDate() + num * 7);
    else if (unit.includes('月')) targetDate.setMonth(targetDate.getMonth() + num);
    else if (unit === '年') targetDate.setFullYear(targetDate.getFullYear() + num);
    text = text.slice(offsetMatch[0].length);
    dateSet = true;
  }

  // 匹配时间
  const timePatterns = [
    { regex: /^早上(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h },
    { regex: /^上午(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h },
    { regex: /^中午(\d{1,2})?点?(\d{1,2})?分?/, hourFn: (h, m) => h ? h : 12 },
    { regex: /^下午(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h === 12 ? 12 : h + 12 },
    { regex: /^晚上(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h === 12 ? 12 : h + 12 },
    { regex: /^傍晚(\d{1,2})点(\d{1,2})?分?/, hourFn: (h) => h + 17 > 23 ? 23 : h + 17 },
    { regex: /^(\d{1,2}):(\d{2})/, hourFn: (h, m) => h },
  ];

  for (const tp of timePatterns) {
    const m = text.match(tp.regex);
    if (m) {
      const h = parseInt(m[1] || '0');
      const min = m[2] ? parseInt(m[2]) : 0;
      targetHour = tp.hourFn(h, min);
      targetMinute = min;
      hasTime = true;
      text = text.slice(m[0].length);
      break;
    }
  }

  if (!hasTime && text.trim()) {
    // 匹配纯数字时间如 "15:00"
    const tm = text.match(/^(\d{1,2})[：:](\d{2})/);
    if (tm) {
      targetHour = parseInt(tm[1]);
      targetMinute = parseInt(tm[2]);
      hasTime = true;
      text = text.slice(tm[0].length);
    }
  }

  // 格式化为日期时间字符串
  const y = targetDate.getFullYear();
  const mo = String(targetDate.getMonth() + 1).padStart(2, '0');
  const d = String(targetDate.getDate()).padStart(2, '0');
  const hh = String(targetHour).padStart(2, '0');
  const mm = String(targetMinute).padStart(2, '0');
  const remindAt = `${y}-${mo}-${d} ${hh}:${mm}`;

  // 生成人类可读的提示
  const weekNamesCN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const repeatNames = { none: '', daily: '每天', weekly: '每周', weekday: '工作日', monthly: '每月', yearly: '每年' };
  const dateStr = `${y}年${mo}月${d}日 ${weekNamesCN[targetDate.getDay()]}`;
  const timeStr = `${hh}:${mm}`;
  const repeatStr = repeatNames[repeatType] || '';
  const hint = repeatStr ? `${repeatStr} ${dateStr} ${timeStr}` : `${dateStr} ${timeStr}`;

  return { remindAt, repeatType, repeatInterval, hint, parsed: true };
}

// ====== 提醒系统 ======
let _editingReminderId = null; // 当前正在编辑的提醒 ID

// 打开提醒设置面板
$('#btn-reminder').addEventListener('click', async () => {
  if (!state.activeNoteId) { alert('请先选择一篇笔记'); return; }
  _editingReminderId = null;
  $('#reminder-panel-title').textContent = '设置提醒';
  $('#reminder-content').value = '';
  $('#reminder-nl-input').value = '';
  $('#reminder-datetime').value = '';
  $('#reminder-parsed-hint').textContent = '';
  $('#reminder-parsed-hint').className = 'hint-text';
  $('#reminder-edit-id').value = '';
  $('#btn-delete-reminder').style.display = 'none';
  // 重置重复选项
  $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
  const defaultBtn = $('#reminder-repeat-options').querySelector('[data-repeat="none"]');
  if (defaultBtn) defaultBtn.classList.add('active');
  // 加载该笔记已有的提醒信息
  try {
    const reminders = await window.pywebview.api.reminder_list(state.activeNoteId);
    if (reminders && reminders.length === 1) {
      const r = reminders[0];
      _editingReminderId = r.id;
      $('#reminder-panel-title').textContent = '编辑提醒';
      $('#reminder-content').value = r.content || '';
      $('#reminder-datetime').value = r.remind_at.replace(' ', 'T');
      $('#reminder-edit-id').value = r.id;
      $('#btn-delete-reminder').style.display = 'block';
      $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
      const rb = $('#reminder-repeat-options').querySelector(`[data-repeat="${r.repeat_type}"]`);
      if (rb) rb.classList.add('active');
    }
  } catch(e) {}
  openPanel($('#reminder-panel'));
});

// 自然语言输入实时解析
$('#reminder-nl-input').addEventListener('input', () => {
  const val = $('#reminder-nl-input').value.trim();
  const hintEl = $('#reminder-parsed-hint');
  if (!val) { hintEl.textContent = ''; hintEl.className = 'hint-text'; return; }
  const parsed = parseNaturalDate(val);
  if (parsed) {
    hintEl.textContent = '📅 ' + parsed.hint;
    hintEl.className = 'hint-text';
    // 自动填充 datetime 选择器
    $('#reminder-datetime').value = parsed.remindAt.replace(' ', 'T');
    // 自动选择重复选项
    $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
    const rb = $('#reminder-repeat-options').querySelector(`[data-repeat="${parsed.repeatType}"]`);
    if (rb) rb.classList.add('active');
    else $('#reminder-repeat-options').querySelector('[data-repeat="none"]').classList.add('active');
  } else {
    hintEl.textContent = '无法识别，请手动选择时间';
    hintEl.className = 'hint-text error';
  }
});

// datetime 手动修改时清理解析提示
$('#reminder-datetime').addEventListener('input', () => {
  if ($('#reminder-nl-input').value.trim()) return;
});

// 重复选项点击
$$('#reminder-repeat-options .repeat-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

// 保存提醒
$('#btn-save-reminder').addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  const content = $('#reminder-content').value.trim();
  const nlInput = $('#reminder-nl-input').value.trim();
  let dt = $('#reminder-datetime').value;
  if (!dt) { alert('请设置提醒时间'); return; }

  // 如果用了自然语言且解析成功，优先使用解析结果
  let repeatType = 'none', repeatInterval = 1;
  if (nlInput) {
    const parsed = parseNaturalDate(nlInput);
    if (parsed) {
      dt = parsed.remindAt.replace(' ', 'T');
      repeatType = parsed.repeatType;
      repeatInterval = parsed.repeatInterval;
    }
  }
  // 从按钮获取重复类型
  if (repeatType === 'none') {
    const activeRepeat = $('#reminder-repeat-options').querySelector('.repeat-btn.active');
    if (activeRepeat) repeatType = activeRepeat.dataset.repeat;
  }
  const remindAt = dt.replace('T', ' ') + ':00';

  const editId = $('#reminder-edit-id').value;
  if (editId) {
    // 更新已有提醒
    await window.pywebview.api.reminder_update(editId, {
      content, remind_at: remindAt, repeat_type: repeatType, repeat_interval: repeatInterval, is_completed: 0
    });
  } else {
    await window.pywebview.api.reminder_create(state.activeNoteId, content || '未命名提醒', remindAt, repeatType, repeatInterval);
  }
  closePanel($('#reminder-panel'));
});

// 删除提醒
$('#btn-delete-reminder').addEventListener('click', async () => {
  const editId = $('#reminder-edit-id').value;
  if (editId) {
    await window.pywebview.api.reminder_delete(editId);
  }
  closePanel($('#reminder-panel'));
});

// ====== 提醒列表面板 ======
$('#btn-reminder-list').addEventListener('click', async () => {
  await loadReminderList();
  openPanel($('#reminder-list-panel'));
});

async function loadReminderList() {
  const container = $('#reminder-list-items');
  if (!container) return;
  try {
    const reminders = await window.pywebview.api.reminder_list_all();
    if (!reminders || reminders.length === 0) {
      container.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-muted);">暂无提醒</div>';
      return;
    }
    const svgCheck = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    const svgEdit = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
    const svgTrash = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
    const svgRepeat = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>';
    const repeatLabels = { none: '', daily: `${svgRepeat}每天`, weekly: `${svgRepeat}每周`, weekday: `${svgRepeat}工作日`, monthly: `${svgRepeat}每月`, yearly: `${svgRepeat}每年` };
    container.innerHTML = reminders.map(r => {
      const isCompleted = r.is_completed === 1;
      const repeatIcon = repeatLabels[r.repeat_type] || '';
      const noteTitle = r.note_title || '已删除笔记';
      return `<div class="reminder-item${isCompleted ? ' completed' : ''}" data-rid="${r.id}">
        <div class="reminder-item-content" title="点击跳转到笔记" data-noteid="${r.note_id}">${escapeHtml(r.content || '未命名提醒')}</div>
        <div class="reminder-item-meta">
          ${repeatIcon ? `<span class="reminder-item-repeat">${repeatIcon}</span>` : ''}
          <span>${r.remind_at}</span>
          <span class="reminder-item-note" data-noteid="${r.note_id}">${escapeHtml(noteTitle)}</span>
        </div>
        <div class="reminder-item-actions">
          ${!isCompleted ? `<button class="reminder-item-btn" title="完成" data-action="complete" data-rid="${r.id}">${svgCheck}</button>` : ''}
          <button class="reminder-item-btn" title="编辑" data-action="edit" data-rid="${r.id}">${svgEdit}</button>
          <button class="reminder-item-btn danger" title="删除" data-action="delete" data-rid="${r.id}">${svgTrash}</button>
        </div>
      </div>`;
    }).join('');

    // 绑定事件
    container.querySelectorAll('.reminder-item-btn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const rid = btn.dataset.rid;
        const action = btn.dataset.action;
        if (action === 'complete') {
          await window.pywebview.api.reminder_complete(rid);
          await loadReminderList();
        } else if (action === 'edit') {
          await editReminderFromList(rid);
        } else if (action === 'delete') {
          if (await window.pywebview.api.confirm('确定要删除此提醒吗？', '删除提醒')) {
            await window.pywebview.api.reminder_delete(rid);
            await loadReminderList();
          }
        }
      });
    });
    // 点击内容/笔记名跳转
    container.querySelectorAll('.reminder-item-content, .reminder-item-note').forEach(el => {
      el.addEventListener('click', async (e) => {
        e.stopPropagation();
        const noteId = el.dataset.noteid;
        if (noteId) {
          closePanel($('#reminder-list-panel'));
          await selectNote(noteId);
        }
      });
    });
  } catch(e) {
    container.innerHTML = '<div style="text-align:center;padding:30px;color:#E53E3E;">加载失败</div>';
  }
}

async function editReminderFromList(rid) {
  closePanel($('#reminder-list-panel'));
  // 获取提醒详情并打开编辑面板
  const r = await window.pywebview.api.reminder_get(rid);
  if (!r) return;
  _editingReminderId = r.id;
  // 切换到对应笔记
  if (r.note_id && r.note_id !== state.activeNoteId) {
    await selectNote(r.note_id);
  }
  $('#reminder-panel-title').textContent = '编辑提醒';
  $('#reminder-content').value = r.content || '';
  $('#reminder-datetime').value = (r.remind_at || '').replace(' ', 'T');
  $('#reminder-nl-input').value = '';
  $('#reminder-parsed-hint').textContent = '';
  $('#reminder-parsed-hint').className = 'hint-text';
  $('#reminder-edit-id').value = r.id;
  $('#btn-delete-reminder').style.display = 'block';
  $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
  const rb = $('#reminder-repeat-options').querySelector(`[data-repeat="${r.repeat_type}"]`);
  if (rb) rb.classList.add('active');
  else $('#reminder-repeat-options').querySelector('[data-repeat="none"]').classList.add('active');
  openPanel($('#reminder-panel'));
}

// ====== Toast 通知系统 ======
let _toastTimers = {};

function showToast(reminder) {
  const toastId = 'toast-' + reminder.id;
  // 避免重复弹出
  if (document.getElementById(toastId)) return;

  const container = $('#toast-container');
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.id = toastId;

  const content = reminder.content || '提醒';
  const time = reminder.remind_at || '';

  toast.innerHTML = `
    <div class="toast-content">🔔 ${escapeHtml(content)}</div>
    <div class="toast-time">${escapeHtml(time)}</div>
    <div class="toast-actions">
      <button class="toast-btn toast-btn-primary" data-action="complete">${svgCheck} 完成</button>
      <div class="snooze-dropdown">
        <button class="toast-btn snooze-toggle">🕐 稍后</button>
        <div class="snooze-menu">
          <button data-snooze="5">5 分钟后</button>
          <button data-snooze="15">15 分钟后</button>
          <button data-snooze="30">30 分钟后</button>
          <button data-snooze="60">1 小时后</button>
        </div>
      </div>
      <button class="toast-close" data-action="dismiss">✕</button>
    </div>
  `;

  container.appendChild(toast);

  // 完成按钮
  toast.querySelector('[data-action="complete"]').addEventListener('click', async () => {
    await window.pywebview.api.reminder_complete(reminder.id);
    removeToast(toastId);
  });

  // 关闭按钮
  toast.querySelector('[data-action="dismiss"]').addEventListener('click', () => {
    removeToast(toastId);
  });

  // 稍后下拉
  const snoozeToggle = toast.querySelector('.snooze-toggle');
  const snoozeMenu = toast.querySelector('.snooze-menu');
  snoozeToggle.addEventListener('click', (e) => {
    e.stopPropagation();
    snoozeMenu.classList.toggle('show');
  });
  toast.querySelectorAll('[data-snooze]').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const minutes = parseInt(btn.dataset.snooze);
      await window.pywebview.api.reminder_snooze(reminder.id, minutes);
      snoozeMenu.classList.remove('show');
      removeToast(toastId);
    });
  });

  // 点击其他地方关闭下拉
  document.addEventListener('click', function hideSnooze(e) {
    if (!toast.contains(e.target)) {
      snoozeMenu.classList.remove('show');
    }
  }, { once: true });

  // 10 秒后自动消失
  _toastTimers[toastId] = setTimeout(() => {
    removeToast(toastId);
  }, 10000);
}

function removeToast(toastId) {
  const toast = document.getElementById(toastId);
  if (!toast) return;
  if (_toastTimers[toastId]) { clearTimeout(_toastTimers[toastId]); delete _toastTimers[toastId]; }
  toast.classList.add('removing');
  setTimeout(() => { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
}

// ====== 定时检查提醒 ======
function checkReminders() {
  if (!window.pywebview || !window.pywebview.api) return;
  window.pywebview.api.reminders_check().then(reminders => {
    if (reminders && reminders.length > 0) {
      reminders.forEach(r => {
        showToast(r);
        if (r.repeat_type && r.repeat_type !== 'none') {
          window.pywebview.api.reminder_update_next_repeat(r.id);
        } else {
          window.pywebview.api.reminder_complete(r.id);
        }
      });
    }
  }).catch(() => {});
}

// 首次 5 秒后检查，之后每 30 秒
setTimeout(() => { checkReminders(); _intervals.push(setInterval(checkReminders, 30000)); }, 5000);

// ====== 待办清单右键菜单：设置提醒 ======
let _checklistContextMenu = null;

function hideChecklistMenu() {
  if (_checklistContextMenu) {
    _checklistContextMenu.remove();
    _checklistContextMenu = null;
  }
}

document.addEventListener('contextmenu', (e) => {
  // 检查是否在待办清单项上
  if (!state.quill) return;
  const li = e.target.closest('li');
  if (!li) { hideChecklistMenu(); return; }
  const listType = li.getAttribute('data-list');
  if (listType !== 'unchecked' && listType !== 'checked') { hideChecklistMenu(); return; }

  e.preventDefault();
  hideChecklistMenu();

  const menu = document.createElement('div');
  menu.className = 'checklist-context-menu';
  menu.style.left = e.clientX + 'px';
  menu.style.top = e.clientY + 'px';

  // 提取待办项文字
  const clone = li.cloneNode(true);
  // 移除嵌套列表
  clone.querySelectorAll('ul, ol').forEach(el => el.remove());
  const text = (clone.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 50);

  menu.innerHTML = `
    <button data-action="set-reminder">🔔 设置提醒</button>
    <button data-action="copy-text">📋 复制文字</button>
  `;

  menu.querySelector('[data-action="set-reminder"]').addEventListener('click', async () => {
    hideChecklistMenu();
    if (!state.activeNoteId) return;
    _editingReminderId = null;
    $('#reminder-panel-title').textContent = '设置提醒';
    $('#reminder-content').value = text;
    $('#reminder-nl-input').value = '';
    $('#reminder-datetime').value = '';
    $('#reminder-parsed-hint').textContent = '';
    $('#reminder-parsed-hint').className = 'hint-text';
    $('#reminder-edit-id').value = '';
    $('#btn-delete-reminder').style.display = 'none';
    $$('#reminder-repeat-options .repeat-btn').forEach(b => b.classList.remove('active'));
    const defaultBtn = $('#reminder-repeat-options').querySelector('[data-repeat="none"]');
    if (defaultBtn) defaultBtn.classList.add('active');
    openPanel($('#reminder-panel'));
  });

  menu.querySelector('[data-action="copy-text"]').addEventListener('click', () => {
    hideChecklistMenu();
    navigator.clipboard.writeText(text).catch(() => {});
  });

  document.body.appendChild(menu);
  _checklistContextMenu = menu;

  // 点击其他地方关闭
  setTimeout(() => {
    document.addEventListener('click', function closeMenu() {
      hideChecklistMenu();
      document.removeEventListener('click', closeMenu);
    }, { once: true });
  }, 0);
});

// 点击编辑器区域也关闭右键菜单
document.addEventListener('click', (e) => {
  if (_checklistContextMenu && !_checklistContextMenu.contains(e.target)) {
    hideChecklistMenu();
  }
});


