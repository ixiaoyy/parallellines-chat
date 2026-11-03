<script lang="ts">
	import { getContext } from 'svelte';
	import type { Writable } from 'svelte/store';
	import type { i18n as I18n } from 'i18next';
	const i18n: Writable<I18n> = getContext('i18n');
	export let message = 'Initialization is temporarily unavailable.';
</script>

<div class="w-full max-w-sm px-5 py-10 text-center" role="alert">
	<p class="text-sm text-gray-600 dark:text-gray-300">{$i18n.t(message)}</p>
	<p class="mt-2 text-xs text-gray-500">{$i18n.t('Check your connection and retry without signing out.')}</p>
	<!-- 重新加载沿用原启动路径，避免重试时重复安装监听器、连接或复用未核实的账号状态。 -->
	<button type="button" class="mt-4 min-h-11 rounded-full bg-gray-100 px-5 py-2 text-sm text-black hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100" on:click={() => location.reload()}>
		{$i18n.t('Retry')}
	</button>
</div>
