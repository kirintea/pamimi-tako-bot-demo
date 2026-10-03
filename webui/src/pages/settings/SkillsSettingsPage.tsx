/**
 * Skill 设置页 — 从 pages/skill 迁移到设置体系
 */

import { BookText, Plus, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { skillApi } from '@/api/skill';
import type { SkillInfo } from '@/api/types';
import { SettingsGroup, SettingsRow } from '@/components/settings';
import { SettingsSectionTitle } from '@/components/settings/SettingsSectionTitle';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Spinner } from '@/components/ui/spinner';

export function SkillsSettingsPage() {
	const { t } = useTranslation();
	const [skills, setSkills] = useState<SkillInfo[]>([]);
	const [loading, setLoading] = useState(true);
	const [showAdd, setShowAdd] = useState(false);
	const [form, setForm] = useState({ name: '', display_name: '', description: '', markdown: '', tags: '' });

	const loadSkills = useCallback(async () => {
		try {
			setLoading(true);
			const data = await skillApi.list();
			setSkills(data);
		} catch (e) {
			console.error('Failed to load skills:', e);
		} finally {
			setLoading(false);
		}
	}, []);

	useEffect(() => { loadSkills(); }, [loadSkills]);

	const handleAdd = async () => {
		try {
			await skillApi.create({
				name: form.name,
				display_name: form.display_name || undefined,
				description: form.description,
				markdown: form.markdown,
				tags: form.tags ? form.tags.split(',').map((t) => t.trim()).filter(Boolean) : [],
			});
			setShowAdd(false);
			setForm({ name: '', display_name: '', description: '', markdown: '', tags: '' });
			await loadSkills();
		} catch (e) {
			console.error('Failed to add skill:', e);
		}
	};

	const handleDelete = async (id: string) => {
		if (!confirm(t('settings.skills.confirmDelete', { defaultValue: '确认删除此 Skill？' }))) return;
		try {
			await skillApi.delete(id);
			await loadSkills();
		} catch (e) {
			console.error('Failed to delete skill:', e);
		}
	};

	return (
		<div className="settings-stack">
			<section>
				<div className="flex items-center justify-between mb-5">
					<SettingsSectionTitle className="mb-0">
						{t('settings.skills.title', { defaultValue: '技能' })}
					</SettingsSectionTitle>
					<Button
						variant="outline"
						size="sm"
						className="h-8 rounded-full gap-1.5 text-[12px]"
						onClick={() => setShowAdd(!showAdd)}
					>
						<Plus className="size-3.5" />
						{t('settings.skills.add', { defaultValue: '添加' })}
					</Button>
				</div>

				{/* 添加表单 */}
				{showAdd && (
					<SettingsGroup className="mb-4 p-4">
						<div className="grid gap-3">
							<div className="grid grid-cols-2 gap-3">
								<Input
									placeholder={t('settings.skills.namePlaceholder', { defaultValue: '名称' })}
									value={form.name}
									onChange={(e) => setForm({ ...form, name: e.target.value })}
									className="h-9 rounded-full text-[13px]"
								/>
								<Input
									placeholder={t('settings.skills.displayNamePlaceholder', { defaultValue: '显示名称' })}
									value={form.display_name}
									onChange={(e) => setForm({ ...form, display_name: e.target.value })}
									className="h-9 rounded-full text-[13px]"
								/>
							</div>
							<Input
								placeholder={t('settings.skills.descPlaceholder', { defaultValue: '描述' })}
								value={form.description}
								onChange={(e) => setForm({ ...form, description: e.target.value })}
								className="h-9 rounded-full text-[13px]"
							/>
							<Input
								placeholder={t('settings.skills.tagsPlaceholder', { defaultValue: '标签 (逗号分隔)' })}
								value={form.tags}
								onChange={(e) => setForm({ ...form, tags: e.target.value })}
								className="h-9 rounded-full text-[13px]"
							/>
							<div className="flex justify-end gap-2">
								<Button variant="ghost" size="sm" className="h-8 rounded-full" onClick={() => setShowAdd(false)}>
									{t('common.cancel', { defaultValue: '取消' })}
								</Button>
								<Button size="sm" className="h-8 rounded-full" onClick={handleAdd} disabled={!form.name}>
									{t('common.add', { defaultValue: '添加' })}
								</Button>
							</div>
						</div>
					</SettingsGroup>
				)}

				{/* Skill 列表 */}
				{loading ? (
					<div className="flex items-center justify-center py-12">
						<Spinner className="size-5 text-muted-foreground" />
					</div>
				) : skills.length === 0 ? (
					<div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
						<BookText className="size-8 mb-3 opacity-40" />
						<p className="text-[13px]">{t('settings.skills.empty', { defaultValue: '暂无技能' })}</p>
					</div>
				) : (
					<SettingsGroup>
						{skills.map((skill) => (
							<SettingsRow
								key={skill.id}
								label={skill.display_name || skill.name}
								description={skill.description || skill.name}
								hoverable
							>
								<div className="flex items-center gap-2">
									{(skill.tags?.length ?? 0) > 0 && (
										<div className="hidden sm:flex gap-1">
											{skill.tags!.slice(0, 2).map((tag) => (
												<Badge key={tag} variant="secondary" className="text-[10px] px-1.5 py-0">
													{tag}
												</Badge>
											))}
										</div>
									)}
									<Button
										variant="ghost"
										size="icon"
										className="size-8 text-muted-foreground hover:text-destructive"
										onClick={() => handleDelete(skill.id)}
									>
										<Trash2 className="size-3.5" />
									</Button>
								</div>
							</SettingsRow>
						))}
					</SettingsGroup>
				)}
			</section>
		</div>
	);
}