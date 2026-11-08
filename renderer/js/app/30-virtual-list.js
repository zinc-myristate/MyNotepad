// ====== 笔记列表的窗口化渲染（虚拟滚动）======
//
// 为什么需要它：列表以前是**全量建 DOM**，2000 篇实测 54,000 个节点、单次 renderNoteList
// **496ms**（后端 notes_list 只占其中 10ms —— 瓶颈完全在前端建元素）。
// 而"单行增量更新 100 次"的实测耗时是 0ms，说明真正贵的是**建行**，不是改行。
// 窗口化之后只建"看得见的那十几行 + 少量缓冲"，与笔记总数无关。
//
// 设计取舍（都为了"不改动周围一堆依赖 DOM 的代码"）：
//   · **不换数据结构**：仍然是一串 `.note-item` 直接挂在 `#note-list` 下，
//     只是只放窗口内的那些。容器委托的事件、`querySelectorAll('.note-item')`
//     （拖拽、选中样式、表格视图）全都照常工作，只是作用域变成"窗口内"。
//   · **用撑高块保持滚动条**：窗口上方/下方各插一个空 div，高度 = 窗口外行数 × 行高。
//     这样滚动条长度与"全部笔记"一致，不会因为只渲染了十几行就缩成一小截。
//   · 行高按**实测**（第一次渲染后量第一行），不同主题/字号下自动跟上；
//     封面固定 38px、标题单行省略，所以行高是齐的（实测 2000 行一致）。
//
// 【注意】由此带来的、**必须知道**的语义变化：
//   任何"遍历全部 `.note-item`"的代码，现在只看到窗口内的行。已经在这些地方收口：
//     · 搜索过滤（`applySearchToDom`）：不再靠给行加类，而是把过滤结果交给本模块，
//       由窗口化只渲染命中的行 —— 搜索真相源从 DOM 挪回了数据；
//     · 批量「全选（只选当前可见）」：判据改成"当前筛选后的集合"，不再数 DOM；
//     · 拖拽排序：只能在窗口内拖动（跨屏拖动会需要边缘自动滚动，那属于另一件事，
//       没有做；已把 drop 目标限制在窗口内的行，不会算错落点）。

import { dom, state } from './01-core.js';

const BUFFER_ROWS = 6;          // 窗口上下各多渲染几行，减少快速滚动时的白屏
const DEFAULT_ROW_H = 76;       // 行高估计值（实测前的第一帧用），随后会被真实值替换

let _rowH = DEFAULT_ROW_H;
let _built = 0;                 // 已渲染的行数（诊断用）
let _lastRange = [0, -1];
let _topSpacer = null;
let _bottomSpacer = null;
let _bound = false;
let _buildItem = null;          // 由 renderWindow / initVirtualList 登记（见 initVirtualList 的注释）
let _focusId = null;

dom.noteList.setAttribute('role', 'list');
dom.noteList.setAttribute('aria-label', '笔记列表，方向键浏览，Enter 打开');
dom.noteList.tabIndex = 0;

dom.noteList.addEventListener('focusin', e => {
  const row = e.target.closest('.note-item');
  if (!row) return;
  _focusId = row.dataset.noteId;
  dom.noteList.tabIndex = -1;
  dom.noteList.querySelectorAll('.note-item').forEach(item => {
    item.tabIndex = item === row ? 0 : -1;
  });
});
dom.noteList.addEventListener('keydown', e => {
  if (e.isComposing || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey ||
      e.target.closest('button, input, select, textarea')) return;
  const items = listItems();
  if (!items.length) return;
  const index = items.findIndex(note => note.id === _focusId);
  let next;
  if (e.key === 'ArrowDown') next = Math.min(items.length - 1, index + 1);
  else if (e.key === 'ArrowUp') next = Math.max(0, index < 0 ? 0 : index - 1);
  else if (e.key === 'Home') next = 0;
  else if (e.key === 'End') next = items.length - 1;
  else if (e.key === 'Enter' || e.key === ' ') {
    const row = e.target.closest('.note-item');
    if (row) { e.preventDefault(); row.click(); }
    return;
  } else return;
  e.preventDefault();
  _focusId = items[next].id;
  const top = next * _rowH;
  if (top < dom.noteList.scrollTop) dom.noteList.scrollTop = top;
  else if (top + _rowH > dom.noteList.scrollTop + viewportHeight()) {
    dom.noteList.scrollTop = top + _rowH - viewportHeight();
  }
  renderWindow();
  dom.noteList.querySelector(`[data-note-id="${CSS.escape(_focusId)}"]`)?.focus({ preventScroll: true });
});

/** 当前要展示的笔记序列（虚拟列表的输入）。
 *
 *  以前列表直接遍历 `state.notes`、靠给行加 `hidden-by-search` 类来过滤。
 *  窗口化之后不能这么算了：如果仍按 `state.notes` 计算滚动高度，被搜索隐藏的行
 *  会留下大片空白（撑高块把它们也算进去了）。所以过滤必须发生在窗口计算**之前**。 */
export function listItems() {
  const q = state.searchQuery;
  if (!q) return state.notes;
  const matched = state.searchMatched;
  if (!matched || !matched.size) return [];
  return state.notes.filter(n => matched.has(n.id));
}

function ensureSpacers() {
  if (_topSpacer && _topSpacer.parentNode === dom.noteList) return;
  _topSpacer = document.createElement('div');
  _topSpacer.className = 'note-list-spacer';
  _topSpacer.setAttribute('aria-hidden', 'true');
  _bottomSpacer = document.createElement('div');
  _bottomSpacer.className = 'note-list-spacer';
  _bottomSpacer.setAttribute('aria-hidden', 'true');
}

function measureRow() {
  const first = dom.noteList.querySelector('.note-item');
  if (!first) return;
  const h = first.getBoundingClientRect().height;
  if (h > 0) _rowH = h;          // 含 margin 的外接高度，与撑高块的口径一致
}

function viewportHeight() {
  const h = dom.noteList.clientHeight;
  // 容器还没布局出来时（首帧）给个保守值，别算出"只渲染 0 行"
  return h > 0 ? h : 600;
}

function scrollAnchor() {
  // 用 getBoundingClientRect 相对定位，避开 padding/边框的换算（#note-list 有 2px 上内边距）
  return dom.noteList.getBoundingClientRect().top;
}

/**
 * 重算窗口并渲染。
 *
 * @param {Function} buildItem  (note, index) => HTMLElement，由 03-notes 提供（它知道行长什么样）
 * @param {boolean}  force      数据本身变了（新建/删除/排序/刷新）时强制重建
 *
 * 注意：两个参数都**可以省略**（滚动/缩放那条路就不传）：`buildItem` 会登记到 `_buildItem`
 * 供后续复用；`force` 省略时按"窗口没变就什么都不做"处理。
 * 之所以支持省略 —— 见 `initVirtualList` 的注释：以前滚动/缩放把回调错当成 buildItem 传进来，
 * 导致每次滚动都抛 `appendChild ... not of type 'Node'`。
 */
export function renderWindow(buildItem, force = false) {
  if (typeof buildItem === 'function') _buildItem = buildItem;
  const builder = _buildItem;
  const items = listItems();
  const oldFocus = document.activeElement;
  const focusedRow = oldFocus?.closest('.note-item');
  const hadFocus = focusedRow && dom.noteList.contains(focusedRow);
  const focusedButton = hadFocus && oldFocus.tagName === 'BUTTON' ? oldFocus.className : null;
  if (!items.some(note => note.id === _focusId)) {
    _focusId = items.find(note => note.id === state.activeNoteId)?.id || items[0]?.id || null;
  }
  ensureSpacers();

  if (!items.length) {
    dom.noteList.innerHTML = '';
    _lastRange = [0, -1];
    _built = 0;
    dom.noteList.tabIndex = 0;
    if (hadFocus) dom.noteList.focus({ preventScroll: true });
    return;
  }
  if (typeof builder !== 'function') return;   // 还没人登记 builder：什么都不做（不猜）

  const listTop = scrollAnchor();
  const scroll = dom.noteList.scrollTop;
  const first = Math.max(0, Math.floor(scroll / _rowH) - BUFFER_ROWS);
  const visibleCount = Math.ceil(viewportHeight() / _rowH) + BUFFER_ROWS * 2;
  const last = Math.min(items.length - 1, first + visibleCount);

  if (!force && first === _lastRange[0] && last === _lastRange[1]) return;  // 窗口没变，什么都不做
  _lastRange = [first, last];

  const frag = document.createDocumentFragment();
  for (let i = first; i <= last; i++) {
    const node = builder(items[i], i);
    if (!node) {
      // 到这里说明 builder 对某一行没有返回元素。以前这会变成一句
      // "appendChild: parameter 1 is not of type 'Node'"，栈顶指向本文件第 104 行，
      // 完全看不出是**哪一行数据**、也看不出是 builder 的问题。现在报清楚。
      console.error('[virtual-list] buildItem 没有返回元素，跳过这一行：',
                    { index: i, note: items[i] && { id: items[i].id, title: items[i].title } });
      continue;
    }
    node.tabIndex = items[i].id === _focusId ? 0 : -1;
    node.setAttribute('role', 'listitem');
    node.setAttribute('aria-posinset', String(i + 1));
    node.setAttribute('aria-setsize', String(items.length));
    node.setAttribute('aria-label', items[i].title || '未命名笔记');
    frag.appendChild(node);
  }
  _built = last - first + 1;

  dom.noteList.innerHTML = '';
  dom.noteList.appendChild(_topSpacer);
  dom.noteList.appendChild(frag);
  dom.noteList.appendChild(_bottomSpacer);

  _topSpacer.style.height = (first * _rowH) + 'px';
  _bottomSpacer.style.height = Math.max(0, (items.length - 1 - last) * _rowH) + 'px';

  if (_rowH === DEFAULT_ROW_H) measureRow();   // 首帧之后拿到真实行高
  // 撑高块高度变了会让 scrollTop 漂移，这里把它按"窗口首个可见行"重新对齐
  if (Math.abs(dom.noteList.getBoundingClientRect().top - listTop) > 1) {
    dom.noteList.scrollTop = scroll;
  }

  // 【注意】焦点必须等撑高块定好高度才还回去：focus() 会强制布局，提前调用时滚动高度还是
  // 上一个窗口的，scrollTop 被夹断之后再也回不来（实测 End 键落在视口下方 450px）。
  const focusRow = dom.noteList.querySelector(`[data-note-id="${CSS.escape(_focusId)}"]`);
  dom.noteList.tabIndex = focusRow ? -1 : 0;
  if (hadFocus) {
    const button = focusedButton && focusRow && [...focusRow.querySelectorAll('button')]
      .find(el => el.className === focusedButton);
    (button || focusRow || dom.noteList).focus({ preventScroll: true });
  }
}

/** 绑定滚动/尺寸监听（只绑一次）。
 *
 *  `buildItem` 在这里**登记一次**，之后滚动/缩放都复用它 —— 不再每次调用都传进来。
 *
 * ## 为什么改成这样（修的是一个真 bug）
 *
 * 原签名是 `initVirtualList(onWindowChange)`，而 `09-boot.js` 传进来的是
 * `() => renderNoteList()`。于是那两处监听器里写的是：
 *
 *     renderWindow(onWindowChange)          // 把"回调"当成了 buildItem
 *     renderWindow(onWindowChange, true)
 *
 * `renderWindow` 的第 1 个参数是 builder，所以它会把那个回调当 builder 调：
 * `() => renderNoteList()` **没有 return** → `buildItem(...)` 返回 `undefined`
 * → `frag.appendChild(undefined)` → **每次滚动/缩放都抛**
 * `TypeError: Failed to execute 'appendChild' on 'Node': parameter 1 is not of type 'Node'`
 * （实测 error.log 从 2026-10-01 起反复出现，栈顶正是 `30-virtual-list.js:104`）。
 *
 * 更隐蔽的后果：滚动那条路整个是错的 —— 它绕回 `renderNoteList()`（那个内部会再调一次
 * `renderWindow`），于是"窗口化滚动"实际上没生效，每次滚动都在全量重算。
 * 单看界面只觉得"有点卡 + 偶发红条"，两者都指向这里。
 *
 * 改成登记式之后：**builder 有且只有一个来源**，滚动/缩放/重渲染走同一条路，
 * 也不再有"参数位置传错"的余地。
 */
export function initVirtualList(buildItem) {
  if (typeof buildItem === 'function') _buildItem = buildItem;
  if (_bound) return;
  _bound = true;
  if (typeof _buildItem !== 'function') {
    // 没登记 builder 就滚动会渲染不出东西 —— 明确报出来，别静默什么都不做
    console.error('[virtual-list] initVirtualList 需要 buildItem，滚动/缩放才会渲染');
  }
  dom.noteList.addEventListener('scroll', () => {
    renderWindow();
  }, { passive: true });
  // 窗口拉宽/拉高会改变可见行数；侧栏本身也可能被折叠
  window.addEventListener('resize', () => {
    _lastRange = [0, -1];
    renderWindow(true);
  });
}

/** 行高变了（换主题/字号）时重新测量 */
export function invalidateRowHeight() {
  _rowH = DEFAULT_ROW_H;
  _lastRange = [0, -1];
}

/** 诊断/测试用：当前渲染了多少行、行高估到多少 */
export function virtualListStats() {
  return { rendered: _built, rowHeight: _rowH, range: _lastRange.slice() };
}
