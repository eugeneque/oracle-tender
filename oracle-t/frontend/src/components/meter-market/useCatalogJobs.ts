import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../../api/client";
import type { MarketCatalogJob } from "../../api/types";

export function useCatalogJobs(onCompleted: () => void, onError: (err: unknown, fallback: string) => void) {
  const [jobs, setJobs] = useState<MarketCatalogJob[]>([]);
  const running = useRef(new Set<string>());
  const loadJobs = useCallback(() => {
    api.get<MarketCatalogJob[]>("/meter-market/catalog-jobs").then(setJobs).catch(() => undefined);
  }, []);
  useEffect(loadJobs, [loadJobs]);
  const hasRunning = jobs.some((job) => job.state === "running");
  useEffect(() => {
    running.current = new Set(jobs.filter((job) => job.state === "running").map((job) => job.key));
  }, [jobs]);
  useEffect(() => {
    if (!hasRunning) return;
    const timer = window.setInterval(() => {
      api.get<MarketCatalogJob[]>("/meter-market/catalog-jobs").then((next) => {
        const finished = next.filter((job) => job.state !== "running" && running.current.has(job.key));
        setJobs(next);
        if (finished.length) onCompleted();
      }).catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(timer);
  }, [hasRunning, onCompleted]);
  const startJob = useCallback(async (path: string, body: Record<string, unknown> = {}) => {
    try {
      const result = await api.post<{ job: string }>(path, body);
      await loadJobs();
      return result.job;
    } catch (err) {
      onError(err, "Не удалось запустить ИИ-разметку");
      return null;
    }
  }, [loadJobs, onError]);
  return { jobs, startJob, hasRunning };
}
