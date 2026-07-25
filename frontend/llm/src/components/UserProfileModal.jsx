import React, { useEffect, useId, useRef, useState } from 'react';
import { Camera, Loader2, Mail, MapPin, Save, UserRound, X } from 'lucide-react';
import { AUTH_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { useModalFocus } from './ui/useModalFocus';

const emptyProfile = {
  display_name: '',
  gender: 'unspecified',
  birth_date: '',
  region: '',
  contact_email: '',
  signature: '',
  avatar_url: null,
};

const normalizeProfile = (currentUser, profile = {}) => ({
  ...emptyProfile,
  ...profile,
  display_name: profile.display_name || currentUser?.display_name || currentUser?.username || '',
  birth_date: profile.birth_date || '',
  contact_email: profile.contact_email || '',
});

export default function UserProfileModal({ open, currentUser, onClose, onSaved }) {
  const dialogRef = useModalFocus(open);
  const titleId = useId();
  const fileInputRef = useRef(null);
  const [profile, setProfile] = useState(() => normalizeProfile(currentUser));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError('');
      setNotice('');
      try {
        const response = await fetchWithAuth(`${AUTH_API_BASE}/me/profile`);
        const payload = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(payload.detail || '个人信息加载失败');
        if (!cancelled) setProfile(normalizeProfile(payload.user || currentUser, payload.profile));
      } catch (requestError) {
        if (!cancelled) {
          setProfile(normalizeProfile(currentUser));
          setError(requestError.message || '个人信息加载失败');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [open, currentUser?.user_id]);

  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;

  const updateField = (field, value) => setProfile((current) => ({ ...current, [field]: value }));

  const save = async (event) => {
    event.preventDefault();
    setSaving(true);
    setError('');
    setNotice('');
    try {
      const response = await fetchWithAuth(`${AUTH_API_BASE}/me/profile`, {
        method: 'PATCH',
        body: JSON.stringify({
          display_name: profile.display_name.trim(),
          gender: profile.gender,
          birth_date: profile.birth_date || null,
          region: profile.region.trim(),
          contact_email: profile.contact_email.trim() || null,
          signature: profile.signature.trim(),
        }),
      });
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '个人信息保存失败');
      const savedProfile = normalizeProfile(payload.user || currentUser, payload.profile);
      setProfile(savedProfile);
      onSaved?.(payload.user, savedProfile);
      setNotice('个人信息已保存');
    } catch (requestError) {
      setError(requestError.message || '个人信息保存失败');
    } finally {
      setSaving(false);
    }
  };

  const uploadAvatar = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    if (file.size > 1024 * 1024) {
      setError('头像图片不能超过 1 MB');
      return;
    }
    setUploading(true);
    setError('');
    setNotice('');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const response = await fetchWithAuth(`${AUTH_API_BASE}/me/avatar`, {
        method: 'PUT',
        body: formData,
      });
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '头像上传失败');
      const savedProfile = normalizeProfile(payload.user || currentUser, payload.profile);
      setProfile(savedProfile);
      onSaved?.(payload.user, savedProfile);
      setNotice('头像已更新');
    } catch (requestError) {
      setError(requestError.message || '头像上传失败');
    } finally {
      setUploading(false);
    }
  };

  const avatarAlt = `${profile.display_name || currentUser?.username || '当前用户'}的头像`;
  const initial = (profile.display_name || currentUser?.username || '用').trim().slice(0, 1).toUpperCase();

  return (
    <div className="user-profile-modal-backdrop" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="user-profile-modal"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="user-profile-modal__header">
          <div>
            <span className="user-profile-modal__eyebrow">账户中心</span>
            <h2 id={titleId}>完善个人信息</h2>
            <p>这些信息仅用于你的账户展示与联系，不会写入学习画像或智能体上下文。</p>
          </div>
          <button type="button" data-autofocus className="user-profile-modal__close" aria-label="关闭个人信息" onClick={onClose}>
            <X aria-hidden="true" size={21} />
          </button>
        </header>

        <form onSubmit={save} className="user-profile-modal__form">
          <div className="user-profile-modal__avatar-section">
            <div className="user-profile-modal__avatar" aria-label={avatarAlt}>
              {profile.avatar_url ? <img src={profile.avatar_url} alt={avatarAlt} /> : <span>{initial}</span>}
            </div>
            <div className="user-profile-modal__avatar-copy">
              <strong>个人头像</strong>
              <span>支持 JPG、PNG、WebP，大小不超过 1 MB</span>
              <button
                type="button"
                className="user-profile-modal__upload"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? <Loader2 aria-hidden="true" size={17} className="animate-spin" /> : <Camera aria-hidden="true" size={17} />}
                {uploading ? '正在上传' : '更换头像'}
              </button>
              <input
                ref={fileInputRef}
                className="sr-only"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                aria-label="上传头像图片"
                onChange={uploadAvatar}
              />
            </div>
          </div>

          {loading ? (
            <div className="user-profile-modal__loading" role="status"><Loader2 aria-hidden="true" size={20} className="animate-spin" />正在加载个人信息…</div>
          ) : (
            <div className="user-profile-modal__fields">
              <label>
                <span><UserRound aria-hidden="true" size={16} />昵称</span>
                <input value={profile.display_name} onChange={(event) => updateField('display_name', event.target.value)} maxLength={64} required placeholder="请输入昵称" />
              </label>
              <label>
                <span>性别</span>
                <select value={profile.gender} onChange={(event) => updateField('gender', event.target.value)}>
                  <option value="unspecified">暂不透露</option>
                  <option value="female">女</option>
                  <option value="male">男</option>
                </select>
              </label>
              <label>
                <span>出生日期</span>
                <input type="date" value={profile.birth_date} max={new Date().toISOString().slice(0, 10)} onChange={(event) => updateField('birth_date', event.target.value)} />
              </label>
              <label>
                <span><MapPin aria-hidden="true" size={16} />地区</span>
                <input value={profile.region} onChange={(event) => updateField('region', event.target.value)} maxLength={128} placeholder="例如：上海市" />
              </label>
              <label className="user-profile-modal__field--full">
                <span><Mail aria-hidden="true" size={16} />邮箱账号</span>
                <input type="email" value={profile.contact_email} onChange={(event) => updateField('contact_email', event.target.value)} maxLength={254} placeholder="用于账户联系，不影响当前登录方式" />
              </label>
              <label className="user-profile-modal__field--full">
                <span>个性化签名</span>
                <textarea value={profile.signature} onChange={(event) => updateField('signature', event.target.value)} maxLength={240} rows={3} placeholder="写下一句属于你的学习寄语" />
              </label>
            </div>
          )}

          {error && <p className="user-profile-modal__message user-profile-modal__message--error" role="alert">{error}</p>}
          {notice && <p className="user-profile-modal__message" role="status">{notice}</p>}

          <footer className="user-profile-modal__footer">
            <button type="button" className="user-profile-modal__cancel" onClick={onClose}>取消</button>
            <button type="submit" className="user-profile-modal__save" disabled={loading || saving}>
              {saving ? <Loader2 aria-hidden="true" size={17} className="animate-spin" /> : <Save aria-hidden="true" size={17} />}
              {saving ? '正在保存' : '保存信息'}
            </button>
          </footer>
        </form>
      </section>
    </div>
  );
}