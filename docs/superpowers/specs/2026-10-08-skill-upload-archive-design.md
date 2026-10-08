# Skill 压缩包上传功能设计

## 1. 功能概述

在 Skill 管理页面的 InstallDialog 中新增 "上传压缩包" Tab，支持用户拖拽或选择 zip/tar/tar.gz 文件上传，前端解压后预览内容，确认后提交到后端创建 skill。

## 2. 技术栈

### 前端新增依赖
- `fflate` - 轻量级解压库（支持 zip + tar + gzip，约 8KB gzip）

### 后端
- 无新增依赖，复用现有 API

## 3. UI 设计

### 3.1 InstallDialog 布局

```
┌─────────────────────────────────────────────────┐
│  安装技能                                         │
├─────────────────────────────────────────────────┤
│  [表单]  [粘贴]  [上传压缩包]                      │
├─────────────────────────────────────────────────┤
│                                                   │
│  ┌─────────────────────────────────────────────┐ │
│  │                                             │ │
│  │     📁 拖拽压缩包到此处，或点击选择文件        │ │
│  │                                             │ │
│  │     支持 .zip, .tar, .tar.gz 格式            │ │
│  │                                             │ │
│  └─────────────────────────────────────────────┘ │
│                                                   │
│  [文件名.zip]  ✕                                   │
│                                                   │
│  ───────────── 预览 ─────────────                  │
│  Skill 名称: [可编辑输入框]                         │
│  描述: 从 SKILL.md 解析的描述                      │
│                                                   │
│  文件列表:                                         │
│  ├── SKILL.md                                     │
│  ├── README.md                                    │
│  └── examples/                                    │
│      └── basic.py                                 │
│                                                   │
│  SKILL.md 内容预览:                                │
│  ┌─────────────────────────────────────────────┐ │
│  │ ---                                         │ │
│  │ name: my-skill                              │ │
│  │ description: 示例技能                        │ │
│  │ ---                                         │ │
│  │ # 使用说明                                  │ │
│  │ ...                                         │ │
│  └─────────────────────────────────────────────┘ │
│                                                   │
│                              [取消]  [安装]        │
└─────────────────────────────────────────────────┘
```

### 3.2 交互流程

1. **初始状态**：显示拖拽区域
2. **拖拽/选择文件后**：
   - 显示文件名 + 删除按钮
   - 自动解压压缩包
   - 解析 SKILL.md，提取 name/description
   - 显示预览区域
3. **点击安装**：
   - 校验：必须有 SKILL.md，name 不能为空
   - 提交：将所有文件内容打包成 JSON，调用 `skillApi.create()`
   - 成功后刷新列表

## 4. 数据流

### 4.1 前端处理流程

```
用户拖拽文件
    ↓
验证文件格式 (.zip/.tar/.tar.gz)
    ↓
使用 fflate 解压
    ↓
检测 SKILL.md 位置（根目录或子目录）
    ↓
解析 SKILL.md frontmatter (name, description)
    ↓
显示预览（文件列表、元数据、内容）
    ↓
用户确认后，打包所有文件为 JSON
    ↓
调用 skillApi.create({name, description, markdown, files})
```

### 4.2 文件打包格式

```typescript
interface SkillPackage {
  name: string;           // skill 名称
  description?: string;   // 描述
  markdown: string;       // SKILL.md 内容
  files: Record<string, string>;  // 文件路径 → 内容映射
}

// 示例：
{
  name: "my-skill",
  description: "示例技能",
  markdown: "---\nname: my-skill\n---\n# 使用说明\n...",
  files: {
    "SKILL.md": "---\nname: my-skill\n---\n# 使用说明\n...",
    "README.md": "# My Skill\n...",
    "examples/basic.py": "print('hello')"
  }
}
```

## 5. 后端改动

### 5.1 修改 `api/skill.py` 的 `CreateSkillRequest`

```python
class CreateSkillRequest(BaseModel):
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

### 5.2 修改 `create_skill` 端点

```python
@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(request: Request, body: CreateSkillRequest, user_id: str = "anonymous"):
    # ... 现有逻辑 ...

    # 1. 创建 SKILL.md 文件
    skills_dir = _get_skills_dir(request)
    if skills_dir:
        try:
            _create_skill_file(skills_dir, body.name, body.markdown)

            # 2. 如果有额外文件，写入到 skill 目录
            if body.files:
                skill_dir = Path(skills_dir) / body.name
                for file_path, content in body.files.items():
                    # 安全校验：防止路径穿越
                    safe_path = skill_dir / file_path
                    if not str(safe_path).startswith(str(skill_dir)):
                        raise HTTPException(400, f"非法文件路径: {file_path}")
                    safe_path.parent.mkdir(parents=True, exist_ok=True)
                    safe_path.write_text(content, encoding="utf-8")
        except Exception as e:
            # ... 错误处理 ...

    # 3. 更新元数据
    # ... 现有逻辑 ...
```

## 6. 前端改动

### 6.1 新增文件

- `webui/src/components/skill/UploadTab.tsx` - 上传压缩包 Tab 组件
- `webui/src/utils/archive.ts` - 压缩包解压工具函数

### 6.2 修改文件

- `webui/src/pages/skill/index.tsx` - InstallDialog 中添加 "上传压缩包" Tab
- `webui/src/api/skill.ts` - 更新 `CreateSkillRequest` 类型，添加 `files` 字段
- `webui/src/api/types.ts` - 更新类型定义

### 6.3 依赖安装

```bash
cd webui
npm install fflate
```

## 7. 关键实现细节

### 7.1 压缩包解压

```typescript
import { unzip, untar } from 'fflate';

async function decompressArchive(file: File): Promise<Map<string, Uint8Array>> {
  const buffer = await file.arrayBuffer();
  const data = new Uint8Array(buffer);

  if (file.name.endsWith('.zip')) {
    return new Promise((resolve, reject) => {
      unzip(data, (err, result) => {
        if (err) reject(err);
        else resolve(result);
      });
    });
  } else if (file.name.endsWith('.tar.gz') || file.name.endsWith('.tgz')) {
    const decompressed = gunzipSync(data);
    return untar(decompressed);
  } else if (file.name.endsWith('.tar')) {
    return untar(data);
  }

  throw new Error('不支持的文件格式');
}
```

### 7.2 SKILL.md 检测

```typescript
function findSkillMd(files: Map<string, Uint8Array>): { path: string; content: string } | null {
  // 1. 检查根目录是否有 SKILL.md
  if (files['SKILL.md']) {
    return { path: 'SKILL.md', content: new TextDecoder().decode(files['SKILL.md']) };
  }

  // 2. 检查子目录中是否有 SKILL.md
  for (const [path, content] of Object.entries(files)) {
    if (path.endsWith('/SKILL.md') || path.endsWith('\\SKILL.md')) {
      return { path, content: new TextDecoder().decode(content) };
    }
  }

  return null;
}
```

### 7.3 Frontmatter 解析

```typescript
function parseFrontmatter(markdown: string): { name?: string; description?: string } {
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
```

## 8. 错误处理

| 错误场景 | 处理方式 |
|---------|---------|
| 文件格式不支持 | 显示错误提示："仅支持 .zip, .tar, .tar.gz 格式" |
| 解压失败 | 显示错误提示："压缩包解压失败，请检查文件是否损坏" |
| 没有 SKILL.md | 显示错误提示："压缩包中未找到 SKILL.md 文件" |
| SKILL.md 没有 name | 显示警告，允许用户手动输入名称 |
| 文件路径非法 | 显示错误提示："文件路径包含非法字符" |
| 提交失败 | 显示后端返回的错误信息 |

## 9. 测试用例

1. **正常流程**：上传包含 SKILL.md 的 zip 文件 → 预览 → 安装成功
2. **tar.gz 格式**：上传 tar.gz 文件 → 正常解压和预览
3. **子目录结构**：压缩包内有子目录 → 自动检测 SKILL.md
4. **无 SKILL.md**：上传不含 SKILL.md 的压缩包 → 显示错误
5. **大文件**：上传包含大量文件的压缩包 → 正常处理
6. **路径穿越**：压缩包内包含 `../` 路径 → 拒绝并提示错误
