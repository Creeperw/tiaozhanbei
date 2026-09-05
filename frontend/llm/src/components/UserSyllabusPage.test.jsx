import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import UserSyllabusPage from './UserSyllabusPage';

const response = (payload, ok = true, status = 200) => Promise.resolve({
  ok, status, text: async () => JSON.stringify(payload),
});

describe('UserSyllabusPage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uploads a syllabus and renders the returned structure', async () => {
    let listCalls = 0;
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url.endsWith('/v1/user-syllabi') && !options.method) {
        listCalls += 1;
        return response({ items: listCalls === 1 ? [] : [{ syllabus_id: 'USY_1', title: '\u4e2d\u836f\u8003\u7eb2', processing_status: 'success', is_active: true }] });
      }
      if (url.endsWith('/v1/user-syllabi') && options.method === 'POST') {
        expect(options.body).toBeInstanceOf(FormData);
        return response({ manifest: { syllabus_id: 'USY_1' }, structured: { title: '\u4e2d\u836f\u8003\u7eb2', sections: [{ section_id: 'SEC_1', title: '\u603b\u8bba', requirements: [{ requirement_id: 'REQ_1', title: '\u56db\u6c14\u4e94\u5473', mastery_level: '\u638c\u63e1', source_pages: [1] }] }] } }, true, 201);
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<UserSyllabusPage />);
    await screen.findByText('\u8fd8\u6ca1\u6709\u4e2a\u4eba\u8003\u7eb2');
    const file = new File(['outline'], 'outline.pdf', { type: 'application/pdf' });
    fireEvent.change(screen.getByLabelText('\u8003\u7eb2\u6587\u4ef6'), { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: /\u4e0a\u4f20\u8003\u7eb2/ }));
    await waitFor(() => expect(screen.getByText('\u56db\u6c14\u4e94\u5473')).toBeInTheDocument());
    expect(screen.getByText('\u8003\u7eb2\u5df2\u89e3\u6790\u5e76\u81ea\u52a8\u6fc0\u6d3b')).toBeInTheDocument();
  });
});
