"use client";

import { PointerEvent, useRef, useState } from "react";

type Figure = { page: number; bbox: number[]; role: string; option_index: number | null; alt: string };
type Source = { filename: string; source_page: number; stem_markdown: string; original_url?: string; conversion_issues?: string[]; pages: { page: number; width: number; height: number }[]; diagrams: Figure[] };

export function FigureEditor({ questionId, options, api, done }: {
  questionId: string;
  options: { value?: string }[];
  api: (path: string, options?: RequestInit) => Promise<any>;
  done: () => void | Promise<void>;
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
  const [choiceCount, setChoiceCount] = useState(4);
  const [choiceKind, setChoiceKind] = useState("single_choice");
  const start = useRef<number[] | null>(null);
  const info = source?.pages.find(p => p.page === page);
  const open = async () => {
    setBusy(true); setError("");
    try {
      const data: Source = await api(`/questions/${questionId}/figure-source`);
      setSource(data); setPage(data.source_page);
      setFigures((data.diagrams || []).filter(d => Array.isArray(d.bbox) && d.bbox.length === 4)
        .map(d => ({ ...d, role: d.role || "question", option_index: d.option_index ?? null })));
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  const point = (event: PointerEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return [Math.max(0, Math.min(info!.width, (event.clientX-bounds.left)/bounds.width*info!.width)),
      Math.max(0, Math.min(info!.height, (event.clientY-bounds.top)/bounds.height*info!.height))];
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
      setSource(undefined); setConfirmed(false); await done();
    } catch (e: any) { setError(e.message); }
    finally { setBusy(false); }
  };
  const addChoices = async () => {
    setBusy(true); setError("");
    try {
      await api(`/questions/${questionId}/draft`, { method:"PUT", body:JSON.stringify({
        kind:choiceKind,stem_markdown:source!.stem_markdown,answer:null,
        options:Array.from({length:choiceCount},(_,i)=>({value:String.fromCharCode(97+i),markdown:"",requires_diagram:true})),
      }) });
      setSource(undefined); await done();
    } catch (e:any) { setError(e.message); }
    finally { setBusy(false); }
  };
  return <section className="figure-review">
    {!source ? <button type="button" className="secondary-action" disabled={busy} onClick={open}>
      {busy ? "Opening source…" : "Review / correct figures"}
    </button> : <>
      <strong>Figures from {source.filename}</strong>
      {!!source.conversion_issues?.length && <p>Check complex Word graphics against the original document. If a figure is missing from this converted preview, upload a PDF exported from Word.
        {source.original_url && <> <a href={source.original_url} target="_blank" rel="noreferrer">Open original document</a></>}
      </p>}
      {options.length<2 && <div className="figure-review-controls">
        <p>If the source has diagram-only answer choices, add them before assigning figures.</p>
        <label>Number of choices <select value={choiceCount} onChange={e=>setChoiceCount(Number(e.target.value))}>
          {[2,3,4,5,6,7,8].map(n=><option key={n} value={n}>{n}</option>)}
        </select></label>
        <label>Answer type <select value={choiceKind} onChange={e=>setChoiceKind(e.target.value)}>
          <option value="single_choice">Single correct choice</option><option value="multiple_choice">Multiple correct choices</option>
        </select></label>
        <button type="button" disabled={busy} onClick={addChoices}>Add visual answer choices</button>
      </div>}
      <p>Drag around each complete figure, including its labels. Select a box to assign it to the question, an answer option, or the solution. Remove incorrect boxes.</p>
      <div className="figure-review-controls">
        <label>Source page <select value={page} onChange={e => { setPage(Number(e.target.value)); setSelected(-1); setDrag(null); start.current=null; }}>
          {source.pages.map(p => <option key={p.page} value={p.page}>{p.page}</option>)}
        </select></label>
        <label>Figure belongs to <select value={owner} onChange={e => changeOwner(e.target.value)}>
          <option value="question">Question</option>
          {options.map((o,i) => <option key={i} value={`option:${i}`}>Option {String.fromCharCode(65+i)}</option>)}
          <option value="solution">Worked solution</option>
        </select></label>
        <button type="button" className="secondary-action" disabled={selected < 0} onClick={() => {
          setFigures(previous => previous.filter((_,i) => i !== selected)); setSelected(-1); setConfirmed(false);
        }}>Remove selected figure</button>
        <button type="button" className="secondary-action" onClick={() => { setSelected(-1); setOwner("question"); }}>Draw another figure</button>
      </div>
      {info && <svg className="figure-source-canvas" viewBox={`0 0 ${info.width} ${info.height}`}
        aria-label="Source page: drag to select a figure" role="img"
        onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); start.current=point(event); setDrag(null); setSelected(-1); }}
        onPointerMove={event => { if (start.current) { const p=point(event); setDrag([Math.min(start.current[0],p[0]),Math.min(start.current[1],p[1]),Math.max(start.current[0],p[0]),Math.max(start.current[1],p[1])]); } }}
        onPointerCancel={() => { start.current=null; setDrag(null); }}
        onPointerUp={event => {
          if (start.current) {
            const p=point(event), b=[Math.min(start.current[0],p[0]),Math.min(start.current[1],p[1]),Math.max(start.current[0],p[0]),Math.max(start.current[1],p[1])];
            if (b[2]-b[0] >= 6 && b[3]-b[1] >= 6) {
              setSelected(figures.length); setFigures(previous => [...previous,{page,bbox:b,...owning(owner),alt:"Reviewed source figure"}]); setConfirmed(false);
            }
          }
          start.current=null; setDrag(null);
        }}>
        <image href={`/api/questions/${questionId}/figure-source/${page}`} width={info.width} height={info.height} style={{pointerEvents:"none"}} />
        {figures.map((figure,i) => figure.page === page ? <g key={i} onPointerDown={event => {
          event.stopPropagation(); setSelected(i); setOwner(figure.role === "option" ? `option:${figure.option_index}` : figure.role);
        }}>
          <rect x={figure.bbox[0]} y={figure.bbox[1]} width={figure.bbox[2]-figure.bbox[0]} height={figure.bbox[3]-figure.bbox[1]}
            fill={i === selected ? "#f5a62333" : "#26777722"} stroke={i === selected ? "#bd6900" : "#267777"} strokeWidth="2" />
          <text x={figure.bbox[0]+3} y={figure.bbox[1]+12} fontSize="10" fill="#174e4e">
            {figure.role === "option" ? `Option ${String.fromCharCode(65+(figure.option_index ?? 0))}` : figure.role}
          </text>
        </g> : null)}
        {drag && <rect x={drag[0]} y={drag[1]} width={drag[2]-drag[0]} height={drag[3]-drag[1]} fill="#f5a62322" stroke="#bd6900" strokeDasharray="4 3" />}
      </svg>}
      <p>{figures.length} figure(s) across all source pages.</p>
      <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} /> I checked every figure and visual answer option against the source.</label>
      <div className="review-buttons">
        <button type="button" disabled={busy || !confirmed} onClick={save}>{busy ? "Saving…" : "Save reviewed figures"}</button>
        <button type="button" className="secondary-action" onClick={() => { setSource(undefined); setConfirmed(false); }}>Cancel</button>
      </div>
      <small>Saved corrections require question approval. Existing test versions keep their original figures.</small>
    </>}
    {error && <p role="alert" className="diagram-error">{error}</p>}
  </section>;
}
