// 页眉下拉菜单左右翻转：按钮贴屏幕左边缘时，菜单改左对齐，避免伸出屏幕外。
// （手机上顶栏按钮换行到左边缘，语言/主题/风格菜单按右对齐会伸出屏幕外。）
//
// 注意：rectRight 必须在点击处理里同步量好再传进来。React 处理完事件后会把
// e.currentTarget 置空（react-dom executeDispatch 在监听器返回后立即置空），
// 如果把事件对象塞进 setState 回调里再量，渲染时 currentTarget 已是 null，
// getBoundingClientRect 抛 TypeError；项目又没有 ErrorBoundary，整棵 React
// 树被卸载，页面一片空白。#35 的 flipMenu 就是这么写的，点语言/主题/风格
// 三个按钮都会白屏。
export function menuShouldFlipLeft(rectRight: number, menuWidth: number): boolean {
  return rectRight - menuWidth < 8;
}
