"use client";

import { PointerEvent, useEffect, useRef, useState } from "react";

type Figure = { page: number; bbox: number[]; role: string; option_index: number | null; alt: string };
type Viewport = { page: number; bbox: number[] };
type Source = {
  filename: string; source_page: number; stem_markdown: string; original_url?: string;
  conversion_issues?: string[]; visual_intent?: string; focus?: Viewport | null;
  pages: { page: number; width: number; height: number }[]; diagrams: Figure[];
};

export function FigureEditor({ questionId, options, api, done, onActiveChange }: {
  questionId: string;
  options: { value?: string }[];
  api: (path: string, options?: RequestInit) => Promise<any>;
  done: () => void | Promise<void>;
  onActiveChange?: (active: boolean) => void;
}) {
  const [source, setSource] = useState<Source>();
  const [figures, setFigures] = useState<Figure[]>([]);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(-1);
  const [owner, setOwner] = useState("question");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [drag, setDrag] = useState<number[] | null>(null);
  const [showFullPage, setShowFullPage] = useState(false);
  const [choiceCount, setChoiceCount] = useState(4);
  const [choiceKind, setChoiceKind] = useState("single_choice");
  const start = useRef<number[] | null>(null);
  const request = useRef<AbortController | null>(null);
  const info = source?.pages.find(p => p.page === page);
  const viewport = info && !showFullPage && source?.focus?.page === page
    ? source.focus.bbox : info ? [0, 0, info.width, info.height] : [0, 0, 1, 1];

  useEffect(() => () => request.current?.abort(), []);
  const close = () => {
    request.current?.abort(); request.current = null;
    start.current = null; setDrag(null); setSource(undefined); setConfirmed(false); setError("");
    onActiveChange?.(false);
  };
  const open = async () => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setBusy(true); setError("");
    try {
      const data: Source = await api(`/questions/${questionId}/figure-source`, { signal: controller.signal });
      if (request.current !== controller) return;
      setSource(data); setPage(data.focus?.page || data.source_page);
      setShowFullPage(false);
      setFigures((data.diagrams || []).filter(d => Array.isArray(d.bbox) && d.bbox.length === 4)
        .map(d => ({ ...d, role: d.role || "question", option_index: d.option_index ?? null })));
      onActiveChange?.(true);
    } catch (e: any) {
      if (e?.name !== "AbortError") setError(e.message);
    } finally { if (request.current === controller) setBusy(false); }
  };
  const point = (event: PointerEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    const width = viewport[2] - viewport[0], height = viewport[3] - viewport[1];
    return [Math.max(viewport[0], Math.min(viewport[2], viewport[0] + (event.clientX-bounds.left)/bounds.width*width)),
      Math.max(viewport[1], Math.min(viewport[3], viewport[1] + (event.clientY-bounds.top)/bounds.height*height))];
  };
  const owning = (value: string) => value.startsWith("option:")
    ? { role: "option", option_index: Number(value.split(":")[1]) }
    : { role: value, option_index: null };
  const changeOwner = (value: string) => {
    setOwner(value); setConfirmed(false);
    if (selected >= 0) setFigures(previous => previous.map((f,i) => i === selected ? { ...f, ...owning(value) } : f));
  };
  const save = async () => {
    setBusy(true); setError("");
    try {
      await api(`/questions/${questionId}/figures`, { method: "PUT", body: JSON.stringify({ diagrams: figures, confirmed }) });
      close(); await done();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  const addChoices = async () => {
    if (!source) return;
    setBusy(true); setError("");
    try {
      await api(`/questions/${questionId}/draft`, { method:"PUT", body:JSON.stringify({
        kind:choiceKind, stem_markdown:source.stem_markdown, answer:null,
        options:Array.from({length:choiceCount},(_,i)=>({value:String.fromCharCode(97+i),markdown:"",requires_diagram:true})),
      }) });
      close(); await done();
    } catch (e:any) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <section className="figure-review">
    {!source ? <button type="button" className="secondary-action" disabled={busy} onClick={open}>
      {busy ? "Opening figure adjustment…" : "Adjust detected figure"}
    </button> : <div className="figure-editor-drawer" role="dialog" aria-label="Adjust detected figure">
      <div className="figure-editor-heading"><strong>Adjust figure from {source.filename}</strong><button type="button" className="secondary-action" onClick={close}>Close</button></div>
      {!!source.conversion_issues?.length && <p>Check complex Word graphics against the original document. {source.original_url && <a href={source.original_url} target="_blank" rel="noreferrer">Open original document</a>}</p>}
      <p>Start with the detected question area. Drag around a complete figure only when the proposed crop needs correction.</p>
      {options.length < 2 && <div className="figure-review-controls">
        <p>For diagram-only choices, add the printed choice count before assigning each crop.</p>
        <label>Choices <select value={choiceCount} onChange={e => setChoiceCount(Number(e.target.value))}>{[2,3,4,5,6,7,8].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
        <label>Type <select value={choiceKind} onChange={e => setChoiceKind(e.target.value)}><option value="single_choice">Single correct</option><option value="multiple_choice">Multiple correct</option></select></label>
        <button type="button" disabled={busy} onClick={addChoices}>Add visual choices</button>
      </div>}
      <div className="figure-review-controls">
        <label>Source page <select value={page} onChange={e => { setPage(Number(e.target.value)); setShowFullPage(false); setSelected(-1); setDrag(null); start.current=null; }}>
          {source.pages.map(p => <option key={p.page} value={p.page}>{p.page}</option>)}
        </select></label>
        <label>Figure belongs to <select value={owner} onChange={e => changeOwner(e.target.value)}>
          <option value="question">Question</option>
          {options.map((o,i) => <option key={i} value={`option:${i}`}>Option {String.fromCharCode(65+i)}</option>)}
          <option value="solution">Worked solution</option>
        </select></label>
        <button type="button" className="secondary-action" onClick={() => setShowFullPage(value => !value)}>{showFullPage ? "Focus question area" : "View full source page"}</button>
        <button type="button" className="secondary-action" disabled={selected < 0} onClick={() => { setFigures(previous => previous.filter((_,i) => i !== selected)); setSelected(-1); setConfirmed(false); }}>Remove selected</button>
      </div>
      {info && <svg className="figure-source-canvas" viewBox={`${viewport[0]} ${viewport[1]} ${viewport[2]-viewport[0]} ${viewport[3]-viewport[1]}`}
        aria-label="Source page: drag to select a figure" role="img"
        onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); start.current=point(event); setDrag(null); setSelected(-1); }}
        onPointerMove={event => { if (start.current) { const p=point(event); setDrag([Math.min(start.current[0],p[0]),Math.min(start.current[1],p[1]),Math.max(start.current[0],p[0]),Math.max(start.current[1],p[1])]); } }}
        onPointerCancel={() => { start.current=null; setDrag(null); }}
        onPointerUp={event => { if (start.current) { const p=point(event), b=[Math.min(start.current[0],p[0]),Math.min(start.current[1],p[1]),Math.max(start.current[0],p[0]),Math.max(start.current[1],p[1])]; if (b[2]-b[0] >= 6 && b[3]-b[1] >= 6) { setSelected(figures.length); setFigures(previous => [...previous,{page,bbox:b,...owning(owner),alt:"Reviewed source figure"}]); setConfirmed(false); } } start.current=null; setDrag(null); }}>
        <image href={`/api/questions/${questionId}/figure-source/${page}`} width={info.width} height={info.height} style={{pointerEvents:"none"}} />
        {figures.map((figure,i) => figure.page === page ? <g key={i} onPointerDown={event => { event.stopPropagation(); setSelected(i); setOwner(figure.role === "option" ? `option:${figure.option_index}` : figure.role); }}>
          <rect x={figure.bbox[0]} y={figure.bbox[1]} width={figure.bbox[2]-figure.bbox[0]} height={figure.bbox[3]-figure.bbox[1]} fill={i === selected ? "#f5a62333" : "#26777722"} stroke={i === selected ? "#bd6900" : "#267777"} strokeWidth="2" />
        </g> : null)}
        {drag && <rect x={drag[0]} y={drag[1]} width={drag[2]-drag[0]} height={drag[3]-drag[1]} fill="#f5a62322" stroke="#bd6900" strokeDasharray="4 3" />}
      </svg>}
      <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> I checked the detected figures and visual choices against the source.</label>
      <div className="review-buttons"><button type="button" disabled={busy || !confirmed} onClick={save}>{busy ? "Saving…" : "Save figure adjustment"}</button><button type="button" className="secondary-action" onClick={close}>Cancel</button></div>
    </div>}
    {error && <p role="alert" className="diagram-error">{error}</p>}
  </section>;
}
