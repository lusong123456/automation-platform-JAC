/** 把后端 ISO 时间按浏览器本地时区展示；兼容历史数据（无时区标记，按 UTC 解释）。 */
export function formatLocalTime(value: string | null | undefined): string {
  if (!value) return '—'
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  const date = new Date(hasZone ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
