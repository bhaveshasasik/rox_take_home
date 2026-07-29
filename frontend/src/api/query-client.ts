import { QueryClient, isServer } from "@tanstack/react-query";

import { ApiError } from "./client";

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // With SSR, a zero staleTime refetches immediately on the client for
        // data the server just sent. A short window avoids that round trip
        // without making the pipeline views feel stale.
        staleTime: 30_000,
        // A 404 or a 422 will fail again the same way; only retry that which
        // might genuinely be transient.
        retry: (failureCount, error) =>
          error instanceof ApiError && error.status < 500 ? false : failureCount < 2,
      },
    },
  });
}

let browserQueryClient: QueryClient | undefined;

/**
 * One client per request on the server, one shared client in the browser.
 *
 * Creating it at module scope would leak cached data between users during SSR;
 * creating it during render would throw the cache away whenever React suspends
 * before the provider has mounted.
 */
export function getQueryClient() {
  if (isServer) return makeQueryClient();
  browserQueryClient ??= makeQueryClient();
  return browserQueryClient;
}
