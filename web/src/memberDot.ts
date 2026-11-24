// 分组成员选择区：直接显示实例的真实全局状态。
//
// 后端 status_dot 是全局状态（Running 且被某个域名指向 = green），
// 成员按钮只负责显示这个真实状态，不因“未选中”而改成灰点：
// 选中与否只看按钮边框（选中=蓝框，未选=灰框），状态点与状态文案
// 全站统一（在线/排队中/已停机/已跳出/未知与其它页面一致）。
// 之前未选中时把 green/green_pulse/yellow 改成灰点标“离队”的做法
// 会让一台正常排队中的实例看起来像已排除，是显示错误（2026-11-24
// 用户截图：SWAS-KR 在成员区是灰点，其它页面都是黄点排队中）。
export function groupMemberDot(
  selected: boolean,
  statusDot: string | undefined,
): { dot: string | undefined; leftGroup: boolean } {
  void selected;
  return { dot: statusDot, leftGroup: false };
}
