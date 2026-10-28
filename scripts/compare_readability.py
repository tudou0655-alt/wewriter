"""Before/after comparison: rewrite saved drafts with the current writing pipeline (T-029).

Calls the real writing model (paid). Do not run without the customer's approval of the cost.
Per draft: one writing call, one or two fact-review calls, and one style-review call when the
reference signals find something. With --generate-card, one more call first distils the style
card from the example file(s). No web search is made: each draft's saved sources are reused.

    .venv\\Scripts\\python.exe scripts\\compare_readability.py --draft 10 --draft 11 --draft 12 ^
        --example .local-data\\examples\\seed-80-设备更新验收.md [--generate-card] --yes

The database is opened from a copy and never written. Results go to --out (default
.local-data\\readability-compare): per draft a Markdown file with the summary, the new text and the
old text, plus summary.json with the figures, the call count and the prompt sizes in characters.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wewriter.api import Model, Transport, banned_phrases, readability, readability_problems  # noqa: E402
from wewriter.config import migrate  # noqa: E402
from wewriter.db import Database  # noqa: E402
from wewriter.pipeline.composition import recent_shapes, style_pass, style_report  # noqa: E402
from wewriter.secrets import Vault  # noqa: E402
from wewriter.api import ApiError  # noqa: E402
from wewriter.style import clean_style_card, read_example_file, skeleton, style_block  # noqa: E402


class CountingTransport(Transport):
    """Counts model calls and keeps the size of every prompt sent."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def _open(self, url, key, payload, timeout, accept):
        if isinstance(payload, dict):
            messages = payload.get('messages') or []
            system = next((m['content'] for m in messages if m.get('role') == 'system'), payload.get('instructions', ''))
            user = next((m['content'] for m in messages if m.get('role') == 'user'), payload.get('input', ''))
            self.calls.append({'system_chars': len(system or ''), 'user_chars': len(user or '')})
        return super()._open(url, key, payload, timeout, accept)


class RecordingModel(Model):
    """Keeps every parsed model output, so a rejected article can be inspected afterwards."""

    def __init__(self, *args):
        super().__init__(*args)
        self.outputs = []

    def complete(self, *args, **kwargs):
        result = super().complete(*args, **kwargs)
        self.outputs.append(result)
        return result


class Run:
    """The parts of a task run that style_pass uses."""

    def __init__(self, config, writer):
        self.config, self.writer, self.styled = config, writer, None

    def stage(self, *args):
        pass


class Engine:
    @staticmethod
    def check():
        pass


def figures(title, body, recent):
    text = {'title': title, 'body': body}
    stats = readability(text)
    return {'title': title, 'title_chars': len(''.join(c for c in title if c.isalnum())),
            'mean_sentence': stats['mean'], 'long_share': stats['long_share'],
            'banned': banned_phrases(title + '\n' + body), 'signals': readability_problems(text, recent)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--draft', type=int, action='append', required=True, help='draft id, repeatable')
    parser.add_argument('--example', action='append', default=[], help='example article (.md/.docx), repeatable')
    parser.add_argument('--generate-card', action='store_true', help='distil the style card from the examples first (one paid call)')
    parser.add_argument('--card', help='reuse a saved style card (JSON, {"card": …} or the card itself); no call')
    parser.add_argument('--data-dir', default=str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'WeWriter'))
    parser.add_argument('--out', default=str(ROOT / '.local-data' / 'readability-compare'))
    parser.add_argument('--yes', action='store_true', help='confirm that real (paid) model calls may be made')
    args = parser.parse_args()
    if not args.yes:
        sys.exit('This script makes paid model calls. Re-run with --yes once the cost is approved.')
    data_dir, out = Path(args.data_dir), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    examples = []
    for n, path in enumerate(args.example, 1):
        title, body, meta = read_example_file(path)
        examples.append({'id': n, 'kind': meta.get('kind', 'good'), 'title': title, 'score': int(meta['score'])
                         if str(meta.get('score', '')).isdigit() else None, 'avoid': meta.get('avoid', ''),
                         'skeleton': skeleton(title, body, meta.get('avoid', ''))})
    work = Path(tempfile.mkdtemp(prefix='readability-'))
    try:
        for suffix in ('', '-wal', '-shm'):
            source = data_dir / ('workspace.sqlite' + suffix)
            if source.exists():
                shutil.copy2(source, work / source.name)
        db = Database(work / 'workspace.sqlite')
        vault = Vault(data_dir / 'keys.dpapi.json')
        base = migrate(db.config())
        name = base['writer_provider']
        card = base.get('style_card')
        if args.card:
            saved = json.loads(Path(args.card).read_text(encoding='utf-8'))
            card, fixes = clean_style_card(saved.get('card', saved), [e['avoid'] for e in examples])
            (out / 'style_card.json').write_text(json.dumps({'card': card, 'from': args.card, 'fixes': fixes},
                                                            ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'style_card': card, 'fixes': fixes}, ensure_ascii=False))
        elif args.generate_card:
            transport = CountingTransport()
            card = Model(name, base['providers'][name], vault.get(name), transport).style_card(examples)
            (out / 'style_card.json').write_text(json.dumps({'card': card, 'calls': transport.calls},
                                                            ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'style_card': card}, ensure_ascii=False))
        example = next((e for e in examples if e['kind'] == 'good'), None)
        for draft_id in args.draft:
            row = db.rows('''SELECT d.id, d.title, d.body, d.sources, j.config FROM drafts d
                             JOIN jobs j ON j.id=d.job_id WHERE d.id=?''', (draft_id,))
            if not row:
                print(f'draft {draft_id}: not found')
                continue
            row = row[0]
            config = migrate(json.loads(row['config']))
            sources = json.loads(row['sources'])
            if config.get('writing_mode') == 'single_web' and not any(s.get('source_id', '').startswith('W') for s in sources):
                config['research_degraded'] = True
            recent = recent_shapes(db, exclude=draft_id)
            config.update(recent_drafts=recent, style_card=card, style_review_enabled=True,
                          account_names=[a['name'] for a in db.accounts()],
                          example=example and {'id': example['id'], 'title': example['title'], 'skeleton': example['skeleton']})
            config['style_note'] = style_block(card, config['example'])[1]
            transport = CountingTransport()
            model = RecordingModel(name, config['providers'][name], vault.get(name), transport)
            try:
                written = model.write(sources, config)
                reviewed = model.review(written, sources, config)
                run = Run(config, model)
                final = style_pass(Engine, run, reviewed, sources)
            except (ApiError, ValueError) as exc:
                # Stop at the first failure: keep the raw outputs that were rejected, spend nothing more.
                (out / f'draft-{draft_id}-failed.json').write_text(json.dumps(
                    {'error': str(exc), 'calls': transport.calls, 'outputs': model.outputs},
                    ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({'draft': draft_id, 'failed': str(exc), 'calls': len(transport.calls)}, ensure_ascii=False))
                sys.exit(1)
            summary = {'before': figures(row['title'], row['body'], recent),
                       'after': figures(final['title'], final['body'], recent),
                       'calls': len(transport.calls), 'prompt_chars': transport.calls,
                       'style': style_report(config, run.styled).strip()}
            folder = out / f'draft-{draft_id}'
            folder.mkdir(parents=True, exist_ok=True)
            (folder / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            (folder / 'after.md').write_text('# ' + final['title'] + '\n\n' + final['body'], encoding='utf-8')
            (folder / 'before.md').write_text('# ' + row['title'] + '\n\n' + row['body'], encoding='utf-8')
            (out / f'draft-{draft_id}.md').write_text(
                f'# 稿件 {draft_id}：改前 / 改后\n\n```json\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n```\n\n'
                f'## 改后\n\n### {final["title"]}\n\n{final["body"]}\n\n---\n\n## 改前\n\n### {row["title"]}\n\n{row["body"]}\n',
                encoding='utf-8')
            print(json.dumps({'draft': draft_id, **{k: summary[k] for k in ('before', 'after', 'calls', 'prompt_chars', 'style')}},
                             ensure_ascii=False))
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
