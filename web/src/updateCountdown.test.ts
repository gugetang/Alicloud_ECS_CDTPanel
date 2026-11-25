import test from "node:test";
import assert from "node:assert/strict";
import { shouldReloadUpdate, UPDATE_COUNTDOWN_SECONDS } from "./updateCountdown.ts";

test("倒计时走完就刷新", () => {
  assert.equal(shouldReloadUpdate(0, "v1", "v1"), true);
  assert.equal(shouldReloadUpdate(-1, "v1", "v1"), true);
});

test("新版本上线就提前刷新", () => {
  assert.equal(shouldReloadUpdate(30, "abc1234", "def5678"), true);
});

test("版本没变、查不到版本、旧版本未知时继续等", () => {
  assert.equal(shouldReloadUpdate(30, "abc1234", "abc1234"), false);
  assert.equal(shouldReloadUpdate(30, "abc1234", null), false);
  assert.equal(shouldReloadUpdate(30, "abc1234", undefined), false);
  assert.equal(shouldReloadUpdate(30, "", "def5678"), false);
});

test("倒计时秒数是正数", () => {
  assert.ok(UPDATE_COUNTDOWN_SECONDS > 0);
});
