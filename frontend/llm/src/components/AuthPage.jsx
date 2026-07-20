import React, { useEffect, useState } from 'react';
import {
  BookOpen,
  BrainCircuit,
  Cpu,
  KeyRound,
  Library,
  LineChart,
  Loader2,
  Lock,
  Mail,
  Target,
  User as UserIcon,
} from 'lucide-react';
import { API_BASE, readJsonResponse } from '../utils/api';

const capabilityCards = [
  { icon: BrainCircuit, title: '培训助手', description: '围绕中医药学习问答、知识拆解与过程追踪展开。' },
  { icon: Library, title: '知识库溯源', description: '管理公共与个人资料，保留检索来源与学习线索。' },
  { icon: Target, title: '练习批改', description: '把练习、错因和复盘建议沉淀到后续学习路径。' },
  { icon: LineChart, title: '学情规划', description: '结合学习画像与阶段信号生成任务卡和报告。' },
];

const AuthPage = ({ onLogin }) => {
  const [mode, setMode] = useState('login');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [successMsg, setSuccessMsg] = useState('');
  const [formData, setFormData] = useState({
    username: '',
    email: '',
    password: '',
    code: '',
    newPassword: '',
  });
  const [countdown, setCountdown] = useState(0);

  useEffect(() => {
    let timer;
    if (countdown > 0) {
      timer = setTimeout(() => setCountdown(countdown - 1), 1000);
    }
    return () => clearTimeout(timer);
  }, [countdown]);

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSendCode = async (purpose) => {
    if (!formData.email) {
      setError('请输入邮箱地址');
      return;
    }
    setLoading(true);
    setError('');

    try {
      const res = await fetch(`${API_BASE}/send-code`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: formData.email, purpose }),
      });
      const data = await readJsonResponse(res, {});
      if (!res.ok) throw new Error(data.detail);

      setSuccessMsg(`验证码已发送至 ${formData.email}`);
      setCountdown(60);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccessMsg('');

    try {
      if (mode === 'login') {
        const formBody = new FormData();
        formBody.append('username', formData.username);
        formBody.append('password', formData.password);

        const res = await fetch(`${API_BASE}/token`, { method: 'POST', body: formBody });
        const data = await readJsonResponse(res, {});
        if (!res.ok) throw new Error(data.detail || '登录失败');

        localStorage.setItem('token', data.access_token);
        onLogin(formData.username);
      } else if (mode === 'register') {
        const res = await fetch(`${API_BASE}/register`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username: formData.username,
            email: formData.email,
            password: formData.password,
            verification_code: formData.code,
          }),
        });
        const data = await readJsonResponse(res, {});
        if (!res.ok) throw new Error(data.detail);

        localStorage.setItem('token', data.access_token);
        onLogin(formData.username);
      } else if (mode === 'reset') {
        const res = await fetch(`${API_BASE}/reset-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            email: formData.email,
            verification_code: formData.code,
            new_password: formData.newPassword,
          }),
        });
        const data = await readJsonResponse(res, {});
        if (!res.ok) throw new Error(data.detail);

        setSuccessMsg('密码重置成功，请重新登录。');
        setTimeout(() => setMode('login'), 1500);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const title = mode === 'login' ? '进入学习工作台' : mode === 'register' ? '创建学习账号' : '重置登录密码';
  const description =
    mode === 'login'
      ? '登录后进入时珍智训首页，继续使用培训助手、知识库溯源、练习批改与学情规划。'
      : mode === 'register'
        ? '注册后即可建立学习画像，开始沉淀知识来源、练习记录与阶段任务。'
        : '通过邮箱验证码重置密码，恢复你的学习进度与个人工作台。';

  return (
    <div className="auth-page min-h-screen bg-[linear-gradient(180deg,#f7faf8_0%,#eef7f2_52%,#f5f7f2_100%)] px-4 py-8 text-slate-900 lg:px-8">
      <div className="mx-auto grid min-h-[calc(100vh-4rem)] max-w-7xl gap-8 lg:grid-cols-[1.15fr_0.85fr] lg:items-center">
        <section className="order-2 overflow-hidden rounded-[32px] border border-emerald-100 bg-gradient-to-br from-white via-emerald-50/70 to-teal-50/70 p-6 shadow-xl shadow-emerald-100/60 lg:order-1 lg:p-10">
          <div className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-white/90 px-3 py-1 text-sm font-medium text-emerald-800">
            <Cpu size={16} /> 时珍智训
          </div>
          <div className="mt-6 max-w-2xl space-y-4">
            <h1 className="text-4xl font-semibold tracking-tight text-slate-950 lg:text-5xl">中医药学习与培训助手平台</h1>
            <p className="text-base leading-7 text-slate-700 lg:text-lg">
              围绕培训助手、中医药学习路径、学习画像、知识库溯源、练习批改与学情规划，
              为不同阶段的学习者提供结构化、可追踪的进阶工作台。
            </p>
            <p className="text-sm leading-6 text-slate-600">
              登录后默认进入首页，查看今日任务、近期监测摘要、推荐资源和继续学习入口。
            </p>
          </div>
          <div className="mt-8 overflow-hidden rounded-[28px] border border-white/80 bg-white shadow-lg shadow-emerald-100/60">
            <div className="relative aspect-[16/7] min-h-[220px]">
              <img
                src="/design-images/login-hero.png"
                alt="中医药在线学习场景"
                className="h-full w-full object-cover object-center"
              />
              <div className="absolute inset-0 bg-gradient-to-r from-white/82 via-white/28 to-transparent" />
              <div className="absolute bottom-4 left-4 rounded-2xl border border-white/80 bg-white/86 px-4 py-3 shadow-sm shadow-emerald-950/10 backdrop-blur">
                <div className="text-sm font-semibold text-emerald-950">循证学习场景</div>
                <div className="mt-1 text-xs leading-5 text-emerald-800">从学习路径、知识卡到训练反馈，形成可追踪的进阶闭环。</div>
              </div>
            </div>
          </div>

          <div className="mt-8 grid gap-4 sm:grid-cols-2">
            {capabilityCards.map((item) => {
              const Icon = item.icon;
              return (
                <div key={item.title} className="rounded-[24px] border border-white/80 bg-white/85 p-4 shadow-sm shadow-emerald-100/40">
                  <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-700">
                    <Icon size={20} />
                  </div>
                  <h2 className="mt-4 text-lg font-semibold text-slate-900">{item.title}</h2>
                  <p className="mt-2 text-sm leading-6 text-slate-600">{item.description}</p>
                </div>
              );
            })}
          </div>
          <div className="mt-8 flex flex-wrap gap-3 text-sm text-slate-600">
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-100 bg-white/80 px-3 py-2">
              <BookOpen size={15} className="text-emerald-700" /> 学习画像驱动推荐
            </div>
            <div className="inline-flex items-center gap-2 rounded-full border border-emerald-100 bg-white/80 px-3 py-2">
              <Library size={15} className="text-emerald-700" /> 资料来源可追踪
            </div>
          </div>
        </section>

        <section className="order-1 rounded-[32px] border border-slate-200 bg-white p-6 shadow-xl shadow-slate-200/50 lg:order-2 lg:p-8">
          <div className="flex items-center gap-3">
            <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-600 text-white shadow-sm shadow-emerald-200">
              <BrainCircuit size={24} />
            </div>
            <div>
              <div className="text-sm font-medium text-emerald-700">时珍智训</div>
              <div className="text-sm text-slate-500">培训助手平台登录</div>
            </div>
          </div>

          <div className="mt-6">
            <h2 className="text-2xl font-semibold tracking-tight text-slate-950">{title}</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">{description}</p>
          </div>

          <form onSubmit={handleSubmit} className="mt-6 space-y-4">
            {mode === 'login' && (
              <>
                <div>
                  <label htmlFor="auth-login-username" className="mb-1 block text-sm font-medium text-slate-700">账号</label>
                  <div className="relative">
                    <UserIcon className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      id="auth-login-username"
                      name="username"
                      autoComplete="username"
                      value={formData.username}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder="用户名或邮箱"
                      required
                    />
                  </div>
                </div>
                <div>
                  <div className="mb-1 flex justify-between">
                    <label htmlFor="auth-login-password" className="block text-sm font-medium text-slate-700">密码</label>
                    <button type="button" onClick={() => setMode('reset')} className="text-xs font-medium text-emerald-700 hover:text-emerald-800">
                      忘记密码
                    </button>
                  </div>
                  <div className="relative">
                    <Lock className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      id="auth-login-password"
                      name="password"
                      type="password"
                      autoComplete="current-password"
                      value={formData.password}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder="输入登录密码"
                      required
                    />
                  </div>
                </div>
              </>
            )}

            {(mode === 'register' || mode === 'reset') && (
              <>
                {mode === 'register' && (
                  <div>
                    <label className="mb-1 block text-sm font-medium text-slate-700">用户名</label>
                    <div className="relative">
                      <UserIcon className="absolute left-3 top-3 text-slate-400" size={18} />
                      <input
                        name="username"
                        value={formData.username}
                        onChange={handleChange}
                        className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                        placeholder="设置学习账号名称"
                        required
                      />
                    </div>
                  </div>
                )}
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-700">邮箱</label>
                  <div className="flex gap-2">
                    <div className="relative flex-1">
                      <Mail className="absolute left-3 top-3 text-slate-400" size={18} />
                      <input
                        name="email"
                        type="email"
                        value={formData.email}
                        onChange={handleChange}
                        className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                        placeholder="用于接收验证码"
                        required
                      />
                    </div>
                    <button
                      type="button"
                      onClick={() => handleSendCode(mode)}
                      disabled={countdown > 0 || loading}
                      className="min-w-[104px] rounded-2xl border border-emerald-100 bg-emerald-50 px-3 text-sm font-medium text-emerald-800 transition hover:bg-emerald-100 disabled:opacity-50"
                    >
                      {countdown > 0 ? `${countdown}s` : '发送验证码'}
                    </button>
                  </div>
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-700">验证码</label>
                  <div className="relative">
                    <KeyRound className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      name="code"
                      value={formData.code}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder="输入 6 位验证码"
                      required
                      maxLength={6}
                    />
                  </div>
                </div>
                <div>
                  <label className="mb-1 block text-sm font-medium text-slate-700">{mode === 'reset' ? '新密码' : '密码'}</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      name={mode === 'reset' ? 'newPassword' : 'password'}
                      type="password"
                      value={mode === 'reset' ? formData.newPassword : formData.password}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder={mode === 'reset' ? '设置新密码' : '设置登录密码'}
                      required
                    />
                  </div>
                </div>
              </>
            )}

            {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
            {successMsg && <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{successMsg}</div>}

            <button
              type="submit"
              disabled={loading}
              className="flex w-full items-center justify-center rounded-2xl bg-emerald-600 px-4 py-3 text-sm font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-50"
            >
              {loading ? <Loader2 className="animate-spin" size={20} /> : mode === 'login' ? '进入时珍智训' : '提交'}
            </button>
          </form>

          <div className="mt-6 text-center text-sm text-slate-600">
            {mode === 'login' ? (
              <>
                还没有账号？{' '}
                <button onClick={() => { setMode('register'); setError(''); }} className="font-medium text-emerald-700 hover:text-emerald-800">
                  创建学习账号
                </button>
              </>
            ) : (
              <>
                已有账号？{' '}
                <button onClick={() => { setMode('login'); setError(''); }} className="font-medium text-emerald-700 hover:text-emerald-800">
                  返回登录
                </button>
              </>
            )}
          </div>
        </section>
      </div>
    </div>
  );
};

export default AuthPage;
