// ====== 工具函数 ======
// 提取自 app.js — 全局命名空间，与现有代码兼容

export function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

export function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// 防抖函数（返回的函数带 .cancel() 可取消待执行任务）
export function debounce(fn, delay) {
  let timer;
  const wrapped = function (...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), delay);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}
