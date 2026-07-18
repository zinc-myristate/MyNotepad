// ====== 笔记本管理 ======
let currentNotebookId = null; // null = 全部笔记

/** 同步更新笔记本计数徽章（轻量，无需 API 调用） */
function updateNotebookCount() {
  const countEl = document.getElementById('notebook-count');
  if (!countEl) return;
  if (currentNotebookId === null) {
    countEl.textContent = '(' + state.notes.length + ')';
  } else {
    const nbNotes = state.notes.filter(function(n) { return n.notebook_id === currentNotebookId; });
    countEl.textContent = '(' + nbNotes.length + ')';
  }
}

async function loadNotebookBar() {
  const notebooks = await window.pywebview.api.notebooks_list();
  const dot = $('#notebook-dot');
  const name = $('#notebook-name');

  // 更新按钮文字和颜色
  if (currentNotebookId === null) {
    dot.style.background = 'var(--accent)';
    name.textContent = '全部笔记';
  } else {
    const nb = notebooks.find(function(n) { return n.id === currentNotebookId; });
    if (nb) {
      dot.style.background = nb.color || '#7D8A6E';
      name.textContent = nb.name;
    } else {
      currentNotebookId = null;
      loadNotebookBar();
      return;
    }
  }

  // 同步更新计数
  updateNotebookCount();

  // 构建下拉列表
  const dropdown = $('#notebook-dropdown');
  dropdown.innerHTML = '';
  notebooks.forEach(nb => {
    const item = document.createElement('div');
    item.className = 'notebook-drop-item' + (nb.id === currentNotebookId ? ' active' : '');
    item.innerHTML = `<span class="notebook-dot" style="background:${nb.color||'#7D8A6E'}"></span>${escapeHtml(nb.name)}<span style="margin-left:auto;font-size:10px;color:var(--text-muted);">${state.notes.filter(n=>n.notebook_id===nb.id).length}篇</span><button class="notebook-delete-btn" title="删除笔记本" style="margin-left:4px;color:var(--text-muted);background:none;border:none;cursor:pointer;font-size:14px;padding:0 4px;">×</button>`;
    // 删除按钮事件（阻止冒泡）
    setTimeout(() => {
      const delBtn = item.querySelector('.notebook-delete-btn');
      if (delBtn) delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!confirm(`确定删除笔记本「${nb.name}」？其中的笔记将移回未分类。`)) return;
        await window.pywebview.api.notebooks_delete(nb.id);
        currentNotebookId = null;
        await loadAllNotes();
        loadNotebookBar();
      });
    }, 0);
    item.addEventListener('click', async () => {
      currentNotebookId = nb.id;
      dropdown.style.display = 'none';
      await filterByNotebook(nb.id);
    });
    dropdown.appendChild(item);
  });
  // 全部笔记选项
  const allItem = document.createElement('div');
  allItem.className = 'notebook-drop-item' + (currentNotebookId === null ? ' active' : '');
  allItem.innerHTML = `<span class="notebook-dot" style="background:var(--accent)"></span>全部笔记<span style="margin-left:auto;font-size:10px;color:var(--text-muted);">${state.notes.length}篇</span>`;
  allItem.addEventListener('click', async () => {
    currentNotebookId = null;
    dropdown.style.display = 'none';
    await loadAllNotes();
  });
  dropdown.appendChild(allItem);
}

async function filterByNotebook(nbId) {
  state.notes = await window.pywebview.api.notes_list();
  state.notes = state.notes.filter(n => n.notebook_id === nbId);
  renderNoteList();
  loadNotebookBar();
  if (state.notes.length > 0) {
    await verifyAndSelectNote(state.notes[0].id);
  } else {
    hideEditorUI();
  }
}

async function loadAllNotes() {
  state.notes = await window.pywebview.api.notes_list();
  renderNoteList();
  loadNotebookBar();
  if (state.notes.length > 0 && !state.activeNoteId) {
    await verifyAndSelectNote(state.notes[0].id);
  }
}

$('#btn-notebook-select').addEventListener('click', () => {
  const dropdown = $('#notebook-dropdown');
  dropdown.style.display = dropdown.style.display === 'block' ? 'none' : 'block';
  loadNotebookBar();
});

// 点击其他地方关闭下拉
document.addEventListener('click', (e) => {
  if (!e.target.closest('.notebook-bar-wrapper')) {
    $('#notebook-dropdown').style.display = 'none';
  }
});

$('#btn-new-notebook-sidebar').addEventListener('click', async () => {
  const name = prompt('请输入笔记本名称：', '新笔记本');
  if (!name) return;
  await window.pywebview.api.notebooks_create(name);
  loadNotebookBar();
});

// 笔记移动到笔记本（在标签栏加按钮）
$('#btn-move-notebook')?.addEventListener('click', async () => {
  if (!state.activeNoteId) return;
  const notebooks = await window.pywebview.api.notebooks_list();
  const names = notebooks.map(n => n.name + (n.id === currentNotebookId ? ' (当前)' : ''));
  names.unshift('无 (全部笔记)');
  const choice = prompt('移动到笔记本：\n' + names.map((n,i) => `${i}. ${n}`).join('\n') + '\n\n输入序号：', '0');
  if (choice === null) return;
  const idx = parseInt(choice);
  if (isNaN(idx) || idx < 0 || idx > notebooks.length) return;
  const targetId = idx === 0 ? null : notebooks[idx-1].id;
  await window.pywebview.api.notes_update(state.activeNoteId, { notebook_id: targetId || '' });
  loadNotes().then(renderNoteList);
  loadNotebookBar();
});

// ====== 搜索过滤 ======
async function filterNotesBySearch(query) {
  const q = query.trim().toLowerCase();
  if (!q) {
    // 无搜索词：显示全部
    dom.noteList.querySelectorAll('.note-item').forEach(el => el.classList.remove('hidden-by-search'));
    dom.btnSearchClear.style.display = 'none';
    return;
  }
  dom.btnSearchClear.style.display = 'flex';

  // 前端搜索：匹配标题，或加载内容进行匹配
  const items = dom.noteList.querySelectorAll('.note-item');
  for (const item of items) {
    const noteId = item.dataset.noteId;
    const note = state.notes.find(n => n.id === noteId);
    if (!note) continue;

    let matched = note.title.toLowerCase().includes(q);
    // 如果标题没匹配，检查正文（从数据库加载）
    if (!matched && note.content) {
      matched = note.content.toLowerCase().includes(q);
    }
    item.classList.toggle('hidden-by-search', !matched);
  }
}

dom.searchInput.addEventListener('input', () => filterNotesBySearch(dom.searchInput.value));
dom.btnSearchClear.addEventListener('click', () => {
  dom.searchInput.value = '';
  filterNotesBySearch('');
  dom.searchInput.focus();
});

// ====== 启动应用 ======
async function initApp() {
  // 初始化 Quill
  initQuill();
  // 字体/字号下拉同步
  syncFontSizeDisplay();

  // 加载设置
  await loadSettings();

  // 加载笔记列表
  const notes = await loadNotes();

  // 如果有笔记，自动选择第一篇（加密笔记会弹出密码验证）
  if (notes.length > 0) {
    await verifyAndSelectNote(notes[0].id);
  } else {
    // 无笔记状态
    hideEditorUI();
    document.querySelector('.ql-toolbar')?.classList.add('hidden');
  }

  // 加载标签筛选
  loadTagFilter();
  // 加载笔记本栏
  loadNotebookBar();
  // 初始化 Dock 拖拽
  initAllDrag();
  console.log('📒 我的记事本已就绪！');
  console.log(`   - ${notes.length} 篇笔记已加载`);
  console.log(`   - 当前主题：${state.currentTheme}`);
}

// 启动！
initApp().catch(err => {
  console.error('启动失败:', err);
});
