import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, CalendarCheck2, UploadCloud } from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import {
  EMPTY_HOME_PAYLOAD,
  HOME_ACTIONS,
  buildHomePortalState,
  getHomeActionIntent,
} from '../homePortal';

const HIGHLIGHT_COPY = {
  'continue-learning': {
    description: '回到上次学习内容，沿着既定路径继续推进。',
  },
  'pending-tasks': {
    description: '集中查看今天需要完成的学习安排。',
  },
  'ai-qa': {
    description: '围绕资料提问，获得引用来源与可追溯回答。',
  },
};

const FEATURE_COPY = {
  'resource-search': '检索公共资料与个人上传内容。',
  'knowledge-graph': '按学习主题浏览知识结构与关联。',
  'question-workspace': '围绕知识点查找、整理与练习题目。',
  'focused-practice': '提交练习，获取 AI 批改与复盘建议。',
  'mistake-reinforcement': '针对薄弱知识点生成变式训练。',
  'case-training': '进入案例分析与情境化训练。',
};

function actionFor(key) {
  return HOME_ACTIONS.find((item) => item.key === key);
}

function HomeIllustration({ action, className = '' }) {
  return (
    <img
      src={action.image}
      alt={`${action.title}功能插图`}
      className={className}
      width="168"
      height="168"
    />
  );
}

export default function HomePage({ onNavigate }) {
  const [payload, setPayload] = useState(EMPTY_HOME_PAYLOAD);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [checkinLoading, setCheckinLoading] = useState(false);
  const [checkinMessage, setCheckinMessage] = useState('');
  const homeState = useMemo(() => buildHomePortalState(payload), [payload]);
  const highlights = useMemo(() => (
    ['continue-learning', 'pending-tasks', 'ai-qa'].map(actionFor)
  ), []);
  const features = useMemo(() => HOME_ACTIONS.filter((item) => !highlights.includes(item)), [highlights]);

  useEffect(() => {
    let cancelled = false;

    const loadSummary = async () => {
      setLoading(true);
      setError('');
      try {
        const response = await fetchWithAuth(`${MAIN_API_BASE}/dashboard/home`);
        const result = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(result.detail || '首页数据暂不可用');
        if (!result || typeof result !== 'object' || Array.isArray(result)) {
          throw new Error('首页数据暂不可用');
        }
        if (!cancelled) setPayload(result);
      } catch (requestError) {
        if (!cancelled) setError(requestError.message || '首页数据暂不可用');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    loadSummary();
    return () => { cancelled = true; };
  }, []);

  const navigate = (key) => {
    onNavigate?.(getHomeActionIntent(key, payload));
  };

  const checkinStatus = payload.checkin_status || EMPTY_HOME_PAYLOAD.checkin_status;
  const submitCheckin = async () => {
    if (checkinStatus.checked_in_today || checkinLoading) return;
    setCheckinLoading(true);
    setCheckinMessage('');
    try {
      const response = await fetchWithAuth(`${MAIN_API_BASE}/checkin`, { method: 'POST' });
      const result = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(result.detail || '签到失败');
      setPayload((current) => ({ ...current, checkin_status: result.status || current.checkin_status }));
      setCheckinMessage(result.message || '今日签到成功');
    } catch (requestError) {
      setCheckinMessage(requestError.message || '签到失败');
    } finally {
      setCheckinLoading(false);
    }
  };

  const highlightDetail = (key) => {
    if (key === 'continue-learning') return homeState.continueLearning.title;
    if (key === 'pending-tasks') {
      return homeState.pendingTasks.count > 0
        ? `${homeState.pendingTasks.count} 项待完成任务 · ${homeState.pendingTasks.duration}`
        : '暂无待办任务';
    }
    return HIGHLIGHT_COPY[key].description;
  };

  return (
    <div className="home-portal" aria-busy={loading}>
      <section className="home-portal__hero" aria-labelledby="home-portal-title">
        <img className="home-portal__hero-ribbon" src="/design-images/home/hero-ribbon.png" alt="" aria-hidden="true" width="1672" height="941" />
        <div className="home-portal__hero-actions">
          <button type="button" className="home-portal__checkin" onClick={submitCheckin} disabled={checkinLoading || checkinStatus.checked_in_today} aria-label={checkinStatus.checked_in_today ? `今日已签到，连续${checkinStatus.streak || 0}天` : '今日签到'}>
            <CalendarCheck2 aria-hidden="true" size={18} />{checkinStatus.checked_in_today ? `已签到 · ${checkinStatus.streak || 0}天` : checkinLoading ? '签到中…' : '签到'}
          </button>
          <button type="button" className="home-portal__upload" onClick={() => onNavigate?.({ page: 'knowledge', params: { view: 'personal' } })}>
            <UploadCloud aria-hidden="true" size={19} />上传资料
          </button>
          <button type="button" className="home-portal__start" onClick={() => navigate('focused-practice')}>
            开始学习<ArrowRight aria-hidden="true" size={20} />
          </button>
        </div>
        <div className="home-portal__hero-copy">
          <p className="home-portal__hero-lead">今天，让学习更有方向</p>
          <h1 className="home-portal__hero-title" id="home-portal-title">循序精进</h1>
          <span className="home-portal__hero-desc">聚合资料、题库与 AI 辅导，为你安排清晰可执行的学习路径。</span>
        </div>
      </section>

      {error && <div className="home-portal__notice" role="alert">{error}</div>}
      {checkinMessage && <div className="home-portal__notice" role="status">{checkinMessage}</div>}
      {!error && homeState.announcements[0] && (
        <div className="home-portal__notice" role="status">{homeState.announcements[0]}</div>
      )}

      <section className="home-portal__highlights" aria-label="学习摘要">
        {highlights.map((action) => (
          <button
            key={action.key}
            type="button"
            className={`home-portal__highlight home-portal__highlight--${action.key}`}
            onClick={() => navigate(action.key)}
            aria-label={`${action.title}：${highlightDetail(action.key)}`}
          >
            <HomeIllustration action={action} className="home-portal__highlight-image" />
            <span className="home-portal__highlight-copy">
              <strong>{action.title}</strong>
              <span>{highlightDetail(action.key)}</span>
              {action.key === 'continue-learning' && homeState.continueLearning.progress !== null && (
                <progress
                  className="home-portal__progress"
                  aria-label="学习进度"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  aria-valuenow={homeState.continueLearning.progress}
                  value={homeState.continueLearning.progress}
                  max="100"
                >
                  {homeState.continueLearning.progress}%
                </progress>
              )}
            </span>
            <ArrowRight aria-hidden="true" className="home-portal__card-arrow" size={20} />
          </button>
        ))}
      </section>

      <section className="home-portal__features" aria-labelledby="home-features-title">
        <h2 id="home-features-title">常用功能</h2>
        <div className="home-portal__feature-grid">
          {features.map((action) => (
            <button
              key={action.key}
              type="button"
              className={`home-portal__feature home-portal__feature--${action.key}`}
              onClick={() => navigate(action.key)}
              aria-label={`${action.title}：${FEATURE_COPY[action.key]}`}
            >
              <HomeIllustration action={action} className="home-portal__feature-image" />
              <span className="home-portal__feature-copy">
                <strong>{action.title}</strong>
                <span>{FEATURE_COPY[action.key]}</span>
              </span>
              <ArrowRight aria-hidden="true" className="home-portal__card-arrow" size={19} />
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
