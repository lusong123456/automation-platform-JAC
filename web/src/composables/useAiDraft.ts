import { reactive } from 'vue'

import {
  getActiveAiDraft,
  startAiDraft,
  streamAiDraft,
  type AiDraftSSEEvent,
  type AiDraftTaskInfo,
} from '../api'
import type { AiDraftResponse } from '../types'

export type AiDraftPhase = 'idle' | 'running' | 'success' | 'fail'

/** 模块级状态：不随组件卸载销毁，切页回来进度和结果都还在 */
const state = reactive({
  taskId: '',
  contractName: '',
  stem: '',
  phase: 'idle' as AiDraftPhase,
  events: [] as AiDraftSSEEvent[],
  result: null as AiDraftResponse | null,
  error: '',
})

let abort: AbortController | null = null
let consumed = false  // 结果是否已被页面消费（回填编辑器），避免重复弹抽屉

function toResult(evt: AiDraftSSEEvent | null): AiDraftResponse | null {
  if (!evt) return null
  return {
    contract_name: evt.contract_name || '',
    standard_data: (evt.standard_data || []) as AiDraftResponse['standard_data'],
    total_groups: evt.total_groups || 0,
    total_issues: evt.total_issues || 0,
    note: evt.note ?? '',
  }
}

function applySnapshot(info: AiDraftTaskInfo) {
  state.taskId = info.task_id
  state.contractName = info.contract_name
  state.stem = info.stem
  state.events = info.events || []
  state.result = toResult(info.result)
  state.error = info.error || ''
  state.phase = info.status === 'running' ? 'running' : info.status === 'success' ? 'success' : 'fail'
}

function reset() {
  abort?.abort()
  abort = null
  state.taskId = ''
  state.contractName = ''
  state.stem = ''
  state.phase = 'idle'
  state.events = []
  state.result = null
  state.error = ''
  consumed = false
}

/** 订阅进度（模块级，不随页面卸载中断） */
async function follow() {
  if (!state.taskId) return
  abort?.abort()
  const ctrl = new AbortController()
  abort = ctrl
  const offset = state.events.length
  try {
    await streamAiDraft(
      state.taskId,
      offset,
      (evt) => {
        if (evt.done) {
          state.result = toResult(evt)
          state.phase = 'success'
          consumed = false
        } else {
          state.events.push(evt)
          if (evt.status === 'fail' && evt.desc) {
            state.error = evt.desc
            state.phase = 'fail'
          }
        }
      },
      { signal: ctrl.signal },
    )
  } catch (e) {
    if ((e as Error).name === 'AbortError') return
    // 连接异常不代表任务失败：服务端仍在跑，下次恢复会补齐
    if (state.phase === 'running') state.error = (e as Error).message
  } finally {
    if (abort === ctrl) abort = null
  }
}

async function start(contract: string): Promise<void> {
  reset()
  const info = await startAiDraft(contract)
  state.taskId = info.task_id
  state.contractName = info.contract_name
  state.stem = info.stem
  state.phase = 'running'
  void follow()
}

/** 页面挂载时恢复：优先复用内存里的任务，否则问服务端要最近一个任务 */
async function restore(): Promise<boolean> {
  if (state.taskId && state.phase === 'running') {
    void follow()
    return true
  }
  const info = await getActiveAiDraft()
  if (!info) return false
  applySnapshot(info)
  if (state.phase === 'running') void follow()
  return true
}

/** 取一次生成结果（只消费一次） */
function consumeResult(): AiDraftResponse | null {
  if (!state.result || consumed) return null
  consumed = true
  return state.result
}

export function useAiDraft() {
  return { state, start, restore, consumeResult, reset }
}
