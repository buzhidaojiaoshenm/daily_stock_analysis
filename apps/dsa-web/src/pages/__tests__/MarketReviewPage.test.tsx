import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import MarketReviewPage from '../MarketReviewPage';

const { mockRun } = vi.hoisted(() => ({
  mockRun: vi.fn(),
}));

vi.mock('../../api/marketReview', () => ({
  marketReviewApi: {
    run: mockRun,
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockRun.mockResolvedValue({
    status: 'completed',
    region: 'cn',
    report: '### 市场温度\n\n偏强',
    notificationSent: false,
  });
});

describe('MarketReviewPage', () => {
  it('runs CN market review without notification by default and renders the report', async () => {
    render(<MarketReviewPage />);

    fireEvent.click(screen.getByRole('button', { name: '生成复盘' }));

    await waitFor(() => {
      expect(mockRun).toHaveBeenCalledWith({
        region: 'cn',
        sendNotification: false,
      });
    });

    expect(await screen.findByText('市场温度')).toBeInTheDocument();
    expect(screen.getByText('偏强')).toBeInTheDocument();
    expect(screen.getByText('通知未发送')).toBeInTheDocument();
  });

  it('runs both-region market review with notification when selected', async () => {
    mockRun.mockResolvedValue({
      status: 'completed',
      region: 'both',
      report: '## A股大盘复盘\n\n## 美股大盘复盘',
      notificationSent: true,
    });

    render(<MarketReviewPage />);

    fireEvent.change(screen.getByLabelText('复盘市场'), { target: { value: 'both' } });
    fireEvent.click(screen.getByLabelText('生成后发送通知'));
    fireEvent.click(screen.getByRole('button', { name: '生成复盘' }));

    await waitFor(() => {
      expect(mockRun).toHaveBeenCalledWith({
        region: 'both',
        sendNotification: true,
      });
    });

    expect(await screen.findByText('A股大盘复盘')).toBeInTheDocument();
    expect(screen.getByText('美股大盘复盘')).toBeInTheDocument();
    expect(screen.getByText('通知已发送')).toBeInTheDocument();
  });
});

