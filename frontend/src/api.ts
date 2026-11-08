export interface LoginSession {
  access_token: string;
  expires_in: number;
}

export interface Citation {
  evidence_id: string;
  title: string;
  version: string;
  sections: string[];
  clause_numbers: number[];
  source_format: string;
  locations: Record<string, number | null>[];
}

export interface Answer {
  status: "answer" | "partial_answer" | "clarify" | "insufficient_evidence" | "report_conflict" | "access_denied";
  answer: string;
  as_of_date: string;
  citations: Citation[];
  session_id: string;
  awaiting_clarification: boolean;
}

export interface Question {
  question: string;
  session_id: string;
  as_of_date?: string;
  new_question?: boolean;
}

export type Stage = "searching" | "generating" | "reviewing" | "retrying";
export type Progress =
  | { event: "phase"; data: { stage: Stage } }
  | { event: "draft_delta"; data: { text: string } }
  | { event: "draft_reset"; data: Record<string, never> };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function checkStatus(response: Response) {
  if (response.ok) return;
  const body = await response.json().catch(() => null);
  const detail = typeof body?.detail === "string" ? body.detail : "";
  throw new ApiError(response.status, detail);
}

export async function login(username: string, password: string): Promise<LoginSession> {
  const response = await fetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  await checkStatus(response);
  return response.json();
}

export async function ask(
  question: Question,
  token: string,
  signal: AbortSignal,
  onProgress: (progress: Progress) => void,
): Promise<Answer> {
  const response = await fetch("/rag/ask/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      Accept: "text/event-stream",
    },
    body: JSON.stringify(question),
    signal,
  });
  await checkStatus(response);
  if (!response.body || !response.headers.get("Content-Type")?.includes("text/event-stream")) {
    throw new Error("问答服务没有返回有效的流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      let boundary: RegExpExecArray | null;
      while ((boundary = /\r?\n\r?\n/.exec(buffer)) !== null) {
        const lines = buffer.slice(0, boundary.index).split(/\r?\n/);
        buffer = buffer.slice(boundary.index + boundary[0].length);
        const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim();
        const payload = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5)).join("\n");
        if (!event || !payload) continue;
        const data = JSON.parse(payload);
        if (event === "final") return data as Answer;
        if (event === "error") throw new ApiError(data.status, data.message);
        if (event === "phase" || event === "draft_delta" || event === "draft_reset") {
          onProgress({ event, data });
        }
      }
      if (done) throw new Error("连接已中断，未收到正式回答。");
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
