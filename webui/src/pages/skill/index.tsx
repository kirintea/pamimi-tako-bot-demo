/**
 * Skill 管理页面 — Penpot v2 设计规格
 *
 * 管理 UI 抽至 components/skill/SkillManager（与设置页技能共用）。
 */

import { SkillManager } from '@/components/skill/SkillManager';

export function SkillPage() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="px-12 pt-[52px] pb-12">
        <SkillManager />
      </div>
    </div>
  );
}
