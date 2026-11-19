import type { Page, User } from './types'

function resolveApiBase(): string {
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL.replace(/\/$/, '')
  }
  if (typeof window !== 'undefined' && window.location?.hostname) {
    return `${window.location.protocol}//${window.location.hostname}:8000`
  }
  return 'http://localhost:8000'
}

const API_BASE = resolveApiBase()

export class ApiError extends Error {
  status: number
  payload: unknown
  constructor(message: string, status: number, payload?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
}

async function decodeResponse<T>(response: Response): Promise<T> {
  const contentType = response.headers.get('content-type') || ''
  const payload = contentType.includes('application/json') ? await response.json().catch(() => null) : await response.text()
  if (!response.ok) {
    const detail = typeof payload === 'object' && payload && 'detail' in payload
      ? String((payload as { detail: unknown }).detail)
      : typeof payload === 'string' && payload ? payload : `Request failed (${response.status})`
    throw new ApiError(detail, response.status, payload)
  }
  return payload as T
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (!(init.body instanceof FormData) && init.body !== undefined && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  })
  return decodeResponse<T>(response)
}

export function apiUrl(path: string) {
  return `${API_BASE}${path}`
}

export interface StreamEvent {
  type: string
  data: any
  id?: string
}

function parseFrame(frame: string): StreamEvent | null {
  if (!frame.trim()) return null
  let type = 'message'
  let id: string | undefined
  const dataLines: string[] = []
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(':')) continue
    const separator = line.indexOf(':')
    const field = separator < 0 ? line : line.slice(0, separator)
    const value = separator < 0 ? '' : line.slice(separator + 1).replace(/^ /, '')
    if (field === 'event') type = value
    if (field === 'id') id = value
    if (field === 'data') dataLines.push(value)
  }
  if (!dataLines.length) return null
  const raw = dataLines.join('\n')
  let data: any = raw
  try { data = JSON.parse(raw) } catch { /* keep raw text */ }
  return { type, data, id }
}

export async function streamChat(
  path: string,
  body: Record<string, unknown>,
  onEvent: (event: StreamEvent) => void,
  onUnauthorized: () => void,
  signal?: AbortSignal,
) {
  let lastEventId = ''
  let reconnects = 0
  const terminal = new Set(['done', 'error', 'interrupted', 'session_terminated'])

  while (reconnects <= 2) {
    let response: Response
    try {
      const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' })
      if (reconnects > 0) headers.set('Last-Event-ID', lastEventId || '0-0')
      response = await fetch(apiUrl(path), {
        method: 'POST',
        headers,
        credentials: 'include',
        body: JSON.stringify(body),
        signal,
      })
    } catch (error) {
      if (signal?.aborted) throw error
      reconnects += 1
      if (reconnects > 2) throw error
      continue
    }

    if (response.status === 401) {
      onUnauthorized()
      throw new ApiError('Your session has expired.', 401)
    }
    if (!response.ok || !response.body) {
      await decodeResponse(response)
      throw new ApiError('Streaming response unavailable.', response.status)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let shouldReconnect = false
    let finished = false

    try {
      while (!finished) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let boundary = buffer.search(/\r?\n\r?\n/)
        while (boundary >= 0) {
          const frame = buffer.slice(0, boundary)
          const match = buffer.slice(boundary).match(/^\r?\n\r?\n/)
          buffer = buffer.slice(boundary + (match?.[0].length || 2))
          const event = parseFrame(frame)
          if (event) {
            if (event.id) lastEventId = event.id
            onEvent(event)
            if (event.type === 'error' && event.data?.retryable && reconnects < 2) {
              shouldReconnect = true
              finished = true
              break
            }
            if (terminal.has(event.type)) finished = true
          }
          boundary = buffer.search(/\r?\n\r?\n/)
        }
      }
      if (buffer.trim() && !finished) {
        const event = parseFrame(buffer)
        if (event) {
          if (event.id) lastEventId = event.id
          onEvent(event)
          if (terminal.has(event.type)) finished = true
        }
      }
    } finally {
      reader.releaseLock()
    }

    if (shouldReconnect) {
      reconnects += 1
      continue
    }
    if (finished) return
    if (signal?.aborted) return
    reconnects += 1
    if (reconnects > 2) throw new ApiError('The response stream disconnected. Reload the conversation to view the saved response.', 0)
  }
}

export async function getMe() {
  return api<User>('/api/v1/auth/me')
}

export function pageQuery(page: number, pageSize: number, extra: Record<string, string | undefined> = {}) {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  Object.entries(extra).forEach(([key, value]) => { if (value) params.set(key, value) })
  return params.toString()
}

export type { Page }
