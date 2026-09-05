import React, { useState } from 'react';
import {
  CheckCircle2,
  Loader2,
  Lock,
  User as UserIcon,
} from 'lucide-react';
import { AUTH_API_BASE, readJsonResponse } from '../utils/api';
import OnboardingSurveyPanel from './OnboardingSurveyPanel';
import RegistrationJourneyFrame from './RegistrationJourneyFrame';

const serviceUnavailableMessage = '认证服务尚未连接，请先启动后端服务后重试。';

export default function RegistrationJourney({
  existingUser = null,
  onComplete,
  onExit,
  serviceUnavailable = false,
}) {
  const [phase, setPhase] = useState(existingUser ? 'survey' : 'account');
  const [registeredUser, setRegisteredUser] = useState(existingUser);
  const [formData, setFormData] = useState({
    username: existingUser?.username || '',
    displayName: existingUser?.display_name || '',
    password: '',
  });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const accountLocked = Boolean(registeredUser);

  const updateField = (event) => {
    setFormData((current) => ({ ...current, [event.target.name]: event.target.value }));
  };

  const continueFromAccount = async (event) => {
    event.preventDefault();
    setError('');
    if (accountLocked) {
      setPhase('survey');
      return;
    }
    setLoading(true);
    try {
      const response = await fetch(`${AUTH_API_BASE}/register`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: formData.username.trim(),
          password: formData.password,
          ...(formData.displayName.trim() ? { display_name: formData.displayName.trim() } : {}),
        }),
      });
      const data = await readJsonResponse(response, {});
      if (!response.ok) {
        const detail = typeof data.detail === 'string' ? data.detail.trim() : '';
        if (response.status >= 500 && !detail) throw new Error(serviceUnavailableMessage);
        throw new Error(detail || '注册失败');
      }
      if (!data.user) throw new Error('注册响应缺少用户信息');
      setRegisteredUser(data.user);
      setPhase('survey');
    } catch (reason) {
      const networkError = reason instanceof TypeError || reason?.name === 'AbortError';
      setError(networkError ? serviceUnavailableMessage : reason.message || '注册失败，请稍后重试。');
    } finally {
      setLoading(false);
    }
  };

  const completeJourney = (payload) => {
    onComplete?.(payload?.user || {
      ...registeredUser,
      onboarding_required: false,
    });
  };

  return (
    <>
      <div hidden={phase !== 'account'}>
        <RegistrationJourneyFrame
          step={1}
          eyebrow={accountLocked ? '账号已创建' : '第一步 · 创建账号'}
          title={accountLocked ? '你的学习账号已经准备好了' : '先创建你的学习账号'}
          description={accountLocked
            ? '账号信息已经提交。继续下一步完善学情，系统才能为你生成专属学习路径。'
            : '账号信息只用于登录和展示。完成后，我会继续了解你的学习情况。'}
          mascotMessage={accountLocked
            ? '账号已经创建好了。我们继续完成学情调查吧。'
            : '你好，我是李时珍。先告诉我怎么称呼你，再一起制定学习计划。'}
          onExit={onExit}
          exitLabel={accountLocked ? '退出注册' : '返回展示页'}
        >
          {accountLocked ? (
            <div className="registration-journey__locked">
              <CheckCircle2 size={23} aria-hidden="true" />
              <div>
                <strong>{registeredUser.display_name || registeredUser.username}</strong>
                <span>账号：{registeredUser.username} · 已安全创建</span>
              </div>
            </div>
          ) : (
            <form id="registration-account-form" className="registration-journey__form" onSubmit={continueFromAccount}>
              <label className="registration-journey__field" htmlFor="journey-username">
                <span>用户名 <small>必填</small></span>
                <span className="registration-journey__input-wrap">
                  <UserIcon size={18} aria-hidden="true" />
                  <input
                    id="journey-username"
                    className="registration-journey__input"
                    name="username"
                    value={formData.username}
                    onChange={updateField}
                    minLength={3}
                    autoComplete="username"
                    placeholder="设置学习账号名称"
                    required
                  />
                </span>
              </label>
              <label className="registration-journey__field" htmlFor="journey-display-name">
                <span>显示名 <small>选填</small></span>
                <span className="registration-journey__input-wrap">
                  <UserIcon size={18} aria-hidden="true" />
                  <input
                    id="journey-display-name"
                    className="registration-journey__input"
                    name="displayName"
                    value={formData.displayName}
                    onChange={updateField}
                    autoComplete="name"
                    placeholder="例如：林同学"
                  />
                </span>
              </label>
              <label className="registration-journey__field" htmlFor="journey-password">
                <span>密码 <small>必填 · 至少 8 位</small></span>
                <span className="registration-journey__input-wrap">
                  <Lock size={18} aria-hidden="true" />
                  <input
                    id="journey-password"
                    className="registration-journey__input"
                    name="password"
                    type="password"
                    value={formData.password}
                    onChange={updateField}
                    minLength={8}
                    autoComplete="new-password"
                    placeholder="设置登录密码"
                    required
                  />
                </span>
              </label>
            </form>
          )}

          {serviceUnavailable && !accountLocked && (
            <div className="registration-journey__notice" role="status">{serviceUnavailableMessage}</div>
          )}
          {error && <div className="registration-journey__error" role="alert">{error}</div>}
          <div className="registration-journey__actions">
            <span />
            <button
              type={accountLocked ? 'button' : 'submit'}
              form={accountLocked ? undefined : 'registration-account-form'}
              onClick={accountLocked ? () => setPhase('survey') : undefined}
              disabled={loading}
              className="registration-journey__primary"
            >
              {loading ? <Loader2 className="mx-auto animate-spin" size={20} /> : accountLocked ? '继续填写学情' : '创建账号并继续'}
            </button>
          </div>
        </RegistrationJourneyFrame>
      </div>

      {registeredUser && (
        <div hidden={phase !== 'survey'}>
          <OnboardingSurveyPanel
            required
            stepOffset={1}
            onBackToAccount={() => setPhase('account')}
            onExit={onExit}
            onSaved={completeJourney}
          />
        </div>
      )}
    </>
  );
}
