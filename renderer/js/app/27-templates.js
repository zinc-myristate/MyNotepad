// ====== 模板抽屉（第 10 轮）======
// 模板就是**一段预置的 Markdown**：新建笔记时把它铺进去，变量（`{{date}}` 等）在那一刻
// 替换一次，之后它就是一篇普通笔记。刻意不做模板 DSL / 条件 / 循环——模板是用户手写的东西，
// 语法越少越不容易和正文本身打架（`{{...}}` 这种写法在笔记里本来就不常见）。
//
// 这里只做界面。替换规则、`{{title}}` 的取值、保存到数据库全在后端（`render_template`）：
// 预览走的是同一个 `template_render`，所以**预览与真建出来的笔记永远一致**，
// 不存在"前端自己拼一遍、后端再拼一遍"的分叉（状态栏/字数那一轮踩过这个坑）。
//
// 保存是**自动的**（输入停顿 450ms 落库，关抽屉前 flush）：模板只有几行字，
// 让用户记住"要点保存"是没有意义的负担。

import { $, state, showToast, showInputDialog, showConfirmAsync } from './01-core.js';
import { loadNotes, selectNote } from './03-notes.js';
import { applyMarkdownAction } from './17-markdown-actions.js';

let _templates = [];
let _activeId = null;
let _visible = false;
let _saveTimer = null;
let _previewTimer = null;
let _savedTimer = null;
let _pending = false;      // 有未落库的改动

function esc(s) {
  return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function api() {
  return window.pywebview.api;
}

/** 模板列表（捕获菜单也要用，所以单独导出） */
export async function listTemplates() {
  try {
    _templates = (await api().templates_list()) || [];
  } catch (e) {
    _templates = [];
  }
  return _templates;
}

function renderList() {
  const list = $('#tpl-list');
  if (!list) return;
  if (!_templates.length) {
    list.innerHTML = '<div class="tpl-empty">还没有模板。点「新建」写一个，'
      + '比如日记、会议记录、读书笔记。</div>';
    return;
  }
  list.innerHTML = _templates.map((t) => '<button class="tpl-chip'
    + (t.id === _activeId ? ' active' : '') + '" data-tpl="' + esc(t.id) + '" title="'
    + esc(t.name) + '">' + esc(t.name) + '</button>').join('');
}

function currentTemplate() {
  return _templates.find((t) => t.id === _activeId) || null;
}

function fillEditor(tpl) {
  const name = $('#tpl-name');
  const content = $('#tpl-content');
  if (name) name.value = tpl ? (tpl.name || '') : '';
  if (content) content.value = tpl ? (tpl.content || '') : '';
  const edit = $('.tpl-edit');
  if (edit) edit.classList.toggle('hidden', !tpl);
}

async function refreshPreview() {
  const pre = $('#tpl-preview');
  if (!pre) return;
  if (!_activeId) { pre.textContent = ''; return; }
  // 标题留空时按**模板名**渲染，与「用模板新建」的实际行为一致（那个输入框的占位文案
  // 就写着"留空 = 用模板名当标题"）；否则预览里 `{{title}}` 会是空的，看着像坏掉了
  const typed = ($('#tpl-title') && $('#tpl-title').value.trim()) || '';
  const tpl = currentTemplate();
  const title = typed || (tpl && tpl.name) || '';
  try {
    const text = await api().template_render(_activeId, title);
    pre.textContent = text || '（空模板）';
  } catch (e) {
    pre.textContent = '（预览失败）';
  }
}

function flashSaved() {
  const el = $('#tpl-saved');
  if (!el) return;
  el.textContent = '已保存';
  if (_savedTimer) clearTimeout(_savedTimer);
  _savedTimer = setTimeout(() => { el.textContent = ''; }, 1500);
}

async function saveActive() {
  if (!_activeId || !_pending) return;
  const id = _activeId;
  const fields = {
    name: (($('#tpl-name') && $('#tpl-name').value) || '').trim() || '未命名模板',
    content: ($('#tpl-content') && $('#tpl-content').value) || '',
  };
  _pending = false;
  try {
    const saved = await api().template_update(id, fields);
    const idx = _templates.findIndex((t) => t.id === id);
    if (idx >= 0 && saved) _templates[idx] = saved;
    renderList();
    flashSaved();
    await refreshPreview();
  } catch (e) {
    _pending = true;
    showToast('模板保存失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
}

/** 停手 450ms 落库（模板只有几行，比笔记的 500ms 略快一点） */
function scheduleSave() {
  _pending = true;
  if (_saveTimer) clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => { _saveTimer = null; saveActive(); }, 450);
}

async function flushSave() {
  if (_saveTimer) { clearTimeout(_saveTimer); _saveTimer = null; }
  await saveActive();
}

async function selectTemplate(id) {
  if (id === _activeId) return;
  await flushSave();
  _activeId = id;
  renderList();
  fillEditor(currentTemplate());
  await refreshPreview();
}

export async function refreshTemplates() {
  await listTemplates();
  if (!_templates.find((t) => t.id === _activeId)) _activeId = _templates.length ? _templates[0].id : null;
  renderList();
  fillEditor(currentTemplate());
  await refreshPreview();
}

async function createTemplate() {
  await flushSave();
  const name = await showInputDialog({
    title: '新建模板', message: '模板名称（之后可改）', defaultValue: '', okText: '创建',
  });
  if (name === null || name === undefined) return;
  try {
    const tpl = await api().template_create(String(name).trim() || '新模板', '');
    await refreshTemplates();
    if (tpl && tpl.id) {
      _activeId = tpl.id;
      renderList();
      fillEditor(currentTemplate());
      $('#tpl-content')?.focus();
    }
  } catch (e) {
    showToast('新建模板失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
}

async function deleteTemplate() {
  const tpl = currentTemplate();
  if (!tpl) return;
  const ok = await showConfirmAsync({
    title: '删除模板', message: '确定删除模板「' + (tpl.name || '') + '」？已用它建出的笔记不受影响。',
    okText: '删除', danger: true,
  });
  if (!ok) return;
  try {
    await api().template_delete(tpl.id);
    _activeId = null;
    await refreshTemplates();
    showToast('已删除模板', { type: 'info' });
  } catch (e) {
    showToast('删除失败：' + (e && e.message ? e.message : e), { type: 'error' });
  }
}

/** 用模板建一篇新笔记（捕获菜单与抽屉共用的唯一路径） */
export async function createNoteFromTemplate(templateId, title) {
  if (!templateId) { showToast('请先选一个模板', { type: 'warn' }); return null; }
  try {
    const note = await api().notes_create_from_template(templateId, title || null);
    if (!note || !note.id) throw new Error('创建失败');
    await loadNotes();
    await selectNote(note.id);
    toggleTemplates(false);
    showToast('已用模板新建「' + (note.title || '未命名笔记') + '」', { type: 'success' });
    return note;
  } catch (e) {
    showToast('用模板新建失败：' + (e && e.message ? e.message : e), { type: 'error' });
    return null;
  }
}

/** 把渲染后的模板插进当前笔记（只支持 Markdown 笔记：富文本没有"文本片段"这回事） */
async function insertIntoCurrent() {
  if (!_activeId) return;
  if (!state.activeNoteId) { showToast('请先打开一篇笔记', { type: 'warn' }); return; }
  if (state.noteFormat !== 'md') {
    showToast('富文本笔记请先点格式徽标转成 Markdown 再插入模板', { type: 'warn' });
    return;
  }
  const title = ($('#tpl-title') && $('#tpl-title').value.trim())
    || (state.notes.find((n) => n.id === state.activeNoteId) || {}).title || '';
  let text = '';
  try {
    text = await api().template_render(_activeId, title);
  } catch (e) {
    showToast('模板渲染失败', { type: 'error' });
    return;
  }
  if (!text) { showToast('这个模板是空的', { type: 'warn' }); return; }
  applyMarkdownAction('insert-text', { text });
  showToast('已插入模板', { type: 'success' });
}

function insertVar(token) {
  const ta = $('#tpl-content');
  if (!ta || !_activeId) return;
  const start = ta.selectionStart || 0;
  const end = ta.selectionEnd || 0;
  ta.value = ta.value.slice(0, start) + token + ta.value.slice(end);
  ta.selectionStart = ta.selectionEnd = start + token.length;
  ta.focus();
  scheduleSave();
}

export function toggleTemplates(force) {
  const drawer = $('#templates-drawer');
  if (!drawer) return;
  const next = force === undefined ? !_visible : !!force;
  if (next === _visible) return;
  _visible = next;
  if (_visible) {
    document.dispatchEvent(new CustomEvent('myapp:templates-opened'));
    refreshTemplates();
  } else {
    flushSave();
  }
  drawer.classList.toggle('hidden', !_visible);
}

export function openTemplates() {
  toggleTemplates(true);
}

export function initTemplates() {
  $('#templates-close')?.addEventListener('click', () => toggleTemplates(false));
  // 三个右侧抽屉互斥（与大纟/链接同一套自定义事件，避免模块间 import 成环）
  document.addEventListener('myapp:outline-opened', () => toggleTemplates(false));
  document.addEventListener('myapp:links-opened', () => toggleTemplates(false));
  $('#tpl-new')?.addEventListener('click', () => createTemplate());
  $('#tpl-delete')?.addEventListener('click', () => deleteTemplate());
  $('#tpl-create-note')?.addEventListener('click', () => {
    const title = ($('#tpl-title') && $('#tpl-title').value.trim()) || null;
    createNoteFromTemplate(_activeId, title);
  });
  $('#tpl-insert')?.addEventListener('click', () => insertIntoCurrent());
  $('#tpl-list')?.addEventListener('click', (ev) => {
    const chip = ev.target.closest && ev.target.closest('[data-tpl]');
    if (chip) selectTemplate(chip.getAttribute('data-tpl'));
  });
  $('#tpl-name')?.addEventListener('input', scheduleSave);
  $('#tpl-content')?.addEventListener('input', scheduleSave);
  // 标题只影响预览（它是 `{{title}}` 的取值），不落库。
  // 注意：用**独立**计时器——与保存共用 `_saveTimer` 的话，输完正文再改标题会把待保存的
  // 改动一起取消掉，正文编辑就悄悄丢了（只有下一次输入才会再触发保存）。
  $('#tpl-title')?.addEventListener('input', () => {
    if (_previewTimer) clearTimeout(_previewTimer);
    _previewTimer = setTimeout(() => { _previewTimer = null; refreshPreview(); }, 300);
  });
  document.querySelectorAll('.tpl-var').forEach((btn) => {
    btn.addEventListener('click', () => insertVar(btn.getAttribute('data-var') || ''));
  });
}
