/**
 * WebUI 配置下发 API
 *
 * 后端 GET /webui-config 返回前端功能开关（服务端 rag.api_url 等配置的投影）。
 * 当前仅有 rag_url：非空（/rag/）时侧边栏「知识库 RAG」入口跳转该地址
 * （本站反代，后端自动注入 LightRAG 登录 token）；为 null 时显示占位提示。
 */

import { client } from './client';

export interface WebuiConfig {
	/** RAG webui 反代入口（/rag/），null = 未配置 */
	rag_url: string | null;
}

export const webuiApi = {
	getConfig: () => client.get<WebuiConfig>('/webui-config'),
};
