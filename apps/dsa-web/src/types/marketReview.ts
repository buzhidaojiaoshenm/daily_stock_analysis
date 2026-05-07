export type MarketReviewRegion = 'cn' | 'us' | 'both';

export interface MarketReviewRunRequest {
  region?: MarketReviewRegion;
  sendNotification?: boolean;
}

export interface MarketReviewRunResponse {
  status: 'completed';
  region: MarketReviewRegion;
  report: string;
  notificationSent: boolean;
}

