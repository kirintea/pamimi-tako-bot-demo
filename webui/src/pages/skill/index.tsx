/**
 * Skill 管理页面 — Penpot v2 设计规格
 *
 * 卡片网格 + 过滤 pills + 安装对话框（表单 / 粘贴双 tab）
 */

import { Loader2, Plus, Trash2, Wrench } from 'lucide-react';
import { useEffect, useState, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { skillApi } from '@/api/skill';
import type { SkillInfo, CreateSkillRequest } from '@/api/types';
import { UploadTab } from '@/components/skill/UploadTab';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_DESC_LEN = 150;

const SKILL_MD_EXAMPLE = `---
name: my-skill
description: 示例技能 — 演示 SKILL.md frontmatter 格式
---

# 使用说明

在此编写技能的执行步骤、约束与示例。`;

type FilterKey = 'all' | 'enabled' | 'disabled';

/** 来源类型（UI-only，后端不区分） */
type SourceType = 'market' | 'local' | 'git';

// ---------------------------------------------------------------------------
// SkillPage
// ---------------------------------------------------------------------------

export function SkillPage() {
  const { t } = useTranslation();
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [filter, setFilter] = useState<FilterKey>('all');

  // ---- data loading ----

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      setSkills(await skillApi.list());
    } catch (e) {
      console.error('加载 Skill 列表失败:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  // ---- toggle enable/disable (optimistic) ----

  const handleToggle = useCallback(
    async (skill: SkillInfo, nextEnabled: boolean) => {
      setSkills((prev) =>
        prev.map((s) => (s.id === skill.id ? { ...s, enabled: nextEnabled } : s)),
      );
      try {
        await skillApi.update(skill.id, { enabled: nextEnabled });
        toast.success(
          nextEnabled
            ? t('skill.toastEnabled', { defaultValue: '已启用' })
            : t('skill.toastDisabled', { defaultValue: '已停用' }),
        );
      } catch {
        // revert
        setSkills((prev) =>
          prev.map((s) => (s.id === skill.id ? { ...s, enabled: !nextEnabled } : s)),
        );
        toast.error(t('skill.toastToggleFailed', { defaultValue: '操作失败' }));
      }
    },
    [t],
  );

  // ---- delete ----

  const handleDelete = useCallback(
    async (id: string) => {
      if (!confirm(t('skill.confirmDelete', { defaultValue: '确定删除此技能？' }))) return;
      try {
        await skillApi.delete(id);
        toast.success(t('skill.toastDeleted', { defaultValue: '已删除' }));
        await loadSkills();
      } catch {
        toast.error(t('skill.toastDeleteFailed', { defaultValue: '删除失败' }));
      }
    },
    [t, loadSkills],
  );

  // ---- filter ----

  const filteredSkills = skills.filter((s) => {
    if (filter === 'enabled') return s.enabled !== false;
    if (filter === 'disabled') return s.enabled === false;
    return true;
  });

  // ---- render ----

  return (
    <div className="h-full overflow-y-auto px-12 pt-[52px] pb-12">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-[#111928] dark:text-[#f5f5f9]">
            {t('skill.title', { defaultValue: '技能 Skill' })}
          </h1>
          <p className="mt-1 text-[13px] text-[#6B7280] dark:text-[#9ca3af]">
            {t('skill.subtitle', {
              defaultValue: '装配给 Agent 的可复用能力包，支持版本管理与启停',
            })}
          </p>
        </div>
        <Button
          className="h-9 w-36 self-start rounded-[10px] bg-primary text-[13px] font-semibold text-white hover:bg-primary-hover"
          onClick={() => setShowAdd(true)}
        >
          <Plus className="size-4" />
          {t('skill.install', { defaultValue: '安装技能' })}
        </Button>
      </div>

      {/* Filter pills */}
      <div className="mt-10 flex gap-2">
        {(
          [
            { key: 'all', label: t('skill.filterAll', { defaultValue: '全部' }) },
            { key: 'enabled', label: t('skill.filterEnabled', { defaultValue: '已启用' }) },
            { key: 'disabled', label: t('skill.filterDisabled', { defaultValue: '已停用' }) },
          ] as const
        ).map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setFilter(item.key)}
            className={cn(
              'h-7 rounded-full px-4 text-xs font-semibold transition-colors',
              filter === item.key
                ? 'bg-primary text-white'
                : 'bg-[#F3F4F6] dark:bg-[#383838] text-[#4B5563] hover:bg-[#E5E7EB]',
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-32">
          <Spinner className="size-5 text-muted-foreground" />
        </div>
      ) : filteredSkills.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-32 text-[#9CA3AF]">
          <Wrench className="size-8 mb-3 opacity-40" />
          <span className="text-sm">
            {t('skill.empty', { defaultValue: '暂无技能配置' })}
          </span>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-6 mt-6">
          {filteredSkills.map((skill) => (
            <SkillCard
              key={skill.id}
              skill={skill}
              onToggle={handleToggle}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      {/* Install dialog */}
      {showAdd && (
        <InstallDialog
          onClose={() => setShowAdd(false)}
          onInstalled={async () => {
            setShowAdd(false);
            await loadSkills();
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SkillCard
// ---------------------------------------------------------------------------

interface SkillCardProps {
  skill: SkillInfo;
  onToggle: (skill: SkillInfo, enabled: boolean) => void;
  onDelete: (id: string) => void;
}

function SkillCard({ skill, onToggle, onDelete }: SkillCardProps) {
  const { t } = useTranslation();
  const enabled = skill.enabled !== false;
  const versionLabel = skill.version ? `v${skill.version}` : 'v—';

  return (
    <div
      className={cn(
        'group relative min-h-[156px] rounded-[16px] border border-border bg-white dark:bg-[#303030] p-6 transition-shadow',
        'hover:shadow-[0_1px_2px_rgba(24,25,28,0.05)]',
      )}
    >
      {/* Top row */}
      <div className="mt-0.5 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div
            className={cn(
              'flex size-9 items-center justify-center rounded-[10px]',
              enabled ? 'bg-[#E8F0FE] text-primary' : 'bg-[#F3F4F6] dark:bg-[#383838] text-[#9CA3AF]',
            )}
          >
            <Wrench className="size-4" />
          </div>
          <span className="text-[15px] font-semibold text-[#111928] dark:text-[#f5f5f9]">
            {skill.display_name || skill.name}
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Delete — hover only */}
          <button
            type="button"
            onClick={() => onDelete(skill.id)}
            className={cn(
              'flex size-7 items-center justify-center rounded-md text-[#9CA3AF]',
              'opacity-0 transition-opacity hover:bg-[#F3F4F6] dark:bg-[#383838] group-hover:opacity-100',
            )}
            title={t('skill.delete', { defaultValue: '删除' })}
          >
            <Trash2 className="size-3.5" />
          </button>
          {/* Version badge */}
          <span className="flex h-[22px] min-w-[56px] items-center justify-center rounded-[8px] bg-[#F3F4F6] dark:bg-[#383838] px-2 text-[11px] font-semibold text-[#6B7280] dark:text-[#9ca3af]">
            {versionLabel}
          </span>
        </div>
      </div>

      {/* Description — truncated, full text on hover */}
      <p
        className="mt-4 line-clamp-3 text-xs leading-5 text-[#9CA3AF]"
        title={skill.description || undefined}
      >
        {skill.description || ' '}
      </p>

      {/* Bottom row */}
      <div className="absolute inset-x-6 bottom-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span
            className={cn(
              'size-2 rounded-full',
              enabled ? 'bg-[#10B981]' : 'bg-[#9CA3AF]',
            )}
          />
          <span
            className={cn(
              'text-xs font-semibold',
              enabled ? 'text-[#047857]' : 'text-[#6B7280] dark:text-[#9ca3af]',
            )}
          >
            {enabled
              ? t('skill.statusEnabled', { defaultValue: '已启用' })
              : t('skill.statusDisabled', { defaultValue: '已停用' })}
          </span>
        </div>

        <Switch
          checked={enabled}
          onCheckedChange={(val) => onToggle(skill, val)}
          className="h-5 w-9"
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// InstallDialog — three tabs (form / paste / upload)
// ---------------------------------------------------------------------------

interface InstallDialogProps {
  onClose: () => void;
  onInstalled: () => Promise<void>;
}

type TabKey = 'form' | 'paste' | 'upload';

function InstallDialog({ onClose, onInstalled }: InstallDialogProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<TabKey>('form');
  const [submitting, setSubmitting] = useState(false);

  // form fields
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [sourceType, setSourceType] = useState<SourceType>('local');
  const [trigger, setTrigger] = useState('');
  const [iconName, setIconName] = useState('');

  // paste field
  const [pasteContent, setPasteContent] = useState(SKILL_MD_EXAMPLE);

  // ---- derive name from frontmatter ----

  const deriveName = useCallback((text: string): string | null => {
    const m = text.match(/^name:\s*(\S+)/m);
    return m ? m[1] : null;
  }, []);

  // ---- submit ----

  const handleSubmit = useCallback(async () => {
    let skillName = name.trim();
    let skillDesc = description.trim();
    let skillMarkdown: string | undefined;

    if (tab === 'paste') {
      const derived = deriveName(pasteContent);
      skillName = derived ?? skillName;
      // also try to derive description
      const descMatch = pasteContent.match(/^description:\s*(.+)/m);
      if (!skillDesc && descMatch) skillDesc = descMatch[1].trim();
      skillMarkdown = pasteContent;
      if (!skillName) {
        toast.error(
          t('skill.toastNoName', {
            defaultValue: '无法解析 name，请使用表单页填写技能名称',
          }),
        );
        return;
      }
    }

    if (!skillName) {
      toast.error(t('skill.toastNameRequired', { defaultValue: '请输入技能名称' }));
      return;
    }

    setSubmitting(true);
    try {
      await skillApi.create({
        name: skillName,
        description: skillDesc || undefined,
        markdown: skillMarkdown,
        tags: trigger.trim() ? [trigger.trim()] : undefined,
      });
      toast.success(t('skill.toastInstalled', { defaultValue: '技能已安装' }));
      await onInstalled();
    } catch {
      toast.error(t('skill.toastInstallFailed', { defaultValue: '安装失败' }));
    } finally {
      setSubmitting(false);
    }
  }, [name, description, tab, pasteContent, trigger, deriveName, onInstalled, t]);

  const canSubmit =
    tab === 'form' ? name.trim().length > 0 : pasteContent.trim().length > 0;

  // ---- render ----

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#111928]/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-[680px] max-h-[90vh] overflow-y-auto rounded-[22px] bg-card p-8 shadow-panel"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Title */}
        <h2 className="text-lg font-semibold">
          {t('skill.dialogTitle', { defaultValue: '添加技能' })}
        </h2>
        <p className="mt-1 text-[13px] text-[#6B7280] dark:text-[#9ca3af]">
          {t('skill.dialogSubtitle', {
            defaultValue: '安装可复用技能包，装配后可在对话中调用',
          })}
        </p>

        {/* Tab row */}
        <div className="mt-5 flex h-8 w-[330px] gap-1 rounded-[8px] bg-[#F3F4F6] dark:bg-[#383838] p-1">
          {(
            [
              { key: 'form' as TabKey, label: t('skill.tabForm', { defaultValue: '表单' }) },
              { key: 'paste' as TabKey, label: t('skill.tabPaste', { defaultValue: '粘贴' }) },
              { key: 'upload' as TabKey, label: t('skill.tabUpload', { defaultValue: '上传压缩包' }) },
            ] as const
          ).map((tb) => (
            <button
              key={tb.key}
              type="button"
              onClick={() => setTab(tb.key)}
              className={cn(
                'flex-1 rounded-[6px] text-[13px] transition-colors',
                tab === tb.key
                  ? 'bg-white dark:bg-[#303030] font-semibold text-primary shadow-sm'
                  : 'text-[#6B7280] dark:text-[#9ca3af]',
              )}
            >
              {tb.label}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {tab === 'form' && (
          <FormTab
            name={name}
            setName={setName}
            description={description}
            setDescription={setDescription}
            sourceType={sourceType}
            setSourceType={setSourceType}
            trigger={trigger}
            setTrigger={setTrigger}
            iconName={iconName}
            setIconName={setIconName}
          />
        )}
        {tab === 'paste' && (
          <PasteTab content={pasteContent} setContent={setPasteContent} />
        )}
        {tab === 'upload' && (
          <UploadTab
            onInstall={async (data: CreateSkillRequest) => {
              setSubmitting(true);
              try {
                await skillApi.create(data);
                toast.success(t('skill.toastInstalled', { defaultValue: '技能已安装' }));
                await onInstalled();
              } catch (e) {
                toast.error(t('skill.toastInstallFailed', { defaultValue: '安装失败' }));
                throw e;
              } finally {
                setSubmitting(false);
              }
            }}
            installing={submitting}
          />
        )}

        {/* Footer — hidden for upload tab (it has its own install button) */}
        {tab !== 'upload' && (
          <div className="mt-6 flex justify-end gap-3">
            <Button
              variant="outline"
              className="h-9 w-[110px] rounded-[8px] border border-border text-[14px] font-semibold hover:bg-[#F3F4F6] dark:bg-[#383838]"
              onClick={onClose}
            >
              {t('common.cancel', { defaultValue: '取消' })}
            </Button>
            <Button
              className="h-9 w-[110px] rounded-[8px] bg-primary text-[14px] font-semibold text-white hover:bg-primary-hover"
              disabled={!canSubmit || submitting}
              onClick={handleSubmit}
            >
              {submitting && <Loader2 className="mr-1 size-3.5 animate-spin" />}
              {t('skill.installBtn', { defaultValue: '安装' })}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// FormTab
// ---------------------------------------------------------------------------

interface FormTabProps {
  name: string;
  setName: (v: string) => void;
  description: string;
  setDescription: (v: string) => void;
  sourceType: SourceType;
  setSourceType: (v: SourceType) => void;
  trigger: string;
  setTrigger: (v: string) => void;
  iconName: string;
  setIconName: (v: string) => void;
}

function FormTab({
  name,
  setName,
  description,
  setDescription,
  sourceType,
  setSourceType,
  trigger,
  setTrigger,
  iconName,
  setIconName,
}: FormTabProps) {
  const { t } = useTranslation();

  const sourceOptions = [
    { key: 'market' as SourceType, label: t('skill.sourceMarket', { defaultValue: '市场' }) },
    { key: 'local' as SourceType, label: t('skill.sourceLocal', { defaultValue: '本地文件' }) },
    { key: 'git' as SourceType, label: t('skill.sourceGit', { defaultValue: 'Git 仓库' }) },
  ];

  return (
    <div className="mt-6 space-y-4">
      {/* 技能名称 */}
      <Field label={t('skill.fieldName', { defaultValue: '技能名称' })} required>
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-skill"
          className="h-10 w-full rounded-[12px] border border-border bg-white dark:bg-[#303030] px-3 text-[13px] placeholder:text-[#9CA3AF] dark:placeholder:text-[#6b7280]"
        />
      </Field>

      {/* 描述 */}
      <Field label={t('skill.fieldDesc', { defaultValue: '描述' })}>
        <textarea
          value={description}
          onChange={(e) => {
            if (e.target.value.length <= MAX_DESC_LEN) setDescription(e.target.value);
          }}
          rows={2}
          placeholder={t('skill.fieldDescPlaceholder', { defaultValue: '简要说明技能用途' })}
          className="h-16 w-full resize-none rounded-[12px] border border-border bg-white dark:bg-[#303030] px-3 py-2 text-[13px] placeholder:text-[#9CA3AF] dark:placeholder:text-[#6b7280] outline-none"
        />
        <span className="mt-1 block text-xs text-[#9CA3AF]">
          ≤{MAX_DESC_LEN} {t('skill.chars', { defaultValue: '字符' })}
        </span>
      </Field>

      {/* 来源类型 */}
      <Field label={t('skill.fieldSource', { defaultValue: '来源类型' })}>
        <div className="flex h-8 gap-1 rounded-[8px] bg-[#F3F4F6] dark:bg-[#383838] p-1">
          {sourceOptions.map((opt) => (
            <button
              key={opt.key}
              type="button"
              onClick={() => setSourceType(opt.key)}
              className={cn(
                'flex-1 rounded-[6px] text-[13px] transition-colors',
                sourceType === opt.key
                  ? 'bg-white dark:bg-[#303030] font-semibold text-primary shadow-sm'
                  : 'text-[#6B7280] dark:text-[#9ca3af]',
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <span className="mt-1 block text-xs text-[#9CA3AF]">
          {t('skill.sourceHint', {
            defaultValue: '市场/Git 来源规划中，当前以本地内容安装',
          })}
        </span>
      </Field>

      {/* 触发词 */}
      <Field label={t('skill.fieldTrigger', { defaultValue: '触发词/命令' })}>
        <Input
          value={trigger}
          onChange={(e) => setTrigger(e.target.value)}
          placeholder="run-report"
          className="h-10 w-full rounded-[12px] border border-border bg-white dark:bg-[#303030] px-3 text-[13px] placeholder:text-[#9CA3AF] dark:placeholder:text-[#6b7280]"
        />
      </Field>

      {/* 图标名称 */}
      <Field label={t('skill.fieldIcon', { defaultValue: '图标名称' })}>
        <Input
          value={iconName}
          onChange={(e) => setIconName(e.target.value)}
          placeholder="wrench"
          className="h-10 w-full rounded-[12px] border border-border bg-white dark:bg-[#303030] px-3 text-[13px] placeholder:text-[#9CA3AF] dark:placeholder:text-[#6b7280]"
        />
        <span className="mt-1 block text-xs text-[#9CA3AF]">
          {t('skill.iconHint', { defaultValue: 'lucide 图标名，规划中生效' })}
        </span>
      </Field>

      {/* Note strip */}
      <div className="flex h-11 items-center rounded-[12px] bg-[#EFF6FF] px-4 text-xs text-[#3B82F6]">
        {t('skill.noteSkillMd', {
          defaultValue: '技能将以 SKILL.md 形式注入 Agent 上下文',
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PasteTab
// ---------------------------------------------------------------------------

interface PasteTabProps {
  content: string;
  setContent: (v: string) => void;
}

function PasteTab({ content, setContent }: PasteTabProps) {
  const { t } = useTranslation();

  return (
    <div className="mt-6 space-y-4">
      <Field label={t('skill.fieldSkillMd', { defaultValue: 'SKILL.md 内容' })}>
        <div className="h-[430px] rounded-[12px] bg-[#0D1117] p-4">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="h-full w-full resize-none bg-transparent font-mono text-[13px] leading-5 text-[#E6EDF3] outline-none"
            spellCheck={false}
          />
        </div>
      </Field>

      {/* Blue note */}
      <div className="rounded-[12px] bg-[#EFF6FF] px-4 py-3 text-xs text-[#3B82F6]">
        {t('skill.pasteHint', {
          defaultValue:
            '粘贴 SKILL.md 完整内容（含 YAML frontmatter）。解析 name/description 自动填入元数据，规划中：自动解析',
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Field — shared label + children wrapper
// ---------------------------------------------------------------------------

interface FieldProps {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}

function Field({ label, required, children }: FieldProps) {
  return (
    <div>
      <label className="mb-1.5 block text-[13px] font-semibold text-[#374151] dark:text-gray-300">
        {label}
        {required && <span className="ml-0.5 text-red-500">*</span>}
      </label>
      {children}
    </div>
  );
}