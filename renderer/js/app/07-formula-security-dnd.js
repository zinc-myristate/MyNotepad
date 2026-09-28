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
    // trust:false —— 与 quill-blots.js / 14-markdown-render.js 统一，理由见 quill-blots.js 里的说明
    katex.render(latex, preview, { displayMode: mathMode === 'block', throwOnError: false, strict: false, trust: false });
  } catch(e) {
    // 用 textContent 而不是拼 innerHTML：e.message 是第三方库给的字符串，
    // 把它塞进 innerHTML 等于给"第三方文案"留了一条 HTML 通道（同类写法全项目就这一处）
    preview.textContent = '';
    const err = document.createElement('span');
    err.style.color = 'var(--danger)';
    err.textContent = '格式错误: ' + (e.message || e);
    preview.appendChild(err);
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
      katex.render(latex, editingMathNode, { displayMode: mathMode === 'block', throwOnError: false, strict: false, trust: false });
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

// ====== 闲置自动锁定 ======
// 解锁状态一直是"到手动锁定或关窗为止"。这里补上超时：闲置 N 分钟后自动锁回去，
// 并把编辑器恢复成锁定态——只清后端密钥缓存而界面还显示明文，等于没锁。
let _autoLockMinutes = 0;      // 0 = 关闭
let _lastActivity = Date.now();
let _autoLockTimer = null;

function _markActivity() { _lastActivity = Date.now(); }

/** 从设置读取超时值并刷新下拉框（面板打开/启动时调用） */
export async function loadAutoLockSetting() {
  try {
    const v = await window.pywebview.api.settings_get('auto_lock_minutes');
    _autoLockMinutes = parseInt(v || '0', 10) || 0;
  } catch (e) {
    _autoLockMinutes = 0;
  }
  const sel = $('#sel-autolock');
  if (sel) sel.value = String(_autoLockMinutes);
  // 同一条设置也告诉后端：前端负责"用户一停手就锁"的即时体验，
  // 后端那层是兜底（前端脚本出错/窗口被挂起时密钥也不长留）。两边的分钟数必须一致，
  // 否则会出现"界面显示着明文、后端其实已经过期"或反过来。
  try {
    await window.pywebview.api.note_set_lock_ttl(_autoLockMinutes);
  } catch (e) { /* 后端不支持时忽略（旧版本/单测桩） */ }
  return _autoLockMinutes;
}

/** 与后端对齐解锁状态：后端已经过期/清掉的笔记，界面上也必须回到锁定态。
 *
 *  为什么必须有这一步：光在后端清密钥而界面继续显示明文，等于没锁（这个模块开头就写着
 *  这条教训）。后端那层 TTL 是兜底，它过期时前端不一定知道 —— 所以搭车 30 秒的提醒轮询
 *  问一次"后端还认为哪些没锁"，把对不上的收回来。
 */
export async function reconcileUnlockState() {
  // 正在加载/切换笔记时不动手：那会把还没建立好的状态当成"该锁了"
  if (state.isLoading) return;
  let alive = null;
  try {
    alive = await window.pywebview.api.note_unlock_status();
  } catch (e) {
    return;                       // 问不到就不动（宁可保持现状，不要误锁用户正在看的笔记）
  }
  if (!Array.isArray(alive)) return;
  const aliveSet = new Set(alive);
  const stale = Object.keys(unlockedNotes).filter(id => {
    if (aliveSet.has(id)) return false;
    // 第二道闸：只收回**真加密**的笔记。`unlockedNotes` 历史上被当成"可打开"用过，
    // 万一还有别处给明文笔记置了标记，这里也不会把普通笔记锁掉（那会表现为
    // "用着用着编辑区自己收起"）。后端不该持有明文笔记的密钥，所以这条过滤不会
    // 放过真正该锁的笔记。
    const note = state.notes.find(n => n.id === id);
    return !!(note && note.has_password);
  });
  if (!stale.length) return;
  const activeWasStale = state.activeNoteId && stale.includes(state.activeNoteId);
  stale.forEach(id => { delete unlockedNotes[id]; });
  if (activeWasStale) {
    // 与手动锁定/闲置锁定同一套收尾动作：清编辑器 + 隐藏编辑区
    await flushSave().catch(() => {});
    state.activeNoteId = null;
    state.currentContent = '';
    state.currentTitle = '';
    if (state.quill) { state.quill.setContents([]); state.quill.enable(true); }
    hideEditorUI();
    showToast('后端已锁定（闲置超时），编辑区已收起', { type: 'info' });
  }
  updateLockButton();
  renderNoteList();
}

async function _lockAllUnlocked() {
  const ids = Object.keys(unlockedNotes);
  if (!ids.length) return;
  const activeWasUnlocked = state.activeNoteId && unlockedNotes[state.activeNoteId];
  for (const id of ids) {
    try {
      await window.pywebview.api.note_lock(id);
    } catch (e) { /* 单条失败不影响其余 */ }
    delete unlockedNotes[id];
  }
  if (activeWasUnlocked) {
    // 恢复成"加密未解锁"的样子：清空编辑器 + 隐藏编辑区（与 selectNote 的锁定分支一致）
    await flushSave();
    state.activeNoteId = null;
    state.currentContent = '';
    state.currentTitle = '';
    if (state.quill) { state.quill.setContents([]); state.quill.enable(true); }
    hideEditorUI();
  }
  updateLockButton();
  renderNoteList();
  showToast('已自动锁定（闲置 ' + _autoLockMinutes + ' 分钟）', { type: 'info' });
}

/** 启动自动锁定：记录活动时间 + 每分钟检查一次。返回清理函数（测试用）。 */
export function initAutoLock() {
  loadAutoLockSetting();
  ['mousemove', 'keydown', 'wheel', 'click'].forEach(ev =>
    document.addEventListener(ev, _markActivity, { passive: true }));
  if (_autoLockTimer) clearInterval(_autoLockTimer);
  _autoLockTimer = setInterval(() => {
    if (!_autoLockMinutes) return;
    if (Object.keys(unlockedNotes).length === 0) return;
    // 密码面板开着时不锁：用户正在输密码，锁掉只会让流程错乱
    const pwdOpen = ['password-panel', 'password-verify-panel'].some(id => {
      const el = document.getElementById(id);
      return el && el.style.display === 'flex';
    });
    if (pwdOpen) { _markActivity(); return; }
    if (Date.now() - _lastActivity >= _autoLockMinutes * 60 * 1000) _lockAllUnlocked();
  }, 60 * 1000);
  return () => { if (_autoLockTimer) { clearInterval(_autoLockTimer); _autoLockTimer = null; } };
}

// 面板里的下拉框：改完立刻生效并存库
const _autolockSel = $('#sel-autolock');
if (_autolockSel) {
  _autolockSel.addEventListener('change', async (e) => {
    _autoLockMinutes = parseInt(e.target.value, 10) || 0;
    try {
      await window.pywebview.api.settings_set('auto_lock_minutes', String(_autoLockMinutes));
    } catch (err) { /* 存不上也不影响本次会话生效 */ }
    _markActivity();
  });
}

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
    // 【注意】只给**加密**笔记记这个标记。`unlockedNotes` 的语义是"已解锁"，而后端能报出的
    // 解锁集合只含真正持有密钥的笔记 —— 明文笔记永远不在里面。
    // 以前这里对明文笔记也置 true，于是"后端说它没解锁"与"前端说它解锁了"长期打架：
    // 与后端对齐解锁状态的逻辑（reconcileUnlockState）每 30 秒就会把打开着的普通笔记
    // 当成"该锁了"，顺手清空 activeNoteId + 收起编辑区 —— 现象是"用着用着编辑区自己没了"。
    if (hasPwd) unlockedNotes[noteId] = true;
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
// 【注意】索引必须从**数据**里取，不能用 DOM 位置。窗口化渲染之后 `dom.noteList.children`
// 里夹着上下两个撑高块（`.note-list-spacer`），而且只包含**窗口内的十几行** ——
// 用 `[...children].indexOf(item)` 既会被撑高块偏移，跨屏拖动还会算出完全错误的落点。
// 行的 `data-note-id` 才是稳定标识，`state.notes` 的顺序才是真相。
function dataIndexOfItem(item) {
  if (!item) return -1;
  const id = item.dataset.noteId;
  return state.notes.findIndex(n => n.id === id);
}

let dragSrcIndex = null;

dom.noteList.addEventListener('dragstart', (e) => {
  const item = e.target.closest('.note-item');
  if (!item) return;
  dragSrcIndex = dataIndexOfItem(item);
  if (dragSrcIndex < 0) return;
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
  const dstIndex = dataIndexOfItem(item);
  if (dstIndex < 0 || dragSrcIndex === dstIndex) return;

  // 重新排列 state.notes（统一走 store）
  notesStore.move(dragSrcIndex, dstIndex);

  // 一次桥调用把新顺序写库（后端一个事务）。以前是 for 循环逐条 notes_update ——
  // 800 篇就是 800 次跨语言往返 + 800 个事务，拖一次卡一下；而本项目对批量动作的
  // 既有原则就是"一个桥调用"（见 12-bulk-actions.js 开头）。sort_order 的算法
  // （列表顶部取最大值）已经搬进后端 notes_reorder，与旧写法逐字等价。
  // 纯排序更新不 bump updated_at，拖拽后顺序才可持久。
  try {
    await window.pywebview.api.notes_reorder(state.notes.map(n => n.id));
  } catch (e) {
    console.error('保存排序失败:', e);
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

