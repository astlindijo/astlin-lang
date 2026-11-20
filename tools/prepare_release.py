"""Build and verify the versioned wheel, source archive, and checksums."""
import argparse
from email.parser import BytesParser
import hashlib
from pathlib import Path
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import build_backend


def prepare(output):
    version = build_backend.VERSION
    notes = ROOT / 'releases' / f'v{version}.md'
    if not notes.is_file():
        raise ValueError(f'missing release notes: {notes}')
    if not (ROOT / 'LICENSE').is_file():
        raise ValueError('a selected LICENSE is required before preparing a public release')
    output.mkdir(parents=True, exist_ok=True)
    wheel = output / build_backend.build_wheel(output)
    source = output / build_backend.build_sdist(output)
    required = {'astlin/__init__.py', 'astlin/__main__.py', 'astlin/compiler.py',
                'astlin/runtime.h', 'astlin/runtime_json.h', 'astlin/runtime_ai.h', 'astlin/runtime_http.h',
                'astlin/ai_builtins.py', 'astlin/std/math.ad', 'astlin/std/text.ad',
                'astlin/std/ai/http.ad', 'astlin/std/ai/llm.ad', 'astlin/std/ai/rag.ad',
                'astlin/std/ai/pipeline.ad', 'astlin/std/ai/tensor.ad',
                'astlin/templates/rag/main.ad', 'astlin/templates/llm/main.ad'}
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        if not (required | {f'{build_backend.INFO}/licenses/LICENSE'}) <= names:
            raise ValueError('wheel is missing compiler/runtime/library files')
        metadata = BytesParser().parsebytes(archive.read(f'{build_backend.INFO}/METADATA'))
        if metadata['Name'] != 'astlin-lang' or metadata['Version'] != version:
            raise ValueError('wheel metadata version/name does not match the compiler')
        if metadata['License-Expression'] != 'MIT':
            raise ValueError('wheel metadata does not declare the selected MIT license')
    with tarfile.open(source, 'r:gz') as archive:
        names = set(archive.getnames())
        if any('.DS_Store' in Path(name).parts or '__pycache__' in Path(name).parts for name in names):
            raise ValueError('source archive contains local metadata or caches')
        expected = required | {'pyproject.toml', 'tools/build_backend.py', 'tools/prepare_release.py',
                               'README.md', 'LANGUAGE.md', 'AI.md', 'RELEASING.md', 'LICENSE', f'releases/v{version}.md'}
        if not {f'{build_backend.DIST}/{name}' for name in expected} <= names:
            raise ValueError('source archive is missing release or build files')
        member = archive.extractfile(f'{build_backend.DIST}/PKG-INFO')
        if member is None or BytesParser().parsebytes(member.read())['Version'] != version:
            raise ValueError('source archive metadata version does not match the compiler')
    lines = []
    for artifact in (wheel, source):
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        lines.append(f'{digest}  {artifact.name}\n')
    sums = output / 'SHA256SUMS'
    sums.write_text(''.join(lines), encoding='ascii')
    for artifact in (wheel, source, sums):
        print(artifact.resolve())
    print(f'Astlin v{version}: artifacts verified locally; publication is a separate step.')
    return wheel, source, sums


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-o', '--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    try:
        prepare(args.output)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        parser.exit(1, f'release preparation failed: {error}\n')
