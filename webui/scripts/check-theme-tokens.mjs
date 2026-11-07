#!/usr/bin/env node
/**
 * 主题 token 校验 —— 暗色主题改造的自动化守卫。
 *
 * 用法:
 *   node scripts/check-theme-tokens.mjs           # 校验（提交前 / CI）
 *   node scripts/check-theme-tokens.mjs --update  # 重建基线（仅在【有意】变更浅色或降低 hex 数时用）
 *
 * 四项断言:
 *   A. @theme inline 里每个 --color-* 必须走 var() 间接层
 *      （否则字面量被 Tailwind 内联进工具类，.dark 覆盖是死代码）
 *   B. :root 与 .dark 的 token 集合对等（缺一边暗色就不生效/浅色就缺定义）
 *   C. 浅色 token 值必须与 theme-light-baseline.json 完全一致 —— 机器化执行「浅色不动」
 *   D. 裸十六进制数量不得超过 theme-hex-ratchet.json（只降不升）
 */
import { readFileSync, writeFileSync, existsSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const webuiRoot = join(here, '..');
const cssPath = join(webuiRoot, 'src', 'index.css');
const lightBaselinePath = join(webuiRoot, 'theme-light-baseline.json');
const hexRatchetPath = join(webuiRoot, 'theme-hex-ratchet.json');

const updating = process.argv.includes('--update');
const failures = [];
const ok = (m) => console.log(`  PASS  ${m}`);
const bad = (m) => {
	failures.push(m);
	console.log(`  FAIL  ${m}`);
};

const css = readFileSync(cssPath, 'utf8');

/** 按正则定位块首（该正则须匹配到开括号前的位置），返回块体 */
function readBlock(src, re, label) {
	const m = re.exec(src);
	if (!m) throw new Error(`找不到块: ${label}`);
	const start = src.indexOf('{', m.index);
	let depth = 0;
	for (let j = start; j < src.length; j++) {
		if (src[j] === '{') depth++;
		else if (src[j] === '}') {
			depth--;
			if (depth === 0) return src.slice(start + 1, j);
		}
	}
	throw new Error(`块未闭合: ${label}`);
}

/** 去掉注释 */
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '');

/** 提取 --x: value（同名后者覆盖前者） */
function decls(body) {
	const out = new Map();
	for (const mm of stripComments(body).matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+)/g)) {
		out.set(mm[1], mm[2].trim());
	}
	return out;
}

const themeInlineBody = readBlock(css, /@theme\s+inline\s*\{/, '@theme inline');
const rootBody = readBlock(css, /^:root\s*\{/m, ':root');
const darkBody = readBlock(css, /^\.dark\s*\{/m, '.dark');

const rootDecls = decls(rootBody);
const darkDecls = decls(darkBody);
const inlineDecls = decls(themeInlineBody);

// ---------- A. @theme inline 的 --color-* 必须走 var() 间接层 ----------
console.log('\n[A] @theme inline 的 --color-* 必须走 var() 间接层');
const literalTokens = [];
for (const [name, value] of inlineDecls) {
	if (!name.startsWith('--color-')) continue;
	if (!/^var\(/.test(value)) literalTokens.push(`${name}: ${value}`);
}
if (literalTokens.length === 0) {
	ok(`@theme inline 中 ${[...inlineDecls.keys()].filter((k) => k.startsWith('--color-')).length} 个 --color-* 均经 var() 间接层`);
} else {
	bad(`@theme inline 中 ${literalTokens.length} 个 token 写了字面量，.dark 覆盖无效:`);
	for (const t of literalTokens) console.log(`        ${t}`);
}

// ---------- B. :root / .dark token 集合对等 ----------
console.log('\n[B] :root 与 .dark token 集合对等');
const onlyRoot = [...rootDecls.keys()].filter((k) => !darkDecls.has(k));
const onlyDark = [...darkDecls.keys()].filter((k) => !rootDecls.has(k));
if (onlyRoot.length === 0 && onlyDark.length === 0) {
	ok(`两侧 token 集合一致（共 ${rootDecls.size} 个）`);
} else {
	if (onlyRoot.length) bad(`仅 :root 定义（暗色缺失，会回落到浅色值）: ${onlyRoot.join(', ')}`);
	if (onlyDark.length) bad(`仅 .dark 定义（浅色缺失）: ${onlyDark.join(', ')}`);
}

// 附加：@theme inline 引用的 var 必须有定义
const referenced = new Set();
for (const mm of stripComments(themeInlineBody).matchAll(/var\((--[a-z0-9-]+)/g)) {
	referenced.add(mm[1]);
}
const definedSomewhere = new Set([...rootDecls.keys(), ...inlineDecls.keys()]);
const dangling = [...referenced].filter((v) => !definedSomewhere.has(v));
if (dangling.length === 0) {
	ok(`@theme inline 引用的 ${referenced.size} 个 var 均有定义`);
} else {
	bad(`@theme inline 引用了未定义的 var: ${dangling.join(', ')}`);
}

// ---------- C. 浅色 token 值冻结 ----------
console.log('\n[C] 浅色 token 值 = 基线快照（浅色不动）');
const sortedLight = Object.fromEntries([...rootDecls.entries()].sort());
const lightJson = `${JSON.stringify(sortedLight, null, 2)}\n`;
if (updating) {
	writeFileSync(lightBaselinePath, lightJson);
	ok(`已写入浅色基线 ${relative(webuiRoot, lightBaselinePath)}（${rootDecls.size} 个 token）`);
} else if (!existsSync(lightBaselinePath)) {
	bad(`缺少 ${relative(webuiRoot, lightBaselinePath)}，先跑 --update 建立基线`);
} else {
	const baseline = JSON.parse(readFileSync(lightBaselinePath, 'utf8'));
	const changed = [];
	for (const k of Object.keys(baseline)) {
		if (!(k in sortedLight)) changed.push(`${k}: 基线有、现无`);
		else if (baseline[k] !== sortedLight[k]) changed.push(`${k}: ${baseline[k]} -> ${sortedLight[k]}`);
	}
	for (const k of Object.keys(sortedLight)) {
		if (!(k in baseline)) changed.push(`${k}: 新增（基线没有）`);
	}
	if (changed.length === 0) {
		ok(`浅色 ${Object.keys(baseline).length} 个 token 与基线完全一致`);
	} else {
		bad(`浅色 token 发生变化 ${changed.length} 处（浅色必须不动；确需变更请 --update 并人工复核渲染）:`);
		for (const c of changed) console.log(`        ${c}`);
	}
}

// ---------- D. 裸十六进制棘轮 ----------
console.log('\n[D] 裸十六进制数量（棘轮，只降不升）');
const SKIP = new Set(['src/index.css', 'src/lib/channelBrands.ts']);
const HEX = /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b/g;
const srcRoot = join(webuiRoot, 'src');
const perFile = {};
let total = 0;
for (const entry of readdirSync(srcRoot, { recursive: true })) {
	const abs = join(srcRoot, entry);
	if (!/\.(ts|tsx|css)$/.test(abs)) continue;
	const rel = relative(webuiRoot, abs).replace(/\\/g, '/');
	if (SKIP.has(rel)) continue;
	const hits = readFileSync(abs, 'utf8').match(HEX);
	if (hits && hits.length) {
		perFile[rel] = hits.length;
		total += hits.length;
	}
}
if (updating) {
	writeFileSync(hexRatchetPath, `${JSON.stringify({ max: total, perFile }, null, 2)}\n`);
	ok(`已写入 hex 棘轮上限 ${total}`);
} else if (!existsSync(hexRatchetPath)) {
	bad(`缺少 ${relative(webuiRoot, hexRatchetPath)}，先跑 --update 建立基线`);
} else {
	const ratchet = JSON.parse(readFileSync(hexRatchetPath, 'utf8'));
	if (total <= ratchet.max) {
		ok(`裸 hex ${total} 个 ≤ 上限 ${ratchet.max}`);
	} else {
		bad(`裸 hex ${total} 个 > 上限 ${ratchet.max}，新增于:`);
		for (const [f, n] of Object.entries(perFile)) {
			const was = ratchet.perFile[f] || 0;
			if (n > was) console.log(`        ${f}: ${was} -> ${n}`);
		}
	}
}

// ---------- 汇总 ----------
console.log('');
if (failures.length) {
	console.log(`主题校验未通过：${failures.length} 项`);
	process.exit(1);
}
console.log('主题校验全部通过');
