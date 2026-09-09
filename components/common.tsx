'use client';

import Link from 'next/link';
import { ArrowUpRight, Bookmark, BookOpen, Check, ChevronRight, FileText, LoaderCircle, RefreshCw, X } from 'lucide-react';
import { useState } from 'react';
import type { Job, Resource } from '@/lib/client-types';
import { api, label, useRoom } from './ui-context';

export function PageHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description?: string; action?: React.ReactNode }) {
  return <header className="page-heading"><div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1>{description && <p>{description}</p>}</div>{action}</header>;
}
export function Empty({ title, description, children }: { title: string; description?: string; children?: React.ReactNode }) {
  return <div className="empty-state"><BookOpen size={28} strokeWidth={1.4}/><h3>{title}</h3>{description && <p>{description}</p>}{children}</div>;
}
export function Loading({ text = '자료를 불러오고 있습니다' }: { text?: string }) { return <div className="loading-state" role="status"><LoaderCircle size={22} className="spin"/>{text}</div>; }
export function ErrorPanel({ error, retry }: { error: string; retry?: () => void }) { return <div className="error-panel" role="alert"><strong>잠시 확인이 필요합니다</strong><p>{error}</p>{retry && <button className="button small" onClick={retry}><RefreshCw size={15}/> 다시 시도</button>}</div>; }
export function SourceBadge({ resource }: { resource: Resource }) { return resource.versionId ? <span className="source-status ready"><span/>원문 저장됨</span> : <span className="source-status"><span/>{resource.sourceStatus === 'failed' ? '가져오기 실패' : '가져오기 전'}</span>; }
export function ResourceCard({ resource, compact = false }: { resource: Resource; compact?: boolean }) {
  const { data, run, refresh } = useRoom();
  const bookmark = data.bookmarks.find(b => b.resourceId === resource.id && !b.sourceVersionId);
  const [saving, setSaving] = useState(false);
  async function toggle() { setSaving(true); await run(async () => { await api(bookmark ? `/api/bookmarks/${bookmark.id}` : '/api/bookmarks', bookmark ? 'DELETE' : 'POST', bookmark ? undefined : { resourceId: resource.id }); await refresh(); }, bookmark ? '북마크를 해제했습니다.' : '북마크에 담았습니다.'); setSaving(false); }
  const detailHref = `/resources/${resource.id}`;
  return <article className={`resource-card ${compact ? 'compact' : ''}`}><div className="resource-card-top"><span className={`file-icon ${resource.format.toLowerCase().includes('xls') || resource.format === 'excel' ? 'excel-icon' : ''}`}><FileText size={19}/></span><span className="small-caps">{label(resource.format.toLowerCase())}</span><button className={`icon-button bookmark-action ${bookmark ? 'is-bookmarked' : ''}`} aria-label={`${resource.titleKo} ${bookmark ? '북마크 해제' : '북마크'}`} disabled={saving} onClick={toggle}><Bookmark size={18} fill={bookmark ? 'currentColor' : 'none'}/></button></div><Link className="card-title" href={detailHref}>{resource.titleKo}<ArrowUpRight size={17}/></Link><p className="resource-english" lang="en">{resource.titleEn}</p><p className="resource-summary">{resource.summaryKo}</p><div className="tag-row"><span className={`tag ${resource.priority === 'essential' || resource.priority === '필수' ? 'blue' : ''}`}>{label(resource.priority)}</span><span className="tag">{label(resource.level)}</span><span className="tag">{label(resource.kind)}</span></div><div className="resource-card-bottom"><SourceBadge resource={resource}/>{resource.blockCount > 0 && <span>{resource.translatedCount}/{resource.blockCount} 문단 번역</span>}</div></article>;
}
export function ResourceRow({ resource, number }: { resource: Resource; number?: number }) {
  return <Link href={`/resources/${resource.id}`} className="resource-row"><span className="row-number">{number ? String(number).padStart(2, '0') : <FileText size={18}/>}</span><span className="row-copy"><strong>{resource.titleKo}</strong><span>{resource.summaryKo}</span></span><span className="tag">{label(resource.format.toLowerCase())}</span><ChevronRight size={18}/></Link>;
}
export function JobCard({ job }: { job: Job }) {
  const { run, refresh } = useRoom(); const [busy, setBusy] = useState(false);
  async function action(type: string) { setBusy(true); await run(async () => { await api(`/api/jobs/${job.id}/${type}`, 'POST'); await refresh(); }, type === 'cancel' ? '작업 취소를 요청했습니다.' : '미완료 작업을 다시 요청했습니다.'); setBusy(false); }
  const active = ['queued', 'running'].includes(job.status);
  return <div className="job-card"><div className="row-between"><strong>{label(job.type)}</strong><span className={`tag ${job.status === 'failed' ? 'red' : active ? 'blue' : ''}`}>{label(job.status)}</span></div><small>작업 {job.id.slice(0, 8)} · 전체 {job.total}개</small><div className="progress-track"><span style={{ width: `${job.total ? Math.min(100, ((job.completed + job.failed + job.needsReview) / job.total) * 100) : 0}%` }}/></div><p className="job-stats">완료 {job.completed} · 검토 {job.needsReview} · 실패 {job.failed} · 남음 {job.remaining}</p>{job.errorMessage && <p className="inline-error">{job.errorMessage}</p>}<div className="job-actions">{job.resourceId && <Link href={`/resources/${job.resourceId}`} className="text-link">자료 보기 <ChevronRight size={14}/></Link>}{active ? <button className="button small secondary" disabled={busy} onClick={() => action('cancel')}><X size={14}/> 이 작업 전체 취소</button> : ['failed', 'partial', 'cancelled'].includes(job.status) && <button className="button small secondary" disabled={busy} onClick={() => action('retry')}><RefreshCw size={14}/> 미완료 재시도</button>}</div>{active && <small>취소해도 이미 전송된 번역 호출의 사용량은 발생할 수 있습니다.</small>}</div>;
}
export function CheckList({ items }: { items: string[] }) { return <ul className="check-list">{items.map((item, index) => <li key={index}><Check size={16}/><span>{item}</span></li>)}</ul>; }
