<script lang="ts">
	import { onMount } from 'svelte';
	import i18n, { getLanguages, changeLanguage } from '$lib/i18n';
	import { loginForFeature, readGuestDraft, saveGuestDraft } from '$lib/apis/main-unified-browser.mjs';
	export let siteName = 'Open WebUI';
	let draft = '';
	let storageUnavailable = false;
	let languageError = false;
	let languages: Awaited<ReturnType<typeof getLanguages>> = [];
	let locale = '';
	$: locale = $i18n.language;
	onMount(async () => { draft = readGuestDraft(); languages = await getLanguages(); });
	// 复用原生语言切换及 locale 保存；不触发登录或挂载私有设置。
	async function selectLanguage(event: Event) {
		languageError = false;
		try { await changeLanguage((event.currentTarget as HTMLSelectElement).value); }
		catch { languageError = true; }
	}
	function remember() {
		try { saveGuestDraft(draft); } catch { storageUnavailable = true; }
	}
	function login() {
		try { loginForFeature(draft); } catch { storageUnavailable = true; }
	}
	function loginWithoutDraft() { window.location.assign('/oauth/oidc/login'); }
</script>

<!-- 游客分支不挂载 Chat/Sidebar，不能初始化私有模型、历史、工具、文件或 Socket。 -->
<div class="guest-shell">
	<aside class="guest-sidebar" aria-label={$i18n.t('Chat sidebar')}>
		<a class="guest-brand" href="/" aria-label={$i18n.t('Chat home')}><img src="/static/favicon.png" alt="" />{siteName}</a>
		<button class="guest-history" on:click={login}><span aria-hidden="true">＋</span> {$i18n.t('New Chat')}</button>
		<button class="guest-history" on:click={login}><span aria-hidden="true">◷</span> {$i18n.t('History')}</button>
		<div class="guest-sidebar-bottom"><button class="guest-login" on:click={login}>{$i18n.t('Sign in')}</button><small>Open WebUI</small></div>
	</aside>
	<main id="main-content" class="guest-main">
		<header class="guest-header"><span>{siteName}</span><div class="guest-header-actions">
			<label class="guest-sr" for="guest-language">{$i18n.t('Language')}</label>
			<select id="guest-language" class="guest-language" value={locale} on:change={selectLanguage}>
				{#each languages as language}<option value={language.code}>{language.title}</option>{/each}
			</select>
			<button class="guest-login" on:click={login}>{$i18n.t('Sign in')}</button>
		</div></header>
		{#if languageError}<p class="guest-storage" role="status">{$i18n.t('The language could not be loaded. Please try again.')}</p>{/if}
		<section class="guest-content" aria-labelledby="guest-title">
			<img class="guest-mark" src="/static/favicon.png" alt="Open WebUI" />
			<h1 id="guest-title">{$i18n.t('How can I help you?')}</h1>
			<form class="guest-composer" on:submit|preventDefault={login}>
				<label class="guest-sr" for="guest-message">{$i18n.t('Message draft')}</label>
				<textarea id="guest-message" bind:value={draft} on:input={remember} maxlength="20000" rows="3" placeholder={$i18n.t('Message...')} />
				<div class="guest-composer-actions"><button type="button" class="guest-attach" aria-label={$i18n.t('Upload files')} on:click={login}>＋</button><button type="button" class="guest-model" on:click={login}>{$i18n.t('Select a model')}</button><button class="guest-send" type="submit" aria-label={$i18n.t('Send message')}>↑</button></div>
			</form>
			{#if storageUnavailable}<p class="guest-storage" role="status">{$i18n.t('Your draft could not be saved. Copy it before continuing.')}<button type="button" on:click={loginWithoutDraft}>{$i18n.t('Continue to sign in')}</button></p>{/if}
		</section>
	</main>
</div>

<style>
	.guest-shell{display:flex;min-height:100dvh;background:#fff;color:#202123;font-family:Inter,ui-sans-serif,system-ui,sans-serif}
	.guest-sidebar{position:relative;display:flex;flex-direction:column;width:260px;flex:none;padding:18px 12px;background:#f9f9f9}
	.guest-brand{display:flex;align-items:center;gap:10px;min-height:44px;padding:0 10px;font-weight:600;text-decoration:none;color:inherit}
	.guest-brand img{width:28px;height:28px;object-fit:contain}.guest-history{display:flex;align-items:center;gap:10px;min-height:46px;padding:9px 12px;border:0;border-radius:10px;background:transparent;color:inherit;text-align:left;font:inherit;cursor:pointer}.guest-history:hover{background:#ececec}.guest-history span{font-size:22px}.guest-sidebar-bottom{display:grid;gap:12px;margin-top:auto;padding:18px 8px 0}.guest-sidebar-bottom small{color:#777;font-size:12px}.guest-main{display:flex;flex-direction:column;flex:1;min-width:0}.guest-header{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:14px 24px;font-size:18px;font-weight:600}.guest-login{min-width:64px;min-height:44px;border:1px solid #dedede;border-radius:10px;padding:9px 14px;background:#fff;color:inherit;font:inherit;font-size:14px;cursor:pointer}.guest-content{width:min(720px,calc(100% - 40px));margin:auto;padding:44px 0 80px;text-align:center}.guest-mark{width:48px;height:48px;object-fit:contain;margin:0 auto 18px}h1{margin:0;font-size:clamp(25px,4vw,34px);font-weight:600;letter-spacing:-.04em}.guest-composer{margin-top:28px;padding:15px 16px 10px;border:1px solid #e6e6e6;border-radius:24px;background:#f4f4f4;text-align:left}.guest-composer textarea{display:block;width:100%;min-height:86px;max-height:240px;resize:vertical;border:0;background:transparent;color:inherit;outline:none;font:inherit;font-size:16px;line-height:1.65}.guest-model{border:0;background:transparent;color:inherit;font:inherit;cursor:pointer}.guest-model:hover{color:#202123}.guest-composer-actions{display:flex;align-items:center;gap:12px;color:#737373;font-size:12px}.guest-attach,.guest-send{display:grid;place-items:center;flex:none;width:44px;height:44px;border:0;border-radius:50%;background:transparent;color:inherit;font-size:25px;cursor:pointer}.guest-send{margin-left:auto;background:#202123;color:#fff;font-size:24px}.guest-storage{margin:14px auto 0;color:#9b5715;font-size:12px;line-height:1.7}.guest-storage button{text-decoration:underline;margin-left:8px}button:focus-visible,a:focus-visible{outline:2px solid #777;outline-offset:3px}.guest-composer:focus-within{outline:2px solid #aaa;outline-offset:2px}.guest-sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
	.guest-header-actions{display:flex;align-items:center;gap:10px;min-width:0}.guest-language{max-width:190px;min-width:0;min-height:44px;padding:8px 10px;border:1px solid #dedede;border-radius:10px;background:#fff;color:inherit;font:inherit;font-size:13px}.guest-language:focus-visible{outline:2px solid #777;outline-offset:3px}:global(.dark) .guest-language{background:#212121;border-color:#454545}
	:global(.dark) .guest-shell{background:#212121;color:#ececec}:global(.dark) .guest-sidebar{background:#171717}:global(.dark) .guest-composer{background:#303030;border-color:#383838}:global(.dark) .guest-login{background:#212121;border-color:#454545}:global(.dark) .guest-send{background:#ececec;color:#212121}:global(.dark) .guest-history:hover{background:#292929}
	@media(max-width:760px){.guest-sidebar{display:none}.guest-header{padding:12px 16px;font-size:16px}.guest-header>span{font-size:14px}.guest-language{max-width:130px;font-size:12px;padding:8px 4px}.guest-header-actions{gap:6px}.guest-login{padding:9px 10px;min-width:58px}.guest-content{width:calc(100% - 32px);padding:24px 0 40px}.guest-composer{border-radius:22px}}
</style>
