import React, { useEffect, useState } from 'react';
import {
  BrainCircuit,
  Loader2,
  Lock,
  Target,
  User as UserIcon,
} from 'lucide-react';
import { AUTH_API_BASE, readJsonResponse } from '../utils/api';
import './AuthPage.css';

const authServiceUnavailableMessage = '认证服务尚未连接，请先启动后端服务后重试。';

const AuthPage = ({ onLogin, onBack }) => {
  const [mode, setMode] = useState('login');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [authServiceStatus, setAuthServiceStatus] = useState('checking');
  const [formData, setFormData] = useState({
    username: '',
    displayName: '',
    password: '',
  });

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);

    fetch('/health', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('health check failed');
        if (active) setAuthServiceStatus('ready');
      })
      .catch(() => {
        if (active) setAuthServiceStatus('unavailable');
      })
      .finally(() => window.clearTimeout(timeout));

    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, []);

  const switchMode = (nextMode) => {
    setMode(nextMode);
    setError('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');

    try {
      const endpoint = mode === 'login' ? 'login' : 'register';
      const payload = {
        username: formData.username,
        password: formData.password,
        ...(mode === 'register' && formData.displayName.trim()
          ? { display_name: formData.displayName.trim() }
          : {}),
      };
      const res = await fetch(`${AUTH_API_BASE}/${endpoint}`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await readJsonResponse(res, {});
      if (!res.ok) {
        const detail = typeof data.detail === 'string' ? data.detail.trim() : '';
        if (res.status >= 500 && !detail) throw new Error(authServiceUnavailableMessage);
        throw new Error(detail || (mode === 'login' ? '登录失败' : '注册失败'));
      }
      if (!data.user) throw new Error('登录响应缺少用户信息');
      onLogin(data.user);
    } catch (err) {
      const isNetworkError = err instanceof TypeError || err?.name === 'AbortError';
      setError(
        isNetworkError
          ? authServiceUnavailableMessage
          : err.message || '登录失败，请稍后重试。',
      );
    } finally {
      setLoading(false);
    }
  };

  const title = mode === 'login' ? '进入学习工作台' : '创建学习账号';
  const description =
    mode === 'login'
      ? '登录后进入时珍智训首页，继续使用培训助手、知识库溯源、练习批改与学情规划。'
      : '创建账号后将直接登录并进入时珍智训首页。';

  return (
    <div className="auth-page auth-page--single-screen relative min-h-screen overflow-hidden text-slate-900" style={{backgroundImage: 'url(/design-images/home/login-bg.png)', backgroundSize: 'cover', backgroundPosition: 'center', backgroundRepeat: 'no-repeat'}}>
      {/* 白色蒙版遮罩层 */}
      <div className="pointer-events-none absolute inset-0 bg-white/20" />

      <div className="auth-page__frame relative z-10">
        <div className="auth-page__glow auth-page__glow--top" />
        <div className="auth-page__glow auth-page__glow--bottom" />

        <main className="relative mx-auto h-full max-w-[1440px] px-5 sm:px-8 lg:px-12">
          {onBack && (
            <button
              type="button"
              className="absolute left-5 top-5 z-20 rounded-full bg-white/90 px-4 py-2 text-sm font-medium text-emerald-800 shadow-sm transition hover:bg-white sm:left-8 sm:top-8 lg:left-12 lg:top-10"
              onClick={onBack}
            >
              返回首页
            </button>
          )}
          <section className="auth-showcase grid h-full min-h-0 items-center gap-12 py-8 sm:py-10 lg:grid-cols-[minmax(0,1.03fr)_minmax(380px,0.97fr)] lg:gap-16 lg:py-10">
            <div className="auth-showcase__content max-w-3xl text-center lg:text-left">
              <h1 className="text-6xl font-black leading-[1.4] tracking-[-0.055em] text-emerald-950 sm:text-7xl lg:text-8xl">承时珍医脉<br /><span className="auth-title-gradient">启智慧学习</span></h1>
              <p className="mt-10 max-w-2xl text-lg leading-9 text-slate-600 sm:text-xl lg:text-2xl">融合中医药经典智慧与智能学习技术，构建可理解、可追踪、可持续的个性化学习工作台，陪伴每一位学习者循证精进。</p>
              <div className="mt-10 flex flex-wrap gap-4">
                <div className="inline-flex items-center gap-2.5 rounded-full bg-white px-5 py-2.5 shadow-sm">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#059669" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="10" />
                    <polyline points="8 12 11 15 16 9" />
                  </svg>
                  <span className="text-sm font-medium text-slate-700">学习画像驱动推荐</span>
                </div>
                <div className="inline-flex items-center gap-2.5 rounded-full bg-white px-5 py-2.5 shadow-sm">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#059669" strokeWidth="2.5" strokeLinecap="round">
                    <line x1="6" y1="18" x2="6" y2="10" />
                    <line x1="12" y1="18" x2="12" y2="4" />
                    <line x1="18" y1="18" x2="18" y2="14" />
                  </svg>
                  <span className="text-sm font-medium text-slate-700">资料来源可追踪</span>
                </div>
              </div>
            </div>

            <div className="auth-login-card relative w-full rounded-[2rem] border border-white/95 bg-white/92 p-6 shadow-2xl shadow-emerald-200/70 sm:p-8">
              <div className="flex items-center gap-3"><div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-600 text-white shadow-lg shadow-emerald-200"><BrainCircuit size={24} /></div><div><div className="text-sm font-bold text-emerald-700">时珍智训</div><div className="text-xs text-slate-500">进入你的学习工作台</div></div></div>
              <div className="mt-6"><h2 className="text-2xl font-bold tracking-tight text-emerald-950">{title}</h2><p className="mt-2 text-sm leading-6 text-slate-600">{description}</p></div>

              {authServiceStatus === 'unavailable' && (
                <div className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800" role="status">
                  {authServiceUnavailableMessage}
                </div>
              )}

              <form onSubmit={handleSubmit} className="mt-6 space-y-4">
                <div>
                  <label htmlFor="auth-username" className="mb-1 block text-sm font-medium text-slate-700">{mode === 'login' ? '账号' : '用户名'}</label>
                  <div className="relative">
                    <UserIcon className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      id="auth-username"
                      name="username"
                      autoComplete="username"
                      minLength={mode === 'register' ? 3 : undefined}
                      value={formData.username}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-emerald-100 bg-emerald-50/45 py-3 pl-10 pr-4 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder={mode === 'login' ? '用户名或邮箱' : '设置学习账号名称'}
                      required
                    />
                  </div>
                </div>
                {mode === 'register' && <div>
                  <label htmlFor="auth-display-name" className="mb-1 block text-sm font-medium text-slate-700">显示名（可选）</label>
                  <div className="relative">
                    <UserIcon className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      id="auth-display-name"
                      name="displayName"
                      autoComplete="name"
                      value={formData.displayName}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-emerald-100 bg-emerald-50/45 py-3 pl-10 pr-4 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder="例如：林同学"
                    />
                  </div>
                </div>}
                <div>
                  <label htmlFor="auth-password" className="mb-1 block text-sm font-medium text-slate-700">密码</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      id="auth-password"
                      name="password"
                      type="password"
                      autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                      minLength={mode === 'register' ? 8 : undefined}
                      value={formData.password}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-emerald-100 bg-emerald-50/45 py-3 pl-10 pr-4 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder={mode === 'login' ? '输入登录密码' : '至少 8 位'}
                      required
                    />
                  </div>
                </div>
                {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
                <button type="submit" disabled={loading} className="auth-primary-button flex w-full items-center justify-center rounded-2xl px-4 py-3 text-sm font-semibold text-white transition hover:-translate-y-0.5 disabled:opacity-50">{loading ? <Loader2 className="animate-spin" size={20} /> : mode === 'login' ? '进入时珍智训' : '提交'}</button>
              </form>
              <div className="mt-6 text-center text-sm text-slate-600">
                {mode === 'login' ? <>还没有账号？ <button type="button" onClick={() => switchMode('register')} className="font-medium text-emerald-700 hover:text-emerald-800">创建学习账号</button></> : <>已有账号？ <button type="button" onClick={() => switchMode('login')} className="font-medium text-emerald-700 hover:text-emerald-800">返回登录</button></>}
              </div>
            </div>
          </section>
        </main>
      </div>
    </div>
  );
};

export default AuthPage;
