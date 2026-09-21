// ====== 编辑器状态栏（字数 / 字符 / 段落 / 阅读时长 + 选中字数）======
// 统计口径**刻意与后端派生索引保持一致**（backend._markdown_to_text 的同一套剥离规则）：
// 剥掉标题标记、列表符号、强调符、链接地址、行内 HTML、代码围栏，再数
// 中日韩字符（按字）+ 拉丁数字（按词）。否则列表里显示"123 字"、状态栏显示"156 字"，
// 用户会以为哪里坏了。
//
// 为什么不直接读后端的 note_derived：那是**已保存**的数字。状态栏要跟着输入实时变，
// 所以在前端用同样规则现算（纯字符串操作，几千字的笔记开销可忽略）。

import { $, state } from './01-core.js';

// 与 backend.WORD_RE **逐字符相同**：中日韩汉字按字计、拉丁/数字按词计。
// 故意只认 \u4e00-\u9fff 基本区（后端如此），不要"顺手"加上假名/谚文/扩展区——
// 那会让同一篇笔记的状态栏数字与侧栏的字数不一致（tests/test_editor_ux_e2e.py 锁死这条）。
const WORD_RE = /[\u4e00-\u9fff]|[A-Za-z0-9_]+/g;
// backend._MD_ENTITIES 同一张表，`&amp;` 必须最后解（否则 `&amp;lt;` 会解成 `<`）
const ENTITIES = [['&nbsp;', ' '], ['&lt;', '<'], ['&gt;', '>'],
  ['&quot;', '"'], ['&#39;', "'"], ['&amp;', '&']];

/** 与后端 `_markdown_to_text` 同一套剥离规则（顺序也一样）。
 *  改这里就必须同步 backend.py 的同名正则——两边算出的字数必须一样，
 *  否则列表里显示"123 字"、状态栏显示"156 字"，用户会以为哪里坏了。 */
export function plainTextForStats(text, fmt) {
  if (!text) return '';
  // delta 笔记：调用方传进来的已经是 state.quill.getText()（纯文本），
  // 与后端 _delta_to_text 拼 op 文本的结果一致，无需再剥。
  if (fmt !== 'md') return String(text);
  let t = String(text);
  t = t.replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, ' ');   // _MD_SCRIPT
  t = t.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');            // _MD_IMAGE → alt
  t = t.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');             // _MD_LINK → 文字
  t = t.replace(/<[^>]+>/g, ' ');                            // _MD_TAG
  t = t.replace(/`{1,3}|~{2}|\*{1,3}/g, '');                 // _MD_INLINE：不留空格
  t = t.replace(/^\s*>+\s*|^\s*[-*+]\s+(\[[ xX]\]\s*)?|^\s*\d+[.)]\s+|^#{1,6}\s+/gm, ' ');
  for (const [ent, ch] of ENTITIES) t = t.split(ent).join(ch);
  return t;
}

export function countStats(text) {
  const words = (text.match(WORD_RE) || []).length;
  // 字符数不含空白；用展开运算符按**码点**计数，与 Python 的 len 对齐（emoji 不会被算成 2）
  const chars = [...text.replace(/\s+/g, '')].length;
  const paragraphs = text.split(/\n\s*\n/).filter((s) => s.trim()).length;
  return { words, chars, paragraphs };
}

function readingMinutes(words) {
  return Math.max(0, Math.round(words / 300));      // 中文阅读速度按 300 字/分钟粗估
}

/** 取当前编辑器正文（md 走源码，delta 走 Quill 纯文本） */
function currentText() {
  if (state.noteFormat === 'md') {
    const cm = (document.querySelector('.CodeMirror') || {}).CodeMirror;
    return cm ? cm.getValue() : '';
  }
  return state.quill ? state.quill.getText() : '';
}

/** 当前选中文字（没有选区返回空串） */
function currentSelection() {
  if (state.noteFormat === 'md') {
    const cm = (document.querySelector('.CodeMirror') || {}).CodeMirror;
    return cm ? (cm.getSelection() || '') : '';
  }
  if (!state.quill) return '';
  const r = state.quill.getSelection();
  return r && r.length ? state.quill.getText(r.index, r.length) : '';
}

export function updateStatusBar() {
  const box = $('#editor-status');
  if (!box) return;
  if (!state.activeNoteId) {
    box.classList.add('hidden');
    return;
  }
  box.classList.remove('hidden');
  const text = plainTextForStats(currentText(), state.noteFormat);
  const stats = countStats(text);
  const sel = currentSelection();
  const parts = [];
  if (sel.trim()) {
    const s = countStats(plainTextForStats(sel, state.noteFormat));
    parts.push('选中 ' + s.words + ' 字');
  }
  parts.push(stats.words + ' 字');
  parts.push(stats.chars + ' 字符');
  parts.push(stats.paragraphs + ' 段');
  const mins = readingMinutes(stats.words);
  parts.push(mins > 0 ? '约 ' + mins + ' 分钟' : '不足 1 分钟');
  const label = $('#status-text');
  if (label) label.textContent = parts.join(' · ');
  const fmt = $('#status-format');
  if (fmt) fmt.textContent = state.noteFormat === 'md' ? 'Markdown' : '富文本';
}

export function initStatusBar() {
  updateStatusBar();
}
