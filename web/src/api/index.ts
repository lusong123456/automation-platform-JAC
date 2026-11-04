// API 封装：统一 baseURL 与后端错误结构 {code, message}
import axios from 'axios'

import type {
  AccuracyRunsResponse,
  AccuracyRunDetail,
  AiDraftResponse,
  CasesResponse,
  ContractItem,
  ContractsResponse,
  RunCreateResponse,
  RunDetail,
  RunsResponse,
  StandardGroup,
  StandardResponse,
  TemplateResponse,
} from '../types'

/** 带业务 code 的错误；409 场景额外携带 run_id */
export class ApiError extends Error {
  code: string
  runId?: string

  constructor(message: string, code: string, runId?: string) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.runId = runId
  }
}

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// 统一把后端错误转换为带中文 message 的 ApiError
http.interceptors.response.use(
  (response) => response,
  (error) => {
    const response = error?.response
    const data = response?.data
    if (data && typeof data === 'object') {
      // 兼容两类后端错误结构：
      // 1. 业务 JSON：{code: 401, msg: "..."}
      // 2. FastAPI HTTPException：{detail: "..."}
      const message = data.msg || data.message || data.detail || '请求失败，请稍后重试'
      const code = data.code || String(response.status || '')
      return Promise.reject(new ApiError(message, code, data.run_id))
    }
    if (error?.code === 'ECONNABORTED') {
      return Promise.reject(new ApiError('请求超时，请检查后端服务是否正常', 'TIMEOUT'))
    }
    return Promise.reject(new ApiError('网络异常，无法连接后端服务', 'NETWORK_ERROR'))
  },
)

/** 获取用例清单（按模块分组） */
export async function getCases(): Promise<CasesResponse> {
  const { data } = await http.get<CasesResponse>('/cases')
  return data
}

/** 重新收集用例清单 */
export async function refreshCases(): Promise<CasesResponse> {
  const { data } = await http.post<CasesResponse>('/cases/refresh')
  return data
}

/** 勾选用例并触发执行 */
export async function createRun(case_ids: string[], triggered_by: string): Promise<RunCreateResponse> {
  const { data } = await http.post<RunCreateResponse>('/runs', { case_ids, triggered_by })
  return data
}

/** 查询单次执行详情（含实时进度） */
export async function getRun(run_id: string): Promise<RunDetail> {
  const { data } = await http.get<RunDetail>(`/runs/${encodeURIComponent(run_id)}`)
  return data
}

/** 执行记录分页列表（服务端分页） */
export async function listRuns(limit = 20, offset = 0): Promise<RunsResponse> {
  const { data } = await http.get<RunsResponse>('/runs', { params: { limit, offset } })
  return data
}

// ──────────────────── 审核正确性（Accuracy） ────────────────────

/** 列出所有有审核正确性详情的执行 */
export async function listAccuracyRuns(limit = 20): Promise<AccuracyRunsResponse> {
  const { data } = await http.get<AccuracyRunsResponse>('/accuracy/runs', { params: { limit } })
  return data
}

/** 获取单条 accuracy 执行的完整 diff 详情 */
export async function getAccuracyDetail(run_id: string): Promise<AccuracyRunDetail> {
  const { data } = await http.get<AccuracyRunDetail>(`/accuracy/runs/${encodeURIComponent(run_id)}`)
  return data
}

// ──────────────────── 合同-标答管理（Contracts） ────────────────────

/** 上传 .docx 合同 */
export async function uploadContract(file: File): Promise<ContractItem> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<ContractItem>('/contracts/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return data
}

/** 启动 AI 初稿任务（服务端后台执行，切页不中断） */
export interface AiDraftSSEEvent {
  step?: string          // upload / extract / commit / poll / report
  status?: string        // running / done / fail
  desc?: string          // 中文描述
  elapsed?: number       // 秒
  idx?: number           // 轮询当前次
  total?: number         // 轮询总次数
  auditStatus?: string   // 后端审核状态
  issues?: number        // report 阶段返回的 issues 数量
  done?: boolean         // 最终结果标记
  // 最终结果字段
  contract_name?: string
  standard_data?: any[]
  total_groups?: number
  total_issues?: number
  note?: string
}

export interface AiDraftTaskInfo {
  task_id: string
  contract_name: string
  stem: string
  status: 'running' | 'success' | 'fail'
  events: AiDraftSSEEvent[]
  result: AiDraftSSEEvent | null
  error: string
  created_at: number
}

export async function startAiDraft(file: File): Promise<{ task_id: string; contract_name: string; stem: string }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await http.post<{ task_id: string; contract_name: string; stem: string }>(
    '/contracts/ai-draft/start', form, { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return data
}

/** 最近一个 AI 初稿任务（含已结束未过期的），用于切页返回时恢复 */
export async function getActiveAiDraft(): Promise<AiDraftTaskInfo | null> {
  const { data } = await http.get<AiDraftTaskInfo | { task_id: string }>('/contracts/ai-draft/active')
  return 'status' in data && data.task_id ? (data as AiDraftTaskInfo) : null
}

/** 订阅任务进度：从 offset 开始回放历史事件，再持续推送新增事件，任务结束自动关闭 */
export async function streamAiDraft(
  taskId: string,
  offset: number,
  onEvent: (evt: AiDraftSSEEvent) => void,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  const resp = await fetch(`/api/contracts/ai-draft/${taskId}/stream?offset=${offset}`, {
    credentials: 'include',
    signal: opts?.signal,
    headers: {
      ...(localStorage.getItem('token') ? { Authorization: 'Bearer ' + localStorage.getItem('token') } : {}),
    },
  })
  if (!resp.ok || !resp.body) {
    throw new Error(`订阅进度失败 ${resp.status}`)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop() || ''
    for (const part of parts) {
      for (const line of part.split('\n')) {
        if (!line.startsWith('data: ')) continue
        const json = line.slice(6).trim()
        if (!json) continue
        try {
          onEvent(JSON.parse(json) as AiDraftSSEEvent)
        } catch { /* ignore parse error */ }
      }
    }
  }
}

/** 列出所有合同（含配对状态） */
export async function listContracts(): Promise<ContractItem[]> {
  const { data } = await http.get<ContractsResponse>('/contracts')
  return data.items
}

/** 删除合同（同时删除配对的标答） */
export async function deleteContract(name: string): Promise<{ deleted: { contract: boolean; standard: boolean }; stem: string }> {
  const { data } = await http.delete(`/contracts/${encodeURIComponent(name)}`)
  return data
}

/** 读取指定标答 JSON */
export async function getStandard(name: string): Promise<StandardResponse> {
  const { data } = await http.get<StandardResponse>(`/standards/${encodeURIComponent(name)}`)
  return data
}

/** 保存/覆盖指定标答 JSON */
export async function saveStandard(name: string, groups: StandardGroup[]): Promise<{ name: string; stem: string; groups: number }> {
  const { data } = await http.put(`/standards/${encodeURIComponent(name)}`, groups)
  return data
}

/** 生成标答模板（可指定合同名或合同类型关键词） */
export async function getTemplate(contract?: string, contract_type?: string): Promise<TemplateResponse> {
  const params: Record<string, string> = {}
  if (contract) params.contract = contract
  if (contract_type) params.contract_type = contract_type
  const { data } = await http.get<TemplateResponse>('/template', { params })
  return data
}
