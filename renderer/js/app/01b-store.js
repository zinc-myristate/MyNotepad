// ====== notesStore：state.notes 唯一写入通道 ======
// 背景：曾因「标题输入处理器提前改 state.notes[].title，保存去重比较误判无变化」
// 导致纯标题修改不落库。收敛所有写入到 store，杜绝任意代码隐式篡改共享状态。
// 约定：读取仍直接用 state.notes（find/map/循环等只读操作不受限）；
//       任何赋值/push/splice/sort/属性修改必须走这里。
const notesStore = {
  /** 整体替换列表（loadNotes/标签筛选/笔记本筛选），可选归一化 + 排序 */
  setNotes(arr, { normalize = false, sort = false } = {}) {
    let notes = arr || [];
    if (normalize) {
      notes = notes.map(n => ({ ...n, is_pinned: Number(n.is_pinned) || 0, is_favorite: Number(n.is_favorite) || 0 }));
    }
    if (sort) {
      // 与后端 ORDER BY 对齐：置顶 → 手动排序 DESC → 最近更新
      notes.sort((a, b) => {
        if (b.is_pinned !== a.is_pinned) return b.is_pinned - a.is_pinned;
        const so = (b.sort_order || 0) - (a.sort_order || 0);
        if (so !== 0) return so;
        return (b.updated_at || '').localeCompare(a.updated_at || '');
      });
    }
    state.notes = notes;
  },

  /** 更新单条笔记的字段（保存成功回填/标题即时刷新） */
  updateFields(id, fields) {
    const idx = state.notes.findIndex(n => n.id === id);
    if (idx >= 0) Object.assign(state.notes[idx], fields);
    return idx >= 0;
  },

  /** 删除一条 */
  remove(id) {
    state.notes = state.notes.filter(n => n.id !== id);
  },

  /** 头部插入（日历新建当天笔记） */
  unshift(note) {
    state.notes.unshift(note);
  },

  /** 拖拽排序：把 fromIndex 的元素移到 toIndex */
  move(fromIndex, toIndex) {
    const moved = state.notes.splice(fromIndex, 1)[0];
    state.notes.splice(toIndex, 0, moved);
    return moved;
  },

  /** 便捷读取 */
  get(id) {
    return state.notes.find(n => n.id === id);
  },
};
