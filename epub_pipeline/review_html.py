"""Render aligned translations and existing AI findings as a standalone HTML file."""
from html import escape

from .domain import validate_review
from .prompts import STYLE_NAMES


CATEGORY_NAMES = {'omission': '누락', 'addition': '정보 추가', 'distortion': '의미 왜곡',
                  'terminology': '용어·호칭', 'style': '문체'}
CSS = """
:root { color-scheme: light; font-family: system-ui, sans-serif; color: #172536; background: #f4f6f8; }
* { box-sizing: border-box; } body { margin: 0; } main { max-width: 1500px; margin: auto; padding: 28px; }
h1 { margin: 8px 0 12px; font-size: 1.7rem; } h2 { font-size: 1rem; margin: 0; }
p { line-height: 1.75; } .muted { color: #526477; } code { overflow-wrap: anywhere; font-size: .8rem; }
.controls { display: flex; flex-wrap: wrap; align-items: center; gap: 18px; padding: 16px; margin: 24px 0 10px;
  background: white; border: 1px solid #d5dee5; border-radius: 10px; }
input[type=search], select { font: inherit; padding: 8px; max-width: 100%; border: 1px solid #a7b8c7; border-radius: 6px; }
input[type=search] { width: 280px; } input[type=checkbox] { width: 17px; height: 17px; vertical-align: middle; }
.paragraph { margin: 18px 0; border: 1px solid #d5dee5; border-radius: 10px; background: white; overflow: hidden; }
.location { background: #eaf0f5; padding: 14px 18px; display: flex; gap: 12px; flex-wrap: wrap; align-items: baseline; }
.columns { display: grid; grid-template-columns: 1fr 1fr 1fr; padding: 10px 18px; background: #f7f9fa; font-weight: 650; }
.comparison { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); }
.source { padding: 18px; } .version { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  border-left: 1px solid #d5dee5; border-bottom: 1px solid #d5dee5; }
.version:last-child { border-bottom: 0; } .translation, .findings { padding: 18px; }
.findings { border-left: 1px solid #d5dee5; background: #fafbfc; }
.text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.85; margin: 12px 0 0; }
.badge { display: inline-block; font-size: .8rem; padding: 3px 8px; border-radius: 5px; margin: 6px 0 0; background: #eef2f5; }
.flagged .badge { background: #fff0cd; color: #714800; } .issue { border-left: 3px solid #d38d13; padding-left: 12px; margin: 0 0 18px; }
.issue:last-child { margin-bottom: 0; } .issue p { margin: 5px 0 0; }
[hidden] { display: none !important; } button { font: inherit; padding: 8px 12px; cursor: pointer; }
@media (max-width: 800px) {
  main { padding: 14px; } .columns { display: none; } .comparison { display: block; }
  .source { border-bottom: 1px solid #d5dee5; } .source::before { content: '원문'; font-weight: 650; }
  .version { border-left: 0; } .findings::before { content: '검수 지적'; display: block; font-weight: 650; margin-bottom: 10px; }
}
@media (max-width: 480px) { .version { display: block; } .findings { border-left: 0; border-top: 1px solid #d5dee5; } }
@media print { .controls, #visible-count, noscript { display: none; } main { max-width: none; padding: 0; }
  .paragraph { break-inside: auto; } .version { break-inside: avoid; } }
"""
JS = """
const style = document.getElementById('style');
const flagged = document.getElementById('flagged');
const search = document.getElementById('search');
function update() {
  let visible = 0;
  document.querySelectorAll('.paragraph').forEach(section => {
    const rows = [...section.querySelectorAll('.version')];
    rows.forEach(row => { row.hidden = !!style.value && row.dataset.style !== style.value; });
    const chosen = rows.filter(row => !row.hidden);
    const hasIssues = chosen.some(row => row.querySelector('.issue'));
    const text = [section.querySelector('.location').textContent, section.querySelector('.source').textContent,
                  ...chosen.map(row => row.textContent)].join(' ').toLocaleLowerCase();
    section.hidden = (flagged.checked && !hasIssues) || !text.includes(search.value.trim().toLocaleLowerCase());
    if (!section.hidden) visible++;
  });
  document.getElementById('visible-count').textContent = `표시 중: ${visible}개 항목 (제목 포함)`;
}
[style, flagged, search].forEach(control => control.addEventListener('input', update));
document.getElementById('print').addEventListener('click', () => window.print());
update();
"""


def render_review_html(bundle: dict, report: dict) -> str:
    items = [bundle['title'], *bundle['paragraphs']]
    ids = [p['id'] for p in items]
    if len(set(ids)) != len(ids):
        raise ValueError('HTML 보고서의 문단 ID가 중복되었습니다.')
    findings = {}
    for style, value in report.items():
        if style not in STYLE_NAMES:
            raise ValueError(f'지원하지 않는 검수 스타일: {style}')
        raw = value['issues']
        validate_review(items, {'issues': [{key: issue[key] for key in ('id', 'category', 'description')}
                                          for issue in raw]})
        for issue in raw:
            findings.setdefault((style, issue['id']), []).append(issue)
    chapter = '프롤로그' if bundle['chapter'] == 0 else f"{bundle['chapter']}장"
    title = f"{chapter} · {bundle['original_title']} · 검수 보고서"
    total = sum(len(v['issues']) for v in report.values())
    sections = []
    for number, item in enumerate(items):
        versions = []
        for style, label in STYLE_NAMES.items():
            translation = item['translations'].get(style)
            issues = findings.get((style, item['id']), [])
            reviewed = report.get(style, {}).get('ai_review_executed') is True
            if translation is None:
                status = '번역 미생성'
            elif not reviewed:
                status = 'AI 검수 미실행'
            else:
                status = f'AI 지적 {len(issues)}건' if issues else 'AI 지적 없음'
            descriptions = ''.join(
                f'<div class="issue"><strong>{escape(CATEGORY_NAMES[i["category"]])}</strong>'
                f'<p class="text">{escape(i["description"])}</p></div>' for i in issues)
            versions.append(f'<div class="version{" flagged" if issues else ""}" data-style="{style}">'
                            f'<div class="translation"><h2>{escape(label)}</h2><span class="badge">{status}</span>'
                            f'<p class="text">{escape(translation) if translation is not None else "아직 생성되지 않음"}</p></div>'
                            f'<div class="findings">{descriptions or f"<p class=muted>{status}</p>"}</div></div>')
        location = '제목 (문단 0)' if number == 0 else f'문단 {number}'
        sections.append(f'<section class="paragraph" id="paragraph-{number}">'
                        f'<div class="location"><h2>{escape(chapter)} · {location}</h2><code>{escape(item["id"])}</code></div>'
                        '<div class="columns" aria-hidden="true"><span>원문</span><span>번역문</span><span>검수 지적</span></div>'
                        f'<div class="comparison"><div class="source"><p class="text">{escape(item["source_text"])}</p></div>'
                        f'<div class="versions">{"".join(versions)}</div></div></section>')
    options = ''.join(f'<option value="{s}">{escape(label)}</option>' for s, label in STYLE_NAMES.items())
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{escape(title)}</title><style>{CSS}</style></head><body><main>
<header><div class="muted">원문 대조 · 로컬 검수 보고서</div><h1>{escape(title)}</h1>
<p>본문 {len(items) - 1}문단 · AI 지적 {total}건 · 추가 AI 호출 없음<br>
AI 지적은 자동 수정되지 않았습니다. 지적 없음은 사람의 최종 승인을 뜻하지 않습니다.</p>
<p class="muted">번역 모델: {escape(bundle.get('model', '알 수 없음'))} · 검수 모델: {escape(bundle.get('review_model', '알 수 없음'))}<br>
출력 revision: <code>{escape(bundle.get('revision', '기록 없음'))}</code></p></header>
<div class="controls"><label>문체 <select id="style"><option value="">세 문체 모두</option>{options}</select></label>
<label><input id="flagged" type="checkbox"> 지적 있는 문단만</label>
<label>검색 <input id="search" type="search" placeholder="원문·번역·지적 검색"></label><button id="print" type="button">인쇄</button></div>
<noscript><p>검색과 필터는 JavaScript를 켜면 사용할 수 있습니다. 전체 보고서는 아래에서 읽을 수 있습니다.</p></noscript>
<p id="visible-count" role="status" class="muted"></p>{''.join(sections)}
</main><script>{JS}</script></body></html>\n'''
