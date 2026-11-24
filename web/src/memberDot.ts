// 分组成员选择区：未选中实例的显示状态。
//
// 后端 status_dot 是全局状态（Running 且被某个域名指向 = green），
// 在本分组里未选中的实例不能显示正在运行：green（在线）、green_pulse
// （启动中）、yellow（排队中）都改成灰点并标“离队”。已停机（red /
// red_pulse）是实例本身的状态、不是在组内运行，保留；其他灰点状态
// （已跳出/未参与/未知）保持不变。
export function groupMemberDot(
  selected: boolean,
  statusDot: string | undefined,
): { dot: string | undefined; leftGroup: boolean } {
  const leftGroup =
    !selected &&
    (statusDot === "green" ||
      statusDot === "green_pulse" ||
      statusDot === "yellow");
  return { dot: leftGroup ? "gray" : statusDot, leftGroup };
}
