import React, { useEffect, useState } from 'react';
import {
  ArrowRight,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  Library,
  LineChart,
  Loader2,
  Lock,
  Sparkles,
  Target,
  User as UserIcon,
} from 'lucide-react';
import { AUTH_API_BASE, readJsonResponse } from '../utils/api';
import './AuthPage.css';

const capabilityCards = [
  { icon: BrainCircuit, title: 'AI 学习规划', description: '结合学习画像与阶段信号，生成清晰、可执行的进阶路径。' },
  { icon: Library, title: '本草知识溯源', description: '连接经典教材与个人资料，保留每一次学习检索的来源线索。' },
  { icon: Target, title: '训练反馈闭环', description: '把练习、错因和复盘建议沉淀到后续任务，持续看见进步。' },
];

const authServiceUnavailableMessage = '认证服务尚未连接，请先启动后端服务后重试。';

const AuthVisual = () => (
  <div className="auth-visual relative mx-auto flex aspect-square w-full max-w-[560px] items-center justify-center">
    <div className="auth-visual__orb" />
    <div className="auth-visual__ring auth-visual__ring--outer" />
    <div className="auth-visual__ring auth-visual__ring--inner" />
    <div className="auth-visual__core relative flex h-48 w-48 items-center justify-center overflow-hidden rounded-[3.25rem] border border-white/90 bg-white/72 text-emerald-600 shadow-2xl shadow-emerald-200/80 backdrop-blur-xl sm:h-60 sm:w-60">
      <img src="/design-images/login-hero.png" alt="中医药在线学习场景" className="h-full w-full object-cover" />
      <div className="auth-visual__image-shade absolute inset-0" />
      <div className="absolute inset-x-4 bottom-5 text-center text-xs font-bold tracking-[0.2em] text-white">本草 · 智学</div>
    </div>
    <div className="auth-float-card auth-float-card--top rounded-2xl border border-white/90 bg-white/88 px-4 py-3 shadow-xl shadow-emerald-200/50 backdrop-blur-xl">
      <div className="flex items-center gap-2 text-sm font-bold text-emerald-950"><Sparkles size={16} className="text-amber-500" /> 智能拆解知识</div>
      <div className="mt-1 text-xs text-slate-500">从经典到重点，一目了然</div>
    </div>
    <div className="auth-float-card auth-float-card--bottom rounded-2xl border border-white/90 bg-white/88 px-4 py-3 shadow-xl shadow-emerald-200/50 backdrop-blur-xl">
      <div className="flex items-center gap-2 text-sm font-bold text-emerald-950"><LineChart size={16} className="text-emerald-600" /> 进步持续可见</div>
      <div className="mt-1 text-xs text-slate-500">任务、练习与反馈形成闭环</div>
    </div>
  </div>
);

const AuthPage = ({ onLogin }) => {
  const [mode, setMode] = useState('login');
  const [showAuth, setShowAuth] = useState(false);
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

  const openAuth = (nextMode) => {
    setMode(nextMode);
    setError('');
    setShowAuth(true);
  };

  const closeAuth = () => {
    setError('');
    setMode('login');
    setShowAuth(false);
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
      : '注册后即可建立学习画像，开始沉淀知识来源、练习记录与阶段任务。';

  return (
    <div className="auth-page min-h-screen overflow-hidden bg-[#f4fbf7] text-slate-900">
      <div className="auth-page__glow auth-page__glow--top" />
      <div className="auth-page__glow auth-page__glow--bottom" />
      <header className="auth-nav border-b border-emerald-100/80 bg-white/72 px-5 py-4 backdrop-blur-xl sm:px-8 lg:px-12">
        <div className="mx-auto flex max-w-[1440px] items-center justify-between gap-5">
          <div className="flex items-center gap-3">
            <div className="auth-brand-mark flex h-11 w-11 items-center justify-center rounded-2xl text-white shadow-lg shadow-emerald-200/80"><BookOpen size={23} strokeWidth={2.25} /></div>
            <div className="leading-none">
              <div className="auth-brand-name text-xl font-black tracking-tight sm:text-2xl">时珍智训</div>
              <div className="mt-1 text-[9px] font-bold uppercase tracking-[0.24em] text-emerald-600">Shizhen AI · TCM</div>
            </div>
          </div>
          <nav className="hidden items-center gap-8 text-sm font-semibold text-emerald-950/65 lg:flex">
            <a href="#capabilities" className="auth-nav-link">学习路径</a>
            <a href="#capabilities" className="auth-nav-link">本草知识库</a>
            <a href="#capabilities" className="auth-nav-link">AI 学习助手</a>
          </nav>
          <div className="flex items-center gap-3">
            <button type="button" onClick={() => openAuth('login')} className="text-sm font-bold text-emerald-900/65 transition hover:text-emerald-700">登录</button>
            <button type="button" onClick={() => openAuth('register')} className="auth-cta rounded-full px-4 py-2.5 text-sm font-bold text-white shadow-lg shadow-emerald-200/80 transition hover:-translate-y-0.5 sm:px-6">开始学习</button>
          </div>
        </div>
      </header>

      <main className="relative mx-auto max-w-[1440px] px-5 sm:px-8 lg:px-12">
        <section className="auth-showcase grid min-h-[calc(100vh-76px)] items-center gap-12 py-16 lg:grid-cols-[minmax(0,1.03fr)_minmax(380px,0.97fr)] lg:gap-16 lg:py-24">
          <div className="auth-showcase__content max-w-3xl text-center lg:text-left">
            <div className="auth-kicker inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-white/75 px-4 py-2 text-xs font-bold tracking-[0.12em] text-emerald-700 shadow-sm shadow-emerald-100">
              <span className="relative flex h-2.5 w-2.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" /><span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-emerald-500" /></span>
              AI 赋能 · 时珍精粹
            </div>
            <h1 className="mt-7 text-5xl font-black leading-[1.08] tracking-[-0.055em] text-emerald-950 sm:text-6xl lg:text-8xl">承时珍医脉<br /><span className="auth-title-gradient">启智慧学习</span></h1>
            <p className="mt-7 max-w-2xl text-base leading-8 text-slate-600 sm:text-lg lg:text-xl">融合中医药经典智慧与智能学习技术，构建可理解、可追踪、可持续的个性化学习工作台，陪伴每一位学习者循证精进。</p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3 text-sm font-semibold text-emerald-950/70 lg:justify-start">
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-100 bg-white/70 px-4 py-2.5 shadow-sm shadow-emerald-100/60"><CheckCircle2 size={16} className="text-emerald-600" /> 学习画像驱动推荐</span>
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-100 bg-white/70 px-4 py-2.5 shadow-sm shadow-emerald-100/60"><Library size={16} className="text-emerald-600" /> 资料来源可追踪</span>
            </div>
            <div className="mt-10 flex flex-wrap items-center justify-center gap-4 lg:justify-start">
              <button type="button" onClick={() => openAuth('register')} className="auth-primary-button group inline-flex items-center gap-3 rounded-2xl px-6 py-3.5 text-sm font-bold text-white shadow-xl shadow-emerald-200/80 transition hover:-translate-y-0.5">开启智训之旅 <ArrowRight size={18} className="transition group-hover:translate-x-1" /></button>
              <button type="button" onClick={() => openAuth('login')} className="rounded-2xl border border-emerald-200 bg-white/75 px-6 py-3.5 text-sm font-bold text-emerald-800 shadow-sm shadow-emerald-100 transition hover:-translate-y-0.5 hover:border-emerald-300 hover:bg-white">登录已有账号</button>
            </div>
          </div>
          <AuthVisual />
        </section>

        <section id="capabilities" className="pb-16 pt-4 lg:pb-24">
          <div className="mx-auto max-w-3xl text-center"><div className="text-xs font-bold tracking-[0.18em] text-emerald-600">ONE PLATFORM · COMPLETE LEARNING LOOP</div><h2 className="mt-3 text-3xl font-black tracking-tight text-emerald-950 sm:text-4xl">以智能重塑<span className="auth-title-gradient">本草学习</span></h2><p className="mt-4 text-base leading-7 text-slate-600">让经典知识更易理解，让每一次练习都成为下一步成长的依据。</p></div>
          <div className="mt-10 grid gap-5 md:grid-cols-3">
            {capabilityCards.map((item) => {
              const Icon = item.icon;
              return <article key={item.title} className="auth-capability-card rounded-[2rem] border border-white/90 bg-white/72 p-6 shadow-lg shadow-emerald-100/55 backdrop-blur-sm"><div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-emerald-100 bg-emerald-50 text-emerald-600"><Icon size={26} /></div><h2 className="mt-5 text-xl font-bold text-emerald-950">{item.title}</h2><p className="mt-3 text-sm leading-7 text-slate-600">{item.description}</p><div className="mt-5 border-t border-emerald-100 pt-4 text-xs font-bold text-emerald-600">探索时珍智训 <ArrowRight size={14} className="ml-1 inline" /></div></article>;
            })}
          </div>
        </section>
      </main>

      {showAuth && (
        <section className="auth-login-view fixed inset-0 z-[70] overflow-y-auto bg-[#f4fbf7]/96 px-4 py-6 backdrop-blur-md sm:px-8 sm:py-10" role="dialog" aria-modal="true" aria-label={title}>
          <div className="auth-login-layout mx-auto flex min-h-full max-w-6xl items-center">
            <div className="auth-login-card relative w-full rounded-[2rem] border border-white/95 bg-white/92 p-6 shadow-2xl shadow-emerald-200/70 sm:p-8">
              <button type="button" onClick={closeAuth} className="auth-login-back absolute right-5 top-5 text-sm font-semibold text-emerald-700 transition hover:text-emerald-900">返回展示页</button>
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
                {mode === 'login' ? <>还没有账号？ <button type="button" onClick={() => { setMode('register'); setError(''); }} className="font-medium text-emerald-700 hover:text-emerald-800">创建学习账号</button></> : <>已有账号？ <button type="button" onClick={() => { setMode('login'); setError(''); }} className="font-medium text-emerald-700 hover:text-emerald-800">返回登录</button></>}
              </div>
            </div>
          </div>
        </section>
      )}

      <footer className="border-t border-emerald-100 bg-white/45 px-5 py-8 text-center text-xs text-slate-500 sm:px-8 lg:px-12"><div className="mx-auto flex max-w-[1440px] flex-col items-center justify-between gap-3 sm:flex-row"><span>© 2026 时珍智训 · 让经典智慧在每一次学习中焕新</span><span className="font-semibold text-emerald-700">SHIZHEN AI · TCM LEARNING PLATFORM</span></div></footer>
    </div>
  );
};

export default AuthPage;
