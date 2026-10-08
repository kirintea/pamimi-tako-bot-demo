/**
 * 压缩包解压工具函数
 * 支持 .zip, .tar, .tar.gz, .tgz 格式
 * ZIP 使用 fflate 库解压，TAR 使用自定义解析器
 */

import { unzip, gunzipSync } from 'fflate';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// TAR parser (fflate does not include untar)
// ---------------------------------------------------------------------------

/**
 * Parse a TAR archive buffer and return a map of path -> content.
 * Supports USTAR (POSIX) long filename prefix.
 */
function untarSync(data: Uint8Array): Record<string, Uint8Array> {
  const result: Record<string, Uint8Array> = {};
  let offset = 0;

  while (offset + 512 <= data.length) {
    // Check for two consecutive zero blocks (end-of-archive marker)
    const header = data.slice(offset, offset + 512);
    let allZero = true;
    for (let i = 0; i < 512; i++) {
      if (header[i] !== 0) {
        allZero = false;
        break;
      }
    }
    if (allZero) break;

    // Read fields from the 512-byte header
    const name = readTarString(header, 0, 100);
    const sizeOctal = readTarString(header, 124, 12);
    const prefix = readTarString(header, 345, 155);

    // Determine full path (USTAR prefix + name)
    const fullPath = prefix ? `${prefix}/${name}` : name;

    // File size (octal)
    const fileSize = parseInt(sizeOctal, 8) || 0;

    // Move past the 512-byte header
    offset += 512;

    // Extract file data (skip directory entries that end with '/')
    if (fullPath && !fullPath.endsWith('/')) {
      result[fullPath] = data.slice(offset, offset + fileSize);
    }

    // Advance past file data, padded to 512-byte boundary
    offset += Math.ceil(fileSize / 512) * 512;
  }

  return result;
}

/** Read a null-terminated string from a specific offset in a TAR header block. */
function readTarString(block: Uint8Array, start: number, maxLen: number): string {
  let end = start;
  while (end < start + maxLen && block[end] !== 0) {
    end++;
  }
  return new TextDecoder().decode(block.slice(start, end)).trim();
}

// ---------------------------------------------------------------------------
// Decompression
// ---------------------------------------------------------------------------

/**
 * Decompress an archive file.
 * Supports .zip, .tar, .tar.gz, .tgz formats.
 */
export async function decompressArchive(file: File): Promise<ArchiveFile[]> {
  const buffer = await file.arrayBuffer();
  const data = new Uint8Array(buffer);
  const fileName = file.name.toLowerCase();

  let decompressed: Record<string, Uint8Array>;

  if (fileName.endsWith('.zip')) {
    decompressed = await new Promise<Record<string, Uint8Array>>((resolve, reject) => {
      unzip(data, (err, result) => {
        if (err) reject(new Error(`ZIP 解压失败: ${err.message}`));
        else resolve(result);
      });
    });
  } else if (fileName.endsWith('.tar.gz') || fileName.endsWith('.tgz')) {
    try {
      const unzipped = gunzipSync(data);
      decompressed = untarSync(unzipped);
    } catch (e) {
      throw new Error(`GZIP 解压失败: ${e instanceof Error ? e.message : '未知错误'}`);
    }
  } else if (fileName.endsWith('.tar')) {
    decompressed = untarSync(data);
  } else {
    throw new Error('不支持的文件格式，请上传 .zip, .tar, .tar.gz 或 .tgz 文件');
  }

  // Filter out directory entries (paths ending with /)
  const files: ArchiveFile[] = [];
  for (const [path, content] of Object.entries(decompressed)) {
    if (!path.endsWith('/')) {
      files.push({ path, content });
    }
  }

  if (files.length === 0) {
    throw new Error('压缩包为空');
  }

  return files;
}

// ---------------------------------------------------------------------------
// SKILL.md detection
// ---------------------------------------------------------------------------

/**
 * Find the SKILL.md file in the archive.
 * Priority: root directory > single-level subdirectory > deeper directories.
 */
export function findSkillMd(files: ArchiveFile[]): { path: string; content: string } | null {
  // 1. Root-level SKILL.md
  const rootSkillMd = files.find(f => f.path === 'SKILL.md');
  if (rootSkillMd) {
    return {
      path: rootSkillMd.path,
      content: new TextDecoder().decode(rootSkillMd.content),
    };
  }

  // 2. Single-level subdirectory (e.g. my-skill/SKILL.md)
  const subSkillMd = files.find(
    f => f.path.endsWith('/SKILL.md') && !f.path.includes('/', f.path.indexOf('/') + 1),
  );
  if (subSkillMd) {
    return {
      path: subSkillMd.path,
      content: new TextDecoder().decode(subSkillMd.content),
    };
  }

  // 3. Deeper directory
  const deepSkillMd = files.find(f => f.path.endsWith('/SKILL.md'));
  if (deepSkillMd) {
    return {
      path: deepSkillMd.path,
      content: new TextDecoder().decode(deepSkillMd.content),
    };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Frontmatter parsing
// ---------------------------------------------------------------------------

/**
 * Parse YAML frontmatter from a SKILL.md markdown string.
 * Extracts `name` and `description` fields.
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

// ---------------------------------------------------------------------------
// File tree
// ---------------------------------------------------------------------------

/**
 * Build a hierarchical file tree from a flat list of archive files.
 */
export function buildFileTree(files: ArchiveFile[]): FileTreeNode[] {
  const root: FileTreeNode[] = [];
  const pathMap = new Map<string, FileTreeNode>();

  // Sort by path depth so parent directories are created before children
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
 * Convert a file tree to a string representation (for preview display).
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

// ---------------------------------------------------------------------------
// Path safety
// ---------------------------------------------------------------------------

/**
 * Validate file path safety (prevent path traversal attacks).
 */
export function isPathSafe(path: string): boolean {
  // Check for path traversal
  if (path.includes('..') || path.startsWith('/') || path.startsWith('\\')) {
    return false;
  }

  // Check for dangerous characters
  const dangerousChars = /[<>:"|?*]/;
  if (dangerousChars.test(path)) {
    return false;
  }

  return true;
}
