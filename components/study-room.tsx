'use client';

import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowRight, ArrowUpRight, BookMarked, BookOpen, Bookmark, Check, ChevronRight, CircleCheck, CircleHelp, Clock3, CloudDownload, Download, ExternalLink, FileText, FolderOpen, HardDriveDownload, Home, Languages, Library, Menu, NotebookPen, Route, Search, Settings2, ShieldCheck, SlidersHorizontal, Sparkles, Table2, Trash2, Upload, X } from 'lucide-react';
import type { Bootstrap, Module, Note, Resource, ResourceDetail, Term } from '@/lib/client-types';
import { api, dateLabel, label, readerLink, RoomContext, useRoom } from './ui-context';
import { CheckList, Empty, ErrorPanel, JobCard, Loading, PageHeading, ResourceCard, ResourceRow, SourceBadge } from './common';
import Reader from './reader';

const navigation = [{ path: '/', title: '공부 홈', icon: Home }, { path: '/learn', title: '학습 경로', icon: Route }, { path: '/library', title: '자료실', icon: Library }, { path: '/tools', title: 'Excel 도구실', icon: Table2 }, { path: '/glossary', title: '금융 용어사전', icon: BookOpen }, { path: '/notes', title: '내 학습 기록', icon: NotebookPen }];

export default function StudyRoom() {
  const pathname = usePathname(); const params = useSearchParams();
  const [data, setData] = useState<Bootstrap | null>(null); const [error, setError] = useState('');
  const [notice, setNotice] = useState<{ text: string; error: boolean } | null>(null);
  const [menuOpen, setMenuOpen] = useState(false); const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refresh = useCallback(async () => { const next = await api<Bootstrap>('/api/bootstrap'); setData(next); setError(''); }, []);
  const notify = useCallback((text: string, isError = false) => { setNotice({ text, error: isError }); if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => setNotice(null), 6500); }, []);
  const run = useCallback(async <T,>(work: () => Promise<T>, message?: string): Promise<T | null> => { try { const result = await work(); if (message) notify(message); return result; } catch (e) { notify(e instanceof Error ? e.message : '요청을 처리하지 못했습니다.', true); return null; } }, [notify]);
  useEffect(() => { refresh().catch(e => setError(e.message)); return () => { if (timer.current) clearTimeout(timer.current); }; }, [refresh]);
  useEffect(() => { setMenuOpen(false); }, [pathname]);
  const hasActiveJobs = data?.jobs.some(j => ['queued', 'running'].includes(j.status));
  useEffect(() => { if (!hasActiveJobs) return; const poll = setInterval(() => { refresh().catch(() => {}); }, 1500); return () => clearInterval(poll); }, [hasActiveJobs, refresh]);
  const segment = pathname.split('/').filter(Boolean); const isReader = segment[0] === 'reader';
  const activeNav = navigation.find(n => n.path === '/' ? pathname === '/' : pathname.startsWith(n.path)) || (pathname.startsWith('/resources') || isReader ? navigation[2] : null);
  const finished = data?.progress.filter(p => p.status === 'completed').length || 0;
  let content: React.ReactNode = null;
  if (data) {
    if (!segment.length) content = <Dashboard/>;
    else if (segment[0] === 'learn') content = segment[1] ? <ModuleDetail slug={segment[1]}/> : <LearningPath/>;
    else if (segment[0] === 'library') content = <LibraryPage/>;
    else if (segment[0] === 'resources' && segment[1]) content = <ResourcePage key={segment[1]} id={segment[1]}/>;
    else if (segment[0] === 'reader' && segment[1]) content = <Reader key={segment[1]} resourceId={segment[1]}/>;
    else if (segment[0] === 'tools') content = segment[1] ? <ToolPage key={segment[1]} slug={segment[1]}/> : <ToolsPage/>;
    else if (segment[0] === 'glossary') content = <GlossaryPage/>;
    else if (segment[0] === 'notes') content = <NotesPage/>;
    else if (segment[0] === 'settings') content = <SettingsPage/>;
    else content = <Empty title="이 페이지를 찾을 수 없습니다"><Link className="button" href="/">공부 홈으로</Link></Empty>;
  }
  return <div className={`app-shell ${menuOpen ? 'menu-open' : ''} ${sidebarCollapsed && isReader ? 'sidebar-collapsed' : ''}`}>
    <a className="skip-link" href="#main-content">본문으로 건너뛰기</a>
    {menuOpen && <button className="sidebar-scrim" aria-label="메뉴 닫기" onClick={() => setMenuOpen(false)}/>}
    <aside className="sidebar"><Link className="brand" href="/"><span className="brand-mark"><BookOpen size={23} strokeWidth={1.7}/></span><span>달모다란<small>MoonModaran</small></span></Link><div className="sidebar-section-title">나의 공부 공간</div><nav aria-label="주 메뉴">{navigation.map(item => <Link key={item.path} href={item.path} aria-current={activeNav?.path === item.path ? 'page' : undefined} className={`nav-item ${activeNav?.path === item.path ? 'active' : ''}`}><item.icon size={19}/><span>{item.title}</span>{item.path === '/notes' && !!data?.notes.length && <span className="nav-count">{data.notes.length}</span>}</Link>)}</nav><div className="sidebar-bottom"><div className="sidebar-progress"><div className="row-between"><span>한 걸음씩, 나의 속도로</span><span>{finished}<em> / {data?.modules.length || 8}</em></span></div><div className="progress-track"><span style={{ width: `${finished / (data?.modules.length || 8) * 100}%` }}/></div><small>완료한 학습 단원</small></div><Link className={`nav-item ${pathname === '/settings' ? 'active' : ''}`} href="/settings"><Settings2 size={19}/>환경 설정</Link><div className="local-status"><span/>나만의 로컬 공부방<ShieldCheck size={14}/></div></div></aside>
    <div className="workspace"><header className="topbar"><div className="topbar-left"><button className="icon-button mobile-menu" aria-label="메뉴 열기" onClick={() => setMenuOpen(true)}><Menu size={22}/></button>{isReader && <button className="icon-button desktop-only" aria-label={sidebarCollapsed ? '메뉴 펼치기' : '메뉴 접기'} onClick={() => setSidebarCollapsed(v => !v)}><Menu size={19}/></button>}<span className="breadcrumb">나의 공부방 <ChevronRight size={13}/><strong>{isReader ? '읽기' : activeNav?.title || '환경 설정'}</strong></span></div><form action="/library" className="topbar-search"><Search size={17}/><input name="q" aria-label="전체 자료 검색" placeholder="어떤 개념이 궁금한가요?" defaultValue={params.get('q') || ''}/><kbd>검색</kbd></form><span className="profile-avatar" aria-label="개인 학습 공간">나</span></header>
      <main id="main-content" className={`main-content ${isReader ? 'reader-main' : ''}`}>
        {error ? <ErrorPanel error={error} retry={() => refresh().catch(e => setError(e.message))}/> : data ? <RoomContext.Provider value={{ data, refresh, run, notify }}>{content}</RoomContext.Provider> : <Loading text="나의 공부방을 준비하고 있습니다"/>}
      </main><footer className="site-footer"><span>달모다란 (MoonModaran)</span><span>Aswath Damodaran의 자료와 함께, 차근차근.</span><a href="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm" target="_blank" rel="noreferrer">원본 사이트 <ArrowUpRight size={12}/></a></footer>
    </div>{notice && <div className={`toast ${notice.error ? 'toast-error' : ''}`} role={notice.error ? 'alert' : 'status'}>{notice.error ? <CircleHelp size={18}/> : <CircleCheck size={18}/>}<span>{notice.text}</span><button aria-label="알림 닫기" onClick={() => setNotice(null)}><X size={16}/></button></div>}
  </div>;
}

function Dashboard() {
  const { data } = useRoom(); const nextModule = data.modules.find(m => data.progress.find(p => p.moduleId === m.id)?.status !== 'completed') || data.modules[0];
  const recent = [...data.positions].sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''))[0];
  const current = recent ? data.resources.find(r => r.id === recent.resourceId) : data.resources.find(r => r.id === nextModule?.resourceIds[0]);
  const finished = data.progress.filter(p => p.status === 'completed').length;
  const featured = ['R01', 'R02', 'R05'].map(id => data.resources.find(r => r.id === id)).filter((r): r is Resource => !!r);
  const term = data.glossary.find(t => /present value/i.test(t.termEn)) || data.glossary[0];
  return <div className="dashboard"><PageHeading eyebrow="YOUR PERSONAL STUDY ROOM" title="숫자 너머의 가치를 읽는 시간" description="서두르지 않아도 괜찮아요. 오늘도 하나의 개념을 나의 것으로." action={<span className="quiet-label"><span className="tiny-dot"/>나의 속도로, 깊이 있게</span>}/>
    <div className="dashboard-top"><section className="continue-card"><div className="continue-copy"><span className="pill-light"><BookOpen size={14}/>{recent ? '이어서 공부하기' : '오늘의 첫걸음'}</span><span className="continue-module">{nextModule?.id.replace('M', 'STEP ')} · {nextModule?.titleKo}</span><h2>{recent ? current?.titleKo : '이익과 현금은\n왜 다를까요?'}</h2><p>{recent ? '지난번에 읽던 문단과 메모가 그대로 기다리고 있어요.' : '기업을 이해하는 첫 번째 언어, 재무제표.\n기초부터 차근차근 함께 읽어보세요.'}</p><Link className="button primary" href={recent ? readerLink(recent) : `/learn/${nextModule?.slug}`}>{recent ? '이어서 읽기' : '첫 단원 시작하기'}<ArrowRight size={17}/></Link>{current && <span className="continue-source">{current.titleEn} <span>·</span> Aswath Damodaran</span>}</div><div className="study-illustration" aria-hidden="true"><div className="illustration-grid"/><div className="illustration-orbit"/><div className="paper paper-back"/><div className="paper paper-front"><span className="paper-eyebrow">UNDERSTANDING VALUE</span><span className="paper-title">A little more<br/>understanding.<br/><em>Every day.</em></span><div className="mini-chart"><span/><span/><span/><span/><span/><span/></div><div className="paper-rule"/><span className="paper-number">01 — THE FOUNDATIONS</span></div><div className="illustration-stamp"><BookMarked size={23}/></div><span className="illustration-caption">가치는 이해에서 시작됩니다.</span></div></section>
    <section className="overview-card"><div className="section-heading"><h2>나의 학습 여정</h2><Route size={19}/></div><div className="journey-count"><strong>{finished}</strong><span>/ {data.modules.length} 단원 완료</span></div><div className="segmented-progress">{data.modules.map(m => <span key={m.id} className={data.progress.find(p => p.moduleId === m.id)?.status === 'completed' ? 'done' : data.progress.find(p => p.moduleId === m.id)?.status === 'in_progress' ? 'current' : ''}/>)}</div><p>{finished === data.modules.length ? '모든 단원을 마쳤어요. 기록을 보며 복습해 볼까요?' : finished ? '쌓아온 이해 위에 다음 개념을 더해보세요.' : '작은 시작이 단단한 이해를 만듭니다.'}</p><div className="overview-stats"><Link href="/notes?tab=bookmarks"><Bookmark size={16}/><span>담아둔 자료</span><strong>{data.bookmarks.length}</strong></Link><Link href="/notes"><NotebookPen size={16}/><span>남긴 메모</span><strong>{data.notes.length}</strong></Link></div><Link className="text-link" href="/learn">전체 학습 경로 보기 <ArrowRight size={15}/></Link></section></div>
    <section className="home-section"><div className="section-heading"><div><h2>탄탄한 시작을 위한 읽을거리 <span className="section-count">03</span></h2><p>재무의 기초부터 가치평가의 큰 그림까지</p></div><Link className="text-link" href="/library">자료실 둘러보기 <ArrowRight size={15}/></Link></div><div className="resource-grid three">{featured.map(r => <ResourceCard key={r.id} resource={r}/>)}</div></section>
    <div className="dashboard-bottom"><section className="path-preview"><div className="section-heading"><h2>배움의 흐름</h2><Link className="text-link" href="/learn">8개 단원 <ArrowUpRight size={15}/></Link></div><div className="mini-path">{data.modules.slice(0, 4).map((m, i) => <Link href={`/learn/${m.slug}`} key={m.id}><span className={`path-dot ${data.progress.find(p => p.moduleId === m.id)?.status === 'completed' ? 'done' : ''}`}>{data.progress.find(p => p.moduleId === m.id)?.status === 'completed' ? <Check size={14}/> : i + 1}</span><strong>{m.titleKo}</strong><small>{i < 2 ? '기초 다지기' : '개념 넓히기'}</small></Link>)}</div><p className="path-footnote">재무제표에서 DCF까지, 연결하며 배우는 가치평가</p></section>{term && <section className="term-preview"><span className="eyebrow"><Sparkles size={13}/> 하나의 개념, 한 걸음 더</span><Link href={`/glossary?termId=${term.id}`}><h2>{term.termKo} <ArrowUpRight size={17}/></h2></Link><span className="term-english">{term.termEn}{term.acronym ? ` · ${term.acronym}` : ''}</span><p>{term.definitionKo}</p></section>}</div>
    {data.notes.length > 0 && <section className="home-section"><div className="section-heading"><h2>최근에 남긴 생각</h2><Link className="text-link" href="/notes">내 기록 보기 <ArrowRight size={15}/></Link></div><div className="recent-note-list">{data.notes.slice(0, 2).map(n => <Link href={readerLink(n)} key={n.id}><NotebookPen size={18}/><span><strong>{n.text}</strong><small>{n.titleKo || data.resources.find(r => r.id === n.resourceId)?.titleKo} · {dateLabel(n.updatedAt)}</small></span><ArrowUpRight size={17}/></Link>)}</div></section>}
  </div>;
}

function LearningPath() {
  const { data } = useRoom(); const completed = data.progress.filter(p => p.status === 'completed').length;
  return <><PageHeading eyebrow="LEARNING PATH" title="기초에서 가치평가까지" description="8개의 연결된 단원. 익숙한 내용은 건너뛰고, 궁금한 곳은 다시 읽어도 좋아요."/><div className="path-summary"><Route size={22}/><div><strong>하나의 기업을 스스로 이해하는 여정</strong><p>재무제표 → 현재가치 → 위험과 자본비용 → DCF와 상대가치</p></div><span><strong>{completed}</strong> / 8 완료</span></div><div className="module-list">{data.modules.map((module, index) => { const status = data.progress.find(p => p.moduleId === module.id)?.status || 'not_started'; return <Link className={`module-card ${status === 'completed' ? 'module-completed' : ''}`} key={module.id} href={`/learn/${module.slug}`}><span className="module-index">{status === 'completed' ? <Check size={25}/> : String(index + 1).padStart(2, '0')}</span><div className="module-copy"><div className="row-gap"><span className="small-caps">{index < 2 ? 'FOUNDATIONS' : index < 5 ? 'BUILDING BLOCKS' : 'VALUATION IN PRACTICE'}</span><span className={`tag ${status === 'in_progress' ? 'blue' : ''}`}>{label(status)}</span></div><h2>{module.titleKo}</h2><p>{module.question}</p><div className="module-objectives">{module.objectives.slice(0, 2).map(item => <span key={item}>{item}</span>)}</div></div><span className="module-resource-count">자료 {module.resourceIds.length}개 <ArrowUpRight size={19}/></span></Link>; })}</div></>;
}

function ModuleDetail({ slug }: { slug: string }) {
  const { data, run, refresh } = useRoom(); const module = data.modules.find(m => m.slug === slug); const [saving, setSaving] = useState(false);
  if (!module) return <Empty title="단원을 찾을 수 없습니다"><Link href="/learn" className="button">학습 경로로</Link></Empty>;
  const status = data.progress.find(p => p.moduleId === module.id)?.status || 'not_started';
  const resources = module.resourceIds.map(id => data.resources.find(r => r.id === id)).filter((r): r is Resource => !!r);
  const next = data.modules.find(m => m.order === module.order + 1);
  async function update(nextStatus: string) { setSaving(true); await run(async () => { await api(`/api/progress/${module!.id}`, 'PATCH', { status: nextStatus }); await refresh(); }, '학습 상태를 저장했습니다.'); setSaving(false); }
  return <><Link className="back-link" href="/learn">← 학습 경로</Link><PageHeading eyebrow={`STEP ${String(module.order).padStart(2, '0')} · ${label(status)}`} title={module.titleKo} description={module.question}/><div className="detail-grid"><div><section className="content-panel"><span className="eyebrow">LEARNING OBJECTIVES</span><h2>이 단원에서 배우는 것</h2><CheckList items={module.objectives}/></section><section className="home-section"><div className="section-heading"><h2>이 순서로 읽어보세요</h2><span className="muted">{resources.length}개 자료</span></div><div className="resource-rows">{resources.map((r, i) => <ResourceRow resource={r} number={i + 1} key={r.id}/>)}</div></section><section className="content-panel question-panel"><span className="eyebrow">CHECK YOUR UNDERSTANDING · 학습실 작성</span><h2>스스로에게 던져볼 질문</h2><ol>{module.questions.map((question, i) => <li key={i}><span>{String(i + 1).padStart(2, '0')}</span>{question}</li>)}</ol><p className="muted">답을 자신의 말로 설명할 수 있다면, 다음 개념을 만날 준비가 된 거예요.</p></section></div><aside className="detail-aside"><section className="content-panel"><h3>나의 학습 상태</h3><p className="muted">충분히 이해했다면 직접 완료를 표시해 주세요.</p><label className="field-label" htmlFor="module-status">현재 상태</label><select id="module-status" value={status} onChange={e => update(e.target.value)} disabled={saving}><option value="not_started">시작 전</option><option value="in_progress">학습 중</option><option value="completed">학습 완료</option></select>{status !== 'completed' && <button className="button primary full" disabled={saving} onClick={() => update('completed')}><CircleCheck size={17}/>이 단원 학습 완료</button>}</section><section className="content-panel"><h3>먼저 알아둘 용어</h3><div className="prerequisite-terms">{module.prerequisiteTermIds.map(id => data.glossary.find(t => t.id === id)).filter((t): t is Term => !!t).map(term => <Link key={term.id} href={`/glossary?termId=${term.id}`}><span>{term.termKo}<small>{term.acronym || term.termEn}</small></span><ArrowUpRight size={15}/></Link>)}</div></section>{next && <Link className="next-module" href={`/learn/${next.slug}`}><small>다음 단원</small><strong>{next.titleKo}<ArrowRight size={17}/></strong></Link>}</aside></div></>;
}

function LibraryPage() {
  const { data, run, refresh } = useRoom(); const router = useRouter(); const params = useSearchParams();
  const [uploading, setUploading] = useState(false); const inputRef = useRef<HTMLInputElement>(null);
  const q = params.get('q') || ''; const [query, setQuery] = useState(q); useEffect(() => setQuery(q), [q]);
  function filter(key: string, value: string) { const next = new URLSearchParams(params.toString()); value ? next.set(key, value) : next.delete(key); next.delete('page'); router.replace(`/library?${next}`); }
  let resources = data.resources.filter(r => !q || `${r.titleKo} ${r.titleEn} ${r.summaryKo} ${r.tags.join(' ')}`.toLowerCase().includes(q.toLowerCase()));
  for (const key of ['kind', 'format', 'level', 'priority'] as const) { const value = params.get(key); if (value) resources = resources.filter(r => r[key] === value); }
  if (params.get('module')) resources = resources.filter(r => r.moduleIds.includes(params.get('module')!));
  if (params.get('translation') === 'none') resources = resources.filter(r => r.translatedCount === 0);
  if (params.get('translation') === 'partial') resources = resources.filter(r => r.translatedCount > 0 && r.translatedCount < r.blockCount);
  if (params.get('translation') === 'complete') resources = resources.filter(r => r.blockCount > 0 && r.translatedCount === r.blockCount);
  const pageCount = Math.max(1, Math.ceil(resources.length / 12)); const page = Math.max(1, Math.min(pageCount, Number(params.get('page')) || 1));
  async function upload(file: File | undefined) { if (!file) return; setUploading(true); const form = new FormData(); form.append('file', file); await run(async () => { const result = await api<{ resourceId: string }>('/api/uploads', 'POST', form); await refresh(); router.push(`/resources/${result.resourceId}`); }, 'PDF 원본을 저장하고 추출 작업을 등록했습니다.'); setUploading(false); if (inputRef.current) inputRef.current.value = ''; }
  return <><PageHeading eyebrow="RESOURCE LIBRARY" title="필요한 자료를, 한곳에서" description="입문 글, 강의노트, 문제와 해답. 배움에 필요한 원문을 선별해 두었어요." action={<><input type="file" accept="application/pdf,.pdf" ref={inputRef} className="visually-hidden" aria-label="PDF 파일 선택" onChange={e => upload(e.target.files?.[0])}/><button className="button secondary" disabled={uploading} onClick={() => inputRef.current?.click()}><Upload size={16}/>{uploading ? 'PDF 저장 중…' : '내 PDF 가져오기'}</button></>}/><div className="library-search"><Search size={21}/><form onSubmit={e => { e.preventDefault(); filter('q', query); }}><input aria-label="자료 제목과 내용 검색" value={query} onChange={e => setQuery(e.target.value)} placeholder="한국어·영어 제목, 개념으로 찾아보세요"/><button className="button primary small" type="submit">검색</button></form></div><div className="filter-bar"><SlidersHorizontal size={17}/>{(['kind', 'format', 'level', 'priority'] as const).map((key, i) => <select aria-label={['자료 종류', '파일 형식', '난이도', '우선순위'][i]} value={params.get(key) || ''} onChange={e => filter(key, e.target.value)} key={key}><option value="">{['모든 종류', '모든 형식', '모든 난이도', '모든 우선순위'][i]}</option>{[...new Set(data.resources.map(r => r[key]))].map(v => <option key={v} value={v}>{label(v)}</option>)}</select>)}<select aria-label="학습 단원" value={params.get('module') || ''} onChange={e => filter('module', e.target.value)}><option value="">모든 단원</option>{data.modules.map(m => <option key={m.id} value={m.id}>{m.titleKo}</option>)}</select><select aria-label="번역 상태" value={params.get('translation') || ''} onChange={e => filter('translation', e.target.value)}><option value="">모든 번역 상태</option><option value="none">미번역</option><option value="partial">일부 번역</option><option value="complete">번역 완료</option></select>{params.size > 0 && <Link className="text-link reset-filter" href="/library">초기화</Link>}</div><div className="section-heading library-results"><span><strong>{resources.length}</strong>개의 자료</span><span className="muted">원문과 한국어 학습 안내</span></div>{resources.length ? <><div className="resource-grid three">{resources.slice((page - 1) * 12, page * 12).map(r => <ResourceCard key={r.id} resource={r}/>)}</div>{pageCount > 1 && <div className="pagination">{Array.from({ length: pageCount }, (_, i) => <button key={i} aria-label={`${i + 1}페이지`} aria-current={page === i + 1 ? 'page' : undefined} className={page === i + 1 ? 'current' : ''} onClick={() => { const next = new URLSearchParams(params.toString()); next.set('page', String(i + 1)); router.replace(`/library?${next}`); window.scrollTo(0, 0); }}>{i + 1}</button>)}</div>}</> : <Empty title="검색 결과가 없습니다" description="다른 개념이나 더 넓은 조건으로 찾아보세요."><Link className="button secondary" href="/library">전체 자료 보기</Link></Empty>}</>;
}

function ResourcePage({ id }: { id: string }) {
  const { data, refresh, run } = useRoom(); const [detail, setDetail] = useState<ResourceDetail | null>(null); const [error, setError] = useState(''); const [busy, setBusy] = useState(false);
  const resource = data.resources.find(r => r.id === id); const versionCount = resource?.versionCount;
  useEffect(() => { let alive = true; api<ResourceDetail>(`/api/resources/${id}`).then(d => { if (alive) { setDetail(d); setError(''); } }).catch(e => { if (alive) setError(e.message); }); return () => { alive = false; }; }, [id, versionCount]);
  if (error) return <ErrorPanel error={error}/>; if (!detail) return <Loading/>;
  const r = resource || detail.resource; const guide = data.toolGuides.find(t => t.resourceId === id); const bookmarks = data.bookmarks.find(b => b.resourceId === id && !b.sourceVersionId); const resourceJobs = data.jobs.filter(j => j.resourceId === id); const jobs = resourceJobs.slice(0, 3); const version = detail.versions.find(v => v.id === r.versionId) || detail.versions[0];
  const importActive = resourceJobs.some(j => j.type === 'import' && ['queued', 'running'].includes(j.status));
  async function importSource() {
    setBusy(true);
    await run(async () => { await api(`/api/resources/${id}/import`, 'POST'); await refresh(); }, r.versionId ? '새 원문 확인을 시작했습니다. 바뀐 원문은 새 버전으로 보관합니다.' : '원문 가져오기를 시작했습니다. 완료되면 앱 보관함에 표시됩니다.');
    setBusy(false);
  }
  return <>
    <Link className="back-link" href="/library">← 자료실</Link>
    <PageHeading eyebrow={`${id} · ${label(r.format.toLowerCase())} · ${label(r.kind)}`} title={r.titleKo} description={r.titleEn}/>
    <div className="detail-grid">
      <div>
        <section className="content-panel resource-intro">
          <div className="tag-row"><span className="tag blue">{label(r.priority)}</span><span className="tag">{label(r.level)}</span><SourceBadge resource={r}/></div>
          <h2>{r.question || '이 자료는 무엇을 알려주나요?'}</h2>
          <p className="lead-copy">{r.summaryKo}</p>
          {r.objectives?.length > 0 && <><h3>읽고 나면 이해할 수 있어요</h3><CheckList items={r.objectives}/></>}
          <div className="resource-primary-actions">
            {guide
              ? <Link className="button primary" href={`/tools/${guide.slug}`}><Table2 size={17}/>한국어 사용 가이드</Link>
              : r.versionId
                ? <Link className="button primary" href={`/reader/${id}`}><BookOpen size={17}/>{detail.position ? '이어서 읽기' : '공부방에서 읽기'}<ArrowRight size={17}/></Link>
                : <button className="button primary" aria-busy={busy || importActive} disabled={!r.url || busy || importActive} onClick={importSource}><CloudDownload size={17}/>{busy || importActive ? '가져오는 중…' : '원문 가져오기'}</button>}
            {r.url && <a className="button secondary" href={r.url} target="_blank" rel="noreferrer">보관하지 않고 원저자 사이트 열기 (새 탭) <ExternalLink size={15}/></a>}
          </div>
        </section>
        {r.kind === 'catalog' || r.kind === 'index' || r.kind === 'collection' || r.kind === '목록' ? <div className="info-note"><FolderOpen size={18}/><p>여러 자료로 연결되는 목록입니다. 확인된 PDF와 문제·해답은 아래 연결 자료에서 선택할 수 있어요.</p></div> : null}
        {detail.relations.length > 0 && <section className="home-section"><div className="section-heading"><h2>함께 읽는 자료</h2></div><div className="resource-rows">{detail.relations.filter(r => r.relation !== 'solution').map(r => <ResourceRow resource={r} key={r.id}/>)}{detail.relations.filter(r => r.relation === 'solution').map(r => <details className="solution-disclosure" key={r.id}><summary>문제를 푼 뒤 해답 보기</summary><ResourceRow resource={r}/></details>)}</div></section>}
        <section className="content-panel stored-source-panel">
          <h2><HardDriveDownload size={19}/>보관한 원문</h2>
          <p className="muted">앱 보관함에 저장된 원문입니다. 다운로드하면 원저자 사이트에 접속하지 않고 선택한 버전을 내 파일로 복사합니다.</p>
          {detail.versions.length
            ? <div className="version-list">{detail.versions.map(v => <div key={v.id}><FileText size={19}/><span><strong>{dateLabel(v.importedAt)} 보관</strong><small>{label(v.extractionStatus)}{v.pageCount ? ` · ${v.pageCount}페이지` : ''} · 버전 {v.id.slice(0, 8)}</small></span><a href={`/api/resources/${id}/original?versionId=${v.id}`} download className="button small secondary version-download" aria-label={`${dateLabel(v.importedAt)} 버전 ${v.id.slice(0, 8)} 보관본 다운로드`}><HardDriveDownload size={15}/>보관본 다운로드</a>{!guide && <Link href={`/reader/${id}?versionId=${v.id}`} className="button small secondary">읽기</Link>}</div>)}</div>
            : <p className="source-action-empty">아직 보관한 원문이 없습니다. 먼저 원문을 가져오세요.</p>}
          {r.url && (detail.versions.length > 0 || guide) && <>
            <div className="source-action-divider"><span>새 원문이 필요할 때</span></div>
            <section className="source-action-section remote compact" aria-labelledby={`resource-import-${id}`}>
              <div className="source-action-heading"><span className="source-action-icon"><CloudDownload size={18}/></span><span><small>웹에서 앱으로 가져오기</small><h3 id={`resource-import-${id}`}>{r.versionId ? '새 원문 버전 확인' : '원문을 앱에 처음 보관'}</h3></span><span className="source-action-status">인터넷 필요</span></div>
              <div className="source-action-route" aria-label="원저자 사이트에서 앱 보관함으로"><span>원저자 사이트</span><b aria-hidden="true">→</b><span>앱 보관함</span></div>
              <p>{r.versionId ? '원문이 바뀌었으면 새 버전으로 따로 보관합니다. 기존 버전과 학습 기록은 유지됩니다.' : '원저자 사이트에서 파일을 받아 형식을 확인한 뒤 앱 보관함에 저장합니다.'}</p>
              <button className="button small secondary" aria-busy={busy || importActive} disabled={busy || importActive} onClick={importSource}><CloudDownload size={15}/>{busy || importActive ? '확인하는 중…' : r.versionId ? '새 원문 확인' : '원문 가져오기'}</button>
            </section>
          </>}
        </section>
      </div>
      <aside className="detail-aside">
        <section className="content-panel">
          <h3>자료 정보</h3>
          <dl className="metadata"><dt>원저자</dt><dd>{r.author || (r.url ? 'Aswath Damodaran' : '확인되지 않음')}</dd><dt>발행일</dt><dd>{dateLabel(version?.publishedAt)}</dd><dt>가져온 날짜</dt><dd>{version ? dateLabel(version.importedAt) : '아직 가져오지 않음'}</dd><dt>번역 상태</dt><dd>{r.blockCount ? `${r.translatedCount} / ${r.blockCount}문단` : '원문 추출 후 확인'}</dd></dl>
          <label className="field-label" htmlFor="priority">나의 우선순위</label>
          <select id="priority" value={r.priority} onChange={e => { run(async () => { await api(`/api/resources/${id}/preferences`, 'PATCH', { priority: e.target.value }); await refresh(); }, '우선순위를 저장했습니다.'); }}>{[...new Set([...data.resources.map(r => r.priority), r.priority])].map(v => <option key={v} value={v}>{label(v)}</option>)}</select>
          <button className="button secondary full" onClick={() => run(async () => { await api(bookmarks ? `/api/bookmarks/${bookmarks.id}` : '/api/bookmarks', bookmarks ? 'DELETE' : 'POST', bookmarks ? undefined : { resourceId: id }); await refresh(); }, bookmarks ? '북마크를 해제했습니다.' : '자료를 북마크에 담았습니다.')}><Bookmark size={17} fill={bookmarks ? 'currentColor' : 'none'}/>{bookmarks ? '담아둔 자료' : '북마크에 담기'}</button>
        </section>
        {detail.modules.length > 0 && <section className="content-panel"><h3>이어지는 학습 단원</h3>{detail.modules.map(m => <Link className="related-module" href={`/learn/${m.slug}`} key={m.id}><span>{m.titleKo}</span><ChevronRight size={16}/></Link>)}</section>}
        {jobs.map(j => <JobCard job={j} key={j.id}/>)}
      </aside>
    </div>
  </>;
}

function ToolsPage() {
  const { data } = useRoom();
  return <><PageHeading eyebrow="EXCEL WORKBENCH" title="이해한 개념을, 직접 계산해 보기" description="입력값의 의미부터 결과를 읽는 법까지. 10개의 Excel 도구와 한국어 가이드."/><div className="info-note"><Table2 size={20}/><p>한국어 가이드를 읽고 원본 Excel에서 실습하세요. 원본 파일과 수식은 그대로 보관됩니다.</p></div><div className="tools-grid">{data.toolGuides.map((guide, index) => { const r = data.resources.find(r => r.id === guide.resourceId); return r && <Link className="tool-card" key={guide.slug} href={`/tools/${guide.slug}`}><div className="row-between"><span className="tool-icon"><Table2 size={24}/></span><span className="tool-number">{String(index + 1).padStart(2, '0')}</span></div><h2>{r.titleKo}</h2><p className="resource-english">{r.titleEn}</p><p>{guide.purpose}</p><div className="tool-footer"><span>{label(r.priority)} · 한국어 가이드</span><ArrowRight size={18}/></div></Link>; })}</div></>;
}

function ToolPage({ slug }: { slug: string }) {
  const { data, run, refresh } = useRoom(); const guide = data.toolGuides.find(g => g.slug === slug); const r = data.resources.find(r => r.id === guide?.resourceId); const [busy, setBusy] = useState(false);
  if (!guide || !r) return <Empty title="도구를 찾을 수 없습니다"><Link href="/tools">도구실로 돌아가기</Link></Empty>;
  const modules = data.modules.filter(m => m.resourceIds.includes(r.id)); const resourceJobs = data.jobs.filter(j => j.resourceId === r.id); const jobs = resourceJobs.slice(0, 2);
  const importActive = resourceJobs.some(j => j.type === 'import' && ['queued', 'running'].includes(j.status));
  async function importOriginal() {
    setBusy(true);
    await run(async () => { await api(`/api/resources/${r!.id}/import`, 'POST'); await refresh(); }, r!.versionId ? '새 원본 확인을 시작했습니다. 바뀐 파일은 새 버전으로 보관합니다.' : '원본 가져오기를 시작했습니다. 완료되면 앱 보관함에 표시됩니다.');
    setBusy(false);
  }
  const savedFileAction = <section className={`source-action-section local ${r.versionId ? '' : 'unavailable'}`} aria-labelledby={`saved-file-${r.id}`}>
    <div className="source-action-heading"><span className="source-action-icon"><HardDriveDownload size={18}/></span><span><small>저장된 파일 받기</small><h4 id={`saved-file-${r.id}`}>보관본 다운로드</h4></span><span className="source-action-status">{r.versionId ? '인터넷 불필요' : '아직 없음'}</span></div>
    <div className="source-action-route" aria-label="앱 보관함에서 내 파일로"><span>앱 보관함</span><b aria-hidden="true">→</b><span>내 파일</span></div>
    {r.versionId
      ? <><p>앱이 보관 중인 Excel 파일을 내 파일로 복사합니다. 원저자 사이트에는 접속하지 않습니다.</p><a href={`/api/resources/${r.id}/original?versionId=${r.versionId}`} download className="button primary full"><HardDriveDownload size={17}/>보관본 다운로드</a></>
      : <p className="source-action-empty">‘원본 가져오기’를 완료하면 여기서 다운로드할 수 있습니다.</p>}
  </section>;
  const webSourceAction = <section className="source-action-section remote" aria-labelledby={`web-source-${r.id}`}>
    <div className="source-action-heading"><span className="source-action-icon"><CloudDownload size={18}/></span><span><small>웹에서 앱으로 가져오기</small><h4 id={`web-source-${r.id}`}>{r.versionId ? '새 원본 확인' : '원본 가져오기'}</h4></span><span className="source-action-status">인터넷 필요</span></div>
    <div className="source-action-route" aria-label="원저자 사이트에서 앱 보관함으로"><span>원저자 사이트</span><b aria-hidden="true">→</b><span>앱 보관함</span></div>
    <p>{r.versionId ? '원본이 바뀌었으면 새 버전으로 따로 보관합니다. 기존 보관본은 그대로 유지됩니다.' : '원저자 사이트에서 Excel 파일을 받아 형식을 확인한 뒤 앱 보관함에 저장합니다.'}</p>
    <button className={`button ${r.versionId ? 'secondary' : 'primary'} full`} aria-busy={busy || importActive} disabled={busy || importActive} onClick={importOriginal}><CloudDownload size={17}/>{busy || importActive ? (r.versionId ? '확인하는 중…' : '가져오는 중…') : r.versionId ? '새 원본 확인' : '원본 가져오기'}</button>
    {r.url && <a href={r.url} className="source-action-direct-link" target="_blank" rel="noreferrer">앱에 보관하지 않고 원저자 파일 열기 (새 탭) <ExternalLink size={14}/></a>}
  </section>;
  return <>
    <Link className="back-link" href="/tools">← Excel 도구실</Link>
    <PageHeading eyebrow={`${r.id} · EXCEL LEARNING GUIDE · 학습실 작성`} title={r.titleKo} description={r.titleEn}/>
    <div className="detail-grid">
      <div>
        <section className="content-panel original-file-panel">
          <span className="tool-icon"><Table2 size={24}/></span>
          <h2>원본 Excel 파일</h2>
          <p className="muted">앱 보관함은 원본을 보존하는 곳입니다. 다운로드한 파일은 Excel에서 직접 사용할 수 있는 내 복사본입니다.</p>
          <div className="source-action-intro"><strong>원하는 작업을 선택하세요</strong><p>보관본을 받는 것과 웹에서 새 원본을 가져오는 것은 서로 다른 작업입니다.</p></div>
          <div className="source-action-stack">
            {r.versionId ? <>{savedFileAction}{webSourceAction}</> : <>{webSourceAction}{savedFileAction}</>}
          </div>
          <Link href={`/resources/${r.id}`} className="text-link">출처와 보관 버전 관리 <ChevronRight size={15}/></Link>
          {!guide.internalVerified && <p className="small-notice">파일 내부 입력 위치 미확인. 특정 시트명이나 셀 주소를 지정하지 않는 개념 가이드입니다.</p>}
        </section>
        <section className="content-panel"><h2>이 도구가 해결하는 문제</h2><p className="lead-copy">{guide.purpose}</p><h3>입력 전에 준비하세요</h3><CheckList items={guide.inputs}/></section>
        <section className="content-panel"><span className="eyebrow">HOW TO USE</span><h2>차근차근 따라 해 보기</h2><ol className="guide-steps">{guide.steps.map((step, i) => <li key={i}><span>{String(i + 1).padStart(2, '0')}</span><p>{step}</p></li>)}</ol><div className="interpretation"><h3>결과는 이렇게 읽으세요</h3><p>{guide.interpretation}</p></div></section>
        <section className="content-panel"><h2>흔히 놓치는 부분</h2><ul className="simple-list">{guide.commonMistakes.map((mistake, i) => <li key={i}>{mistake}</li>)}</ul></section>
      </div>
      <aside className="detail-aside">
        <section className="content-panel"><h3>먼저 읽으면 좋아요</h3>{modules.map(m => <Link href={`/learn/${m.slug}`} className="related-module" key={m.id}>{m.titleKo}<ChevronRight size={15}/></Link>)}</section>
        {jobs.map(j => <JobCard key={j.id} job={j}/>)}
      </aside>
    </div>
  </>;
}

function GlossaryPage() {
  const { data } = useRoom(); const params = useSearchParams(); const router = useRouter(); const q = params.get('q') || ''; const [query, setQuery] = useState(q);
  useEffect(() => { setQuery(q); }, [q]);
  const terms = data.glossary.filter(t => `${t.termKo} ${t.termEn} ${t.acronym || ''} ${t.aliases.join(' ')}`.toLowerCase().includes(q.toLowerCase()));
  const selected = data.glossary.find(t => t.id === params.get('termId')) || terms[0];
  return <><PageHeading eyebrow="FINANCIAL GLOSSARY" title="낯선 용어가 익숙해지는 곳" description="한국어 뜻과 영문 용어를 연결하고, 정의와 예시로 이해를 넓혀보세요."/><form className="glossary-search library-search" onSubmit={e => { e.preventDefault(); const next = new URLSearchParams(); if (query) next.set('q', query); router.replace(`/glossary?${next}`); }}><Search size={21}/><input aria-label="금융 용어 검색" value={query} onChange={e => setQuery(e.target.value)} placeholder="현재가치, Present Value, PV…"/><button className="button primary small">검색</button></form><div className="glossary-grid"><div className="glossary-list"><div className="glossary-list-heading">{terms.length}개의 금융 용어</div>{terms.map(t => <Link key={t.id} href={`/glossary?${new URLSearchParams({ ...(q ? { q } : {}), termId: t.id })}`} className={selected?.id === t.id ? 'selected' : ''}><strong>{t.termKo}</strong><span>{t.termEn}{t.acronym ? ` · ${t.acronym}` : ''}</span></Link>)}{terms.length === 0 && <Empty title="일치하는 용어가 없습니다"/>}</div>{selected ? <TermArticle term={selected}/> : <Empty title="검색어를 바꿔보세요"/>}</div></>;
}

export function TermArticle({ term, compact = false }: { term: Term; compact?: boolean }) {
  const { data } = useRoom();
  return <article className={`term-article ${compact ? 'term-compact' : ''}`}><span className="eyebrow">FINANCIAL CONCEPT</span><h2>{term.termKo}</h2><div className="term-subtitle" lang="en">{term.termEn}{term.acronym && <span>{term.acronym}</span>}</div><p className="term-definition">{term.definitionKo}</p>{term.formula && <div className="term-formula"><small>핵심 관계</small><code>{term.formula}</code></div>}{term.exampleKo && <section><h3>예를 들어 볼까요? <span className="tag">학습실 작성</span></h3><p>{term.exampleKo}</p></section>}{term.notes && <section className="term-note"><h3>함께 기억하세요</h3><p>{term.notes}</p></section>}{term.aliases.length > 0 && <p className="muted">다른 이름 · {term.aliases.join(', ')}</p>}{!compact && <><h3>이 개념을 만나는 단원</h3><div className="tag-row">{term.moduleIds.map(id => data.modules.find(m => m.id === id)).filter((m): m is Module => !!m).map(m => <Link className="tag link-tag" href={`/learn/${m.slug}`} key={m.id}>{m.titleKo}<ArrowUpRight size={13}/></Link>)}</div><div className="term-sources"><span>정의 참고 · 한국어 학습용 정리</span>{term.sources.map((source, i) => <a href={source} target="_blank" rel="noreferrer" key={source}>참고 원문 {i + 1}<ExternalLink size={12}/></a>)}</div></>}</article>;
}

function NotesPage() {
  const { data, run, refresh } = useRoom(); const params = useSearchParams(); const tab = params.get('tab') || 'notes'; const [editing, setEditing] = useState<string | null>(null); const [draft, setDraft] = useState(''); const [deleting, setDeleting] = useState<string | null>(null);
  const title = (n: { resourceId: string; titleKo?: string }) => n.titleKo || data.resources.find(r => r.id === n.resourceId)?.titleKo || n.resourceId;
  async function save(note: Note) { const result = await run(async () => { await api(`/api/notes/${note.id}`, 'PATCH', { text: draft }); await refresh(); return true; }, '메모를 수정했습니다.'); if (result) setEditing(null); }
  return <><PageHeading eyebrow="MY LEARNING JOURNAL" title="읽고, 생각하고, 남긴 것들" description="흩어져 있던 배움을 나만의 언어로 모아두세요."/><div className="record-stats"><div><NotebookPen size={21}/><strong>{data.notes.length}</strong><span>남긴 메모</span></div><div><Bookmark size={21}/><strong>{data.bookmarks.length}</strong><span>북마크</span></div><div><CircleCheck size={21}/><strong>{data.progress.filter(p => p.status === 'completed').length}</strong><span>완료한 단원</span></div></div><div className="tabs">{[['notes', '나의 메모', data.notes.length], ['bookmarks', '북마크', data.bookmarks.length], ['history', '읽던 자료', data.positions.length], ['progress', '학습 상태', data.modules.length]].map(([key, text, count]) => <Link key={key} href={`/notes?tab=${key}`} className={tab === key ? 'active' : ''}>{text}<span>{count}</span></Link>)}</div>{tab === 'notes' && (data.notes.length ? <div className="notes-list">{data.notes.map(note => <article className="note-card" key={note.id}><div className="row-between"><Link href={readerLink(note)} className="text-link"><FileText size={15}/>{title(note)}<ArrowUpRight size={14}/></Link><small>{dateLabel(note.updatedAt)}</small></div>{note.quote && <blockquote lang="en">{note.quote}</blockquote>}{editing === note.id ? <><label className="visually-hidden" htmlFor={`note-${note.id}`}>메모 내용</label><textarea id={`note-${note.id}`} value={draft} onChange={e => setDraft(e.target.value)}/><div className="row-gap"><button className="button primary small" disabled={!draft.trim()} onClick={() => save(note)}>수정 저장</button><button className="button small secondary" onClick={() => setEditing(null)}>취소</button></div></> : <p className="note-text">{note.text}</p>}<div className="note-bottom"><span>{note.pageIndex != null ? `${note.pageIndex + 1}페이지` : note.blockId ? '문단 메모' : '자료 메모'}{note.sourceVersionId && ` · 버전 ${note.sourceVersionId.slice(0, 8)}`}</span>{deleting === note.id ? <span className="row-gap"><span>메모를 삭제할까요?</span><button className="text-link danger" onClick={() => run(async () => { await api(`/api/notes/${note.id}`, 'DELETE'); await refresh(); setDeleting(null); }, '메모를 삭제했습니다.')}>삭제</button><button className="text-link" onClick={() => setDeleting(null)}>취소</button></span> : <span className="row-gap"><button className="text-link" onClick={() => { setEditing(note.id); setDraft(note.text); }}>수정</button><button className="icon-button" aria-label={`${title(note)} 메모 삭제`} onClick={() => setDeleting(note.id)}><Trash2 size={15}/></button></span>}</div></article>)}</div> : <Empty title="첫 번째 생각을 남겨보세요" description="자료를 읽으며 문단을 선택하면, 그 위치에 메모를 남길 수 있어요."><Link className="button primary" href="/library">읽을거리 찾아보기 <ArrowRight size={16}/></Link></Empty>)}{tab === 'bookmarks' && (data.bookmarks.length ? <div className="record-rows">{data.bookmarks.map(b => <div key={b.id}><Bookmark size={19}/><Link href={readerLink(b)}><strong>{title(b)}</strong><small>{b.pageIndex != null ? `${b.pageIndex + 1}페이지` : b.blockId ? '저장한 문단' : '자료 전체'}{b.sourceVersionId ? ` · 버전 ${b.sourceVersionId.slice(0, 8)}` : ''}</small></Link><button className="icon-button" aria-label={`${title(b)} 북마크 해제`} onClick={() => run(async () => { await api(`/api/bookmarks/${b.id}`, 'DELETE'); await refresh(); }, '북마크를 해제했습니다.')}><X size={17}/></button></div>)}</div> : <Empty title="다시 읽고 싶은 자료를 담아두세요" description="자료의 책갈피 버튼으로 북마크에 추가할 수 있어요."/>)}{tab === 'history' && (data.positions.length ? <div className="record-rows">{data.positions.map(p => <div key={`${p.resourceId}-${p.sourceVersionId}`}><Clock3 size={19}/><Link href={readerLink(p)}><strong>{title(p)}</strong><small>{p.pageIndex != null ? `${p.pageIndex + 1}페이지 · ` : ''}{dateLabel(p.updatedAt)} · 버전 {p.sourceVersionId.slice(0, 8)}</small></Link><ArrowUpRight size={19}/></div>)}</div> : <Empty title="아직 읽기 기록이 없습니다" description="공부방에서 자료를 열면 마지막 위치를 저장합니다."/>)}{tab === 'progress' && <div className="record-rows">{data.modules.map(m => <div key={m.id}><span className="row-number">{m.id.replace('M', '')}</span><Link href={`/learn/${m.slug}`}><strong>{m.titleKo}</strong><small>{m.question}</small></Link><span className="tag">{label(data.progress.find(p => p.moduleId === m.id)?.status || 'not_started')}</span><ChevronRight size={17}/></div>)}</div>}</>;
}

function SettingsPage() {
  const { data, run, refresh } = useRoom(); const settings = data.settings; const status = data.translationStatus;
  const [fontSize, setFontSize] = useState(settings.fontSize); const [mode, setMode] = useState(settings.languageMode);
  return <>
    <PageHeading eyebrow="ROOM PREFERENCES" title="나에게 편안한 공부방" description="읽기 환경과 번역 설정, 진행 중인 작업을 확인하세요."/>
    <div className="settings-grid">
      <section className="content-panel">
        <h2><Settings2 size={21}/>읽기 환경</h2>
        <label className="field-label" htmlFor="font-size">본문 글자 크기 <strong>{fontSize}px</strong></label>
        <input type="range" min="15" max="24" id="font-size" value={fontSize} onChange={e => setFontSize(Number(e.target.value))}/>
        <div className="font-preview" style={{ fontSize }}>가치는 숫자를 이해하는 것에서 시작된다.<br/><span lang="en">Value begins with understanding.</span></div>
        <label className="field-label" htmlFor="default-mode">기본 읽기 모드</label>
        <select id="default-mode" value={mode} onChange={e => setMode(e.target.value as typeof mode)}><option value="parallel">원문·한국어 나란히</option><option value="ko">한국어</option><option value="en">원문</option></select>
        <button className="button primary" onClick={() => run(async () => { await api('/api/settings', 'PATCH', { fontSize, languageMode: mode }); await refresh(); }, '읽기 설정을 저장했습니다.')}><Check size={16}/>읽기 설정 저장</button>
      </section>
      <section className="content-panel" aria-label="한국어 번역 설정">
        <div className="section-heading"><h2><Languages size={21}/>한국어 번역</h2><span className={`tag ${status.configured ? 'blue' : ''}`}>{status.configured ? '사용 가능' : status.local ? '모델 설치 필요' : '설정 필요'}</span></div>
        <p>{status.local ? '무료 번역 · 이 PC에서 처리합니다. API 키 없이 선택한 문단과 페이지를 한국어로 바꿀 수 있어요.' : '선택한 문단과 페이지를 OpenAI API로 번역합니다. API 사용 요금이 발생할 수 있어요.'}</p>
        <p className="muted" role="status">{status.statusMessage}</p>
        <dl className="metadata">
          <dt>제공자</dt><dd>{status.providerLabel}</dd>
          <dt>처리 위치</dt><dd>{status.local ? '이 PC · 설치 후 오프라인 사용' : 'OpenAI 서버'}</dd>
          <dt>API 키</dt><dd>{status.apiKeyRequired ? status.keyConfigured ? '서버에 설정됨' : '설정되지 않음' : '필요 없음'}</dd>
          <dt>번역 모델</dt><dd>{status.modelConfigured ? status.model : status.local ? '영어 → 한국어 모델 설치 필요' : '설정되지 않음'}</dd>
          <dt>실제 번역 검증</dt><dd>{status.liveVerified ? `검증 기록 있음${status.verifiedAt ? ` · ${dateLabel(status.verifiedAt)}` : ''}` : '아직 확인되지 않음'}</dd>
          <dt>작업당 분량</dt><dd>{status.maxCharsPerJob.toLocaleString()}자</dd>
          <dt>하루 분량</dt><dd>{status.maxCharsPerDay.toLocaleString()}자 · 서울 기준</dd>
        </dl>
        <p className="muted">다모다란 용어 규칙 {(status.glossaryRuleCount || 0).toLocaleString()}개 · 검수 번역 {(status.reviewedPairCount || 0).toLocaleString()}개</p>
        <p className="small-notice">검수 번역은 같은 원문·문맥·용어집 기준에서 재사용합니다.</p>
        {status.glossarySources && status.glossarySources.length > 0 && <p className="row-gap" style={{ flexWrap: 'wrap' }}>{status.glossarySources.map(source => <a className="text-link" key={source} href={source} target="_blank" rel="noreferrer">{source.includes('definitions.html') ? '금융지표 정의' : source.includes('glossary.htm') ? '금융용어사전' : '용어 규칙 출처'}<ExternalLink size={12}/></a>)}</p>}
        <details className="setup-instructions">
          <summary>{status.provider === 'hymt' ? 'Hy-MT2 사용 안내' : status.provider === 'finetuned' ? '학습 모델 사용 안내' : status.local ? '무료 번역 설치 방법' : '번역 설정 방법'}</summary>
          {status.provider === 'hymt' ? <p>비교와 검토를 마친 Hy-MT2 구성을 등록한 뒤 이 PC에서 무료로 사용합니다. 번역 결과는 사용자 검수가 필요합니다.</p> : status.provider === 'finetuned' ? <p>학습·평가를 마친 모델을 등록해 주세요. 등록한 모델은 이 PC에서 무료로 사용하며, 번역 결과는 사용자 검수가 필요합니다.</p> : status.local ? <><p>처음 한 번, 인터넷에 연결한 상태에서 앱 폴더의 터미널에서 실행하세요.</p><pre>npm run setup:translation</pre><p>Argos Translate와 영어 → 한국어 모델을 이 PC에 설치합니다. 설치가 끝나면 앱을 다시 실행하세요. 이후 번역할 원문은 외부 서버로 보내지 않습니다.</p></> : <><p>앱 폴더의 <code>.env.local</code>에 아래 서버 설정을 추가한 뒤 앱을 다시 실행하세요.</p><pre>TRANSLATION_PROVIDER=openai{'\n'}OPENAI_API_KEY=발급받은_API_키{'\n'}TRANSLATION_MODEL=사용할_모델_ID</pre><p>무료 로컬 번역을 쓰려면 제공자를 <code>argos</code>로 변경하고 <code>npm run setup:translation</code>을 실행하세요.</p></>}
          <p>저장한 번역은 재사용합니다. 원문·메모·기존 번역은 새 번역 설정 없이도 읽을 수 있습니다.</p>
        </details>
        <p className="small-notice">기계 번역은 사용자 미검수 상태로 저장됩니다. 금융용어·숫자·수식은 원문과 함께 확인하세요.</p>
      </section>
      <section className="content-panel" aria-label="번역 사용량">
        <h2>{status.local ? '누적 로컬 번역량' : '누적 API 번역 사용량'}</h2>
        <div className="usage-count"><strong>{(status.local ? data.usage.localSourceChars || 0 : data.usage.remoteSourceChars || 0).toLocaleString()}</strong><span>원문 글자</span></div>
        {status.local ? <><dl className="metadata"><dt>로컬 번역 작업</dt><dd>{data.usage.localJobs || 0}건</dd><dt>API 사용료</dt><dd>없음 · 로컬 번역</dd></dl><p className="small-notice">실제로 처리한 원문 분량입니다. 저장한 번역을 다시 읽으면 처리량이 늘지 않습니다.</p></> : <><dl className="metadata"><dt>입력 토큰</dt><dd>{(data.usage.inputTokens || 0).toLocaleString()}</dd><dt>출력 토큰</dt><dd>{(data.usage.outputTokens || 0).toLocaleString()}</dd><dt>결과 불명확 호출</dt><dd>{data.usage.unknownCount || 0}건</dd></dl><p className="small-notice">실제 기록된 API 호출량입니다. 모델별 금액은 이 화면에서 추정하지 않습니다.</p></>}
      </section>
      <section className="content-panel"><h2><ShieldCheck size={21}/>나의 자료 보관</h2><p>자료, 번역, 메모와 진도는 내 PC에 저장됩니다. DB와 원본 파일을 함께 백업해 주세요.</p><label className="field-label">백업 만들기</label><pre>npm run backup</pre><p className="small-notice">완성된 백업은 별도 보관할 수 있습니다. 복원은 README의 절차에 따라 새 폴더에서 검증한 뒤 전환하세요.</p></section>
    </div>
    <section className="home-section"><div className="section-heading"><div><h2>작업 기록</h2><p>자료 가져오기와 번역의 실제 처리 상태</p></div><button className="text-link" onClick={() => run(refresh, '작업 상태를 새로 확인했습니다.')}>새로고침</button></div>{data.jobs.length ? <div className="jobs-grid">{data.jobs.map(job => <JobCard job={job} key={job.id}/>)}</div> : <Empty title="아직 실행한 작업이 없습니다" description="원문을 가져오거나 선택한 문단을 번역하면 여기에 표시됩니다."/>}</section>
  </>;
}
