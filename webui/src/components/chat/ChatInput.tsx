/**
 * 对话输入框组件
 *
 * 支持：Enter 发送、Shift+Enter 换行、自适应高度、停止按钮
 * 图片：粘贴/拖拽/点击上传、预览、删除
 */

import { ImagePlus, Loader2, Send, Square, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';

const MIN_SEND_INTERVAL_MS = 2000;
const MAX_IMAGE_COUNT = 5;
const MAX_IMAGE_SIZE_MB = 10;
const ALLOWED_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp'];

import { uploadImage } from '@/api/images';
import type { ContentPart, ImageAttachment } from '@/api/types';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

import type { ReplyPhase } from '@/hooks/useMessages';

interface Props {
	phase: ReplyPhase;
	disabled?: boolean;
	userId?: string;
	onSend: (content: string | ContentPart[]) => void;
	onInterrupt?: () => void;
	className?: string;
}

export function ChatInput({ phase, disabled, userId, onSend, onInterrupt, className }: Props) {
	const [input, setInput] = useState('');
	const [images, setImages] = useState<ImageAttachment[]>([]);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const fileInputRef = useRef<HTMLInputElement>(null);
	const lastSendTimeRef = useRef<number>(0);

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

	return (
		<div className={cn('flex flex-col gap-2 p-4', className)}>
			{/* 图片预览区 */}
			{hasImages && (
				<div className="flex gap-2 flex-wrap">
					{images.map((img, i) => (
						<div key={i} className="relative group">
							<img
								src={img.previewUrl}
								alt={img.name}
								className={cn(
									'w-16 h-16 object-cover rounded-lg border',
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
								<div className="absolute inset-0 flex items-center justify-center bg-destructive/10 rounded-lg">
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

			{/* 输入区 */}
			<div
				className="flex items-end gap-2"
				onDrop={handleDrop}
				onDragOver={handleDragOver}
			>
				{/* 图片按钮 */}
				<Button
					size="icon"
					variant="ghost"
					className="rounded-xl shrink-0"
					onClick={() => fileInputRef.current?.click()}
					disabled={disabled || isStreaming || images.length >= MAX_IMAGE_COUNT}
					title="添加图片"
				>
					<ImagePlus className="size-4" />
				</Button>
				<input
					ref={fileInputRef}
					type="file"
					accept="image/*"
					multiple
					className="hidden"
					onChange={handleFileSelect}
				/>

				{/* 文本输入 */}
				<div className="flex-1 flex items-end bg-muted rounded-2xl border border-border focus-within:border-primary/50 transition-colors px-4 py-3">
					<textarea
						ref={textareaRef}
						value={input}
						onChange={(e) => setInput(e.target.value)}
						onKeyDown={handleKeyDown}
						onInput={handleInput}
						onPaste={handlePaste}
						placeholder={isStreaming ? '等待回复中...' : '输入消息... (Enter 发送, 粘贴/拖拽图片)'}
						disabled={disabled || isStreaming}
						rows={1}
						className="flex-1 bg-transparent resize-none outline-none text-sm leading-relaxed max-h-[150px] placeholder:text-muted-foreground disabled:opacity-50"
					/>
				</div>

				{isStreaming ? (
					<Button
						size="icon"
						variant="destructive"
						className="rounded-xl shrink-0"
						onClick={onInterrupt}
						disabled={phase === 'interrupting'}
						title="停止生成"
					>
						{phase === 'interrupting' ? (
							<Loader2 className="size-4 animate-spin" />
						) : (
							<Square className="size-4" />
						)}
					</Button>
				) : (
					<Button
						size="icon"
						className="rounded-xl shrink-0"
						onClick={handleSend}
						disabled={!canSend}
						title="发送"
					>
						<Send className="size-4" />
					</Button>
				)}
			</div>
		</div>
	);
}
