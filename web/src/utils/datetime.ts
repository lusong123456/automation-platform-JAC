/** 把后端 ISO 时间按浏览器本地时区展示；兼容历史数据（无时区标记，按 UTC 解释）。 */
export function formatLocalTime(value: string | null | undefined): string {
  const date = parseLocal(value)
  if (!date) return value || '—'

  const now = new Date()
  const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`

  if (isSameDay(date, now)) return `今天 ${time}`
  if (isSameDay(date, new Date(now.getTime() - 86400000))) return `昨天 ${time}`
  if (date.getFullYear() === now.getFullYear()) {
    return `${date.getMonth() + 1}月${date.getDate()}日 ${time}`
  }
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${time}`
}

/** 耗时易读化：毫秒 / 秒 / 分秒 / 小时分。 */
export function formatDurationText(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—'
  if (ms < 1000) return `${Math.round(ms)} 毫秒`

  const totalSeconds = ms / 1000
  if (totalSeconds < 60) return `${totalSeconds.toFixed(1)} 秒`

  const totalMinutes = Math.floor(totalSeconds / 60)
  const seconds = Math.round(totalSeconds % 60)
  if (totalMinutes < 60) {
    return seconds ? `${totalMinutes} 分 ${seconds} 秒` : `${totalMinutes} 分`
  }

  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`
}

function parseLocal(value: string | null | undefined): Date | null {
  if (!value) return null
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
  const date = new Date(hasZone ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? null : date
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
}

function pad(value: number): string {
  return String(value).padStart(2, '0')
}
