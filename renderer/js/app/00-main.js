// ==========================================
// 模块入口 —— 应用唯一的模块图起点
// ==========================================
// 改造前：index.html 用 FILES 数组按顺序注入 17 个经典脚本，模块之间靠「全局作用域 +
// 加载顺序」互相依赖。任一个文件拉取失败（WebView2 实测约 1/20）或顺序被改动，依赖它的
// 模块就会在顶层抛 ReferenceError 而整段不执行——典型症状是「新建笔记点了没反应」加上
// 「工具栏按钮图标集体消失」（监听器与 initApp 都注册在该文件里，永远执行不到）。
//
// 改造后：每个文件显式 import 自己用到的东西，加载顺序由 ES 模块图决定，不再依赖
// index.html 里的数组顺序；文件缺失或顺序错位不再可能造成「界面半死」——要么整图加载成功，
// 要么整图失败并在顶部红条明确报错（见 index.html 的启动加载器）。
//
// 本文件的两项职责：
//   1. 按原顺序 import 全部应用模块（副作用导入）。顺序仍会影响同一元素上监听器的注册
//      次序，因此保持与改造前 FILES 数组一致的排列；第三方 UMD 库（Quill / KaTeX）仍是
//      经典脚本，由 index.html 在本模块之前加载，这里只管应用自身的模块图。
//   2. 暴露极少量对外接口（e2e 探针 / 调试用）。

import './01-core.js';
import './01b-store.js';
import '../shared/utils.js';
import '../shared/icons.js';
import '../quill/quill-blots.js';
import './02-editor.js';
import './03-notes.js';
import './04-appearance.js';
import './05-shell.js';
import './06-versions-reminders.js';
import './07-formula-security-dnd.js';
import './08-appearance2.js';
import './09-boot.js';
import './10-trash.js';
import './11-quick-switch.js';
import './12-bulk-actions.js';
import '../quill/quill-deco.js';

// ----- 对外接口 -----
// 只暴露无副作用的引用（state / dom 是 const 对象，函数是稳定绑定），供 e2e 探针与调试使用。
// 生产逻辑一律走显式 import，不再依赖全局作用域。
import { state, dom, showConfirmAsync } from './01-core.js';
import { debouncedSave, flushSave } from './03-notes.js';

window.__app = { state, dom, debouncedSave, flushSave, showConfirmAsync };
