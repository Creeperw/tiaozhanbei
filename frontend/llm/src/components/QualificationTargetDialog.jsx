import React, { useEffect, useState } from 'react';
import { Check, Loader2, X } from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { loadLearningTarget, saveLearningTarget } from './exam-atlas/examAtlasApi';

export default function QualificationTargetDialog({ onCancel, onSaved }) {
  const [options, setOptions] = useState([]);
  const [selectedId, setSelectedId] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    const loadOptions = async () => {
      setLoading(true);
      setError('');
      try {
        const [response, activePayload] = await Promise.all([
          fetchWithAuth(`${MAIN_API_BASE}/qualification-targets`),
          loadLearningTarget().catch(() => ({})),
        ]);
        const payload = await readJsonResponse(response, { items: [] });
        if (!response.ok) throw new Error(payload.detail || '资格考试目录加载失败');
        const items = Array.isArray(payload.items) ? payload.items : [];
        if (!items.length) throw new Error('暂无可选择的资格考试');
        if (!cancelled) {
          const active = activePayload?.target || activePayload || {};
          const current = items.find((item) => item.exam_track_id === active.exam_track_id);
          setOptions(items);
          setSelectedId(current?.target_id || items[0].target_id);
        }
      } catch (reason) {
        if (!cancelled) setError(reason.message || '资格考试目录加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    loadOptions();
    return () => { cancelled = true; };
  }, []);

  const confirm = async () => {
    const selected = options.find((item) => item.target_id === selectedId);
    if (!selected || saving) return;
    setSaving(true);
    setError('');
    try {
      const payload = await saveLearningTarget(selected.exam_track_id);
      onSaved?.({ ...selected, target: payload?.target || payload || {} });
    } catch (reason) {
      setError(reason.message || '考试类别保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="qualification-target-dialog__backdrop" role="presentation">
      <section
        className="qualification-target-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="qualification-target-dialog-title"
      >
        <button
          type="button"
          className="qualification-target-dialog__close"
          aria-label="关闭资格考试选择"
          onClick={onCancel}
        >
          <X aria-hidden="true" size={19} />
        </button>
        <header>
          <span>学习目标</span>
          <h2 id="qualification-target-dialog-title">选择资格考试</h2>
          <p>你的选择将用于匹配考试大纲、经典教材和后续个性化学习路径。</p>
        </header>

        {loading ? (
          <div className="qualification-target-dialog__state" role="status">
            <Loader2 aria-hidden="true" className="animate-spin" size={21} />
            正在加载考试类别
          </div>
        ) : (
          <div className="qualification-target-dialog__options" role="radiogroup" aria-label="资格考试类别">
            {options.map((item) => {
              const selected = selectedId === item.target_id;
              return (
                <button
                  key={item.target_id}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  className={selected ? 'is-selected' : ''}
                  onClick={() => setSelectedId(item.target_id)}
                >
                  <span>{item.official_name}</span>
                  <Check aria-hidden="true" size={18} />
                </button>
              );
            })}
          </div>
        )}

        {error && <div className="qualification-target-dialog__error" role="alert">{error}</div>}
        <footer>
          <button type="button" className="qualification-target-dialog__cancel" onClick={onCancel}>暂不选择</button>
          <button
            type="button"
            className="qualification-target-dialog__confirm"
            disabled={loading || saving || !selectedId}
            onClick={confirm}
          >
            {saving ? '正在保存…' : '确认并开始学习'}
          </button>
        </footer>
      </section>
    </div>
  );
}
