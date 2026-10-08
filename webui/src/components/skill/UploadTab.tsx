/**
 * UploadTab — 上传压缩包安装 Skill
 *
 * 支持拖拽 / 点击选择 .zip / .tar / .tar.gz / .tgz 文件，
 * 预览文件列表、SKILL.md 内容和元数据，编辑名称后安装。
 */

import * as React from 'react';
import {
  Upload,
  FileArchive,
  FileText,
  FolderOpen,
  X,
  Check,
  AlertCircle,
} from 'lucide-react';

import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Spinner } from '@/components/ui/spinner';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Empty, EmptyHeader, EmptyTitle, EmptyDescription } from '@/components/ui/empty';

import {
  decompressArchive,
  findSkillMd,
  parseFrontmatter,
  buildFileTree,
  fileTreeToString,
  isPathSafe,
  type ArchiveFile,
  type FileTreeNode,
  type SkillMetadata,
} from '@/utils/archive';
import type { CreateSkillRequest } from '@/api/types';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const ACCEPTED_EXTENSIONS = ['.zip', '.tar', '.tar.gz', '.tgz'];
const MAX_FILE_SIZE = 50 * 1024 * 1024; // 50 MB

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface UploadTabProps {
  /** 安装回调 — 由父组件调用 skillApi.create */
  onInstall: (data: CreateSkillRequest) => Promise<void>;
  /** 安装中状态（父组件控制） */
  installing?: boolean;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function UploadTab({ onInstall, installing = false }: UploadTabProps) {
  // --- State ---
  const [file, setFile] = React.useState<File | null>(null);
  const [archiveFiles, setArchiveFiles] = React.useState<ArchiveFile[]>([]);
  const [fileTree, setFileTree] = React.useState<FileTreeNode[]>([]);
  const [skillMd, setSkillMd] = React.useState<{ path: string; content: string } | null>(null);
  const [metadata, setMetadata] = React.useState<SkillMetadata>({});
  const [editName, setEditName] = React.useState('');
  const [error, setError] = React.useState<string | null>(null);
  const [parsing, setParsing] = React.useState(false);
  const [dragOver, setDragOver] = React.useState(false);

  const inputRef = React.useRef<HTMLInputElement>(null);

  // --- Helpers ---

  const reset = React.useCallback(() => {
    setFile(null);
    setArchiveFiles([]);
    setFileTree([]);
    setSkillMd(null);
    setMetadata({});
    setEditName('');
    setError(null);
    setParsing(false);
  }, []);

  const validateFile = React.useCallback((f: File): string | null => {
    const nameLower = f.name.toLowerCase();
    const valid = ACCEPTED_EXTENSIONS.some((ext) => nameLower.endsWith(ext));
    if (!valid) {
      return `不支持的文件格式。请上传 ${ACCEPTED_EXTENSIONS.join(', ')} 文件`;
    }
    if (f.size > MAX_FILE_SIZE) {
      return `文件过大（${(f.size / 1024 / 1024).toFixed(1)} MB），最大允许 50 MB`;
    }
    return null;
  }, []);

  const processFile = React.useCallback(
    async (f: File) => {
      reset();
      setError(null);

      const validationError = validateFile(f);
      if (validationError) {
        setError(validationError);
        return;
      }

      setFile(f);
      setParsing(true);

      try {
        const files = await decompressArchive(f);

        // 额外路径安全检查
        for (const af of files) {
          if (!isPathSafe(af.path)) {
            throw new Error(`压缩包包含不安全的路径: ${af.path}`);
          }
        }

        const tree = buildFileTree(files);
        const found = findSkillMd(files);
        const meta = found ? parseFrontmatter(found.content) : {};

        setArchiveFiles(files);
        setFileTree(tree);
        setSkillMd(found);
        setMetadata(meta);
        setEditName(meta.name || f.name.replace(/\.(zip|tar\.gz|tgz|tar)$/i, ''));
      } catch (e) {
        setError(e instanceof Error ? e.message : '解析压缩包失败');
      } finally {
        setParsing(false);
      }
    },
    [reset, validateFile],
  );

  // --- Event Handlers ---

  const handleFileChange = React.useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selected = e.target.files?.[0];
      if (selected) processFile(selected);
      // 重置 input 以允许重复选择同一文件
      if (inputRef.current) inputRef.current.value = '';
    },
    [processFile],
  );

  const handleDrop = React.useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const dropped = e.dataTransfer.files[0];
      if (dropped) processFile(dropped);
    },
    [processFile],
  );

  const handleDragOver = React.useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(true);
  }, []);

  const handleDragLeave = React.useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
  }, []);

  const handleInstall = React.useCallback(async () => {
    if (!editName.trim()) {
      setError('请输入 Skill 名称');
      return;
    }

    // 构建 files 映射
    const filesMap: Record<string, string> = {};
    for (const af of archiveFiles) {
      filesMap[af.path] = new TextDecoder().decode(af.content);
    }

    const data: CreateSkillRequest = {
      name: editName.trim(),
      display_name: editName.trim(),
      description: metadata.description,
      markdown: skillMd?.content,
      files: Object.keys(filesMap).length > 0 ? filesMap : undefined,
    };

    try {
      await onInstall(data);
      reset();
    } catch {
      // 错误由父组件处理
    }
  }, [editName, archiveFiles, metadata, skillMd, onInstall, reset]);

  // --- Render ---

  // 未选择文件：显示上传区域
  if (!file) {
    return (
      <div className="flex flex-col gap-4">
        {/* 拖拽上传区域 */}
        <div
          role="button"
          tabIndex={0}
          className={cn(
            'relative flex flex-col items-center justify-center gap-3 rounded-xl border-2 border-dashed p-10 transition-colors cursor-pointer',
            'hover:border-primary/50 hover:bg-primary/5',
            dragOver
              ? 'border-primary bg-primary/10'
              : 'border-muted-foreground/25',
          )}
          onClick={() => inputRef.current?.click()}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault();
              inputRef.current?.click();
            }
          }}
          onDrop={handleDrop}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
        >
          <Upload className="size-10 text-muted-foreground" />
          <div className="text-center">
            <p className="text-sm font-medium">
              将压缩包拖拽到此处，或点击选择文件
            </p>
            <p className="mt-1 text-xs text-muted-foreground">
              支持 {ACCEPTED_EXTENSIONS.join(' / ')} 格式，最大 50 MB
            </p>
          </div>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(',')}
            className="hidden"
            onChange={handleFileChange}
          />
        </div>

        {error && (
          <Alert variant="destructive">
            <AlertCircle />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
      </div>
    );
  }

  // 解析中
  if (parsing) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16">
        <Spinner className="size-8" />
        <p className="text-sm text-muted-foreground">正在解析压缩包...</p>
      </div>
    );
  }

  // 解析失败
  if (error && archiveFiles.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <Alert variant="destructive">
          <AlertCircle />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
        <Button variant="outline" onClick={reset} className="self-start">
          重新选择
        </Button>
      </div>
    );
  }

  // 预览与安装
  return (
    <div className="flex flex-col gap-4">
      {/* 文件信息栏 */}
      <div className="flex items-center gap-2">
        <FileArchive className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium truncate">{file.name}</span>
        <Badge variant="secondary" className="text-xs">
          {(file.size / 1024).toFixed(1)} KB
        </Badge>
        <Badge variant="secondary" className="text-xs">
          {archiveFiles.length} 个文件
        </Badge>
        <div className="flex-1" />
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={reset}
          tooltip="移除文件"
        >
          <X className="size-4" />
        </Button>
      </div>

      {/* 名称编辑 */}
      <div className="flex flex-col gap-1.5">
        <label htmlFor="skill-name" className="text-sm font-medium">
          Skill 名称
        </label>
        <Input
          id="skill-name"
          value={editName}
          onChange={(e) => setEditName(e.target.value)}
          placeholder="输入 Skill 名称"
        />
      </div>

      {/* 元数据 */}
      {metadata.description && (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-muted-foreground">描述</span>
          <p className="text-sm">{metadata.description}</p>
        </div>
      )}

      {/* 文件树预览 */}
      {fileTree.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            <FolderOpen className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">文件结构</span>
          </div>
          <pre className="max-h-48 overflow-auto rounded-lg border bg-muted/50 p-3 text-xs leading-relaxed">
            {fileTreeToString(fileTree)}
          </pre>
        </div>
      )}

      {/* SKILL.md 预览 */}
      {skillMd && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            <FileText className="size-4 text-muted-foreground" />
            <span className="text-sm font-medium">SKILL.md</span>
            <span className="text-xs text-muted-foreground">
              ({skillMd.path})
            </span>
          </div>
          <pre className="max-h-64 overflow-auto rounded-lg border bg-muted/50 p-3 text-xs leading-relaxed whitespace-pre-wrap">
            {skillMd.content}
          </pre>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <Alert variant="destructive">
          <AlertCircle />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* 操作按钮 */}
      <div className="flex items-center gap-2 pt-2">
        <Button variant="outline" onClick={reset} disabled={installing}>
          重新选择
        </Button>
        <div className="flex-1" />
        <Button
          onClick={handleInstall}
          disabled={installing || !editName.trim()}
        >
          {installing ? (
            <>
              <Spinner className="size-4" />
              安装中...
            </>
          ) : (
            <>
              <Check className="size-4" />
              安装
            </>
          )}
        </Button>
      </div>
    </div>
  );
}
