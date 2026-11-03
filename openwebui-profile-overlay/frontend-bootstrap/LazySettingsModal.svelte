<script lang="ts">
	import { getContext, onMount, onDestroy } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	import type { SettingsModalRequest } from '$lib/stores';
	import { user } from '$lib/stores';
	import { saveSettingsRetry, takeSettingsRetry } from '$lib/utils/settingsRetry';
	import Modal from '$lib/components/common/Modal.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	const i18n: Writable<I18n> = getContext('i18n');
	export let show: boolean | string | SettingsModalRequest = false;
	let component: typeof import('./SettingsModal.svelte').default | null = null;
	let loading = false;
	let failed = false;
	let recoveryFailed = false;
	let destroyed = false;
	let timer: ReturnType<typeof setTimeout> | undefined;

	/** 首次打开时加载原弹窗；失败等待用户重试，成功后保留组件实例和原始 show 请求。 */
	async function loadSettings() {
		if (loading || component) return;
		loading = true;
		failed = false;
		try {
			const module = await Promise.race([
				import('./SettingsModal.svelte'),
				new Promise<never>((_resolve, reject) => {
					timer = setTimeout(() => reject(new Error('Settings module timed out')), 12000);
				})
			]);
			if (!destroyed) component = module.default;
		} catch (error) {
			if (!destroyed) {
				failed = true;
				console.error('Settings module unavailable:', error);
			}
		} finally {
			clearTimeout(timer);
			if (!destroyed) loading = false;
		}
	}
	/** 浏览器会缓存失败的模块导入；重载前保存本账号的原请求，不能用同 URL 的 import 假装重试。 */
	function retrySettings() {
		try {
			saveSettingsRetry(sessionStorage, $user?.id ?? '', show);
			location.reload();
		} catch (error) {
			recoveryFailed = true;
			console.error('Settings retry could not preserve the request:', error);
		}
	}
	onMount(() => {
		try {
			const recovered = takeSettingsRetry(sessionStorage, $user?.id ?? '');
			if (recovered && !show) show = recovered;
		} catch (error) {
			console.error('Settings retry restoration unavailable:', error);
		}
	});
	$: if (show && !component && !loading && !failed) void loadSettings();
	onDestroy(() => { destroyed = true; clearTimeout(timer); });
</script>

{#if component}
	<!-- 加载后即使关闭也不卸载，保留原弹窗内部状态与双向关闭绑定。 -->
	<svelte:component this={component} bind:show />
{:else if show}
	<Modal show={true} size="sm" ariaLabel={$i18n.t('Settings')} onRequestClose={() => (show = false)}>
		<div class="p-6 text-sm text-gray-700 dark:text-gray-200" aria-live="polite">
			<h2 class="mb-4 text-lg font-medium">{$i18n.t('Settings')}</h2>
			{#if failed}
				<p role="alert">{$i18n.t(recoveryFailed ? 'Unable to restore settings. Reload the page and open settings again.' : 'Unable to load settings.')}</p>
			{:else}
				<div class="flex items-center gap-3"><Spinner className="size-5" /><p>{$i18n.t('Loading settings...')}</p></div>
			{/if}
			<div class="mt-5 flex justify-end gap-2">
				<button type="button" class="min-h-11 rounded-full px-4 py-2 hover:bg-gray-100 dark:hover:bg-gray-800" on:click={() => (show = false)}>{$i18n.t('Cancel')}</button>
				{#if failed}<button type="button" class="min-h-11 rounded-full bg-gray-100 px-4 py-2 text-black hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100" on:click={retrySettings}>{$i18n.t('Retry')}</button>{/if}
			</div>
		</div>
	</Modal>
{/if}
