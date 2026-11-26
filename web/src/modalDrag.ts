// 实例设置弹窗的拖动判定（纯函数，见 modalDrag.test.ts）：
// 拖动可从弹窗内任意非编辑区域开始；输入框/按钮等控件除外，点控件时
// 正常聚焦/点击不拖动（与云端备份对话框 onWdDragStart 同一思路）。
export type DragTargetLike = { closest?: (selector: string) => unknown } | null;

// label 只在包着控件时才算编辑区（如“每日定时开关机”的 label 包着 checkbox，
// 点它会开关勾选）；不包控件的 label（如字段名）只是文字，可拖动/点按保存。
const INTERACTIVE_SELECTOR = "input, select, textarea, button, a, [contenteditable]";
const LABEL_CONTROL_SELECTOR = "input, select, textarea, button";

export function isDragHandleTarget(target: DragTargetLike): boolean {
  if (!target || typeof target.closest !== "function") return false;
  if (target.closest(INTERACTIVE_SELECTOR) != null) return false;
  const label = target.closest("label") as { querySelector?: (selector: string) => unknown } | null;
  if (label && typeof label.querySelector === "function" && label.querySelector(LABEL_CONTROL_SELECTOR) != null) return false;
  return true;
}

// 松手时是否点按保存：没进入拖动、移动 <6px 是点按→保存；拖动过、或移动
// 超过 6px 都不保存（拖动已由 shouldCommitDrag 判定，触摸竖滑交给滚动）。
export function shouldSaveOnTap(committed: boolean, dx: number, dy: number): boolean {
  if (committed) return false;
  return Math.abs(dx) < 6 && Math.abs(dy) < 6;
}

// 按住后是否进入拖动：鼠标移动 >3px 即拖；触摸/手写笔只认横向为主的移动
// （|dx|>|dy| 且 >8px），竖向为主时交给内容滚动，不拖动。
export function shouldCommitDrag(pointerType: string, dx: number, dy: number): "commit" | "abandon" | "wait" {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (pointerType === "mouse") return ax > 3 || ay > 3 ? "commit" : "wait";
  if (ax > ay && ax > 8) return "commit";
  if (ay >= ax && ay > 8) return "abandon";
  return "wait";
}

// 拖动位置钳制：至少 60px 留在屏幕内（与登录弹窗同一规则）。
export function clampModalPos(left: number, top: number, w: number, vw: number, vh: number): { left: number; top: number } {
  return {
    left: Math.min(Math.max(left, -(w - 60)), vw - 60),
    top: Math.min(Math.max(top, 0), vh - 60),
  };
}
