// 更新倒计时：点“立即更新”后弹窗里倒数，倒数结束或新版本上线就刷新页面。
// 剩余秒数 <= 0：刷新（倒计时走完）。
// 查到的当前版本与点击时不同（且两者都非空）：新版本已上线，提前刷新。
// 查不到版本（容器重启中，请求失败）或版本没变：继续等。
export function shouldReloadUpdate(
  remaining: number,
  oldVersion: string,
  currentVersion: string | null | undefined,
): boolean {
  if (remaining <= 0) return true;
  if (!oldVersion || !currentVersion) return false;
  return currentVersion !== oldVersion;
}

// 更新倒计时的秒数：拉镜像 + 重建容器一般在 1 分钟内；
// 新版本一上线就提前刷新，倒计时只是兜底。
export const UPDATE_COUNTDOWN_SECONDS = 60;
