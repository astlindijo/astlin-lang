"""Small dependency-free PEP 517 wheel/sdist backend for this pure-Python compiler.

The compiler's runtime header and standard library travel inside its Python package.
No third-party build tool is needed to install Astlin in an offline environment.
"""
import argparse
import base64
import csv
import hashlib
import io
from pathlib import Path
import re
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = re.search(r'^VERSION = "([^"]+)"', (ROOT / 'astlin/compiler.py').read_text(), re.M).group(1)
DIST = f'astlin_lang-{VERSION}'
INFO = DIST + '.dist-info'


def package_files():
    return sorted(p for p in (ROOT / 'astlin').rglob('*')
                  if p.is_file() and p.suffix in {'.py', '.h', '.ad', '.txt', '.md'})


def metadata_files():
    metadata = ('Metadata-Version: 2.4\nName: astlin-lang\n'
                f'Version: {VERSION}\n'
                'Summary: Astlin .ad language with native JSON, LLM clients, and RAG\nRequires-Python: >=3.10\n'
                'Author: Astlin Dijo\nLicense-Expression: MIT\nLicense-File: LICENSE\n'
                'Project-URL: Source, https://github.com/astlindijo/astlin-lang\n'
                'Project-URL: Issues, https://github.com/astlindijo/astlin-lang/issues\n'
                'Project-URL: Releases, https://github.com/astlindijo/astlin-lang/releases\n'
                'Description-Content-Type: text/markdown\n\n' + (ROOT / 'README.md').read_text(encoding='utf-8'))
    files = {
        'METADATA': metadata.encode('utf-8'),
        'WHEEL': b'Wheel-Version: 1.0\nGenerator: astlin-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n',
        'entry_points.txt': b'[console_scripts]\nastlin = astlin.compiler:main\n',
        'top_level.txt': b'astlin\n',
    }
    license_file = ROOT / 'LICENSE'
    if license_file.is_file():
        files['licenses/LICENSE'] = license_file.read_bytes()
    return files


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []


def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    target = Path(metadata_directory) / INFO
    target.mkdir(parents=True, exist_ok=True)
    for name, data in metadata_files().items():
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return INFO


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    directory = Path(wheel_directory)
    directory.mkdir(parents=True, exist_ok=True)
    filename = DIST + '-py3-none-any.whl'
    entries = {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in package_files()}
    entries.update({f'{INFO}/{name}': data for name, data in metadata_files().items()})
    record = io.StringIO(newline='')
    writer = csv.writer(record, lineterminator='\n')
    for name, data in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode('ascii')
        writer.writerow([name, 'sha256=' + digest, len(data)])
    writer.writerow([f'{INFO}/RECORD', '', ''])
    entries[f'{INFO}/RECORD'] = record.getvalue().encode('utf-8')
    with zipfile.ZipFile(directory / filename, 'w', zipfile.ZIP_DEFLATED) as wheel:
        for name, data in sorted(entries.items()):
            wheel.writestr(name, data)
    return filename


def build_sdist(sdist_directory, config_settings=None):
    directory = Path(sdist_directory)
    directory.mkdir(parents=True, exist_ok=True)
    filename = DIST + '.tar.gz'
    files = package_files()
    source_suffixes = {'.py', '.ad', '.h', '.md', '.json', '.toml', '.yml', '.yaml', '.txt'}
    for folder in ('tools', 'tests', 'examples', 'editors', 'releases', '.github'):
        files.extend(p for p in (ROOT / folder).rglob('*')
                     if p.is_file() and p.suffix in source_suffixes and '__pycache__' not in p.parts)
    files.extend(ROOT / name for name in ('astlin.py', 'pyproject.toml', 'README.md', 'LANGUAGE.md',
                                          'CHANGELOG.md', 'RELEASING.md', 'AI.md', 'LICENSE', 'Makefile', 'astlin.json', '.gitignore')
                 if (ROOT / name).is_file())
    with tarfile.open(directory / filename, 'w:gz') as archive:
        for path in sorted(set(files)):
            archive.add(path, arcname=DIST + '/' + path.relative_to(ROOT).as_posix())
        data = metadata_files()['METADATA']
        info = tarfile.TarInfo(DIST + '/PKG-INFO')
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    return filename


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build Astlin distributions without external dependencies')
    parser.add_argument('kind', choices=('wheel', 'sdist', 'all'), nargs='?', default='all')
    parser.add_argument('-o', '--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    if args.kind in ('wheel', 'all'):
        print(args.output / build_wheel(args.output))
    if args.kind in ('sdist', 'all'):
        print(args.output / build_sdist(args.output))
