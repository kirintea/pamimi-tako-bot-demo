/**
 * 图片上传 API
 *
 * 上传流程：
 * 1. 调用 getUploadUrl 获取上传凭证（presigned URL 或代理接口）
 * 2. 上传图片（S3 直传或后端代理）
 * 3. 返回对象 key，随消息发送给后端
 */

import { client } from './client';

interface UploadUrlResponse {
	upload_url: string;
	key: string;
	expires_in: number;
	headers?: Record<string, string> | null;
}

interface AccessUrlResponse {
	url: string;
	expires_in: number;
}

export const imagesApi = {
	/** 请求上传凭证 */
	getUploadUrl(data: { filename: string; content_type: string; size: number; user_id?: string }) {
		const params: Record<string, string> = {};
		if (data.user_id) params.user_id = data.user_id;
		// POST 需要的 body
		const body = {
			filename: data.filename,
			content_type: data.content_type,
			size: data.size,
		};
		return client.post<UploadUrlResponse>('/images/upload', body);
	},

	/** 获取图片访问 URL（历史消息用） */
	getAccessUrl(key: string) {
		return client.get<AccessUrlResponse>(`/images/${encodeURIComponent(key)}/url`);
	},
};

/**
 * 上传图片到对象存储
 *
 * 本地模式：POST 文件到后端代理
 * S3 模式：PUT 直传到 presigned URL
 */
export async function uploadImage(file: File, userId?: string): Promise<string> {
	// 1. 获取上传凭证
	const { upload_url, key, headers } = await imagesApi.getUploadUrl({
		filename: file.name,
		content_type: file.type,
		size: file.size,
		user_id: userId,
	});

	// 2. 上传文件
	const isLocalProxy = upload_url.startsWith('/images/upload/');

	if (isLocalProxy) {
		// 本地模式：PUT + FormData
		const formData = new FormData();
		formData.append('file', file);
		const res = await fetch(upload_url, {
			method: 'PUT',
			body: formData,
		});
		if (!res.ok) {
			const text = await res.text();
			throw new Error(`图片上传失败: ${res.status} ${text}`);
		}
	} else {
		// S3 模式：PUT 直传
		const reqHeaders: Record<string, string> = {
			'Content-Type': file.type,
			...(headers || {}),
		};
		const res = await fetch(upload_url, {
			method: 'PUT',
			headers: reqHeaders,
			body: file,
		});
		if (!res.ok) {
			throw new Error(`图片上传失败: ${res.status}`);
		}
	}

	return key;
}
