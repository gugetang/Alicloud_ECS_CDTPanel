// 推送时间手动输入：边输边自动补齐 ":"。
// 只留数字、最多 4 位；输满 2 位后自动加冒号，输 0000 即 00:00。
// 粘贴带冒号的 00:00 也一样处理。
export function formatTimeInput(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 4);
  if (digits.length <= 2) return digits;
  return digits.slice(0, 2) + ":" + digits.slice(2);
}
