export type BankLocation = { bankId: string; documentId?: string; questionId?: string };

export function readBankLocation(pathname: string, hash: string): BankLocation | null {
  const parts = pathname.split("/").filter(Boolean);
  try {
    if (parts[0] === "admin" && parts[1] === "banks" && parts[2]) {
      return { bankId: decodeURIComponent(parts[2]), documentId: parts[3] === "documents" && parts[4] ? decodeURIComponent(parts[4]) : undefined, questionId: parts[3] === "questions" && parts[4] ? decodeURIComponent(parts[4]) : parts[5] === "questions" && parts[6] ? decodeURIComponent(parts[6]) : undefined };
    }
    const legacy = new URLSearchParams(hash.replace(/^#/, ""));
    const bankId = legacy.get("bank");
    return bankId ? { bankId, documentId: legacy.get("document") || undefined, questionId: legacy.get("question") || undefined } : null;
  } catch { return null; }
}

export function bankUrl(location: BankLocation) {
  let path = `/admin/banks/${encodeURIComponent(location.bankId)}`;
  if (location.documentId) path += `/documents/${encodeURIComponent(location.documentId)}`;
  if (location.questionId) path += `/questions/${encodeURIComponent(location.questionId)}`;
  return path;
}

export class LatestRequest {
  private sequence = 0;
  private controller: AbortController | null = null;
  key: string | null = null;
  start(key: string) {
    this.controller?.abort();
    this.controller = new AbortController();
    this.key = key;
    const sequence = ++this.sequence;
    return { signal: this.controller.signal, current: () => this.sequence === sequence && this.key === key };
  }
  cancel() { this.controller?.abort(); this.key = null; this.sequence++; }
}
