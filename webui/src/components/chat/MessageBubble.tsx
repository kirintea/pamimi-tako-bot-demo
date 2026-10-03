/**
 * 消息气泡组件 — 渲染用户/助手消息（新的 风格）
 *
 * 用户消息：右对齐、bg-secondary/70 rounded-floating、无头像
 * 助手消息：全宽纯文本、无气泡、无头像
 * 工具调用：Wrench 图标 + 可折叠
 * 操作栏：圆形图标按钮 + Tooltip
 */

import { Check, ChevronDown, ChevronRight, Copy, GitBranch, Wrench } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { imagesApi } from '@/api/images';
import type { ChatMessage } from '@/api/types';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

interface Props {
  message: ChatMessage;
  onFork?: () => void;
  /** 透传到根元素（消息间距 / 选中态用） */
  className?: string;
}

export function MessageBubble({ message, onFork, className }: Props) {
  const isUser = message.role === 'user';

  if (isUser) {
    return <UserMessage message={message} className={className} />;
  }

  return <AssistantMessage message={message} onFork={onFork} className={className} />;
}

/** 用户消息：右对齐气泡，无头像 */
function UserMessage({ message, className }: { message: ChatMessage; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copyResetRef = useState<number | null>(null);

  const handleCopy = useCallback(() => {
    if (!message.content) return;
    navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [message.content]);

  const hasText = message.content.trim().length > 0;
  const hasImages = message.images && message.images.length > 0;

  return (
    <div
      data-user-prompt-id={message.id}
      className={cn('group ml-auto flex max-w-[min(85%,36rem)] flex-col items-end gap-1.5', className)}
    >
      {/* 图片 */}
      {hasImages && <ImageGallery keys={message.images!} align="right" />}

      {/* 文本气泡 */}
      {hasText && (
        <p className="ml-auto w-fit max-w-full min-w-0 rounded-floating px-4 py-2 text-left text-[16px]/[1.75] whitespace-pre-wrap [overflow-wrap:anywhere] bg-secondary/70">
          {message.content}
        </p>
      )}

      {/* 操作栏 */}
      {(hasText || hasImages) && (
        <TooltipProvider>
          <div className="flex min-h-8 items-center justify-end gap-1.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">
            {hasText && (
              <CopyButton content={message.content} copied={copied} onCopy={handleCopy} />
            )}
          </div>
        </TooltipProvider>
      )}
    </div>
  );
}

/** 助手消息：全宽纯文本，无气泡 */
function AssistantMessage({ message, onFork, className }: { message: ChatMessage; onFork?: () => void; className?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    if (!message.content) return;
    navigator.clipboard.writeText(message.content).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [message.content]);

  const hasContent = message.content.trim().length > 0;
  const hasToolCalls = message.toolCalls && message.toolCalls.length > 0;

  return (
    <div className={cn('group w-full text-[15px]', className)} style={{ lineHeight: 'var(--cjk-line-height, 1.75)' }}>
      {/* Thinking block */}
      {message.thinking && <ThinkingBlock content={message.thinking} />}

      {/* 主要内容 */}
      {hasContent && (
        <div data-assistant-selectable="true" className="min-w-0">
          <div className="prose prose-sm dark:prose-invert max-w-none break-words">
            <Markdown remarkPlugins={[remarkGfm]}>{message.content}</Markdown>
          </div>
        </div>
      )}

      {/* 图片 */}
      {message.images && message.images.length > 0 && (
        <ImageGallery keys={message.images} align="left" />
      )}

      {/* Tool calls */}
      {hasToolCalls && (
        <TraceGroup toolCalls={message.toolCalls!} />
      )}

      {/* 操作栏 */}
      {(hasContent || hasToolCalls) && (
        <TooltipProvider>
          <div className="message-actions mt-2 flex min-h-8 flex-wrap items-center gap-x-2 gap-y-1 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity">
            {hasContent && (
              <CopyButton content={message.content} copied={copied} onCopy={handleCopy} />
            )}
            {onFork && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={onFork}
                    aria-label="从此处分叉会话"
                    className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-muted/55 hover:text-foreground"
                  >
                    <GitBranch className="h-4 w-4" aria-hidden />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" align="center">从此处分叉会话</TooltipContent>
              </Tooltip>
            )}
          </div>
        </TooltipProvider>
      )}
    </div>
  );
}

/** 复制按钮 */
function CopyButton({ content, copied, onCopy }: { content: string; copied: boolean; onCopy: () => void }) {
  const label = copied ? '已复制' : '复制';
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          onClick={onCopy}
          aria-label={label}
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-muted/55 hover:text-foreground"
        >
          {copied ? (
            <Check className="h-4 w-4 text-green-500" aria-hidden />
          ) : (
            <Copy className="h-4 w-4" aria-hidden />
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="top" align="center">{label}</TooltipContent>
    </Tooltip>
  );
}

/** 思考过程折叠 */
function ThinkingBlock({ content }: { content: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-2">
      <button
        className="flex items-center gap-1.5 px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground rounded-full hover:bg-muted transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />}
        思考过程
      </button>
      {expanded && (
        <div className="mt-1 ml-2 pl-3 border-l-2 border-muted-foreground/20 text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed">
          {content}
        </div>
      )}
    </div>
  );
}

/** 工具调用组 — 可折叠的 trace 列表 */
function TraceGroup({ toolCalls }: { toolCalls: NonNullable<ChatMessage['toolCalls']> }) {
  const count = toolCalls.length;
  const [open, setOpen] = useState(false);

  return (
    <div className="w-full mt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="group/trace flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted/45"
        aria-expanded={open}
      >
        <Wrench className="h-3.5 w-3.5" aria-hidden />
        <span className="font-medium">
          {count === 1 ? '1 tool call' : `${count} tool calls`}
        </span>
        <ChevronRight
          aria-hidden
          className={cn(
            'ml-auto h-3.5 w-3.5 transition-transform duration-200',
            open && 'rotate-90',
          )}
        />
      </button>
      {open && (
        <ul className="mt-1 space-y-0.5 border-l border-muted-foreground/20 pl-3">
          {toolCalls.map((tc) => (
            <li key={tc.tool_call_id} className="whitespace-pre-wrap break-words">
              <ToolCallItem toolCall={tc} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 单个工具调用详情 */
function ToolCallItem({ toolCall }: { toolCall: NonNullable<ChatMessage['toolCalls']>[number] }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-md overflow-hidden">
      <button
        className="flex items-center gap-2 w-full px-2 py-1.5 text-xs hover:bg-muted/45 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="font-mono text-[11.5px] text-foreground">{toolCall.tool_name}</span>
        {toolCall.state && (
          <span
            className={cn(
              'ml-auto text-[10px]',
              toolCall.state === 'success' && 'text-green-500',
              toolCall.state === 'error' && 'text-destructive',
            )}
          >
            {toolCall.state === 'success' ? '✓' : toolCall.state === 'error' ? '✗' : '⏳'}
          </span>
        )}
        {expanded ? (
          <ChevronDown className="size-3 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3 text-muted-foreground" />
        )}
      </button>
      {expanded && (
        <div className="border-t border-border/50 px-2 py-1.5">
          {toolCall.tool_args != null && (
            <div className="mb-1">
              <div className="text-[10px] text-muted-foreground mb-0.5">参数:</div>
              <pre className="text-[11.5px] font-mono bg-background rounded p-1.5 overflow-x-auto max-h-40">
                {typeof toolCall.tool_args === 'string'
                  ? toolCall.tool_args
                  : JSON.stringify(toolCall.tool_args, null, 2)}
              </pre>
            </div>
          )}
          {toolCall.result && (
            <div>
              <div className="text-[10px] text-muted-foreground mb-0.5">结果:</div>
              <pre className="text-[11.5px] font-mono bg-background rounded p-1.5 overflow-x-auto max-h-40">
                {toolCall.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 图片画廊 */
function ImageGallery({ keys, align }: { keys: string[]; align: 'left' | 'right' }) {
  const [urls, setUrls] = useState<(string | null)[]>(keys.map(() => null));
  const [errors, setErrors] = useState<(string | null)[]>(keys.map(() => null));

  useEffect(() => {
    let cancelled = false;

    async function loadUrls() {
      const results: (string | null)[] = [];
      const errs: (string | null)[] = [];
      for (const key of keys) {
        try {
          const res = await imagesApi.getAccessUrl(key);
          results.push(res.url);
          errs.push(null);
        } catch {
          results.push(null);
          errs.push('加载失败');
        }
      }
      if (!cancelled) {
        setUrls(results);
        setErrors(errs);
      }
    }

    loadUrls();
    return () => { cancelled = true; };
  }, [keys]);

  return (
    <div
      className={cn(
        'mt-2 flex flex-wrap gap-2',
        align === 'right' ? 'ml-auto justify-end' : 'mr-auto justify-start',
      )}
    >
      {keys.map((key, i) => (
        <div key={key} className="relative group">
          {urls[i] ? (
            <a href={urls[i]!} target="_blank" rel="noopener noreferrer">
              <img
                src={urls[i]!}
                alt={`图片 ${i + 1}`}
                className="h-24 w-24 rounded-control border border-border/60 bg-muted/40 object-cover cursor-pointer hover:opacity-90 transition-opacity"
              />
            </a>
          ) : errors[i] ? (
            <div className="h-24 w-24 rounded-control border border-destructive/30 bg-destructive/5 flex items-center justify-center text-xs text-destructive">
              {errors[i]}
            </div>
          ) : (
            <div className="h-24 w-24 rounded-control border border-border bg-muted flex items-center justify-center">
              <div className="size-4 border-2 border-muted-foreground border-t-transparent rounded-full animate-spin" />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
