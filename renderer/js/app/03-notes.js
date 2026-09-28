// ====== 笔记操作 ======

// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { $, dom, hideEditorUI, openPanel, reportError, saveIndicatorTimer, setSaveIndicatorTimer, showConfirmAsync, showEditorUI, showToast, state } from './01-core.js';
import {
  getMarkdownContent, setMarkdownContent, setMarkdownReadOnly, setMarkdownVisible,
} from './13-markdown-editor.js';
import { syncFormatBadge } from './15-note-format.js';
import { updateStatusBar } from './21-status-bar.js';
import { refreshOutline } from './22-outline.js';
import { refreshFindIfOpen } from './23-find-bar.js';
import { refreshPropBar } from './24-properties.js';
import { refreshLinksIfOpen } from './25-links.js';
import { notesStore } from './01b-store.js';
import { isVoiceRecording, stopVoiceRecording, toggleFavoriteNote, togglePinNote } from './02-editor.js';
import { applyNoteBackground } from './04-appearance.js';
import { loadTagBar } from './05-shell.js';
import { setCurrentPreviewVersionId } from './06-versions-reminders.js';
import { openPasswordPanel, setEditingMathNode, setPasswordVerifyCallback, setPendingSelectNoteId, unlockedNotes, updateLockButton, verifyAndSelectNote } from './07-formula-security-dnd.js';
import { generateNoteCover, loadPaperForNote } from './08-appearance2.js';
import { getCurrentNotebookId, applySearchToDom, refreshNotebookCounts, updateNotebookCount } from './09-boot.js';
import { listItems, renderWindow, initVirtualList } from './30-virtual-list.js';
import { syncStickersToOverlay, syncStickersToQuill } from '../quill/quill-deco.js';
import { ICONS } from '../shared/icons.js';
import { clearSelection, extendSelectionTo, isMultiSelecting, toggleSelection } from './12-bulk-actions.js';
import { debounce, escapeHtml } from '../shared/utils.js';

export async function loadNotes(retryCount = 0) {
  try {
    if (!window.pywebview || !window.pywebview.api) {
      if (retryCount < 10) {
        await new Promise(r => setTimeout(r, 500));
        return loadNotes(retryCount + 1);
      }
      return [];
    }
    // 归一化 is_pinned/is_favorite + 客户端排序双保险，统一走 store。
    // 视角范围（笔记本）**由后端过滤**：列表永远只回当前那一本 ——
    // 复制 / 回收站恢复 / 待办勾选 / OCR 落库 / 模板新建… 20+ 条刷新路径都不会再把范围冲成全量。
    notesStore.setNotes(await window.pywebview.api.notes_list(getCurrentNotebookId()),
                        { normalize: true, sort: true });
    renderNoteList();
    await refreshNotebookCounts();   // 下拉里的「N 篇」只能由后端数（前端手里只有当前这一本）
    return state.notes;
  } catch (err) {
    if (retryCount < 10) {
      await new Promise(r => setTimeout(r, 500));
      return loadNotes(retryCount + 1);
    }
    console.error('加载笔记列表失败:', err);
    return [];
  }
}

/** 列表项里那一行摘要的 HTML（三种来源：搜索命中片段 > 正文摘要 > 加密占位）。

 * 安全：所有文本都先转义再拼 `innerHTML`；搜索高亮不能用「先转义整段再找关键词」的做法
 * （关键词含 < & 时会错位），而是按关键词切成多段、逐段转义后再插 <mark>。
 */
export function previewHtmlFor(note) {
  if (!note) return '';
  const q = state.searchQuery || '';
  const snip = (q && state.searchSnippets) ? state.searchSnippets[note.id] : '';
  if (snip) return highlightHtml(snip, q);
  if (note.has_password) {
    return '<span class="note-item-preview-locked">已加密 · 解锁后可见</span>';
  }
  const text = note.preview || '';
  return text ? escapeHtml(text) : '<span class="note-item-preview-empty">（空白笔记）</span>';
}

function highlightHtml(text, q) {
  const ql = (q || '').toLowerCase();
  if (!ql) return escapeHtml(text);
  const lower = String(text).toLowerCase();
  let out = '', i = 0;
  for (;;) {
    const at = lower.indexOf(ql, i);
    if (at < 0) { out += escapeHtml(String(text).slice(i)); break; }
    out += escapeHtml(String(text).slice(i, at)) +
           '<mark>' + escapeHtml(String(text).slice(at, at + ql.length)) + '</mark>';
    i = at + ql.length;
  }
  return out;
}

/** 建一行笔记 DOM（窗口化渲染会给每个可见行调一次）。 */
function buildNoteItem(note) {
  const item = document.createElement('div');
  item.className = `note-item${note.id === state.activeNoteId ? ' active' : ''}` +
    (state.selectedIds.has(note.id) ? ' selected' : '');
  item.dataset.noteId = note.id;
  item.draggable = true;
  // SVG 图标定义（统一取 icons.js，避免循环内重复字符串）
  const svgLock = ICONS.lock;
  const svgPin = ICONS.pin;
  const svgStar = ICONS.star;
  const svgStarOutline = ICONS['star-outline'];
  const svgTrash = ICONS.trash;
  const svgKey = ICONS.key;
  const svgCopy = ICONS.copy;
  const pinIcon = note.is_pinned ? `<span class="note-status-icon pinned">${svgPin}</span>` : '';
  const favIcon = note.is_favorite ? `<span class="note-status-icon fav">${svgStar}</span>` : '';
  const lockIcon = note.has_password ? `<span class="note-status-icon locked">${svgLock}</span>` : '';
  // 生成封面
  const coverHtml = generateNoteCover(note);
  // 事件统一走 #note-list 容器委托（见下方），元素只保留 data-* 路由属性
  item.innerHTML = `
    <div class="note-item-row-cover">
      ${coverHtml}
      <div class="note-item-info">
        <div class="note-item-row">
          <span class="note-item-title">${escapeHtml(note.title || '未命名笔记')}</span>${pinIcon}${favIcon}${lockIcon}
        </div>
        <div class="note-item-meta">
          <span class="note-item-preview">${previewHtmlFor(note)}</span>
          <span class="note-item-time">${(note.updated_at || '').substring(0, 16)}</span>
        </div>
      </div>
      <button class="note-item-password" title="设置密码" data-pwd-id="${note.id}">${svgKey}</button>
      <button class="note-item-copy" title="复制这篇笔记" data-copy-id="${note.id}">${svgCopy}</button>
      <button class="note-item-pin" title="${note.is_pinned ? '取消置顶' : '置顶'}" data-pin-id="${note.id}">${svgPin}</button>
      <button class="note-item-fav" title="${note.is_favorite ? '取消收藏' : '收藏'}" data-fav-id="${note.id}">${note.is_favorite ? svgStar : svgStarOutline}</button>
      <button class="note-item-delete" title="删除笔记" data-delete-id="${note.id}">${svgTrash}</button>
    </div>
  `;
  return item;
}

/** 渲染列表（窗口化：只建看得见的那十几行 + 缓冲）。
 *
 *  以前这里是"清空 + 遍历 state.notes 全量建行"，2000 篇实测 496ms / 54,000 个节点。
 *  现在交给 `30-virtual-list.js` 算窗口、只建窗口内的行，上下用撑高块保持滚动条长度。
 *  【注意】搜索过滤**不在这里**做：窗口必须基于"过滤后的集合"来算，否则被隐藏的行会留下
 *  大片空白（撑高块把它们也算进去了）。过滤结果由 `applySearchToDom()` 写进
 *  `state.searchMatched`，`listItems()` 据此取集合。
 */
export function renderNoteList() {
  const items = listItems();
  if (items.length === 0) {
    dom.noteList.innerHTML = '';
  }
  if (state.notes.length === 0) {
    dom.emptyHint.classList.remove('hidden');
  } else {
    dom.emptyHint.classList.add('hidden');
  }
  renderWindow((note) => buildNoteItem(note), true);
}

// 封面图加载失败 → 退回首字。走**委托 + 捕获阶段**：
//   · 图片的 error 事件**不冒泡**，所以要 `capture: true` 才能在容器上接到；
//   · 用委托而不是给每个 <img> 绑监听：列表是整表重建的（renderNoteList），
//     逐个绑既要在重建后重绑、也容易漏。
// 这样封面就不需要内联 `onerror=` 了（以前那串还把手写标题拼进了 JS 字符串里）。
dom.noteList.addEventListener('error', (e) => {
  const img = e.target;
  if (!img || img.tagName !== 'IMG' || !img.dataset || img.dataset.coverFallback === undefined) return;
  const holder = img.parentElement;
  img.style.display = 'none';
  if (holder && !holder.dataset.coverFellBack) {
    holder.dataset.coverFellBack = '1';
    holder.textContent = img.dataset.coverFallback || '';
  }
}, true);

// 列表点击事件委托：单监听器代替每行 5 个，靠 data-* 属性路由
// （与 07 文件的 dragstart/dragover/drop 委托是不同事件类型，互不冲突）
dom.noteList.addEventListener('click', (e) => {
  const pin = e.target.closest('[data-pin-id]');
  if (pin) { e.stopPropagation(); togglePinNote(pin.dataset.pinId); return; }
  const fav = e.target.closest('[data-fav-id]');
  if (fav) { e.stopPropagation(); toggleFavoriteNote(fav.dataset.favId); return; }
  const pwd = e.target.closest('[data-pwd-id]');
  if (pwd) { e.stopPropagation(); handlePasswordButton(pwd.dataset.pwdId); return; }
  const dup = e.target.closest('[data-copy-id]');
  if (dup) { e.stopPropagation(); duplicateNote(dup.dataset.copyId); return; }
  const del = e.target.closest('[data-delete-id]');
  if (del) {
    e.stopPropagation();
    const it = del.closest('.note-item');
    const title = it ? (it.querySelector('.note-item-title') || {}).textContent : '';
    confirmDeleteNote(del.dataset.deleteId, title);
    return;
  }
  const item = e.target.closest('.note-item');
  if (!item) return;
  const id = item.dataset.noteId;
  // 多选：Ctrl 点加/减选，Shift 连选，普通点击退出多选态再打开
  if (e.ctrlKey || e.metaKey) { toggleSelection(id); return; }
  if (e.shiftKey) { extendSelectionTo(id); return; }
  if (isMultiSelecting()) clearSelection();
  verifyAndSelectNote(id);
});

/** 复制一篇笔记：正文/标签/笔记本/外观 + 附件文件一起复制（后端 notes_duplicate）。
 * 加密笔记在锁定态会被后端拒绝——那种情况给出明确解释，而不是静默失败。 */
async function duplicateNote(noteId) {
  try {
    const dup = await window.pywebview.api.notes_duplicate(noteId);
    if (!dup) {
      showToast('无法复制：加密笔记需要先解锁', { type: 'warn' });
      return;
    }
    await loadNotes();                      // 副本会像新笔记一样排在最前
    showToast('已复制为「' + (dup.title || '副本') + '」', { type: 'success' });
  } catch (err) {
    showToast('复制失败：' + (err.message || err), { type: 'error' });
  }
}

// 密码按钮逻辑（原每行内联处理器，委托后独立成函数）
async function handlePasswordButton(noteId) {  // 加密笔记需要先验证密码才能管理密码设置
  const hasPwd = await window.pywebview.api.note_has_password(noteId);
  if (hasPwd && !unlockedNotes[noteId]) {
    // 先弹出验证面板，验证成功后自动打开密码管理面板
    await flushSave();  // 旧笔记未保存内容先落盘，防止解锁后误存到加密笔记
    setPendingSelectNoteId(noteId);
    state.activeNoteId = noteId;
    setPasswordVerifyCallback(() => {
      openPasswordPanel('set');
    });
    $('#password-verify-input').value = '';
    $('#password-error-msg').style.display = 'none';
    openPanel($('#password-verify-panel'));
    return;
  }
  // 无密码或已解锁：直接打开密码管理面板
  await selectNote(noteId);
  openPasswordPanel('set');
}

function updateNoteListItem(noteId) {
  const note = state.notes.find(n => n.id === noteId);
  if (!note) return;

  // 更新列表中的标题和封面
  const item = dom.noteList.querySelector(`[data-note-id="${noteId}"]`);
  if (item) {
    const titleSpan = item.querySelector('.note-item-title');
    if (titleSpan) {
      titleSpan.textContent = note.title || '未命名笔记';
    }
    // 封面首字跟随标题实时更新（图片封面除外）
    if (note.cover_type !== 'image') {
      const coverEl = item.querySelector('.note-cover');
      if (coverEl && !coverEl.querySelector('img')) {
        coverEl.textContent = (note.title || '笔')[0];
      }
    }
  }

  // 更新活跃状态
  dom.noteList.querySelectorAll('.note-item').forEach(el => {
    el.classList.toggle('active', el.dataset.noteId === noteId);
  });
}

// selectNote 的请求序号（见函数内说明）：只有最后一次选择的结果允许写界面
let _selectSeq = 0;
// saveCurrentNote 的串行队列尾（见函数内说明）。放模块内部而不是 state 上：
// state 的字段是给界面/探针读的，一条 promise 链不属于那个范畴。
let _saveChain = null;

export async function selectNote(noteId) {
  if (state.activeNoteId === noteId) return;
  // 请求序号：连点两篇笔记时两个 selectNote 会并发（开头的 activeNoteId 判断拦不住 ——
  // 那时它还没被赋值），后发先至就会让**迟到的旧响应**去 setContents，把新笔记的内容盖掉，
  // 而用户在切换窗口里的按键也随之丢失。同一份能力在 09-boot.js 的 _searchSeq 里已有正确写法。
  //
  // 序号必须在这里、在**第一个 await 之前**同步领取：await 处会让出执行权，
  // 若把 `++_selectSeq` 放在 await flushSave() 之后，先发起的调用可能后恢复执行、
  // 反而领到更大的序号 —— 迟到的旧请求就被当成"最新"，守卫形同虚设。
  const seq = ++_selectSeq;
  if (isVoiceRecording) stopVoiceRecording();
  // 重置版本/公式/背景缓存状态防止跨笔记错乱
  setCurrentPreviewVersionId(null);
  setEditingMathNode(null);
  state._noteBgDataUri = null;
  await flushSave();
  if (seq !== _selectSeq) return;   // flush 期间又切了别的笔记

  // 加载新笔记（传递解锁状态）
  state.isLoading = true;
  try {
    const isUnlocked = unlockedNotes[noteId] === true;
    const note = await window.pywebview.api.notes_get(noteId, isUnlocked);
    if (seq !== _selectSeq) return;   // 已经有更新的选择，这次的结果作废
    if (!note) return;

    // 如果笔记已加密且未解锁，不加载内容，显示密码验证面板
    if (note.is_encrypted) {
      state.activeNoteId = note.id;
      state.noteFormat = note.format === 'md' ? 'md' : 'delta';
      state.currentContent = '';
      state.currentTitle = note.title || '';
      setSaveDot('saved');  // 加密锁定态不闪
      dom.titleInput.value = note.title || '';
      dom.titleInput.classList.remove('hidden');
      dom.titleInput.readOnly = true;  // 加密未解锁时禁止编辑标题
      if (state.noteFormat === 'md') {
        // 锁定态：显示空的只读源码栏，别让用户以为内容丢了
        setMarkdownVisible(true);
        setMarkdownContent('');
        setMarkdownReadOnly(true);
        document.querySelector('.ql-toolbar')?.classList.add('hidden');
        dom.quillEditor.classList.add('hidden');
      } else {
        document.querySelector('.ql-toolbar')?.classList.remove('hidden');
        dom.quillEditor.classList.remove('hidden');
        if (state.quill) {
          state.quill.setContents([]);
          state.quill.enable(false);
        }
      }
      dom.noNoteHint.classList.add('hidden');
      updateNoteListItem(noteId);
      state.isLoading = false;
      // 弹出密码验证面板
      setPendingSelectNoteId(noteId);
      setPasswordVerifyCallback(null);  // 默认行为：解锁后加载笔记即可
      $('#password-verify-input').value = '';
      $('#password-error-msg').style.display = 'none';
      openPanel($('#password-verify-panel'));
      return;
    }

    state.activeNoteId = note.id;
    state.noteFormat = note.format === 'md' ? 'md' : 'delta';
    state.currentContent = note.content || '';
    state.currentTitle = note.title || '';
    setSaveDot('saved');

    // 显示编辑器，隐藏空提示（按格式决定显示 Quill 还是 Markdown 双栏）
    showEditorUI();

    // 设置标题
    dom.titleInput.value = note.title || '';
    dom.titleInput.readOnly = false;  // 解锁后允许编辑标题

    syncFormatBadge();
    // 设置编辑器内容：按 format 分流，这是双轨唯一的分叉点
    if (state.noteFormat === 'md') {
      setMarkdownVisible(true);
      setMarkdownReadOnly(false);
      setMarkdownContent(note.content || '');
    } else {
      setMarkdownVisible(false);
      if (state.quill) {
        state.quill.enable(true);
        if (note.content) {
          // Quill 内容可能是 HTML 或 Delta
          try {
            const delta = JSON.parse(note.content);
            state.quill.setContents(delta);
          } catch {
            // 如果不是 JSON，作为 HTML 处理
            state.quill.setText(note.content);
          }
        } else {
          state.quill.setContents([]);
        }
      }
    }

    // 加载标签
    loadTagBar();
    // 应用笔记背景
    applyNoteBackground(note);
    // 应用纸张样式
    loadPaperForNote(note);
  } catch (err) {
    console.error('加载笔记失败:', err);
  }
  state.isLoading = false;

  // 同步贴纸到浮动覆盖层
  if (typeof syncStickersToOverlay === 'function') {
    setTimeout(() => syncStickersToOverlay(), 100);
  }
  updateLockButton();

  updateNoteListItem(noteId);
  updateStatusBar();     // 切笔记时数字必须立刻跟着变，不能等下一次输入
  refreshOutline();
  refreshFindIfOpen();   // 查找条开着时，旧笔记的高亮/下标必须重算（正文已经换了）
  refreshPropBar();
  refreshLinksIfOpen();  // 链接抽屉开着才查库（每切一次笔记都问一遍后端没必要）
}

/** 强制重载当前笔记（格式转换后必须走它：selectNote 对"同一篇"会直接 return） */
export async function reloadActiveNote() {
  const id = state.activeNoteId;
  if (!id) return;
  state.activeNoteId = null;
  await loadNotes();
  await selectNote(id);
}

export async function createNewNote() {
  try {
    // 先保存当前笔记
    await saveCurrentNote();

    // 新建的笔记落进**当前笔记本**（在「原神」里点新建就进原神，而不是混到未分类里）；
    // 「全部笔记」视角下没有"当前笔记本"可言 → 传 null，落未分类（与旧行为一致）
    const note = await window.pywebview.api.notes_create(getCurrentNotebookId());
    if (!note) return;

    // 用 loadNotes 全量刷新以确保置顶排序正确（它自带当前笔记本范围，不会把筛选冲掉）
    await loadNotes();
    await selectNote(note.id);

    // 聚焦标题输入框
    dom.titleInput.focus();
    dom.titleInput.select();
  } catch (err) {
    console.error('创建笔记失败:', err);
    showToast('创建笔记失败：' + (err.message || err), { type: 'error' });
  }
}

/** 取当前编辑器里的正文 —— **唯一**读正文的地方（保存、关窗快照、手动保存都走它）。
 *  Markdown 笔记存原始文本，Delta 笔记存序列化后的 Delta JSON。 */
function currentEditorContent() {
  if (state.noteFormat === 'md') return getMarkdownContent();
  return state.quill ? JSON.stringify(state.quill.getContents()) : '';
}

// ====== 保存状态小圆点（灰=已保存 / 主题色呼吸=未保存或保存中 / 红=保存失败） ======
export function setSaveDot(s) {
  const d = document.getElementById('save-dot');
  if (!d) return;
  d.classList.toggle('dirty', s === 'dirty');
  d.classList.toggle('error', s === 'error');
  d.title = { saved: '已保存', dirty: '有未保存修改…', error: '保存失败（已记录日志）' }[s] || '';
}

export async function saveCurrentNote() {
  // 串行化（队列式）：把每次保存挂到上一条的尾巴上，保证**同一时刻只有一条写库在飞**。
  //
  // 为什么不能只写 `if (state._savePromise) await state._savePromise`：
  // 那个写法在并发调用 ≥3 个时就会失效 —— 第 2、3 个调用者会同时等在**同一个** promise 上，
  // 醒来后各自覆盖 state._savePromise；而先前那条 promise 的 finally 会把**正在飞行中**的
  // 引用清成 null，于是第 4 个调用者看到 null 就直接并发发起写库。
  // 两条请求各自读的是自己开始时刻的编辑器内容，桥接返回顺序不保证 ⇒ 旧内容可能后落库。
  // 触发点很多：500ms 防抖、编辑器 blur、切笔记 flushSave、Ctrl+S、关窗快照。
  const run = async () => {
    if (!state.activeNoteId) return;
    // 加密未解锁时编辑器禁用，防止空内容覆盖（Markdown 笔记走只读源码，不存在这个状态）
    if (state.noteFormat !== 'md' && state.quill && !state.quill.isEnabled()) return;
    const noteId = state.activeNoteId;
    const note = state.notes.find(n => n.id === noteId);
    if (note && note.has_password && !unlockedNotes[noteId]) return;

    try {
      // 保存前同步贴纸覆盖层位置到 Quill blot
      if (typeof syncStickersToQuill === 'function') syncStickersToQuill();

      const title = dom.titleInput.value.trim() || '未命名笔记';
      const content = currentEditorContent();
      // 去重基线用 currentTitle/currentContent，不能用 state.notes（标题输入处理器为刷新列表
      // 已提前更新 state.notes[].title，拿它比较会误判"无变化"导致纯标题修改永不落库）
      if (title === state.currentTitle && content === state.currentContent) {
        setSaveDot('saved');  // 改动被撤销回原状
        return;
      }

      // 返回值必须判空：后端在「笔记不在库/已被删进回收站」「没有可更新字段」
      // 「加密笔记未解锁 → 剥掉 content 后没有别的字段」这三种情况下返回 None。
      // 桥接层成功时回的是非空回执（{id, title, updated_at, format}），所以能区分。
      // 不判的后果：走到 None 分支时基线与小圆点照样前进 → 这次改动**永久丢失**
      // （去重基线已经当成"存过了"，下次输入才会再触发一次比较），而界面显示"已保存"。
      const ack = await window.pywebview.api.notes_update(noteId, { title, content });
      if (!ack) throw new Error('后端拒绝了这次保存');
      // 保存成功后才更新基线：失败时基线不动，下次自动重试。
      // 期间若已切换到其他笔记（在途保存的 await 期间 selectNote 完成），
      // 迟到的保存只落库、不得用旧值覆盖新笔记的 currentTitle/currentContent 基线
      if (state.activeNoteId === noteId) {
        state.currentTitle = title;
        state.currentContent = content;
        notesStore.updateFields(noteId, { title, content });
        updateNoteListItem(noteId);
        setSaveDot('saved');
      }
    } catch (err) {
      console.error('保存笔记失败:', err.message || err);
      reportError('保存笔记失败: ' + (err.message || err), err && err.stack, 'saveCurrentNote');
      setSaveDot('error');
    }
  };
  // 队列尾：上一条无论成功失败都要继续往下走（run 内部已吞掉异常）
  const p = (_saveChain || Promise.resolve()).then(run, run);
  _saveChain = p;
  return p;
}

// 防抖自动保存：连续输入合并为一次写库；flushSave 在切换/失焦/锁定等时机立即落盘
export const debouncedSave = debounce(() => saveCurrentNote(), 500);
export async function flushSave() {
  debouncedSave.cancel();
  await saveCurrentNote();
}

// 窗口关闭前兜底保存（尽力而为）
window.addEventListener('beforeunload', () => {
  debouncedSave.cancel();
  saveCurrentNote();
});

// 关窗兜底（主通道）：pywebview closing 事件里 Python 侧同步调用本函数取未存快照，
// 直接落库后放行关闭（不走 JS→Python 异步桥，避免关窗竞态丢最后 500ms 输入）。
// 必须保持同步、镜像 saveCurrentNote 的全部守卫。
window.__getUnsavedSnapshot = function () {
  try {
    if (!state.activeNoteId) return null;
    if (state.noteFormat !== 'md') {
      if (!state.quill) return null;
      if (!state.quill.isEnabled()) return null;                    // 加密锁定态
    }
    const note = state.notes.find(n => n.id === state.activeNoteId);
    if (note && note.has_password && !unlockedNotes[state.activeNoteId]) return null;
    if (state.noteFormat !== 'md' && typeof syncStickersToQuill === 'function') syncStickersToQuill();
    const title = dom.titleInput.value.trim() || '未命名笔记';
    const content = currentEditorContent();
    if (title === state.currentTitle && content === state.currentContent) return null; // 无未存改动
    return JSON.stringify({ noteId: state.activeNoteId, title: title, content: content });
  } catch (e) {
    return null;
  }
};

export async function confirmDeleteNote(noteId, title) {
  const ok = await showConfirmAsync({
    title: '删除笔记',
    message: `确定要删除笔记「${title || '未命名笔记'}」吗？\n\n笔记将移入回收站，可在回收站中恢复。`,
    okText: '删除',
    danger: true
  });
  if (ok) await deleteNoteById(noteId);
}

async function deleteNoteById(noteId) {
  try {
    const wasActive = state.activeNoteId === noteId;
    if (wasActive) debouncedSave.cancel(); // 取消待保存任务，防止删除后迟到写库

    await window.pywebview.api.notes_delete(noteId);
    notesStore.remove(noteId);

    if (wasActive) {
      // 先保存再清除状态
      state.activeNoteId = null;
      state.currentContent = '';
      state.currentTitle = '';
      setSaveDot('saved');

      if (state.quill) {
        state.quill.setContents([]);
      }
      dom.titleInput.value = '';
      hideEditorUI();

      // 清除笔记背景
      dom.noteBgLayer.style.backgroundImage = '';
      dom.noteBgLayer.style.opacity = '';

      // 自动选择第一篇笔记
      if (state.notes.length > 0) {
        await selectNote(state.notes[0].id);
      }
    }

    renderNoteList();
    updateNotebookCount();
  } catch (err) {
    console.error('删除笔记失败:', err);
    reportError('删除笔记失败: ' + (err.message || err), err && err.stack, 'deleteNoteById');
    showToast('删除笔记失败：' + (err.message || err), { type: 'error' });
  }
}

// 标题输入框事件 - 列表即时刷新 + 防抖持久化
dom.titleInput.addEventListener('input', () => {
  if (state.activeNoteId) {
    setSaveDot('dirty');
    debouncedSave();
    const title = dom.titleInput.value.trim() || '未命名笔记';
    if (notesStore.updateFields(state.activeNoteId, { title })) {
      updateNoteListItem(state.activeNoteId);
    }
  }
});

// 手动保存按钮 + 提示（saveIndicatorTimer 由 01-core.js 统一声明，避免全局 let 重复）
function showSaveToast() {
  let toast = document.getElementById('save-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'save-toast';
    toast.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);padding:10px 24px;background:#38A169;color:#fff;border-radius:20px;font-size:14px;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(56,161,105,0.4);transition:all 0.3s ease;opacity:0;pointer-events:none;';
    document.body.appendChild(toast);
  }
  toast.innerHTML = ICONS['saved-check'] + '已保存';
  toast.style.opacity = '1';
  toast.style.transform = 'translateX(-50%) translateY(-10px)';
  if (saveIndicatorTimer) clearTimeout(saveIndicatorTimer);
  setSaveIndicatorTimer(setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(0)';
  }, 1500));
}

$('#btn-save').addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  await flushSave();
  // 手动保存时自动创建历史版本。
  // 取正文必须走 currentEditorContent()（它按 state.noteFormat 分流）：这个按钮在 md 笔记下
  // 是**隐藏**的（Quill 工具栏整体 .hidden），但命令面板的 Ctrl+P `>` 模式用
  // `getElementById(id).click()` 触发 —— 隐藏元素照样会被点到。那时 Quill 里留着的还是
  // 上一篇富文本笔记的内容（selectNote 的 md 分支根本不动 Quill），于是会把**别的笔记**
  // 的内容存成本篇的历史版本。格式守卫在这里，命令面板那条路就自然安全了。
  const title = dom.titleInput.value.trim() || '未命名笔记';
  const content = currentEditorContent();
  window.pywebview.api.versions_create(state.activeNoteId, title, content).catch(e => console.error('版本创建失败:', e));
  showSaveToast();
});

// 新建笔记按钮
dom.btnNewNote.addEventListener('click', createNewNote);

