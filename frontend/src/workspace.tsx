import { Fragment, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ApiError, ask } from "./api";
import type { Answer, Citation, Question, Stage } from "./api";
import { Brand, Icon, NebulaMark } from "./visuals";

interface Failure {
  message: string;
  uncertain: boolean;
  edit?: boolean;
}

interface Turn {
  id: string;
  payload: Question;
  followup: boolean;
  stage: Stage;
  draft: string;
  revision: number;
  result?: Answer;
  failure?: Failure;
}

interface Conversation {
  id: string;
  turns: Turn[];
}

interface EvidenceView {
  turnId: string;
  citations: Citation[];
  selected: string;
}

const examples = [
  { icon: "briefcase", label: "差旅报销", question: "员工出差报销需要准备哪些材料？" },
  { icon: "clock", label: "请假审批", question: "连续请假应该由谁审批？" },
  { icon: "box", label: "采购流程", question: "采购需要向几家供应商询价？" },
] as const;

const stages: Record<Stage, string> = {
  searching: "正在查询制度",
  generating: "正在生成回答",
  reviewing: "正在复核依据",
  retrying: "正在重新生成",
};

const statuses: Record<Answer["status"], string> = {
  answer: "回答完成",
  partial_answer: "部分回答",
  clarify: "需要补充条件",
  insufficient_evidence: "依据不足",
  report_conflict: "制度口径存在差异",
  access_denied: "无权访问",
};

function emptyConversation(): Conversation {
  return { id: crypto.randomUUID(), turns: [] };
}

function failureMessage(error: unknown): Failure {
  if (!(error instanceof ApiError)) {
    return { message: "连接中断，处理结果未确认。可以带上完整问题重新发送。", uncertain: true };
  }
  const messages: Record<number, string> = {
    409: "上一条消息仍在处理，请稍后重试。",
    422: "请检查问题内容和查询日期，再重新发送。",
    502: "模型服务暂时不可用，请稍后重试。",
    503: "检索服务暂时不可用，请稍后重试。",
  };
  return {
    message: error.status === 422 && error.message ? error.message : messages[error.status] || "问答服务暂时不可用。",
    uncertain: false,
    edit: error.status === 422,
  };
}

function DraftText({ text }: { text: string }) {
  const [length, setLength] = useState(0);
  const characters = Array.from(text);
  const size = useRef(characters.length);
  useEffect(() => {
    size.current = characters.length;
  }, [characters.length]);
  useEffect(() => {
    const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const timer = window.setInterval(() => {
      setLength((previous) => motion.matches ? size.current
        : Math.min(size.current, previous + Math.max(1, Math.ceil((size.current - previous) / 8))));
    }, 24);
    return () => clearInterval(timer);
  }, []);
  return <div className="answer-text draft-text" aria-hidden="true">{characters.slice(0, length).join("")}<span className="cursor" /></div>;
}

function locations(citation: Citation) {
  const range = (start: number, end: number | null | undefined) => end && end !== start ? `${start}–${end}` : start;
  return [...new Set(citation.locations.flatMap((item) => {
    if (item.page_start != null) return [`第 ${range(item.page_start, item.page_end)} 页`];
    if (item.line_start != null) return [`第 ${range(item.line_start, item.line_end)} 行`];
    if (item.paragraph_index != null) return [`第 ${item.paragraph_index} 段`];
    if (item.table_index != null) return [`第 ${item.table_index} 张表`];
    return [];
  }))];
}

function CitationPanel({ evidence, close }: { evidence: EvidenceView; close: () => void }) {
  const [compact, setCompact] = useState(() => matchMedia("(max-width: 1100px)").matches);
  const dialog = useRef<HTMLDialogElement>(null);
  const selected = useRef<HTMLElement>(null);
  useEffect(() => {
    const media = matchMedia("(max-width: 1100px)");
    const change = () => setCompact(media.matches);
    media.addEventListener("change", change);
    return () => media.removeEventListener("change", change);
  }, []);
  useEffect(() => {
    if (compact) dialog.current?.showModal();
  }, [compact]);
  useEffect(() => {
    selected.current?.scrollIntoView({ block: "nearest" });
  }, [compact, evidence]);

  const content = <>
    <header className="evidence-header">
      <div className="evidence-heading"><span className="source-icon"><Icon name="source" size={19} /></span>
        <div><h2 id="evidence-title">制度依据</h2><p>本轮回答引用 {evidence.citations.length} 处</p></div>
      </div>
      <button className="icon-button" onClick={close} aria-label="关闭制度依据"><Icon name="close" /></button>
    </header>
    <div className="evidence-list">
      {evidence.citations.map((citation) => {
        const position = locations(citation);
        const active = citation.evidence_id === evidence.selected;
        return <article key={citation.evidence_id} className={`citation-card ${active ? "selected" : ""}`} ref={active ? selected : undefined}>
          <div className="citation-meta"><span>{citation.evidence_id}</span><span>{citation.source_format.toUpperCase()} · 版本 {citation.version}</span></div>
          <h3>{citation.title}</h3>
          <dl>
            {citation.sections.length > 0 && <><dt>章节</dt><dd>{citation.sections.join(" / ")}</dd></>}
            {citation.clause_numbers.length > 0 && <><dt>条款</dt><dd>{citation.clause_numbers.map((number) => `第 ${number} 条`).join("、")}</dd></>}
            {position.length > 0 && <><dt>位置</dt><dd>{position.join("、")}</dd></>}
          </dl>
        </article>;
      })}
    </div>
  </>;

  return compact
    ? <dialog ref={dialog} className="evidence-dialog" aria-labelledby="evidence-title"
        onCancel={(event) => { event.preventDefault(); close(); }}
        onClick={(event) => { if (event.currentTarget === event.target) close(); }}>
        <div className="evidence-sheet">{content}</div>
      </dialog>
    : <aside className="evidence-sidebar" aria-labelledby="evidence-title">{content}</aside>;
}

function FinalAnswer({ result, view }: { result: Answer; view: (id: string, button: HTMLElement) => void }) {
  const [copied, setCopied] = useState("");
  const ids = new Set(result.citations.map((citation) => citation.evidence_id));

  return <>
    <div className="answer-meta">
      <span className={`status status-${result.status}`}>{statuses[result.status]}</span>
      <span>适用日期 {result.as_of_date}</span>
    </div>
    <div className="answer-text">{result.answer.split(/(\[E\d+\])/g).map((part, index) => {
      const id = part.slice(1, -1);
      return /^\[E\d+\]$/.test(part) && ids.has(id)
        ? <button key={index} className="inline-citation" aria-label={`查看制度依据 ${id}`}
            onClick={(event) => view(id, event.currentTarget)}>{id}</button>
        : <Fragment key={index}>{part}</Fragment>;
    })}</div>
    {result.status === "clarify" && !result.awaiting_clarification &&
      <p className="answer-hint">发送下一条消息时，请把原问题和补充条件一起写上。</p>}
    <div className="answer-tools">
      {result.citations.length > 0 && <button className="text-button" onClick={(event) => view(result.citations[0].evidence_id, event.currentTarget)}>
        <Icon name="source" size={16} /> 查看依据 <span className="count">{result.citations.length}</span>
      </button>}
      <button className="text-button" onClick={async () => {
        try {
          await navigator.clipboard.writeText(result.answer);
          setCopied("已复制");
        } catch {
          setCopied("复制失败，可手动选择正文");
        }
      }}><Icon name="copy" size={16} /> 复制</button>
      <span role="status">{copied}</span>
    </div>
  </>;
}

export function Workspace({ username, token, onSignOut }: {
  username: string; token: string; onSignOut: (expired?: boolean) => void;
}) {
  const [initial] = useState(emptyConversation);
  const [conversations, setConversations] = useState([initial]);
  const [activeId, setActiveId] = useState(initial.id);
  const [input, setInput] = useState("");
  const [date, setDate] = useState("");
  const [showDate, setShowDate] = useState(false);
  const [menu, setMenu] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceView | null>(null);
  const evidenceTrigger = useRef<HTMLElement>(null);
  const pending = useRef(new Map<string, AbortController>());
  const textarea = useRef<HTMLTextAreaElement>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const navAction = useRef<HTMLButtonElement>(null);
  const followScroll = useRef(true);
  const conversation = conversations.find((item) => item.id === activeId)!;
  const latest = conversation.turns.at(-1);
  const latestResult = conversation.turns.findLast((turn) => turn.result)?.result;
  const busy = !!latest && !latest.result && !latest.failure;
  const awaiting = !!latestResult?.awaiting_clarification && !latest?.failure?.uncertain;
  const history = conversations.filter((item) => item.turns.length > 0).toReversed();
  const inputLength = Array.from(input.trim()).length;

  useEffect(() => {
    const requests = pending.current;
    return () => requests.forEach((controller) => controller.abort());
  }, []);

  useEffect(() => {
    if (!menu) return;
    navAction.current?.focus();
    const media = matchMedia("(max-width: 760px)");
    const onResize = () => { if (!media.matches) setMenu(false); };
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenu(false);
        requestAnimationFrame(() => menuButton.current?.focus());
      }
    };
    window.addEventListener("keydown", close);
    media.addEventListener("change", onResize);
    return () => {
      window.removeEventListener("keydown", close);
      media.removeEventListener("change", onResize);
    };
  }, [menu]);

  useEffect(() => {
    const container = scroll.current;
    if (!container) return;
    const body = container.firstElementChild;
    if (!body) return;
    const observer = new ResizeObserver(() => {
      if (followScroll.current) container.scrollTop = container.scrollHeight;
    });
    observer.observe(body);
    return () => observer.disconnect();
  }, [activeId]);

  function closeEvidence() {
    setEvidence(null);
    const trigger = evidenceTrigger.current;
    requestAnimationFrame(() => trigger?.focus());
  }

  function select(id: string) {
    followScroll.current = true;
    setActiveId(id);
    setInput("");
    setDate("");
    setShowDate(false);
    setMenu(false);
    setEvidence(null);
  }

  function startNew() {
    if (conversation.turns.length > 0) {
      const next = emptyConversation();
      setConversations((previous) => [...previous.filter((item) => item.turns.length > 0), next]);
      select(next.id);
    } else {
      select(activeId);
    }
    requestAnimationFrame(() => textarea.current?.focus());
  }

  async function send(payload: Question, followup: boolean, replaceId?: string) {
    if (pending.current.has(activeId)) return;
    const controller = new AbortController();
    const turn: Turn = { id: crypto.randomUUID(), payload, followup, stage: "searching", draft: "", revision: 0 };
    const updateTurns = (update: (turns: Turn[]) => Turn[]) => setConversations((previous) =>
      previous.map((item) => item.id === activeId ? { ...item, turns: update(item.turns) } : item));
    const updateTurn = (update: (current: Turn) => Turn) =>
      updateTurns((turns) => turns.map((item) => item.id === turn.id ? update(item) : item));
    pending.current.set(activeId, controller);
    followScroll.current = true;
    setInput("");
    updateTurns((turns) => [...turns.filter((item) => item.id !== replaceId), turn]);

    try {
      const result = await ask(payload, token, controller.signal, (event) => {
        if (controller.signal.aborted) return;
        updateTurn((current) => {
          if (event.event === "draft_delta") {
            return { ...current, draft: current.draft + event.data.text, stage: "generating" };
          }
          if (event.event === "draft_reset") {
            return { ...current, draft: "", stage: "retrying", revision: current.revision + 1 };
          }
          return { ...current, stage: event.data.stage };
        });
      });
      if (!controller.signal.aborted) updateTurn((current) => ({ ...current, draft: "", result }));
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.status === 401) {
        onSignOut(true);
        return;
      }
      updateTurn((current) => ({ ...current, draft: "", failure: failureMessage(error) }));
    } finally {
      pending.current.delete(activeId);
    }
  }

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!inputLength || inputLength > 1000 || busy) return;
    void send({
      question: input.trim(), session_id: activeId,
      ...(!awaiting && date ? { as_of_date: date } : {}),
      ...(latest?.failure?.uncertain ? { new_question: true } : {}),
    }, awaiting);
  }

  function retry(turn: Turn) {
    if (turn.failure?.edit) {
      setInput(turn.payload.question);
      setDate(turn.payload.as_of_date ?? "");
      setShowDate(true);
      textarea.current?.focus();
      return;
    }
    if (!turn.failure?.uncertain) {
      void send(turn.payload, turn.followup, turn.id);
      return;
    }
    const first = conversation.turns.findLastIndex((item) => !item.followup);
    const parts = conversation.turns.slice(Math.max(0, first)).filter((item) => !item.failure || item === turn);
    const question = parts.map((item, index) => index ? `补充条件：${item.payload.question}` : item.payload.question).join("\n");
    setInput(question);
    setDate(parts[0]?.payload.as_of_date ?? "");
    setShowDate(!!parts[0]?.payload.as_of_date);
    textarea.current?.focus();
  }

  function showEvidence(turn: Turn, id: string, trigger: HTMLElement) {
    if (!turn.result) return;
    evidenceTrigger.current = trigger;
    setEvidence({ turnId: turn.id, citations: turn.result.citations, selected: id });
  }

  const lockedDate = awaiting ? latestResult?.as_of_date : undefined;

  return <div className="workspace">
    {menu && <button className="nav-backdrop" onClick={() => {
      setMenu(false);
      requestAnimationFrame(() => menuButton.current?.focus());
    }} aria-label="关闭菜单" />}
    <aside className={`navigation ${menu ? "open" : ""}`}>
      <Brand />
      <button className="new-question" ref={navAction} onClick={startNew}><Icon name="plus" size={18} /> 新问题</button>
      <nav className="history" aria-label="本页问答记录">
        <h2>本页问答记录</h2>
        {history.length === 0 && <p>开始提问后，记录会显示在这里。</p>}
        {history.map((item) => <button key={item.id} title={item.turns[0].payload.question} aria-current={item.id === activeId ? "true" : undefined}
          onClick={() => {
            select(item.id);
            if (menu) requestAnimationFrame(() => menuButton.current?.focus());
          }}><Icon name="chat" size={17} /><span>{item.turns[0].payload.question}</span></button>)}
      </nav>
      <div className="account">
        <span className="avatar" aria-hidden="true">{Array.from(username)[0]?.toUpperCase()}</span>
        <div className="account-name"><strong>{username}</strong><small>公司账号</small></div>
        <button className="icon-button" aria-label="退出登录" title="退出登录" onClick={() => onSignOut()}><Icon name="exit" size={19} /></button>
      </div>
    </aside>
    <main className="desk" inert={menu}>
      <header className="desk-header">
        <button ref={menuButton} className="icon-button mobile-menu" aria-label="打开菜单" aria-expanded={menu} onClick={() => setMenu(true)}><Icon name="menu" /></button>
        <div className="desk-title"><h1>制度问答</h1><span>星云科技有限公司</span></div>
        <button className="icon-button mobile-new" aria-label="新问题" onClick={startNew}><Icon name="plus" /></button>
      </header>
      <div ref={scroll} className="desk-scroll" onScroll={(event) => {
        const element = event.currentTarget;
        followScroll.current = element.scrollHeight - element.clientHeight - element.scrollTop < 100;
      }}>
        {conversation.turns.length === 0 ? <section className="welcome">
          <div className="welcome-orbit"><NebulaMark /></div>
          <h2>今天想查什么制度？</h2>
          <p>了解流程、核对条款，找到适用于你的制度依据。</p>
          <div className="examples" aria-label="常用问题">
            {examples.map((example) => <button key={example.label} onClick={() => {
              setInput(example.question);
              textarea.current?.focus();
            }}>
              <span className="example-icon"><Icon name={example.icon} size={19} /></span>
              <span className="example-copy"><strong>{example.label}</strong><span>{example.question}</span></span>
              <Icon name="arrow" size={16} />
            </button>)}
          </div>
        </section> : <section className="thread" aria-label="问答对话">
          {conversation.turns.map((turn) => <article className="turn" key={turn.id}>
            <div className="question-bubble">{turn.payload.question}</div>
            <div className="answer-row">
              <NebulaMark />
              <div className="answer-card">
                <h2>制度助手</h2>
                {turn.result ? <FinalAnswer result={turn.result} view={(id, button) => showEvidence(turn, id, button)} />
                  : turn.failure ? <div className="turn-error">
                    <p role="alert">{turn.failure.message}</p>
                    {turn.id === latest?.id && <button className="text-button" onClick={() => retry(turn)}>
                      {turn.failure.uncertain || turn.failure.edit ? "编辑后重发" : "重试"}
                    </button>}
                  </div>
                  : <>
                    <p className="progress" role="status"><span className="loading-dot" />{stages[turn.stage]}{turn.draft && " · 草稿"}</p>
                    {turn.draft && <DraftText key={turn.revision} text={turn.draft} />}
                  </>}
              </div>
            </div>
          </article>)}
        </section>}
      </div>
      <div className="composer-area">
        <form className="composer" onSubmit={submit}>
          {awaiting && <p className="followup">请补充上方提示中的条件。查询日期沿用 {lockedDate}。</p>}
          <textarea ref={textarea} aria-label="输入制度问题" rows={2} aria-invalid={inputLength > 1000}
            placeholder={awaiting ? "输入补充条件…" : "输入你的制度问题…"}
            value={input} onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229) {
                event.preventDefault();
                submit();
              }
            }} />
          {showDate && !awaiting && <label className="date-row">
            <span>查询日期</span><input type="date" value={date} onChange={(event) => setDate(event.target.value)} />
            {date && <button type="button" className="text-button" onClick={() => setDate("")}>清除</button>}
          </label>}
          <div className="composer-tools">
            <button type="button" className="text-button date-toggle" aria-expanded={showDate && !awaiting} disabled={awaiting}
              onClick={() => setShowDate(!showDate)}><Icon name="calendar" size={17} />{lockedDate || date || "查询日期"}</button>
            <span className={`composer-count ${inputLength > 1000 ? "over-limit" : ""}`}>{inputLength} / 1000</span>
            <button className="primary send-button" aria-label="发送问题" disabled={busy || !inputLength || inputLength > 1000}>
              <Icon name="send" size={20} />
            </button>
          </div>
        </form>
        <p className="composer-note"><Icon name="shield" size={14} /> 问答依据你有权限查看的公司制度</p>
      </div>
    </main>
    {evidence && <CitationPanel key={evidence.turnId} evidence={evidence} close={closeEvidence} />}
  </div>;
}
