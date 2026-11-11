// webui/src/api/credentials.ts — 凭证管理（admin，root）
import { client } from './client';

export interface Credential {
	id: number;
	cred_type: string;
	key_prefix: string;
	app_id: string;
	channel: string;
	scopes: string;
	status: 'active' | 'revoked';
	description?: string | null;
	created_at?: string;
	revoked_at?: string | null;
}

/** 创建响应：key 明文仅此一次（P2/D1） */
export interface CreatedCredential extends Credential {
	key: string;
}

export interface CredentialCreateBody {
	app_id: string;
	channel: string;
	description?: string;
	cred_type?: string;
	scopes?: string;
	key?: string;
}

export const credentialsApi = {
	list: (viewer: string) =>
		client.get<{ items: Credential[] }>('/admin/credentials', { viewer }),

	create: (viewer: string, body: CredentialCreateBody) =>
		client.post<CreatedCredential>(
			`/admin/credentials?viewer=${encodeURIComponent(viewer)}`,
			body,
		),

	revoke: (viewer: string, id: number) =>
		client.post<Credential>(
			`/admin/credentials/${id}/revoke?viewer=${encodeURIComponent(viewer)}`,
		),
};
