import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { createRoot } from "react-dom/client";
import { ApiError, login } from "./api";
import { Brand, Icon, OrbitArt } from "./visuals";
import { Workspace } from "./workspace";
import "./style.css";

interface Identity {
  username: string;
  token: string;
  expiresAt: number;
}

function LoginPage({ onLogin, notice }: { onLogin: (identity: Identity) => void; notice: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !username.trim() || !password) return;
    setBusy(true);
    setError("");
    try {
      const session = await login(username.trim(), password);
      onLogin({
        username: username.trim(), token: session.access_token,
        expiresAt: Date.now() + session.expires_in * 1000,
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        setError("账号或密码有误，请核对后重试。");
      } else if (error instanceof ApiError && error.status === 503 && error.message === "登录服务未配置") {
        setError("登录服务尚未配置，请联系系统管理员。");
      } else {
        setError("暂时无法登录，请稍后重试。");
      }
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return <main className="login-layout">
    <section className="login-hero">
      <Brand />
      <OrbitArt />
      <div className="hero-copy">
        <span className="hero-tag">星云科技有限公司</span>
        <p>工作中的制度问题，<br />在这里查到依据。</p>
        <small>查询审批流程、适用条件和制度条款。</small>
      </div>
    </section>
    <section className="login-side">
      <form onSubmit={submit} className="login-form">
        <span className="login-tag"><Icon name="shield" size={16} /> 员工入口</span>
        <h1>企业制度智能问答平台</h1>
        <p>请使用你的公司账号登录。</p>
        <label htmlFor="username">用户名</label>
        <input id="username" autoComplete="username" required maxLength={255}
          value={username} onChange={(event) => setUsername(event.target.value)} placeholder="输入公司账号" />
        <label htmlFor="password">密码</label>
        <div className="password-field">
          <input id="password" autoComplete="current-password" required type={visible ? "text" : "password"}
            value={password} onChange={(event) => setPassword(event.target.value)} placeholder="输入账号密码" />
          <button type="button" className="icon-button" aria-label={visible ? "隐藏密码" : "显示密码"}
            onClick={() => setVisible(!visible)}><Icon name={visible ? "eyeOff" : "eye"} size={19} /></button>
        </div>
        {(error || notice) && <p className="form-error" role="alert">{error || notice}</p>}
        <button className="primary login-submit" disabled={busy || !username.trim() || !password}>
          {busy ? "正在登录…" : "登录"} <Icon name="arrow" size={19} />
        </button>
      </form>
      <p className="login-footer">星云科技有限公司 <span>·</span> 企业制度智能问答平台</p>
    </section>
  </main>;
}

function App() {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [notice, setNotice] = useState("");
  const signOut = useCallback((expired = false) => {
    setIdentity(null);
    setNotice(expired ? "登录已失效，请重新登录。" : "");
  }, []);

  useEffect(() => {
    if (!identity) return;
    const timer = window.setTimeout(() => signOut(true), Math.max(0, identity.expiresAt - Date.now()));
    return () => clearTimeout(timer);
  }, [identity, signOut]);

  return identity
    ? <Workspace username={identity.username} token={identity.token} onSignOut={signOut} />
    : <LoginPage notice={notice} onLogin={(session) => { setNotice(""); setIdentity(session); }} />;
}

createRoot(document.getElementById("root")!).render(<App />);
