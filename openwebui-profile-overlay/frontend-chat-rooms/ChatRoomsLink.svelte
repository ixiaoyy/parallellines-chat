<script lang="ts">
	import { getContext } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Users from '$lib/components/icons/Users.svelte';

	export let compact = false;
	const i18n = getContext('i18n');
</script>

<!-- 只打开多人聊天公开地址；保留当前对话，不转交 Chat 凭据或来源地址。 -->
<Tooltip
	content={compact ? $i18n.t('Chat Channels') : ''}
	className={compact ? 'flex justify-center' : 'px-1 flex text-gray-700 dark:text-gray-300'}
	placement="right"
>
	<a
		href="https://tavern.pingxingxian.space/"
		target="_blank"
		rel="noopener noreferrer"
		data-testid="chat-rooms-link"
		aria-label={$i18n.t('Chat Channels')}
		on:click={(event) => event.stopPropagation()}
		class="group transition focus-visible:outline-2 focus-visible:outline-offset-[-2px] {compact
			? 'flex size-8 items-center justify-center rounded-lg'
			: 'grow flex items-center gap-2 rounded-xl px-2 py-1.5 hover:bg-gray-100 dark:hover:bg-gray-900'}"
	>
		{#if compact}
			<span class="flex size-[calc(30px*var(--app-text-scale,1))] items-center justify-center rounded-lg group-hover:bg-gray-100 dark:group-hover:bg-gray-900">
				<Users className="size-4" />
			</span>
		{:else}
			<Users className="size-4 shrink-0" />
			<span class="text-[0.8125rem] leading-5">{$i18n.t('Chat Channels')}</span>
		{/if}
	</a>
</Tooltip>
