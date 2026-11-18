// ====== 粘贴、拖进笔记的文件（两种编辑器共用）======
//
// 截图、图片、PDF……粘贴或拖进笔记：存成这篇笔记的附件，能显示的图片插成图片，别的插成附件
// （Markdown 是一行附件链接，富文本是附件卡片）。文件内容由页面读出来，以 base64 交给 Python（桥接 file_add_to_note）：
// WebView2 里拿不到拖进来的文件的路径，`file.path` 是 Electron 的属性。
//
// 以前（2026-11-18 实测）：
// · Markdown 笔记里粘贴截图没反应，拖进图片没反应；拖进 .txt、.csv 这类文件，CodeMirror 自己会把文件内容整个当文字贴进正文
// · 富文本笔记里粘贴或拖进一张图，会插进两张：Quill 自带的 uploader 插一张 data URI，02-editor.js 另外监听的 paste / drop
//   又插一张附件。拖进 PDF 这类文件什么都没有（靠的是 `file.path`）
//
// 现在富文本这边由 Quill 的 uploader 决定什么时候算"贴进文件"、插在哪里（拖放时是落点），文件交给这里；
// Markdown 这边由 13-markdown-editor.js 的 paste / drop 交给这里。

import { showToast, state } from './01-core.js';
import { insertAttachmentResult, insertImageResult } from './02-editor.js';
import { applyMarkdownAction } from './17-markdown-actions.js';

// 内容经桥接传，太大会卡住：超过这个数的请用工具栏的「附件」按钮（走文件对话框，只传路径）。
// 改的话 app.pyw 的 _DROP_MAX_BYTES 一起改（tests/test_paste_drop.py 对照两边）
export const MAX_DROP_BYTES = 50 * 1024 * 1024;

// 能直接显示的图片才插成图片（值是没有文件名时补的扩展名）；TIFF、HEIC 这类 WebView2 显示不出来，当附件
const DISPLAYABLE_IMAGES = {
  'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp', 'image/bmp': 'bmp',
  'image/svg+xml': 'svg', 'image/x-icon': 'ico', 'image/vnd.microsoft.icon': 'ico', 'image/avif': 'avif',
};

/** 拖放 / 粘贴带进来的文件。要在事件处理函数里同步取：事件一结束 DataTransfer 就读不到了 */
export function filesIn(dataTransfer) {
  return Array.from((dataTransfer && dataTransfer.files) || []);
}

/** 粘贴时要当附件的文件。剪贴板里有文字就照常粘贴文字：从 Word、Excel 复制时，
 *  剪贴板里除了文字还附带一张整段内容的图，不能把它当成用户要贴的图 */
export function pastedFiles(clipboardData) {
  if (!clipboardData) return [];
  if ((clipboardData.getData('text/plain') || '').length) return [];
  return filesIn(clipboardData);
}

function readBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result || '');
      resolve(url.slice(url.indexOf(',') + 1));
    };
    reader.onerror = () => reject(reader.error || new Error('read failed'));
    reader.readAsDataURL(file);
  });
}

/**
 * 把文件存成当前笔记的附件，插进正文。一个一个来：大文件并着传会把桥接塞住。
 * @param {File[]} files
 * @param {{range?: {index: number, length: number}, at?: {line: number, ch: number}}} [opts]
 *        range：富文本的插入位置（Quill 算好的，拖放时是落点）；at：Markdown 的拖放落点。不给就插在光标处
 */
export async function addFilesToNote(files, opts = {}) {
  if (!files.length) return;
  const noteId = state.activeNoteId;
  if (!noteId) {
    showToast('请先选择或新建一篇笔记', { type: 'warn' });
    return;
  }
  const format = state.noteFormat;
  const added = [];
  const problems = [];
  for (const file of files) {
    const kind = Object.hasOwn(DISPLAYABLE_IMAGES, file.type) ? 'image' : 'file';
    // 剪贴板里的图一般叫 image.png；万一没有名字，补一个带扩展名的（存下来的文件靠扩展名认是什么图）
    const name = file.name || (kind === 'image' ? '粘贴的图片.' + DISPLAYABLE_IMAGES[file.type] : '粘贴的文件');
    if (file.size > MAX_DROP_BYTES) {
      problems.push('「' + name + '」超过 ' + (MAX_DROP_BYTES >> 20) + ' MB，请用工具栏的「附件」按钮添加');
      continue;
    }
    let data;
    try {
      data = await readBase64(file);
    } catch (err) {
      problems.push('「' + name + '」读不出来（拖的是文件夹的话，请把里面的文件拖进来）');
      continue;
    }
    let info;
    try {
      info = await window.pywebview.api.file_add_to_note(noteId, name, data, kind);
    } catch (err) {
      problems.push('「' + name + '」没加进来：' + (err && err.message ? err.message : err));
      continue;
    }
    if (!info || info.error) {
      problems.push((info && info.error) || ('「' + name + '」没加进来'));
      continue;
    }
    added.push({ kind, info });
  }
  if (added.length) {
    if (state.activeNoteId === noteId) insertIntoNote(added, format, opts);
    // 存附件的那一会儿切到了别的笔记：附件已经在原来那篇里，不能插进眼前这篇
    else problems.unshift('插入之前已经切到了别的笔记，没有插进去');
  }
  if (problems.length) showToast(problems.join('；'), { type: 'error', duration: 6000 });
}

function insertIntoNote(added, format, opts) {
  if (format === 'md') {
    applyMarkdownAction('assets', {
      at: opts.at,
      items: added.map(({ kind, info }) => ({
        kind,
        name: info.original_name || info.filename,
        // 相对路径：换台机器、被 Obsidian 打开都能解析（与工具栏插入同一种写法）
        path: 'attachments/' + state.activeNoteId + '/' + info.filename,
      })),
    });
    return;
  }
  const quill = state.quill;
  if (!quill) return;
  const range = opts.range;
  if (range) {
    if (range.length) quill.deleteText(range.index, range.length, 'user');      // 贴在选中的文字上：替换掉它
    quill.setSelection(range.index, 0, 'silent');
  }
  for (const { kind, info } of added) {
    if (kind === 'image') insertImageResult(info);
    else insertAttachmentResult(info);
  }
}

// 文件拖到两个编辑器以外的地方：不接，光标显示"不能放"，也不让浏览器拿它去打开那个文件。
// 编辑器、笔记列表自己的 dragover 已经 preventDefault 过的不管。
// （用 CDP 模拟的拖放实测不会导航走，但真实的系统拖放验不到，留着兜底）
function hasFiles(dataTransfer) {
  return !!dataTransfer && Array.from(dataTransfer.types || []).includes('Files');
}

document.addEventListener('dragover', (e) => {
  if (e.defaultPrevented || !hasFiles(e.dataTransfer)) return;
  e.preventDefault();
  e.dataTransfer.dropEffect = 'none';
});
document.addEventListener('drop', (e) => {
  if (hasFiles(e.dataTransfer)) e.preventDefault();
});
