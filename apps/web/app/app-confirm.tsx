"use client";

import { useEffect, useRef, useState } from "react";
import { useDialogFocus } from "./dialog-focus";

type Request = { message: string; resolve: (accepted: boolean) => void };
const eventName = "testnow:confirm";

export function confirmApp(message: string): Promise<boolean> {
  return new Promise((resolve) => window.dispatchEvent(new CustomEvent<Request>(eventName, { detail: { message, resolve } })));
}

export function AppConfirmHost() {
  const [request, setRequest] = useState<Request | null>(null);
  const requestRef = useRef<Request | null>(null);
  const close = (accepted = false) => { const current = requestRef.current; requestRef.current = null; setRequest(null); current?.resolve(accepted); };
  const dialog = useDialogFocus(Boolean(request), () => close(false));
  useEffect(() => {
    const show = (event: Event) => { const next = (event as CustomEvent<Request>).detail; if (requestRef.current) requestRef.current.resolve(false); requestRef.current = next; setRequest(next); };
    window.addEventListener(eventName, show);
    return () => window.removeEventListener(eventName, show);
  }, []);
  if (!request) return null;
  return <div className="app-confirm-backdrop" role="presentation"><section ref={dialog} tabIndex={-1} className="app-confirm" role="alertdialog" aria-modal="true" aria-labelledby="app-confirm-title" aria-describedby="app-confirm-message"><p className="eyebrow">CONFIRM ACTION</p><h2 id="app-confirm-title">Please confirm</h2><p id="app-confirm-message">{request.message}</p><div className="dialog-actions"><button type="button" className="secondary-action" onClick={() => close(false)}>Cancel</button><button type="button" className="danger" onClick={() => close(true)}>Confirm</button></div></section></div>;
}
