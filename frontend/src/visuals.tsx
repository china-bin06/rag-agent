import type { ReactNode } from "react";

const shapes = {
  plus: <path d="M12 5v14M5 12h14" />,
  arrow: <path d="M5 12h14m-6-6 6 6-6 6" />,
  send: <path d="M12 19V5m-6 6 6-6 6 6" />,
  chat: <path d="M20 11.5a7.5 7.5 0 0 1-7.5 7.5H5l1.5-3A7.5 7.5 0 1 1 20 11.5Z" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  calendar: <><rect x="4" y="6" width="16" height="15" rx="2" /><path d="M8 3v6m8-6v6M4 11h16" /></>,
  source: <><path d="M6 3h9l4 4v14H6zM14 3v5h5M9 12h7m-7 4h5" /></>,
  exit: <path d="M10 4H5v16h5m-1-8h11m-4-4 4 4-4 4" />,
  shield: <path d="M12 3 5 6v5c0 4 2.8 7.4 7 10 4.2-2.6 7-6 7-10V6zM9 12l2 2 4-4" />,
  eye: <><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z" /><circle cx="12" cy="12" r="2.5" /></>,
  eyeOff: <><path d="m3 3 18 18M10 6a11 11 0 0 1 2-.2c6.5 0 10 6.2 10 6.2a13 13 0 0 1-3 3.4M6.4 7.2A16 16 0 0 0 2 12s3.5 6 10 6a10 10 0 0 0 4-.8" /></>,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V4H4v12h4" /></>,
  briefcase: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7V4h8v3M3 12h18m-9 0v3" /></>,
  clock: <><circle cx="12" cy="12" r="8" /><path d="M12 7v5l3 2" /></>,
  box: <path d="m12 3 8 4v10l-8 4-8-4V7zM4 7l8 4 8-4m-8 4v10" />,
} satisfies Record<string, ReactNode>;

export function Icon({ name, size = 20 }: { name: keyof typeof shapes; size?: number }) {
  return <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{shapes[name]}</svg>;
}

export function NebulaMark({ className = "" }: { className?: string }) {
  return <span className={`nebula-mark ${className}`} aria-hidden="true">
    <svg viewBox="0 0 40 40" fill="none">
      <ellipse cx="20" cy="20" rx="14" ry="6.5" transform="rotate(-43 20 20)" stroke="currentColor" strokeWidth="1.65" />
      <path d="M20 11.5 22 18l6.5 2-6.5 2-2 6.5-2-6.5-6.5-2 6.5-2z" fill="currentColor" />
      <circle cx="30.5" cy="10.2" r="1.7" fill="currentColor" />
    </svg>
  </span>;
}

export function Brand() {
  return <div className="brand"><NebulaMark /><div><strong>星云科技</strong><span>企业制度智能问答平台</span></div></div>;
}

export function OrbitArt() {
  return <svg aria-hidden="true" className="orbit-art" viewBox="0 0 680 620" fill="none">
    <defs>
      <radialGradient id="orbit-core"><stop stopColor="#91adff" /><stop offset=".48" stopColor="#b3c8ff" /><stop offset="1" stopColor="#aac1ff" stopOpacity="0" /></radialGradient>
      <linearGradient id="orbit-ring"><stop stopColor="#5075ca" stopOpacity=".8" /><stop offset="1" stopColor="#7194e6" stopOpacity=".3" /></linearGradient>
    </defs>
    <circle cx="370" cy="305" r="161" fill="url(#orbit-core)" opacity=".86" />
    <g transform="rotate(-35 370 305)" stroke="url(#orbit-ring)">
      <ellipse cx="370" cy="305" rx="237" ry="82" />
      <ellipse cx="370" cy="305" rx="274" ry="111" />
      <ellipse cx="370" cy="305" rx="312" ry="142" />
      <ellipse cx="370" cy="305" rx="351" ry="175" opacity=".6" />
      <ellipse cx="370" cy="305" rx="386" ry="210" opacity=".35" />
      <circle cx="148" cy="276" r="5" fill="#7291e6" />
      <circle cx="570" cy="381" r="4" fill="#7797e9" />
      <circle cx="471" cy="153" r="2.5" fill="#688bdd" />
    </g>
    <path d="m370 261 9 35 35 9-35 9-9 35-9-35-35-9 35-9z" fill="#f0f5ff" />
    <path d="m516 109 3 11 11 3-11 3-3 11-3-11-11-3 11-3zM205 420l2 7 7 2-7 2-2 7-2-7-7-2 7-2z" fill="#8ba6ef" />
  </svg>;
}
