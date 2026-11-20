// ====== 弹出菜单（菜单写在页面里，这里只管开合与键盘）======
//
// 侧栏的「外观」、Markdown 工具栏的「插入」共用。约定与视图的「…」菜单一致：
// 按钮带 aria-haspopup / aria-expanded，菜单 role=menu；↑↓ / Home / End 换项，Esc、Tab、点别处收起，焦点回到按钮。
//
// 选了一项：先收起菜单、把焦点放回按钮，再让那一项自己的点击处理接着跑（这里在捕获阶段先动手）。
// 这样它打开的面板记下的"打开者"就是按钮——关掉面板时焦点回得来；要是记成了菜单项，菜单一收它就看不见了。
//
// 菜单要挂在 <body> 下：图片背景下工具栏、侧栏带 backdrop-filter，挂在它们里面时 position: fixed 会按它们定位。

let _open = null;            // { anchor, menu }

function items(menu) {
  return [...menu.querySelectorAll('button')].filter((b) => !b.disabled && !b.hidden);
}

function place(anchor, menu) {
  // 贴着按钮往下开；底下放不下就往上开，左右不出窗口
  const r = anchor.getBoundingClientRect();
  const w = menu.offsetWidth;
  const h = menu.offsetHeight;
  menu.style.left = Math.max(8, Math.min(r.left, innerWidth - w - 8)) + 'px';
  menu.style.top = (r.bottom + 4 + h <= innerHeight - 8 ? r.bottom + 4 : Math.max(8, r.top - h - 4)) + 'px';
}

function onPointerOutside(ev) {
  if (_open && !_open.menu.contains(ev.target) && !_open.anchor.contains(ev.target)) closeMenu();
}

function onViewportChange() { closeMenu(); }

export function closeMenu(returnFocus = false) {
  if (!_open) return;
  const { anchor, menu } = _open;
  _open = null;
  menu.classList.add('hidden');
  anchor.setAttribute('aria-expanded', 'false');
  anchor.classList.remove('active');
  document.removeEventListener('pointerdown', onPointerOutside, true);
  window.removeEventListener('resize', onViewportChange);
  if (returnFocus && anchor.isConnected) anchor.focus();
}

export function openMenu(anchor, menu) {
  closeMenu();
  menu.classList.remove('hidden');
  place(anchor, menu);
  _open = { anchor, menu };
  anchor.setAttribute('aria-expanded', 'true');
  anchor.classList.add('active');
  document.addEventListener('pointerdown', onPointerOutside, true);
  window.addEventListener('resize', onViewportChange);
  items(menu)[0]?.focus();
}

export function isMenuOpen(menu) {
  return !!_open && (!menu || _open.menu === menu);
}

/** 把一个按钮和它的菜单接起来。菜单里的每个 <button> 是一项（它们自己的点击处理不用改） */
export function bindMenu(anchor, menu, { label } = {}) {
  if (!anchor || !menu) return;
  anchor.setAttribute('aria-haspopup', 'menu');
  anchor.setAttribute('aria-expanded', 'false');
  menu.setAttribute('role', 'menu');
  if (label) menu.setAttribute('aria-label', label);
  menu.querySelectorAll('button').forEach((b) => {
    b.setAttribute('role', 'menuitem');
    b.tabIndex = -1;
  });
  anchor.addEventListener('click', () => {
    if (isMenuOpen(menu)) closeMenu(true);
    else openMenu(anchor, menu);
  });
  // 捕获阶段：比菜单项自己的点击处理先跑（见文件开头的说明）
  menu.addEventListener('click', (ev) => {
    if (ev.target.closest('button')) closeMenu(true);
  }, true);
  menu.addEventListener('keydown', (ev) => {
    const list = items(menu);
    const i = list.indexOf(document.activeElement);
    if (ev.key === 'Escape' || ev.key === 'Tab') {
      ev.preventDefault();
      ev.stopPropagation();          // Esc 只收菜单，不再往下清多选、退专注模式
      closeMenu(true);
    } else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault();
      list[(i + (ev.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length]?.focus();
    } else if (ev.key === 'Home' || ev.key === 'End') {
      ev.preventDefault();
      (ev.key === 'Home' ? list[0] : list[list.length - 1])?.focus();
    }
  });
  // 点在菜单的空白处（按钮之间的内边距）不能把焦点弄丢：焦点掉到 body 上，Esc 就收不到了
  menu.addEventListener('mousedown', (ev) => { if (!ev.target.closest('button')) ev.preventDefault(); });
}
