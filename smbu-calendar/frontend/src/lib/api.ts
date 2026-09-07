export type Lang = 'zh' | 'en' | 'ru'

export interface Term {
  code: string
  name: string
  current: boolean
}

export interface TermsResponse {
  terms: Term[]
  default_code: string | null
}

export interface Section {
  index: number
  start: string
  end: string
}

export interface Meeting {
  name: string
  day_of_week: number
  start_section: number
  end_section: number
  weeks: number[]
  week_label: string
  section_label: string
  teacher: string | null
  location: string | null
  code: string | null
  class_name: string | null
}

export interface ScheduleResponse {
  term_code: string
  term_name: string
  weeks: { index: string; start: string; end: string }[]
  sections: Section[]
  meetings: Meeting[]
  event_count: number
  warnings: string[]
}

export interface LoginResponse {
  token: string
  student_id: string
  display_name: string
  expires_in: number
}

export class ApiError extends Error {
  code: string
  status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.code = code
    this.status = status
  }
}

const TOKEN_KEY = 'smbu.token'

export const tokenStore = {
  get(): string | null {
    try {
      return sessionStorage.getItem(TOKEN_KEY)
    } catch {
      return null
    }
  },
  set(token: string) {
    try {
      sessionStorage.setItem(TOKEN_KEY, token)
    } catch {
      /* private mode — the in-memory copy still works for this tab */
    }
  },
  clear() {
    try {
      sessionStorage.removeItem(TOKEN_KEY)
    } catch {
      /* noop */
    }
  },
}

async function request<T>(
  path: string,
  init: RequestInit & { token?: string | null } = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  const token = init.token ?? tokenStore.get()
  if (token) headers.set('X-Session-Token', token)
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  let res: Response
  try {
    res = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  } catch {
    throw new ApiError('NETWORK', 'network unreachable', 0)
  }

  if (res.status === 204) return undefined as T

  const text = await res.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }

  if (!res.ok) {
    const err = (payload as { error?: { code?: string; message?: string } })?.error
    throw new ApiError(err?.code ?? 'UNKNOWN', err?.message ?? `HTTP ${res.status}`, res.status)
  }
  return payload as T
}

export const api = {
  login(username: string, password: string, captcha = '') {
    return request<LoginResponse>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, captcha }),
    })
  },

  logout(token: string) {
    return request<{ ok: boolean }>('/api/auth/logout', { method: 'POST', token })
  },

  terms(token: string) {
    return request<TermsResponse>('/api/terms', { token })
  },

  schedule(token: string, term?: string) {
    const qs = term ? `?term=${encodeURIComponent(term)}` : ''
    return request<ScheduleResponse>(`/api/schedule${qs}`, { token })
  },

  icsUrl(term: string | null, lang: Lang, reminder: number) {
    const params = new URLSearchParams()
    if (term) params.set('term', term)
    params.set('lang', lang)
    params.set('reminder', String(reminder))
    return `/api/export.ics?${params.toString()}`
  },
}
