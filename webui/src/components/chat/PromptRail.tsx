/**
 * PromptRail — 消息区左侧竖排提示词标记栏（新的 简化移植）
 *
 * 保留：[data-user-prompt-id] 测量、ResizeObserver + passive scroll + rAF 节流、
 *       ≥2 提示词且滚动范围 ≥80px 才显示、二分查找 active、16px 均匀分布、
 *       hover 宽度编排 [28,22,16,11]、hover 预览面板（inert）
 * 简化：去掉分桶逻辑（DENSE_*，仅 ≥30 提示词生效）、预览用纯文本替代 MarkdownText
 */

import {
  Fragment,
  type RefObject,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';
import type { ChatMessage } from '@/api/types';
import {
  type PromptAnchor,
  promptTop,
  userPromptAnchors,
} from '@/components/chat/promptNavigation';

interface PromptRailProps {
  bottomOffset: number;
  messages: ChatMessage[];
  onJumpToPrompt: (promptId: string) => void;
  scrollRef: RefObject<HTMLDivElement | null>;
}

interface MeasuredPrompt extends PromptAnchor {
  top: number;
  topPercent: number;
}

interface PromptMarker {
  answerPreview: string;
  id: string;
  label: string;
  preview: string;
  topPercent: number;
}

const MIN_PROMPTS_FOR_RAIL = 2;
const RAIL_MIN_SCROLL_RANGE_PX = 80;
const MARKER_BASE_WIDTH_PX = 9;
const MARKER_STACK_GAP_PX = 16;
const RAIL_FALLBACK_HEIGHT_PX = 300;
const HOVER_MARKER_WIDTHS_PX = [28, 22, 16, 11];

export function PromptRail({
  bottomOffset,
  messages,
  onJumpToPrompt,
  scrollRef,
}: PromptRailProps) {
  const { t } = useTranslation();
  const railRef = useRef<HTMLDivElement>(null);
  const measuredPromptsRef = useRef<MeasuredPrompt[]>([]);
  const promptAnchors = useMemo(() => userPromptAnchors(messages), [messages]);
  const [markers, setMarkers] = useState<PromptMarker[]>([]);
  const [activePromptId, setActivePromptId] = useState<string | null>(null);
  const [focusedMarkerIndex, setFocusedMarkerIndex] = useState<number | null>(null);

  const updateMarkers = useCallback(() => {
    const scrollEl = scrollRef.current;
    const nextRailHeight = railRef.current?.clientHeight ?? 0;

    if (!scrollEl || promptAnchors.length < MIN_PROMPTS_FOR_RAIL) {
      measuredPromptsRef.current = [];
      setMarkers([]);
      setActivePromptId(null);
      return;
    }

    const scrollRange = scrollEl.scrollHeight - scrollEl.clientHeight;
    if (scrollRange < RAIL_MIN_SCROLL_RANGE_PX) {
      measuredPromptsRef.current = [];
      setMarkers([]);
      setActivePromptId(null);
      return;
    }

    const measured = measurePrompts(scrollEl, promptAnchors, scrollRange);
    measuredPromptsRef.current = measured;
    setMarkers(distributeMarkerPositions(measured, nextRailHeight));
    setActivePromptId(activePromptForScroll(measured, scrollEl.scrollTop));
  }, [promptAnchors, scrollRef]);

  const updateActivePrompt = useCallback(() => {
    const scrollEl = scrollRef.current;
    if (!scrollEl) return;
    const next = activePromptForScroll(measuredPromptsRef.current, scrollEl.scrollTop);
    setActivePromptId((current) => current === next ? current : next);
  }, [scrollRef]);

  const railVisible = markers.length > 0;
  useEffect(() => {
    const scrollEl = scrollRef.current;
    if (!scrollEl) return undefined;

    let scrollFrame = 0;
    let resizeFrame = 0;
    const scheduleActivePrompt = () => {
      window.cancelAnimationFrame(scrollFrame);
      scrollFrame = window.requestAnimationFrame(updateActivePrompt);
    };
    const scheduleMeasurement = () => {
      if (resizeFrame) return;
      resizeFrame = window.requestAnimationFrame(() => {
        resizeFrame = 0;
        updateMarkers();
      });
    };

    scheduleMeasurement();
    const observer = typeof ResizeObserver === 'undefined'
      ? null
      : new ResizeObserver(scheduleMeasurement);
    observer?.observe(scrollEl);
    if (scrollEl.firstElementChild) observer?.observe(scrollEl.firstElementChild);
    if (railRef.current) observer?.observe(railRef.current);
    scrollEl.addEventListener('scroll', scheduleActivePrompt, { passive: true });
    window.addEventListener('resize', scheduleMeasurement);
    return () => {
      window.cancelAnimationFrame(scrollFrame);
      window.cancelAnimationFrame(resizeFrame);
      observer?.disconnect();
      scrollEl.removeEventListener('scroll', scheduleActivePrompt);
      window.removeEventListener('resize', scheduleMeasurement);
    };
  }, [bottomOffset, railVisible, scrollRef, updateActivePrompt, updateMarkers]);

  if (markers.length === 0) return null;

  return (
    <div
      ref={railRef}
      aria-label={t('chat.promptNavigator.railAria')}
      className={cn(
        'thread-prompt-rail group pointer-events-auto absolute top-3 z-20 w-9 opacity-100',
        'transition-opacity duration-200',
        'motion-safe:animate-in motion-safe:fade-in-0 motion-safe:duration-200',
      )}
      onPointerLeave={() => setFocusedMarkerIndex(null)}
      style={{ bottom: Math.max(80, bottomOffset) }}
    >
      {markers.map((marker, index) => {
        const active = marker.id === activePromptId;
        const previewVisible = focusedMarkerIndex === index;
        const hoverDistance =
          focusedMarkerIndex === null ? null : Math.abs(index - focusedMarkerIndex);
        return (
          <Fragment key={marker.id}>
            <button
              type="button"
              aria-label={t('chat.promptNavigator.jumpTo', { label: marker.label })}
              onClick={() => onJumpToPrompt(marker.id)}
              onBlur={() => setFocusedMarkerIndex(null)}
              onFocus={() => setFocusedMarkerIndex(index)}
              onPointerEnter={() => setFocusedMarkerIndex(index)}
              onPointerLeave={() => setFocusedMarkerIndex(null)}
              className={cn(
                'absolute left-0 h-4 w-9 -translate-y-1/2 overflow-visible rounded-sm',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400/60',
              )}
              style={{ top: `${marker.topPercent}%` }}
            >
              <span
                aria-hidden
                className={cn(
                  'absolute left-0 top-1/2 h-0.5 -translate-y-1/2 rounded-full',
                  'transition-[width,background-color,opacity,height] duration-150',
                  railMarkerTone(hoverDistance, active),
                )}
                style={{
                  height: markerHeight(hoverDistance),
                  width: markerWidth(hoverDistance),
                }}
              />
            </button>
            <div
              ref={makeInert}
              aria-hidden
              className={cn(
                'pointer-events-none absolute left-10 z-30 w-[34rem] max-w-[calc(100vw-4rem)] -translate-y-1/2 rounded-panel border border-border/70 bg-popover px-4 py-3 text-left shadow-[0_8px_24px_rgba(15,23,42,0.13)] dark:border-white/10',
                'transition-[opacity,transform] duration-150',
                previewVisible
                  ? 'translate-x-0 scale-100 opacity-100'
                  : '-translate-x-2 scale-[0.98] opacity-0',
              )}
              style={{ top: `${marker.topPercent}%` }}
            >
              {previewVisible ? (
                <>
                  <div className="line-clamp-2 whitespace-pre-wrap break-words text-[15px] font-semibold leading-6">
                    {marker.preview}
                  </div>
                  {marker.answerPreview ? (
                    <div className="mt-1.5 max-h-[4.5rem] overflow-hidden break-words whitespace-pre-wrap text-[14px] leading-6 text-muted-foreground dark:text-white/55">
                      {marker.answerPreview}
                    </div>
                  ) : null}
                </>
              ) : null}
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

function makeInert(node: HTMLDivElement | null): void {
  if (node) node.inert = true;
}

function measurePrompts(
  scrollEl: HTMLElement,
  anchors: PromptAnchor[],
  scrollRange: number,
): MeasuredPrompt[] {
  const elements = new Map<string, HTMLElement>();
  for (const element of scrollEl.querySelectorAll<HTMLElement>('[data-user-prompt-id]')) {
    const id = element.dataset.userPromptId;
    if (id !== undefined && !elements.has(id)) elements.set(id, element);
  }
  return anchors.flatMap((anchor) => {
    const target = elements.get(anchor.id);
    if (!target) return [];
    const top = Math.max(0, Math.min(scrollRange, promptTop(scrollEl, target) - 16));
    return [{
      ...anchor,
      top,
      topPercent: clamp((top / scrollRange) * 100, 2, 98),
    }];
  });
}

function distributeMarkerPositions(measured: MeasuredPrompt[], railHeight: number): PromptMarker[] {
  const markers = measured.map((prompt) => ({
    answerPreview: prompt.answerPreview,
    id: prompt.id,
    label: prompt.label,
    preview: prompt.preview,
    topPercent: prompt.topPercent,
  }));

  const height = railHeight > 0 ? railHeight : RAIL_FALLBACK_HEIGHT_PX;
  if (markers.length <= 1) {
    return markers.map((marker) => ({ ...marker, topPercent: 50 }));
  }

  const availableHeight = Math.max(0, height - MARKER_STACK_GAP_PX);
  const stepPx = Math.min(MARKER_STACK_GAP_PX, availableHeight / (markers.length - 1));
  const stackHeight = stepPx * (markers.length - 1);
  const firstMarkerPx = (height - stackHeight) / 2;

  return markers.map((marker, index) => ({
    ...marker,
    topPercent: ((firstMarkerPx + stepPx * index) / height) * 100,
  }));
}

function activePromptForScroll(
  measured: MeasuredPrompt[],
  scrollTop: number,
): string | null {
  if (measured.length === 0) return null;
  const cursor = scrollTop + 96;
  let lower = 0;
  let upper = measured.length - 1;
  let activeIndex = 0;
  while (lower <= upper) {
    const middle = Math.floor((lower + upper) / 2);
    if (measured[middle].top <= cursor) {
      activeIndex = middle;
      lower = middle + 1;
    } else {
      upper = middle - 1;
    }
  }
  return measured[activeIndex].id;
}

function markerWidth(hoverDistance: number | null): number {
  if (hoverDistance === null) return MARKER_BASE_WIDTH_PX;
  return HOVER_MARKER_WIDTHS_PX[hoverDistance] ?? MARKER_BASE_WIDTH_PX;
}

function markerHeight(hoverDistance: number | null): number {
  return hoverDistance === 0 ? 3 : 2;
}

function railMarkerTone(hoverDistance: number | null, active: boolean): string {
  if (hoverDistance === 0) {
    return 'bg-[#222222] opacity-100 dark:bg-white';
  }
  if (hoverDistance !== null && hoverDistance < HOVER_MARKER_WIDTHS_PX.length) {
    return 'bg-[#d0d0d0] opacity-100 dark:bg-white/35';
  }
  if (active) {
    return 'bg-[#6f6f6f] opacity-100 dark:bg-white/55';
  }
  return 'bg-[#d8d8d8] opacity-100 dark:bg-white/25';
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}
