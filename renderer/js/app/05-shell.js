// ====== 窗口关闭前保存 ======
let _intervals = [];
let _stickerSyncTimer = null;
window.addEventListener('beforeunload', async () => {
  _intervals.forEach(clearInterval);
  if (isVoiceRecording) stopVoiceRecording();
  if (state.activeNoteId && state.quill) {
    await saveCurrentNote();
  }
});

// ====== 键盘快捷键 ======
// 正在输入字段（标题/搜索/面板输入/Quill 编辑区）时，Ctrl+D/E 不接管（避免吃掉编辑器内的删除/其他默认行为）
function inTypingField() {
  const el = document.activeElement;
  if (!el) return false;
  if (el.isContentEditable) return true;
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
}

document.addEventListener('keydown', async (e) => {
  // Ctrl+N 新建笔记
  if (e.ctrlKey && e.key === 'n') {
    e.preventDefault();
    await createNewNote();
  }
  // Ctrl+S 手动保存
  if (e.ctrlKey && e.key === 's') {
    e.preventDefault();
    await flushSave();
  }
  // Ctrl+F 聚焦搜索框
  if (e.ctrlKey && e.key === 'f') {
    e.preventDefault();
    dom.searchInput.focus();
    dom.searchInput.select();
  }
  // Ctrl+D 删除当前笔记（输入字段内不接管）
  if (e.ctrlKey && e.key === 'd' && !inTypingField()) {
    e.preventDefault();
    if (state.activeNoteId) {
      const n = state.notes.find(x => x.id === state.activeNoteId);
      confirmDeleteNote(n ? n.id : state.activeNoteId, n && n.title);
    }
  }
  // Ctrl+E 导出（输入字段内不接管）
  if (e.ctrlKey && e.key === 'e' && !inTypingField()) {
    e.preventDefault();
    $('#btn-export').click();
  }
  // ESC 取消语音临时文字
  if (e.key === 'Escape' && isVoiceRecording) {
    e.preventDefault();
    clearVoiceTemp();
    stopVoiceRecording();
  }
});

// ====== 标签系统 ======
let currentTagFilter = null; // 当前筛选的标签 ID

async function loadTagBar() {
  if (!state.activeNoteId) { $('#tag-bar').classList.add('hidden'); return; }
  const tags = await window.pywebview.api.note_tags_get(state.activeNoteId);
  const tagList = $('#tag-list');
  tagList.innerHTML = '';
  tags.forEach(tag => {
    const chip = document.createElement('span');
    chip.className = 'tag-chip';
    chip.setAttribute('data-tag-color', tag.color || '');
    chip.innerHTML = `${escapeHtml(tag.name)}<span class="tag-remove" data-tag-id="${tag.id}">×</span>`;
    chip.querySelector('.tag-remove').addEventListener('click', async (e) => {
      e.stopPropagation();
      const current = await window.pywebview.api.note_tags_get(state.activeNoteId);
      const ids = current.filter(t => t.id !== tag.id).map(t => t.id);
      await window.pywebview.api.note_tags_set(state.activeNoteId, ids);
      loadTagBar();
      loadTagFilter();
    });
    tagList.appendChild(chip);
  });
  $('#tag-bar').classList.remove('hidden');
}

async function loadTagFilter() {
  const tags = await window.pywebview.api.tags_list();
  const container = $('#tag-filter-list');
  container.innerHTML = '';
  if (tags.length === 0) { $('#tag-filter').style.display = 'none'; return; }
  $('#tag-filter').style.display = 'block';
  tags.forEach(tag => {
    const chip = document.createElement('button');
    chip.className = 'tag-filter-chip';
    if (currentTagFilter === tag.id) chip.classList.add('active');
    chip.textContent = tag.name;
    chip.addEventListener('click', async () => {
      if (currentTagFilter === tag.id) {
        currentTagFilter = null;
        notesStore.setNotes(await window.pywebview.api.notes_list());
      } else {
        currentTagFilter = tag.id;
        notesStore.setNotes(await window.pywebview.api.notes_by_tag(tag.id));
      }
      renderNoteList();
      loadTagFilter();
    });
    container.appendChild(chip);
  });
}

$('#btn-clear-tag-filter').addEventListener('click', async () => {
  currentTagFilter = null;
  notesStore.setNotes(await window.pywebview.api.notes_list());
  renderNoteList();
  loadTagFilter();
});

$('#btn-add-tag').addEventListener('click', () => openTagPicker());
$('#btn-tag-manager').addEventListener('click', () => openTagManager());

async function openTagPicker() {
  const allTags = await window.pywebview.api.tags_list();
  const noteTags = await window.pywebview.api.note_tags_get(state.activeNoteId);
  const selectedIds = noteTags.map(t => t.id);
  const list = $('#tag-picker-list');
  list.innerHTML = '';
  allTags.forEach(tag => {
    const chip = document.createElement('button');
    chip.className = 'tag-picker-chip';
    if (selectedIds.includes(tag.id)) chip.classList.add('selected');
    chip.textContent = tag.name;
    chip.addEventListener('click', () => {
      chip.classList.toggle('selected');
    });
    list.appendChild(chip);
  });
  openPanel($('#tag-picker-panel'));
}

$('#btn-save-tags').addEventListener('click', async () => {
  const selected = [...$('#tag-picker-list').querySelectorAll('.selected')]
    .map(el => {
      const tagName = el.textContent;
      return window.pywebview.api.tags_list().then(tags => tags.find(t => t.name === tagName)?.id);
    });
  const ids = (await Promise.all(selected)).filter(Boolean);
  await window.pywebview.api.note_tags_set(state.activeNoteId, ids);
  closePanel($('#tag-picker-panel'));
  loadTagBar();
  loadTagFilter();
});

// 清除当前笔记全部标签
$('#btn-clear-all-tags').addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  await window.pywebview.api.note_tags_set(state.activeNoteId, []);
  closePanel($('#tag-picker-panel'));
  loadTagBar();
  loadTagFilter();
});

async function openTagManager() {
  const tags = await window.pywebview.api.tags_list();
  const list = $('#tag-manager-list');
  list.innerHTML = '';
  tags.forEach(tag => {
    const item = document.createElement('div');
    item.className = 'tag-manager-item';
    item.innerHTML = `<span class="tag-chip" data-tag-color="${tag.color || ''}">${escapeHtml(tag.name)}</span>
      <button class="btn-link" data-delete-tag="${tag.id}">删除</button>`;
    item.querySelector('[data-delete-tag]').addEventListener('click', async () => {
      if (await showConfirmAsync({ title: '删除标签', message: `确定删除标签「${tag.name}」？`, okText: '删除', danger: true })) {
        await window.pywebview.api.tags_delete(tag.id);
        openTagManager();
        loadTagFilter();
      }
    });
    list.appendChild(item);
  });
  openPanel($('#tag-manager-panel'));
}

$('#btn-create-tag').addEventListener('click', async () => {
  const name = $('#new-tag-input').value.trim();
  if (!name) return;
  await window.pywebview.api.tags_create(name);
  $('#new-tag-input').value = '';
  openTagManager();
  loadTagFilter();
});

// ====== 笔记本系统 ======
async function loadNotebooks() {
  const notebooks = await window.pywebview.api.notebooks_list();
  if (notebooks.length === 0) {
    await window.pywebview.api.notebooks_create('默认笔记本');
    return loadNotebooks();
  }
  // 渲染笔记本列表在侧边栏中
  // 简化实现：在搜索框下方显示笔记本选择
}

