/**
 * 对话输入框组件 — v2 composer surface（Penpot「💬 Chat 主对话页 v2」）
 *
 * surface：hero 720×124 白底 r22 border #E5E7EB；thread 49.5rem。
 * footer：左 Attach(24) + 「完全访问」badge(92×26 #FFFBEB/#B45309)
 *        右 GLM-5 标签 + Mic(24) + 发送(32 圆 #215BEF)。
 * hero 下方：「选择项目」13px 链接（规划中 → toast）。
 * 支持：Enter 发送、Shift+Enter 换行、自适应高度、停止按钮、
 *       图片粘贴/拖拽/点击上传、预览、删除、发送节流。
 * ChatInputHandle.setText：外部（快捷提示词）填入文本并聚焦
 */

import { ArrowUp, ChevronDown, ImagePlus, Loader2, Mic, Square, X } from 'lucide-react';
import { useCallback, useImperativeHandle, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

const MIN_SEND_INTERVAL_MS = 2000;
const MAX_IMAGE_COUNT = 5;
const MAX_IMAGE_SIZE_MB = 10;
const ALLOWED_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

import { uploadImage } from '@/api/images';
import type { ContentPart, ImageAttachment } from '@/api/types';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import type { ReplyPhase } from '@/hooks/useMessages';

/** 命令式句柄：填入文本 + 聚焦（QuickActionCards 用） */
export interface ChatInputHandle {
	setText: (text: string) => void;
}

interface Props {
	phase: ReplyPhase;
	disabled?: boolean;
	userId?: string;
	onSend: (content: string | ContentPart[]) => void;
	onInterrupt?: () => void;
	className?: string;
	/** 空状态（hero）模式：720×124 + 底部「选择项目」 */
	hero?: boolean;
	/** React 19 ref-as-prop */
	ref?: React.Ref<ChatInputHandle>;
}

function readModelName(): string {
	try {
		return localStorage.getItem('llm_model_name') || 'GLM-5';
	} catch {
		return 'GLM-5';
	}
}

export function ChatInput({ phase, disabled, userId, onSend, onInterrupt, className, hero, ref }: Props) {
	const { t } = useTranslation();
	const [input, setInput] = useState('');
	const [images, setImages] = useState<ImageAttachment[]>([]);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const lastSendTimeRef = useRef<number>(0);

	// ---- 命令式填值 ----
	useImperativeHandle(ref, () => ({
		setText: (text: string) => {
			setInput(text);
			requestAnimationFrame(() => {
				const el = textareaRef.current;
				if (!el) return;
				el.focus();
				el.setSelectionRange(text.length, text.length);
				el.style.height = 'auto';
				el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
			});
		},
	}), []);

	// ---- 图片处理 ----

	const validateFile = useCallback((file: File): string | null => {
		if (!ALLOWED_TYPES.includes(file.type)) {
			return `不支持的文件类型: ${file.type}`;
		}
		if (file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024) {
			return `文件大小超过 ${MAX_IMAGE_SIZE_MB}MB 限制`;
		}
		return null;
	}, []);

	const addImages = useCallback(async (files: FileList | File[]) => {
		const fileArr = Array.from(files);
		const remaining = MAX_IMAGE_COUNT - images.length;
		if (remaining <= 0) return;

		const toAdd = fileArr.slice(0, remaining);
		const newImages: ImageAttachment[] = toAdd.map(file => ({
			previewUrl: URL.createObjectURL(file),
			key: '',
			status: 'uploading' as const,
			name: file.name,
		}));

		setImages(prev => [...prev, ...newImages]);

		// 逐个上传
		for (let i = 0; i < toAdd.length; i++) {
			const file = toAdd[i];
			const idx = images.length + i;
			const err = validateFile(file);
			if (err) {
				setImages(prev => prev.map((img, j) =>
					j === idx ? { ...img, status: 'error' as const, error: err } : img,
				));
				continue;
			}
			try {
				const key = await uploadImage(file, userId);
				setImages(prev => prev.map((img, j) =>
					j === idx ? { ...img, key, status: 'done' as const } : img,
				));
			} catch (e) {
				setImages(prev => prev.map((img, j) =>
					j === idx ? { ...img, status: 'error' as const, error: String(e) } : img,
				));
			}
		}
	}, [images.length, userId, validateFile]);

	const removeImage = useCallback((index: number) => {
		setImages(prev => {
			const removed = prev[index];
			if (removed.previewUrl) URL.revokeObjectURL(removed.previewUrl);
			return prev.filter((_, i) => i !== index);
		});
	}, []);

	// ---- 粘贴 ----

	const handlePaste = useCallback((e: React.ClipboardEvent) => {
		const files = e.clipboardData?.files;
		if (files && files.length > 0) {
			const imageFiles = Array.from(files).filter(f => f.type.startsWith('image/'));
			if (imageFiles.length > 0) {
				e.preventDefault();
				addImages(imageFiles);
			}
		}
	}, [addImages]);

	// ---- 拖拽 ----

	const handleDrop = useCallback((e: React.DragEvent) => {
		e.preventDefault();
		e.stopPropagation();
		const files = e.dataTransfer?.files;
		if (files && files.length > 0) {
			const imageFiles = Array.from(files).filter(f => f.type.startsWith('image/'));
			if (imageFiles.length > 0) {
				addImages(imageFiles);
			}
		}
	}, [addImages]);

	const handleDragOver = useCallback((e: React.DragEvent) => {
		e.preventDefault();
		e.stopPropagation();
	}, []);

	// ---- 文件选择 ----

	const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
		const files = e.target.files;
		if (files && files.length > 0) {
			addImages(files);
		}
		// 清空 input 允许重复选择同一文件
		if (fileInputRef.current) fileInputRef.current.value = '';
	}, [addImages]);

	// ---- 发送 ----

	const hasUploading = images.some(img => img.status === 'uploading');
	const hasImages = images.length > 0;

	const handleSend = useCallback(() => {
		const content = input.trim();
		if ((!content && !hasImages) || phase !== 'idle') return;
		if (hasUploading) return; // 等待上传完成

		// 发送节流
		const now = Date.now();
		if (now - lastSendTimeRef.current < MIN_SEND_INTERVAL_MS) return;
		lastSendTimeRef.current = now;

		if (hasImages) {
			// 多模态消息
			const parts: ContentPart[] = [];
			if (content) parts.push({ type: 'text', text: content });
			for (const img of images) {
				if (img.key && img.status === 'done') {
					parts.push({ type: 'image', key: img.key });
				}
			}
			onSend(parts);
		} else {
			// 纯文本
			onSend(content);
		}

		setInput('');
		setImages([]);
		if (textareaRef.current) textareaRef.current.style.height = 'auto';
	}, [input, phase, onSend, hasImages, hasUploading, images]);

	const handleKeyDown = useCallback(
		(e: React.KeyboardEvent) => {
			if (e.key === 'Enter' && !e.shiftKey) {
				e.preventDefault();
				handleSend();
			}
		},
		[handleSend],
	);

	const handleInput = useCallback(() => {
		const el = textareaRef.current;
		if (!el) return;
		el.style.height = 'auto';
		el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
	}, []);

	const isStreaming = phase === 'streaming' || phase === 'interrupting';
	const canSend = (input.trim() || hasImages) && !hasUploading && !isStreaming && !disabled;

	const modelName = readModelName();

	const placeholder = isStreaming
		? t('chat.waiting', { defaultValue: '等待回复中...' })
		: hero
			? t('chat.placeholderHero', { defaultValue: '有什么可以帮你的？' })
			: t('chat.placeholder', { defaultValue: '输入消息... (Enter 发送, 粘贴/拖拽图片)' });

	return (
		<div className={cn('flex w-full flex-col', className)}>
			{/* composer surface */}
			<div
				className={cn(
					'relative mx-auto flex w-full flex-col border border-border bg-background transition-all duration-200',
					'focus-within:border-[#D1D5DB]',
					hero ? 'max-w-[720px] rounded-[22px]' : 'max-w-[49.5rem] rounded-[22px]',
					hero && 'min-h-[124px]',
				)}
				onDrop={handleDrop}
				onDragOver={handleDragOver}
			>
				{/* 图片预览区 */}
				{hasImages && (
					<div className="flex flex-wrap gap-2 px-4 pt-3">
						{images.map((img, i) => (
							<div key={i} className="relative group">
								<img
									src={img.previewUrl}
									alt={img.name}
									className={cn(
										'w-16 h-16 object-cover rounded-compact border',
										img.status === 'uploading' && 'opacity-50',
										img.status === 'error' && 'border-destructive',
									)}
								/>
								{img.status === 'uploading' && (
									<div className="absolute inset-0 flex items-center justify-center">
										<Loader2 className="size-4 animate-spin text-primary" />
									</div>
								)}
								{img.status === 'error' && (
									<div className="absolute inset-0 flex items-center justify-center bg-destructive/10 rounded-compact">
										<span className="text-[10px] text-destructive">失败</span>
									</div>
								)}
								<button
									onClick={() => removeImage(i)}
									className="absolute -top-1.5 -right-1.5 size-5 rounded-full bg-foreground/80 text-background flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
									title="移除图片"
								>
									<X className="size-3" />
								</button>
							</div>
						))}
					</div>
				)}

				{/* 文本输入 —— placeholder 15/400 #9CA3AF */}
				<div className="relative min-w-0 px-4 pt-[18px]">
					<textarea
						ref={textareaRef}
						value={input}
						onChange={(e) => setInput(e.target.value)}
						onKeyDown={handleKeyDown}
						onInput={handleInput}
						onPaste={handlePaste}
						placeholder={placeholder}
						disabled={disabled || isStreaming}
						rows={1}
						className="block w-full resize-none bg-transparent text-[15px] leading-relaxed text-foreground outline-none placeholder:text-text-tertiary focus:outline-none disabled:cursor-not-allowed disabled:opacity-50 max-h-[150px]"
					/>
				</div>

				{/* footer */}
				<div className="mt-auto flex flex-nowrap items-center gap-x-2 px-4 pb-5 pt-3">
					{/* 左：Attach + 完全访问 badge */}
					<div className="flex min-w-0 flex-1 basis-0 items-center gap-2">
						<input
							ref={fileInputRef}
							type="file"
							accept="image/*"
							multiple
							className="hidden"
							onChange={handleFileSelect}
						/>
						<Button
							type="button"
							size="icon"
							variant="ghost"
							className="size-6 rounded-full text-text-secondary hover:bg-row-hover hover:text-foreground"
							onClick={() => fileInputRef.current?.click()}
							disabled={disabled || isStreaming || images.length >= MAX_IMAGE_COUNT}
							title={t('chat.addImage', { defaultValue: '添加图片' })}
						>
							<ImagePlus className="size-5" />
						</Button>
						<button
							type="button"
							onClick={() =>
								toast.info(t('chat.fullAccessPlanned', { defaultValue: '「完全访问」权限模式 — 规划中' }))
							}
							className="flex h-[26px] items-center gap-1 rounded-full bg-warning-light px-3 text-[12px] font-semibold text-warning-text transition-opacity hover:opacity-85"
						>
							{t('chat.fullAccess', { defaultValue: '完全访问' })}
							<ChevronDown className="size-3" />
						</button>
					</div>

					{/* 右：模型 + Mic + 发送 */}
					<div className="ml-auto flex min-w-0 items-center justify-end gap-2.5">
						<span className="hidden shrink-0 text-[12px] font-normal text-text-secondary sm:inline">
							{modelName}
						</span>
						<Button
							type="button"
							size="icon"
							variant="ghost"
							className="size-6 rounded-full text-text-secondary hover:bg-row-hover hover:text-foreground"
							onClick={() =>
								toast.info(t('chat.voicePlanned', { defaultValue: '语音输入 — 规划中' }))
							}
							title={t('chat.voiceInput', { defaultValue: '语音输入' })}
						>
							<Mic className="size-5" />
						</Button>
						{isStreaming ? (
							<Button
								type="button"
								size="icon"
								className="size-8 rounded-full border border-border bg-background text-foreground hover:bg-muted disabled:text-muted-foreground"
								onClick={onInterrupt}
								disabled={phase === 'interrupting'}
								title={t('chat.stop', { defaultValue: '停止生成' })}
							>
								{phase === 'interrupting' ? (
									<Loader2 className="size-4 animate-spin" />
								) : (
									<Square className="size-3 fill-current stroke-current" />
								)}
							</Button>
						) : (
							<Button
								type="button"
								size="icon"
								className={cn(
									'size-8 rounded-full bg-primary text-primary-foreground hover:bg-primary-hover',
									'disabled:bg-muted disabled:text-text-tertiary disabled:shadow-none',
									canSend && 'hover:scale-[1.03] active:scale-95',
								)}
								onClick={handleSend}
								disabled={!canSend}
								title={t('chat.send', { defaultValue: '发送' })}
							>
								<ArrowUp className="size-4" />
							</Button>
						)}
					</div>
				</div>
			</div>

			{/* hero 下方：选择项目 */}
			{hero && (
				<div className="mx-auto w-full max-w-[720px]">
					<button
						type="button"
						onClick={() =>
							toast.info(t('chat.projectPlanned', { defaultValue: '项目选择 — 规划中' }))
						}
						className="mt-3.5 pl-4 text-[13px] font-normal text-text-secondary transition-colors hover:text-foreground"
					>
						{t('chat.selectProject', { defaultValue: '选择项目' })}
					</button>
				</div>
			)}
		</div>
	);
}
