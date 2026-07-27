import React, { useCallback, useEffect, useRef, useState } from 'react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { loadLearningTarget, saveLearningTarget } from './exam-atlas/examAtlasApi';

const FALLBACK_TARGET_NAME = '中医执业医师资格考试';

export default function LearningTargetSelector({ className = '', onSaved, variant = 'select' }) {
  const [options, setOptions] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const mountedRef = useRef(false);
  const loadRequestRef = useRef(0);
  const savingRef = useRef(false);

  const load = useCallback(async () => {
    const requestId = loadRequestRef.current + 1;
    loadRequestRef.current = requestId;
    setLoading(true);
    setError('');
    setMessage('');

    try {
      const [catalogResponse, targetPayload] = await Promise.all([
        fetchWithAuth(`${MAIN_API_BASE}/qualification-targets`).then(async (response) => {
          const payload = await readJsonResponse(response, { items: [] });
          if (!response.ok) {
            throw new Error(payload.detail || '资格考试目录加载失败');
          }
          return payload;
        }),
        loadLearningTarget(),
      ]);
      if (!mountedRef.current || requestId !== loadRequestRef.current) return;

      const nextOptions = Array.isArray(catalogResponse?.items) ? catalogResponse.items : [];
      if (!nextOptions.length) {
        throw new Error('暂无可用的资格考试');
      }
      const target = targetPayload?.target || targetPayload || {};
      const selected = nextOptions.find((item) => item.exam_track_id === target.exam_track_id)
        || nextOptions[0];
      setOptions(nextOptions);
      setSelectedId(selected.target_id);
    } catch (requestError) {
      if (!mountedRef.current || requestId !== loadRequestRef.current) return;
      setOptions([]);
      setSelectedId('');
      setError(requestError.message || '学习目标加载失败');
    } finally {
      if (mountedRef.current && requestId === loadRequestRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    load();
    return () => {
      mountedRef.current = false;
      loadRequestRef.current += 1;
    };
  }, [load]);

  const selectTargetById = async (targetId) => {
    if (savingRef.current) return;
    const selected = options.find((item) => item.target_id === targetId);
    if (!selected || selected.target_id === selectedId) return;

    const previousId = selectedId;
    savingRef.current = true;
    setSaving(true);
    setError('');
    setMessage('');
    setSelectedId(selected.target_id);

    let savedTarget;
    try {
      const savedPayload = await saveLearningTarget(selected.exam_track_id);
      if (!mountedRef.current) return;
      savedTarget = savedPayload?.target || savedPayload || {};
      setMessage('学习目标已更新');
    } catch (requestError) {
      if (!mountedRef.current) return;
      setSelectedId(previousId);
      setError(requestError.message || '学习目标保存失败');
      return;
    } finally {
      savingRef.current = false;
      if (mountedRef.current) setSaving(false);
    }

    try {
      await onSaved?.({ ...selected, target: savedTarget });
    } catch {
      // Consumer callback failures must not roll back a target already persisted by the server.
    }
  };

  const selectTarget = (event) => selectTargetById(event.target.value);

  const rootClassName = [
    'learning-target-selector',
    variant === 'menu' ? 'learning-target-selector--menu' : '',
    className,
  ].filter(Boolean).join(' ');

  return (
    <div className={rootClassName}>
      {loading ? (
        <span className="learning-target-selector__loading" role="status">
          正在加载学习目标
        </span>
      ) : error && !options.length ? (
        <div className="learning-target-selector__load-error">
          <span role="alert">{error}</span>
          <button type="button" onClick={load}>重试加载学习目标</button>
        </div>
      ) : variant === 'menu' ? (
        <div className="learning-target-selector__options" role="menu" aria-label="资格考试选项">
          {options.map((item) => (
            <button
              key={item.target_id}
              type="button"
              role="menuitemradio"
              aria-checked={item.target_id === selectedId}
              disabled={saving}
              onClick={() => selectTargetById(item.target_id)}
            >
              {item.official_name}
            </button>
          ))}
        </div>
      ) : (
        <label className="learning-target-selector__control">
          <span>学习目标</span>
          <select
            className="learning-target-selector__input"
            aria-label="学习目标"
            value={selectedId}
            disabled={saving}
            onChange={selectTarget}
          >
            {!options.length && <option value="">{FALLBACK_TARGET_NAME}</option>}
            {options.map((item) => (
              <option key={item.target_id} value={item.target_id}>{item.official_name}</option>
            ))}
          </select>
        </label>
      )}
      {error && options.length > 0 && <span role="alert">{error}</span>}
      {message && <span role="status">{message}</span>}
    </div>
  );
}
