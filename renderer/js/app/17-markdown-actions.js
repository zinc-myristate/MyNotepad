// ====== Markdown 编辑动作（工具栏按钮与快捷键共用的实现）======
// 单独一个模块：动作本身是"对 CodeMirror 做编辑"，工具栏（16-markdown-toolbar.js）
// 只负责把点击映射成动作名。这样快捷键与按钮走的是同一条代码路径，不会两套行为。

import { showToast } from './01-core.js';

function _cm() {
  const w = document.querySelector('.CodeMirror');
  return (w && w.CodeMirror) || null;
}

/** 用成对标记包住选区；没有选区时插入占位并选中它（方便直接改） */
function wrapSelection(cm, before, after, placeholder) {
  const doc = cm.getDoc();
  const sel = doc.getSelection();
  const text = sel || placeholder || '';
  doc.replaceSelection(before + text + after);
  if (!sel) {
    const cur = doc.getCursor();
    doc.setSelection({ line: cur.line, ch: cur.ch - after.length - text.length },
      { line: cur.line, ch: cur.ch - after.length });
  }
}

/** 给选中行加前缀；已有同前缀则去掉（可当开关用） */
function prefixLines(cm, prefix, stripPattern) {
  const doc = cm.getDoc();
  const from = doc.getCursor('from');
  const to = doc.getCursor('to');
  for (let ln = from.line; ln <= to.line; ln += 1) {
    const line = doc.getLine(ln) || '';
    const m = stripPattern.exec(line);
    if (m) doc.replaceRange('', { line: ln, ch: 0 }, { line: ln, ch: m[0].length });
    else doc.replaceRange(prefix, { line: ln, ch: 0 });
  }
}

const RE_HEADING = /^#{1,6}\s+/;
const RE_LIST = /^\s*[-*+]\s+/;
const RE_TODO = /^\s*[-*+]\s+\[[ xX]\]\s+/;
const RE_OL = /^\s*\d+[.)]\s+/;
const RE_QUOTE = /^\s*>\s?/;

const TABLE_TEMPLATE = '\n| 列一 | 列二 |\n| --- | --- |\n| 内容 | 内容 |\n';

/**
 * 插入**块级**元素（表格/分割线/代码围栏）。
 *
 * 为什么要另起一段而不是就地插入：光标常在列表项里（`- [ ] 待办`），就地插进去
 * 表格会被解析成"列表项里的文字"而不是表格（实测预览里整张表变成一行 li 内容）。
 * 在行尾补一个空行再接块级元素，列表就正常结束了。
 */
function insertBlock(cm, text) {
  const doc = cm.getDoc();
  const cur = doc.getCursor();
  const lineText = doc.getLine(cur.line) || '';
  if (lineText.trim()) {
    doc.replaceRange('\n' + text, { line: cur.line, ch: lineText.length });
  } else {
    doc.replaceRange(text.replace(/^\n/, ''), { line: cur.line, ch: 0 });
  }
}

/**
 * 执行一个 Markdown 插入动作。
 * @param {string} action bold|italic|strike|code|math|h1|h2|h3|ul|ol|todo|quote|
 *                        codeblock|divider|table|link|image|attachment
 * @param {{path?: string, name?: string}} [payload] image/attachment 用（相对路径 + 显示名）
 */
export function applyMarkdownAction(action, payload = {}) {
  const cm = _cm();
  if (!cm) {
    showToast('Markdown 编辑器还没就绪', { type: 'warn' });
    return;
  }
  cm.focus();
  const doc = cm.getDoc();
  switch (action) {
    case 'bold': wrapSelection(cm, '**', '**', '粗体'); break;
    case 'italic': wrapSelection(cm, '*', '*', '斜体'); break;
    case 'strike': wrapSelection(cm, '~~', '~~', '删除线'); break;
    case 'code': wrapSelection(cm, '`', '`', '代码'); break;
    case 'math': wrapSelection(cm, '$', '$', 'x^2'); break;
    case 'link': wrapSelection(cm, '[', '](https://)', '链接文字'); break;
    case 'h1': prefixLines(cm, '# ', RE_HEADING); break;
    case 'h2': prefixLines(cm, '## ', RE_HEADING); break;
    case 'h3': prefixLines(cm, '### ', RE_HEADING); break;
    case 'ul': prefixLines(cm, '- ', RE_LIST); break;
    case 'ol': prefixLines(cm, '1. ', RE_OL); break;
    case 'todo': prefixLines(cm, '- [ ] ', RE_TODO); break;
    case 'quote': prefixLines(cm, '> ', RE_QUOTE); break;
    case 'codeblock': wrapFence(cm); break;
    case 'divider': insertBlock(cm, '\n<hr class="divider-1">\n'); break;
    case 'table': insertBlock(cm, TABLE_TEMPLATE); break;
    case 'image':
      doc.replaceSelection(`![${payload.name || ''}](${payload.path || 'attachments/'})`);
      break;
    case 'attachment':
      doc.replaceSelection(`[📎 ${payload.name || '附件'}](${payload.path || 'attachments/'})`);
      break;
    // 模板 / 其它模块送进来的一整段 Markdown：也走「另起一段」，
    // 否则从列表项里插入会被解析成列表内容（与表格同一个坑）
    case 'insert-text':
      if (payload.text) insertBlock(cm, '\n' + payload.text.replace(/\n?$/, '\n'));
      break;
    default:
      break;
  }
}

/** 把选中行包进 ``` 围栏（首尾已是围栏时再去掉） */
function wrapFence(cm) {
  const doc = cm.getDoc();
  const from = doc.getCursor('from');
  const to = doc.getCursor('to');
  const first = doc.getLine(from.line) || '';
  const last = doc.getLine(to.line) || '';
  if (/^\s*```/.test(first) && /^\s*```/.test(last)) {
    doc.replaceRange('', { line: to.line, ch: 0 },
      { line: to.line, ch: last.length });
    doc.replaceRange('', { line: from.line, ch: 0 }, { line: from.line, ch: first.length });
    return;
  }
  doc.replaceRange('```\n', { line: from.line, ch: 0 });
  const targetLine = to.line + 1;
  doc.replaceRange('\n```', { line: targetLine, ch: (doc.getLine(targetLine) || '').length });
}
