import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  ArrowLeft, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, ArrowUp,
  Heart, Bookmark, Coffee, ArrowRight, HelpCircle,
  Send, Stethoscope, History, AlertCircle, Trash2, Loader2,
  Star, Activity, FileText, RotateCcw, X, User
} from 'lucide-react';
import { fetchWithAuth, readJsonResponse } from '../utils/api';
import AcupuncturePractice from './acupuncture/AcupuncturePractice';

// ── localStorage keys ────────────────────────────────────
const STORAGE_SESSION = 'sp-session-id';
const STORAGE_SESSIONS = 'sp-completed-sessions'; // 完整接诊记录
const STORAGE_COLLECTIONS = 'sp-collections';

// ── Helpers ───────────────────────────────────────────────
const getSessions = () => { try { return JSON.parse(localStorage.getItem(STORAGE_SESSIONS) || '[]'); } catch { return []; } };
const setSessions = (v) => localStorage.setItem(STORAGE_SESSIONS, JSON.stringify(v));
const getCollections = () => { try { return JSON.parse(localStorage.getItem(STORAGE_COLLECTIONS) || '[]'); } catch { return []; } };
const setCollections = (v) => localStorage.setItem(STORAGE_COLLECTIONS, JSON.stringify(v));

const levelLabel = (score, max) => {
  const pct = max > 0 ? score / max : 0;
  if (pct >= 0.8) return '优秀';
  if (pct >= 0.6) return '良好';
  if (pct >= 0.4) return '正常';
  return '一般';
};

const barClass = (score, max) => {
  const pct = max > 0 ? score / max : 0;
  if (pct >= 0.8) return 'sp-report__breakdown-bar-fill--excellent';
  if (pct >= 0.6) return 'sp-report__breakdown-bar-fill--good';
  if (pct >= 0.4) return 'sp-report__breakdown-bar-fill--normal';
  return 'sp-report__breakdown-bar-fill--poor';
};

// ── Component ─────────────────────────────────────────────
export default function SimulatedPatientChat({ showBack = true, onBack }) {
  // Core state
  const [view, setView] = useState('welcome'); // welcome | practice_select | consultation | report
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem(STORAGE_SESSION) || '');
  const [messages, setMessages] = useState([]);
  const [patient, setPatient] = useState(null);
  const [turnCount, setTurnCount] = useState(0);
  const [helpAvailable, setHelpAvailable] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // Sidebar
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [sidebarMode, setSidebarMode] = useState('history'); // history | favorites | mistakes
  const [sidebarLoading, setSidebarLoading] = useState(false);

  // Sidebar data
  const [favoritesList, setFavoritesList] = useState(getCollections);
  const [mistakesList, setMistakesList] = useState([]);
  const [backendHistory, setBackendHistory] = useState([]);
  const [completedSessions, setCompletedSessions] = useState(getSessions);

  // Practice mode
  const [practiceMode, setPracticeMode] = useState(null);
  const [specialtyInput, setSpecialtyInput] = useState('');

  // Consultation
  const [input, setInput] = useState('');
  const [showHelpCard, setShowHelpCard] = useState(false);
  const [helpData, setHelpData] = useState(null);
  const [showDiagnosisForm, setShowDiagnosisForm] = useState(false);
  const [diagnosis, setDiagnosis] = useState({ syndrome: '', prescription: '', composition: '', notes: '' });
  const [grading, setGrading] = useState(false); // true during submit/scoring

  // Report
  const [report, setReport] = useState(null);

  // History view
  const [viewingHistory, setViewingHistory] = useState(null);
  const [viewingReportExpanded, setViewingReportExpanded] = useState(false);

  // Stats
  const [stats, setStats] = useState({ total: 0, rate: 0 });
  const [currentUserName, setCurrentUserName] = useState('');

  // Refs
  const messagesEndRef = useRef(null);
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  // ── API Helper ──────────────────────────────────────────
  const callAPI = useCallback(async (action, payload = {}, { forceNewSession } = {}) => {
    setLoading(true);
    setError('');
    try {
      const id = (forceNewSession || !sessionIdRef.current) ? `sp-${crypto.randomUUID()}` : sessionIdRef.current;
      const res = await fetchWithAuth('/api/v1/simulated-patient', {
        method: 'POST',
        body: JSON.stringify({ session_id: id, action, ...payload }),
      });
      const data = await readJsonResponse(res, {});
      if (!res.ok || !data.success) throw new Error(data.error || data.detail || '模拟病患服务暂不可用');
      if (data.session_id) {
        setSessionId(data.session_id);
        sessionStorage.setItem(STORAGE_SESSION, data.session_id);
      }
      if (data.turn_count !== undefined) setTurnCount(data.turn_count);
      if (data.help_available !== undefined) setHelpAvailable(Boolean(data.help_available));
      return data;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setLoading(false);
    }
  }, []);

  // ── Initialization ─────────────────────────────────────
  useEffect(() => {
    loadSidebarData();
    loadStats();
    fetchWithAuth('/api/v1/auth/me', {}).then(r => readJsonResponse(r, {})).then(d => {
      const u = d?.user;
      if (u?.username) setCurrentUserName(u.display_name || u.username);
    }).catch(() => { });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [completedSessions]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Auto-save messages to localStorage so dialogue survives page exits
  useEffect(() => {
    if (!sessionId || messages.length === 0) return;
    const sessions = getSessions();
    const idx = sessions.findIndex(s => s.session_id === sessionId);
    if (idx >= 0 && sessions[idx].status === 'active') {
      sessions[idx].messages = [...messages];
      sessions[idx].turn_count = turnCount;
      sessions[idx].help_available = helpAvailable;
      setSessions(sessions);
      refreshSessions();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, turnCount, helpAvailable]);

  const loadStats = async () => {
    const sessions = getSessions();
    const completed = sessions.filter(s => s.status === 'completed');
    const today = new Date().toISOString().slice(0, 10);
    const todaySessions = completed.filter(s => (s.completed_at || s.started_at || '').slice(0, 10) === today);
    const total = todaySessions.length;
    const correct = todaySessions.filter(s => s.diagnosis_correct).length;
    const rate = total > 0 ? Math.round((correct / total) * 100) : 0;
    setStats({ total, rate });
    try {
      const response = await fetchWithAuth('/api/v1/simulated-patient', {
        method: 'POST',
        body: JSON.stringify({ session_id: 'stats', action: 'stats' }),
      });
      const result = await readJsonResponse(response, {});
      if (response.ok && result.success) {
        setStats({
          total: Number(result.data?.total) || 0,
          rate: Number(result.data?.rate) || 0,
        });
      }
    } catch { /* keep local fallback */ }
  };

  const loadSidebarData = async () => {
    setSidebarLoading(true);
    try {
      const [favRes, mistRes, histRes] = await Promise.all([
        fetchWithAuth('/api/v1/simulated-patient', { method: 'POST', body: JSON.stringify({ session_id: 'fav', action: 'collections' }) }).then(r => readJsonResponse(r, {})),
        fetchWithAuth('/api/v1/simulated-patient', { method: 'POST', body: JSON.stringify({ session_id: 'mist', action: 'mistakes' }) }).then(r => readJsonResponse(r, {})),
        fetchWithAuth('/api/v1/simulated-patient', { method: 'POST', body: JSON.stringify({ session_id: 'hist', action: 'history_list' }) }).then(r => readJsonResponse(r, {})),
      ]);
      if (favRes.success) setFavoritesList(favRes.data?.list || []);
      if (mistRes.success) setMistakesList(mistRes.data?.list || []);
      if (histRes.success) setBackendHistory(histRes.data?.list || []);
    } catch { /* silent */ }
    setSidebarLoading(false);
  };

  const refreshSessions = () => setCompletedSessions(getSessions());

  // ── Actions ─────────────────────────────────────────────

  const handleStartPractice = () => setView('practice_select');

  const handleStartSession = async () => {
    if (practiceMode === 'acupuncture') {
      setView('acupuncture');
      return;
    }
    const payload = {};
    if (practiceMode === 'topic' && specialtyInput.trim()) {
      payload.specialty = specialtyInput.trim();
    }
    const result = await callAPI('start', payload, { forceNewSession: true });
    if (!result) return;
    const opening = result.data?.patient_reply || '您好，请开始问诊。';
    const info = result.data?.patient_info || null;
    setPatient(info);
    setMessages([{ role: 'patient', content: opening }]);
    setReport(null);
    setHelpData(null);
    setView('consultation');
    // Start a draft session record (will be finalized on submit)
    if (info && result.session_id) {
      const sessions = getSessions();
      const existing = sessions.findIndex(s => s.session_id === result.session_id);
      const draft = {
        session_id: result.session_id,
        case_name: result.data?.case_name || '',
        gender: info.gender,
        age_range: info.age_range,
        body_type: info.body_type,
        started_at: new Date().toISOString(),
        status: 'active',
      };
      if (existing >= 0) sessions[existing] = { ...sessions[existing], ...draft };
      else sessions.unshift(draft);
      setSessions(sessions);
      refreshSessions();
    }
  };

  const handleSendMessage = async () => {
    const text = input.trim();
    if (!text) return;
    setInput('');
    const asked = text;
    setMessages(prev => [...prev, { role: 'doctor', content: asked }]);
    const result = await callAPI('dialogue', { user_input: asked });
    if (!result) {
      setMessages(prev => prev.slice(0, -1));
      return;
    }
    setMessages(prev => [...prev, { role: 'patient', content: result.data?.patient_reply || '患者暂未作答。' }]);
  };

  const handleClear = async () => {
    const result = await callAPI('reset');
    if (!result) return;
    setMessages([{ role: 'patient', content: result.data?.patient_reply || '' }]);
    setHelpData(null);
    setTurnCount(result.turn_count || 1);
    setHelpAvailable(false);
  };

  const handleHelp = async (helpType) => {
    const result = await callAPI('help', { help_type: helpType });
    if (!result) return;
    setHelpData(result.data || {});
    setShowHelpCard(false);
  };

  const handleSubmitDiagnosis = async () => {
    if (!diagnosis.syndrome.trim()) return;
    setShowDiagnosisForm(false);
    setGrading(true);
    setError('');
    const result = await callAPI('submit', { diagnosis });
    setGrading(false);
    if (!result) {
      setView('consultation');
      return;
    }
    const gradingReport = result.data?.grading_report || {};
    if (!gradingReport || Object.keys(gradingReport).length === 0) {
      setError('评分生成失败，请重试');
      setView('consultation');
      return;
    }
    // Set report BEFORE changing view to avoid null rendering
    setReport(gradingReport);
    // Finalize complete consultation record
    const sid = sessionIdRef.current;
    if (sid) {
      const sessions = getSessions();
      const existing = sessions.findIndex(s => s.session_id === sid);
      const existingCase = existing >= 0 ? sessions[existing].case_name : '';
      const record = {
        session_id: sid,
        history_id: result.data?.history_id || '',
        case_name: existingCase || gradingReport.correct_answer?.syndrome || gradingReport.syndrome_comparison?.correct_syndrome || '',
        score: gradingReport.score ?? 0,
        diagnosis_correct: gradingReport.diagnosis_correct ?? false,
        turn_count: turnCount,
        completed_at: new Date().toISOString(),
        gender: patient?.gender || '',
        age_range: patient?.age_range || '',
        body_type: patient?.body_type || '',
        status: 'completed',
        messages: [...messages],
        grading_report: gradingReport,
      };
      if (existing >= 0) sessions[existing] = { ...sessions[existing], ...record };
      else sessions.unshift(record);
      setSessions(sessions);
      refreshSessions();
      loadSidebarData();
      loadStats();
    }
    setView('report');
  };

  const handleAddCollection = () => {
    const collections = getCollections();
    const sessions = getSessions();
    const session = sessions.find(s => s.session_id === sessionId) || {};
    const item = {
      history_id: session.history_id || '',
      session_id: sessionId,
      case_name: report?.correct_answer?.syndrome || report?.syndrome_comparison?.correct_syndrome || session.case_name || '',
      score: report?.score || 0,
      collected_at: new Date().toISOString(),
    };
    collections.unshift(item);
    setCollections(collections);
    setFavoritesList(prev => [{ case_id: item.history_id, case_name: item.case_name, collected_at: item.collected_at }, ...prev]);
  };

  const handleRest = () => {
    setReport(null);
    setMessages([]);
    setPatient(null);
    setTurnCount(0);
    setHelpAvailable(false);
    setHelpData(null);
    setView('welcome');
  };

  const handleNextPatient = () => {
    setReport(null);
    setMessages([]);
    setPatient(null);
    setTurnCount(0);
    setHelpAvailable(false);
    setHelpData(null);
    setSessionId('');
    sessionStorage.removeItem(STORAGE_SESSION);
    setPracticeMode(null);
    setSpecialtyInput('');
    setView('practice_select');
  };

  const handleViewHistory = async (item) => {
    const sid = item.session_id;
    const histId = item.history_id;

    // Try to restore from saved localStorage messages first (most reliable)
    if (item.messages && item.messages.length > 0) {
      setMessages(item.messages);
      setReport(item.grading_report || null);
      setPatient({ gender: item.gender, age_range: item.age_range, body_type: item.body_type });
      setTurnCount(item.turn_count || 0);
      setViewingHistory(item);
      setViewingReportExpanded(false);
      setView('consultation');
      setSessionId(sid);
      sessionStorage.setItem(STORAGE_SESSION, sid);
      return;
    }

    // Fallback: try backend history_detail API
    if (histId) {
      try {
        setLoading(true);
        const res = await fetchWithAuth('/api/v1/simulated-patient', {
          method: 'POST',
          body: JSON.stringify({ session_id: 'hist', action: 'history_detail', history_id: histId }),
        });
        const data = await readJsonResponse(res, {});
        if (data.success && data.data?.full_dialogue) {
          const record = data.data;
          const dialogue = record.full_dialogue || [];
          setMessages(dialogue.map(d => ({ role: d.role === 'doctor' ? 'doctor' : 'patient', content: d.content })));
          setReport(record.grading_report || null);
          setPatient({ gender: item.gender, age_range: item.age_range, body_type: item.body_type });
          setTurnCount(record.turn_count || item.turn_count || 0);
          setViewingHistory(item);
          setViewingReportExpanded(false);
          setView('consultation');
          setSessionId(sid);
          sessionStorage.setItem(STORAGE_SESSION, sid);
          setLoading(false);
          return;
        }
      } catch { /* fallback */ }
      setLoading(false);
    }

    // Nothing available
    setError('无法加载该接诊记录');
  };

  const handleBackToWelcome = () => {
    setViewingHistory(null);
    setView('welcome');
    setMessages([]);
    setReport(null);
  };

  // ── Sidebar content ────────────────────────────────────
  const renderSidebarList = () => {
    if (sidebarLoading) {
      return <div className="sp-loading"><div className="sp-loading__spinner" /></div>;
    }

    const findAndViewSession = async (item) => {
      // Try to find matching session in localStorage
      const sessions = getSessions();
      const match = sessions.find(s =>
        (item.session_id && s.session_id === item.session_id) ||
        (item.history_id && s.history_id === item.history_id) ||
        (item.case_name && s.case_name === item.case_name)
      );
      if (match) {
        if (match.status === 'active') {
          setSessionId(match.session_id);
          sessionStorage.setItem(STORAGE_SESSION, match.session_id);
          setPatient({ gender: match.gender, age_range: match.age_range, body_type: match.body_type });
          setTurnCount(match.turn_count || 0);
          setHelpAvailable(match.help_available || false);
          setMessages(match.messages || []);
          setReport(match.grading_report || null);
          setView('consultation');
        } else {
          handleViewHistory(match);
        }
        return;
      }
      // Try backend history_detail API if item has history_id
      if (item.history_id) {
        try {
          setLoading(true);
          const res = await fetchWithAuth('/api/v1/simulated-patient', { method: 'POST', body: JSON.stringify({ session_id: 'hist', action: 'history_detail', history_id: item.history_id }) });
          const data = await readJsonResponse(res, {});
          setLoading(false);
          if (data.success && data.data) {
            const record = data.data;
            const dialogue = record.full_dialogue || [];
            setMessages(dialogue.map(d => ({ role: d.role === 'doctor' ? 'doctor' : 'patient', content: d.content })));
            setReport(record.grading_report || (item.score !== undefined ? { score: item.score, diagnosis_correct: item.diagnosis_correct } : null));
            setPatient({ gender: item.gender || '', age_range: item.age_range || '', body_type: item.body_type || '' });
            setTurnCount(record.turn_count || item.turn_count || 0);
            setViewingHistory({...item, ...record});
            setViewingReportExpanded(false);
            setView('consultation');
            if (record.session_id) { setSessionId(record.session_id); sessionStorage.setItem(STORAGE_SESSION, record.session_id); }
            return;
          }
        } catch {}
        setLoading(false);
      }
      // Fallback: show basic info
      const fallbackReport = item.grading_report || (item.score !== undefined ? { score: item.score, diagnosis_correct: item.diagnosis_correct } : null);
      setReport(fallbackReport);
      setViewingHistory(item);
      setViewingReportExpanded(false);
      setMessages([]);
      setView('consultation');
    };

    if (sidebarMode === 'favorites') {
      if (favoritesList.length === 0) return <div className="sp-sidebar-empty">暂无收藏</div>;
      return favoritesList.map((item, i) => (
        <div key={i} className="sp-sidebar-item" onClick={() => findAndViewSession(item)}>
          <div className="sp-sidebar-item__name">{item.case_name || '未知案例'}</div>
          <div className="sp-sidebar-item__score">收藏于 {item.collected_at?.slice(0, 10) || ''}</div>
        </div>
      ));
    }

    if (sidebarMode === 'mistakes') {
      if (mistakesList.length === 0) return <div className="sp-sidebar-empty">暂无错题记录</div>;
      return mistakesList.map((item, i) => (
        <div key={i} className="sp-sidebar-item" onClick={() => findAndViewSession(item)}>
          <div className="sp-sidebar-item__name">{item.case_name || '未知案例'}</div>
          <div className="sp-sidebar-item__meta">
            得分: {item.score ?? '--'} / 100
          </div>
        </div>
      ));
    }

    // History mode — merge localStorage with backend history
    const activeSessions = completedSessions.filter(s => s.status === 'active');
    const localCompleted = completedSessions.filter(s => s.status === 'completed');
    const localIds = new Set([...localCompleted.map(s => s.session_id), ...localCompleted.map(s => s.history_id)].filter(Boolean));
    const backendOnly = backendHistory.filter(r => !localIds.has(r.session_id) && !localIds.has(r.history_id));
    const mergedCompleted = [...localCompleted, ...backendOnly.map(r => ({
      session_id: r.session_id || '', history_id: r.history_id || '', case_name: r.case_name || '',
      score: r.score, diagnosis_correct: r.diagnosis_correct, completed_at: r.timestamp || '',
      status: 'completed', gender: r.gender || '', age_range: r.age_range || '', body_type: r.body_type || '',
      turn_count: r.turn_count || 0, messages: [], grading_report: null,
    }))].sort((a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''));
    const allSessions = [...activeSessions, ...mergedCompleted];
    if (allSessions.length === 0) return <div className="sp-sidebar-empty">暂无接诊记录</div>;
    return allSessions.map((item, i) => {
      const isActive = item.status === 'active';
      const name = isActive ? '未知病例' : (item.case_name || '未知疾病');
      const demo = [item.gender, item.age_range, '体型' + item.body_type].filter(Boolean).join(' · ');
      return (
        <div
          key={item.session_id || i}
          className={`sp-sidebar-item${viewingHistory?.session_id === item.session_id ? ' is-active' : ''}${isActive ? ' sp-sidebar-item--pending' : ''}`}
          onClick={async () => {
            if (isActive) {
              setSessionId(item.session_id);
              sessionStorage.setItem(STORAGE_SESSION, item.session_id);
              setPatient({ gender: item.gender, age_range: item.age_range, body_type: item.body_type });
              setTurnCount(item.turn_count || 0);
              setHelpAvailable(item.help_available || false);
              setView('consultation');
              setViewingHistory(null);
              // Restore saved messages if available, otherwise restart
              if (item.messages && item.messages.length > 0) {
                setMessages(item.messages);
              } else {
                setMessages([]);
                const result = await callAPI('start');
                if (result) {
                  setMessages([{ role: 'patient', content: result.data?.patient_reply || '' }]);
                  setPatient(result.data?.patient_info || null);
                }
              }
              // Mark as active again in sessions list
              const sessions = getSessions();
              const idx = sessions.findIndex(s => s.session_id === item.session_id);
              if (idx >= 0) {
                sessions[idx].status = 'active';
                setSessions(sessions);
                refreshSessions();
              }
            } else {
              handleViewHistory(item);
            }
          }}
        >
          <div className="sp-sidebar-item__name">
            {name}
            {isActive && <span className="sp-sidebar-item__badge">待作答</span>}
          </div>
          {demo && <div className="sp-sidebar-item__meta">{demo}</div>}
          {!isActive && item.score !== undefined && (
            <div className="sp-sidebar-item__score">得分: {item.score}/100</div>
          )}
          {isActive && (
            <div className="sp-sidebar-item__hint">点击继续问诊</div>
          )}
        </div>
      );
    });
  };

  // ── Render: Welcome ─────────────────────────────────────
  const renderWelcome = () => (
    <div className="sp-welcome">
      <div className="sp-welcome__icon-wrapper">
        <Stethoscope className="sp-welcome__icon" />
      </div>
      <h1 className="sp-welcome__heading">欢迎{currentUserName || ''}医生</h1>

      {view === 'welcome' && (
        <>
          <button className="sp-welcome__cta" onClick={handleStartPractice}>
            <Activity className="sp-welcome__cta-icon" />
            开始今天的问诊吧
          </button>
          <div className="sp-welcome__stats">
            <div className="sp-welcome__stat-card">
              <div className="sp-welcome__stat-value">{stats.total}</div>
              <div className="sp-welcome__stat-label">今日接诊数</div>
            </div>
            <div className="sp-welcome__stat-card">
              <div className="sp-welcome__stat-value">{stats.rate}%</div>
              <div className="sp-welcome__stat-label">正确率</div>
            </div>
          </div>
        </>
      )}

      {view === 'practice_select' && (
        <div className="sp-welcome__mode-select">
          <div className="sp-welcome__mode-options">
            <button
              className={`sp-welcome__mode-option${practiceMode === 'free' ? ' is-active' : ''}`}
              onClick={() => setPracticeMode('free')}
            >
              随心练
            </button>
            <button
              className={`sp-welcome__mode-option${practiceMode === 'acupuncture' ? ' is-active' : ''}`}
              onClick={() => setPracticeMode('acupuncture')}
            >
              针灸专练
            </button>
            <button
              className={`sp-welcome__mode-option${practiceMode === 'topic' ? ' is-active' : ''}`}
              onClick={() => setPracticeMode('topic')}
            >
              题型专练
            </button>
          </div>
          {practiceMode === 'topic' && (
            <input
              className="sp-welcome__specialty-input"
              type="text"
              placeholder="输入科室或专科方向（可选）"
              value={specialtyInput}
              onChange={e => setSpecialtyInput(e.target.value)}
            />
          )}
          {practiceMode && (
            <button className="sp-welcome__start-btn" onClick={handleStartSession} disabled={loading}>
              {loading ? <><div className="sp-loading__spinner" style={{ width: 16, height: 16, borderWidth: 2 }} /></> : '开始问诊'}
            </button>
          )}
        </div>
      )}

      {loading && view === 'practice_select' && (
        <div className="sp-loading" style={{ marginTop: 16 }}>
          <div className="sp-loading__spinner" />
          正在准备病例...
        </div>
      )}
    </div>
  );

  // ── Render: Scoring Report ──────────────────────────────
  const renderReport = () => {
    if (!report || typeof report !== 'object') return null;
    try {
      const breakdown = report.score_breakdown || {};
      const hasBreakdown = Object.keys(breakdown).length > 0;
      const correct = report.correct_answer || {};
      const knowledge = report.knowledge_points || report.knowledge_analysis?.details || [];
      const comparison = report.syndrome_comparison || {};
      const errorAnalysis = report.error_analysis || {};
      const isPass = report.diagnosis_correct;

      const dims = [
        { key: 'syndrome_score', maxKey: 'syndrome_max', label: '证型诊断' },
        { key: 'prescription_name_score', maxKey: 'prescription_name_max', label: '方剂名称' },
        { key: 'prescription_comp_score', maxKey: 'prescription_comp_max', label: '方剂组成' },
        { key: 'inquiry_score', maxKey: 'inquiry_max', label: '问诊内容' },
        { key: 'time_efficiency_score', maxKey: 'time_efficiency_max', label: '时间效率' },
        { key: 'compassion_score', maxKey: 'compassion_max', label: '人文关怀' },
      ];

      return (
        <div className="sp-report">
          <div className="sp-report__card">
            <div className="sp-report__header">
              <div className="sp-report__total-score">{report.score ?? '--'}</div>
              <div className={`sp-report__verdict ${isPass ? 'sp-report__verdict--pass' : 'sp-report__verdict--fail'}`}>
                {isPass ? '✅ 正确' : '❌ 错误'}
              </div>
            </div>

            <div className="sp-report__section">
              <div className="sp-report__section-title"><Star size={15} /> 得分明细</div>
              {hasBreakdown ? (
                <div className="sp-report__breakdown">
                  {dims.map(d => {
                    const score = breakdown[d.key] ?? 0;
                    const max = breakdown[d.maxKey] ?? (d.key.includes('syndrome') ? 40 : d.key.includes('prescription') ? 15 : d.key.includes('inquiry') ? 15 : d.key.includes('time') ? 8 : 7);
                    const pct = max > 0 ? (score / max) * 100 : 0;
                    return (
                      <div key={d.key} className="sp-report__breakdown-item">
                        <div className="sp-report__breakdown-label">{d.label}</div>
                        <div className="sp-report__breakdown-bar-track">
                          <div className={`sp-report__breakdown-bar-fill ${barClass(score, max)}`} style={{ width: `${Math.min(pct, 100)}%` }} />
                        </div>
                        <div className="sp-report__breakdown-score">{score}/{max} ({levelLabel(score, max)})</div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div style={{color:'#94a3b8',fontSize:'.85rem',padding:'4px 0'}}>详细评分暂不可用</div>
              )}
            </div>

            {correct.syndrome && (
              <div className="sp-report__section">
                <div className="sp-report__section-title"><FileText size={15} /> 正确答案</div>
                <div className="sp-report__text-item">疾病/证型：{correct.syndrome || '-'}</div>
                {correct.prescription_name && <div className="sp-report__text-item">方剂名称：{correct.prescription_name}</div>}
                {correct.prescription_composition && <div className="sp-report__text-item">方剂组成：{correct.prescription_composition}</div>}
              </div>
            )}

            {knowledge.length > 0 && (
              <div className="sp-report__section">
                <div className="sp-report__section-title"><HelpCircle size={15} /> 相关知识点</div>
                {knowledge.slice(0, 5).map((kp, i) => (
                  <div key={i} className="sp-report__text-item">
                    {kp.kp_name || kp.name || kp}: {kp.kp_description || kp.description || ''}
                  </div>
                ))}
              </div>
            )}

            {comparison.comparison && (
              <div className="sp-report__section">
                <div className="sp-report__section-title"><AlertCircle size={15} /> 疾病辨析</div>
                <div className="sp-report__text-item">你的回答：{comparison.user_syndrome || '-'}</div>
                <div className="sp-report__text-item">正确答案：{comparison.correct_syndrome || '-'}</div>
                <div className="sp-report__text-item">辨析说明：{comparison.comparison}</div>
              </div>
            )}

            {(errorAnalysis.suggestion || errorAnalysis.review_schedule) && (
              <div className="sp-report__section">
                <div className="sp-report__section-title"><Activity size={15} /> 综合建议</div>
                {errorAnalysis.suggestion && <div className="sp-report__text-item">💡 建议：{errorAnalysis.suggestion}</div>}
                {report.error_reason && <div className="sp-report__text-item">❌ 错因：{report.error_reason}</div>}
                {errorAnalysis.review_schedule && <div className="sp-report__text-item">📅 {errorAnalysis.review_schedule}</div>}
              </div>
            )}

            <div className="sp-report__actions">
              <button className="sp-report__action sp-report__action--outline" onClick={handleAddCollection}>
                <Bookmark className="sp-report__action-icon" /> 加入收藏夹
              </button>
              <button className="sp-report__action sp-report__action--outline" onClick={handleRest}>
                <Coffee className="sp-report__action-icon" /> 休息一下
              </button>
              <button className="sp-report__action sp-report__action--primary" onClick={handleNextPatient}>
                <ArrowRight className="sp-report__action-icon" /> 接诊下一位
              </button>
            </div>
          </div>
        </div>
      );
    } catch (e) {
      console.error('renderReport error:', e);
      return <div className="sp-report"><div className="sp-report__card"><p>评分报告渲染出错，请重试</p></div></div>;
    }
  };

  // ── Render: Help Card Overlay ───────────────────────────
  const renderHelpCard = () => {
    if (!showHelpCard) return null;
    return (
      <>
        <div className="sp-help-overlay__backdrop" onClick={() => setShowHelpCard(false)} />
        <div className="sp-help-overlay">
          <div className="sp-help-overlay__title">选择援助模式</div>
          <div className="sp-help-overlay__options">
            <button className="sp-help-overlay__option" onClick={() => handleHelp('question')}>
              关键问题 — 获取引导性提问
            </button>
            <button className="sp-help-overlay__option" onClick={() => handleHelp('interpretation')}>
              症状解读 — 获取病机分析
            </button>
          </div>
          {helpData && (
            <div className="sp-help-overlay__result">
              {helpData.question || helpData.interpretation || helpData.answer || JSON.stringify(helpData)}
            </div>
          )}
          <button className="sp-help-overlay__close" onClick={() => setShowHelpCard(false)}>关闭</button>
        </div>
      </>
    );
  };

  // ── Render: Diagnosis Form Overlay ──────────────────────
  const renderDiagnosisForm = () => {
    if (!showDiagnosisForm) return null;
    const fields = [
      { key: 'syndrome', label: '疾病/证型', placeholder: '请输入诊断的证型名称' },
      { key: 'prescription', label: '治法或方剂', placeholder: '请输入方剂名称或治法' },
      { key: 'composition', label: '方剂组成', placeholder: '请输入方剂组成（药味及剂量）' },
      { key: 'notes', label: '诊疗说明', placeholder: '请输入补充说明或注意事项' },
    ];
    return (
      <div className="sp-diagnosis-overlay">
        <div className="sp-diagnosis-overlay__backdrop" onClick={() => setShowDiagnosisForm(false)} />
        <div className="sp-diagnosis-overlay__card">
          <div className="sp-diagnosis-overlay__title">提交诊断</div>
          {fields.map(f => (
            <div key={f.key} className="sp-diagnosis-overlay__field">
              <label>{f.label}</label>
              <textarea
                value={diagnosis[f.key]}
                onChange={e => setDiagnosis(prev => ({ ...prev, [f.key]: e.target.value }))}
                placeholder={f.placeholder}
              />
            </div>
          ))}
          <div className="sp-diagnosis-overlay__actions">
            <button className="sp-diagnosis-overlay__btn sp-diagnosis-overlay__btn--cancel" onClick={() => setShowDiagnosisForm(false)}>取消</button>
            <button
              className="sp-diagnosis-overlay__btn sp-diagnosis-overlay__btn--submit"
              disabled={!diagnosis.syndrome.trim() || loading}
              onClick={handleSubmitDiagnosis}
            >
              {loading ? '提交中...' : '提交诊断'}
            </button>
          </div>
        </div>
      </div>
    );
  };

  // ── Render: Consultation ────────────────────────────────
  const renderConsultation = () => (
    <div className="sp-consultation">
      <div className="sp-consultation__messages">
        {patient && (
          <div className="sp-consultation__patient-info">
            <div className="sp-consultation__patient-info-icon">
              🤒
            </div>
            <div className="sp-consultation__patient-info-text">
              <span className="sp-consultation__patient-info-name">患者信息</span>
              <span className="sp-consultation__patient-info-meta">
                {patient.gender} · {patient.age_range} · 体型{patient.body_type}
              </span>
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`sp-consultation__message sp-consultation__message--${msg.role === 'doctor' ? 'doctor' : 'patient'}`}>
            <div className="sp-consultation__message-avatar">
              {msg.role === 'doctor' ? '🩺' : '🤒'}
            </div>
            <p className="sp-consultation__message-bubble">{msg.content}</p>
          </div>
        ))}
        {loading && (
          <div className="sp-loading">
            <div className="sp-loading__spinner" />
            {grading ? '时珍评分中…' : '患者思考中…'}
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {error && (
        <div className="sp-error">
          <span>{error}</span>
          <button className="sp-error__close" onClick={() => setError('')}><X size={14} /></button>
        </div>
      )}

      {helpData && !showHelpCard && (
        <div className="sp-help-overlay__result" style={{ margin: '0 20px 8px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong>援助结果</strong>
            <button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#64748b' }} onClick={() => setHelpData(null)}><X size={16} /></button>
          </div>
          <div style={{ marginTop: 4 }}>{helpData.question || helpData.interpretation || helpData.answer}</div>
        </div>
      )}

      {!viewingHistory && (
        <div className="sp-consultation__actions">
          <button
            className="sp-consultation__action-btn sp-consultation__action-btn--danger"
            onClick={handleClear}
            disabled={loading}
          >
            <RotateCcw className="sp-consultation__action-btn-icon" />
            清空对话
          </button>
          <div className="sp-tooltip">
            <button
              className={`sp-consultation__action-btn sp-consultation__action-btn--warn`}
              onClick={() => {
                if (!helpAvailable) return;
                setShowHelpCard(true);
                setHelpData(null);
              }}
              disabled={!helpAvailable}
              style={!helpAvailable ? { opacity: 0.45, cursor: 'default' } : {}}
            >
              <HelpCircle className="sp-consultation__action-btn-icon" />
              申请援助
            </button>
            {!helpAvailable && (
              <div className="sp-tooltip__text">十轮后才能开启哦</div>
            )}
          </div>
          <button
            className="sp-consultation__action-btn sp-consultation__action-btn--primary"
            onClick={() => setShowDiagnosisForm(true)}
            disabled={loading}
          >
            <FileText className="sp-consultation__action-btn-icon" />
            提交诊断
          </button>
          <span className="sp-consultation__turn-count">已问诊 {turnCount} 轮</span>
        </div>
      )}

      {!viewingHistory && (
        <form
          className="compact-assistant__composer"
          onSubmit={e => { e.preventDefault(); handleSendMessage(); }}
        >
          <label>
            <span className="sr-only">向患者问诊</span>
            <textarea
              rows={1}
              placeholder="向患者提问…"
              value={input}
              disabled={loading}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSendMessage();
                }
              }}
            />
          </label>
          <button type="submit" disabled={!input.trim() || loading}>
            {loading ? <Loader2 size={14} /> : <ArrowUp size={14} />}
          </button>
        </form>
      )}

      {renderHelpCard()}
      {renderDiagnosisForm()}
    </div>
  );

  // ── Render: History Report Sidebar ──────────────────────
  const renderHistoryReportToggle = () => {
    if (!viewingHistory) return null;
    const hasReport = report && typeof report === 'object' && Object.keys(report).length > 0;
    return (
      <div style={{ borderTop: '1px solid #e2e8f0', marginTop: 8, paddingTop: 8 }}>
        <button
          className="sp-report__toggle"
          onClick={() => setViewingReportExpanded(v => !v)}
          style={{ padding: '14px 20px', fontSize: '1rem', borderRadius: 12 }}
        >
          <FileText size={18} />
          评分报告 ({report?.score ?? '--'}分)
          {viewingReportExpanded ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
        </button>
        {viewingReportExpanded && hasReport && (
          <div style={{ maxHeight: '50vh', overflowY: 'auto', marginTop: 8 }}>
            {renderReport()}
          </div>
        )}
        {viewingReportExpanded && !hasReport && (
          <p style={{ textAlign: 'center', color: '#94a3b8', padding: 12, fontSize: '0.84rem' }}>暂无评分数据</p>
        )}
      </div>
    );
  };

  // ── Main Render ─────────────────────────────────────────
  return (
    <div className="sp-chat">
      <div className={`sp-chat__sidebar${sidebarOpen ? '' : ' is-collapsed'}`}>
        <div className="sp-chat__sidebar-inner">
          <div className="sp-sidebar-header">
            {showBack && (
              <button className="sp-sidebar-back" onClick={onBack || (() => window.history.back())}>
                <ArrowLeft className="sp-sidebar-back__icon" />
                返回训练工坊
              </button>
            )}
            {viewingHistory && (
              <button className="sp-sidebar-back" onClick={handleBackToWelcome}>
                <ArrowLeft className="sp-sidebar-back__icon" />
                返回诊室列表
              </button>
            )}
            <div className="sp-sidebar-actions">
              <button className="sp-sidebar-action sp-sidebar-action--primary" onClick={() => { setView('practice_select'); setReport(null); setMessages([]); }}>
                <Stethoscope className="sp-sidebar-action__icon" />
                开始诊断
              </button>
              <button
                className={`sp-sidebar-action${sidebarMode === 'favorites' ? ' is-active' : ''}`}
                onClick={() => setSidebarMode(sidebarMode === 'favorites' ? 'history' : 'favorites')}
              >
                <Bookmark className="sp-sidebar-action__icon" />
                我的收藏
              </button>
              <button
                className={`sp-sidebar-action${sidebarMode === 'mistakes' ? ' is-active' : ''}`}
                onClick={() => setSidebarMode(sidebarMode === 'mistakes' ? 'history' : 'mistakes')}
              >
                <AlertCircle className="sp-sidebar-action__icon" />
                我的误诊
              </button>
              <button
                className={`sp-sidebar-action${sidebarMode === 'history' ? ' is-active' : ''}`}
                onClick={() => setSidebarMode('history')}
              >
                <History className="sp-sidebar-action__icon" />
                历史记录
              </button>
            </div>
          </div>

          <div className="sp-sidebar-section">
            <h3 className="sp-sidebar-section__title">
              {sidebarMode === 'favorites' ? '我的收藏' : sidebarMode === 'mistakes' ? '我的误诊' : '我的诊室'}
            </h3>
            <div className="sp-sidebar-list">
              {renderSidebarList()}
            </div>
          </div>
        </div>
      </div>

      <button
        className="sp-chat__collapse-toggle"
        onClick={() => setSidebarOpen(!sidebarOpen)}
        title={sidebarOpen ? '收起侧栏' : '展开侧栏'}
      >
        {sidebarOpen ? <ChevronLeft size={14} /> : <ChevronRight size={14} />}
      </button>

      <div className="sp-chat__main">
        {view === 'acupuncture' ? (
          <AcupuncturePractice onBack={() => setView('practice_select')} />
        ) : view === 'report' ? renderReport() :
          view === 'consultation' ? (
            <>
              {renderConsultation()}
              {renderHistoryReportToggle()}
            </>
          ) :
            renderWelcome()
        }
      </div>
    </div>
  );
}
