// 分组成员选择区：未选中的实例若全局状态是在线/启动中/排队中，
// 显示灰点标“离队”——它不在这个分组里，从这个分组看就是离队。
// 选中与否看按钮边框（选中=蓝框，未选=灰框）；状态点与状态文案
// 在成员区按这个规则，其它页面按后端 status_dot/status_label。
//
// 注意（2026-11-24 用户纠正）：21:22 那次把这里改成“直接显示真实
// 状态”是改反了。用户原意是：SWAS-KR 未被任何分组选中，应是离队，
// 其它位置也应是离队——成员区灰点是对的，其它位置黄点才是错的。
// 所以这里恢复成旧的灰点覆盖；“其它位置也离队”由后端 _public_account
// 负责（未被任何分组选中时 status_dot=gray、status_label=离队），
// 全站统一。不要再把这里改成直接返回真实状态。
export function groupMemberDot(
  selected: boolean,
  statusDot: string | undefined,
): { dot: string | undefined; leftGroup: boolean } {
  if (
    !selected &&
    (statusDot === "green" ||
      statusDot === "green_pulse" ||
      statusDot === "yellow")
  ) {
    return { dot: "gray", leftGroup: true };
  }
  return { dot: statusDot, leftGroup: false };
}
