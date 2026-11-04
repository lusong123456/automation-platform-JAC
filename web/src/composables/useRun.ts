// 执行进度组合式函数：SSE 实时推送优先，轮询 fallback
import { onMounted, onUnmounted, ref } from 'vue'

import { getRun } from '../api'
import type { RunDetail, SubStepEvent } from '../types'

const POLL_INTERVAL = 3000
const SSE_RECONNECT_DELAY = 2000

export function useRun() {
  /** 当前执行详情（含实时进度） */
  const run = ref<RunDetail | null>(null)
  /** 当前 run_id */
  const runId = ref<string | null>(null)
  /** 是否处于进度跟踪中 */
  const polling = ref(false)
  /** 轮询过程中的错误提示 */
  const error = ref<string | null>(null)
  /** 子步骤实时事件（SSE 推送） */
  const subSteps = ref<SubStepEvent[]>([])
  /** 最近一条子步骤（供 UI 显示） */
  const latestSubStep = ref<SubStepEvent | null>(null)

  let timer: number | null = null
  let sseAbort: AbortController | null = null
  let sseReconnectTimer: number | null = null
  let stopped = false

  /** 停掉所有跟踪（SSE + 轮询） */
  function stop() {
    stopped = true
    // 停 SSE
    if (sseAbort) {
      sseAbort.abort()
      sseAbort = null
    }
    if (sseReconnectTimer !== null) {
      window.clearTimeout(sseReconnectTimer)
      sseReconnectTimer = null
    }
    // 停轮询
    if (timer !== null) {
      window.clearInterval(timer)
      timer = null
    }
    polling.value = false
  }

  /** 拉一次详情（轮询 fallback） */
  async function pollOnce() {
    if (!runId.value) return
    try {
      const detail = await getRun(runId.value)
      run.value = detail
      error.value = null
      if (detail.status !== 'running') {
        stop()
      }
    } catch (e) {
      error.value = (e as Error).message || '查询执行进度失败'
    }
  }

  /** 解析 SSE 流式 */
  async function connectSSE() {
    if (!runId.value || stopped) return
    stopped = false
    const ac = new AbortController()
    sseAbort = ac
    const token = localStorage.getItem('token') || ''  // 可能不需要
    const url = `/api/runs/${encodeURIComponent(runId.value)}/stream`

    try {
      const resp = await fetch(url, {
        signal: ac.signal,
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!resp.ok || !resp.body) {
        throw new Error(`SSE 连接失败: ${resp.status}`)
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (!stopped) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按 SSE 格式逐行解析："data: {...}\n\n"
        const idx = buffer.indexOf('\n\n')
        if (idx === -1) continue
        const chunk = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)

        const lines = chunk.split('\n')
        for (const line of lines) {
          if (!line.startsWith('data:')) continue
          const jsonStr = line.slice(5).trim()
          if (!jsonStr) continue
          try {
            const evt = JSON.parse(jsonStr)
            if (evt.done) {
              // 用 SSE 结束时再拉一次完整详情（确保最终状态准确）
              await pollOnce()
              stop()
              return
            }
            // 更新 run 基础字段
            if (evt.case_executed !== undefined) {
              if (!run.value) run.value = { case_total: 0, status: 'running' } as RunDetail
              run.value.case_executed = evt.case_executed
              run.value.case_passed = evt.case_passed
              run.value.case_failed = evt.case_failed
              run.value.case_total = evt.case_total
              run.value.current_case = evt.current_case
              run.value.status = evt.status
            }
            // 更新子步骤
            if (evt.latest_sub_step) {
              const step = evt.latest_sub_step as SubStepEvent
              latestSubStep.value = step
              // 只追加不同的子步骤，避免重复
              const last = subSteps.value[subSteps.value.length - 1]
              if (!last || last.id !== step.id) {
                subSteps.value.push(step)
              }
            }
          } catch {
            // 忽略解析失败
          }
        }
      }
    } catch (e) {
      // SSE 断了 → fallback 轮询（如果还没停）
      if (!stopped) {
        console.warn('[useRun] SSE 断开，切换到轮询 fallback:', e)
        error.value = null  // 轮询可能还能工作
        startPollingOnly()
      }
    }
  }

  /** 纯轮询 fallback（SSE 断了用） */
  function startPollingOnly() {
    if (timer !== null) return
    timer = window.setInterval(() => {
      void pollOnce()
    }, POLL_INTERVAL)
  }

  /** 启动跟踪（SSE + 轮询备份） */
  function start(id: string) {
    stop()
    runId.value = id
    run.value = null
    subSteps.value = []
    latestSubStep.value = null
    error.value = null
    polling.value = true

    // 立即拉一次详情
    void pollOnce()

    // 同时开 SSE 和轮询备份
    void connectSSE()
    timer = window.setInterval(() => {
      void pollOnce()
    }, POLL_INTERVAL)
  }

  /** 组件 mount 时检查是否有残留的 running run（切换页面回来恢复） */
  async function restoreActiveRun() {
    try {
      const resp = await fetch('/api/runs/active')
      if (!resp.ok) return
      const data = await resp.json()
      if (data.run && data.run.status === 'running') {
        run.value = data.run
        runId.value = data.run.run_id
        subSteps.value = data.run.sub_steps || []
        if (subSteps.value.length > 0) {
          latestSubStep.value = subSteps.value[subSteps.value.length - 1]
        }
        polling.value = true
        // 重新开 SSE
        void connectSSE()
        timer = window.setInterval(() => {
          void pollOnce()
        }, POLL_INTERVAL)
      }
    } catch {
      // 静默失败
    }
  }

  onMounted(() => {
    void restoreActiveRun()
  })

  onUnmounted(stop)

  return { run, polling, error, subSteps, latestSubStep, start, stop }
}
