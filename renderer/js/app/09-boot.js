// ====== 笔记本管理 ======
// ====== ESM 依赖（原先靠全局作用域与加载顺序隐式依赖，现显式声明）======
import { $, closePanel, dom, hideEditorUI, openPanel, showConfirmAsync, showInputDialog, showToast, state } from './01-core.js';
import { flushSave, loadNotes, previewHtmlFor, renderNoteList } from './03-notes.js';
import { initQuill, syncFontSizeDisplay } from './02-editor.js';
import { initMarkdownEditor } from './13-markdown-editor.js';
import { initNoteFormatBadge } from './15-note-format.js';
import { initMarkdownToolbar } from './16-markdown-toolbar.js';
import { initTodoPanel } from './18-todo-panel.js';
import { initSavedSearches } from './20-saved-searches.js';
import { initStatusBar } from './21-status-bar.js';
import { initOutline } from './22-outline.js';
import { initFindBar } from './23-find-bar.js';
import { initVirtualList } from './30-virtual-list.js';
import { initProperties } from './24-properties.js';
import { initLinks } from './25-links.js';
import { initTableView } from './26-table-view.js';
import { initTemplates } from './27-templates.js';
import { initCapture } from './28-capture.js';
import { initOcr } from './29-ocr.js';
import { loadSettings } from './04-appearance.js';
import { getCurrentTagFilter, loadTagFilter, reapplyTagFilter } from './05-shell.js';
import { initAllDrag, initAutoLock, verifyAndSelectNote } from './07-formula-security-dnd.js';
import { debounce, escapeHtml } from '../shared/utils.js';
import { ICONS } from '../shared/icons.js';

let currentNotebookId = null; // null = 全部笔记
let _notebooks = [];          // 最近一次加载的笔记本列表（供名称查询，避免各处重复请求）
let _counts = { by_id: {}, uncategorized: 0, total: 0 };  // notebook_counts() 缓存，见 refreshNotebookCounts

/** 当前笔记本筛选（null = 全部）。供导出等"按当前范围"的功能读取 */
export function getCurrentNotebookId() { return currentNotebookId; }

/** 当前笔记本名字（未筛选返回空串）：导出时要告诉用户导的是哪一本 */
export function getCurrentNotebookName() {
  if (!currentNotebookId) return '';
  const nb = _notebooks.find(n => n.id === currentNotebookId);
  return nb ? nb.name : '';
}

/** 同步更新笔记本计数徽章。
 *
 *  列表现在**就是**"当前笔记本那几篇"（后端 notes_list(notebook_id) 过滤），
 *  所以直接数 state.notes 就是对的。旧实现在「全部笔记」时数 state.notes、在笔记本时
 *  再 filter 一遍 —— 两者在"列表被某条路径整体覆盖"时会互相打架，
 *  正是截图里「原神 (0)」却列出 6 篇全量笔记的来源。 */
export function updateNotebookCount() {
  const countEl = document.getElementById('notebook-count');
  if (!countEl) return;
  countEl.textContent = '(' + state.notes.length + ')';
}

/** 计数缓存：下拉里每本的 `N 篇` 和「全部笔记 N 篇」**只能由后端算** ——
 *  范围过滤后前端手里只有当前那一本，靠 state.notes 数别的笔记本必然是 0。
 *  刷新时机：任何一次列表刷新（loadNotes 会调）+ 笔记本栏刷新。 */
export async function refreshNotebookCounts() {
  try {
    const c = await window.pywebview.api.notebook_counts();
    if (c) _counts = { by_id: c.by_id || {}, uncategorized: c.uncategorized || 0, total: c.total || 0 };
  } catch (e) { /* 计数拿不到不影响列表 */ }
  updateNotebookCount();
  return _counts;
}

/** 切换笔记本视角（null = 全部笔记）—— 切换笔记本的**唯一入口**。
 *
 *  范围由后端过滤，于是"在「原神」里新建/复制/恢复/勾待办/OCR 落库"之后，
 *  列表刷新仍然只有原神 —— 不会再像以前那样被任何一次 loadNotes 冲成全量。
 *  当前打开的那篇不在新范围里时跳这一本的第一篇；一本都没有就清空编辑区。 */
export async function setNotebookScope(nbId) {
  await flushSave();                 // 编辑区可能被收起来，先把手里的字落盘
  currentNotebookId = nbId || null;
  await loadNotes();                 // 笔记本范围（后端过滤）
  const scopeNotes = state.notes.slice();
  // 标签与笔记本是叠加关系：换了笔记本必须重套一次标签，否则标签会被静默丢掉
  if (getCurrentTagFilter()) await reapplyTagFilter();
  await loadNotebookBar();
  // 当前这篇还在范围里就留着，不在就跳这一本的第一篇（被标签筛空时退回笔记本范围里挑）
  const pool = state.notes.length ? state.notes : scopeNotes;
  if (pool.length === 0) {
    state.activeNoteId = null;       // 编辑区收起来了，activeNoteId 也必须清（否则下次"同一篇"会被误判）
    hideEditorUI();
    return;
  }
  if (!pool.some(n => n.id === state.activeNoteId)) {
    await verifyAndSelectNote(pool[0].id);
  }
}

/** 打开一篇"可能不在当前视角里"的笔记（Ctrl+P 跳转 / 待办 / 双链 / 提醒 / 捕获 / 回收站恢复…）。
 *
 *  编辑区与列表范围必须一致：这篇不在当前笔记本里就把视角切到它所在的笔记本
 *  （未分类 → 全部笔记）再选中它，否则会出现"列表里看不见，编辑区却在编辑它"。
 *  Ctrl+P 的跨笔记本跳转正是靠它落地的。 */
export async function revealAndSelectNote(noteId) {
  if (!noteId) return;
  let note = state.notes.find(n => n.id === noteId);
  if (!note) {
    try { note = await window.pywebview.api.notes_get(noteId); } catch (e) { note = null; }
  }
  const target = (note && note.notebook_id) || null;
  if (target !== currentNotebookId) {
    currentNotebookId = target;
    await loadNotes();
    await loadNotebookBar();
  }
  await verifyAndSelectNote(noteId);
}

export async function loadNotebookBar() {
  const notebooks = await window.pywebview.api.notebooks_list();
  _notebooks = notebooks;
  await refreshNotebookCounts();
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

  // 构建下拉列表（每本的篇数来自后端计数：范围过滤后前端手里只有当前那一本）
  const dropdown = $('#notebook-dropdown');
  dropdown.innerHTML = '';
  notebooks.forEach(nb => {
    const item = document.createElement('div');
    item.className = 'notebook-drop-item' + (nb.id === currentNotebookId ? ' active' : '');
    const nbCount = (_counts.by_id && _counts.by_id[nb.id]) || 0;
    item.innerHTML = `<span class="notebook-dot" style="background:${nb.color||'#7D8A6E'}"></span>${escapeHtml(nb.name)}<span style="margin-left:auto;font-size:10px;color:var(--text-muted);">${nbCount}篇</span><button class="notebook-delete-btn" title="删除笔记本" style="margin-left:4px;color:var(--text-muted);background:none;border:none;cursor:pointer;font-size:14px;padding:0 4px;">×</button>`;
    // 删除按钮事件（阻止冒泡）
    setTimeout(() => {
      const delBtn = item.querySelector('.notebook-delete-btn');
      if (delBtn) delBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        if (!(await showConfirmAsync({ title: '删除笔记本', message: `确定删除笔记本「${nb.name}」？其中的笔记将移回未分类。`, okText: '删除', danger: true }))) return;
        await window.pywebview.api.notebooks_delete(nb.id);
        await setNotebookScope(null);
      });
    }, 0);
    item.addEventListener('click', async () => {
      dropdown.style.display = 'none';
      await setNotebookScope(nb.id);
    });
    dropdown.appendChild(item);
  });
  // 全部笔记选项
  const allItem = document.createElement('div');
  allItem.className = 'notebook-drop-item' + (currentNotebookId === null ? ' active' : '');
  allItem.innerHTML = `<span class="notebook-dot" style="background:var(--accent)"></span>全部笔记<span style="margin-left:auto;font-size:10px;color:var(--text-muted);">${_counts.total}篇</span>`;
  allItem.addEventListener('click', async () => {
    dropdown.style.display = 'none';
    await setNotebookScope(null);
  });
  dropdown.appendChild(allItem);
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
  const name = await showInputDialog({
    title: '新建笔记本',
    message: '请输入笔记本名称：',
    defaultValue: '新笔记本',
    okText: '创建'
  });
  if (!name || !name.trim()) return;
  await window.pywebview.api.notebooks_create(name.trim());
  loadNotebookBar();
});

// 笔记移动到笔记本：列表面板选择（替代原「输序号」prompt）
$('#btn-move-notebook')?.addEventListener('click', async () => {
  if (!state.activeNoteId) { showToast('请先选择一篇笔记', { type: 'warn' }); return; }
  const notebooks = await window.pywebview.api.notebooks_list();
  const list = $('#move-notebook-list');
  list.innerHTML = '';
  const mk = (label, targetId, isCurrent) => {
    const btn = document.createElement('button');
    btn.className = 'tag-picker-chip' + (isCurrent ? ' selected' : '');
    btn.style.cssText = 'display:block;width:100%;text-align:left;margin-bottom:6px;';
    btn.textContent = label;
    btn.addEventListener('click', async () => {
      closePanel($('#move-notebook-panel'));
      await window.pywebview.api.notes_update(state.activeNoteId, { notebook_id: targetId || '' });
      // 视角跟着这篇笔记走：移到哪本就切到哪本（移进"无"就回全部笔记）。
      // 否则刚移走的笔记在列表里消失、编辑区却还开着它 —— 正是"列表与编辑区不一致"。
      await setNotebookScope(targetId || null);
    });
    list.appendChild(btn);
  };
  mk('无（全部笔记）', '', currentNotebookId === null);
  notebooks.forEach(nb => mk(nb.name + (nb.id === currentNotebookId ? '（当前）' : ''), nb.id, nb.id === currentNotebookId));
  openPanel($('#move-notebook-panel'));
});

// ====== 搜索过滤 ======
// 后端 FTS5 全文搜索（标题+正文明文提取索引；加密笔记只搜标题；<3 字符自动 LIKE 回退）
let _searchSeq = 0;  // 请求序号：丢弃迟到的乱序响应

/** 让搜索筛选生效（数据侧 + 重渲染列表）。
 *
 *  【注意】这里的实现方式在窗口化之后**换过一次**，两代都在修同一个坑，值得说清：
 *  第一代：筛选只写在 DOM 的 `hidden-by-search` 类上，而 `renderNoteList()` 会重建全部行、
 *   不带这个类 → 置顶/收藏/复制/勾选待办/OCR 落库等 20+ 条刷新路径只要发生一次，筛选就
 *   变回全量，而 `state.searchQuery` 还是旧词、高亮还在（"筛选莫名失效、高亮却还在"）。
 *   修法是重建后重放一次。
 *  第二代（现在）：列表改窗口化渲染，**不能再靠给行加类**了 —— 撑高块要按"过滤后的行数"
 *   算，否则被隐藏的行会留下大片空白。所以筛选的真相源从 DOM 挪回数据：
 *   `state.searchMatched` 由过滤逻辑写入，`30-virtual-list.js` 的 `listItems()` 据此取集合、
 *   只渲染命中的行。本函数因此变成"通知重渲染 + 更新范围提示"。
 */
export function applySearchToDom() {
  const q = state.searchQuery;
  const matched = state.searchMatched || new Set();
  // 列表窗口按新的过滤结果重建（listItems() 会读 state.searchMatched）
  renderNoteList();
  if (!q) {
    updateScopeSearchHint(0);
    return;
  }
  // 命中数与"列表里真的有的"求交：笔记本视角下搜索天然是"在当前笔记本里搜"；
  // 一篇都没命中而全库有命中时，把原因说出来，别让用户以为"搜不到"
  const visible = state.notes.filter(n => matched.has(n.id)).length;
  updateScopeSearchHint(visible === 0 ? matched.size : 0);
}

async function filterNotesBySearch(query) {
  const q = query.trim();
  if (!q) {
    // 无搜索词：显示全部，摘要恢复为正文摘要
    state.searchQuery = '';
    state.searchSnippets = {};
    state.searchMatched = new Set();
    renderNoteList();          // 清掉搜索：列表要按"全部"重建窗口
    dom.btnSearchClear.style.display = 'none';
    updateScopeSearchHint(0);
    return;
  }
  dom.btnSearchClear.style.display = 'flex';

  const seq = ++_searchSeq;
  let matched;
  let snippets = {};
  try {
    const result = await window.pywebview.api.notes_search(q);
    if (seq !== _searchSeq) return;  // 已有更新的查询，丢弃本次结果
    matched = new Set((result && result.ids) || []);
    snippets = (result && result.snippets) || {};
  } catch (e) {
    if (seq !== _searchSeq) return;
    // 后端不可达时回退旧的前端标题匹配
    const lower = q.toLowerCase();
    matched = new Set(state.notes.filter(n => (n.title || '').toLowerCase().includes(lower)).map(n => n.id));
  }
  state.searchQuery = q;
  state.searchSnippets = snippets;
  state.searchMatched = matched;
  applySearchToDom();
}

/** 「当前笔记本里没有匹配」提示条（范围筛选 + 搜索叠加时的解释）。
 *  只在笔记本视角下出现：全部笔记视角下列表就是全库，没命中就是真没有。 */
function updateScopeSearchHint(otherCount) {
  let hint = document.getElementById('search-scope-hint');
  if (!otherCount || currentNotebookId === null) {
    if (hint) hint.remove();
    return;
  }
  if (!hint) {
    hint = document.createElement('div');
    hint.id = 'search-scope-hint';
    hint.className = 'search-scope-hint';
    dom.noteList.appendChild(hint);
  }
  hint.textContent = '';
  const txt = document.createElement('span');
  txt.textContent = '当前笔记本里没有匹配，其它笔记本里有 ' + otherCount + ' 篇';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'search-scope-hint-btn';
  btn.textContent = '在全部笔记里找';
  btn.addEventListener('click', async () => {
    await setNotebookScope(null);
    await filterNotesBySearch(dom.searchInput.value);
  });
  hint.appendChild(txt);
  hint.appendChild(btn);
}

/** 就地刷新某一行的摘要文字：不整表重渲染，避免搜索时列表滚动位置被重置 */
function refreshItemPreview(item) {
  const el = item.querySelector('.note-item-preview');
  if (!el) return;
  const note = state.notes.find(n => n.id === item.dataset.noteId);
  if (note) el.innerHTML = previewHtmlFor(note);
}

const _debouncedSearch = debounce(() => filterNotesBySearch(dom.searchInput.value), 200);
dom.searchInput.addEventListener('input', () => {
  const q = dom.searchInput.value.trim();
  if (!q) {
    // 清空立即恢复全部（不等防抖）
    _debouncedSearch.cancel();
    filterNotesBySearch('');
    return;
  }
  _debouncedSearch();
});
dom.btnSearchClear.addEventListener('click', () => {
  _debouncedSearch.cancel();
  dom.searchInput.value = '';
  filterNotesBySearch('');
  dom.searchInput.focus();
});

// ====== 启动应用 ======
/** 启动阶段的追踪（诊断/e2e 可读）。
 *
 *  为什么要它：启动是一条很长的串行链（十几个 init + 两次 await），中间任何一步抛出，
 *  后面的步骤就静默不执行 —— 现象是"界面看起来好了一半"（列表出来了、编辑区空着），
 *  而控制台只在 DevTools 里能看到。把走过的步骤记在 window 上，出问题时一眼看出断在哪。
 *  只在 __bootError 为空时记录（成功路径），失败信息仍走 __bootError。 */
function _bootStep(name) {
  try {
    (window.__bootTrace = window.__bootTrace || []).push(name);
  } catch (e) { /* 追踪失败不影响启动 */ }
}

/** 启动应用 */
async function initApp() {
  // 初始化 Quill
  initQuill();
  _bootStep('quill');
  initMarkdownEditor();   // Markdown 笔记的源码 + 预览双栏（与 Quill 二选一显示）
  initNoteFormatBadge();  // 标题栏的格式徽标：Markdown ⇄ 富文本 互转
  initMarkdownToolbar();  // Markdown 工具栏（加粗/列表/待办/表格/图片/附件…）
  initTodoPanel();        // 跨笔记待办清单
  initSavedSearches();    // 侧栏「视图」区（保存的搜索）
  initStatusBar();        // 状态栏（字数统计）
  initOutline();          // 大纲抽屉
  initFindBar();          // 笔记内查找替换
  initProperties();       // 属性行（front-matter）
  initLinks();            // 双链抽屉
  initTableView();        // 表格视图
  initTemplates();        // 模板抽屉（第 10 轮）
  initCapture();          // 快速捕获菜单 + 跨窗口插入桥（第 10 轮）
  initOcr();              // 图片文字识别（第 11 轮）
  // 列表窗口化渲染（滚动时只重建窗口内的行，见 30-virtual-list.js）
  initVirtualList(() => renderNoteList());
  _bootStep('virtual-list');
  // 字体/字号下拉同步
  syncFontSizeDisplay();

  // 加载设置
  await loadSettings();
  _bootStep('settings');
  // 闲置自动锁定（读设置 + 起每分钟检查）
  initAutoLock();

  // 加载笔记列表
  const notes = await loadNotes();
  _bootStep('load-notes:' + (notes ? notes.length : 'null'));

  // 如果有笔记，自动选择第一篇（加密笔记会弹出密码验证）
  if (notes.length > 0) {
    await verifyAndSelectNote(notes[0].id);
    _bootStep('selected-first');
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

/** 启动提示（开发态数据目录 / 另一份数据更完整）：一次性展示，可关闭。
 * 用常驻小横条而不是 Toast：这条信息说的是"你打开的可能不是平时那本记事本"，错过就没意义了。 */
async function showStartupNotice() {
  try {
    const msg = await window.pywebview.api.startup_notice();
    if (!msg) return;
    if (document.getElementById('startup-notice')) return;
    const bar = document.createElement('div');
    bar.id = 'startup-notice';
    bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99990;' +
      'background:#8A6D3B;color:#fff;font:12px/1.6 system-ui,sans-serif;' +
      'padding:8px 40px 8px 14px;';
    const span = document.createElement('span');
    span.textContent = msg;                       // textContent：提示内容不参与 HTML 解析
    const close = document.createElement('button');
    close.innerHTML = ICONS.close;   // 与 20 个面板关闭按钮同一份几何（图标库里就有）
    close.title = '关闭提示';
    close.style.cssText = 'position:absolute;right:8px;top:5px;background:none;border:none;' +
      'color:#fff;cursor:pointer;line-height:0;padding:2px;';
    close.addEventListener('click', () => bar.remove());
    bar.appendChild(span);
    bar.appendChild(close);
    document.body.appendChild(bar);
  } catch (e) { /* 提示失败不影响使用 */ }
}

// 启动！
//
// 注意：刻意延到下一个宏任务再 init，不要在这里直接 `initApp()`：
// 09-boot 是被很多模块 import 的"枢纽"（笔记本视角、计数、revealAndSelectNote 都在这儿），
// 循环边会让它的**函数体在别的模块函数体之前**被求值。实测踩到：13-markdown-editor 的
// `let cm` 还处于 TDZ，initApp 走到 initMarkdownEditor() 就抛
// `ReferenceError: Cannot access 'cm' before initialization` ——
// 界面停在"只有骨架、没有列表、笔记本栏是空的"半死状态，而且只在某个 import 边存在时才复现。
// 模块求值是同步的，排到下一个宏任务时整张图必然已求值完毕，这类顺序坑从此不可能再发生。
function bootApplication() {
  initApp().then(showStartupNotice).catch(err => {
    // 启动失败会让界面停在"半死"状态（没有列表、没有笔记本栏，只有骨架占位），
    // 所以把原因留在 window.__bootError 上：诊断/e2e 能直接读到，不必靠猜。
    try { window.__bootError = (err && (err.stack || err.message)) || String(err); } catch (e) { /* 忽略 */ }
    console.error('启动失败:', err);
  });
}

setTimeout(bootApplication, 0);
