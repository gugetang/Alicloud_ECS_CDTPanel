// menuShouldFlipLeft 的用例：按钮右边缘减菜单宽小于 8 时翻转。
// 运行：node --test web/src/menuFlip.test.ts（Node 22.6+ 类型剥离）
import test from "node:test";
import assert from "node:assert/strict";

import { menuShouldFlipLeft } from "./menuFlip.ts";

test("手机上按钮贴左边缘（right=48，菜单 200）→ 翻转", () => {
  assert.equal(menuShouldFlipLeft(48, 200), true);
});

test("按钮在右边缘（right=1000，菜单 200）→ 不翻转", () => {
  assert.equal(menuShouldFlipLeft(1000, 200), false);
});

test("边界：right - 宽 = 8 → 不翻转；= 7 → 翻转", () => {
  assert.equal(menuShouldFlipLeft(208, 200), false);
  assert.equal(menuShouldFlipLeft(207, 200), true);
});
