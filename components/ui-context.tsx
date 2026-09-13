'use client';

import { createContext, useContext } from 'react';
import type { Bootstrap, Bookmark, Note, Position, Resource } from '@/lib/client-types';

export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(path, { method, cache: 'no-store', ...(body === undefined ? {} : body instanceof FormData ? { body } : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) });
  let result;
  try { result = await response.json(); } catch { throw new Error('서버 응답을 읽지 못했습니다. 서버가 실행 중인지 확인해 주세요.'); }
  if (!response.ok) throw new Error(result.message || result.error?.message || (typeof result.error === 'string' ? result.error : '요청을 처리하지 못했습니다. 다시 시도해 주세요.'));
  return result as T;
}

export const RoomContext = createContext<{ data: Bootstrap; refresh: () => Promise<void>; run: <T>(work: () => Promise<T>, message?: string) => Promise<T | null>; notify: (message: string, error?: boolean) => void } | null>(null);
export function useRoom() { const context = useContext(RoomContext); if (!context) throw new Error('학습 데이터가 준비되지 않았습니다.'); return context; }
export const label = (value: string) => ({ essential: '필수', supplementary: '보충', advanced: '심화', reference: '상시 참조', beginner: '입문', intermediate: '기초·응용', article: '읽을거리', primer: '입문 글', catalog: '자료 목록', index: '자료 목록', collection: '자료 목록', text: '본문', body: '본문', blog: '블로그', excel: 'Excel', spreadsheet: 'Excel', slides: '슬라이드', pdf: 'PDF', html: 'HTML', xls: 'XLS', xlsx: 'XLSX', problem: '연습문제', problems: '연습문제', solution: '해답', tool: '실습 도구', not_started: '시작 전', in_progress: '학습 중', completed: '학습 완료', queued: '대기 중', running: '처리 중', failed: '실패', partial: '일부 완료', cancelled: '취소됨', imported: '원문 저장됨', ready: '읽기 가능', available: '읽기 가능', not_imported: '가져오기 전', pending: '추출 대기', needs_ocr: 'OCR 필요', ocr_needed: 'OCR 필요', not_applicable: '원본 보관', ocr_required: 'OCR 필요', needs_review: '검토 필요', import: '원문 가져오기', translation: '한국어 번역', quality: '번역 의미 검사', extract: 'PDF 추출' }[value] || value);
export function dateLabel(value?: string | null) { return value ? new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(value)) : '확인되지 않음'; }
export function readerLink(item: Position | Note | Bookmark | Resource) {
  if ('sourceVersionId' in item && item.sourceVersionId) {
    const query = new URLSearchParams({ versionId: item.sourceVersionId });
    if (item.blockId) query.set('blockId', item.blockId);
    if (item.pageIndex != null) query.set('page', String(item.pageIndex + 1));
    if ('languageMode' in item) query.set('mode', item.languageMode);
    return `/reader/${item.resourceId}?${query}`;
  }
  if ('resourceId' in item) return `/resources/${item.resourceId}`;
  return `/reader/${item.id}`;
}
