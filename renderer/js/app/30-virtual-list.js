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
 * @param {Function} buildItem  (note, index) => HTMLElement，由 03-notes 提供（它知道行长什么样）
 * @param {boolean}  force      数据本身变了（新建/删除/排序/刷新）时强制重建
 */
export function renderWindow(buildItem, force = false) {
  const items = listItems();
  ensureSpacers();

  if (!items.length) {
    dom.noteList.innerHTML = '';
    _lastRange = [0, -1];
    _built = 0;
    return;
  }

  const listTop = scrollAnchor();
  const scroll = dom.noteList.scrollTop;
  const first = Math.max(0, Math.floor(scroll / _rowH) - BUFFER_ROWS);
  const visibleCount = Math.ceil(viewportHeight() / _rowH) + BUFFER_ROWS * 2;
  const last = Math.min(items.length - 1, first + visibleCount);

  if (!force && first === _lastRange[0] && last === _lastRange[1]) return;  // 窗口没变，什么都不做
  _lastRange = [first, last];

  const frag = document.createDocumentFragment();
  for (let i = first; i <= last; i++) frag.appendChild(buildItem(items[i], i));
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
}

/** 绑定滚动/尺寸监听（只绑一次） */
export function initVirtualList(onWindowChange) {
  if (_bound) return;
  _bound = true;
  dom.noteList.addEventListener('scroll', () => {
    renderWindow(onWindowChange);
  }, { passive: true });
  // 窗口拉宽/拉高会改变可见行数；侧栏本身也可能被折叠
  window.addEventListener('resize', () => {
    _lastRange = [0, -1];
    renderWindow(onWindowChange, true);
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
