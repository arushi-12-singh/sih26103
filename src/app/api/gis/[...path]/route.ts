/**
 * Server-side proxy to the FastAPI GIS API.
 *
 * Why a proxy rather than calling FastAPI straight from the browser: the GIS endpoints
 * require a bearer token, and anything shipped to the client via NEXT_PUBLIC_* is
 * readable by every visitor. Holding the token here keeps it server-only, which is the
 * backend-for-frontend pattern the Next.js docs describe. It also means the browser
 * talks to its own origin, so CORS never enters the picture.
 *
 * This is a pass-through, not a mock: it forwards to the real service and returns its
 * real status code and body untouched. Failures to reach FastAPI surface as 502 with a
 * message naming the URL that could not be reached, so a stopped backend is obvious
 * rather than looking like an application bug.
 *
 * Configure in .env.local:
 *   GIS_API_URL=http://127.0.0.1:8000        # optional, this is the default
 *   GIS_API_TOKEN=<token from backend/scripts/generate_api_tokens.py>
 */

import type { NextRequest } from "next/server";

const API_URL = process.env.GIS_API_URL ?? "http://127.0.0.1:8000";
const API_TOKEN = process.env.GIS_API_TOKEN;

function upstreamHeaders(contentType?: string): HeadersInit {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (contentType) headers["Content-Type"] = contentType;
  // When no token is configured we deliberately forward the request anyway: the backend
  // is fail-closed and answers with its own 401/503 explaining exactly how to fix it,
  // which is more useful than a message invented here.
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`;
  return headers;
}

async function forward(request: NextRequest, path: string[], body?: BodyInit) {
  const search = request.nextUrl.search;
  const target = `${API_URL}/api/v1/gis/${path.join("/")}${search}`;

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: upstreamHeaders(body === undefined ? undefined : "application/json"),
      body,
      cache: "no-store",
    });
  } catch (error) {
    return Response.json(
      {
        detail:
          `Cannot reach the GIS backend at ${API_URL}. Start it with ` +
          `\`uvicorn app.main:app --reload\` from the backend directory. ` +
          `(${error instanceof Error ? error.message : "network error"})`,
      },
      { status: 502 },
    );
  }

  // 204 carries no body; reading one would throw.
  if (upstream.status === 204) return new Response(null, { status: 204 });

  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
  });
}

export async function GET(request: NextRequest, context: RouteContext<"/api/gis/[...path]">) {
  const { path } = await context.params;
  return forward(request, path);
}

export async function POST(request: NextRequest, context: RouteContext<"/api/gis/[...path]">) {
  const { path } = await context.params;
  return forward(request, path, await request.text());
}

export async function DELETE(request: NextRequest, context: RouteContext<"/api/gis/[...path]">) {
  const { path } = await context.params;
  return forward(request, path);
}
