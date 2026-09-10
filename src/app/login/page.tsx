"use client";

import { FormEvent, useEffect, useState } from "react";
import { Activity, AlertTriangle, LockKeyhole, Mail } from "lucide-react";
import { useRouter } from "next/navigation";
import { DEMO_CREDENTIALS, isDemoAuthenticated, startDemoSession } from "@/lib/demo-auth";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (isDemoAuthenticated()) router.replace("/portfolio");
  }, [router]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (email.trim().toLowerCase() !== DEMO_CREDENTIALS.email || password !== DEMO_CREDENTIALS.password) {
      setError("The email or password is incorrect.");
      return;
    }
    startDemoSession();
    router.replace("/portfolio");
  }

  return <main className="login-shell"><section className="login-panel" aria-labelledby="login-title"><div className="login-brand"><span className="brand-mark"><Activity size={17} /></span><strong>PAIMANA</strong><small>INTELLIGENCE</small></div><div className="login-heading"><span className="eyebrow">SECURE WORKSPACE ACCESS</span><h1 id="login-title">Sign in to PAIMANA</h1><p>Access infrastructure project intelligence for your workspace.</p></div><form onSubmit={submit}><label htmlFor="login-email"><span>Email</span><div className="login-input"><Mail size={16} /><input id="login-email" type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required /></div></label><label htmlFor="login-password"><span>Password</span><div className="login-input"><LockKeyhole size={16} /><input id="login-password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required /></div></label>{error && <div className="login-error" role="alert"><AlertTriangle size={15} />{error}</div>}<button className="login-submit" type="submit">Sign in</button></form><p className="login-note">Demo access for the SIH presentation environment.</p></section></main>;
}
