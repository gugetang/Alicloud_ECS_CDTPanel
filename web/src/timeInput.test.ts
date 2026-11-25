import test from "node:test";
import assert from "node:assert/strict";
import { formatTimeInput } from "./timeInput.ts";

test("formatTimeInput：逐字输入自动补齐冒号", () => {
  assert.equal(formatTimeInput(""), "");
  assert.equal(formatTimeInput("0"), "0");
  assert.equal(formatTimeInput("00"), "00");
  assert.equal(formatTimeInput("000"), "00:0");
  assert.equal(formatTimeInput("0000"), "00:00");
  assert.equal(formatTimeInput("2359"), "23:59");
});

test("formatTimeInput：粘贴带冒号、带杂字符、超长都归一化", () => {
  assert.equal(formatTimeInput("00:00"), "00:00");
  assert.equal(formatTimeInput("08:15"), "08:15");
  assert.equal(formatTimeInput("0a0b0c0d"), "00:00");
  assert.equal(formatTimeInput("00000"), "00:00");
});
