// ====== LaTeX 数学公式 ======
// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { $, $$, closePanel, dom, hideEditorUI, openPanel, showInfoDialog, showToast, state } from './01-core.js';
import { notesStore } from './01b-store.js';
import { debouncedSave, flushSave, loadNotes, renderNoteList, selectNote } from './03-notes.js';

let mathMode = 'inline'; // 'inline' | 'block'

// 分类标签切换
$$('.math-cat-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    $$('.math-cat-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const cat = tab.dataset.cat;
    $$('.math-hint-group').forEach(g => g.style.display = g.dataset.cat === cat ? 'block' : 'none');
  });
});

// MathFormula Blot 已在 js/quill/quill-blots.js 中注册

let editingMathNode = null;

// ESM：其他模块需要写入本变量（import 的绑定不可赋值），故导出 setter
export function setEditingMathNode(v) { editingMathNode = v; }

export function editMathFormula(node) {
  editingMathNode = node;
  const latex = node.getAttribute('data-latex') || '';
  const display = node.getAttribute('data-display') || 'inline';
  mathMode = display;
  $('#math-input').value = latex;
  // 更新模式按钮
  $$('.math-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mathMode));
  updateMathPreview();
  // 改按钮文字
  $('#btn-insert-math').textContent = '更新公式';
  openPanel($('#math-panel'));
}

// 模式切换
$$('.math-mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    mathMode = btn.dataset.mode;
    $$('.math-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mathMode));
    updateMathPreview();
  });
});

// 实时预览
$('#math-input').addEventListener('input', updateMathPreview);
function updateMathPreview() {
  const latex = $('#math-input').value;
  const preview = $('#math-preview');
  if (!latex.trim()) { preview.innerHTML = '<span style="color:var(--text-muted);">预览</span>'; return; }
  try {
    katex.render(latex, preview, { displayMode: mathMode === 'block', throwOnError: false, strict: false, trust: true });
  } catch(e) {
    preview.innerHTML = `<span style="color:var(--danger);">格式错误: ${e.message}</span>`;
  }
}

// LaTeX 快捷提示
$$('.math-sym-btn').forEach(hint => {
  hint.addEventListener('click', () => {
    const input = $('#math-input');
    const latex = hint.dataset.latex;
    const start = input.selectionStart;
    const end = input.selectionEnd;
    const text = input.value;
    input.value = text.substring(0, start) + latex + text.substring(end);
    input.focus();
    // 如果模板有 {}，光标移到第一个 {} 内
    const braceIdx = latex.indexOf('{}');
    if (braceIdx >= 0) {
      input.selectionStart = start + braceIdx + 1;
      input.selectionEnd = start + braceIdx + 1;
    } else {
      input.selectionStart = start + latex.length;
      input.selectionEnd = start + latex.length;
    }
    updateMathPreview();
  });
});

// 打开公式面板
$('#btn-math').addEventListener('click', () => {
  if (!state.activeNoteId) { showToast('请先选择一篇笔记', { type: 'warn' }); return; }
  editingMathNode = null;
  $('#math-input').value = '';
  $('#btn-insert-math').textContent = '插入公式';
  $('#math-preview').innerHTML = '<span style="color:var(--text-muted);">预览</span>';
  $$('.math-mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === mathMode));
  openPanel($('#math-panel'));
});

// 插入/更新公式
$('#btn-insert-math').addEventListener('click', () => {
  const latex = $('#math-input').value.trim();
  if (!latex) { showToast('请输入 LaTeX 公式', { type: 'warn' }); return; }

  if (editingMathNode) {
    // 更新已有公式
    editingMathNode.setAttribute('data-latex', latex);
    editingMathNode.setAttribute('data-display', mathMode);
    editingMathNode.className = mathMode === 'block' ? 'math-block' : 'math-inline';
    try {
      katex.render(latex, editingMathNode, { displayMode: mathMode === 'block', throwOnError: false, strict: false, trust: true });
    } catch(e) {
      editingMathNode.textContent = '[错误]';
    }
    editingMathNode = null;
  } else {
    // 插入新公式
    if (!state.quill || !state.activeNoteId) return;
    const range = state.quill.getSelection(true);
    state.quill.insertEmbed(range.index, 'math-formula', {
      latex: latex,
      display: mathMode
    });
    state.quill.setSelection(range.index + 1);
    if (mathMode === 'block') {
      state.quill.insertText(range.index + 1, '\n');
    }
  }
  closePanel($('#math-panel'));
});

// ====== 密码保护 ======
export const unlockedNotes = {}; // 本次会话已解锁的笔记 ID → true
let passwordPanelMode = 'set'; // 'set' | 'remove'

// 工具栏锁按钮
$('#btn-lock').addEventListener('click', async () => {
  if (!state.activeNoteId) { showToast('请先选择一篇笔记', { type: 'warn' }); return; }
  const hasPwd = await window.pywebview.api.note_has_password(state.activeNoteId);
  if (hasPwd) {
    // 已加密 → 先落盘（此时后端仍解锁可加密写入），再清后端密钥缓存
    const noteId = state.activeNoteId;
    await flushSave();
    await window.pywebview.api.note_lock(noteId);
    delete unlockedNotes[noteId];
    state.activeNoteId = null;
    hideEditorUI();
    if (state.quill) state.quill.enable(true);
    updateLockButton();
    renderNoteList();
  } else {
    // 无密码 → 打开设置
    openPasswordPanel('set');
  }
});

export function updateLockButton() {
  const btn = $('#btn-lock');
  if (!btn) return;
  if (state.activeNoteId && unlockedNotes[state.activeNoteId]) {
    // 已解锁 → 高亮锁图标
    btn.style.color = 'var(--accent)';
    btn.title = '锁定笔记';
  } else {
    btn.style.color = '';
    btn.title = state.activeNoteId ? '设置密码' : '锁定笔记';
  }
}

export async function openPasswordPanel(mode) {
  passwordPanelMode = mode;
  const hasPwd = await window.pywebview.api.note_has_password(state.activeNoteId);
  $('#password-input1').value = '';
  $('#password-input2').value = '';
  if (mode === 'set') {
    $('#password-panel-title').textContent = hasPwd ? '修改密码' : '设置密码';
    $('#btn-save-password').textContent = hasPwd ? '修改密码' : '设置密码';
    $('#btn-remove-password').style.display = hasPwd ? 'block' : 'none';
  }
  openPanel($('#password-panel'));
}

$('#btn-save-password').addEventListener('click', async () => {
  const p1 = $('#password-input1').value;
  const p2 = $('#password-input2').value;
  if (!p1) { showToast('请输入密码', { type: 'warn' }); return; }
  if (p1 !== p2) { showToast('两次输入不一致', { type: 'warn' }); return; }
  if (p1.length < 6) { showToast('密码至少 6 位', { type: 'warn' }); return; }
  await flushSave();  // 设密码前先落盘，加密以最新内容为准
  const ok = await window.pywebview.api.note_set_password(state.activeNoteId, p1);
  if (!ok) { showToast('密码设置失败：请先解锁笔记后再修改密码', { type: 'error' }); return; }
  closePanel($('#password-panel'));
  await showInfoDialog({
    title: '密码设置成功',
    message: '笔记内容已加密存储。\n\n请务必牢记密码：忘记密码将无法恢复笔记内容。'
  });
  unlockedNotes[state.activeNoteId] = true;
  loadNotes().then(renderNoteList);
});

$('#btn-remove-password').addEventListener('click', async () => {
  const p = $('#password-input1').value;
  if (!p) { showToast('请先输入当前密码', { type: 'warn' }); return; }
  await flushSave();  // 先落盘，解密回写以最新内容为准
  const ok = await window.pywebview.api.note_remove_password(state.activeNoteId, p);
  if (ok) {
    closePanel($('#password-panel'));
    showToast('密码已移除', { type: 'success' });
    delete unlockedNotes[state.activeNoteId];
    loadNotes().then(renderNoteList);
  } else {
    showToast('密码错误', { type: 'error' });
  }
});

// 密码验证
let pendingSelectNoteId = null;

// ESM：其他模块需要写入本变量（import 的绑定不可赋值），故导出 setter
export function setPendingSelectNoteId(v) { pendingSelectNoteId = v; }
let passwordVerifyCallback = null;  // 验证成功后的自定义回调

// ESM：其他模块需要写入本变量（import 的绑定不可赋值），故导出 setter
export function setPasswordVerifyCallback(v) { passwordVerifyCallback = v; }

// 密码输入框回车键直接验证
$('#password-verify-input').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    $('#btn-verify-password').click();
  }
});

export async function verifyAndSelectNote(noteId) {
  const hasPwd = await window.pywebview.api.note_has_password(noteId);
  if (!hasPwd || unlockedNotes[noteId]) {
    unlockedNotes[noteId] = true;
    await selectNote(noteId);
    return;
  }
  await flushSave();  // 先把当前笔记未保存内容落盘（activeNoteId 此时仍指向旧笔记）
  pendingSelectNoteId = noteId;
  passwordVerifyCallback = null;  // 默认行为：解锁后加载笔记
  $('#password-verify-input').value = '';
  $('#password-error-msg').style.display = 'none';
  openPanel($('#password-verify-panel'));
}

$('#btn-verify-password').addEventListener('click', async () => {
  const p = $('#password-verify-input').value;
  if (!p) return;
  const ok = await window.pywebview.api.note_verify_password(pendingSelectNoteId, p);
  if (ok) {
    unlockedNotes[pendingSelectNoteId] = true;
    updateLockButton();
    closePanel($('#password-verify-panel'));
    // 不在此处保存：activeNoteId 可能已指向加密笔记，而编辑器内容并非它的
    //（未保存的旧笔记内容已在打开面板前 flush），仅取消待保存任务后重新加载
    debouncedSave.cancel();
    state.activeNoteId = null;
    await selectNote(pendingSelectNoteId);
    // 执行自定义回调（如果有）
    if (passwordVerifyCallback) {
      const cb = passwordVerifyCallback;
      passwordVerifyCallback = null;
      cb();
    }
  } else {
    $('#password-error-msg').style.display = 'block';
    $('#password-verify-input').value = '';
    $('#password-verify-input').focus();
  }
});

$('#btn-cancel-verify').addEventListener('click', () => {
  closePanel($('#password-verify-panel'));
  // 恢复之前的状态：如果之前有活跃笔记则切回去
  const prevNoteId = state.activeNoteId;
  state.activeNoteId = null;  // 重置，允许再次点击同一篇加密笔记
  if (pendingSelectNoteId && prevNoteId === pendingSelectNoteId) {
    // 用户取消了加密笔记的解锁，回到无笔记状态或之前的笔记
    hideEditorUI();
    if (state.quill) state.quill.enable(true);
    // 尝试加载第一篇非加密笔记
    const firstUnlocked = state.notes.find(n => !n.has_password);
    if (firstUnlocked) {
      selectNote(firstUnlocked.id);
    }
  }
  pendingSelectNoteId = null;
});

// 修改笔记点击事件：点击加密笔记时先验证
// 在 renderNoteList 中，note-item 的 click 需要调用 verifyAndSelectNote

// 工具栏加密码按钮
// 在标签栏的 btn-add-tag 旁边已有点击事件，无需额外 UI
// 在编辑器区域添加一个密码按钮的入口
// 实际上可以在侧边栏每个笔记的右键菜单里加

// ====== 笔记列表拖拽排序 ======
let dragSrcIndex = null;

dom.noteList.addEventListener('dragstart', (e) => {
  const item = e.target.closest('.note-item');
  if (!item) return;
  dragSrcIndex = [...dom.noteList.children].indexOf(item);
  item.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  e.dataTransfer.setData('text/plain', '');
});

dom.noteList.addEventListener('dragend', (e) => {
  const item = e.target.closest('.note-item');
  if (item) item.classList.remove('dragging');
  dom.noteList.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
});

dom.noteList.addEventListener('dragover', (e) => {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  const item = e.target.closest('.note-item');
  if (item) {
    dom.noteList.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    item.classList.add('drag-over');
  }
});

dom.noteList.addEventListener('drop', async (e) => {
  e.preventDefault();
  const item = e.target.closest('.note-item');
  if (!item || dragSrcIndex === null) return;
  item.classList.remove('drag-over');
  const dstIndex = [...dom.noteList.children].indexOf(item);
  if (dragSrcIndex === dstIndex) return;

  // 重新排列 state.notes（统一走 store）
  notesStore.move(dragSrcIndex, dstIndex);

  // 更新所有笔记的 sort_order：列表按 sort_order DESC 排序，顶部（index 0）取最大值；
  // 后端对纯排序更新不 bump updated_at，拖拽后顺序才可持久
  const n = state.notes.length;
  for (let i = 0; i < n; i++) {
    await window.pywebview.api.notes_update(state.notes[i].id, { sort_order: n - 1 - i });
  }
  renderNoteList();
  dragSrcIndex = null;
});

// ====== 通用拖拽排序函数 ======
function makeDraggable(containerSelector, buttonSelector, settingKey) {
  const container = document.querySelector(containerSelector);
  if (!container) return;

  const makeAllDraggable = () => {
    container.querySelectorAll(buttonSelector).forEach(btn => { btn.draggable = true; });
  };
  makeAllDraggable();

  container.addEventListener('dragstart', (e) => {
    const btn = e.target.closest(buttonSelector);
    if (!btn) return;
    btn.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', '');
  });

  container.addEventListener('dragend', (e) => {
    const btn = e.target.closest(buttonSelector);
    if (btn) btn.classList.remove('dragging');
    container.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
    // 保存顺序
    const ids = [...container.querySelectorAll(buttonSelector)].map(b => b.id).filter(Boolean);
    if (ids.length > 0 && settingKey) {
      window.pywebview.api.settings_set(settingKey, JSON.stringify(ids));
    }
  });

  container.addEventListener('dragover', (e) => {
    e.preventDefault();
    const btn = e.target.closest(buttonSelector);
    if (btn) {
      container.querySelectorAll('.drag-over').forEach(el => el.classList.remove('drag-over'));
      btn.classList.add('drag-over');
    }
  });

  container.addEventListener('drop', (e) => {
    e.preventDefault();
    const btn = e.target.closest(buttonSelector);
    if (!btn) return;
    btn.classList.remove('drag-over');
    const dragged = container.querySelector('.dragging');
    if (!dragged || dragged === btn) return;
    const rect = btn.getBoundingClientRect();
    const mid = rect.left + rect.width / 2;
    if (e.clientX < mid) {
      container.insertBefore(dragged, btn);
    } else {
      container.insertBefore(dragged, btn.nextSibling);
    }
  });

  // 加载保存的顺序
  if (settingKey) {
    (async () => {
      const order = await window.pywebview.api.settings_get(settingKey);
      if (order) {
        try {
          const ids = JSON.parse(order);
          ids.reverse().forEach(id => {
            const btn = container.querySelector('#' + id);
            if (btn) container.insertBefore(btn, container.firstChild);
          });
        } catch(e) {}
      }
    })();
  }
}

// ====== Dock 栏 + 工具栏按钮拖拽排序 ======
export function initAllDrag() {
  makeDraggable('.sidebar-footer', '.btn-sidebar-footer', 'footer_order');
  makeDraggable('.custom-formats', '.ql-custom-btn', 'toolbar_order');
}

