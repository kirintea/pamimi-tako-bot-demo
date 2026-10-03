/**
 * QuickActionCards — 空状态 6 个快捷提示词卡片
 *
 * 新的 有 i18n 文案（chat.empty.quickActions.*）但未实现组件，此处补建。
 * 交互：点击填入输入框并聚焦（不直接发送）——模板需补充细节，且避免误触。
 */

import {
  BarChart3,
  Code2,
  FileText,
  Lightbulb,
  ListChecks,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

export const QUICK_ACTION_KEYS = [
  'plan',
  'analyze',
  'brainstorm',
  'code',
  'summarize',
  'more',
] as const;

const QUICK_ACTION_ICONS: Record<(typeof QUICK_ACTION_KEYS)[number], LucideIcon> = {
  plan: ListChecks,
  analyze: BarChart3,
  brainstorm: Lightbulb,
  code: Code2,
  summarize: FileText,
  more: Sparkles,
};

interface Props {
  /** 点击卡片：把模板提示词填入输入框 */
  onPick: (prompt: string) => void;
}

export function QuickActionCards({ onPick }: Props) {
  const { t } = useTranslation();

  return (
    <div className="grid w-full max-w-[44rem] grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
      {QUICK_ACTION_KEYS.map((key) => {
        const Icon = QUICK_ACTION_ICONS[key];
        const title = t(`chat.empty.quickActions.${key}.title`);
        const prompt = t(`chat.empty.quickActions.${key}.prompt`);
        return (
          <button
            key={key}
            type="button"
            onClick={() => onPick(prompt)}
            className="flex flex-col gap-1.5 rounded-panel border border-border/60 bg-card/60 p-3 text-left transition-colors hover:bg-muted/50"
          >
            <span className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              {title}
            </span>
            <span className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
              {prompt}
            </span>
          </button>
        );
      })}
    </div>
  );
}
