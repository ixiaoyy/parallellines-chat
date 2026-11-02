import { marked } from 'marked';
import markedExtension from '$lib/utils/marked/extension';
import markedKatexExtension from '$lib/utils/marked/katex-extension';
import hljs from 'highlight.js';

/** 按需生成格式化复制的 HTML Blob；text 为原始 Markdown，样式及语言覆盖沿用原实现。 */
export const createFormattedClipboardBlob = (text: string): Blob => {
	let styledHtml = '';
	const options = {
		throwOnError: false,
		highlight: function (code, lang) {
			const language = hljs.getLanguage(lang) ? lang : 'plaintext';
			return hljs.highlight(code, { language }).value;
		}
	};
	marked.use(markedKatexExtension(options));
	marked.use(markedExtension(options));
	// DEVELOPER NOTE: Go to `$lib/components/chat/Messages/Markdown.svelte` to add extra markdown extensions for rendering.

	const htmlContent = marked.parse(text);

	// Add basic styling to make the content look better when pasted
	styledHtml = `
	<div>
		<style>
			pre {
				background-color: #f6f8fa;
				border-radius: 0.375rem;
				padding: 1rem;
				overflow: auto;
			}
			code {
				font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
				font-size: 0.875rem;
			}
			.hljs-keyword { color: #d73a49; }
			.hljs-string { color: #032f62; }
			.hljs-comment { color: #6a737d; }
			.hljs-function { color: #6f42c1; }
			.hljs-number { color: #005cc5; }
			.hljs-operator { color: #d73a49; }
			.hljs-class { color: #6f42c1; }
			.hljs-title { color: #6f42c1; }
			.hljs-params { color: #24292e; }
			.hljs-built_in { color: #005cc5; }
			blockquote {
				border-left: 4px solid #dfe2e5;
				padding-left: 1rem;
				color: #6a737d;
				margin-left: 0;
				margin-right: 0;
			}
			table {
				border-collapse: collapse;
				width: 100%;
				margin-bottom: 1rem;
			}
			table, th, td {
				border: 1px solid #dfe2e5;
			}
			th, td {
				padding: 0.5rem 0.75rem;
			}
			th {
				background-color: #f6f8fa;
			}
		</style>
		${htmlContent}
	</div>
`;
	return new Blob([styledHtml], { type: 'text/html' });
};
