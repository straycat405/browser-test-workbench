import MarkdownIt from '/markdown-it.mjs';

const report = document.getElementById('imported-report');
const markdown = new MarkdownIt({ html: false, linkify: false });

// 보고서 본문 안의 주소는 글자로 표시한다. 저장된 캡처는 위 갤러리에서 연다.
markdown.renderer.rules.link_open = () => '';
markdown.renderer.rules.link_close = () => '';
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index].content);
report.innerHTML = markdown.render(report.textContent);
