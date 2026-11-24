// groupMemberDot 的用例：未选中实例不显示正在运行。
// 运行：node --test web/src/memberDot.test.ts（Node 22.6+ 类型剥离）
import test from "node:test";
import assert from "node:assert/strict";

import { groupMemberDot } from "./memberDot.ts";

test("未选中 + green（在线）→ 灰点离队", () => {
  assert.deepEqual(groupMemberDot(false, "green"), {
    dot: "gray",
    leftGroup: true,
  });
});

test("未选中 + green_pulse（启动中）→ 灰点离队", () => {
  assert.deepEqual(groupMemberDot(false, "green_pulse"), {
    dot: "gray",
    leftGroup: true,
  });
});

test("未选中 + yellow（排队中）→ 灰点离队", () => {
  assert.deepEqual(groupMemberDot(false, "yellow"), {
    dot: "gray",
    leftGroup: true,
  });
});

test("未选中 + red（已停机）→ 红点，仍显示已停机", () => {
  assert.deepEqual(groupMemberDot(false, "red"), {
    dot: "red",
    leftGroup: false,
  });
});

test("未选中 + red_pulse（停止中）→ 保持不变", () => {
  assert.deepEqual(groupMemberDot(false, "red_pulse"), {
    dot: "red_pulse",
    leftGroup: false,
  });
});

test("未选中 + gray（已跳出/未知）→ 保持灰点，不标离队", () => {
  assert.deepEqual(groupMemberDot(false, "gray"), {
    dot: "gray",
    leftGroup: false,
  });
});

test("选中 + green → 绿点在线", () => {
  assert.deepEqual(groupMemberDot(true, "green"), {
    dot: "green",
    leftGroup: false,
  });
});

test("选中 + yellow → 黄点排队", () => {
  assert.deepEqual(groupMemberDot(true, "yellow"), {
    dot: "yellow",
    leftGroup: false,
  });
});

test("选中 + green_pulse → 绿点闪烁启动中", () => {
  assert.deepEqual(groupMemberDot(true, "green_pulse"), {
    dot: "green_pulse",
    leftGroup: false,
  });
});
