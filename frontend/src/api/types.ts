export type Role = 'Admin' | 'Manager' | 'Employee'
export type Clearance = 'Public' | 'Internal' | 'Confidential'

export interface User {
  id: string
  name: string
  username: string
  email: string
  job_title: string
  role: Role
  department: string
  clearance_level: Clearance
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface Page<T> {
  items: T[]
  page: number
  page_size: number
  total: number
  total_pages: number
}

export interface Conversation {
  id: string
  user_id: string
  title: string
  summary?: string | null
  created_at: string
  updated_at: string
}

export interface EvidenceRef {
  anchor: number
  evidence_id: string
  citation: string
  section: string
}

export interface CitationData {
  citations: string[]
  citation_map: Record<string, string>
  evidence_chunks: EvidenceRef[]
}

export interface ChatMessage {
  id: string
  conversation_id: string
  sender: 'user' | 'assistant'
  content: string
  citations?: CitationData | string[] | null
  variant_index: number
  parent_message_id?: string | null
  is_complete?: boolean
  created_at: string
}

export interface DocumentRecord {
  id: string
  title: string
  filename: string
  department: string
  clearance_level: Clearance
  version: number
  status: 'ACTIVE' | 'UPDATING' | 'UPDATE_FAILED'
  is_live: boolean
  is_latest: boolean
  has_source_file: boolean
  uploaded_at: string
  updated_at: string
}

export interface AdminHealth {
  overall: string
  timestamp: string
  services: Record<string, { status: string; detail: string; optional?: boolean }>
  llm_providers?: Record<string, string>
}
