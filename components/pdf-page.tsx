'use client';

import { useEffect, useRef, useState } from 'react';
import { Minus, Plus } from 'lucide-react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { ErrorPanel, Loading } from './common';

export default function PdfPage({ url, pageNumber }: { url: string; pageNumber: number }) {
  const host = useRef<HTMLDivElement>(null); const viewportHost = useRef<HTMLDivElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null); const [error, setError] = useState('');
  const [zoom, setZoom] = useState(1); const [width, setWidth] = useState(580); const [rendering, setRendering] = useState(true);
  useEffect(() => { if (!host.current) return; const observer = new ResizeObserver(entries => setWidth(Math.max(240, entries[0].contentRect.width - 32))); observer.observe(host.current); return () => observer.disconnect(); }, []);
  useEffect(() => {
    let alive = true; let destroy: (() => void) | undefined; setError(''); setDocument(null);
    import('pdfjs-dist').then(pdfjs => {
      if (!alive) return; pdfjs.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';
      const task = pdfjs.getDocument({ url, cMapUrl: '/pdfjs/cmaps/', cMapPacked: true, standardFontDataUrl: '/pdfjs/standard_fonts/', wasmUrl: '/pdfjs/wasm/', isEvalSupported: false });
      destroy = () => { void task.destroy(); }; return task.promise;
    }).then(pdf => { if (alive && pdf) setDocument(pdf); }).catch(e => { if (alive) setError(`PDF 원본을 열지 못했습니다: ${e.message}`); });
    return () => { alive = false; destroy?.(); };
  }, [url]);
  useEffect(() => {
    if (!document || !viewportHost.current) return;
    let alive = true; let cancelRender: (() => void) | undefined; let cancelText: (() => void) | undefined;
    const mount = viewportHost.current; mount.replaceChildren(); setRendering(true); setError('');
    async function render() {
      const pdfjs = await import('pdfjs-dist'); const page = await document!.getPage(pageNumber); if (!alive) return;
      const base = page.getViewport({ scale: 1 }); const scale = width / base.width * zoom; const viewport = page.getViewport({ scale });
      const wrapper = window.document.createElement('div'); wrapper.className = 'pdf-rendered-page'; wrapper.style.width = `${viewport.width}px`; wrapper.style.height = `${viewport.height}px`; wrapper.style.setProperty('--scale-factor', String(scale)); wrapper.style.setProperty('--total-scale-factor', String(scale));
      const canvas = window.document.createElement('canvas'); canvas.setAttribute('aria-label', `원본 PDF ${pageNumber}페이지`); canvas.setAttribute('role', 'img');
      const pixelRatio = window.devicePixelRatio || 1; canvas.width = Math.floor(viewport.width * pixelRatio); canvas.height = Math.floor(viewport.height * pixelRatio); canvas.style.width = `${viewport.width}px`; canvas.style.height = `${viewport.height}px`; wrapper.appendChild(canvas);
      const textContainer = window.document.createElement('div'); textContainer.className = 'textLayer'; textContainer.lang = 'en'; wrapper.appendChild(textContainer); mount.replaceChildren(wrapper);
      const renderTask = page.render({ canvas, canvasContext: canvas.getContext('2d')!, viewport, transform: pixelRatio === 1 ? undefined : [pixelRatio, 0, 0, pixelRatio, 0, 0] }); cancelRender = () => renderTask.cancel();
      const textLayer = new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: textContainer, viewport }); cancelText = () => textLayer.cancel();
      await Promise.all([renderTask.promise, textLayer.render()]); if (alive) setRendering(false);
    }
    render().catch(e => { if (alive && e.name !== 'RenderingCancelledException' && e.name !== 'AbortException') { setError(`이 페이지를 표시하지 못했습니다: ${e.message}`); setRendering(false); } });
    return () => { alive = false; cancelRender?.(); cancelText?.(); };
  }, [document, pageNumber, zoom, width]);
  return <div className="pdf-viewer" ref={host}><div className="pdf-viewer-tools"><span>원본 PDF · {pageNumber}페이지</span><div><button className="icon-button" disabled={zoom <= 0.5} aria-label="PDF 축소" onClick={() => setZoom(v => Math.max(0.5, v - 0.25))}><Minus size={15}/></button><span>{Math.round(zoom * 100)}%</span><button className="icon-button" disabled={zoom >= 3} aria-label="PDF 확대" onClick={() => setZoom(v => Math.min(3, v + 0.25))}><Plus size={15}/></button><button className="text-link" onClick={() => setZoom(1)}>맞춤</button></div></div>{error && <ErrorPanel error={error}/>}<div className="pdf-scroll"><div ref={viewportHost}/></div>{rendering && !error && <Loading text="PDF 페이지를 표시하고 있습니다"/>}</div>;
}
