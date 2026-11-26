// modalDrag.ts 的用例：拖动起点判定、按住后是否进入拖动、位置钳制。
// 运行：node --test web/src/modalDrag.test.ts（Node 22.6+ 类型剥离）
import test from "node:test";
import assert from "node:assert/strict";

import { isDragHandleTarget, shouldCommitDrag, shouldSaveOnTap, clampModalPos } from "./modalDrag.ts";

// closest 返回命中元素表示点在控件里，返回 null 表示点在空白处。
const hit = () => ({ closest: () => ({}) });
const miss = () => ({ closest: () => null });
// label 场景：包着控件的 label 是控件，不包控件的 label 是文字。
const labelWithControl = () => ({ closest: (s: string) => (s === "label" ? { querySelector: () => ({}) } : null) });
const labelPlain = () => ({ closest: (s: string) => (s === "label" ? { querySelector: () => null } : null) });

test("点在输入框/按钮上不拖动，点在空白处可拖动", () => {
  assert.equal(isDragHandleTarget(hit()), false);
  assert.equal(isDragHandleTarget(miss()), true);
  assert.equal(isDragHandleTarget(null), false);
  assert.equal(isDragHandleTarget({} as never), false);
});

test("label：包着控件算编辑区，不包控件算空白处", () => {
  assert.equal(isDragHandleTarget(labelWithControl()), false);
  assert.equal(isDragHandleTarget(labelPlain()), true);
});

test("鼠标：移动 >3px 即拖动，不动不拖", () => {
  assert.equal(shouldCommitDrag("mouse", 0, 0), "wait");
  assert.equal(shouldCommitDrag("mouse", 4, 0), "commit");
  assert.equal(shouldCommitDrag("mouse", 0, 4), "commit");
  assert.equal(shouldCommitDrag("mouse", 2, 2), "wait");
  assert.equal(shouldCommitDrag("mouse", -4, 0), "commit");
});

test("触摸：横向为主且 >8px 才拖，竖向为主交给滚动", () => {
  assert.equal(shouldCommitDrag("touch", 9, 2), "commit");
  assert.equal(shouldCommitDrag("touch", -9, 2), "commit");
  assert.equal(shouldCommitDrag("touch", 2, 9), "abandon");
  assert.equal(shouldCommitDrag("touch", 5, 5), "wait");
  assert.equal(shouldCommitDrag("touch", 10, 10), "abandon");
  assert.equal(shouldCommitDrag("touch", 8, 0), "wait");
  assert.equal(shouldCommitDrag("pen", 9, 2), "commit");
  assert.equal(shouldCommitDrag("pen", 2, 9), "abandon");
});

test("松手点按保存：没拖动、移动 <6px 才保存，拖动过或移动超了不保存", () => {
  assert.equal(shouldSaveOnTap(false, 0, 0), true);
  assert.equal(shouldSaveOnTap(false, 5, 5), true);
  assert.equal(shouldSaveOnTap(false, -5, 0), true);
  assert.equal(shouldSaveOnTap(false, 6, 0), false);
  assert.equal(shouldSaveOnTap(false, 0, 6), false);
  assert.equal(shouldSaveOnTap(false, 20, 0), false);
  assert.equal(shouldSaveOnTap(true, 0, 0), false);
  assert.equal(shouldSaveOnTap(true, 20, 0), false);
});

test("钳制：至少 60px 留在屏幕内", () => {
  // 正常位置不动
  assert.deepEqual(clampModalPos(100, 100, 680, 1280, 800), { left: 100, top: 100 });
  // 左边最多伸出 w-60
  assert.deepEqual(clampModalPos(-700, 100, 680, 1280, 800), { left: -(680 - 60), top: 100 });
  // 右边最多留 60px
  assert.deepEqual(clampModalPos(1300, 100, 680, 1280, 800), { left: 1280 - 60, top: 100 });
  // 顶部不超出
  assert.deepEqual(clampModalPos(100, -50, 680, 1280, 800), { left: 100, top: 0 });
  // 底部最多留 60px
  assert.deepEqual(clampModalPos(100, 900, 680, 1280, 800), { left: 100, top: 800 - 60 });
});
