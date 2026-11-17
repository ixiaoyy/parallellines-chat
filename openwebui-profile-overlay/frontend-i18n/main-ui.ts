import { get } from 'svelte/store';
import i18n from './index';

// 只翻译既有可见提示，不改变接口失败码、抛错对象或登录/计费判断。
const messages: Record<string, string> = {
	'模型授权已失效，请点击重新授权后重试。': 'Model authorization has expired. Reauthorize and try again.',
	'重新授权': 'Reauthorize',
	'重新检查授权': 'Check authorization again',
	'登录账号不一致，已停止自动恢复。请确认主站与当前 Chat 使用原来的同一账号，再重新检查授权。': 'Automatic recovery stopped because the accounts do not match. Use the original account on the main site and in Chat, then check authorization again.',
	'后台恢复模型授权未完成，请手动重新授权后重试。': 'Background model authorization recovery did not complete. Reauthorize manually and try again.',
	'模型授权恢复': 'Model authorization recovery',
	'平台模型需要重新授权，个人连接仍可使用。': 'Platform models need reauthorization. Personal connections are still available.',
	'部分个人模型列表读取失败，可在外部连接中重试或手动填写模型 ID。': 'Some personal model lists could not be loaded. Retry in External Connections or enter model IDs manually.',
	'登录信息无法更新，请清除本站数据后重试。': 'Sign-in information could not be updated. Clear this site’s data and try again.',
	'主站资料同步配置需要管理员检查。': 'An administrator needs to check the main-site profile sync configuration.',
	'主站用户名不可同步，请联系管理员。': 'The main-site username could not be synchronized. Contact an administrator.',
	'请先在主站验证邮箱，再重新登录。': 'Verify your email on the main site, then sign in again.',
	'主站资料与已有账号冲突，请联系管理员。': 'The main-site profile conflicts with an existing account. Contact an administrator.',
	'主站资料同步失败，请重新登录或联系管理员。': 'The main-site profile could not be synchronized. Sign in again or contact an administrator.',
	'模型列表加载失败，请稍后重试；若持续失败请联系管理员。': 'The model list could not be loaded. Try again later or contact an administrator if the problem persists.',
	'模型授权已失效或缺失，请用平行线账号重新授权。': 'Model authorization is missing or expired. Reauthorize with your ParallelLines account.',
	"个人连接": "Personal connection",
	"个人连接请求超时，请检查地址和网络。": "The personal connection timed out. Check the URL and network.",
	"个人连接模型列表读取失败，可检查地址、Key 和跨域配置，或手动填写模型 ID。": "The personal model list could not be loaded. Check the URL, key and cross-origin settings, or enter model IDs manually.",
	"个人模型目录格式无效。": "The personal model list has an invalid format.",
	"个人连接已失效，请重新选择模型。": "The personal connection is no longer valid. Select a model again.",
	"个人连接已删除，请重新选择模型。": "The personal connection was deleted. Select a model again.",
	"个人连接已更改或停用，请重新选择模型。": "The personal connection was changed or disabled. Select a model again.",
	"个人连接 API 地址无效。": "The personal connection API URL is invalid.",
	"个人连接需要不含账号密码、查询参数的 HTTPS API 地址。": "The personal connection needs an HTTPS API URL without credentials or query parameters.",
	"该模型已从个人连接中移除，请重新选择模型。": "This model was removed from the personal connection. Select a model again.",
	"个人连接请求已取消。": "The personal connection request was cancelled.",
	"个人 Key 无效或已失效。": "The personal API key is invalid or expired.",
	"个人连接请求过于频繁，请稍后再试。": "The personal connection is rate limited. Try again later.",
	"个人连接额度或权限不足，请到对应服务商检查。": "The personal connection has insufficient quota or permissions. Check your provider account.",
	"个人连接请求失败，请检查服务商状态。": "The personal connection request failed. Check the provider status.",
	"个人连接未返回响应流。": "The personal connection did not return a response stream.",
	"个人连接返回了错误或无效的响应流。": "The personal connection returned an error or an invalid response stream.",
	"个人连接返回的单条事件过大。": "A personal connection response event is too large.",
	"个人连接响应中断，请重试。": "The personal connection response was interrupted. Try again.",
	"个人连接返回了错误或无效响应。": "The personal connection returned an error or an invalid response.",
	"个人连接请求已取消或超时，请检查连接后重试。": "The personal connection request was cancelled or timed out. Check the connection and try again.",
	"个人连接响应中断或格式不正确，请重试。": "The personal connection response was interrupted or had an invalid format. Try again."
};

/** 翻译已知主站提示；未知上游提示继续交给原生翻译，不猜测其业务含义。 */
export function translateMainUi(message: string): string {
	return get(i18n).t(messages[message] ?? message);
}
