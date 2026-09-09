'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight, Bookmark, BookOpen, Check, ChevronLeft, ChevronRight, Download, ExternalLink, Languages, List, NotebookPen, PanelRightClose, PanelRightOpen, Pencil, Search, X } from 'lucide-react';
import type { Block, BlocksResult, LanguageMode, Position, ResourceDetail, Term, Translation } from '@/lib/client-types';
import { api, dateLabel, label, useRoom } from './ui-context';
import { Empty, ErrorPanel, JobCard, Loading } from './common';
import PdfPage from './pdf-page';

type ReaderLocation = { versionId: string; page: number; anchor?: string; cursor?: string; mode: LanguageMode; offset: number };

export default function Reader({ resourceId }: { resourceId: string }) {
  const { data, refresh, run, notify } = useRoom(); const router = useRouter(); const params = useSearchParams();
  const [detail, setDetail] = useState<ResourceDetail | null>(null); const [payload, setPayload] = useState<BlocksResult | null>(null);
  const [location, setLocation] = useState<ReaderLocation | null>(null); const [error, setError] = useState(''); const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<LanguageMode>(data.settings.languageMode); const [panel, setPanel] = useState<'notes' | 'terms' | 'outline' | null>('notes');
  const [selectedIds, setSelectedIds] = useState<string[]>([]); const [activeBlock, setActiveBlock] = useState<string | null>(null);
  const [noteText, setNoteText] = useState(''); const [saving, setSaving] = useState(false); const [translationBusy, setTranslationBusy] = useState(false);
  const [selectedTerm, setSelectedTerm] = useState<Term | null>(null); const [termQuery, setTermQuery] = useState(''); const [saveState, setSaveState] = useState('');
  const [pageEnd, setPageEnd] = useState(1); const [pageInput, setPageInput] = useState('1');
  const positionReady = useRef(false); const locationRef = useRef<ReaderLocation | null>(null); const scrollTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dataRef = useRef(data); dataRef.current = data;
  const originalUrl = payload ? `/api/resources/${resourceId}/original?versionId=${payload.version.id}` : '';
  const isPdf = (payload?.version.format || detail?.resource.format || '').toLowerCase() === 'pdf';
  const rawSearch = params.toString();

  useEffect(() => {
    if (window.matchMedia('(max-width: 760px)').matches) setPanel(null);
  }, []);

  const fetchBlocks = useCallback(async (loc: ReaderLocation, pdf: boolean) => {
    const query = new URLSearchParams({ versionId: loc.versionId });
    if (loc.cursor && !pdf) query.set('cursor', loc.cursor);
    else if (loc.anchor && !pdf) query.set('anchorBlockId', loc.anchor);
    else query.set('page', String(loc.page));
    const result = await api<BlocksResult>(`/api/resources/${resourceId}/blocks?${query}`);
    if (loc.anchor && !loc.cursor && !result.blocks.some(b => b.id === loc.anchor)) throw new Error('지정한 문단이 이 버전 또는 페이지에 없습니다. 자료 상세에서 올바른 버전을 선택해 주세요.');
    return result;
  }, [resourceId]);

  useEffect(() => {
    let alive = true; positionReady.current = false; setLoading(true); setError('');
    async function load() {
      const query = new URLSearchParams(rawSearch);
      if (!query.get('versionId') && (query.has('page') || query.has('blockId'))) throw new Error('읽기 위치에 원문 버전이 없습니다. 자료 상세에서 버전을 선택해 다시 열어 주세요.');
      if (query.has('page') && (!/^\d+$/.test(query.get('page')!) || Number(query.get('page')) < 1)) throw new Error('페이지 번호는 1 이상의 정수여야 합니다.');
      if (query.has('mode') && !['en', 'ko', 'parallel'].includes(query.get('mode')!)) throw new Error('지원하지 않는 읽기 모드입니다.');
      const info = await api<ResourceDetail>(`/api/resources/${resourceId}`); if (!alive) return; setDetail(info);
      let requestedVersion = query.get('versionId');
      const positions = dataRef.current.positions.filter(p => p.resourceId === resourceId).sort((a, b) => (b.updatedAt || '').localeCompare(a.updatedAt || ''));
      let saved = requestedVersion ? positions.find(p => p.sourceVersionId === requestedVersion) : info.position || positions[0];
      requestedVersion = requestedVersion || saved?.sourceVersionId || info.resource.versionId || info.versions.find(v => !['pending', 'failed'].includes(v.extractionStatus))?.id || null;
      if (!requestedVersion) { if (alive) { setPayload(null); setLoading(false); } return; }
      const version = info.versions.find(v => v.id === requestedVersion);
      if (!version) throw new Error('요청한 원문 버전을 찾을 수 없습니다. 다른 버전으로 자동 전환하지 않았습니다.');
      if (saved?.sourceVersionId !== requestedVersion) saved = undefined;
      const pdf = (version.format || info.resource.format).toLowerCase() === 'pdf';
      const explicitPosition = query.has('page') || query.has('blockId');
      const loc: ReaderLocation = { versionId: requestedVersion, page: query.has('page') ? Number(query.get('page')) : !explicitPosition ? (saved?.pageIndex ?? 0) + 1 : 1, anchor: query.get('blockId') || (!explicitPosition ? saved?.blockId || undefined : undefined), mode: (query.get('mode') as LanguageMode) || saved?.languageMode || (window.matchMedia('(max-width: 760px)').matches ? 'ko' : dataRef.current.settings.languageMode), offset: explicitPosition ? 0 : saved?.offset || 0 };
      let result: BlocksResult;
      if (pdf && loc.anchor && !query.has('page') && explicitPosition) { result = await api<BlocksResult>(`/api/resources/${resourceId}/blocks?${new URLSearchParams({ versionId: loc.versionId, anchorBlockId: loc.anchor })}`); const block = result.blocks.find(b => b.id === loc.anchor); if (!block || block.pageIndex == null) throw new Error('PDF 문단 위치를 찾을 수 없습니다.'); loc.page = block.pageIndex + 1; }
      else result = await fetchBlocks(loc, pdf);
      if (!alive) return;
      setPayload(result); setLocation(loc); locationRef.current = loc; setMode(loc.mode); setPageInput(String(loc.page)); setPageEnd(loc.page); setActiveBlock(loc.anchor || result.blocks[0]?.id || null); setSelectedIds([]); setLoading(false);
      if (!query.get('versionId')) { query.set('versionId', loc.versionId); if (pdf) query.set('page', String(loc.page)); if (loc.anchor) query.set('blockId', loc.anchor); query.set('mode', loc.mode); window.history.replaceState(null, '', `/reader/${resourceId}?${query}`); }
      requestAnimationFrame(() => requestAnimationFrame(() => {
        if (!alive) return; const element = loc.anchor ? window.document.getElementById(`block-${loc.anchor}`) : null;
        if (element) window.scrollTo({ top: element.getBoundingClientRect().top + window.scrollY - 145 + loc.offset, behavior: 'instant' });
        positionReady.current = true;
      }));
    }
    load().catch(e => { if (alive) { setError(e.message); setLoading(false); } });
    return () => { alive = false; positionReady.current = false; };
  }, [rawSearch, resourceId, fetchBlocks]);

  const jobSignature = data.jobs.filter(j => j.resourceId === resourceId).map(j => `${j.id}:${j.status}:${j.completed}:${j.needsReview}`).join('|');
  useEffect(() => {
    if (!locationRef.current || !jobSignature) return; const loc = locationRef.current;
    fetchBlocks(loc, isPdf).then(setPayload).catch(() => {});
  }, [jobSignature, fetchBlocks, isPdf]);

  const savePosition = useCallback(async (blockId: string | null, offset = 0) => {
    const loc = locationRef.current; if (!loc || !positionReady.current) return;
    setSaveState('읽기 위치 저장 중');
    try { await api('/api/reading-position', 'PUT', { resourceId, sourceVersionId: loc.versionId, blockId: blockId || undefined, pageIndex: isPdf ? loc.page - 1 : undefined, offset: Math.max(0, Math.round(offset)), languageMode: loc.mode }); setSaveState('읽기 위치 저장됨'); await refresh(); }
    catch { setSaveState('위치 저장 실패 · 이동 전 다시 시도하세요'); }
  }, [resourceId, isPdf, refresh]);
  useEffect(() => {
    function onScroll() { if (!positionReady.current) return; if (scrollTimer.current) clearTimeout(scrollTimer.current); scrollTimer.current = setTimeout(() => {
      const blocks = Array.from(window.document.querySelectorAll<HTMLElement>('[data-source-block]'));
      const active = blocks.find(el => el.getBoundingClientRect().bottom > 150); const id = active?.dataset.sourceBlock || null;
      if (active) setActiveBlock(id); void savePosition(id, active ? Math.max(0, 150 - active.getBoundingClientRect().top) : 0);
    }, 650); }
    window.addEventListener('scroll', onScroll, { passive: true }); return () => { window.removeEventListener('scroll', onScroll); if (scrollTimer.current) clearTimeout(scrollTimer.current); };
  }, [savePosition]);
  useEffect(() => { if (!loading && payload) { const timer = setTimeout(() => savePosition(locationRef.current?.anchor || payload.blocks[0]?.id || null, locationRef.current?.offset || 0), 450); return () => clearTimeout(timer); } }, [loading, payload?.version.id, savePosition]);

  async function changePage(page: number) {
    if (!location || !payload || page < 1 || page > (payload.version.pageCount || payload.pageCount || 1)) return;
    const query = new URLSearchParams({ versionId: location.versionId, page: String(page), mode }); if (params.get('moduleSlug')) query.set('moduleSlug', params.get('moduleSlug')!);
    router.replace(`/reader/${resourceId}?${query}`); window.scrollTo(0, 0);
  }
  async function changeCursor(cursor: string | number) {
    if (!location) return; const loc = { ...location, cursor: String(cursor), anchor: undefined, offset: 0 }; setLoading(true);
    const result = await run(() => fetchBlocks(loc, false)); if (result) { setPayload(result); setLocation(loc); locationRef.current = loc; setActiveBlock(result.blocks[0]?.id || null); setSelectedIds([]); window.scrollTo(0, 0); setTimeout(() => savePosition(result.blocks[0]?.id || null), 100); } setLoading(false);
  }
  function changeMode(next: LanguageMode) { setMode(next); if (locationRef.current) { locationRef.current.mode = next; void savePosition(activeBlock); const query = new URLSearchParams({ versionId: locationRef.current.versionId, mode: next }); if (isPdf) query.set('page', String(locationRef.current.page)); if (activeBlock) query.set('blockId', activeBlock); if (params.get('moduleSlug')) query.set('moduleSlug', params.get('moduleSlug')!); router.replace(`/reader/${resourceId}?${query}`, { scroll: false }); } }
  async function translate(pages = false) {
    if (!payload || !location) return;
    if (!data.translationStatus.configured) { notify(data.translationStatus.statusMessage, true); return; }
    setTranslationBusy(true); await run(async () => { const result = await api<{ jobIds: string[]; cached: number }>('/api/translations', 'POST', { sourceVersionId: payload.version.id, ...(pages ? { pageRange: [location.page, pageEnd] } : { blockIds: selectedIds }) }); await refresh(); setPayload(await fetchBlocks(location, isPdf)); notify(result.jobIds.length ? `번역 작업 ${result.jobIds.length}개에 연결했습니다. 처리 상태를 확인해 주세요.` : '이미 저장한 유효한 번역을 불러왔습니다.'); }); setTranslationBusy(false);
  }
  async function saveNote() {
    if (!payload || !location || !noteText.trim()) return; setSaving(true);
    const block = payload.blocks.find(b => b.id === activeBlock);
    const result = await run(async () => { await api('/api/notes', 'POST', { resourceId, sourceVersionId: payload.version.id, blockId: block?.id, pageIndex: isPdf ? location.page - 1 : undefined, quote: block?.text.slice(0, 1800), text: noteText }); await refresh(); return true; }, '이 위치에 메모를 저장했습니다.'); if (result) setNoteText(''); setSaving(false);
  }
  async function bookmark() {
    if (!payload || !location) return;
    const block = payload.blocks.find(b => b.id === activeBlock);
    await run(async () => { await api('/api/bookmarks', 'POST', { resourceId, sourceVersionId: payload.version.id, blockId: block?.id, pageIndex: isPdf ? location.page - 1 : undefined }); await refresh(); }, '현재 읽기 위치를 북마크에 담았습니다.');
  }
  function showTerm(term: Term) { setSelectedTerm(term); setPanel('terms'); }
  function selectBlock(block: Block) { setActiveBlock(block.id); void savePosition(block.id); }
  function applyReviewedTranslation(blockId: string, translation: Translation) {
    setPayload(current => current ? { ...current, blocks: current.blocks.map(block => block.id === blockId ? { ...block, translation } : block) } : current);
    refresh().catch(() => {});
  }
  async function reloadTranslation(blockId: string) {
    if (!locationRef.current) throw new Error('현재 읽기 위치를 확인할 수 없습니다.');
    const result = await fetchBlocks(locationRef.current, isPdf);
    const translation = result.blocks.find(block => block.id === blockId)?.translation;
    if (!translation) throw new Error('이 문단의 최신 번역을 찾을 수 없습니다.');
    setPayload(result); return translation;
  }

  if (error) return <><Link href={`/resources/${resourceId}`} className="back-link">← 원문 버전 선택하기</Link><ErrorPanel error={error}/></>;
  if (loading && !payload) return <Loading text="원문과 저장한 읽기 위치를 확인하고 있습니다"/>;
  if (!detail) return <Loading/>;
  if (!payload || !location) return <Empty title="먼저 원문을 가져와 주세요" description="원문을 내 공부방에 보관하면 번역, 메모와 이어 읽기를 사용할 수 있습니다."><Link href={`/resources/${resourceId}`} className="button primary">자료 상세에서 가져오기 <ArrowRight size={16}/></Link></Empty>;
  const resource = detail.resource;
  const notes = data.notes.filter(n => n.resourceId === resourceId && n.sourceVersionId === payload.version.id && (!activeBlock || n.blockId === activeBlock));
  const matchedTerms = data.glossary.filter(t => `${t.termKo} ${t.termEn} ${t.acronym || ''}`.toLowerCase().includes(termQuery.toLowerCase()));
  const selected = payload.blocks.find(b => b.id === activeBlock);
  const currentJobs = data.jobs.filter(j => j.resourceId === resourceId && (['queued', 'running'].includes(j.status) || payload.blocks.some(b => b.activeJobId === j.id))).slice(0, 4);
  const eligibleBlocks = payload.blocks.filter(b => b.type !== "image" && b.text.trim());
  const translated = eligibleBlocks.filter(b => b.translation?.current && b.translation.validationStatus !== 'needs_review').length;
  const outline = payload.blocks.filter(b => /heading|title/.test(b.type));
  return <div className={`reader ${panel ? 'has-panel' : ''}`} style={{ '--reading-font-size': `${data.settings.fontSize}px` } as React.CSSProperties}>
    <div className="reader-heading"><Link href={`/resources/${resourceId}`} className="back-link"><ArrowLeft size={15}/>자료 정보</Link><div className="row-between"><div><span className="eyebrow">{label(resource.format.toLowerCase())} · {resource.author || (resource.url ? 'ASWATH DAMODARAN' : '원저자 미확인 · 사용자 PDF')}</span><h1>{resource.titleKo}</h1><p>{resource.titleEn}</p></div><a className="icon-button" href={originalUrl} download aria-label="보관한 원본 다운로드"><Download size={19}/></a></div></div>
    <div className="reader-toolbar"><div className="mode-switch" aria-label="읽기 모드">{(['ko', 'en', 'parallel'] as const).map(m => <button key={m} onClick={() => changeMode(m)} className={mode === m ? 'active' : ''} aria-pressed={mode === m}>{m === 'ko' ? '한국어' : m === 'en' ? '원문' : '나란히'}</button>)}</div><div className="reader-toolbar-right"><span className="muted translation-count">{translated}/{eligibleBlocks.length} 문단 번역</span><button className="icon-button" aria-label="현재 위치 북마크" onClick={bookmark}><Bookmark size={18}/></button><button className="icon-button" aria-label={panel ? '옆 패널 접기' : '용어·메모 패널 열기'} onClick={() => setPanel(panel ? null : 'notes')}>{panel ? <PanelRightClose size={19}/> : <PanelRightOpen size={19}/>}</button></div></div>
    {isPdf && <div className="pdf-navigation"><div className="page-picker"><button className="icon-button" disabled={location.page <= 1} aria-label="이전 PDF 페이지" onClick={() => changePage(location.page - 1)}><ChevronLeft size={18}/></button><form onSubmit={e => { e.preventDefault(); const value = Number(pageInput); if (Number.isInteger(value) && value >= 1 && value <= (payload.version.pageCount || 1)) changePage(value); else notify('PDF 페이지 범위 안의 정수를 입력해 주세요.', true); }}><input aria-label="PDF 페이지 번호" value={pageInput} onChange={e => setPageInput(e.target.value)} inputMode="numeric"/><span>/ {payload.version.pageCount || payload.pageCount}페이지</span></form><button className="icon-button" disabled={location.page >= (payload.version.pageCount || 1)} aria-label="다음 PDF 페이지" onClick={() => changePage(location.page + 1)}><ChevronRight size={18}/></button></div><div className="page-translate"><label htmlFor="page-end">현재 페이지부터</label><input id="page-end" type="number" min={location.page} max={payload.version.pageCount || 1} value={pageEnd} onChange={e => setPageEnd(Number(e.target.value))}/><span>페이지까지</span><button className="button small primary" disabled={translationBusy || !data.translationStatus.configured || pageEnd < location.page || pageEnd > (payload.version.pageCount || 1)} onClick={() => translate(true)}><Languages size={15}/>번역</button></div></div>}
    {(data.translationStatus.local || !data.translationStatus.configured) && <div className="translation-setup-note"><Languages size={17}/><span>{data.translationStatus.local && <strong>무료 번역 · 이 PC에서 처리 </strong>}{data.translationStatus.statusMessage}</span><Link href="/settings">{data.translationStatus.configured ? '번역 정보' : '설정 안내'} <ArrowUpRight size={14}/></Link></div>}
    {['needs_ocr', 'ocr_required', 'ocr_needed', 'partial', 'failed'].includes(payload.version.extractionStatus) && <div className="info-note"><BookOpen size={18}/><p>{label(payload.version.extractionStatus)} · 추출이 불확실한 내용은 원본과 함께 확인하세요. 스캔 이미지의 OCR은 지원하지 않습니다.</p></div>}
    <div className="reader-body"><div className="reading-column">{loading && <Loading/>}<div className="document-version">버전 {payload.version.id.slice(0, 8)} · {dateLabel(payload.version.importedAt)} 가져옴 <span>{saveState}</span></div>
      {isPdf && mode !== 'ko' && <PdfPage url={originalUrl} pageNumber={location.page}/>}
      {isPdf && mode === 'ko' && <details className="pdf-original-disclosure"><summary>수식·도표와 원본 페이지 확인</summary><PdfPage url={originalUrl} pageNumber={location.page}/></details>}
      <div className={`document-blocks mode-${mode} ${isPdf ? 'pdf-blocks' : ''}`}>
        {payload.blocks.length > 0 && <div className="document-column-labels"><span>{mode === 'ko' ? '한국어 · 기계 번역' : 'ENGLISH · 원문'}</span>{mode === 'parallel' && <span>한국어 · 기계 번역</span>}</div>}
        {payload.blocks.map(block => <section id={`block-${block.id}`} data-source-block={block.id} key={block.id} className={`source-block ${activeBlock === block.id ? 'active-block' : ''} ${selectedIds.includes(block.id) ? 'selected-block' : ''}`} onClick={() => selectBlock(block)}>
          <div className="block-select"><input type="checkbox" aria-label={`${block.order + 1}번째 문단 번역 선택`} checked={selectedIds.includes(block.id)} disabled={block.type === "image" || !block.text.trim()} onChange={e => { setSelectedIds(ids => e.target.checked ? [...ids, block.id] : ids.filter(id => id !== block.id)); setActiveBlock(block.id); }}/><span>{String(block.order + 1).padStart(2, '0')}</span></div>
          {(mode === 'en' || mode === 'parallel') && <div lang="en" className="original-block"><BlockContent block={block} onTerm={showTerm}/></div>}
          {(mode === 'ko' || mode === 'parallel') && <div lang="ko" className="translated-block">{block.type === "image" ? <BlockContent block={block} onTerm={showTerm}/> : block.translation ? <TranslationContent block={block} translation={block.translation} onTerm={showTerm} onReviewed={translation => applyReviewedTranslation(block.id, translation)} onReload={() => reloadTranslation(block.id)}/> : <div className="untranslated"><Languages size={20}/><p>{block.activeJobId ? '번역 작업이 진행 중입니다.' : '아직 번역하지 않은 문단'}</p><button className="text-link" disabled={!data.translationStatus.configured || translationBusy} onClick={() => { setSelectedIds(ids => ids.includes(block.id) ? ids : [...ids, block.id]); }}>번역할 문단에 선택 <Check size={13}/></button>{mode === 'ko' && <details><summary>원문 펼쳐보기</summary><div lang="en"><BlockContent block={block} onTerm={showTerm}/></div></details>}</div>}</div>}
          {block.warnings?.length > 0 && <div className="block-warnings">원문 확인 필요 · {block.warnings.join(' · ')}</div>}
        </section>)}
      </div>{payload.blocks.length === 0 && <Empty title={isPdf ? '이 페이지에서 읽을 텍스트를 찾지 못했습니다' : '추출된 본문이 없습니다'} description={isPdf ? '스캔이나 도표 중심 페이지일 수 있습니다. 원본 페이지를 확인해 주세요.' : '자료 상세에서 원본과 가져오기 상태를 확인해 주세요.'}/>}
      {!isPdf && (payload.prevCursor != null || payload.nextCursor != null) && <div className="reader-pagination"><button className="button secondary" disabled={payload.prevCursor == null || loading} onClick={() => payload.prevCursor != null && changeCursor(payload.prevCursor)}><ArrowLeft size={16}/>이전 문단</button><span>{payload.total}개 문단 중 {payload.blocks.length}개 표시</span><button className="button secondary" disabled={payload.nextCursor == null || loading} onClick={() => payload.nextCursor != null && changeCursor(payload.nextCursor)}>다음 문단<ArrowRight size={16}/></button></div>}
      {isPdf && <div className="reader-pagination"><button className="button secondary" disabled={location.page <= 1} onClick={() => changePage(location.page - 1)}><ArrowLeft size={16}/>이전 페이지</button><span>{location.page} / {payload.version.pageCount}</span><button className="button secondary" disabled={location.page >= (payload.version.pageCount || 1)} onClick={() => changePage(location.page + 1)}>다음 페이지<ArrowRight size={16}/></button></div>}
      <button className="text-link save-position-button" onClick={() => savePosition(activeBlock)}>현재 읽기 위치 저장 <Check size={14}/></button>
    </div>
    {panel && <aside className="reader-panel"><div className="reader-panel-tabs"><button aria-pressed={panel === 'notes'} className={panel === 'notes' ? 'active' : ''} onClick={() => setPanel('notes')}><NotebookPen size={16}/>메모</button><button aria-pressed={panel === 'terms'} className={panel === 'terms' ? 'active' : ''} onClick={() => setPanel('terms')}><BookOpen size={16}/>용어</button><button aria-pressed={panel === 'outline'} className={panel === 'outline' ? 'active' : ''} onClick={() => setPanel('outline')}><List size={16}/>목차</button><button className="icon-button panel-close" aria-label="패널 닫기" onClick={() => setPanel(null)}><X size={16}/></button></div>
      {panel === 'notes' && <div className="panel-inner"><span className="eyebrow">MY THOUGHTS</span><h3>나의 언어로 남기기</h3><p className="muted">문단을 누르면 그 위치에 연결됩니다.</p>{selected && <blockquote lang="en">{selected.text.slice(0, 170)}{selected.text.length > 170 ? '…' : ''}</blockquote>}<label className="field-label" htmlFor="reader-note">{selected ? `${selected.order + 1}번째 문단 메모` : '이 페이지 메모'}</label><textarea id="reader-note" value={noteText} onChange={e => setNoteText(e.target.value)} placeholder="이해한 내용, 떠오른 질문을 자유롭게 적어보세요." rows={6}/><button className="button primary full" disabled={saving || !noteText.trim()} onClick={saveNote}><NotebookPen size={16}/>{saving ? '저장 중…' : '메모 저장'}</button>{notes.length > 0 && <div className="reader-saved-notes"><h4>이 위치의 메모</h4>{notes.map(n => <div key={n.id}><p>{n.text}</p><small>{dateLabel(n.updatedAt)}</small><Link href="/notes">수정 <ArrowUpRight size={12}/></Link></div>)}</div>}</div>}
      {panel === 'terms' && <div className="panel-inner"><div className="panel-search"><Search size={16}/><input aria-label="본문 용어 검색" placeholder="한국어·영문·약어 검색" value={termQuery} onChange={e => { setTermQuery(e.target.value); setSelectedTerm(null); }}/></div>{selectedTerm ? <div className="reader-term"><span className="eyebrow">FINANCIAL CONCEPT</span><h3>{selectedTerm.termKo}</h3><small>{selectedTerm.termEn}{selectedTerm.acronym ? ` · ${selectedTerm.acronym}` : ''}</small><p>{selectedTerm.definitionKo}</p>{selectedTerm.formula && <code>{selectedTerm.formula}</code>}{selectedTerm.exampleKo && <><h4>학습 예시 · 학습실 작성</h4><p>{selectedTerm.exampleKo}</p></>}{selectedTerm.notes && <p className="small-notice">{selectedTerm.notes}</p>}<Link className="text-link" href={`/glossary?termId=${selectedTerm.id}`}>사전에서 더 보기 <ArrowUpRight size={14}/></Link><button className="text-link full" onClick={() => setSelectedTerm(null)}>용어 목록으로</button></div> : <div className="reader-term-list">{matchedTerms.slice(0, 40).map(t => <button key={t.id} onClick={() => setSelectedTerm(t)}><strong>{t.termKo}</strong><span>{t.acronym || t.termEn}</span><ChevronRight size={14}/></button>)}{!matchedTerms.length && <p className="muted">일치하는 용어가 없습니다.</p>}</div>}</div>}
      {panel === 'outline' && <div className="panel-inner"><h3>현재 구간의 목차</h3>{outline.length ? <div className="outline-list">{outline.map(b => <button key={b.id} onClick={() => { setActiveBlock(b.id); window.document.getElementById(`block-${b.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }}>{b.text}</button>)}</div> : <p className="muted">이 구간에는 별도 제목이 없습니다. 문단 순서대로 읽어보세요.</p>}<Link href={`/resources/${resourceId}`} className="text-link">연결 자료와 해답 보기 <ArrowUpRight size={14}/></Link></div>}
      {currentJobs.length > 0 && <div className="panel-inner jobs-in-reader">{currentJobs.map(j => <JobCard key={j.id} job={j}/>)}</div>}
    </aside>}
    </div>{selectedIds.length > 0 && <div className="translation-selection"><div><Languages size={20}/><span><strong>{selectedIds.length}개 문단 선택</strong><small>{payload.blocks.filter(b => selectedIds.includes(b.id)).reduce((sum, b) => sum + b.text.length, 0).toLocaleString()}자 · {data.translationStatus.local ? '무료 · 이 PC에서 번역' : '선택한 원문만 번역'}</small></span></div><div><button className="text-link" onClick={() => setSelectedIds([])}>선택 해제</button><button className="button primary" disabled={translationBusy || !data.translationStatus.configured} onClick={() => translate()}>{translationBusy ? '요청 중…' : '한국어로 번역'}<ArrowRight size={16}/></button></div></div>}
  </div>;
}

function TranslationContent({ block, translation, onTerm, onReviewed, onReload }: { block: Block; translation: Translation; onTerm: (term: Term) => void; onReviewed: (translation: Translation) => void; onReload: () => Promise<Translation> }) {
  const { notify } = useRoom();
  const [editing, setEditing] = useState(false); const [draft, setDraft] = useState(''); const [saving, setSaving] = useState(false); const [checking, setChecking] = useState(false); const [error, setError] = useState(''); const [status, setStatus] = useState('');
  const [editBase, setEditBase] = useState<{ translationId: string; expectedReviewId: string | null } | null>(null);
  const savingRef = useRef(false); const editButton = useRef<HTMLButtonElement>(null); const input = useRef<HTMLTextAreaElement>(null);
  const isTable = block.type === 'table' || Array.isArray(block.structure?.rows) || translation.structure != null;
  const reviewed = ['user_reviewed', 'reviewed', 'approved'].includes(translation.reviewStatus);
  const fieldId = `translation-edit-${block.id}`; const helpId = `${fieldId}-help`; const errorId = `${fieldId}-error`;
  function closeEditor() { if (savingRef.current) return; setEditing(false); setError(''); requestAnimationFrame(() => editButton.current?.focus()); }
  async function saveReview() {
    if (savingRef.current || !draft.trim() || !editBase) return;
    savingRef.current = true; setSaving(true); setError(''); setStatus('');
    try {
      const result = await api<Translation>('/api/translations/review', 'POST', { ...editBase, textKo: draft });
      onReviewed(result); setEditing(false); notify('검수한 번역을 저장했습니다. 같은 원문을 번역할 때 다시 활용합니다.');
      requestAnimationFrame(() => editButton.current?.focus());
    } catch (error) { setError(error instanceof Error ? error.message : '번역을 저장하지 못했습니다. 내용을 확인하고 다시 시도하세요.'); }
    finally { savingRef.current = false; setSaving(false); }
  }
  async function compareLatest() {
    if (savingRef.current) return;
    savingRef.current = true; setChecking(true);
    try {
      const latest = await onReload(); setEditBase({ translationId: latest.id, expectedReviewId: latest.reviewId ?? null });
      setError(''); setStatus('최신 저장본을 위에 불러왔습니다. 작성한 내용과 비교한 뒤 다시 저장하세요.');
      requestAnimationFrame(() => input.current?.focus());
    } catch (error) { setError(error instanceof Error ? error.message : '최신 번역을 불러오지 못했습니다.'); }
    finally { savingRef.current = false; setChecking(false); }
  }
  return <>
    <div className="translation-badges"><span>{reviewed ? '사용자 검수 완료' : '기계 번역 · 사용자 미검수'}</span>{translation.origin === 'memory' && <span>검수 번역 재사용</span>}<span>{translation.validationStatus === 'needs_review' ? '자동 검사: 검토 필요' : `자동 검사: ${['passed', 'valid'].includes(translation.validationStatus) ? '통과' : label(translation.validationStatus)}`}</span>{!translation.current && <span className="warning-label">설정 변경 · 이전 번역</span>}</div>
    <BlockContent block={{ ...block, text: translation.textKo, structure: translation.structure || null, type: block.type === 'table' ? 'table' : /heading|title/.test(block.type) ? 'heading' : 'paragraph' }} onTerm={onTerm}/>
    {isTable ? <p className="small-notice">표 등 구조가 있는 번역은 아직 수정할 수 없습니다. 텍스트 문단을 수정할 수 있습니다.</p> : <div className="translation-review" onClick={event => event.stopPropagation()}>
      {editing ? <form className="translation-editor" style={{ marginTop: 16 }} onSubmit={event => { event.preventDefault(); void saveReview(); }} aria-busy={saving || checking}>
        <label htmlFor={fieldId} className="field-label">{block.order + 1}번째 문단 번역 수정</label>
        <p className="small-notice" id={helpId}>원문과 비교해 다듬어 주세요. 숫자·수식은 그대로 유지하며, 검수한 문장은 다음 번역에 활용합니다.</p>
        <textarea ref={input} id={fieldId} value={draft} onChange={event => setDraft(event.target.value)} rows={Math.min(12, Math.max(4, Math.ceil(draft.length / 45)))} readOnly={saving || checking} aria-describedby={`${helpId}${error ? ` ${errorId}` : ''}`} aria-invalid={!!error} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); closeEditor(); } if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void saveReview(); } }}/>
        {error && <div><p id={errorId} role="alert" className="inline-error">{error}</p><button type="button" className="text-link" disabled={saving || checking} onClick={() => void compareLatest()}>{checking ? '불러오는 중…' : '최신 번역과 비교'}</button><p className="small-notice">작성 중인 내용은 유지합니다. 최신 저장본을 확인한 뒤 다시 저장할 수 있습니다.</p></div>}
        {status && <p role="status" className="small-notice">{status}</p>}
        <div className="row-gap" style={{ marginTop: 12, flexWrap: 'wrap' }}><button type="submit" className="button primary small" disabled={saving || checking || !draft.trim()}><Check size={14}/>{saving ? '저장 중…' : '검수 완료로 저장'}</button><button type="button" className="button secondary small" disabled={saving || checking} onClick={closeEditor}>취소</button></div>
      </form> : <button ref={editButton} type="button" className="text-link" style={{ marginTop: 12 }} onClick={() => { setDraft(translation.textKo); setEditBase({ translationId: translation.id, expectedReviewId: translation.reviewId ?? null }); setError(''); setStatus(''); setEditing(true); requestAnimationFrame(() => input.current?.focus()); }}><Pencil size={13}/>번역 수정</button>}
    </div>}
  </>;
}

function GlossaryText({ text, onTerm }: { text: string; onTerm: (term: Term) => void }) {
  const { data } = useRoom();
  const dictionary = useMemo(() => { const map = new Map<string, Term>(); for (const term of data.glossary) for (const name of [term.termKo, term.termEn, term.acronym || '']) { if (name.length >= 2) map.set(name.toLowerCase(), term); } return map; }, [data.glossary]);
  const pattern = useMemo(() => new RegExp(`(${[...dictionary.keys()].sort((a, b) => b.length - a.length).map(s => /^[a-z0-9 ]+$/i.test(s) ? `\\b${s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b` : s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi'), [dictionary]);
  if (!dictionary.size) return <>{text}</>;
  return <>{text.split(pattern).map((chunk, i) => dictionary.has(chunk.toLowerCase()) ? <button key={i} className="term-link" onClick={e => { e.stopPropagation(); onTerm(dictionary.get(chunk.toLowerCase())!); }} title={`${dictionary.get(chunk.toLowerCase())!.termKo} 뜻 보기`}>{chunk}</button> : chunk)}</>;
}

function BlockContent({ block, onTerm }: { block: Block; onTerm: (term: Term) => void }) {
  const structure = block.structure || {};
  if (block.type === 'image') {
    const assetId = typeof structure.assetId === 'string' ? structure.assetId : null;
    return <figure className="source-image">{assetId && structure.assetStatus !== 'failed' ? <SourceImage assetId={assetId} alt={String(structure.alt || block.text || '원문 이미지')}/> : <p>원문 이미지가 저장되지 않았습니다.</p>}{block.text && <figcaption>{block.text}</figcaption>}{typeof structure.sourceUrl === 'string' && /^https?:\/\//.test(structure.sourceUrl) && <a href={structure.sourceUrl} target="_blank" rel="noreferrer">이미지 원문 <ExternalLink size={12}/></a>}</figure>;
  }
  if (block.type === 'table' && Array.isArray(structure.rows)) {
    type Cell = { text: string; colSpan?: number; rowSpan?: number; header?: boolean };
    return <div className="source-table-scroll"><table><tbody>{(structure.rows as { cells: Cell[] }[]).map((row, i) => <tr key={i}>{row.cells.map((cell, j) => cell.header ? <th key={j} colSpan={cell.colSpan} rowSpan={cell.rowSpan}><GlossaryText text={cell.text} onTerm={onTerm}/></th> : <td key={j} colSpan={cell.colSpan} rowSpan={cell.rowSpan}><GlossaryText text={cell.text} onTerm={onTerm}/></td>)}</tr>)}</tbody></table></div>;
  }
  const content = <GlossaryText text={block.text} onTerm={onTerm}/>;
  const links = Array.isArray(structure.links) ? (structure.links as { text: string; url: string }[]).filter(l => /^https?:\/\//.test(l.url)) : [];
  return <>{/heading|title/.test(block.type) ? <h3>{content}</h3> : block.type === 'list' || block.type === 'list_item' ? <div className="source-list">• {content}</div> : <p>{content}</p>}{links.length > 0 && <div className="source-links">{links.map((link, i) => <a href={link.url} target="_blank" rel="noreferrer" key={i}>{link.text || '원문 링크'} <ExternalLink size={12}/></a>)}</div>}</>;
}

function SourceImage({ assetId, alt }: { assetId: string; alt: string }) {
  const [failed, setFailed] = useState(false);
  return failed ? <p>원문 이미지를 표시하지 못했습니다. 원문 링크에서 확인해 주세요.</p> : <img src={`/api/assets/${assetId}`} alt={alt} loading="lazy" onError={() => setFailed(true)}/>;
}
