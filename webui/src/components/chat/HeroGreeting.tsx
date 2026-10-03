/**
 * HeroGreeting — 空状态大标题（Penpot「💬 Chat 主对话页 v2」）
 *
 * H1 30px/700 #111928，lineHeight 1.2，居中。
 */

export const HERO_GREETING_KEYS = [
	'chat.empty.greetings.workOn',
	'chat.empty.greetings.start',
	'chat.empty.greetings.build',
	'chat.empty.greetings.tackle',
] as const;

export function randomHeroGreetingKey(): (typeof HERO_GREETING_KEYS)[number] {
	const index = Math.floor(Math.random() * HERO_GREETING_KEYS.length);
	return HERO_GREETING_KEYS[index] ?? HERO_GREETING_KEYS[0];
}

export function HeroGreeting({ text }: { text: string }) {
	return (
		<h1 className="select-none text-center text-[30px] font-bold leading-[1.2] tracking-normal text-[#111928] dark:text-[#f5f5f9]">
			{text}
		</h1>
	);
}
