# Skill 压缩包上传功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Skill 管理页面的 InstallDialog 中新增 "上传压缩包" Tab，支持用户拖拽或选择 zip/tar/tar.gz 文件上传，前端解压后预览内容，确认后提交到后端创建 skill。

**Architecture:** 前端使用 fflate 库解压压缩包，预览内容后，将所有文件打包成 JSON 传递给后端。后端接收文件映射并写入 `agent_space/skills/{name}/` 目录。

**Tech Stack:** React 19, TypeScript, fflate, FastAPI, Pydantic

---

## 文件结构

### 前端文件

| 文件 | 职责 | 操作 |
|------|------|------|
| `webui/src/utils/archive.ts` | 压缩包解压、SKILL.md 检测、frontmatter 解析 | 创建 |
| `webui/src/components/skill/UploadTab.tsx` | 上传压缩包 Tab 组件（拖拽、预览、提交） | 创建 |
| `webui/src/api/types.ts` | 更新 SkillInfo 和 CreateSkillRequest 类型 | 修改 |
| `webui/src/api/skill.ts` | 更新 CreateSkillRequest 类型 | 修改 |
| `webui/src/pages/skill/index.tsx` | InstallDialog 中添加 "上传压缩包" Tab | 修改 |

### 后端文件

| 文件 | 职责 | 操作 |
|------|------|------|
| `api/skill.py` | 更新 CreateSkillRequest 和 create_skill 端点 | 修改 |

---

## Task 1: 安装前端依赖 fflate

**Files:**
- Modify: `webui/package.json`

- [ ] **Step 1: 安装 fflate 依赖**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090/webui
npm install fflate
```

Expected: fflate 添加到 package.json 的 dependencies 中

- [ ] **Step 2: 验证安装成功**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090/webui
npm list fflate
```

Expected: 显示 fflate 版本信息

- [ ] **Step 3: 提交依赖变更**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
git add webui/package.json webui/package-lock.json
git commit -m "deps: 添加 fflate 解压库依赖"
```

---

## Task 2: 创建压缩包解压工具函数

**Files:**
- Create: `webui/src/utils/archive.ts`

- [ ] **Step 1: 创建 archive.ts 文件**

创建文件 `webui/src/utils/archive.ts`，包含以下功能：
- `decompressArchive(file: File)` - 解压 zip/tar/tar.gz 文件
- `findSkillMd(files)` - 检测 SKILL.md 文件位置
- `parseFrontmatter(markdown)` - 解析 YAML frontmatter
- `buildFileTree(files)` - 构建文件树结构

```typescript
/**
 * 压缩包解压工具函数
 */

import { unzip, gunzipSync, untar } from 'fflate';

export interface ArchiveFile {
  path: string;
  content: Uint8Array;
}

export interface FileTreeNode {
  name: string;
  path: string;
  isDirectory: boolean;
  children?: FileTreeNode[];
}

export interface SkillMetadata {
  name?: string;
  description?: string;
}

/**
 * 解压压缩包文件
 * 支持 .zip, .tar, .tar.gz, .tgz 格式
 */
export async function decompressArchive(file: File): Promise<ArchiveFile[]> {
  const buffer = await file.arrayBuffer();
  const data = new Uint8Array(buffer);
  const fileName = file.name.toLowerCase();

  let decompressed: Record<string, Uint8Array>;

  if (fileName.endsWith('.zip')) {
    decompressed = await new Promise((resolve, reject) => {
      unzip(data, (err, result) => {
        if (err) reject(new Error(`ZIP 解压失败: ${err.message}`));
        else resolve(result);
      });
    });
  } else if (fileName.endsWith('.tar.gz') || fileName.endsWith('.tgz')) {
    try {
      const unzipped = gunzipSync(data);
      decompressed = await new Promise((resolve, reject) => {
        untar(unzipped, (err, result) => {
          if (err) reject(new Error(`TAR 解压失败: ${err.message}`));
          else resolve(result);
        });
      });
    } catch (e) {
      throw new Error(`GZIP 解压失败: ${e instanceof Error ? e.message : '未知错误'}`);
    }
  } else if (fileName.endsWith('.tar')) {
    decompressed = await new Promise((resolve, reject) => {
      untar(data, (err, result) => {
        if (err) reject(new Error(`TAR 解压失败: ${err.message}`));
        else resolve(result);
      });
    });
  } else {
    throw new Error('不支持的文件格式，请上传 .zip, .tar, .tar.gz 或 .tgz 文件');
  }

  // 过滤掉目录条目，只保留文件
  const files: ArchiveFile[] = [];
  for (const [path, content] of Object.entries(decompressed)) {
    // 跳过目录条目（路径以 / 结尾）
    if (!path.endsWith('/')) {
      files.push({ path, content });
    }
  }

  if (files.length === 0) {
    throw new Error('压缩包为空');
  }

  return files;
}

/**
 * 在文件列表中查找 SKILL.md 文件
 * 优先查找根目录，其次查找子目录
 */
export function findSkillMd(files: ArchiveFile[]): { path: string; content: string } | null {
  // 1. 检查根目录是否有 SKILL.md
  const rootSkillMd = files.find(f => f.path === 'SKILL.md');
  if (rootSkillMd) {
    return {
      path: rootSkillMd.path,
      content: new TextDecoder().decode(rootSkillMd.content),
    };
  }

  // 2. 检查子目录中是否有 SKILL.md（格式：xxx/SKILL.md）
  const subSkillMd = files.find(f =>
    f.path.endsWith('/SKILL.md') && !f.path.includes('/', f.path.indexOf('/') + 1)
  );
  if (subSkillMd) {
    return {
      path: subSkillMd.path,
      content: new TextDecoder().decode(subSkillMd.content),
    };
  }

  // 3. 检查更深层目录中的 SKILL.md
  const deepSkillMd = files.find(f => f.path.endsWith('/SKILL.md'));
  if (deepSkillMd) {
    return {
      path: deepSkillMd.path,
      content: new TextDecoder().decode(deepSkillMd.content),
    };
  }

  return null;
}

/**
 * 解析 SKILL.md 的 YAML frontmatter
 * 提取 name 和 description 字段
 */
export function parseFrontmatter(markdown: string): SkillMetadata {
  const match = markdown.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return {};

  const yaml = match[1];
  const nameMatch = yaml.match(/^name:\s*(.+)$/m);
  const descMatch = yaml.match(/^description:\s*(.+)$/m);

  return {
    name: nameMatch?.[1]?.trim(),
    description: descMatch?.[1]?.trim(),
  };
}

/**
 * 构建文件树结构（用于预览展示）
 */
export function buildFileTree(files: ArchiveFile[]): FileTreeNode[] {
  const root: FileTreeNode[] = [];
  const pathMap = new Map<string, FileTreeNode>();

  // 按路径深度排序
  const sortedFiles = [...files].sort((a, b) => {
    const depthA = a.path.split('/').length;
    const depthB = b.path.split('/').length;
    return depthA - depthB;
  });

  for (const file of sortedFiles) {
    const parts = file.path.split('/');
    let currentPath = '';

    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const parentPath = currentPath;
      currentPath = currentPath ? `${currentPath}/${part}` : part;

      if (!pathMap.has(currentPath)) {
        const isFile = i === parts.length - 1;
        const node: FileTreeNode = {
          name: part,
          path: currentPath,
          isDirectory: !isFile,
          children: isFile ? undefined : [],
        };

        pathMap.set(currentPath, node);

        if (parentPath) {
          const parent = pathMap.get(parentPath);
          if (parent?.children) {
            parent.children.push(node);
          }
        } else {
          root.push(node);
        }
      }
    }
  }

  return root;
}

/**
 * 将文件树转换为字符串表示（用于预览）
 */
export function fileTreeToString(tree: FileTreeNode[], prefix = ''): string {
  let result = '';

  for (let i = 0; i < tree.length; i++) {
    const node = tree[i];
    const isLast = i === tree.length - 1;
    const connector = isLast ? '└── ' : '├── ';
    const childPrefix = isLast ? '    ' : '│   ';

    result += `${prefix}${connector}${node.name}\n`;

    if (node.children && node.children.length > 0) {
      result += fileTreeToString(node.children, prefix + childPrefix);
    }
  }

  return result;
}

/**
 * 验证文件路径安全性（防止路径穿越）
 */
export function isPathSafe(path: string): boolean {
  // 检查是否包含 .. 或绝对路径
  if (path.includes('..') || path.startsWith('/') || path.startsWith('\\')) {
    return false;
  }

  // 检查是否包含危险字符
  const dangerousChars = /[<>:"|?*]/;
  if (dangerousChars.test(path)) {
    return false;
  }

  return true;
}
```

- [ ] **Step 2: 验证 TypeScript 编译**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090/webui
npm run type-check
```

Expected: 无类型错误

- [ ] **Step 3: 提交工具函数**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
git add webui/src/utils/archive.ts
git commit -m "feat: 添加压缩包解压工具函数"
```

---

## Task 3: 更新前端 API 类型定义

**Files:**
- Modify: `webui/src/api/types.ts:176-211`
- Modify: `webui/src/api/skill.ts`

- [ ] **Step 1: 更新 types.ts 中的 CreateSkillRequest 接口**

在 `webui/src/api/types.ts` 中找到 `CreateSkillRequest` 接口，添加 `files` 字段：

```typescript
export interface CreateSkillRequest {
  name: string;
  display_name?: string;
  description?: string;
  markdown?: string;
  tags?: string[];
  author?: string;
  files?: Record<string, string>;  // 新增：文件路径 → 内容映射
}
```

- [ ] **Step 2: 更新 skill.ts 中的 CreateSkillRequest 类型**

在 `webui/src/api/skill.ts` 中找到 `CreateSkillRequest` 类型定义，添加 `files` 字段：

```typescript
interface CreateSkillRequest {
  name: string;
  display_name?: string;
  description?: string;
  markdown?: string;
  tags?: string[];
  author?: string;
  files?: Record<string, string>;  // 新增：文件路径 → 内容映射
}
```

- [ ] **Step 3: 验证 TypeScript 编译**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090/webui
npm run type-check
```

Expected: 无类型错误

- [ ] **Step 4: 提交类型变更**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
git add webui/src/api/types.ts webui/src/api/skill.ts
git commit -m "feat: 更新 Skill API 类型定义，支持 files 字段"
```

---

## Task 4: 创建上传压缩包 Tab 组件

**Files:**
- Create: `webui/src/components/skill/UploadTab.tsx`

- [ ] **Step 1: 创建 UploadTab.tsx 文件**

创建文件 `webui/src/components/skill/UploadTab.tsx`，实现：
- 拖拽上传区域
- 文件选择按钮
- 文件预览（文件列表、SKILL.md 内容、元数据）
- 名称编辑输入框
- 安装按钮

```tsx
/**
 * 上传压缩包 Tab 组件
 * 支持拖拽上传 zip/tar/tar.gz 文件
 */

import React, { useState, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { Upload, File, X, FolderOpen, AlertCircle, CheckCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ScrollArea } from '@/components/ui/scroll-area';
import { toast } from 'sonner';
import {
  decompressArchive,
  findSkillMd,
  parseFrontmatter,
  buildFileTree,
  fileTreeToString,
  isPathSafe,
  type ArchiveFile,
  type SkillMetadata,
} from '@/utils/archive';

interface UploadTabProps {
  onInstall: (data: {
    name: string;
    description: string;
    markdown: string;
    files: Record<string, string>;
  }) => Promise<void>;
  onCancel: () => void;
}

export function UploadTab({ onInstall, onCancel }: UploadTabProps) {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 状态
  const [isDragging, setIsDragging] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [archiveFiles, setArchiveFiles] = useState<ArchiveFile[]>([]);
  const [skillMdContent, setSkillMdContent] = useState<string>('');
  const [metadata, setMetadata] = useState<SkillMetadata>({});
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [fileTreeStr, setFileTreeStr] = useState('');
  const [error, setError] = useState<string | null>(null);

  // 支持的文件格式
  const supportedFormats = ['.zip', '.tar', '.tar.gz', '.tgz'];

  /**
   * 验证文件格式
   */
  const isValidFormat = useCallback((fileName: string): boolean => {
    const lowerName = fileName.toLowerCase();
    return supportedFormats.some(ext => lowerName.endsWith(ext));
  }, []);

  /**
   * 处理文件选择/拖拽
   */
  const handleFile = useCallback(async (file: File) => {
    setError(null);

    // 验证格式
    if (!isValidFormat(file.name)) {
      setError(`不支持的文件格式，请上传 ${supportedFormats.join(', ')} 文件`);
      return;
    }

    setIsProcessing(true);
    setSelectedFile(file);

    try {
      // 解压文件
      const files = await decompressArchive(file);
      setArchiveFiles(files);

      // 查找 SKILL.md
      const skillMd = findSkillMd(files);
      if (!skillMd) {
        setError('压缩包中未找到 SKILL.md 文件');
        setIsProcessing(false);
        return;
      }

      setSkillMdContent(skillMd.content);

      // 解析 frontmatter
      const meta = parseFrontmatter(skillMd.content);
      setMetadata(meta);
      setName(meta.name || file.name.replace(/\.(zip|tar\.gz|tar|tgz)$/i, ''));
      setDescription(meta.description || '');

      // 构建文件树
      const tree = buildFileTree(files);
      setFileTreeStr(fileTreeToString(tree));
    } catch (e) {
      setError(e instanceof Error ? e.message : '解压失败，请检查文件是否损坏');
    } finally {
      setIsProcessing(false);
    }
  }, [isValidFormat]);

  /**
   * 处理拖拽事件
   */
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);

    const files = e.dataTransfer.files;
    if (files.length > 0) {
      handleFile(files[0]);
    }
  }, [handleFile]);

  /**
   * 处理文件选择
   */
  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      handleFile(files[0]);
    }
  }, [handleFile]);

  /**
   * 清除选择
   */
  const handleClear = useCallback(() => {
    setSelectedFile(null);
    setArchiveFiles([]);
    setSkillMdContent('');
    setMetadata({});
    setName('');
    setDescription('');
    setFileTreeStr('');
    setError(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }, []);

  /**
   * 处理安装
   */
  const handleInstall = useCallback(async () => {
    if (!name.trim()) {
      toast.error('请输入 Skill 名称');
      return;
    }

    if (!skillMdContent) {
      toast.error('未找到 SKILL.md 内容');
      return;
    }

    setIsProcessing(true);

    try {
      // 打包所有文件为 Record<string, string>
      const filesRecord: Record<string, string> = {};
      for (const file of archiveFiles) {
        // 安全校验
        if (!isPathSafe(file.path)) {
          toast.error(`文件路径包含非法字符: ${file.path}`);
          return;
        }
        filesRecord[file.path] = new TextDecoder().decode(file.content);
      }

      await onInstall({
        name: name.trim(),
        description: description.trim(),
        markdown: skillMdContent,
        files: filesRecord,
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '安装失败');
    } finally {
      setIsProcessing(false);
    }
  }, [name, description, skillMdContent, archiveFiles, onInstall]);

  return (
    <div className="space-y-4">
      {/* 拖拽上传区域 */}
      {!selectedFile && (
        <div
          className={`
            border-2 border-dashed rounded-lg p-8 text-center cursor-pointer
            transition-colors duration-200
            ${isDragging
              ? 'border-primary bg-primary/10'
              : 'border-muted-foreground/25 hover:border-primary/50'
            }
          `}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip,.tar,.tar.gz,.tgz"
            onChange={handleFileSelect}
            className="hidden"
          />
          <Upload className="mx-auto h-12 w-12 text-muted-foreground mb-4" />
          <p className="text-lg font-medium">
            {isDragging ? '释放文件以上传' : '拖拽压缩包到此处，或点击选择文件'}
          </p>
          <p className="text-sm text-muted-foreground mt-2">
            支持 .zip, .tar, .tar.gz 格式
          </p>
        </div>
      )}

      {/* 处理中状态 */}
      {isProcessing && (
        <div className="flex items-center justify-center p-8">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mr-3" />
          <span>正在解压...</span>
        </div>
      )}

      {/* 错误提示 */}
      {error && (
        <div className="flex items-start gap-3 p-4 bg-destructive/10 border border-destructive/20 rounded-lg">
          <AlertCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
          <div>
            <p className="text-destructive font-medium">错误</p>
            <p className="text-destructive/80 text-sm">{error}</p>
          </div>
        </div>
      )}

      {/* 文件预览 */}
      {selectedFile && !isProcessing && !error && (
        <div className="space-y-4">
          {/* 文件信息 */}
          <div className="flex items-center justify-between p-3 bg-muted rounded-lg">
            <div className="flex items-center gap-3">
              <File className="h-5 w-5 text-primary" />
              <div>
                <p className="font-medium">{selectedFile.name}</p>
                <p className="text-sm text-muted-foreground">
                  {(selectedFile.size / 1024).toFixed(1)} KB
                </p>
              </div>
            </div>
            <Button variant="ghost" size="icon" onClick={handleClear}>
              <X className="h-4 w-4" />
            </Button>
          </div>

          {/* Skill 名称 */}
          <div className="space-y-2">
            <Label htmlFor="skill-name">Skill 名称</Label>
            <Input
              id="skill-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="请输入 Skill 名称"
            />
            {metadata.name && metadata.name !== name && (
              <p className="text-sm text-muted-foreground">
                <CheckCircle className="inline h-3 w-3 mr-1" />
                从 SKILL.md 自动解析: {metadata.name}
              </p>
            )}
          </div>

          {/* Skill 描述 */}
          <div className="space-y-2">
            <Label htmlFor="skill-description">描述</Label>
            <Textarea
              id="skill-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="请输入 Skill 描述"
              rows={2}
            />
          </div>

          {/* 文件列表 */}
          <div className="space-y-2">
            <Label>
              <FolderOpen className="inline h-4 w-4 mr-1" />
              文件列表
            </Label>
            <ScrollArea className="h-[150px] border rounded-md p-3">
              <pre className="text-sm font-mono whitespace-pre">{fileTreeStr}</pre>
            </ScrollArea>
          </div>

          {/* SKILL.md 内容预览 */}
          <div className="space-y-2">
            <Label>SKILL.md 内容预览</Label>
            <ScrollArea className="h-[200px] border rounded-md p-3">
              <pre className="text-sm font-mono whitespace-pre-wrap">{skillMdContent}</pre>
            </ScrollArea>
          </div>

          {/* 操作按钮 */}
          <div className="flex justify-end gap-3 pt-4">
            <Button variant="outline" onClick={onCancel}>
              取消
            </Button>
            <Button onClick={handleInstall} disabled={isProcessing || !name.trim()}>
              {isProcessing ? '安装中...' : '安装'}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 验证 TypeScript 编译**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090/webui
npm run type-check
```

Expected: 无类型错误

- [ ] **Step 3: 提交组件**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
git add webui/src/components/skill/UploadTab.tsx
git commit -m "feat: 添加上传压缩包 Tab 组件"
```

---

## Task 5: 更新 Skill 页面集成 UploadTab

**Files:**
- Modify: `webui/src/pages/skill/index.tsx`

- [ ] **Step 1: 导入 UploadTab 组件**

在 `webui/src/pages/skill/index.tsx` 文件顶部添加导入：

```typescript
import { UploadTab } from '@/components/skill/UploadTab';
```

- [ ] **Step 2: 添加 "上传压缩包" Tab**

找到 InstallDialog 组件中的 Tab 列表，添加新的 Tab：

```tsx
<Tabs defaultValue="form">
  <TabsList>
    <TabsTrigger value="form">表单</TabsTrigger>
    <TabsTrigger value="paste">粘贴</TabsTrigger>
    <TabsTrigger value="upload">上传压缩包</TabsTrigger>  {/* 新增 */}
  </TabsList>

  <TabsContent value="form">
    {/* 现有表单内容 */}
  </TabsContent>

  <TabsContent value="paste">
    {/* 现有粘贴内容 */}
  </TabsContent>

  <TabsContent value="upload">  {/* 新增 */}
    <UploadTab
      onInstall={async (data) => {
        await skillApi.create({
          name: data.name,
          description: data.description,
          markdown: data.markdown,
          files: data.files,
        });
        toast.success('技能安装成功');
        refreshSkills();
      }}
      onCancel={() => setIsInstallDialogOpen(false)}
    />
  </TabsContent>
</Tabs>
```

- [ ] **Step 3: 验证 TypeScript 编译**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090/webui
npm run type-check
```

Expected: 无类型错误

- [ ] **Step 4: 提交集成变更**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
git add webui/src/pages/skill/index.tsx
git commit -m "feat: 集成上传压缩包 Tab 到 Skill 页面"
```

---

## Task 6: 更新后端 API 支持 files 字段

**Files:**
- Modify: `api/skill.py`

- [ ] **Step 1: 更新 CreateSkillRequest 模型**

在 `api/skill.py` 中找到 `CreateSkillRequest` 类，添加 `files` 字段：

```python
class CreateSkillRequest(BaseModel):
    """添加 Skill 请求"""

    name: str = Field(description="Skill 名称（唯一）")
    display_name: str | None = Field(default=None, description="显示名称")
    description: str = Field(default="", description="描述")
    markdown: str = Field(default="", description="SKILL.md 内容")
    tags: list[str] = Field(default_factory=list, description="标签")
    author: str | None = Field(default=None, description="作者")
    files: dict[str, str] | None = Field(
        default=None,
        description="额外文件映射（文件路径 → 内容），用于压缩包上传"
    )
```

- [ ] **Step 2: 添加路径安全校验函数**

在 `api/skill.py` 中添加路径安全校验函数：

```python
def _is_path_safe(file_path: str) -> bool:
    """验证文件路径安全性（防止路径穿越）"""
    # 检查是否包含 ..
    if '..' in file_path:
        return False

    # 检查是否是绝对路径
    if file_path.startswith('/') or file_path.startswith('\\'):
        return False

    # 检查是否包含危险字符
    dangerous_chars = '<>:"|?*'
    if any(char in file_path for char in dangerous_chars):
        return False

    return True
```

- [ ] **Step 3: 更新 create_skill 端点**

修改 `create_skill` 端点，添加处理 `files` 字段的逻辑：

```python
@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    request: Request,
    body: CreateSkillRequest,
    user_id: str = "anonymous",
):
    """添加 Skill

    重要：同时在 agent_space/skills/ 目录下创建实际的 SKILL.md 文件，
    因为该目录是运行时加载的真相源。
    """
    config = getattr(request.app.state, "config", None)
    require_user_id(user_id, is_auth_enabled(config))
    store = request.app.state.skill_config_store

    if store.get_by_name(body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Skill '{body.name}' 已存在",
        )

    # 1. 先在 agent_space/skills/ 目录下创建实际的 SKILL.md 文件
    skills_dir = _get_skills_dir(request)
    if skills_dir:
        try:
            _create_skill_file(skills_dir, body.name, body.markdown)

            # 2. 如果有额外文件，写入到 skill 目录
            if body.files:
                skill_dir = Path(skills_dir) / body.name
                for file_path, content in body.files.items():
                    # 安全校验：防止路径穿越
                    if not _is_path_safe(file_path):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"非法文件路径: {file_path}",
                        )

                    safe_path = skill_dir / file_path
                    # 确保路径在 skill 目录内
                    if not str(safe_path.resolve()).startswith(str(skill_dir.resolve())):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"文件路径越界: {file_path}",
                        )

                    safe_path.parent.mkdir(parents=True, exist_ok=True)
                    safe_path.write_text(content, encoding="utf-8")
                    logger.debug("已写入文件: {}", safe_path)
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("创建 Skill 文件失败: {}", e)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"创建 Skill 文件失败: {e}",
            )
    else:
        logger.warning("skills_dir 未配置，跳过创建实际文件（仅更新元数据）")

    # 3. 更新元数据配置（skills.json）
    entry = SkillConfigEntry(
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        markdown=body.markdown,
        tags=body.tags,
        author=body.author,
        enabled=True,
    )
    await store.upsert(entry)
    created = store.get_by_name(body.name)
    return _entry_to_response(created)
```

- [ ] **Step 4: 验证 Python 语法**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
python -m py_compile api/skill.py
```

Expected: 无语法错误

- [ ] **Step 5: 提交后端变更**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
git add api/skill.py
git commit -m "feat: 后端 API 支持 files 字段，用于压缩包上传"
```

---

## Task 7: 端到端测试

- [ ] **Step 1: 启动后端服务**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
python main.py
```

Expected: 服务正常启动，无错误

- [ ] **Step 2: 构建前端**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090/webui
npm run build
```

Expected: 构建成功，无错误

- [ ] **Step 3: 测试上传 zip 文件**

1. 创建测试 zip 文件，包含 SKILL.md
2. 访问 http://localhost:8090/webui/skill
3. 点击 "安装技能" → "上传压缩包" Tab
4. 拖拽或选择 zip 文件
5. 验证预览显示正确
6. 点击 "安装"
7. 验证 skill 创建成功

- [ ] **Step 4: 测试上传 tar.gz 文件**

重复 Step 3，使用 tar.gz 文件

- [ ] **Step 5: 测试无 SKILL.md 的压缩包**

1. 创建不含 SKILL.md 的 zip 文件
2. 上传后验证显示错误提示

- [ ] **Step 6: 测试子目录结构**

1. 创建包含子目录的 zip 文件（如 `my-skill/SKILL.md`）
2. 上传后验证自动检测 SKILL.md

- [ ] **Step 7: 验证文件写入**

```bash
ls -la d:/temp/temp_2026_07/platform-server-2/platform-server-8090/workspaces/agent_space/skills/
```

Expected: 看到新创建的 skill 目录和所有文件

- [ ] **Step 8: 提交最终变更**

```bash
cd d:/temp/temp_2026_07/platform-server-2/platform-server-8090
git add -A
git commit -m "feat: 完成 Skill 压缩包上传功能"
```

---

## 检查清单

- [ ] 所有文件已创建/修改
- [ ] TypeScript 编译无错误
- [ ] Python 语法无错误
- [ ] 前端依赖已安装
- [ ] 后端 API 已更新
- [ ] 端到端测试通过
- [ ] 代码已提交到 git
