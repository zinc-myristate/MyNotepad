// ====== 笔记操作 ======

async function loadNotes(retryCount = 0) {
  try {
    if (!window.pywebview || !window.pywebview.api) {
      if (retryCount < 10) {
        await new Promise(r => setTimeout(r, 500));
        return loadNotes(retryCount + 1);
      }
      return [];
    }
    state.notes = await window.pywebview.api.notes_list();
    // 防御：确保 is_pinned/is_favorite 字段存在且为数字（防止序列化变成字符串 "0"）
    state.notes = state.notes.map(n => ({ ...n, is_pinned: Number(n.is_pinned) || 0, is_favorite: Number(n.is_favorite) || 0 }));
    // 客户端排序双保险：置顶优先，同组按更新时间倒序
    state.notes.sort((a, b) => {
      if (a.is_pinned !== b.is_pinned) return b.is_pinned - a.is_pinned;
      return (b.updated_at || '').localeCompare(a.updated_at || '');
    });
    renderNoteList();
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

function renderNoteList() {
  dom.noteList.innerHTML = '';

  if (state.notes.length === 0) {
    dom.emptyHint.classList.remove('hidden');
  } else {
    dom.emptyHint.classList.add('hidden');
  }

  state.notes.forEach(note => {
    const item = document.createElement('div');
    item.className = `note-item${note.id === state.activeNoteId ? ' active' : ''}`;
    item.dataset.noteId = note.id;
    item.draggable = true;
    // SVG 图标定义
    const svgLock = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>';
    const svgPin = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M12 2v20M5 9h14l-3-7H8L5 9z"/><circle cx="12" cy="2" r="2"/></svg>';
    const svgStar = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
    const svgStarOutline = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
    const svgTrash = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>';
    const svgKey = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>';
    const pinIcon = note.is_pinned ? `<span class="note-status-icon pinned">${svgPin}</span>` : '';
    const favIcon = note.is_favorite ? `<span class="note-status-icon fav">${svgStar}</span>` : '';
    const lockIcon = note.has_password ? `<span class="note-status-icon locked">${svgLock}</span>` : '';
    // 生成封面
    const coverHtml = generateNoteCover(note);
    item.innerHTML = `
      <div class="note-item-row-cover">
        ${coverHtml}
        <div class="note-item-info">
          <div class="note-item-row">
            <span class="note-item-title">${escapeHtml(note.title || '未命名笔记')}</span>${pinIcon}${favIcon}${lockIcon}
          </div>
          <div class="note-item-meta">
            <span class="note-item-time">${(note.updated_at || '').substring(0, 16)}</span>
          </div>
        </div>
        <button class="note-item-password" title="设置密码" data-pwd-id="${note.id}">${svgKey}</button>
        <button class="note-item-pin" title="${note.is_pinned ? '取消置顶' : '置顶'}" data-pin-id="${note.id}">${svgPin}</button>
        <button class="note-item-fav" title="${note.is_favorite ? '取消收藏' : '收藏'}" data-fav-id="${note.id}">${note.is_favorite ? svgStar : svgStarOutline}</button>
        <button class="note-item-delete" title="删除笔记" data-delete-id="${note.id}">${svgTrash}</button>
      </div>
    `;

    // 置顶按钮
    item.querySelector('.note-item-pin').addEventListener('click', (e) => {
      e.stopPropagation();
      togglePinNote(note.id);
    });

    // 收藏按钮
    item.querySelector('.note-item-fav').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleFavoriteNote(note.id);
    });
    // 密码按钮
    item.querySelector('.note-item-password').addEventListener('click', async (e) => {
      e.stopPropagation();
      // 加密笔记需要先验证密码才能管理密码设置
      const hasPwd = await window.pywebview.api.note_has_password(note.id);
      if (hasPwd && !unlockedNotes[note.id]) {
        // 先弹出验证面板，验证成功后自动打开密码管理面板
        await flushSave();  // 旧笔记未保存内容先落盘，防止解锁后误存到加密笔记
        pendingSelectNoteId = note.id;
        state.activeNoteId = note.id;
        passwordVerifyCallback = () => {
          openPasswordPanel('set');
        };
        $('#password-verify-input').value = '';
        $('#password-error-msg').style.display = 'none';
        openPanel($('#password-verify-panel'));
        return;
      }
      // 无密码或已解锁：直接打开密码管理面板
      await selectNote(note.id);
      openPasswordPanel('set');
    });

    // 点击切换笔记（加密的需要验证密码）
    item.addEventListener('click', async (e) => {
      if (!e.target.closest('.note-item-delete, .note-item-pin, .note-item-fav')) {
        await verifyAndSelectNote(note.id);
      }
    });

    // 删除按钮
    item.querySelector('.note-item-delete').addEventListener('click', (e) => {
      e.stopPropagation();
      confirmDeleteNote(note.id, note.title);
    });

    dom.noteList.appendChild(item);
  });
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

async function selectNote(noteId) {
  if (state.activeNoteId === noteId) return;
  if (isVoiceRecording) stopVoiceRecording();
  // 重置版本/公式/背景缓存状态防止跨笔记错乱
  currentPreviewVersionId = null;
  editingMathNode = null;
  state._noteBgDataUri = null;
  await flushSave();

  // 加载新笔记（传递解锁状态）
  state.isLoading = true;
  try {
    const isUnlocked = unlockedNotes[noteId] === true;
    const note = await window.pywebview.api.notes_get(noteId, isUnlocked);
    if (!note) return;

    // 如果笔记已加密且未解锁，不加载内容，显示密码验证面板
    if (note.is_encrypted) {
      state.activeNoteId = note.id;
      state.currentContent = '';
      state.currentTitle = note.title || '';
      dom.titleInput.value = note.title || '';
      dom.titleInput.classList.remove('hidden');
      dom.titleInput.readOnly = true;  // 加密未解锁时禁止编辑标题
      document.querySelector('.ql-toolbar')?.classList.remove('hidden');
      dom.quillEditor.classList.remove('hidden');
      dom.noNoteHint.classList.add('hidden');
      if (state.quill) {
        state.quill.setContents([]);
        state.quill.enable(false);
      }
      updateNoteListItem(noteId);
      state.isLoading = false;
      // 弹出密码验证面板
      pendingSelectNoteId = noteId;
      passwordVerifyCallback = null;  // 默认行为：解锁后加载笔记即可
      $('#password-verify-input').value = '';
      $('#password-error-msg').style.display = 'none';
      openPanel($('#password-verify-panel'));
      return;
    }

    state.activeNoteId = note.id;
    state.currentContent = note.content || '';
    state.currentTitle = note.title || '';

    // 显示编辑器，隐藏空提示
    showEditorUI();

    // 设置标题
    dom.titleInput.value = note.title || '';
    dom.titleInput.readOnly = false;  // 解锁后允许编辑标题

    // 设置编辑器内容
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
}

async function createNewNote() {
  try {
    // 先保存当前笔记
    await saveCurrentNote();

    const note = await window.pywebview.api.notes_create();
    if (!note) return;

    // 用 loadNotes 全量刷新以确保置顶排序正确
    await loadNotes();
    updateNotebookCount();
    await selectNote(note.id);

    // 聚焦标题输入框
    dom.titleInput.focus();
    dom.titleInput.select();
  } catch (err) {
    console.error('创建笔记失败:', err);
    alert('创建笔记失败：' + err.message);
  }
}

async function saveCurrentNote() {
  if (!state.activeNoteId) return;
  if (state._saving) return;
  if (state.quill && !state.quill.isEnabled()) return; // 加密未解锁时编辑器禁用，防止空内容覆盖
  const note = state.notes.find(n => n.id === state.activeNoteId);
  if (note && note.has_password && !unlockedNotes[state.activeNoteId]) return;

  state._saving = true;
  try {
    // 保存前同步贴纸覆盖层位置到 Quill blot
    if (typeof syncStickersToQuill === 'function') syncStickersToQuill();

    const title = dom.titleInput.value.trim() || '未命名笔记';
    const content = state.quill ? JSON.stringify(state.quill.getContents()) : '';
    // 去重基线用 currentTitle/currentContent，不能用 state.notes（标题输入处理器为刷新列表
    // 已提前更新 state.notes[].title，拿它比较会误判"无变化"导致纯标题修改永不落库）
    if (title === state.currentTitle && content === state.currentContent) return;

    await window.pywebview.api.notes_update(state.activeNoteId, { title, content });
    // 保存成功后才更新基线：失败时基线不动，下次自动重试
    state.currentTitle = title;
    state.currentContent = content;
    const noteIdx = state.notes.findIndex(n => n.id === state.activeNoteId);
    if (noteIdx >= 0) {
      state.notes[noteIdx].title = title;
      state.notes[noteIdx].content = content;
    }
    updateNoteListItem(state.activeNoteId);
  } catch (err) {
    console.error('保存笔记失败:', err.message || err);
    reportError('保存笔记失败: ' + (err.message || err), err && err.stack, 'saveCurrentNote');
  } finally {
    state._saving = false;
  }
}

// 防抖自动保存：连续输入合并为一次写库；flushSave 在切换/失焦/锁定等时机立即落盘
const debouncedSave = debounce(() => saveCurrentNote(), 500);
async function flushSave() {
  debouncedSave.cancel();
  await saveCurrentNote();
}

// 窗口关闭前兜底保存（尽力而为）
window.addEventListener('beforeunload', () => {
  debouncedSave.cancel();
  saveCurrentNote();
});

function confirmDeleteNote(noteId, title) {
  showConfirm(`确定要删除笔记「${escapeHtml(title || '未命名笔记')}」吗？\n\n此操作不可恢复，笔记中的图片和附件也会被删除。`, async () => {
    await deleteNoteById(noteId);
  });
}

async function deleteNoteById(noteId) {
  try {
    const wasActive = state.activeNoteId === noteId;
    if (wasActive) debouncedSave.cancel(); // 取消待保存任务，防止删除后迟到写库

    await window.pywebview.api.notes_delete(noteId);
    state.notes = state.notes.filter(n => n.id !== noteId);

    if (wasActive) {
      // 先保存再清除状态
      state.activeNoteId = null;
      state.currentContent = '';
      state.currentTitle = '';

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
    alert('删除笔记失败：' + err.message);
  }
}

// 标题输入框事件 - 列表即时刷新 + 防抖持久化
dom.titleInput.addEventListener('input', () => {
  if (state.activeNoteId) {
    debouncedSave();
    const title = dom.titleInput.value.trim() || '未命名笔记';
    const noteIdx = state.notes.findIndex(n => n.id === state.activeNoteId);
    if (noteIdx >= 0) {
      state.notes[noteIdx].title = title;
      updateNoteListItem(state.activeNoteId);
    }
  }
});

// 手动保存按钮 + 提示
let saveIndicatorTimer = null;
function showSaveToast() {
  let toast = document.getElementById('save-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'save-toast';
    toast.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);padding:10px 24px;background:#38A169;color:#fff;border-radius:20px;font-size:14px;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(56,161,105,0.4);transition:all 0.3s ease;opacity:0;pointer-events:none;';
    document.body.appendChild(toast);
  }
  toast.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" style="vertical-align:middle;margin-right:6px;"><polyline points="20 6 9 17 4 12"/></svg>已保存';
  toast.style.opacity = '1';
  toast.style.transform = 'translateX(-50%) translateY(-10px)';
  if (saveIndicatorTimer) clearTimeout(saveIndicatorTimer);
  saveIndicatorTimer = setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(0)';
  }, 1500);
}

/** 通用 Toast 提示（无图标，自定义文字和颜色） */
function showToast(msg, bgColor) {
  var toast = document.getElementById('save-toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.id = 'save-toast';
    toast.style.cssText = 'position:fixed;bottom:30px;left:50%;transform:translateX(-50%);padding:10px 24px;color:#fff;border-radius:20px;font-size:14px;font-weight:600;z-index:9999;box-shadow:0 4px 16px rgba(0,0,0,0.3);transition:all 0.3s ease;opacity:0;pointer-events:none;';
    document.body.appendChild(toast);
  }
  toast.style.background = bgColor || '#38A169';
  toast.textContent = msg;
  toast.style.opacity = '1';
  toast.style.transform = 'translateX(-50%) translateY(-10px)';
  if (saveIndicatorTimer) clearTimeout(saveIndicatorTimer);
  saveIndicatorTimer = setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(0)';
  }, 2000);
}

$('#btn-save').addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  await flushSave();
  // 手动保存时自动创建历史版本
  const title = dom.titleInput.value.trim() || '未命名笔记';
  const content = state.quill ? JSON.stringify(state.quill.getContents()) : '';
  window.pywebview.api.versions_create(state.activeNoteId, title, content).catch(e => console.error('版本创建失败:', e));
  showSaveToast();
});

// 新建笔记按钮
dom.btnNewNote.addEventListener('click', createNewNote);

