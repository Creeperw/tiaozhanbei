import React from 'react';

const LABELS = {
  queued: '等待处理', running: '处理中', needs_review: '等待确认或修订',
  succeeded: '处理完成', failed: '处理失败', unknown: '状态待核实',
};

export default function UploadProgress({ progress, fallback = '状态待核实' }) {
  const state = Object.hasOwn(LABELS, progress?.state) ? progress.state : 'unknown';
  return (
    <span className={`resource-upload-progress is-${state}`} role="status">
      {progress ? (progress.label || LABELS[state]) : fallback}
    </span>
  );
}