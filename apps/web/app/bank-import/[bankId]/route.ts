import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// This intentionally sits outside /api. /api is a catch-all rewrite to the
// private backend, while this route must receive multipart bodies itself.
export async function POST(request: NextRequest, context: { params: Promise<{ bankId: string }> }) {
  const apiBase = process.env.API_INTERNAL_URL;
  if (!apiBase) return Response.json({ detail: "Upload service is not configured" }, { status: 503 });
  const { bankId } = await context.params;
  try {
    const form = await request.formData();
    const response = await fetch(`${apiBase}/admin/question-banks/${encodeURIComponent(bankId)}/imports`, {
      method: "POST",
      headers: request.headers.get("cookie") ? { cookie: request.headers.get("cookie")! } : {},
      body: form,
      cache: "no-store",
    });
    const headers = new Headers();
    const contentType = response.headers.get("content-type");
    if (contentType) headers.set("content-type", contentType);
    return new Response(await response.arrayBuffer(), { status: response.status, headers });
  } catch (error) {
    console.error("Bank import relay failed", error);
    return Response.json({ detail: "Upload relay could not reach the import service" }, { status: 502 });
  }
}
