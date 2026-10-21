"""Shared offline fixtures; no real credentials or books required."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from epub_pipeline.domain import APIResponse


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        super().setUp()
        for target in ('socket.create_connection', 'socket.socket.connect', 'socket.socket.connect_ex', 'socket.getaddrinfo'):
            blocker = patch(target, side_effect=AssertionError('Remote network is prohibited'))
            blocker.start()
            self.addCleanup(blocker.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


def epub(path, documents, order=None):
    container = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>'
    items = ''.join(f'<item id="c{i}" href="{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(len(documents)))
    spine = ''.join(f'<itemref idref="c{i}"/>' for i in (order if order is not None else range(len(documents))))
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('META-INF/container.xml', container)
        z.writestr('OPS/book.opf', f'<package xmlns="http://www.idpf.org/2007/opf"><manifest>{items}</manifest><spine>{spine}</spine></package>')
        for i, body in enumerate(documents):
            z.writestr(f'OPS/{i}.xhtml', f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Chapter {i}</title></head><body>{body}</body></html>')


class FakeProvider:
    def __init__(self, failures=None, review_issues=None):
        self.calls = []
        self.failures = list(failures or [])
        self.review_issues = review_issues or []

    def complete(self, **request):
        self.calls.append(request)
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, BaseException):
                raise failure
            if failure is not None:
                return failure
        if request['name'] == 'review_issues':
            result = {'issues': self.review_issues}
        else:
            result = {'translations': [{'id': p['id'], 'text': '번역 ' + p['text']} for p in request['payload']['paragraphs']]}
        return APIResponse(json.dumps(result, ensure_ascii=False),
                           {'input_tokens': 100, 'output_tokens': 50, 'cached_input_tokens': 20},
                           model='mock-snapshot')
