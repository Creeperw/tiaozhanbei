import React, { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import { BookOpenText, CircleHelp, ExternalLink, Film, X } from 'lucide-react';
import 'katex/dist/katex.min.css';

import { atlasImageUrl, loadAtlasImage } from './knowledgeAtlasApi';

function formatTime(seconds) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const rest = value % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}

function detailTitle(detail, fallback) {
  return detail?.kp?.lv3 || detail?.kp?.name || fallback?.name || fallback?.id || '知识点详情';
}

function detailImages(chunk) {
  const raw = chunk?.images || chunk?.image_files || [];
  return (Array.isArray(raw) ? raw : [raw]).map((item) => (
    typeof item === 'string' ? { filename: item, alt: '教材原图' } : {
      filename: item?.filename || item?.name || item?.path,
      alt: item?.alt || item?.caption || '教材原图',
    }
  )).filter((item) => item.filename);
}

function normalizeMarkdownImages(value) {
  return String(value || '').replace(
    /(!?\[[^\]]*\]\s*\()\s*(?:\.\/|\/)?images[\\/]+([^)\\/\s]+)\s*\)/gi,
    (_match, opening, filename) => `${opening}${atlasImageUrl(filename)})`,
  ).replace(
    /\(\s*(?:\.\/|\/)?images[\\/]+([^)\\/\s]+)\s*\)/gi,
    (_match, filename) => `\n\n![教材原图](${atlasImageUrl(filename)})\n\n`,
  );
}

function AuthenticatedAtlasImage({ filename, alt }) {
  const [source, setSource] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    loadAtlasImage(filename, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = typeof blob === 'string' ? blob : URL.createObjectURL(blob);
        setSource(objectUrl);
      })
      .catch((requestError) => {
        if (!controller.signal.aborted && requestError.name !== 'AbortError') setError(requestError.message || '图片加载失败');
      });
    return () => {
      controller.abort();
      if (objectUrl?.startsWith('blob:')) URL.revokeObjectURL(objectUrl);
    };
  }, [filename]);
  if (error) return <span role="alert" className="knowledge-atlas__image-error">{error}</span>;
  if (!source) return <span role="status" className="knowledge-atlas__image-loading">教材图片加载中…</span>;
  return <a href={source} target="_blank" rel="noreferrer"><img src={source} alt={alt} loading="lazy" /></a>;
}

function markdownImage({ src, alt }) {
  const filename = decodeURIComponent(String(src || '').split('/').at(-1) || '');
  return <AuthenticatedAtlasImage filename={filename} alt={alt || '教材原图'} />;
}

function MathText({ children, inline = false }) {
  return (
    <ReactMarkdown
      components={inline ? { p: ({ children: content }) => <>{content}</> } : undefined}
      remarkPlugins={[remarkGfm, remarkMath]}
      rehypePlugins={[rehypeKatex]}
    >
      {String(children || '')}
    </ReactMarkdown>
  );
}

function bilibiliPlayerUrl(video) {
  const params = new URLSearchParams({
    isOutside: 'true',
    bvid: String(video?.bvid || ''),
    p: String(video?.page || 1),
    t: String(Math.floor(Number(video?.start_seconds) || 0)),
    autoplay: '1',
    danmaku: '0',
  });
  if (video?.aid) params.set('aid', String(video.aid));
  if (video?.cid) params.set('cid', String(video.cid));
  return `https://player.bilibili.com/player.html?${params}`;
}

function QuestionCard({ question, index }) {
  const [revealed, setRevealed] = useState(false);
  const options = Array.isArray(question.options)
    ? question.options
    : question.options && typeof question.options === 'object'
      ? Object.entries(question.options).map(([id, content]) => ({ id, content }))
      : [];
  const stem = question.stem || question.content || question.question || '题干缺失';
  const answer = Array.isArray(question.answer)
    ? question.answer.join('、')
    : question.answer && typeof question.answer === 'object'
      ? JSON.stringify(question.answer)
      : String(question.answer || '暂无答案');
  return (
    <article className="knowledge-atlas__question-card">
      <div className="knowledge-atlas__question-heading">
        <span>{question.type || question.question_type || '练习题'} · {String(index + 1).padStart(2, '0')}</span>
        <div><MathText>{stem}</MathText></div>
      </div>
      {options.length > 0 && (
        <ol className="knowledge-atlas__options">
          {options.map((option, optionIndex) => {
            const id = typeof option === 'object' ? (option.option_id || option.id || option.key || String.fromCharCode(65 + optionIndex)) : String.fromCharCode(65 + optionIndex);
            const content = typeof option === 'object' ? (option.content || option.text || option.value || '') : String(option).replace(/^[A-Z][.、]\s*/, '');
            return <li key={`${id}-${content}`}><b>{id}</b><span><MathText inline>{content}</MathText></span></li>;
          })}
        </ol>
      )}
      <button type="button" className="knowledge-atlas__answer-toggle" onClick={() => setRevealed((value) => !value)}>
        {revealed ? '收起答案与解析' : '显示答案与解析'}
      </button>
      {revealed && (
        <div className="knowledge-atlas__answer">
          <p><b>答案</b><MathText inline>{answer}</MathText></p>
          {question.explanation && <p><b>解析</b><MathText inline>{question.explanation}</MathText></p>}
        </div>
      )}
    </article>
  );
}

export default function KnowledgeAtlasDetail({ node, detail, loading, error, onClose }) {
  const [tab, setTab] = useState('resources');
  const [activeVideo, setActiveVideo] = useState(null);
  const dialogRef = useRef(null);
  const closeButtonRef = useRef(null);
  const previousFocusRef = useRef(null);
  const title = detailTitle(detail, node);
  const chunks = detail?.chunks || [];
  const questions = detail?.questions || [];
  const videos = detail?.videos || [];
  const questionCount = Number(detail?.question_count ?? questions.length);
  const path = useMemo(() => [detail?.kp?.lv1, detail?.kp?.lv2].filter(Boolean).join(' / '), [detail]);

  useEffect(() => {
    previousFocusRef.current = document.activeElement;
    closeButtonRef.current?.focus();
    const frame = requestAnimationFrame(() => closeButtonRef.current?.focus());
    return () => {
      cancelAnimationFrame(frame);
      previousFocusRef.current?.focus?.();
    };
  }, []);

  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...dialogRef.current.querySelectorAll('button, a, iframe, [tabindex]:not([tabindex="-1"])')]
      .filter((element) => !element.disabled);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialogRef.current)) { event.preventDefault(); last.focus(); }
    if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };

  return (
    <>
      <button type="button" className="knowledge-atlas__drawer-shade" aria-label="关闭知识点详情" onClick={onClose} />
      <aside
        ref={dialogRef}
        className="knowledge-atlas__drawer"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <header className="knowledge-atlas__drawer-header">
          <div>
            <small>{path || '知识星球 / 知识点'}</small>
            <h2>{title}</h2>
            <p>
              <span>KP {detail?.kp?.id || node?.id}</span>
              {detail?.kp?.alias && <span>别名：{detail.kp.alias}</span>}
            </p>
          </div>
          <button ref={closeButtonRef} type="button" aria-label="关闭详情" onClick={onClose}><X aria-hidden="true" size={19} /></button>
        </header>

        <div className="knowledge-atlas__detail-tabs" role="tablist" aria-label="知识点资源">
          <button type="button" role="tab" aria-selected={tab === 'resources'} onClick={() => setTab('resources')}>
            <BookOpenText aria-hidden="true" size={15} />视频与切片 <span>{chunks.length}</span>
          </button>
          <button type="button" role="tab" aria-selected={tab === 'questions'} onClick={() => setTab('questions')}>
            <CircleHelp aria-hidden="true" size={15} />题目 <span>{questionCount}</span>
          </button>
        </div>

        <div className="knowledge-atlas__detail-content">
          {loading && <div className="knowledge-atlas__detail-state" role="status"><i />正在关联视频、知识切片与题目</div>}
          {!loading && error && <div className="knowledge-atlas__detail-state is-error" role="alert">{error}</div>}

          {!loading && !error && tab === 'resources' && (
            <>
              <section className="knowledge-atlas__videos" aria-labelledby="atlas-video-heading">
                <div className="knowledge-atlas__section-heading"><h3 id="atlas-video-heading"><Film aria-hidden="true" size={16} />视频讲解时间戳</h3><small>{videos.length ? `${videos.length} 个匹配片段` : ''}</small></div>
                {activeVideo && (
                  <div className="knowledge-atlas__player">
                    <iframe
                      title={activeVideo.topic || activeVideo.part_title || 'Bilibili视频讲解'}
                      src={bilibiliPlayerUrl(activeVideo)}
                      allowFullScreen
                      scrolling="no"
                    />
                    <a href={`https://www.bilibili.com/video/${activeVideo.bvid}?p=${activeVideo.page || 1}&t=${Math.floor(activeVideo.start_seconds || 0)}`} target="_blank" rel="noreferrer">
                      在 B 站打开<ExternalLink aria-hidden="true" size={13} />
                    </a>
                  </div>
                )}
                {videos.length ? videos.map((video, index) => (
                  <button
                    type="button"
                    key={`${video.bvid}-${video.page}-${video.start_seconds}-${index}`}
                    className={activeVideo === video ? 'is-active' : ''}
                    onClick={() => setActiveVideo(video)}
                    aria-label={`${formatTime(video.start_seconds)} ${video.topic || video.part_title || '视频讲解'}`}
                  >
                    <time>{formatTime(video.start_seconds)}</time>
                    <span><strong>{video.topic || video.part_title || '视频讲解'}</strong><small>{video.bvid} · P{String(video.page || 1).padStart(3, '0')} · {formatTime(video.start_seconds)}–{formatTime(video.end_seconds)}</small></span>
                  </button>
                )) : <p className="knowledge-atlas__empty">暂无视频资源</p>}
              </section>

              <section className="knowledge-atlas__chunks" aria-label="知识点切片">
                {chunks.length ? chunks.map((chunk, index) => (
                  <article key={chunk.id || `${chunk.book}-${index}`}>
                    <div className="knowledge-atlas__card-kicker"><span>知识点切片 {String(index + 1).padStart(2, '0')} · {chunk.book || '教材来源'}</span><span>{chunk.char_count ? `${chunk.char_count} 字` : ''}</span></div>
                    <ReactMarkdown components={{ img: markdownImage }} remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
                      {normalizeMarkdownImages(chunk.text || chunk.content || '')}
                    </ReactMarkdown>
                    {detailImages(chunk).map((image) => (
                      <figure key={image.filename}>
                        <AuthenticatedAtlasImage filename={image.filename} alt={image.alt} />
                        {image.alt !== '教材原图' && <figcaption>{image.alt}</figcaption>}
                      </figure>
                    ))}
                  </article>
                )) : <p className="knowledge-atlas__empty">未找到可读取的知识点切片</p>}
              </section>
            </>
          )}

          {!loading && !error && tab === 'questions' && (
            <section className="knowledge-atlas__questions-list" aria-label="关联题目">
              {questionCount > questions.length && <p className="knowledge-atlas__question-note">共 {questionCount.toLocaleString('zh-CN')} 道，当前展示前 {questions.length} 道</p>}
              {questions.length ? questions.map((question, index) => (
                <QuestionCard key={question.question_id || question.id || index} question={question} index={index} />
              )) : <p className="knowledge-atlas__empty">该知识点暂未关联题目</p>}
            </section>
          )}
        </div>
      </aside>
    </>
  );
}
