import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/**
 * Multipart imports need an explicit route instead of a Next rewrite.  Some
 * hosting proxies reject a streamed multipart body while following a rewrite,
 * so FastAPI never receives the request.  This route accepts the browser
 * upload at the web service and forwards its parsed form to the private API.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ bankId: string }> }) {
  const apiBase = process.env.API_INTERNAL_URL;
  if (!apiBase) {
    return Response.json({ detail: "Upload service is not configured" }, { status: 503 });
  }
  const { bankId } = await context.params;
  try {
    const body = await request.formData();
    const upstream = await fetch(`${apiBase}/admin/question-banks/${encodeURIComponent(bankId)}/imports`, {
      method: "POST",
      headers: request.headers.get("cookie") ? { cookie: request.headers.get("cookie")! } : {},
      body,
      cache: "no-store",
    });
    const headers = new Headers();
    const contentType = upstream.headers.get("content-type");
    if (contentType) headers.set("content-type", contentType);
    return new Response(await upstream.arrayBuffer(), { status: upstream.status, headers });
  } catch (error) {
    console.error("Question-bank upload proxy failed", error);
    return Response.json({ detail: "The upload could not reach the import service. Please retry." }, { status: 502 });
  }
}
