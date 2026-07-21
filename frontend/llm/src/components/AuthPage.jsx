import React, { useState } from 'react';
import {
  BookOpen,
  BrainCircuit,
  Cpu,
  Library,
  LineChart,
  Loader2,
  Lock,
  Target,
  User as UserIcon,
} from 'lucide-react';
import { AUTH_API_BASE, readJsonResponse } from '../utils/api';

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
    displayName: '',
    password: '',
  });

  const handleChange = (e) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setSuccessMsg('');

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
      if (!res.ok) throw new Error(data.detail || (mode === 'login' ? '登录失败' : '注册失败'));
      if (!data.user) throw new Error('登录响应缺少用户信息');
      onLogin(data.user);
    } catch (err) {
      setError(err.message);
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

            {mode === 'register' && (
              <>
                <div>
                  <label htmlFor="auth-register-username" className="mb-1 block text-sm font-medium text-slate-700">用户名</label>
                  <div className="relative">
                    <UserIcon className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      id="auth-register-username"
                      name="username"
                      autoComplete="username"
                      minLength={3}
                      value={formData.username}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder="设置学习账号名称"
                      required
                    />
                  </div>
                </div>
                <div>
                  <label htmlFor="auth-display-name" className="mb-1 block text-sm font-medium text-slate-700">显示名（可选）</label>
                  <div className="relative">
                    <UserIcon className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      id="auth-display-name"
                      name="displayName"
                      autoComplete="name"
                      value={formData.displayName}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder="例如：林同学"
                    />
                  </div>
                </div>
                <div>
                  <label htmlFor="auth-register-password" className="mb-1 block text-sm font-medium text-slate-700">密码</label>
                  <div className="relative">
                    <Lock className="absolute left-3 top-3 text-slate-400" size={18} />
                    <input
                      id="auth-register-password"
                      name="password"
                      type="password"
                      autoComplete="new-password"
                      minLength={8}
                      value={formData.password}
                      onChange={handleChange}
                      className="w-full rounded-2xl border border-slate-200 bg-slate-50 pl-10 pr-4 py-3 text-slate-800 outline-none transition focus:border-emerald-300 focus:bg-white focus:ring-2 focus:ring-emerald-100"
                      placeholder="至少 8 位"
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
