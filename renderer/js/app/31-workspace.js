// Workspace presentation only: editor buffers and save sequencing stay in their existing modules.
import { $, dom, hasOpenPanel, showToast } from './01-core.js';
import { openQuickSwitch } from './11-quick-switch.js';
import { refreshMarkdownLayout, renderPreviewNow } from './13-markdown-editor.js';

const VIEW_KEY = 'workspace_markdown_view';
const VALID_VIEWS = ['source', 'split', 'preview'];
const SPLIT_MIN_WIDTH = 740;
let preferredView = 'split';
let layoutFrame = 0;
let viewChosen = false;
let preferenceWrite = Promise.resolve();

async function restoreWorkspacePreference() {
  try {
    // WebView2 uses a new local HTTP origin on launch; browser storage is not durable here.
    const saved = await window.pywebview.api.settings_get(VIEW_KEY);
    if (!viewChosen && VALID_VIEWS.includes(saved)) {
      preferredView = saved;
      scheduleWorkspaceLayout();
    }
  } catch (_) { /* Keep the default view when settings are unavailable. */ }
}

function updateWorkspaceLayout() {
  layoutFrame = 0;
  const canSplit = dom.editorArea.clientWidth >= SPLIT_MIN_WIDTH;
  const view = preferredView === 'split' && !canSplit ? 'source' : preferredView;
  document.body.dataset.mdView = view;
  document.querySelectorAll('#md-view-switch button').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.mdView === view));
    if (button.dataset.mdView === 'split') {
      button.disabled = !canSplit;
      button.title = canSplit ? '编辑与预览并排显示' : '加宽窗口或进入专注模式后可分栏';
    }
  });
  refreshMarkdownLayout();
}

function scheduleWorkspaceLayout() {
  if (!layoutFrame) layoutFrame = requestAnimationFrame(updateWorkspaceLayout);
}

$('#md-view-switch').addEventListener('click', event => {
  const button = event.target.closest('button[data-md-view]');
  if (!button || button.disabled) return;
  preferredView = button.dataset.mdView;
  viewChosen = true;
  const selectedView = preferredView;
  preferenceWrite = preferenceWrite
    .then(() => window.pywebview.api.settings_set(VIEW_KEY, selectedView))
    .catch(() => showToast('视图已切换，但未能记住偏好', { type: 'warn' }));
  // Flush the pending preview debounce so a switch immediately shows the latest text.
  renderPreviewNow();
  updateWorkspaceLayout();
});

function toggleFocusMode() {
  const focused = document.body.classList.toggle('focus-mode');
  const button = $('#btn-focus-mode');
  button.setAttribute('aria-pressed', String(focused));
  button.setAttribute('aria-label', focused ? '退出专注模式' : '进入专注模式');
  button.title = (focused ? '退出专注模式' : '专注模式') + ' (Ctrl+Shift+F)';
  button.querySelector('span').textContent = focused ? '退出专注' : '专注';
  scheduleWorkspaceLayout();
}
$('#btn-focus-mode').addEventListener('click', toggleFocusMode);
document.addEventListener('keydown', event => {
  if (event.ctrlKey && event.shiftKey && !event.altKey && event.key.toLowerCase() === 'f') {
    event.preventDefault();
    // A dialog keeps its keyboard context; do not change the workspace behind it.
    if (hasOpenPanel()) return;
    toggleFocusMode();
  }
});

$('#btn-command-palette').addEventListener('click', openQuickSwitch);
$('#btn-welcome-browse').addEventListener('click', openQuickSwitch);
$('#btn-welcome-new').addEventListener('click', () => dom.btnNewNote.click());
dom.btnSearchClear.setAttribute('aria-label', '清空搜索');
$('#note-title-input').setAttribute('aria-label', '笔记标题');
document.querySelectorAll('button[title]:not([aria-label])').forEach(button => {
  button.setAttribute('aria-label', button.title);
});
document.querySelectorAll('.theme-option').forEach(option => {
  option.tabIndex = 0;
  option.setAttribute('role', 'button');
});
// Clickable elements that are not native buttons answer Enter/Space like one.
document.addEventListener('keydown', event => {
  if (event.isComposing || event.defaultPrevented || (event.key !== 'Enter' && event.key !== ' ')) return;
  if (event.target.matches?.('[role="button"][tabindex]')) {
    event.preventDefault(); event.target.click();
  }
});

const notebookName = $('#notebook-name');
function syncWorkspaceScope() {
  $('#workspace-scope').textContent = notebookName.textContent || '全部笔记';
}
new MutationObserver(syncWorkspaceScope).observe(notebookName, { childList: true, subtree: true, characterData: true });
syncWorkspaceScope();

// The native details menu supports Enter/Space automatically. Close after choice, outside click or Esc.
const titlePicker = $('.title-style-picker');
document.addEventListener('click', event => {
  if (!titlePicker.contains(event.target) || event.target.closest('.title-style-btn')) titlePicker.open = false;
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && titlePicker.open) {
    titlePicker.open = false;
    titlePicker.querySelector('summary').focus();
  }
});

if (typeof ResizeObserver !== 'undefined') {
  new ResizeObserver(scheduleWorkspaceLayout).observe(dom.editorArea);
} else {
  window.addEventListener('resize', scheduleWorkspaceLayout);
}
scheduleWorkspaceLayout();
if (window.pywebview?.api?.settings_get) restoreWorkspacePreference();
else window.addEventListener('pywebviewready', restoreWorkspacePreference, { once: true });
