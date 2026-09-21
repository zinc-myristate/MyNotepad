// ====== 正文格式徽标（Markdown ⇄ 富文本）======
// 双轨并存的"搬家通道"：老笔记可以转成 Markdown，Markdown 笔记也能转回富文本。
//
// 为什么要做成标题栏上一个常驻小徽标，而不是藏在设置里：
//   · 用户需要随时知道"这篇是 Markdown 还是富文本"（决定能不能用字体/颜色/贴纸）；
//   · 转换是**不可逆感知**的操作（虽然后端留了版本快照 + 原始 Delta 备份），
//     放在显眼位置 + 明确说清代价，比"某处菜单里点一下"更负责。
//
// 三种情形文案不同（都由后端的 note_format_info 判定，不在前端猜）：
//   富文本 → Markdown：无损，自动备份原始 Delta
//   Markdown（有备份）→ 富文本：直接用原始 Delta，无损
//   Markdown（无备份）→ 富文本：有损（Markdown 表达不了的内容会降级）

import { $, showConfirmAsync, showToast, state } from './01-core.js';
import { reloadActiveNote } from './03-notes.js';

let _busy = false;

function badge() {
  return $('#btn-note-format');
}

/** 选中笔记后刷新徽标（03-notes.selectNote 调用） */
export function syncFormatBadge() {
  const el = badge();
  if (!el) return;
  if (!state.activeNoteId) {
    el.classList.add('hidden');
    return;
  }
  const isMd = state.noteFormat === 'md';
  el.classList.remove('hidden');
  el.classList.toggle('is-delta', !isMd);
  el.textContent = isMd ? 'MD' : '富文本';
  el.title = isMd ? '正文是 Markdown —— 点击可转为富文本' : '正文是富文本 —— 点击可转为 Markdown';
}

/** 供命令面板等其它入口复用：自动判断"无损还原"还是"有损转换"。
 *  抽出来的理由：转换有两条路径，任何新入口都不该自己判断，否则迟早出现"某处点了会丢格式"。
 */
export async function convertActiveNote(to) {
  const noteId = state.activeNoteId;
  if (!noteId) return { ok: false, error: '没有选中的笔记' };
  let info = null;
  try {
    info = await window.pywebview.api.note_format_info(noteId);
  } catch (err) {
    return { ok: false, error: '读取笔记格式失败' };
  }
  if (!info) return { ok: false, error: '笔记不存在' };
  if (info.locked) return { ok: false, error: '加密笔记需要先解锁' };
  const r = (to === 'delta' && info.has_delta_backup)
    ? await window.pywebview.api.restore_delta_backup(noteId)
    : await window.pywebview.api.convert_note_format(noteId, to);
  if (r && r.ok && !r.unchanged) {
    await reloadActiveNote();
    syncFormatBadge();
  }
  return r;
}

async function onClick() {
  if (_busy || !state.activeNoteId) return;
  const noteId = state.activeNoteId;
  let info = null;
  try {
    info = await window.pywebview.api.note_format_info(noteId);
  } catch (err) {
    showToast('读取笔记格式失败：' + (err && err.message ? err.message : err), { type: 'error' });
    return;
  }
  if (!info) return;
  if (info.locked) {
    showToast('加密笔记需要先解锁才能转换格式', { type: 'warn' });
    return;
  }

  const toMd = info.format !== 'md';
  let message;
  let okText;
  if (toMd) {
    message = '把这篇笔记的正文改成 Markdown？\n\n'
      + '· 颜色/字号/字体/下划线会写成内嵌 HTML，我们和 Obsidian 都能显示，Joplin 会丢\n'
      + '· 装饰分割线变成 <hr class="divider-N">，贴纸变成 emoji\n'
      + '· 转换前会自动留一份历史版本 + **原始富文本备份**，可一键还原\n'
      + '· 转换后会按内容守恒校验，发现会丢字就拒绝转换';
    okText = '转为 Markdown';
  } else if (info.has_delta_backup) {
    message = '恢复成转换前的原始富文本？\n\n'
      + '直接用当初留下的原始 Delta 还原（无损）。期间在 Markdown 里的改动不会被合并——\n'
      + '当前内容会先存成一个历史版本，随时可以翻回来。';
    okText = '还原原始富文本';
  } else {
    message = '把这篇 Markdown 笔记转回声明的富文本？\n\n'
      + '· 这是**有损**转换：Markdown 表达不了的东西（贴纸、彩色字号之外的自定义外观等）不会回来\n'
      + '· 转换前会自动留一份历史版本';
    okText = '转为富文本';
  }

  const ok = await showConfirmAsync({
    title: '正文格式', message, okText, cancelText: '取消',
  });
  if (!ok) return;

  _busy = true;
  try {
    const useBackup = !toMd && info.has_delta_backup;
    const r = useBackup
      ? await window.pywebview.api.restore_delta_backup(noteId)
      : await window.pywebview.api.convert_note_format(noteId, toMd ? 'md' : 'delta');
    if (!r || !r.ok) {
      showToast('转换失败：' + ((r && r.error) || '未知错误'), { type: 'error' });
      return;
    }
    if (r.unchanged) {
      showToast('已经是这个格式了', { type: 'info' });
      return;
    }
    await reloadActiveNote();
    syncFormatBadge();
    showToast(useBackup ? '已还原原始富文本'
      : (toMd ? '已转为 Markdown' : '已转为富文本'), { type: 'success' });
  } catch (err) {
    showToast('转换失败：' + (err && err.message ? err.message : err), { type: 'error' });
  } finally {
    _busy = false;
  }
}

export function initNoteFormatBadge() {
  const el = badge();
  if (!el) return;
  el.addEventListener('click', onClick);
  syncFormatBadge();
}
