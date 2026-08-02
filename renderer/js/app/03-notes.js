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
    // 归一化 is_pinned/is_favorite + 客户端排序双保险，统一走 store
    notesStore.setNotes(await window.pywebview.api.notes_list(), { normalize: true, sort: true });
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

  // DocumentFragment 批量挂载：消除每行多次插入引发的重排
  const frag = document.createDocumentFragment();
  state.notes.forEach(note => {
    const item = document.createElement('div');
    item.className = `note-item${note.id === state.activeNoteId ? ' active' : ''}`;
    item.dataset.noteId = note.id;
    item.draggable = true;
    // SVG 图标定义（统一取 icons.js，避免循环内重复字符串）
    const svgLock = ICONS.lock;
    const svgPin = ICONS.pin;
    const svgStar = ICONS.star;
    const svgStarOutline = ICONS['star-outline'];
    const svgTrash = ICONS.trash;
    const svgKey = ICONS.key;
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
            <span class="note-item-time">${(note.updated_at || '').substring(0, 16)}</span>
          </div>
        </div>
        <button class="note-item-password" title="设置密码" data-pwd-id="${note.id}">${svgKey}</button>
        <button class="note-item-pin" title="${note.is_pinned ? '取消置顶' : '置顶'}" data-pin-id="${note.id}">${svgPin}</button>
        <button class="note-item-fav" title="${note.is_favorite ? '取消收藏' : '收藏'}" data-fav-id="${note.id}">${note.is_favorite ? svgStar : svgStarOutline}</button>
        <button class="note-item-delete" title="删除笔记" data-delete-id="${note.id}">${svgTrash}</button>
      </div>
    `;
    frag.appendChild(item);
  });
  dom.noteList.appendChild(frag);
}

// 列表点击事件委托：单监听器代替每行 5 个，靠 data-* 属性路由
// （与 07 文件的 dragstart/dragover/drop 委托是不同事件类型，互不冲突）
dom.noteList.addEventListener('click', (e) => {
  const pin = e.target.closest('[data-pin-id]');
  if (pin) { e.stopPropagation(); togglePinNote(pin.dataset.pinId); return; }
  const fav = e.target.closest('[data-fav-id]');
  if (fav) { e.stopPropagation(); toggleFavoriteNote(fav.dataset.favId); return; }
  const pwd = e.target.closest('[data-pwd-id]');
  if (pwd) { e.stopPropagation(); handlePasswordButton(pwd.dataset.pwdId); return; }
  const del = e.target.closest('[data-delete-id]');
  if (del) {
    e.stopPropagation();
    const it = del.closest('.note-item');
    const title = it ? (it.querySelector('.note-item-title') || {}).textContent : '';
    confirmDeleteNote(del.dataset.deleteId, title);
    return;
  }
  const item = e.target.closest('.note-item');
  if (item) verifyAndSelectNote(item.dataset.noteId);
});

// 密码按钮逻辑（原每行内联处理器，委托后独立成函数）
async function handlePasswordButton(noteId) {
  // 加密笔记需要先验证密码才能管理密码设置
  const hasPwd = await window.pywebview.api.note_has_password(noteId);
  if (hasPwd && !unlockedNotes[noteId]) {
    // 先弹出验证面板，验证成功后自动打开密码管理面板
    await flushSave();  // 旧笔记未保存内容先落盘，防止解锁后误存到加密笔记
    pendingSelectNoteId = noteId;
    state.activeNoteId = noteId;
    passwordVerifyCallback = () => {
      openPasswordPanel('set');
    };
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
      setSaveDot('saved');  // 加密锁定态不闪
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
    setSaveDot('saved');

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
    showToast('创建笔记失败：' + (err.message || err), { type: 'error' });
  }
}

// ====== 保存状态小圆点（灰=已保存 / 主题色呼吸=未保存或保存中 / 红=保存失败） ======
function setSaveDot(s) {
  const d = document.getElementById('save-dot');
  if (!d) return;
  d.classList.toggle('dirty', s === 'dirty');
  d.classList.toggle('error', s === 'error');
  d.title = { saved: '已保存', dirty: '有未保存修改…', error: '保存失败（已记录日志）' }[s] || '';
}

async function saveCurrentNote() {
  // 串行化：撞上在途保存时先等它完成，再重新走去重与保存（不能直接跳过——
  // 期间可能有新输入；且旧保存完成后会更新基线，跳过会让新改动失去触发时机）
  if (state._savePromise) await state._savePromise;
  if (!state.activeNoteId) return;
  if (state.quill && !state.quill.isEnabled()) return; // 加密未解锁时编辑器禁用，防止空内容覆盖
  const noteId = state.activeNoteId;
  const note = state.notes.find(n => n.id === noteId);
  if (note && note.has_password && !unlockedNotes[noteId]) return;

  state._savePromise = (async () => {
    try {
      // 保存前同步贴纸覆盖层位置到 Quill blot
      if (typeof syncStickersToQuill === 'function') syncStickersToQuill();

      const title = dom.titleInput.value.trim() || '未命名笔记';
      const content = state.quill ? JSON.stringify(state.quill.getContents()) : '';
      // 去重基线用 currentTitle/currentContent，不能用 state.notes（标题输入处理器为刷新列表
      // 已提前更新 state.notes[].title，拿它比较会误判"无变化"导致纯标题修改永不落库）
      if (title === state.currentTitle && content === state.currentContent) {
        setSaveDot('saved');  // 改动被撤销回原状
        return;
      }

      await window.pywebview.api.notes_update(noteId, { title, content });
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
    } finally {
      state._savePromise = null;
    }
  })();
  return state._savePromise;
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

// 关窗兜底（主通道）：pywebview closing 事件里 Python 侧同步调用本函数取未存快照，
// 直接落库后放行关闭（不走 JS→Python 异步桥，避免关窗竞态丢最后 500ms 输入）。
// 必须保持同步、镜像 saveCurrentNote 的全部守卫。
window.__getUnsavedSnapshot = function () {
  try {
    if (!state.activeNoteId || !state.quill) return null;
    if (!state.quill.isEnabled()) return null;                      // 加密锁定态
    const note = state.notes.find(n => n.id === state.activeNoteId);
    if (note && note.has_password && !unlockedNotes[state.activeNoteId]) return null;
    if (typeof syncStickersToQuill === 'function') syncStickersToQuill();
    const title = dom.titleInput.value.trim() || '未命名笔记';
    const content = JSON.stringify(state.quill.getContents());
    if (title === state.currentTitle && content === state.currentContent) return null; // 无未存改动
    return JSON.stringify({ noteId: state.activeNoteId, title: title, content: content });
  } catch (e) {
    return null;
  }
};

async function confirmDeleteNote(noteId, title) {
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
  toast.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" style="vertical-align:middle;margin-right:6px;"><polyline points="20 6 9 17 4 12"/></svg>已保存';
  toast.style.opacity = '1';
  toast.style.transform = 'translateX(-50%) translateY(-10px)';
  if (saveIndicatorTimer) clearTimeout(saveIndicatorTimer);
  saveIndicatorTimer = setTimeout(function() {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(-50%) translateY(0)';
  }, 1500);
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

