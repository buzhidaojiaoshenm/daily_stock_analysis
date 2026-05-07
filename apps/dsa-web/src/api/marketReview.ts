import apiClient from './index';
import { toCamelCase } from './utils';
import type { MarketReviewRunRequest, MarketReviewRunResponse } from '../types/marketReview';

export const marketReviewApi = {
  run: async (data: MarketReviewRunRequest = {}): Promise<MarketReviewRunResponse> => {
    const requestData = {
      region: data.region ?? 'cn',
      send_notification: data.sendNotification ?? false,
    };

    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/market-review/run',
      requestData,
    );

    return toCamelCase<MarketReviewRunResponse>(response.data);
  },
};

