import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Film, Loader2 } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import {
  confirmDailyTaskIframeVideo,
  recordDailyTaskVideoEvidence,
} from '../pageDataLoaders';
import {
  createHtml5VideoEvidenceTracker,
  createIframeFocusEvidenceTracker,
  isVideoThresholdMet,
  videoSegment,
} from '../videoTaskEvidence';

const videoTitle = (video) => video?.video_title || video?.title || video?.summary || '视频讲解';

function embeddedVideo(video) {
  const source = String(video?.url || '');
  const bvid = String(video?.bvid || source.match(/\/video\/(BV[\w]+)/i)?.[1] || '');
  if (bvid) {
    let page = Number(video?.page || 1);
    let start = Math.floor(Number(video?.start_seconds || 0));
    try {
      const parsed = new URL(source);
      page = Number(parsed.searchParams.get('p') || page) || 1;
      start = Math.floor(Number(parsed.searchParams.get('t') || start)) || 0;
    } catch {
      // Structured video fields remain authoritative when the URL is partial.
    }
    return { kind: 'iframe', src: `https://player.bilibili.com/player.html?bvid=${encodeURIComponent(bvid)}&p=${page}&t=${start}` };
  }
  const youtubeId = source.match(/(?:youtu\.be\/|youtube\.com\/(?:watch\?v=|embed\/))([\w-]{6,})/i)?.[1];
  if (youtubeId) return { kind: 'iframe', src: `https://www.youtube.com/embed/${youtubeId}` };
  if (/\.(?:mp4|webm|ogg)(?:[?#]|$)/i.test(source)) return { kind: 'video', src: source };
  return null;
}

function DailyTaskVideoPlayer({ player, video, taskItemId }) {
  const mediaRef = useRef(null);
  const trackerRef = useRef(null);
  const [evidence, setEvidence] = useState({ activeSeconds: 0, coverage: 0 });
  const [status, setStatus] = useState('');
  const segment = videoSegment(video);
  const report = async (nextEvidence) => {
    const result = await recordDailyTaskVideoEvidence({
      fetcher: fetchJsonWithAuthFallback,
      taskItemId,
      evidence: nextEvidence,
    });
    if (result.error) setStatus(result.error);
    else if (result.evidence?.status === 'completed') setStatus('视频任务已自动完成');
  };

  useEffect(() => {
    if (!taskItemId || !player || !mediaRef.current) return undefined;
    const options = {
      segmentStart: segment.start,
      segmentEnd: segment.end,
      report,
      onProgress: setEvidence,
    };
    const tracker = player.kind === 'video'
      ? createHtml5VideoEvidenceTracker({ ...options, video: mediaRef.current })
      : createIframeFocusEvidenceTracker({ ...options, iframe: mediaRef.current });
    trackerRef.current = tracker;
    return () => {
      if (trackerRef.current === tracker) trackerRef.current = null;
      void tracker.stop();
    };
  }, [player, report, segment.end, segment.start, taskItemId]);

  const confirm = async () => {
    setStatus('正在确认…');
    await trackerRef.current?.flush();
    const result = await confirmDailyTaskIframeVideo({ fetcher: fetchJsonWithAuthFallback, taskItemId });
    if (result.evidence?.status === 'completed') {
      const tracker = trackerRef.current;
      trackerRef.current = null;
      await tracker?.stop();
    }
    setStatus(result.error || (result.evidence?.status === 'completed' ? '视频任务已完成' : '服务端尚未确认完成'));
  };
  const percent = Math.min(100, Math.floor((evidence.coverage || 0) * 100));
  const thresholdMet = isVideoThresholdMet(evidence.activeSeconds, segment.start, segment.end);

  return <>
    {player.kind === 'iframe' && <p className="rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-sm leading-6 text-sky-900">第三方播放器无法读取真实进度。仅在页面可见且播放器获得焦点时累计有效专注时长，达到 90% 后还需手动确认。</p>}
    {player.kind === 'iframe'
      ? <div className="aspect-video overflow-hidden rounded-2xl bg-slate-950"><iframe ref={mediaRef} title={videoTitle(video)} src={player.src} className="h-full w-full" allow="autoplay; fullscreen; picture-in-picture" allowFullScreen /></div>
      : <video ref={mediaRef} title={videoTitle(video)} src={player.src} className="aspect-video w-full rounded-2xl bg-slate-950" controls preload="metadata" />}
    {taskItemId && <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-sm text-slate-700" aria-live="polite">
      <div className="flex items-center justify-between gap-3"><span>{player.kind === 'iframe' ? '有效专注进度' : '去重观看进度'}</span><strong>{percent}%</strong></div>
      <progress aria-label={player.kind === 'iframe' ? '有效专注进度' : '去重观看进度'} className="mt-2 w-full" max="100" value={percent} />
      {player.kind === 'iframe' && <button type="button" className="mt-2 rounded-lg bg-emerald-700 px-3 py-2 font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50" disabled={!thresholdMet} onClick={confirm}>确认看完</button>}
      {status && <p role={status.includes('失败') ? 'alert' : 'status'} className="mt-2">{status}</p>}
    </div>}
  </>;
}

/**
 * 视频学习面板：承载每日任务/资格路线的视频观看与完成度回写。
 * 知识卡片模块移除后，视频学习仍作为独立能力保留。
 */
export default function VideoLearningPanel({
  video = null,
  taskItemId = '',
  kpName = '',
  title = '',
}) {
  const [activeVideoIndex, setActiveVideoIndex] = useState(0);

  const videos = useMemo(() => {
    const list = Array.isArray(video) ? video : (video ? [video] : []);
    return list.filter((item) => item && (item?.url || item?.bvid || item?.video_url || item?.source_url));
  }, [video]);

  const activeVideo = videos[activeVideoIndex] || videos[0] || null;
  const activeVideoPlayer = activeVideo ? embeddedVideo(activeVideo) : null;
  const heading = title || kpName || (activeVideo ? videoTitle(activeVideo) : '视频学习');

  useEffect(() => {
    if (activeVideoIndex >= videos.length) setActiveVideoIndex(0);
  }, [activeVideoIndex, videos.length]);

  return (
    <div className="video-learning-panel min-w-0 rounded-2xl border border-slate-200 bg-white p-5" aria-live="polite">
      <div className="flex items-center gap-2 pb-4">
        <Film size={18} className="text-emerald-700" aria-hidden="true" />
        <h3 className="text-lg font-semibold text-slate-950">{heading}</h3>
      </div>
      {videos.length === 0 ? (
        <div className="flex min-h-64 flex-col items-center justify-center px-4 text-center">
          <div className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-emerald-50 text-emerald-700"><Film size={23} /></div>
          <p className="max-w-md text-sm leading-6 text-slate-500">当前任务没有可观看的视频资源。</p>
        </div>
      ) : (
        <div className="space-y-4">
          {activeVideoPlayer
            ? <DailyTaskVideoPlayer key={`${activeVideoPlayer.kind}:${activeVideoPlayer.src}:${taskItemId}`} player={activeVideoPlayer} video={activeVideo} taskItemId={taskItemId} />
            : <p className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">该来源暂不支持站内播放，可使用下方原始链接查看。</p>}
          {videos.length > 1 && (
            <div className="grid gap-2 sm:grid-cols-2">
              {videos.map((item, index) => (
                <button
                  key={item.source_id || item.url || index}
                  type="button"
                  onClick={() => setActiveVideoIndex(index)}
                  aria-pressed={activeVideoIndex === index}
                  className={`rounded-xl border px-3 py-3 text-left text-sm font-medium transition ${activeVideoIndex === index ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-slate-200 text-slate-700 hover:border-emerald-200'}`}
                >
                  {videoTitle(item)}
                </button>
              ))}
            </div>
          )}
          {activeVideo?.url && <a href={activeVideo.url} target="_blank" rel="noreferrer" className="inline-block text-xs font-medium text-emerald-700 underline">在原网站打开</a>}
        </div>
      )}
      {!taskItemId && <p className="mt-4 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-500"><Loader2 size={12} className="mr-1 inline" aria-hidden="true" />当前为自由观看模式，观看进度不会计入每日任务。</p>}
    </div>
  );
}
