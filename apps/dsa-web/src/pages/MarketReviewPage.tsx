import type React from 'react';
import { useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { FileText, Send, TrendingUp } from 'lucide-react';
import { marketReviewApi } from '../api/marketReview';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { ApiErrorAlert, AppPage, Badge, Button, EmptyState, PageHeader } from '../components/common';
import type { MarketReviewRegion, MarketReviewRunResponse } from '../types/marketReview';

const REGION_LABELS: Record<MarketReviewRegion, string> = {
  cn: 'A股',
  us: '美股',
  both: 'A股 + 美股',
};

const MARKET_REVIEW_INPUT_CLASS =
  'input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-3 py-2 text-sm transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';

const MarketReviewPage: React.FC = () => {
  const [region, setRegion] = useState<MarketReviewRegion>('cn');
  const [sendNotification, setSendNotification] = useState(false);
  const [result, setResult] = useState<MarketReviewRunResponse | null>(null);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  const handleRun = async () => {
    setIsRunning(true);
    setError(null);
    try {
      const response = await marketReviewApi.run({
        region,
        sendNotification,
      });
      setResult(response);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsRunning(false);
    }
  };

  return (
    <AppPage className="space-y-5">
      <PageHeader
        eyebrow="Market Review"
        title="大盘复盘"
        description="按当前后端配置生成 A 股、美股或双市场盘后复盘。"
        actions={(
          <Button
            type="button"
            variant="primary"
            isLoading={isRunning}
            loadingText="生成中..."
            onClick={() => void handleRun()}
          >
            <TrendingUp className="h-4 w-4" />
            生成复盘
          </Button>
        )}
      />

      <section className="glass-panel px-5 py-4">
        <div className="grid gap-4 md:grid-cols-[minmax(180px,240px)_1fr] md:items-end">
          <label className="block">
            <span className="mb-2 block text-xs font-medium text-secondary-text">复盘市场</span>
            <select
              aria-label="复盘市场"
              className={MARKET_REVIEW_INPUT_CLASS}
              value={region}
              disabled={isRunning}
              onChange={(event) => setRegion(event.target.value as MarketReviewRegion)}
            >
              <option value="cn">A股</option>
              <option value="us">美股</option>
              <option value="both">A股 + 美股</option>
            </select>
          </label>

          <label className="inline-flex min-h-11 items-center gap-3 text-sm text-secondary-text">
            <input
              type="checkbox"
              className="h-4 w-4 rounded border-border bg-surface-2 text-cyan"
              checked={sendNotification}
              disabled={isRunning}
              onChange={(event) => setSendNotification(event.target.checked)}
            />
            生成后发送通知
          </label>
        </div>
      </section>

      {error ? <ApiErrorAlert error={error} onDismiss={() => setError(null)} /> : null}

      <section className="glass-panel min-h-[360px] px-5 py-5">
        {result ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="success">{REGION_LABELS[result.region]}</Badge>
              <Badge variant={result.notificationSent ? 'success' : 'default'}>
                {result.notificationSent ? '通知已发送' : '通知未发送'}
              </Badge>
            </div>
            <article className="prose prose-invert max-w-none text-sm leading-7 text-foreground">
              <Markdown remarkPlugins={[remarkGfm]}>{result.report}</Markdown>
            </article>
          </div>
        ) : (
          <EmptyState
            icon={<FileText className="h-8 w-8" />}
            title="尚未生成大盘复盘"
            description="选择市场后点击生成，报告会直接显示在这里。"
            action={(
              <Button
                type="button"
                variant="secondary"
                isLoading={isRunning}
                loadingText="生成中..."
                onClick={() => void handleRun()}
              >
                <Send className="h-4 w-4" />
                立即生成
              </Button>
            )}
          />
        )}
      </section>
    </AppPage>
  );
};

export default MarketReviewPage;
