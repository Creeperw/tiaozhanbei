import React, { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  Button,
  Drawer,
  EmptyState,
  IconButton,
  InlineError,
  SegmentedControl,
  Skeleton,
  StatusBadge,
} from './index';

describe('UI primitives', () => {
  it('exposes command loading and disabled states', () => {
    render(<Button loading>保存</Button>);
    expect(screen.getByRole('button', { name: '保存' })).toBeDisabled();
    expect(screen.getByRole('button')).toHaveAttribute('aria-busy', 'true');
  });

  it('requires an accessible icon button name', () => {
    render(<IconButton label="关闭"><span aria-hidden="true">x</span></IconButton>);
    expect(screen.getByRole('button', { name: '关闭' })).toBeInTheDocument();
  });

  it('changes segmented control selection', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <SegmentedControl
        label="图谱视图"
        value="atlas"
        options={[{ value: 'atlas', label: '球体' }, { value: 'list', label: '列表' }]}
        onChange={onChange}
      />,
    );
    expect(screen.getByRole('radio', { name: '球体' })).toBeChecked();
    await user.click(screen.getByRole('radio', { name: '列表' }));
    expect(onChange).toHaveBeenCalledWith('list');
  });

  it('renders semantic status, loading, empty, and error feedback', () => {
    render(
      <>
        <StatusBadge status="success">已掌握</StatusBadge>
        <Skeleton label="加载考纲" />
        <EmptyState title="暂无节点" description="该层级暂无内容" />
        <InlineError message="加载失败" onRetry={vi.fn()} />
      </>,
    );
    expect(screen.getByText('已掌握')).toHaveAttribute('data-status', 'success');
    expect(screen.getByLabelText('加载考纲')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByText('暂无节点')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('加载失败');
  });

  it('only exposes an open drawer as a dialog', () => {
    const { rerender } = render(<Drawer open={false} title="知识点详情">内容</Drawer>);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    rerender(<Drawer open title="知识点详情" onClose={vi.fn()}>内容</Drawer>);
    expect(screen.getByRole('dialog', { name: '知识点详情' })).toBeVisible();
  });

  it('moves focus into a drawer and restores it after closing', async () => {
    const user = userEvent.setup();
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>查看详情</button>
          <Drawer open={open} title="知识点详情" onClose={() => setOpen(false)}>
            <button type="button">开始练习</button>
          </Drawer>
        </>
      );
    }

    render(<Harness />);
    const trigger = screen.getByRole('button', { name: '查看详情' });
    await user.click(trigger);
    expect(screen.getByRole('button', { name: '关闭' })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(trigger).toHaveFocus();
  });
});
